from enum import Enum
from typing import Optional, List, Literal
from datetime import datetime, date
from pydantic import BaseModel, EmailStr, ConfigDict
from app.enums import UserRole
from pydantic import BaseModel, constr, EmailStr

# ============================
# AUTH & USERS
# ============================

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class VerifyOTP(BaseModel):
    username: str
    otp: str

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None

class UserCreate(UserBase):
    role: UserRole = UserRole.ADMIN
    franchise_id: Optional[int] = None

class User(UserBase):
    id: int
    role: UserRole
    franchise_id: Optional[int] = None
    is_active: bool
    fcm_token: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None

# ============================
# LOGIN RESPONSE
# ============================

class LoginRequest(BaseModel):
    email: str

class OTPVerifyRequest(BaseModel):
    email: str
    otp: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    name: str
    email: str

# ============================
# FRANCHISES
# ============================
# ============================
# FRANCHISES
# ============================
# ============================
# FRANCHISES
# ============================

class FranchiseBase(BaseModel):
    name: str
    contact_email: EmailStr
    location: str
    razorpay_key_id: Optional[str] = None
    razorpay_webhook_secret: Optional[str] = None
    daily_report_enabled: Optional[bool] = True

class FranchiseCreate(FranchiseBase):
    razorpay_key_secret: str

class Franchise(FranchiseBase):
    id: int
    model_config = ConfigDict(from_attributes=True)
    
class FranchiseUpdate(BaseModel):
    name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    location: Optional[str] = None
    razorpay_key_id: Optional[str] = None
    razorpay_key_secret: Optional[str] = None # Now included for updates
    razorpay_webhook_secret: Optional[str] = None    # Notice `razorpay_key_secret` is deliberately missing here!# ============================
    daily_report_enabled: Optional[bool] = None
# MACHINES
# ============================

class MachineBase(BaseModel):
    id: str
    name: str
    location: str
    franchise_id: int
    price_bw: Optional[float] = 2.0
    price_color: Optional[float] = 5.0
    price_bw_duplex: float = 1.5
    price_color_duplex: float = 4.0
    is_active: bool = True
    supports_duplex: bool = False  # ✅ Add this line

class MachineCreate(MachineBase):
    pass

class Machine(MachineBase):
    last_seen: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class MachineUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    franchise_id: Optional[int] = None
    price_bw: Optional[float] = None
    price_color: Optional[float] = None
    price_bw_duplex: Optional[float] = None
    price_color_duplex: Optional[float] = None
    supports_duplex: Optional[bool] = None
    model_config = ConfigDict(from_attributes=True)

class MachineStatusUpdate(BaseModel):
    ink_black: Optional[float] = None
    ink_cyan: Optional[float] = None
    ink_magenta: Optional[float] = None
    ink_yellow: Optional[float] = None
    paper_count: Optional[int] = None
    is_online: Optional[bool] = None
    orientation: Optional[str] = None

class MachineStatusBase(MachineStatusUpdate):
    machine_id: str
    last_update: datetime
    model_config = ConfigDict(from_attributes=True)

class MachineStatus(MachineStatusBase):
    model_config = ConfigDict(from_attributes=True)

class MachineOut(BaseModel):
    id: str
    name: str
    location: str
    franchise_id: Optional[int]
    price_bw: float
    price_color: float
    api_key: str
    is_active: bool = True
    status: Optional[MachineStatusBase]
    model_config = ConfigDict(from_attributes=True)

class MachineWithStatus(BaseModel):
    id: str
    name: str
    location: Optional[str]
    franchise_id: Optional[int]
    price_bw: float
    is_active: bool
    price_color: float
    status: Optional[MachineStatus]
    model_config = ConfigDict(from_attributes=True)

# ============================
# SESSIONS (✅ FIXED ENUMS)
# ============================

class SessionStatus(str, Enum):
    waiting = "waiting"
    active = "active"              # <--- ADDED THIS
    uploaded = "uploaded"          # <--- ADDED THIS
    payment_pending = "payment_pending" # <--- ADDED THIS
    ready_to_print = "ready_to_print"
    printing = "printing"
    completed = "completed"
    failed = "failed"

