from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date,
    ForeignKey, Float, Enum, JSON  # <--- Added JSON here

)
from sqlalchemy.orm import relationship
from app.database import Base
from app.enums import UserRole
from secrets import token_urlsafe
from uuid import uuid4

# ----------------------------- User & Franchise -----------------------------
# app/models.py (Update User class)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    role = Column(Enum(UserRole), default=UserRole.ADMIN)
    franchise_id = Column(Integer, ForeignKey("franchises.id"), nullable=True)
    is_active = Column(Boolean, default=True)

    # --- NEW FIELDS FOR OTP & SECURITY ---
    otp_code = Column(String, nullable=True)
    otp_expires_at = Column(DateTime, nullable=True)
    fcm_token = Column(String, nullable=True)
    # Brute Force Protection
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)

    franchise = relationship("Franchise", back_populates="users")

class Franchise(Base):
    __tablename__ = "franchises"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    contact_email = Column(String)
    location = Column(String)
    daily_report_enabled = Column(Boolean, default=True)
    # --- 💸 NEW MULTI-TENANT RAZORPAY FIELDS ---
    razorpay_key_id = Column(String, nullable=True)
    razorpay_key_secret = Column(String, nullable=True)
    razorpay_webhook_secret = Column(String, nullable=True)

    users = relationship("User", back_populates="franchise")
    machines = relationship("Machine", back_populates="franchise")
# ----------------------------- Machine Models -----------------------------

class Machine(Base):
    __tablename__ = "machines"

    id = Column(String, primary_key=True, index=True)
    name = Column(String)
    location = Column(String)
    franchise_id = Column(Integer, ForeignKey("franchises.id"))
    price_bw = Column(Float, default=2.0)
    price_color = Column(Float, default=5.0)
    price_bw_duplex = Column(Float, default=1.5)    # e.g., ₹1.5 per side for duplex B&W
    price_color_duplex = Column(Float, default=4.0) # e.g., ₹4.0 per side for duplex Color
    api_key = Column(String, unique=True, default=lambda: token_urlsafe(32))
    supports_duplex = Column(Boolean, default=False)
    franchise = relationship("Franchise", back_populates="machines")
    is_active = Column(Boolean, default=True)
    toner_consumption = relationship("TonerConsumption", back_populates="machine", uselist=False)
    status = relationship(
    "MachineStatus", 
    back_populates="machine", 
    uselist=False, 
    cascade="all, delete-orphan" # 🚀 This is the magic fix
)

    print_jobs = relationship("PrintJob", back_populates="machine")

class MachineStatus(Base):
    __tablename__ = "machine_statuses"

    machine_id = Column(String, ForeignKey("machines.id"), primary_key=True)
    ink_black = Column(Float, default=100.0)
    ink_cyan = Column(Float, default=100.0)
    ink_magenta = Column(Float, default=100.0)
    ink_yellow = Column(Float, default=100.0)
    paper_count = Column(Integer, default=100)
    is_online = Column(Boolean, default=True)
    last_update = Column(DateTime, default=datetime.utcnow)
    orientation = Column(String, default="portrait")

    machine = relationship("Machine", back_populates="status")


class TonerConsumption(Base):
    __tablename__ = "toner_consumption"
    id = Column(Integer, primary_key=True, index=True)
    machine_id = Column(String, ForeignKey("machines.id"), unique=True)
    bw_per_page = Column(Float, default=0.0)
    color_black = Column(Float, default=0.0)
    color_cyan = Column(Float, default=0.0)
    color_magenta = Column(Float, default=0.0)
    color_yellow = Column(Float, default=0.0)

    machine = relationship("Machine", back_populates="toner_consumption")


class RefillLog(Base):
    __tablename__ = "refill_logs"

    id = Column(Integer, primary_key=True, index=True)
    machine_id = Column(String, ForeignKey("machines.id"))
    ink_black = Column(Float)
    ink_cyan = Column(Float)
    ink_magenta = Column(Float)
    ink_yellow = Column(Float)
    paper_count = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)

    # ✅ Add this line to fix the error
    refilled_by = Column(Integer, ForeignKey("users.id"))

    # Optional: to access user details from the log
    user = relationship("User", backref="refill_logs")
# ----------------------------- Sessions & Earnings -----------------------------

