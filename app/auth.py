from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request, APIRouter
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import settings
from app import models

# 🚀 NEW: Import your Telegram alert helper
from app.tasks import send_sync_telegram_alert

# --- Router Initialization ---
# This allows you to include these routes in main.py
router = APIRouter(prefix="/auth", tags=["Authentication"])

# --- Security Configuration ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# --- Password Helpers ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# --- JWT Token Creation ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    now_utc = datetime.now(timezone.utc)

    if expires_delta:
        expire = now_utc + expires_delta
    else:
        expire = now_utc + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

# --- Dependency: Get Current User (Used for Protected Routes) ---
def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        raise credentials_exception
    return user

# --- Machine Authentication (For Kiosk Hardware) ---
def verify_machine_token(request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("x-machine-token")
    client_ip = request.client.host if request.client else "unknown"
    
    if not token:
        alert_msg = (
            f"🚨 <b>Machine Auth Warning</b>\n"
            f"Missing token from IP: <code>{client_ip}</code>"
        )
        send_sync_telegram_alert(alert_msg)
        raise HTTPException(status_code=401, detail="Missing Machine Token")

    machine = db.query(models.Machine).filter(models.Machine.api_key == token).first()
    if not machine:
        masked_token = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "INVALID_FORMAT"
        alert_msg = (
            f"🚨 <b>Invalid Machine Authentication</b>\n"
            f"Attempted Token: <code>{masked_token}</code>\n"
            f"IP: <code>{client_ip}</code>"
        )
        send_sync_telegram_alert(alert_msg)
        raise HTTPException(status_code=401, detail="Invalid Machine Token")
        
    return machine

# --- Internal Service Authentication ---
def verify_internal_api_key(request: Request):
    token = request.headers.get("x-internal-token")
    if token != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Access Denied")
    return True

# --- Role Based Access Control ---
def role_required(allowed_roles: list):
    def dependency(current_user: models.User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions"
            )
        return current_user
    return dependency
