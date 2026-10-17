from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import ConnectionLog
from app import auth, schemas

router = APIRouter(prefix="/fserver/history", tags=["History"])

@router.get("/machine/{machine_id}")
def get_machine_history(
    machine_id: str, 
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))
):
    """
    Returns paired sessions. 
    If a session is active, 'disconnected_at' will be 'Running...'.
    """
    # Fetch logs (Newest first)
    logs = db.query(ConnectionLog).filter(
        ConnectionLog.machine_id == machine_id
    ).order_by(ConnectionLog.timestamp.desc()).limit(200).all()
    
    history = []
    
    for log in logs:
        if log.event_type == "OFFLINE":
            # Start a completed session block (we found the END)
            current_session = {"disconnected_at": log.timestamp, "connected_at": None}
            history.append(current_session)
        
        elif log.event_type == "ONLINE":
            # Found the START
            if history and history[-1]["connected_at"] is None:
                # We have an open 'End' waiting for a 'Start' -> Pair them
                history[-1]["connected_at"] = log.timestamp
            else:
                # We found a START but no recent END -> Machine is currently ON
                history.append({
                    "disconnected_at": "Running...", 
                    "connected_at": log.timestamp
                })
    
    return history

@router.get("/admin/logins")
def get_admin_history(
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    logs = db.query(ConnectionLog).filter(
        ConnectionLog.event_type == "ADMIN_LOGIN"
    ).order_by(ConnectionLog.timestamp.desc()).limit(50).all()
    
    return [{
        "user_id": log.user_id,
        "login_time": log.timestamp,
        "status": "Success"
    } for log in logs]
