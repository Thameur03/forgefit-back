"""Security and entitlement coverage for DAUNTRA Premium on Apple."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from auth.utils import create_access_token, hash_password
from main import app
from models.billing import (
    AppleNotificationEvent,
    AppleSubscription,
    BillingIdentity,
)
from models.user import User
from routers.billing import billing_service_dependency
from services.apple_billing import (
    AppleBillingService,
    AppleVerificationError,
    OfficialAppleJWSVerifier,
    PREMIUM_PRODUCT_IDS,
    VerifiedAppleNotification,
    VerifiedAppleTransaction,
    get_apple_jws_verifier,
)
from tests.support import TestingSessionLocal, client


WEEKLY = "dauntra_premium_weekly"
MONTHLY = "dauntra_premium_monthly"
YEARLY = "dauntra_premium_yearly"
SIGNED_TRANSACTION = "transaction-" + "x" * 64
SIGNED_NOTIFICATION = "notification-" + "x" * 64


class FakeAppleVerifier:
    allowed_environments = frozenset({"Sandbox", "Production"})

    def __init__(self):
        self.transactions: dict[str, VerifiedAppleTransaction | Exception] = {}
        self.notifications: dict[str, VerifiedAppleNotification | Exception] = {}

    def verify_transaction(self, value: str) -> VerifiedAppleTransaction:
        result = self.transactions.get(value)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise AppleVerificationError("invalid signed transaction")
        return result

    def verify_notification(self, value: str) -> VerifiedAppleNotification:
        result = self.notifications.get(value)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise AppleVerificationError("invalid signed notification")
        return result


@pytest.fixture
def apple_verifier():
    verifier = FakeAppleVerifier()
    service = AppleBillingService(verifier)
    app.dependency_overrides[billing_service_dependency] = lambda: service
    try:
        yield verifier
    finally:
        app.dependency_overrides.pop(billing_service_dependency, None)


def _millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _seed_user(email: str) -> tuple[int, str]:
    db = TestingSessionLocal()
    user = User(
        email=email,
        hashed_password=hash_password("Password1"),
        full_name="Billing Tester",
        is_verified=True,
    )
    db.add(user)
    db.commit()
    user_id = user.id
    token = create_access_token({"sub": email})
    db.close()
    return user_id, token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _account_token(access_token: str) -> str:
    response = client.get("/billing/entitlement", headers=_auth(access_token))
    assert response.status_code == 200, response.text
    value = response.json()["app_account_token"]
    assert str(uuid.UUID(value)) == value
    return value


def _transaction(
    account_token: str,
    *,
    product_id: str = WEEKLY,
    transaction_id: str = "200000000000001",
    original_transaction_id: str = "100000000000001",
    expires_delta: timedelta = timedelta(days=7),
    signed_offset: timedelta = timedelta(),
    revocation_date: datetime | None = None,
    bundle_id: str = "com.dauntra.app",
    environment: str = "Sandbox",
) -> VerifiedAppleTransaction:
    now = datetime.now(timezone.utc)
    return VerifiedAppleTransaction(
        original_transaction_id=original_transaction_id,
        transaction_id=transaction_id,
        product_id=product_id,
        bundle_id=bundle_id,
        environment=environment,
        app_account_token=account_token,
        purchase_date_ms=_millis(now - timedelta(minutes=1)),
        expires_date_ms=_millis(now + expires_delta),
        signed_date_ms=_millis(now + signed_offset),
        revocation_date_ms=(
            _millis(revocation_date) if revocation_date is not None else None
        ),
        storefront="USA",
    )


def _submit(
    verifier: FakeAppleVerifier,
    access_token: str,
    transaction: VerifiedAppleTransaction,
):
    verifier.transactions[SIGNED_TRANSACTION] = transaction
    return client.post(
        "/billing/apple/transaction",
        json={"signed_transaction_info": SIGNED_TRANSACTION},
        headers=_auth(access_token),
    )


@pytest.mark.parametrize("product_id", [WEEKLY, MONTHLY, YEARLY])
def test_each_allowed_product_grants_the_same_premium_entitlement(
    apple_verifier, product_id
):
    _, access_token = _seed_user(f"{product_id}@example.com")
    account_token = _account_token(access_token)

    response = _submit(
        apple_verifier,
        access_token,
        _transaction(account_token, product_id=product_id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["entitlement"] == "premium"
    assert response.json()["subscription_status"] == "active"
    assert response.json()["product_id"] == product_id


def test_product_allowlist_contains_exactly_weekly_monthly_and_yearly():
    assert PREMIUM_PRODUCT_IDS == {WEEKLY, MONTHLY, YEARLY}


def test_expired_weekly_subscription_is_free(apple_verifier):
    _, access_token = _seed_user("expired-weekly@example.com")
    account_token = _account_token(access_token)
    response = _submit(
        apple_verifier,
        access_token,
        _transaction(account_token, expires_delta=timedelta(seconds=-1)),
    )
    assert response.status_code == 200
    assert response.json()["entitlement"] == "free"
    assert response.json()["subscription_status"] == "expired"


def test_revoked_weekly_subscription_is_free(apple_verifier):
    _, access_token = _seed_user("revoked-weekly@example.com")
    account_token = _account_token(access_token)
    response = _submit(
        apple_verifier,
        access_token,
        _transaction(
            account_token,
            revocation_date=datetime.now(timezone.utc),
        ),
    )
    assert response.status_code == 200
    assert response.json()["entitlement"] == "free"
    assert response.json()["subscription_status"] == "revoked"


@pytest.mark.parametrize(
    ("transaction", "status_code"),
    [
        (lambda token: _transaction(token, product_id="unknown_product"), 400),
        (lambda token: _transaction(token, bundle_id="com.forgefit.forgefit"), 400),
        (lambda token: _transaction(token, environment="Xcode"), 400),
    ],
)
def test_unknown_product_wrong_bundle_and_wrong_environment_are_rejected(
    apple_verifier, transaction, status_code
):
    _, access_token = _seed_user(f"reject-{uuid.uuid4()}@example.com")
    account_token = _account_token(access_token)
    response = _submit(apple_verifier, access_token, transaction(account_token))
    assert response.status_code == status_code


def test_invalid_and_retryable_signatures_fail_closed(apple_verifier):
    _, access_token = _seed_user("invalid-jws@example.com")
    invalid = client.post(
        "/billing/apple/transaction",
        json={"signed_transaction_info": SIGNED_TRANSACTION},
        headers=_auth(access_token),
    )
    assert invalid.status_code == 400

    apple_verifier.transactions[SIGNED_TRANSACTION] = AppleVerificationError(
        "OCSP unavailable", retryable=True
    )
    unavailable = client.post(
        "/billing/apple/transaction",
        json={"signed_transaction_info": SIGNED_TRANSACTION},
        headers=_auth(access_token),
    )
    assert unavailable.status_code == 503


def test_malformed_transaction_is_rejected(apple_verifier):
    _, access_token = _seed_user("malformed@example.com")
    token = _account_token(access_token)
    malformed = _transaction(token)
    malformed = VerifiedAppleTransaction(
        **{**malformed.__dict__, "expires_date_ms": None}
    )
    response = _submit(apple_verifier, access_token, malformed)
    assert response.status_code == 400


def test_transaction_submission_is_idempotent(apple_verifier):
    _, access_token = _seed_user("duplicate@example.com")
    token = _account_token(access_token)
    transaction = _transaction(token)

    first = _submit(apple_verifier, access_token, transaction)
    second = _submit(apple_verifier, access_token, transaction)

    assert first.status_code == second.status_code == 200
    db = TestingSessionLocal()
    assert db.query(AppleSubscription).count() == 1
    db.close()


def test_purchase_cannot_be_bound_or_replayed_to_another_user(apple_verifier):
    _, token_a = _seed_user("billing-a@example.com")
    _, token_b = _seed_user("billing-b@example.com")
    account_a = _account_token(token_a)
    transaction = _transaction(account_a)
    assert _submit(apple_verifier, token_a, transaction).status_code == 200

    replay = _submit(apple_verifier, token_b, transaction)
    assert replay.status_code == 409


def test_user_cannot_read_another_users_entitlement(apple_verifier):
    _, token_a = _seed_user("entitlement-a@example.com")
    _, token_b = _seed_user("entitlement-b@example.com")
    account_a = _account_token(token_a)
    assert _submit(apple_verifier, token_a, _transaction(account_a)).status_code == 200

    response_b = client.get("/billing/entitlement", headers=_auth(token_b))
    assert response_b.status_code == 200
    assert response_b.json()["entitlement"] == "free"
    assert response_b.json()["app_account_token"] != account_a


def test_entitlement_endpoint_requires_authentication(apple_verifier):
    response = client.get("/billing/entitlement")
    assert response.status_code in (401, 403)


def _notification(
    transaction: VerifiedAppleTransaction,
    *,
    notification_type: str,
    status: str | None,
    notification_uuid: str | None = None,
    signed_offset: timedelta = timedelta(seconds=1),
    subtype: str | None = None,
    grace_delta: timedelta | None = None,
) -> VerifiedAppleNotification:
    now = datetime.now(timezone.utc)
    return VerifiedAppleNotification(
        notification_uuid=notification_uuid or str(uuid.uuid4()),
        notification_type=notification_type,
        subtype=subtype,
        signed_date_ms=_millis(now + signed_offset),
        bundle_id="com.dauntra.app",
        environment="Sandbox",
        status=status,
        transaction=transaction,
        renewal_app_account_token=transaction.app_account_token,
        grace_expires_date_ms=(
            _millis(now + grace_delta) if grace_delta is not None else None
        ),
    )


def _notify(
    verifier: FakeAppleVerifier, notification: VerifiedAppleNotification
):
    verifier.notifications[SIGNED_NOTIFICATION] = notification
    return client.post(
        "/billing/apple/notifications",
        json={"signedPayload": SIGNED_NOTIFICATION},
    )


def test_notification_endpoint_uses_signed_payload_not_user_auth(apple_verifier):
    _, access_token = _seed_user("notification-auth@example.com")
    token = _account_token(access_token)
    response = _notify(
        apple_verifier,
        _notification(_transaction(token), notification_type="DID_RENEW", status="1"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "updated"


def test_duplicate_notification_is_replay_safe(apple_verifier):
    _, access_token = _seed_user("notification-replay@example.com")
    token = _account_token(access_token)
    notification = _notification(
        _transaction(token),
        notification_type="DID_RENEW",
        status="1",
        notification_uuid="8bd26d45-928a-4db9-a69e-e1a581facd97",
    )
    first = _notify(apple_verifier, notification)
    second = _notify(apple_verifier, notification)

    assert first.json()["duplicate"] is False
    assert second.json() == {
        "accepted": True,
        "duplicate": True,
        "outcome": "duplicate",
    }
    db = TestingSessionLocal()
    assert db.query(AppleNotificationEvent).count() == 1
    db.close()


@pytest.mark.parametrize(
    ("kind", "status", "expected_status", "premium"),
    [
        ("EXPIRED", "2", "expired", False),
        ("DID_FAIL_TO_RENEW", "3", "billing_retry", False),
        ("REFUND", "5", "revoked", False),
        ("REVOKE", "5", "revoked", False),
    ],
)
def test_expiration_retry_refund_and_revocation_notifications(
    apple_verifier, kind, status, expected_status, premium
):
    _, access_token = _seed_user(f"notification-{kind}-{uuid.uuid4()}@example.com")
    token = _account_token(access_token)
    transaction = _transaction(token)
    assert _submit(apple_verifier, access_token, transaction).status_code == 200

    response = _notify(
        apple_verifier,
        _notification(transaction, notification_type=kind, status=status),
    )
    assert response.status_code == 200, response.text
    entitlement = client.get(
        "/billing/entitlement", headers=_auth(access_token)
    ).json()
    assert entitlement["subscription_status"] == expected_status
    assert (entitlement["entitlement"] == "premium") is premium


def test_grace_period_is_premium_only_until_verified_grace_expiration(
    apple_verifier,
):
    _, access_token = _seed_user("grace@example.com")
    token = _account_token(access_token)
    expired = _transaction(token, expires_delta=timedelta(seconds=-1))
    response = _notify(
        apple_verifier,
        _notification(
            expired,
            notification_type="DID_FAIL_TO_RENEW",
            subtype="GRACE_PERIOD",
            status="4",
            grace_delta=timedelta(days=3),
        ),
    )
    assert response.status_code == 200
    entitlement = client.get(
        "/billing/entitlement", headers=_auth(access_token)
    ).json()
    assert entitlement["entitlement"] == "premium"
    assert entitlement["subscription_status"] == "grace_period"


def test_renewal_reactivates_an_expired_subscription(apple_verifier):
    _, access_token = _seed_user("renewal@example.com")
    token = _account_token(access_token)
    expired = _transaction(token, expires_delta=timedelta(seconds=-1))
    assert _submit(apple_verifier, access_token, expired).json()["entitlement"] == "free"

    renewed = _transaction(
        token,
        transaction_id="200000000000002",
        expires_delta=timedelta(days=7),
        signed_offset=timedelta(seconds=2),
    )
    response = _notify(
        apple_verifier,
        _notification(
            renewed,
            notification_type="DID_RENEW",
            status="1",
            signed_offset=timedelta(seconds=3),
        ),
    )
    assert response.status_code == 200
    entitlement = client.get(
        "/billing/entitlement", headers=_auth(access_token)
    ).json()
    assert entitlement["entitlement"] == "premium"
    assert entitlement["product_id"] == WEEKLY


def test_notification_for_unknown_account_is_verified_but_does_not_grant(
    apple_verifier,
):
    unknown_token = str(uuid.uuid4())
    response = _notify(
        apple_verifier,
        _notification(
            _transaction(unknown_token),
            notification_type="DID_RENEW",
            status="1",
        ),
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "unbound"
    db = TestingSessionLocal()
    assert db.query(AppleSubscription).count() == 0
    db.close()


def test_account_deletion_removes_user_billing_binding_and_state(apple_verifier):
    user_id, access_token = _seed_user("billing-delete@example.com")
    token = _account_token(access_token)
    assert _submit(apple_verifier, access_token, _transaction(token)).status_code == 200

    response = client.delete("/account/me", headers=_auth(access_token))
    assert response.status_code == 200
    db = TestingSessionLocal()
    assert db.query(BillingIdentity).filter(BillingIdentity.user_id == user_id).count() == 0
    assert db.query(AppleSubscription).filter(AppleSubscription.user_id == user_id).count() == 0
    db.close()


def test_official_verifier_rejects_unsigned_data(monkeypatch):
    monkeypatch.setenv("APPLE_BUNDLE_ID", "com.dauntra.app")
    monkeypatch.setenv("APPLE_IAP_ENVIRONMENTS", "SANDBOX")
    monkeypatch.setenv("APPLE_IAP_ONLINE_CHECKS", "false")
    get_apple_jws_verifier.cache_clear()
    try:
        verifier = get_apple_jws_verifier()
        assert isinstance(verifier, OfficialAppleJWSVerifier)
        with pytest.raises(AppleVerificationError):
            verifier.verify_transaction("unsigned-data-is-not-a-jws")
    finally:
        get_apple_jws_verifier.cache_clear()


def test_billing_payload_size_is_bounded_before_verification(apple_verifier):
    response = client.post(
        "/billing/apple/notifications",
        content=b"x" * (64 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