class SessionRequest(BaseModel):
    machine_id: str
    session_type: Optional[str] = "print" # Default to normal print
class SessionBase(BaseModel):
    id: str
    machine_id: str
    status: SessionStatus
    payment_status: Optional[str] = "pending"
    session_type: str
    scanned_pages: List[str] = []
    
    model_config = ConfigDict(from_attributes=True)
class SessionCreate(SessionBase):
    pass

class SessionStatusUpdate(BaseModel):
    status: SessionStatus
class ScanCommand(BaseModel):
    action: str = "trigger_scan"
    session_id: str
class Session(SessionBase):
    file_path: Optional[str] = None
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    total_pages: Optional[int] = None
    color: Optional[bool] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    orientation: str = "portrait"
    duplex: bool = False
    phonepe_order_id: Optional[str] = None
    copies: int = 1
    model_config = ConfigDict(from_attributes=True)

class PrintJobData(BaseModel):
    file_url: str
    start_page: int
    end_page: int
    color: bool
    total_pages: int
    duplex: bool = False
    copies: int

# ============================
# REFILL
# ============================

class RefillLogCreate(BaseModel):
    machine_id: str
    ink_black: float = 0
    ink_cyan: float = 0
    ink_magenta: float = 0
    ink_yellow: float = 0
    paper_count: int = 0
    notes: Optional[str] = None

class RefillLog(RefillLogCreate):
    id: int
    timestamp: datetime
    refilled_by: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

# ============================
# TONER CONSUMPTION
# ============================

class TonerConsumptionBase(BaseModel):
    bw_per_page: float
    color_black: float
    color_cyan: float
    color_magenta: float
    color_yellow: float

class TonerConsumptionCreate(TonerConsumptionBase):
    machine_id: str

class TonerConsumptionResponse(TonerConsumptionCreate):
    model_config = ConfigDict(from_attributes=True)

# ============================
# EARNINGS
# ============================

class DailyEarnings(BaseModel):
    machine_id: str
    amount: float
    record_date: date
    model_config = ConfigDict(from_attributes=True)

# ============================
# PAYMENTS
# ============================

class PaymentRequest(BaseModel):
    amount: int
    session_id: str

# ============================
# NOTIFICATIONS
# ============================

class NotificationBase(BaseModel):
    message: str
    type: Optional[str] = "info"
    machine_id: Optional[str] = None

class NotificationCreate(NotificationBase):
    pass

class NotificationUpdate(BaseModel):
    is_read: Optional[bool] = None
    is_dismissed: Optional[bool] = None

class Notification(NotificationBase):
    id: int
    is_read: bool
    is_dismissed: bool
    created_at: datetime
    created_by: Optional[int]
    model_config = ConfigDict(from_attributes=True)

class AutoNotificationBase(BaseModel):
    message: str
    machine_id: Optional[str] = None

class AutoNotificationOut(AutoNotificationBase):
    id: int
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# ============================
# FILTERED EARNINGS
# ============================

class FilteredGraphPoint(BaseModel):
    record_date: date
    amount: float

class FilteredStats(BaseModel):
    bw_copies: int
    color_copies: int

class FilteredEarningsResponse(BaseModel):
    graph: list[FilteredGraphPoint]
    stats: FilteredStats

# ============================
# TRANSACTION LOGS
# ============================

class DetailedTransactionLog(BaseModel):
    # --- Original Fields ---
    id: int
    transaction_id: str
    session_id: str
    machine_id: str
    amount: float
    payment_status: str
    ip_address: Optional[str] = None
    provider_reference_id: Optional[str] = None
    created_at: datetime
    duplex: bool
    total_sheets: int
    # --- New Print Details (From Session) ---
    order_id: Optional[str] = None   # PhonePe Order ID
    total_pages: Optional[int] = 0
    copies: Optional[int] = 1
    color: Optional[bool] = False
    file_path: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)





class CustomerUpdatePayload(BaseModel):
    name: str | None = None
    phone: constr(min_length=10, max_length=15) | None = None
    email: EmailStr | None = None
    is_verified: bool | None = None
