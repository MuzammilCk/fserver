from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from pydantic import BaseModel
import logging

from app import schemas, crud, database, auth
from app.database import get_db
from app.models import Machine, MachineStatus, Notification, ConnectionLog, User
from app.auth import verify_machine_token
from app.services.email_service import send_email_alert 
# ✅ FIX: Import the missing schema
from app.schemas import MachineStatusUpdate

# Setup Logger
logger = logging.getLogger("kiosk-alerts")
logger.setLevel(logging.INFO)

router = APIRouter(prefix="/fserver/machines", tags=["machine-status"])

# --- SCHEMAS ---
class StatusUpdate(BaseModel):
    status: str
    is_online: bool = True 

class AlertUpdate(BaseModel):
    title: str = "Machine Alert"
    message: str
    type: str = "info"

# --- HELPER: Send Email ---
def send_email_now(db: Session, machine: Machine, title: str, message: str):
    print(f"⚡ [DEBUG] Email Triggered for {machine.id}...", flush=True)
    
    recipients = set()
    # 1. Super Admins
    admins = db.query(User).filter(User.role == "super_admin").all()
    for admin in admins:
        if admin.email: recipients.add(admin.email)

    # 2. Franchise Owners
    if machine.franchise_id:
        owners = db.query(User).filter(User.franchise_id == machine.franchise_id, User.role == "admin").all()
        for owner in owners:
            if owner.email: recipients.add(owner.email)

    if not recipients:
        print("❌ [DEBUG] No recipients found!", flush=True)
        return False

    email_body = (
        f"🚨 MACHINE ALERT 🚨\n\n"
        f"Machine: {machine.name}\n"
        f"Alert: {title}\n"
        f"Details: {message}\n"
    )
    
    print(f"📧 [DEBUG] Sending to: {list(recipients)}", flush=True)
    success_count = 0
    for email in recipients:
        try:
            send_email_alert(subject=f"⚠️ {title}", body=email_body, to_email=email)
            print(f"✅ [DEBUG] Email sent to {email}", flush=True)
            success_count += 1
        except Exception as e:
            print(f"❌ [DEBUG] Email Failed: {e}", flush=True)
            
    return success_count > 0

# ----------------------------------------------------
# 1) UPDATE STATUS
# ----------------------------------------------------
@router.post("/{machine_id}/status")
def update_machine_status(
    machine_id: str,
    update: StatusUpdate,
    db: Session = Depends(get_db),
    current_machine: Machine = Depends(verify_machine_token)
):
    status_entry = db.query(MachineStatus).filter_by(machine_id=machine_id).first()
    if not status_entry:
        status_entry = MachineStatus(machine_id=machine_id)
        db.add(status_entry)

    # Update Online Status
    now = datetime.utcnow()
    status_entry.last_update = now
    status_entry.is_online = True
    
    # Update Status Text
    if hasattr(status_entry, "status"): status_entry.status = update.status
    elif hasattr(status_entry, "printer_status"): status_entry.printer_status = update.status
        
    db.commit()
    return {"message": "Status updated"}

# ----------------------------------------------------
# 2) REPORT ALERT
# ----------------------------------------------------
@router.post("/{machine_id}/alert")
def report_machine_alert(
    machine_id: str,
    alert: AlertUpdate,
    db: Session = Depends(get_db),
    current_machine: Machine = Depends(verify_machine_token)
):
    # Log incoming alert
    print(f"📦 [DEBUG] ALERT RECEIVED: {alert.dict()}", flush=True)
    logger.warning(f"[KIOSK ALERT] machine={machine_id} title={alert.title} message={alert.message} type={alert.type}")

    # 1. Check for duplicates
    last_alert = db.query(Notification).filter(
        Notification.machine_id == machine_id,
        Notification.is_read == False
    ).order_by(Notification.created_at.desc()).first()

    if last_alert and alert.message in last_alert.message:
        if last_alert.created_at > datetime.utcnow() - timedelta(minutes=1440):
            print("⚠️ [DEBUG] Duplicate alert suppressed", flush=True)
            return {"message": "Duplicate alert suppressed"}

    # 2. Send Email NOW
    email_sent = send_email_now(db, current_machine, alert.title, alert.message)

    # 3. Save to DB (Safe Mode: Combine Title + Message)
    safe_message = f"[{alert.title}] {alert.message}"
    
    new_notif = Notification(
        machine_id=machine_id, 
        message=safe_message,
        is_read=False, 
        created_at=datetime.utcnow()
    )
    if hasattr(Notification, "type"):
        new_notif.type = alert.type

    db.add(new_notif)
    db.commit()

    if email_sent:
        return {"message": "Alert processed and Email SENT"}
    else:
        return {"message": "Alert processed but Email FAILED"}

# ----------------------------------------------------
# 3) STANDARD GETTERS
# ----------------------------------------------------
@router.get("/{machine_id}", response_model=schemas.MachineStatus)
def get_status(machine_id: str, db: Session = Depends(database.get_db), current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))):
    return crud.get_machine_status(db, machine_id)

@router.put("/{machine_id}", response_model=schemas.MachineStatusUpdate)
def admin_update_machine_status(machine_id: str, data: MachineStatusUpdate, db: Session = Depends(database.get_db), current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))):
    status = db.query(MachineStatus).filter_by(machine_id=machine_id).first()
    for key, value in data.dict(exclude_unset=True).items():
        if hasattr(status, key): setattr(status, key, value)
    db.commit()
    return status

@router.get("/{machine_id}/earnings", response_model=list[schemas.DailyEarnings])
def get_earnings(machine_id: str, db: Session = Depends(database.get_db), current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))):
    return crud.get_daily_earnings(db, machine_id)
