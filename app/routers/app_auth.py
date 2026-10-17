from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, constr, EmailStr
from sqlalchemy.orm import Session as DBSession
from datetime import datetime, timedelta
from uuid import uuid4
import random
import jwt
from collections import defaultdict
import time

from app.schemas import CustomerUpdatePayload
from app.database import get_db
from app.models import Customer, OtpRecord
from app.config import settings
from app.customer_auth import get_current_customer
from app import auth  
from app.services.email_service import send_email_alert
from app.tasks import send_sync_telegram_alert  # 🚀 NEW: Telegram alerts

router = APIRouter(tags=["app_auth"])

JWT_SECRET = settings.SECRET_KEY
ALGORITHM = "HS256"

# -------------------------------------------------
# 🚀 OTP Brute-Force Tracker (In-Memory)
# -------------------------------------------------
otp_guess_limits = defaultdict(lambda: {"count": 0, "lockout_until": 0})

# --- Schemas ---
class SignUpPayload(BaseModel):
    name: str
    phone: constr(min_length=10, max_length=15)
    email: EmailStr | None = None

class LoginPayload(BaseModel):
    phone: constr(min_length=10, max_length=15)

class VerifyOtpPayload(BaseModel):
    phone: str
    otp_code: str


# =======================================================================
# 🚀 HELPER: OTP Generator & Dispatcher
# =======================================================================
def generate_and_send_otp(db: DBSession, phone: str, target_email: str = None):
    # 🚀 FIX: Enforce a reasonable 1-Hour Rate Limit (Max 5 requests per hour)
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    recent_otps = db.query(OtpRecord).filter(
        OtpRecord.phone == phone, OtpRecord.created_at >= one_hour_ago
    ).count()

    if recent_otps >= 5:
        raise HTTPException(status_code=429, detail="Limit of 5 OTP requests per hour reached. Please try again later.")

    # Generate OTP
    otp_code = str(random.randint(1000, 9999))
    db.add(OtpRecord(phone=phone, otp_code=otp_code, expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()

    # 📨 Route through Real Email Service if an email profile exists
    if target_email:
        email_body = (
            f"Hello,\n\n"
            f"Your verification code for HyNa Print Vending is: {otp_code}\n\n"
            f"This code expires in 10 minutes. Do not share it with anyone."
        )
        send_email_alert(
            subject="🔐 HyNa Print - Your Verification Code",
            body=email_body,
            to_emails=target_email
        )
    else:
        # Fallback logging if phone has no email record linked
        print(f"📲 MOCK SMS FALLBACK: Phone {phone} has no email address. Code: {otp_code}")

    return {"message": "OTP code dispatched successfully.", "expires_in_minutes": 10}


# =======================================================================
# 1) SIGN UP (Create Account & Send OTP via Email)
# =======================================================================
@router.post("/signup")
def create_account(payload: SignUpPayload, db: DBSession = Depends(get_db)):
    existing_user = db.query(Customer).filter_by(phone=payload.phone).first()

    if existing_user:
        if existing_user.is_verified:
            raise HTTPException(status_code=400, detail="Account already exists. Please login.")
        else:
            existing_user.name = payload.name
            existing_user.email = payload.email
            db.commit()
            return generate_and_send_otp(db, payload.phone, target_email=payload.email)

    new_customer = Customer(
        id=str(uuid4()),
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        is_verified=False
    )
    db.add(new_customer)
    db.commit()

    return generate_and_send_otp(db, payload.phone, target_email=payload.email)


# =======================================================================
# 2) LOGIN (Send OTP via stored Customer Email address)
# =======================================================================
@router.post("/login/send-otp")
def login_request_otp(payload: LoginPayload, db: DBSession = Depends(get_db)):
    customer = db.query(Customer).filter_by(phone=payload.phone).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Account not found. Please sign up first.")

    return generate_and_send_otp(db, payload.phone, target_email=customer.email)


# =======================================================================
# 3) VERIFY OTP & GET TOKEN
# =======================================================================
@router.post("/verify-otp")
def verify_otp(
    payload: VerifyOtpPayload, 
    request: Request, # 🚀 Added to capture attacker IP
    db: DBSession = Depends(get_db)
):
    client_ip = request.client.host if request.client else "unknown"
    current_time = time.time()

    # 🚀 1. Check if IP is locked out from guessing
    if current_time < otp_guess_limits[client_ip]["lockout_until"]:
        remaining = int((otp_guess_limits[client_ip]["lockout_until"] - current_time) / 60) + 1
        raise HTTPException(status_code=429, detail=f"Too many failed attempts. Try again in {remaining} minutes.")

    # 2. Fetch the most recent active OTP
    otp_record = db.query(OtpRecord).filter(
        OtpRecord.phone == payload.phone,
        OtpRecord.expires_at > datetime.utcnow()
    ).order_by(OtpRecord.created_at.desc()).first()

    if not otp_record:
        raise HTTPException(status_code=400, detail="No active OTP found. Please request a new one.")

    # 🚀 3. Process Failed Guesses
    if otp_record.otp_code != payload.otp_code:
        otp_guess_limits[client_ip]["count"] += 1
        
        if otp_guess_limits[client_ip]["count"] >= 3:
            # Lock out the IP for 15 minutes
            otp_guess_limits[client_ip]["lockout_until"] = current_time + 900
            otp_guess_limits[client_ip]["count"] = 0
            
            # Immediately destroy the OTP record so it can't be guessed by another IP
            db.delete(otp_record)
            db.commit()

            # Trigger Telegram Alert
            alert_msg = (
                f"🚨 <b>Customer OTP Brute-Force Blocked</b>\n"
                f"Target Phone: <code>{payload.phone}</code>\n"
                f"Attacker IP: <code>{client_ip}</code>\n"
                f"Action: IP locked for 15m and OTP destroyed."
            )
            send_sync_telegram_alert(alert_msg)

            raise HTTPException(status_code=429, detail="Account locked for 15 minutes due to multiple failed attempts.")

        remaining_guesses = 3 - otp_guess_limits[client_ip]["count"]
        raise HTTPException(status_code=400, detail=f"Invalid OTP. {remaining_guesses} attempts remaining.")

    # 4. Process Successful Verification
    otp_guess_limits[client_ip] = {"count": 0, "lockout_until": 0} # Reset limits
    
    customer = db.query(Customer).filter_by(phone=payload.phone).first()
    customer.is_verified = True
    db.delete(otp_record)
    db.commit()

    token_data = {"sub": customer.id, "role": "customer", "exp": datetime.utcnow() + timedelta(days=30)}
    safe_secret = JWT_SECRET.ljust(32, '0')
    access_token = jwt.encode(token_data, safe_secret, algorithm=ALGORITHM)

    return {
        "access_token": access_token,
        "customer": {"id": customer.id, "name": customer.name, "phone": customer.phone}
    }


# =======================================================================
# 4) GET MY PROFILE (/me)
# =======================================================================
@router.get("/me")
def get_my_profile(current_customer: Customer = Depends(get_current_customer)):
    return {
        "id": current_customer.id,
        "name": current_customer.name,
        "phone": current_customer.phone,
        "email": current_customer.email,
        "joined_date": current_customer.created_at.strftime("%B %Y") if current_customer.created_at else None
    }


# =======================================================================
# 5) GET ALL CUSTOMERS (Super Admin Only)
# =======================================================================
@router.get("/customers")
def get_all_customers(
    skip: int = 0,
    limit: int = 100,
    db: DBSession = Depends(get_db),
    current_user = Depends(auth.role_required(["super_admin"]))
):
    customers = db.query(Customer).order_by(Customer.created_at.desc()).offset(skip).limit(limit).all()

    return [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "email": c.email,
            "is_verified": c.is_verified,
            "joined_date": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else None
        }
        for c in customers
    ]

# =======================================================================
# 6) DELETE CUSTOMER (Super Admin Only)
# =======================================================================
@router.delete("/customers/{customer_id}")
def delete_customer(
    customer_id: str,
    db: DBSession = Depends(get_db),
    current_user = Depends(auth.role_required(["super_admin"]))
):
    customer = db.query(Customer).filter_by(id=customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    customer_name = customer.name

    try:
        db.delete(customer)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Cannot delete customer. They may have active print jobs or transaction history tied to their account."
        )

    return {"message": f"Customer '{customer_name}' has been successfully deleted."}


# =======================================================================
# 7) EDIT CUSTOMER (Super Admin Only)
# =======================================================================
@router.patch("/customers/{customer_id}")
def update_customer_by_admin(
    customer_id: str,
    payload: CustomerUpdatePayload,
    db: DBSession = Depends(get_db),
    current_user = Depends(auth.role_required(["super_admin"]))
):
    customer = db.query(Customer).filter_by(id=customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    if payload.phone and payload.phone != customer.phone:
        existing_phone = db.query(Customer).filter_by(phone=payload.phone).first()
        if existing_phone:
            raise HTTPException(status_code=400, detail="Phone number is already associated with another account.")

    update_data = payload.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(customer, key, value)

    db.commit()
    db.refresh(customer)

    return {"message": f"Customer '{customer.name}' updated successfully."}


# =======================================================================
# 8) EDIT MY PROFILE (Customer updating their own account)
# =======================================================================
@router.patch("/me")
def update_my_profile(
    payload: CustomerUpdatePayload,
    db: DBSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer)
):
    if payload.phone and payload.phone != current_customer.phone:
        existing_phone = db.query(Customer).filter_by(phone=payload.phone).first()
        if existing_phone:
            raise HTTPException(status_code=400, detail="Phone number is already associated with another account.")

    update_data = payload.dict(exclude_unset=True)
    if "is_verified" in update_data:
        del update_data["is_verified"]

    for key, value in update_data.items():
        setattr(current_customer, key, value)

    db.commit()
    db.refresh(current_customer)

    return {
        "message": "Profile updated successfully.",
        "profile": {
            "id": current_customer.id,
            "name": current_customer.name,
            "phone": current_customer.phone,
            "email": current_customer.email
        }
    }
