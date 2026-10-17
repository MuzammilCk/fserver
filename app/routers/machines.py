from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app import schemas, models, database, auth, crud
from app.config import settings
from app.services.websocket_service import manager
from secrets import token_urlsafe
import paho.mqtt.publish as publish
import json
from app.database import get_db

router = APIRouter(tags=["machines"])

# ==========================================
# ✅ REFRESH ADS (Admin Command)
# ==========================================
@router.post("/{machine_id}/refresh-ads")
async def trigger_ads_refresh(
    machine_id: str,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))
):
    if current_user.role == "admin":
        machine = db.query(models.Machine).filter(models.Machine.id == machine_id).first()
        if not machine:
            raise HTTPException(status_code=404, detail="Machine not found")
        if machine.franchise_id != current_user.franchise_id:
            raise HTTPException(status_code=403, detail="Permission denied")

    if machine_id not in manager.active_machines:
        raise HTTPException(status_code=404, detail="Machine is OFFLINE.")

    await manager.send_command(machine_id, "refresh_drive")
    return {"message": "Refresh command sent"}


# ==========================================
# ✅ TOGGLE STATUS (Enable/Disable)
# ==========================================
@router.patch("/{machine_id}/toggle-status")
def toggle_machine_status(
    machine_id: str,
    enable: bool,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))
):
    machine = db.query(models.Machine).filter(models.Machine.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    if current_user.role == "admin" and machine.franchise_id != current_user.franchise_id:
        raise HTTPException(status_code=403, detail="Access denied")

    machine.is_active = enable
    db.commit()
    status_msg = "enabled" if enable else "disabled"
    return {"message": f"Machine '{machine.name}' has been {status_msg}."}


# ==========================================
# ✅ UPDATE MACHINE (Prices, Name, Location, Duplex)
# ==========================================
@router.patch("/{machine_id}", response_model=schemas.MachineOut)
def update_machine(
    machine_id: str,
    machine_update: schemas.MachineUpdate,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))
):
    """
    Updates machine details like Price, Name, Location, and Duplex support.
    """
    machine = db.query(models.Machine).filter(models.Machine.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    # Security Check: Admins can only update their own machines
    if current_user.role == "admin" and machine.franchise_id != current_user.franchise_id:
        raise HTTPException(status_code=403, detail="Permission denied")

    # Apply updates dynamically
    update_data = machine_update.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(machine, key, value)

    db.commit()
    db.refresh(machine)
    return machine


# ==========================================
# ✅ STANDARD CRUD (Create, Read, Delete)
# ==========================================
@router.post("", response_model=schemas.MachineOut)
def create_machine(
    machine: schemas.MachineCreate,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    return crud.create_machine(db=db, machine=machine)


@router.get("", response_model=List[schemas.MachineWithStatus])
def read_machines(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.get_current_user)
):
    if current_user.role == "super_admin":
        machines = db.query(models.Machine).offset(skip).limit(limit).all()
    else:
        machines = db.query(models.Machine).filter(models.Machine.franchise_id == current_user.franchise_id).offset(skip).limit(limit).all()

    # Attach status
    result = []
    for m in machines:
        status = crud.get_machine_status(db, m.id)
        result.append(schemas.MachineWithStatus(
            id=m.id,
            name=m.name,
            location=m.location,
            franchise_id=m.franchise_id,
            price_bw=m.price_bw,
            price_color=m.price_color,
            price_bw_duplex=getattr(m, "price_bw_duplex", 1.5),       # 🚀 NEW: Duplex B&W Pricing
            price_color_duplex=getattr(m, "price_color_duplex", 4.0), # 🚀 NEW: Duplex Color Pricing
            status=status,
            is_active=getattr(m, "is_active", True),
            supports_duplex=getattr(m, "supports_duplex", False)
        ))
    return result


@router.get("/{machine_id}", response_model=schemas.Machine)
def read_machine(
    machine_id: str,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.get_current_user)
):
    machine = crud.get_machine(db, machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    if current_user.role != "super_admin" and machine.franchise_id != current_user.franchise_id:
        raise HTTPException(status_code=403, detail="Permission denied")
    return machine


@router.delete("/{machine_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_machine(
    machine_id: str,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    machine = crud.get_machine(db, machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    db.delete(machine)
    db.commit()


# ==========================================
# ✅ API KEY MANAGEMENT
# ==========================================
@router.get("/{machine_id}/key")
def get_key(
    machine_id: str,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    machine = crud.get_machine(db, machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    return {"machine_id": machine.id, "api_key": machine.api_key}


@router.post("/{machine_id}/regenerate-key")
def regen_key(
    machine_id: str,
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    machine = crud.get_machine(db, machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    machine.api_key = token_urlsafe(32)
    db.commit()
    return {"machine_id": machine.id, "new_api_key": machine.api_key}


# ==========================================
# ✅ POWER CONTROL (Restart / Shutdown)
# ==========================================
@router.post("/{machine_id}/power-control")
def power_control(
    machine_id: str,
    action: str,  # Must be "reboot" or "shutdown"
    db: Session = Depends(database.get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin", "admin"]))
):
    machine = db.query(models.Machine).filter(models.Machine.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    if current_user.role == "admin" and machine.franchise_id != current_user.franchise_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if action not in ["reboot", "shutdown"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    # Broadcast to the Kiosk
    topic = f"kiosks/{machine_id}/commands"
    payload = json.dumps({"action": action})

    try:
        publish.single(
            topic,
            payload=payload,
            hostname=settings.MQTT_BROKER,
            port=settings.MQTT_PORT,
            qos=1,
            retain=False,
            auth={
                "username": settings.MQTT_USERNAME,
                "password": settings.MQTT_PASSWORD
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"MQTT Error: {str(e)}"
        )

    return {"message": f"Command '{action}' sent to {machine.name}."}


@router.get("/public/active")
def get_public_machines(db: Session = Depends(get_db)):
    """Used by the Flutter app to list locations and prices."""
    # 🚀 FIX: Changed Machine to models.Machine so Python can find it!
    machines = db.query(models.Machine).filter(models.Machine.is_active == True).all()

    return [{
        "id": m.id,
        "name": m.name,
        "location": m.location,
        "price_bw": m.price_bw,
        "price_color": m.price_color,
        "price_bw_duplex": getattr(m, 'price_bw_duplex', 1.5),
        "price_color_duplex": getattr(m, 'price_color_duplex', 4.0)
    } for m in machines]
