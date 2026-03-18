from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from models.token import RevokedToken
from schemas.user import (
    UserCreate, UserResponse, Token, LoginBody,
    ForgotPasswordRequest, ResetPasswordRequest, MessageResponse,
    RefreshTokenRequest, LogoutRequest, UserProfileUpdate,
)
from auth.utils import (
    hash_password, verify_password, create_access_token,
    get_current_user, create_refresh_token, is_token_revoked,
    SECRET_KEY, ALGORITHM,
)

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user.

    Accepts email, password, and full_name. Returns the created user
    without the password. Raises 400 if the email is already registered.
    """
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    new_user = User(
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
        is_verified=True,
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


@router.post("/login", response_model=Token, status_code=status.HTTP_200_OK)
def login(login_data: LoginBody, db: Session = Depends(get_db)):
    """
    Authenticate a user and return a JWT access token.

    Accepts email and password as JSON body. Returns an access token
    if credentials are valid. Raises 401 if email not found or password
    is incorrect.
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

    access_token = create_access_token(data={"sub": user.email})
    refresh_token = create_refresh_token(data={"sub": user.email})
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
def get_me(current_user: User = Depends(get_current_user)):
    """
    Get the currently authenticated user's profile.

    Requires a valid JWT Bearer token in the Authorization header.
    Returns the user's profile information without the password.
    """
    return current_user


@router.put("/profile", response_model=UserResponse)
def update_profile(
    profile: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Update the current user's profile. Only provided (non-None) fields are updated.
    """
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


@router.post("/forgot-password", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Request a password reset. Returns success message regardless of
    whether the email exists (security best practice).
    Email sending will be added post-launch.
    """
    return {"message": "If this email exists, a reset code has been sent"}


@router.post("/reset-password", response_model=MessageResponse, status_code=status.HTTP_200_OK)
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Reset a user's password using the 6-digit code from forgot-password.
    """
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code",
        )
    if (
        user.reset_password_code is None
        or user.reset_password_code_expires is None
        or datetime.now(timezone.utc) > user.reset_password_code_expires
        or not verify_password(data.code, user.reset_password_code)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset code",
        )
    user.hashed_password = hash_password(data.new_password)
    user.reset_password_code = None
    user.reset_password_code_expires = None
    db.commit()
    return {"message": "Password reset successfully"}


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
    # Revoke the access token
    try:
        access_payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        access_jti = access_payload.get("jti")
        if access_jti and not is_token_revoked(access_jti, db):
            revoked_access = RevokedToken(token_jti=access_jti, user_id=current_user.id)
            db.add(revoked_access)
    except JWTError:
        pass

    # Revoke the refresh token
    try:
        refresh_payload = jwt.decode(data.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        refresh_jti = refresh_payload.get("jti")
        if refresh_jti and not is_token_revoked(refresh_jti, db):
            revoked_refresh = RevokedToken(token_jti=refresh_jti, user_id=current_user.id)
            db.add(revoked_refresh)
    except JWTError:
        pass

    db.commit()
    return {"message": "Logged out successfully"}
