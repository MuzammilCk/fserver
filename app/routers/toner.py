from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import TonerConsumption, Machine
from app.schemas import TonerConsumptionCreate, TonerConsumptionResponse, User
from app.auth import role_required

router = APIRouter(prefix="/fserver/toner", tags=["Toner"])

# Set toner settings (only for super_admin or admin of that machine)
@router.post("/", response_model=TonerConsumptionResponse)
def set_toner_consumption(
    data: TonerConsumptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(role_required(["super_admin", "admin"]))
):
    machine = db.query(Machine).filter_by(id=data.machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    # Restrict admin access to their own franchise
    if current_user.role == "admin" and machine.franchise_id != current_user.franchise_id:
        raise HTTPException(status_code=403, detail="Permission denied")

    existing = db.query(TonerConsumption).filter_by(machine_id=data.machine_id).first()
    if existing:
        existing.bw_per_page = data.bw_per_page
        existing.color_black = data.color_black
        existing.color_cyan = data.color_cyan
        existing.color_magenta = data.color_magenta
        existing.color_yellow = data.color_yellow
    else:
        new = TonerConsumption(**data.dict())
        db.add(new)

    db.commit()
    return db.query(TonerConsumption).filter_by(machine_id=data.machine_id).first()


# Get toner settings (only for super_admin or admin of that machine)
@router.get("/{machine_id}", response_model=TonerConsumptionResponse)
def get_toner_consumption(
    machine_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(role_required(["super_admin", "admin"]))
):
    machine = db.query(Machine).filter_by(id=machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    # Restrict admin access to their own franchise
    if current_user.role == "admin" and machine.franchise_id != current_user.franchise_id:
        raise HTTPException(status_code=403, detail="Permission denied")

    record = db.query(TonerConsumption).filter_by(machine_id=machine_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Toner consumption not set")

    return record