class Session(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, index=True)
    machine_id = Column(String, nullable=False)
    status = Column(String, default="waiting")  # Enum alternative: waiting, ready_to_print, printing, failed
    file_path = Column(String, nullable=True)
    start_page = Column(Integer, nullable=True)
    end_page = Column(Integer, nullable=True)
    copies = Column(Integer, default=1)
    total_amount = Column(Float, default=0.0) # Stores the exact calculated cart total
    total_pages = Column(Integer, nullable=True)
    color = Column(Boolean, nullable=True)
    duplex = Column(Boolean, default=False)
    orientation = Column(String, default="portrait")
    session_type = Column(String, default="print") # "print" or "scan"
    scanned_pages = Column(JSON, default=[])       # List of uploaded filenames: ["p1.jpg", "p2.jpg"]
    phonepe_order_id = Column(String, nullable=True)  # <-- store actual PhonePe order id
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    payment_status = Column(String, default="pending")  # pending, success, failed
    print_jobs = relationship("PrintJob", back_populates="session")


class DailyEarnings(Base):
    __tablename__ = "daily_earnings"

    id = Column(Integer, primary_key=True, index=True)
    machine_id = Column(String, ForeignKey("machines.id"))
    amount = Column(Float)
    record_date = Column(Date, default=date.today)

    machine = relationship("Machine")



# ----------------------------- notification-----------------------------

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    message = Column(String, nullable=False)
    type = Column(String, default="info")  # info, warning, error, maintenance
    is_read = Column(Boolean, default=False)
    is_dismissed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    machine_id = Column(String, ForeignKey("machines.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    machine = relationship("Machine")
    user = relationship("User")

class AutoNotification(Base):
    __tablename__ = "auto_notifications"

    id = Column(Integer, primary_key=True)

    message = Column(String, nullable=False)
    machine_id = Column(String, ForeignKey("machines.id"), nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    machine = relationship("Machine", backref="auto_notifications")

# models.py
class PrintJob(Base):
    __tablename__ = "print_jobs"

    id = Column(Integer, primary_key=True, index=True)
    machine_id = Column(String, ForeignKey("machines.id"))
    franchise_id = Column(String, ForeignKey("franchises.id"), nullable=True)
    session_id = Column(String, ForeignKey("sessions.id"))  # ✅ Add this line
    timestamp = Column(DateTime, default=datetime.utcnow)
    amount = Column(Float)
    bw_copies = Column(Integer, default=0)
    color_copies = Column(Integer, default=0)
    duplex = Column(Boolean, default=False)
    machine = relationship("Machine", back_populates="print_jobs")
    session = relationship("Session", back_populates="print_jobs")  # ✅ Optional (for easy joins)




class TransactionLog(Base):
    __tablename__ = "transaction_logs"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, unique=True, index=True)
    session_id = Column(String, ForeignKey("sessions.id"))
    machine_id = Column(String, ForeignKey("machines.id"))
    amount = Column(Float)
    payment_status = Column(String)
    ip_address = Column(String)
    provider_reference_id = Column(String, nullable=True)
    
    # ✅ ADD THIS LINE HERE:
    order_id = Column(String, nullable=True) 
    
    created_at = Column(DateTime, default=datetime.utcnow)
    # Relationships
    session = relationship("Session")
    machine = relationship("Machine")





class ConnectionLog(Base):
    __tablename__ = "connection_logs"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    machine_id = Column(String, ForeignKey("machines.id"), nullable=True) # Null if it's an admin event
    user_id = Column(String, ForeignKey("users.id"), nullable=True)       # Null if it's a machine event
    event_type = Column(String)  # "ONLINE", "OFFLINE", "ADMIN_LOGIN"
    timestamp = Column(DateTime, default=datetime.utcnow)

class MachineTemplate(Base):
    __tablename__ = "machine_templates"
    id = Column(String, primary_key=True, index=True)
    machine_id = Column(String, index=True)
    name = Column(String)        # e.g., "Physics Lab Cover"
    file_name = Column(String)   # e.g., "physics_cover.pdf" (Stored in /static/templates/)
    pages = Column(Integer)
class CloudPrintJob(Base):
    __tablename__ = "cloud_print_jobs"

    id = Column(String, primary_key=True, index=True) # E.g., a short 6-digit code or UUID
    user_phone = Column(String, index=True, nullable=True) # To link to the app user
    customer_id = Column(String, ForeignKey("customers.id"), nullable=True)
    file_path = Column(String)
    file_name = Column(String)
    
    # Print Settings
    color = Column(Boolean, default=False)
    duplex = Column(Boolean, default=False)
    copies = Column(Integer, default=1)
    total_pages = Column(Integer)
    total_amount = Column(Float)
    target_machine_id = Column(String, ForeignKey("machines.id"), nullable=True) # 🚀 ADD THIS
    # Payment Tracking
    payment_status = Column(String, default="pending") # pending, success, used
    razorpay_order_id = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)


# =======================================================================
# 📱 APP CUSTOMER MODELS
# =======================================================================
class Customer(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True, index=True) # UUID
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, unique=True, index=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class OtpRecord(Base):
    __tablename__ = "otp_records"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True)
    otp_code = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)
