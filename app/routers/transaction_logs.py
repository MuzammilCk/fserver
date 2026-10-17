import math
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import cast, Date
from typing import List, Optional
from datetime import date

from app import database, schemas, models, auth
from app.database import get_db

router = APIRouter(prefix="/fserver/transaction-logs", tags=["Transaction Logs"])

@router.get("/", response_model=List[schemas.DetailedTransactionLog])
def get_transaction_logs(
    skip: int = 0,
    limit: int = 100,
    machine_id: Optional[str] = None,
    status: Optional[str] = None,
    session_id: Optional[str] = None,
    order_id: Optional[str] = None,
    target_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.role_required(["super_admin", "admin"]))
):
    # Join Session to get File Path/Copies details
    query = db.query(models.TransactionLog, models.Session).\
        outerjoin(models.Session, models.TransactionLog.session_id == models.Session.id)

    # 1. Franchise Security
    if current_user.role == "admin":
        allowed_machines = db.query(models.Machine.id).filter(
            models.Machine.franchise_id == current_user.franchise_id
        ).all()
        allowed_ids = [m.id for m in allowed_machines]
        query = query.filter(models.TransactionLog.machine_id.in_(allowed_ids))

    # 2. Filters
    if machine_id:
        query = query.filter(models.TransactionLog.machine_id == machine_id)
    if status:
        query = query.filter(models.TransactionLog.payment_status == status)
    if session_id:
        query = query.filter(models.TransactionLog.session_id == session_id)
    
    # ✅ FIX: Filter by the REAL order_id in the log, falling back to Session if needed
    if order_id:
        query = query.filter(
            (models.TransactionLog.order_id == order_id) | 
            (models.Session.phonepe_order_id == order_id)
        )
        
    if target_date:
        query = query.filter(cast(models.TransactionLog.created_at, Date) == target_date)

    results = query.order_by(models.TransactionLog.created_at.desc()).offset(skip).limit(limit).all()

    detailed_logs = []
    for trans, sess in results:
        # 1. Determine final IDs and attributes
        final_order_id = trans.order_id if trans.order_id else (sess.phonepe_order_id if sess else None)
        is_duplex = sess.duplex if sess else False
        total_printed_pages = sess.total_pages if sess else 0
        copies = sess.copies if sess else 1
        
        # 2. 🚀 CALCULATE PHYSICAL SHEETS (The logic used in your sessions.py)
        if is_duplex:
            # If duplex, we use ceil(pages_per_copy / 2) * copies
            pages_per_copy = (total_printed_pages // copies) if copies > 0 else 0
            sheets_per_copy = math.ceil(pages_per_copy / 2.0)
            total_sheets = sheets_per_copy * copies
        else:
            total_sheets = total_printed_pages

        detailed_logs.append({
            "id": trans.id,
            "transaction_id": trans.transaction_id,
            "session_id": trans.session_id,
            "machine_id": trans.machine_id,
            "amount": trans.amount,
            "payment_status": trans.payment_status,
            "ip_address": trans.ip_address,
            "provider_reference_id": trans.provider_reference_id,
            "created_at": trans.created_at,
            "order_id": final_order_id,
            "total_pages": total_printed_pages,  # Keep original page count
            "total_sheets": total_sheets,       # 🚀 NEW: Physical paper used
            "duplex": is_duplex,                # 🚀 NEW: Duplex status
            "copies": copies,
            "color": sess.color if sess else False,
            "file_path": sess.file_path if sess else None,
        })

    return detailed_logs
