from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import date, timedelta, datetime
from typing import Optional
import math

from app.database import get_db
from app import models, schemas, auth

router = APIRouter(tags=["earnings"])

# ✅ Helper: Convert UTC to IST (+5:30)
def to_ist(utc_dt):
    if not utc_dt:
        return None
    return utc_dt + timedelta(hours=5, minutes=30)

@router.get("/stats")
def get_dashboard_stats(
    machine_id: Optional[str] = None,
    range: Optional[str] = "week",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))
):
    # ---------------------------------------------------------
    # 1. DATE FILTER LOGIC
    # ---------------------------------------------------------
    today = date.today()
    filter_start = today - timedelta(days=7) 
    filter_end = today + timedelta(days=1)   

    if range == "today":
        filter_start = today
    elif range == "week":
        filter_start = today - timedelta(days=7)
    elif range == "month":
        filter_start = today - timedelta(days=30)
    elif range == "year":
        filter_start = today - timedelta(days=365)
    elif range == "custom" and start_date and end_date:
        try:
            filter_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            filter_end = datetime.strptime(end_date, "%Y-%m-%d").date() + timedelta(days=1)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    # ---------------------------------------------------------
    # 2. BASE QUERY (Apply Date & Success Filter)
    # ---------------------------------------------------------
    
    # ✅ Source of Truth: TransactionLog where Status is SUCCESS
    query_txns = db.query(models.TransactionLog).filter(
        models.TransactionLog.payment_status == 'SUCCESS',
        models.TransactionLog.created_at >= filter_start,
        models.TransactionLog.created_at < filter_end
    )

    # ---------------------------------------------------------
    # 3. SECURITY & MACHINE FILTERING
    # ---------------------------------------------------------
    if current_user.role == "admin":
        machines = db.query(models.Machine.id).filter(models.Machine.franchise_id == current_user.franchise_id).all()
        my_machine_ids = [m.id for m in machines]
        
        if machine_id:
            if machine_id not in my_machine_ids:
                raise HTTPException(status_code=403, detail="Access denied to this machine")
            filter_ids = [machine_id]
        else:
            filter_ids = my_machine_ids
        
        query_txns = query_txns.filter(models.TransactionLog.machine_id.in_(filter_ids))

    elif machine_id:
        query_txns = query_txns.filter(models.TransactionLog.machine_id == machine_id)

    # ---------------------------------------------------------
    # 4. AGGREGATIONS (FIXED WITHOUT CHANGING MODELS)
    # ---------------------------------------------------------
    
    # A. Total Earnings
    total_earnings = query_txns.with_entities(func.sum(models.TransactionLog.amount)).scalar() or 0.0
    
    # B. Total Prints (✅ FIX: Join Session to get total_pages)
    # Since TransactionLog doesn't have 'total_pages', we ask the Session table for it.
    total_prints = query_txns.join(models.Session).with_entities(
        func.sum(models.Session.total_pages)
    ).scalar() or 0

    # C. Active Machines (Snapshot)
    active_machines_query = db.query(models.MachineStatus).filter(models.MachineStatus.is_online == True)
    if current_user.role == "admin":
        machines = db.query(models.Machine.id).filter(models.Machine.franchise_id == current_user.franchise_id).all()
        my_ids = [m.id for m in machines]
        active_machines_query = active_machines_query.filter(models.MachineStatus.machine_id.in_(my_ids))
    
    active_machines = active_machines_query.count()

    # ---------------------------------------------------------
    # 5. DATA LISTS (With IST Conversion & Paper Math)
    # ---------------------------------------------------------
    
    # Join Session to extract duplex, color, and accurate sheet counts
    db_txns_with_session = query_txns\
        .add_columns(models.Session)\
        .outerjoin(models.Session, models.TransactionLog.session_id == models.Session.id)\
        .order_by(desc(models.TransactionLog.created_at))\
        .all()

    recent_transactions = []
    for txn, sess in db_txns_with_session:
        is_duplex = sess.duplex if sess else False
        total_printed_pages = sess.total_pages if sess else 0
        copies = sess.copies if sess else 1
        
        # Calculate physical sheets
        if is_duplex:
            pages_per_copy = (total_printed_pages // copies) if copies > 0 else 0
            total_sheets = math.ceil(pages_per_copy / 2.0) * copies
        else:
            total_sheets = total_printed_pages

        recent_transactions.append({
            "id": str(txn.id),
            "amount": txn.amount,
            "payment_status": txn.payment_status,
            "created_at": to_ist(txn.created_at).isoformat() if txn.created_at else None,
            "machine_id": txn.machine_id,
            "total_pages": total_printed_pages,
            "total_sheets": total_sheets,  # 🚀 FLUTTER DASHBOARD WILL READ THIS NOW
            "copies": copies,
            "color": sess.color if sess else False,
            "duplex": is_duplex            # 🚀 AND THIS
        })

    graph_data = (
        query_txns
        .with_entities(
            func.date(models.TransactionLog.created_at).label("tx_date"),
            func.sum(models.TransactionLog.amount).label("total")
        )
        .group_by(func.date(models.TransactionLog.created_at))
        .order_by(func.date(models.TransactionLog.created_at))
        .all()
    )

    earnings_graph = [
        {"date": str(row.tx_date), "amount": row.total}
        for row in graph_data
    ]

    return {
        "total_earnings": total_earnings,
        "active_machines": active_machines,
        "total_prints": total_prints,
        "recent_transactions": recent_transactions,
        "earnings_graph": earnings_graph
    }
