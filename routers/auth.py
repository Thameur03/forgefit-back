import os
import random
import string
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from limiter import limiter

from database import get_db
from models.user import User
from models.token import RevokedToken
from schemas.user import (
    UserCreate, UserResponse, Token, LoginBody,
    ForgotPasswordRequest, ResetPasswordRequest, MessageResponse,
    RefreshTokenRequest, LogoutRequest, UserProfileUpdate,
    VerifyEmailRequest, ResendVerificationRequest,
)
from auth.utils import (
    hash_password, verify_password, create_access_token,
    get_current_user, create_refresh_token, is_token_revoked,
    SECRET_KEY, ALGORITHM,
)
from auth.email import send_verification_email, send_password_reset_email

logger = logging.getLogger(__name__)
router = APIRouter()

OTP_EXPIRY_MINUTES = 15


# ── Beta feature flag ───────────────────────────────────────────────────────────────

def _env_bool(name: str, default: bool = True) -> bool:
    """
    Robustly parse a boolean env variable.
    Treats '1', 'true', 'yes', 'on' (case-insensitive, stripped) as True.
    Returns `default` when the variable is not set.
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


REQUIRE_EMAIL_VERIFICATION: bool = _env_bool("REQUIRE_EMAIL_VERIFICATION", default=True)
logger.info("[Auth] REQUIRE_EMAIL_VERIFICATION=%s", REQUIRE_EMAIL_VERIFICATION)


def _generate_otp(length: int = 6) -> str:
    """Generate a random numeric OTP."""
    return "".join(random.choices(string.digits, k=length))


# ═══════════════════════════════════════════════════════════
# REGISTER
# ═══════════════════════════════════════════════════════════

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def register(request: Request, user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user.

    When REQUIRE_EMAIL_VERIFICATION=false (beta mode): creates the account
    with is_verified=True immediately — no OTP or email is generated.

    When REQUIRE_EMAIL_VERIFICATION=true: creates with is_verified=False,
    generates a verification OTP, and sends it via email.
    """
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    if not REQUIRE_EMAIL_VERIFICATION:
        # ── Beta mode: skip verification entirely ──────────────────────────
        logger.info("[Auth] Email verification disabled for beta. Creating verified user.")
        new_user = User(
            email=user_data.email,
            hashed_password=hash_password(user_data.password),
            full_name=user_data.full_name,
            is_verified=True,
            verification_code=None,
            verification_code_expires=None,
            date_of_birth=user_data.date_of_birth,
            gender=user_data.gender,
            weight_kg=user_data.weight_kg,
            height_cm=user_data.height_cm,
            fitness_level=user_data.fitness_level,
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return new_user

    # ── Normal mode: generate OTP, send verification email ─────────────────
    otp = _generate_otp()
    new_user = User(
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
        is_verified=False,
        verification_code=hash_password(otp),
        verification_code_expires=datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES),
        date_of_birth=user_data.date_of_birth,
        gender=user_data.gender,
        weight_kg=user_data.weight_kg,
        height_cm=user_data.height_cm,
        fitness_level=user_data.fitness_level,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Send verification email (non-blocking — errors are logged, not raised)
    send_verification_email(new_user.email, otp)

    return new_user


# ═══════════════════════════════════════════════════════════
# VERIFY EMAIL
# ═══════════════════════════════════════════════════════════

@router.post("/verify-email", response_model=MessageResponse, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def verify_email(request: Request, data: VerifyEmailRequest, db: Session = Depends(get_db)):
    """Verify a user's email using the 6-digit OTP."""
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    if user.is_verified:
        return {"message": "Email is already verified"}

    if (
        user.verification_code is None
        or user.verification_code_expires is None
        or datetime.now(timezone.utc) > user.verification_code_expires
        or not verify_password(data.code, user.verification_code)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        )

    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires = None
    db.commit()

    return {"message": "Email verified successfully"}


# ═══════════════════════════════════════════════════════════
# RESEND VERIFICATION CODE
# ═══════════════════════════════════════════════════════════

@router.post("/resend-verification-code", response_model=MessageResponse, status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
def resend_verification_code(request: Request, data: ResendVerificationRequest, db: Session = Depends(get_db)):
    """Resend the email verification OTP. Returns generic message to avoid email enumeration."""
    user = db.query(User).filter(User.email == data.email).first()

    if user and user.is_verified:
        return {"message": "Email is already verified"}

    if user and not user.is_verified:
        otp = _generate_otp()
        user.verification_code = hash_password(otp)
        user.verification_code_expires = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)
        db.commit()
        send_verification_email(user.email, otp)

    # Always return a generic message
    return {"message": "If this email is registered, a new verification code has been sent"}


# ═══════════════════════════════════════════════════════════
# LOGIN
# ═══════════════════════════════════════════════════════════

@router.post("/login", response_model=Token, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def login(request: Request, login_data: LoginBody, db: Session = Depends(get_db)):
    """
    Authenticate a user and return a JWT access token.
    Blocks unverified users with a 403 status.
    """
    user = db.query(User).filter(User.email == login_data.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    logger.info(
        "[Auth] Login verification_check enabled=%s user_verified=%s email=%s",
        REQUIRE_EMAIL_VERIFICATION,
        user.is_verified,
        user.email,
    )
    # Block login for unverified users (skipped when email verification is disabled)
    if REQUIRE_EMAIL_VERIFICATION and not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in.",
        )

    # Track last login time
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    access_token = create_access_token(data={"sub": user.email})
    refresh_token = create_refresh_token(data={"sub": user.email})
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


# ═══════════════════════════════════════════════════════════
# GET ME
# ═══════════════════════════════════════════════════════════

@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
def get_me(current_user: User = Depends(get_current_user)):
    """Get the currently authenticated user's profile."""
    return current_user


# ═══════════════════════════════════════════════════════════
# UPDATE PROFILE
# ═══════════════════════════════════════════════════════════

@router.put("/profile", response_model=UserResponse)
def update_profile(
    profile: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the current user's profile. Only provided (non-None) fields are updated."""
    if profile.full_name is not None:
        current_user.full_name = profile.full_name
    if profile.date_of_birth is not None:
        current_user.date_of_birth = profile.date_of_birth
    if profile.gender is not None:
        current_user.gender = profile.gender
    if profile.weight_kg is not None:
        current_user.weight_kg = profile.weight_kg
    if profile.height_cm is not None:
        current_user.height_cm = profile.height_cm
    if profile.fitness_level is not None:
        current_user.fitness_level = profile.fitness_level
    db.commit()
    db.refresh(current_user)
    return current_user


# ═══════════════════════════════════════════════════════════
# FORGOT PASSWORD
# ═══════════════════════════════════════════════════════════

@router.post("/forgot-password", response_model=MessageResponse, status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
def forgot_password(request: Request, data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Request a password reset OTP. Always returns a generic success message
    regardless of whether the email exists (security best practice).
    """
    user = db.query(User).filter(User.email == data.email).first()
    if user:
        otp = _generate_otp()
        user.reset_password_code = hash_password(otp)
        user.reset_password_code_expires = datetime.now(timezone.utc) + timedelta(minutes=OTP_EXPIRY_MINUTES)
        db.commit()
        delivered = send_password_reset_email(user.email, otp)
        if not delivered:
            logger.warning("[Auth] Reset email delivery failed for user (details in email.py logs)")

    return {"message": "If this email exists, a reset code has been sent"}



# ═══════════════════════════════════════════════════════════
# RESET PASSWORD
# ═══════════════════════════════════════════════════════════

@router.post("/reset-password", response_model=MessageResponse, status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
def reset_password(request: Request, data: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Reset a user's password using the 6-digit code from forgot-password."""
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code",
        )
    if (
        user.reset_password_code is None
        or user.reset_password_code_expires is None
        or not verify_password(data.code, user.reset_password_code)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code",
        )
    # Normalize expiry to UTC regardless of whether the DB driver returns
    # a timezone-aware or timezone-naive datetime (SQLite returns naive).
    expires = user.reset_password_code_expires
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code",
        )
    user.hashed_password = hash_password(data.new_password)
    user.reset_password_code = None
    user.reset_password_code_expires = None
    db.commit()
    return {"message": "Password reset successfully"}


# ═══════════════════════════════════════════════════════════
# REFRESH
# ═══════════════════════════════════════════════════════════

@router.post("/refresh", response_model=Token, status_code=status.HTTP_200_OK)
def refresh_tokens(data: RefreshTokenRequest, db: Session = Depends(get_db)):
    """
    Refresh access and refresh tokens. The old refresh token is revoked
    and new tokens are issued.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )
    try:
        payload = jwt.decode(data.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        jti: str = payload.get("jti")
        token_type: str = payload.get("type")
        if email is None or token_type != "refresh" or jti is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Check if refresh token is revoked
    if jti and is_token_revoked(jti, db):
        raise credentials_exception

    # Find the user
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception

    # Revoke the old refresh token
    revoked = RevokedToken(token_jti=jti, user_id=user.id)
    db.add(revoked)
    db.commit()

    # Issue new tokens
    new_access_token = create_access_token(data={"sub": user.email})
    new_refresh_token = create_refresh_token(data={"sub": user.email})
    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


# ═══════════════════════════════════════════════════════════
# LOGOUT
# ═══════════════════════════════════════════════════════════

@router.post("/logout", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def logout(
    data: LogoutRequest,
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Logout the current user by revoking both the access token and refresh token.
    Requires a valid access token in the Authorization header.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or mismatched refresh token",
    )

    # Validate the refresh token before mutating the revocation table. A user
    # may only revoke a refresh token belonging to their own authenticated
    # account, and an access token cannot be substituted in this field.
    try:
        refresh_payload = jwt.decode(
            data.refresh_token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )
    except JWTError as exc:
        raise credentials_exception from exc

    refresh_email = refresh_payload.get("sub")
    refresh_jti = refresh_payload.get("jti")
    refresh_type = refresh_payload.get("type")
    if (
        refresh_email != current_user.email
        or refresh_jti is None
        or refresh_type != "refresh"
    ):
        raise credentials_exception

    # Revoke the access token
    try:
        access_payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        access_jti = access_payload.get("jti")
        if access_jti and not is_token_revoked(access_jti, db):
            revoked_access = RevokedToken(token_jti=access_jti, user_id=current_user.id)
            db.add(revoked_access)
    except JWTError:
        pass

    if not is_token_revoked(refresh_jti, db):
        revoked_refresh = RevokedToken(token_jti=refresh_jti, user_id=current_user.id)
        db.add(revoked_refresh)

    current_user.last_logout_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Logged out successfully"}
