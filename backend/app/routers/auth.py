from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import RegistrationRequest, User
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    ProfileUpdate,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserOut,
)
from ..security import create_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        # A pending applicant deserves a clearer answer than "wrong password".
        if user is None and db.scalar(
            select(RegistrationRequest).where(RegistrationRequest.email == body.email.lower())
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Your registration is awaiting approval by the bank manager",
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")
    return TokenResponse(access_token=create_token(user))


@router.post("/register", response_model=RegisterResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    if db.scalar(select(RegistrationRequest).where(RegistrationRequest.email == email)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A registration for this email is already awaiting approval",
        )
    db.add(
        RegistrationRequest(
            display_name=body.display_name.strip(),
            email=email,
            whatsapp_number=body.whatsapp_number,
            password_hash=hash_password(body.password),
        )
    )
    db.commit()
    return RegisterResponse(
        message="Registration submitted. The bank manager will review your request."
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.put("/profile", response_model=UserOut)
def update_profile(
    body: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.display_name is not None:
        name = body.display_name.strip()
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Display name cannot be empty")
        user.display_name = name
    if body.preferred_language is not None:
        user.preferred_language = body.preferred_language
    if body.whatsapp_number is not None:
        user.whatsapp_number = body.whatsapp_number
    if body.mcp_trading_enabled is not None:
        user.mcp_trading_enabled = body.mcp_trading_enabled
    db.commit()
    db.refresh(user)
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    db.commit()
