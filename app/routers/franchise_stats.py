# app/routers/franchise_stats.py

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app import database, models
from app.models import User
from app.database import get_db

# 1. CHANGE THIS IMPORT
# Old: from app.dependencies import get_current_active_superadmin
# New: Import role_required from auth
from app.auth import role_required 

router = APIRouter(prefix="/fserver/franchise", tags=["Franchise Stats"])

@router.get("/stats", tags=["Franchise Stats"])
def get_stats(
    db: Session = Depends(get_db),
    # 2. CHANGE THIS DEPENDENCY
    # Old: current_user: User = Depends(get_current_active_superadmin)
    # New: Use role_required(["super_admin"])
    current_user: User = Depends(role_required(["super_admin"]))
):
    total_franchises = db.query(models.Franchise).count()
    total_admins = db.query(models.User).filter(models.User.role == "admin").count()
    
    total_earnings = db.query(models.DailyEarnings).with_entities(
        func.sum(models.DailyEarnings.amount)
    ).scalar() or 0

    return {
        "total_franchises": total_franchises,
        "admin_users": total_admins,
        "total_earnings": total_earnings
    }	
