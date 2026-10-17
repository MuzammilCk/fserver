from datetime import datetime
from sqlalchemy.orm import Session
from app import models, schemas
from typing import Optional
from app.utils.hashing import get_password_hash  # ✅ Correct import
from app.utils.fcm import send_push_notification 

# ========== USERS ==========

def get_users(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.User).offset(skip).limit(limit).all()

def get_user(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()

def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(models.User.email == email).first()

def create_user(db: Session, user: schemas.UserCreate, hashed_password: str):
    db_user = models.User(
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        franchise_id=user.franchise_id,
        hashed_password=hashed_password,
        is_active=True,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def delete_user(db: Session, user_id: int):
    user = get_user(db, user_id)
    if user:
        db.delete(user)
        db.commit()
        return user
    return None

def update_user(db: Session, user_id: int, updates: schemas.UserUpdate):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return None

    if updates.email:
        user.email = updates.email
    # Password updates usually handled separately, but if included in schema:
    # if updates.password:
    #     user.hashed_password = get_password_hash(updates.password)

    db.commit()
    db.refresh(user)
    return user


# ========== FRANCHISES ==========

def create_franchise(db: Session, franchise: schemas.FranchiseCreate):
    db_franchise = models.Franchise(**franchise.dict())
    db.add(db_franchise)
    db.commit()
    db.refresh(db_franchise)
    return db_franchise

def get_franchise(db: Session, franchise_id: int):
    return db.query(models.Franchise).filter(models.Franchise.id == franchise_id).first()

def get_all_franchises(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Franchise).offset(skip).limit(limit).all()

def delete_franchise(db: Session, franchise_id: int):
    franchise = get_franchise(db, franchise_id)
    if franchise:
        db.delete(franchise)
        db.commit()
        return franchise
    return None
def update_franchise(db: Session, franchise_id: int, franchise_update: schemas.FranchiseUpdate):
    db_franchise = db.query(models.Franchise).filter(models.Franchise.id == franchise_id).first()
    if not db_franchise:
        return None
    
    # Convert input data to a dictionary, excluding unset values
    update_data = franchise_update.dict(exclude_unset=True)
    
    for key, value in update_data.items():
        setattr(db_franchise, key, value)
    
    db.commit()
    db.refresh(db_franchise)
    return db_franchise
# ========== MACHINES ==========

def create_machine(db: Session, machine: schemas.MachineCreate):
    db_machine = models.Machine(**machine.dict())
    db.add(db_machine)
    db.commit()
    db.refresh(db_machine)
    return db_machine

def get_machine(db: Session, machine_id: str):
    return db.query(models.Machine).filter(models.Machine.id == machine_id).first()

def get_machines(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Machine).offset(skip).limit(limit).all()

def update_machine(db: Session, machine_id: str, machine_data: schemas.MachineUpdate):
    machine = get_machine(db, machine_id)
    if not machine:
        return None

    for field, value in machine_data.dict(exclude_unset=True).items():
        setattr(machine, field, value)

    db.commit()
    db.refresh(machine)
    return machine

# ========== MACHINE STATUS ==========

def get_machine_status(db: Session, machine_id: str):
    return db.query(models.MachineStatus).filter_by(machine_id=machine_id).first()


# ========== MACHINE STATUS ==========
def update_machine_status(db: Session, machine_id: str, status_data: schemas.MachineStatusBase):
    existing = get_machine_status(db, machine_id)
    
    # --- 🚨 ALERT DETECTION LOGIC ---
    needs_alert = False
    alert_title = ""
    alert_body = ""
    
    if existing:
        # 1. Detect if it just dropped offline
        if existing.is_online and getattr(status_data, 'is_online', True) is False:
            needs_alert = True
            alert_title = "⚠️ Kiosk Offline!"
            alert_body = "The machine has lost power or internet connection."
            
        # 2. ✅ FIXED: Safely detect if paper just hit zero
        elif (existing.paper_count or 0) > 0 and status_data.paper_count is not None and status_data.paper_count <= 0:
            needs_alert = True
            alert_title = "📄 Out of Paper"
            alert_body = "The machine requires an immediate paper refill."
            
    # --- UPDATE DATABASE ---
    if existing:
        # Ignore None values so we don't overwrite existing paper/ink levels with Nulls during an LWT
        for field, value in status_data.dict(exclude_unset=True).items():
            if value is not None: 
                setattr(existing, field, value)
        existing.last_update = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        updated_status = existing
    else:
        new_status = models.MachineStatus(
            machine_id=machine_id,
            **status_data.dict(exclude_none=True), # Exclude None so DB defaults apply
            last_update=datetime.utcnow()
        )
        db.add(new_status)
        db.commit()
        db.refresh(new_status)
        updated_status = new_status
        
    # --- 📲 FIRE ANDROID PUSH NOTIFICATION ---
    if needs_alert:
        machine = get_machine(db, machine_id)
        if machine and machine.franchise_id:
            # Find the Franchise Admin/Owner
            owner = db.query(models.User).filter_by(
                franchise_id=machine.franchise_id, 
                role="admin" # Adjust if your enum uses "franchise_admin"
            ).first()
            
            if owner and owner.fcm_token:
                from app.utils.fcm import send_push_notification
                send_push_notification(
                    fcm_token=owner.fcm_token,
                    title=f"{alert_title} - {machine.name}",
                    body=alert_body
                )

    return updated_status
# ========== REFILL LOG (✅ FIXED) ==========

# Renamed type hint from RefillLogBase to RefillLogCreate
def create_refill_log(db: Session, refill: schemas.RefillLogCreate):
    db_log = models.RefillLog(
        machine_id=refill.machine_id,
        ink_black=refill.ink_black,
        ink_cyan=refill.ink_cyan,
        ink_magenta=refill.ink_magenta,
        ink_yellow=refill.ink_yellow,
        paper_count=refill.paper_count,
        timestamp=datetime.utcnow()
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log

# ========== EARNINGS ==========

def get_daily_earnings(db: Session, machine_id: str):
    return db.query(models.DailyEarnings).filter_by(machine_id=machine_id).all()

# ========== NOTIFICATIONS ==========

def create_notification(db: Session, notif: schemas.NotificationCreate, user_id: Optional[int] = None):
    db_notif = models.Notification(
        message=notif.message,
        type=notif.type,
        machine_id=notif.machine_id,
        created_by=user_id
    )
    db.add(db_notif)
    db.commit()
    db.refresh(db_notif)
    return db_notif

def get_notifications(db: Session, machine_id: Optional[str] = None):
    query = db.query(models.Notification)
    if machine_id:
        query = query.filter(models.Notification.machine_id == machine_id)
    return query.order_by(models.Notification.created_at.desc()).all()

def update_notification(db: Session, notif_id: int, update: schemas.NotificationUpdate):
    notif = db.query(models.Notification).filter(models.Notification.id == notif_id).first()
    if not notif:
        return None
    for field, value in update.dict(exclude_unset=True).items():
        setattr(notif, field, value)
    db.commit()
    db.refresh(notif)
    return notif

def delete_notification(db: Session, notif_id: int):
    notif = db.query(models.Notification).filter(models.Notification.id == notif_id).first()
    if notif:
        db.delete(notif)
        db.commit()
        return True
    return False

# ========== ACTIVITY LOGS ==========

def create_activity_log(
    db: Session,
    action: str,
    description: str,
    user_id: Optional[int] = None,
    machine_id: Optional[str] = None
):
    db_log = models.ActivityLog(
        action=action,
        description=description,
        user_id=user_id,
        machine_id=machine_id,
        timestamp=datetime.utcnow()
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


def add_scanned_page_to_session(db: Session, session_id: str, filename: str):
    db_session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if db_session:
        # Create a copy of the list to trigger SQLAlchemy change detection
        current_pages = list(db_session.scanned_pages) if db_session.scanned_pages else []
        current_pages.append(filename)
        db_session.scanned_pages = current_pages
        
        db.commit()
        db.refresh(db_session)
    return db_session

def remove_scanned_page_from_session(db: Session, session_id: str, filename: str):
    db_session = db.query(models.Session).filter(models.Session.id == session_id).first()
    if db_session and db_session.scanned_pages:
        current_pages = [p for p in db_session.scanned_pages if p != filename]
        db_session.scanned_pages = current_pages
        db.commit()
        db.refresh(db_session)
    return db_session
