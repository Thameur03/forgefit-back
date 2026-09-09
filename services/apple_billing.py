"""Server-authoritative DAUNTRA Premium verification and persistence."""

from __future__ import annotations

import hmac
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import (
    SignedDataVerifier,
    VerificationException,
    VerificationStatus,
)
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import env_bool, is_production
from models.billing import (
    AppleNotificationEvent,
    AppleSubscription,
    BillingIdentity,
)


PERMANENT_APPLE_BUNDLE_ID = "com.dauntra.app"
PREMIUM_PRODUCT_IDS = frozenset(
    {
        "dauntra_premium_weekly",
        "dauntra_premium_monthly",
        "dauntra_premium_yearly",
    }
)
ENTITLED_STATUSES = frozenset({"active", "grace_period"})
_APPLE_ROOT_DIRECTORY = Path(__file__).resolve().parents[1] / "certificates" / "apple"


class AppleBillingError(Exception):
    """Base class for errors safe to map at the HTTP boundary."""


class AppleBillingConfigurationError(AppleBillingError):
    pass


class AppleVerificationError(AppleBillingError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class AppleTransactionRejected(AppleBillingError):
    pass


class AppleAccountBindingError(AppleBillingError):
    pass


@dataclass(frozen=True)
class VerifiedAppleTransaction:
    original_transaction_id: str | None
    transaction_id: str | None
    product_id: str | None
    bundle_id: str | None
    environment: str | None
    app_account_token: str | None
    purchase_date_ms: int | None
    expires_date_ms: int | None
    signed_date_ms: int | None = None
    revocation_date_ms: int | None = None
    storefront: str | None = None


@dataclass(frozen=True)
class VerifiedAppleNotification:
    notification_uuid: str | None
    notification_type: str | None
    subtype: str | None
    signed_date_ms: int | None
    bundle_id: str | None
    environment: str | None
    status: str | None
    transaction: VerifiedAppleTransaction | None
    renewal_app_account_token: str | None = None
    grace_expires_date_ms: int | None = None


@dataclass(frozen=True)
class EntitlementSnapshot:
    entitlement: str
    subscription_status: str
    app_account_token: str
    product_id: str | None = None
    platform: str | None = None
    environment: str | None = None
    expires_at: datetime | None = None
    grace_expires_at: datetime | None = None

    def to_dict(self) -> dict[str, object | None]:
        return {
            "entitlement": self.entitlement,
            "subscription_status": self.subscription_status,
            "product_id": self.product_id,
            "platform": self.platform,
            "environment": self.environment,
            "expires_at": self.expires_at,
            "grace_expires_at": self.grace_expires_at,
            "app_account_token": self.app_account_token,
        }


class AppleJWSVerifier(Protocol):
    @property
    def allowed_environments(self) -> frozenset[str]: ...

    def verify_transaction(self, signed_transaction: str) -> VerifiedAppleTransaction: ...

    def verify_notification(self, signed_payload: str) -> VerifiedAppleNotification: ...


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _transaction_from_apple(payload: object) -> VerifiedAppleTransaction:
    return VerifiedAppleTransaction(
        original_transaction_id=getattr(payload, "originalTransactionId", None),
        transaction_id=getattr(payload, "transactionId", None),
        product_id=getattr(payload, "productId", None),
        bundle_id=getattr(payload, "bundleId", None),
        environment=_enum_value(getattr(payload, "environment", None)),
        app_account_token=getattr(payload, "appAccountToken", None),
        purchase_date_ms=getattr(payload, "purchaseDate", None),
        expires_date_ms=getattr(payload, "expiresDate", None),
        signed_date_ms=getattr(payload, "signedDate", None),
        revocation_date_ms=getattr(payload, "revocationDate", None),
        storefront=getattr(payload, "storefront", None),
    )


class OfficialAppleJWSVerifier:
    """Adapter around Apple's library; nested notification JWS is re-verified."""

    def __init__(self, verifiers: dict[str, SignedDataVerifier]):
        if not verifiers:
            raise AppleBillingConfigurationError(
                "At least one Apple IAP environment must be configured"
            )
        self._verifiers = verifiers

    @property
    def allowed_environments(self) -> frozenset[str]:
        return frozenset(self._verifiers)

    def _try_each(self, method: str, value: str) -> tuple[object, SignedDataVerifier]:
        retryable = False
        for verifier in self._verifiers.values():
            try:
                return getattr(verifier, method)(value), verifier
            except VerificationException as exc:
                retryable = retryable or (
                    exc.status == VerificationStatus.RETRYABLE_VERIFICATION_FAILURE
                )
        raise AppleVerificationError(
            "Apple signed data could not be verified", retryable=retryable
        )

    def verify_transaction(self, signed_transaction: str) -> VerifiedAppleTransaction:
        payload, _ = self._try_each(
            "verify_and_decode_signed_transaction", signed_transaction
        )
        return _transaction_from_apple(payload)

    def verify_notification(self, signed_payload: str) -> VerifiedAppleNotification:
        payload, verifier = self._try_each(
            "verify_and_decode_notification", signed_payload
        )
        data = getattr(payload, "data", None)
        transaction = None
        signed_transaction = getattr(data, "signedTransactionInfo", None)
        if signed_transaction:
            try:
                transaction = _transaction_from_apple(
                    verifier.verify_and_decode_signed_transaction(signed_transaction)
                )
            except VerificationException as exc:
                raise AppleVerificationError(
                    "Apple notification transaction could not be verified",
                    retryable=(
                        exc.status
                        == VerificationStatus.RETRYABLE_VERIFICATION_FAILURE
                    ),
                ) from exc

        renewal_token = None
        grace_expires = None
        signed_renewal = getattr(data, "signedRenewalInfo", None)
        if signed_renewal:
            try:
                renewal = verifier.verify_and_decode_renewal_info(signed_renewal)
            except VerificationException as exc:
                raise AppleVerificationError(
                    "Apple notification renewal data could not be verified",
                    retryable=(
                        exc.status
                        == VerificationStatus.RETRYABLE_VERIFICATION_FAILURE
                    ),
                ) from exc
            renewal_token = getattr(renewal, "appAccountToken", None)
            grace_expires = getattr(renewal, "gracePeriodExpiresDate", None)

        return VerifiedAppleNotification(
            notification_uuid=getattr(payload, "notificationUUID", None),
            notification_type=_enum_value(
                getattr(payload, "notificationType", None)
                or getattr(payload, "rawNotificationType", None)
            ),
            subtype=_enum_value(
                getattr(payload, "subtype", None)
                or getattr(payload, "rawSubtype", None)
            ),
            signed_date_ms=getattr(payload, "signedDate", None),
            bundle_id=getattr(data, "bundleId", None),
            environment=_enum_value(getattr(data, "environment", None)),
            status=_enum_value(getattr(data, "status", None)),
            transaction=transaction,
            renewal_app_account_token=renewal_token,
            grace_expires_date_ms=grace_expires,
        )


def _parse_environments() -> tuple[Environment, ...]:
    default = "PRODUCTION" if is_production() else "SANDBOX"
    raw = os.getenv("APPLE_IAP_ENVIRONMENTS", default)
    values = [piece.strip().upper() for piece in raw.split(",") if piece.strip()]
    if not values or any(value not in {"PRODUCTION", "SANDBOX"} for value in values):
        raise AppleBillingConfigurationError(
            "APPLE_IAP_ENVIRONMENTS must contain PRODUCTION and/or SANDBOX"
        )
    return tuple(dict.fromkeys(Environment[value] for value in values))


def _load_root_certificates() -> list[bytes]:
    paths = sorted(_APPLE_ROOT_DIRECTORY.glob("*.pem"))
    if not paths:
        raise AppleBillingConfigurationError("Apple root certificates are missing")
    result = []
    for path in paths:
        certificate = x509.load_pem_x509_certificate(path.read_bytes())
        result.append(certificate.public_bytes(Encoding.DER))
    return result


@lru_cache(maxsize=1)
def get_apple_jws_verifier() -> AppleJWSVerifier:
    bundle_id = os.getenv("APPLE_BUNDLE_ID", PERMANENT_APPLE_BUNDLE_ID).strip()
    if bundle_id != PERMANENT_APPLE_BUNDLE_ID:
        raise AppleBillingConfigurationError(
            f"APPLE_BUNDLE_ID must be {PERMANENT_APPLE_BUNDLE_ID}"
        )

    environments = _parse_environments()
    app_id_raw = os.getenv("APPLE_APP_ID", "").strip()
    app_id = None
    if app_id_raw:
        try:
            app_id = int(app_id_raw)
        except ValueError as exc:
            raise AppleBillingConfigurationError(
                "APPLE_APP_ID must be an integer"
            ) from exc
    if Environment.PRODUCTION in environments and app_id is None:
        raise AppleBillingConfigurationError(
            "APPLE_APP_ID is required for production Apple verification"
        )

    online_checks = env_bool("APPLE_IAP_ONLINE_CHECKS", default=True)
    roots = _load_root_certificates()
    verifiers = {
        environment.value: SignedDataVerifier(
            roots,
            online_checks,
            environment,
            bundle_id,
            app_id if environment == Environment.PRODUCTION else None,
        )
        for environment in environments
    }
    return OfficialAppleJWSVerifier(verifiers)


def _millis_to_datetime(value: int | None, *, field: str) -> datetime:
    if value is None or not isinstance(value, int) or isinstance(value, bool):
        raise AppleTransactionRejected(f"Verified transaction is missing {field}")
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise AppleTransactionRejected(
            f"Verified transaction contains an invalid {field}"
        ) from exc


def _optional_millis(value: int | None) -> datetime | None:
    if value is None:
        return None
    return _millis_to_datetime(value, field="timestamp")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_uuid(value: str | None) -> str:
    if not value:
        raise AppleAccountBindingError(
            "Verified Apple transaction has no appAccountToken"
        )
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise AppleAccountBindingError(
            "Verified Apple transaction has an invalid appAccountToken"
        ) from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise AppleAccountBindingError(
            "Verified Apple transaction appAccountToken is not canonical"
        )
    return canonical


def _validate_transaction(
    transaction: VerifiedAppleTransaction,
    *,
    allowed_environments: frozenset[str],
) -> None:
    if transaction.bundle_id != PERMANENT_APPLE_BUNDLE_ID:
        raise AppleTransactionRejected("Verified transaction bundle ID is invalid")
    if transaction.environment not in allowed_environments:
        raise AppleTransactionRejected("Verified transaction environment is invalid")
    if transaction.product_id not in PREMIUM_PRODUCT_IDS:
        raise AppleTransactionRejected("Verified transaction product is not supported")
    if not transaction.original_transaction_id or not transaction.transaction_id:
        raise AppleTransactionRejected(
            "Verified transaction identifiers are incomplete"
        )
    _canonical_uuid(transaction.app_account_token)
    _millis_to_datetime(transaction.purchase_date_ms, field="purchaseDate")
    _millis_to_datetime(transaction.expires_date_ms, field="expiresDate")


def _status_from_transaction(
    transaction: VerifiedAppleTransaction, *, now: datetime
) -> str:
    if transaction.revocation_date_ms is not None:
        return "revoked"
    expires_at = _millis_to_datetime(transaction.expires_date_ms, field="expiresDate")
    return "active" if expires_at > now else "expired"


def _status_from_notification(
    notification: VerifiedAppleNotification,
    transaction: VerifiedAppleTransaction,
    *,
    now: datetime,
) -> str:
    if transaction.revocation_date_ms is not None:
        return "revoked"
    status_mapping = {
        "1": "active",
        "2": "expired",
        "3": "billing_retry",
        "4": "grace_period",
        "5": "revoked",
    }
    if notification.status in status_mapping:
        return status_mapping[notification.status]

    kind = (notification.notification_type or "").upper()
    subtype = (notification.subtype or "").upper()
    if kind in {"REFUND", "REVOKE"}:
        return "revoked"
    if kind in {"EXPIRED", "GRACE_PERIOD_EXPIRED"}:
        return "expired"
    if kind == "DID_FAIL_TO_RENEW":
        return "grace_period" if subtype == "GRACE_PERIOD" else "billing_retry"
    if kind in {"SUBSCRIBED", "DID_RENEW", "RENEWAL_EXTENDED"}:
        return "active"
    return _status_from_transaction(transaction, now=now)


class AppleBillingService:
    def __init__(self, verifier: AppleJWSVerifier):
        self._verifier = verifier

    def _identity_for_user(self, db: Session, user_id: int) -> BillingIdentity:
        identity = (
            db.query(BillingIdentity)
            .filter(BillingIdentity.user_id == user_id)
            .first()
        )
        if identity is not None:
            return identity
        identity = BillingIdentity(
            user_id=user_id,
            app_account_token=str(uuid.uuid4()),
        )
        db.add(identity)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            identity = (
                db.query(BillingIdentity)
                .filter(BillingIdentity.user_id == user_id)
                .first()
            )
            if identity is None:
                raise
        return identity

    def entitlement_for_user(
        self, db: Session, user_id: int, *, commit_identity: bool = True
    ) -> EntitlementSnapshot:
        identity = self._identity_for_user(db, user_id)
        if commit_identity:
            db.commit()
        subscriptions = (
            db.query(AppleSubscription)
            .filter(AppleSubscription.user_id == user_id)
            .all()
        )
        now = datetime.now(timezone.utc)
        ranked = sorted(
            subscriptions,
            key=lambda item: (
                1 if self._effective_status(item, now=now) in ENTITLED_STATUSES else 0,
                _as_utc(item.grace_expires_at)
                or _as_utc(item.expires_at)
                or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        selected = ranked[0] if ranked else None
        if selected is None:
            return EntitlementSnapshot(
                entitlement="free",
                subscription_status="none",
                app_account_token=identity.app_account_token,
            )
        effective_status = self._effective_status(selected, now=now)
        return EntitlementSnapshot(
            entitlement=(
                "premium" if effective_status in ENTITLED_STATUSES else "free"
            ),
            subscription_status=effective_status,
            product_id=selected.product_id,
            platform="apple",
            environment=selected.environment,
            expires_at=_as_utc(selected.expires_at),
            grace_expires_at=_as_utc(selected.grace_expires_at),
            app_account_token=identity.app_account_token,
        )

    @staticmethod
    def _effective_status(subscription: AppleSubscription, *, now: datetime) -> str:
        if subscription.status == "revoked" or subscription.revoked_at is not None:
            return "revoked"
        expires_at = _as_utc(subscription.expires_at)
        grace_expires_at = _as_utc(subscription.grace_expires_at)
        if subscription.status == "grace_period":
            if grace_expires_at is not None and grace_expires_at > now:
                return "grace_period"
            return "expired"
        if subscription.status == "active":
            return "active" if expires_at is not None and expires_at > now else "expired"
        if subscription.status == "billing_retry":
            return "billing_retry"
        return "expired"

    def submit_transaction(
        self, db: Session, *, user_id: int, signed_transaction: str
    ) -> EntitlementSnapshot:
        transaction = self._verifier.verify_transaction(signed_transaction)
        _validate_transaction(
            transaction, allowed_environments=self._verifier.allowed_environments
        )
        identity = self._identity_for_user(db, user_id)
        token = _canonical_uuid(transaction.app_account_token)
        if not hmac.compare_digest(identity.app_account_token, token):
            raise AppleAccountBindingError(
                "Apple transaction is bound to a different DAUNTRA account"
            )
        now = datetime.now(timezone.utc)
        self._upsert_subscription(
            db,
            user_id=user_id,
            transaction=transaction,
            status=_status_from_transaction(transaction, now=now),
            grace_expires_at=None,
            source_signed_at=_optional_millis(transaction.signed_date_ms),
            now=now,
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise AppleAccountBindingError(
                "Apple transaction is already bound to another account"
            ) from exc
        return self.entitlement_for_user(db, user_id)

    def process_notification(
        self, db: Session, *, signed_payload: str
    ) -> tuple[bool, str]:
        notification = self._verifier.verify_notification(signed_payload)
        if notification.bundle_id != PERMANENT_APPLE_BUNDLE_ID:
            raise AppleTransactionRejected("Verified notification bundle ID is invalid")
        if notification.environment not in self._verifier.allowed_environments:
            raise AppleTransactionRejected("Verified notification environment is invalid")
        if not notification.notification_uuid or not notification.notification_type:
            raise AppleTransactionRejected("Verified notification metadata is incomplete")

        duplicate = (
            db.query(AppleNotificationEvent.id)
            .filter(
                AppleNotificationEvent.notification_uuid
                == notification.notification_uuid
            )
            .first()
        )
        if duplicate is not None:
            return True, "duplicate"

        outcome = "ignored"
        transaction = notification.transaction
        if transaction is not None:
            try:
                _validate_transaction(
                    transaction,
                    allowed_environments=self._verifier.allowed_environments,
                )
                transaction_token = _canonical_uuid(transaction.app_account_token)
                if notification.renewal_app_account_token:
                    renewal_token = _canonical_uuid(
                        notification.renewal_app_account_token
                    )
                    if not hmac.compare_digest(transaction_token, renewal_token):
                        raise AppleAccountBindingError(
                            "Verified Apple notification account tokens disagree"
                        )
                identity = (
                    db.query(BillingIdentity)
                    .filter(
                        BillingIdentity.app_account_token == transaction_token
                    )
                    .first()
                )
                if identity is None:
                    outcome = "unbound"
                else:
                    now = datetime.now(timezone.utc)
                    self._upsert_subscription(
                        db,
                        user_id=identity.user_id,
                        transaction=transaction,
                        status=_status_from_notification(
                            notification, transaction, now=now
                        ),
                        grace_expires_at=_optional_millis(
                            notification.grace_expires_date_ms
                        ),
                        source_signed_at=_optional_millis(
                            notification.signed_date_ms
                            or transaction.signed_date_ms
                        ),
                        now=now,
                    )
                    outcome = "updated"
            except AppleTransactionRejected as exc:
                if transaction.product_id not in PREMIUM_PRODUCT_IDS:
                    outcome = "ignored_product"
                else:
                    raise exc

        db.add(
            AppleNotificationEvent(
                notification_uuid=notification.notification_uuid,
                notification_type=notification.notification_type,
                subtype=notification.subtype,
                signed_at=_optional_millis(notification.signed_date_ms),
                outcome=outcome,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return True, "duplicate"
        return False, outcome

    def _upsert_subscription(
        self,
        db: Session,
        *,
        user_id: int,
        transaction: VerifiedAppleTransaction,
        status: str,
        grace_expires_at: datetime | None,
        source_signed_at: datetime | None,
        now: datetime,
    ) -> AppleSubscription:
        original_id = transaction.original_transaction_id or ""
        transaction_id = transaction.transaction_id or ""
        existing = (
            db.query(AppleSubscription)
            .filter(AppleSubscription.original_transaction_id == original_id)
            .first()
        )
        if existing is not None and existing.user_id != user_id:
            raise AppleAccountBindingError(
                "Apple subscription is already bound to another DAUNTRA account"
            )
        transaction_owner = (
            db.query(AppleSubscription)
            .filter(AppleSubscription.latest_transaction_id == transaction_id)
            .first()
        )
        if transaction_owner is not None and (
            transaction_owner.user_id != user_id
            or transaction_owner.original_transaction_id != original_id
        ):
            raise AppleAccountBindingError(
                "Apple transaction is already bound to another subscription"
            )

        purchase_at = _millis_to_datetime(
            transaction.purchase_date_ms, field="purchaseDate"
        )
        expires_at = _millis_to_datetime(
            transaction.expires_date_ms, field="expiresDate"
        )
        revoked_at = _optional_millis(transaction.revocation_date_ms)
        token = _canonical_uuid(transaction.app_account_token)

        if existing is None:
            existing = AppleSubscription(
                user_id=user_id,
                app_account_token=token,
                original_transaction_id=original_id,
                latest_transaction_id=transaction_id,
                product_id=transaction.product_id,
                environment=transaction.environment,
                storefront=transaction.storefront,
                status=status,
                purchased_at=purchase_at,
                expires_at=expires_at,
                grace_expires_at=grace_expires_at,
                revoked_at=revoked_at,
                last_signed_at=source_signed_at,
                last_verified_at=now,
            )
            db.add(existing)
            return existing

        if not hmac.compare_digest(existing.app_account_token, token):
            raise AppleAccountBindingError(
                "Apple subscription account binding cannot be changed"
            )
        previous_signed_at = _as_utc(existing.last_signed_at)
        should_apply = previous_signed_at is None or (
            source_signed_at is not None and source_signed_at >= previous_signed_at
        )
        existing.last_verified_at = now
        if not should_apply:
            return existing

        existing.latest_transaction_id = transaction_id
        existing.product_id = transaction.product_id
        existing.environment = transaction.environment
        existing.storefront = transaction.storefront
        existing.status = status
        existing.purchased_at = purchase_at
        existing.expires_at = expires_at
        existing.grace_expires_at = grace_expires_at
        existing.revoked_at = revoked_at
        existing.last_signed_at = source_signed_at
        return existing


def get_apple_billing_service() -> AppleBillingService:
    return AppleBillingService(get_apple_jws_verifier())
