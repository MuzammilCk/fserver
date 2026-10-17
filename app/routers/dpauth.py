from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.database import get_db
from app.models import User, ConnectionLog
from app.schemas import (
    LoginRequest,
    OTPVerifyRequest,
    LoginResponse,
    Token,
)
from app.utils.hashing import verify_password
from app.auth import create_access_token 
from app.services.email_service import send_email_alert 
from app.enums import UserRole
from secrets import randbelow

router = APIRouter(tags=["auth"])

# -------------------------------------------------
# Utility: Generate 6-digit OTP
# -------------------------------------------------
def generate_otp():
    return f"{randbelow(900000) + 100000}"

# -------------------------------------------------
# Step 1: Login request → Generate OTP
# -------------------------------------------------
@router.post("/login")
def request_otp(
    data: LoginRequest, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check for account lock
    if user.locked_until and user.locked_until > datetime.utcnow():
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
        raise HTTPException(status_code=403, detail=f"Account locked. Try again in {remaining} minutes")

    # =========================================================
    # 🚀 PLAY STORE REVIEWER BYPASS
    # =========================================================
    if user.email == "playstore@hynatechnologies.in":
        user.otp_code = "123456"
        # Set expiry to 10 years so it never expires during review
        user.otp_expires_at = datetime.utcnow() + timedelta(days=3650)
        user.failed_login_attempts = 0
        db.commit()
        return {"message": "OTP bypassed for App Reviewer"}
    # =========================================================

    otp = generate_otp()
    user.otp_code = otp
    user.otp_expires_at = datetime.utcnow() + timedelta(minutes=5)

    # Reset login attempts
    user.failed_login_attempts = 0
    db.commit()

    # ✅ SEND EMAIL (Run in background so UI doesn't freeze)
    print(f"[DEBUG] Sending OTP to {user.email}...") 
    
    background_tasks.add_task(
        send_email_alert,
        subject="HyNa Admin Login OTP",
        body=f"Hello {user.full_name or 'Admin'},\n\nYour OTP for login is: {otp}\n\nThis code expires in 5 minutes.",
        to_emails=user.email
    )

    return {"message": "OTP sent to registered email"}

# -------------------------------------------------
# Step 2: Verify OTP → Return JWT Token
# -------------------------------------------------
@router.post("/verify", response_model=LoginResponse)
def verify_otp(data: OTPVerifyRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # =========================================================
    # 🚀 PLAY STORE REVIEWER BYPASS
    # =========================================================
    is_reviewer = (user.email == "playstore@hynatechnologies.in" and data.otp == "123456")

    if not is_reviewer:
        # Normal User Validation
        if not user.otp_code or not user.otp_expires_at:
            raise HTTPException(status_code=400, detail="OTP not generated")

        if user.otp_expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="OTP expired")

        if user.otp_code != data.otp:
            user.failed_login_attempts += 1

            if user.failed_login_attempts >= 5:
                user.locked_until = datetime.utcnow() + timedelta(minutes=15)

            db.commit()
            raise HTTPException(status_code=401, detail="Invalid OTP")

    # OTP correct — reset
    user.otp_code = None
    user.otp_expires_at = None
    user.failed_login_attempts = 0
    user.locked_until = None

    # =========================================================
    # ✅ HISTORY LOGIC: Record Admin Login
    # =========================================================
    login_log = ConnectionLog(
        user_id=user.id,
        event_type="ADMIN_LOGIN",
        timestamp=datetime.utcnow()
    )
    db.add(login_log)
    # =========================================================

    db.commit()

    token = create_access_token({"sub": user.email})

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        role=user.role.value if hasattr(user.role, 'value') else str(user.role),
        name=user.full_name or "",
        email=user.email,
    )

# -------------------------------------------------
# Validate JWT
# -------------------------------------------------
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from app.config import settings

# Note: This tokenUrl must match the login endpoint for Swagger to work nicely
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/server/auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return user

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
        
@router.post("/update-fcm")
async def update_fcm_token(
    payload: dict, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    fcm_token = payload.get("fcm_token")
    if not fcm_token:
        raise HTTPException(status_code=400, detail="Token is required")
    
    current_user.fcm_token = fcm_token
    db.commit()
    
    return {"status": "success", "message": "FCM token updated"}
