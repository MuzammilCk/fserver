from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app import models, schemas, auth
from app.database import get_db
from datetime import datetime

router = APIRouter(tags=["refill_logs"])

@router.post("/refill-logs", response_model=schemas.RefillLog)
def create_refill_log(
    log: schemas.RefillLogCreate,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.get_current_user)
):
    # 1. Verify Machine Ownership
    machine = db.query(models.Machine).filter(models.Machine.id == log.machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    # If not Super Admin, ensure they own the machine
    user_role = str(current_user.role).lower()
    if "super" not in user_role and machine.franchise_id != current_user.franchise_id:
        raise HTTPException(status_code=403, detail="Permission denied for this machine")

    # 2. Update Machine Status (Real-time levels)
    status = db.query(models.MachineStatus).filter_by(machine_id=log.machine_id).first()
    if status:
        if log.ink_black > 0: status.ink_black = 100
        if log.ink_cyan > 0: status.ink_cyan = 100
        if log.ink_magenta > 0: status.ink_magenta = 100
        if log.ink_yellow > 0: status.ink_yellow = 100
        
        # 🚀 UPDATED: Add paper with no artificial limits
        if log.paper_count > 0:
            status.paper_count = status.paper_count + log.paper_count

        status.last_update = datetime.utcnow()
        db.add(status)

    # 3. Create Log Entry
    db_log = models.RefillLog(
        machine_id=log.machine_id,
        ink_black=log.ink_black,
        ink_cyan=log.ink_cyan,
        ink_magenta=log.ink_magenta,
        ink_yellow=log.ink_yellow,
        paper_count=log.paper_count,
        timestamp=datetime.utcnow(),
        refilled_by=current_user.id
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log

@router.get("/refill-logs", response_model=List[schemas.RefillLog])
def read_refill_logs(
    machine_id: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.get_current_user)
):
    # ✅ Start query joined with Machine table to check ownership
    query = db.query(models.RefillLog).join(models.Machine)

    # ✅ SECURITY CHECK: Filter by Franchise if not Super Admin
    user_role = str(current_user.role).lower()
    if "super" not in user_role:
        query = query.filter(models.Machine.franchise_id == current_user.franchise_id)

    # Optional: Filter by specific machine if requested
    if machine_id:
        query = query.filter(models.RefillLog.machine_id == machine_id)

    return query.order_by(models.RefillLog.timestamp.desc()).offset(skip).limit(limit).all()
@router.delete("/refill-logs/{log_id}", status_code=204)
def delete_refill_log(
    log_id: int,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.get_current_user)
):
    # 1. Find the specific log entry
    log_entry = db.query(models.RefillLog).filter(models.RefillLog.id == log_id).first()
    if not log_entry:
        raise HTTPException(status_code=404, detail="Refill log not found")

    # 2. Security Check: Find the machine to verify ownership
    machine = db.query(models.Machine).filter(models.Machine.id == log_entry.machine_id).first()
    user_role = str(current_user.role).lower()
    
    if "super" not in user_role:
        if not machine or machine.franchise_id != current_user.franchise_id:
            raise HTTPException(status_code=403, detail="Permission denied to delete this log")

    # 3. Reverse the Math in Machine Status
    status = db.query(models.MachineStatus).filter_by(machine_id=log_entry.machine_id).first()
    if status:
        # Subtract the paper that was added, but don't let it go below 0
        if log_entry.paper_count > 0:
            status.paper_count = max(0, status.paper_count - log_entry.paper_count)
        
        # Note: We cannot easily "reverse" the ink because the creation API 
        # sets it straight to 100 rather than adding a specific amount.
        
        status.last_update = datetime.utcnow()
        db.add(status)

    # 4. Delete the log and commit
    db.delete(log_entry)
    db.commit()
    
    return {"message": "Log deleted and machine status reverted"}
