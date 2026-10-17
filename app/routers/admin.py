from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, auth  # 🚀 IMPORT MODELS AND AUTH MODULES
from app.auth import get_current_user

router = APIRouter(prefix="/fserver/franchises", tags=["Franchises"])

# app/routers/admin.py                                  
@router.patch("/{franchise_id}/toggle-report") # 🚀 Removed extra /franchises/ prefix
def toggle_daily_report(                                    
    franchise_id: int, 
    enable: bool, 
    db: Session = Depends(get_db),
    # 🚀 USE auth.role_required (Now that auth is imported)
    current_user = Depends(auth.role_required(["super_admin", "admin"]))                                        
):                                                          
    # 🚀 USE models.Franchise
    franchise = db.query(models.Franchise).filter(models.Franchise.id == franchise_id).first()                  
    
    if not franchise:
        raise HTTPException(status_code=404, detail="Franchise not found")
                                                            
    franchise.daily_report_enabled = enable
    db.commit()
                                                            
    return {                                                    
        "message": f"Daily report {'enabled' if enable else 'disabled'} for {franchise.name}.",
        "daily_report_enabled": franchise.daily_report_enabled                                                      
    }

@router.get("/app-customers")
def list_app_customers(
    db: Session = Depends(get_db),
    current_admin = Depends(get_current_user) # Only admins can see this
):
    customers = db.query(Customer).order_by(Customer.created_at.desc()).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "email": c.email,
            "joined": c.created_at
        } for c in customers
    ]
