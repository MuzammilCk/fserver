from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app import crud, models, schemas, auth
from app.auth import role_required
from app.database import get_db
from app.schemas import User
from typing import List

router = APIRouter()

@router.get("/notifications", response_model=list[schemas.Notification])
def list_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(role_required(["admin", "super_admin"]))
):
    query = db.query(models.Notification)

    if current_user.role == "admin":
        # Get all machine IDs in the admin's franchise
        machine_ids = (
            db.query(models.Machine.id)
            .filter(models.Machine.franchise_id == current_user.franchise_id)
            .all()
        )
        machine_ids = [m.id for m in machine_ids]

        if not machine_ids:
            return []

        query = query.filter(models.Notification.machine_id.in_(machine_ids))

    return query.order_by(models.Notification.created_at.desc()).all()

@router.post("/notifications", response_model=schemas.Notification)
def create_notification(
    notif: schemas.NotificationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(role_required(["super_admin"]))  # Only super admin can post
):
    return crud.create_notification(db, notif, user_id=current_user.id)

@router.patch("/notifications/{notif_id}", response_model=schemas.Notification)
def mark_notification(
    notif_id: int,
    update: schemas.NotificationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(role_required(["admin", "super_admin"]))
):
    notif = crud.update_notification(db, notif_id, update)
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notif

@router.delete("/notifications/{notif_id}")
def delete_notification(
    notif_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(role_required(["super_admin"]))  # Only super admin can delete
):
    success = crud.delete_notification(db, notif_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"detail": "Notification deleted"}

@router.get("/auto-notifications", response_model=List[schemas.AutoNotificationOut])
def get_auto_notifications(
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["admin", "super_admin"]))
):
    query = db.query(models.AutoNotification)

    if current_user.role == "admin":
        machine_ids = (
            db.query(models.Machine.id)
            .filter(models.Machine.franchise_id == current_user.franchise_id)
            .all()
        )
        machine_ids = [m.id for m in machine_ids]

        if not machine_ids:
            return []

        query = query.filter(models.AutoNotification.machine_id.in_(machine_ids))

    return query.order_by(models.AutoNotification.created_at.desc()).all()

@router.patch("/auto-notifications/{notif_id}/read")
def mark_auto_notification_read(notif_id: int, db: Session = Depends(get_db)):
    notif = db.query(models.AutoNotification).filter(models.AutoNotification.id == notif_id).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_read = True
    db.commit()
    return {"message": "Marked as read"}

@router.delete("/auto-notifications/{notif_id}")
def delete_auto_notification(notif_id: int, db: Session = Depends(get_db)):
    notif = db.query(models.AutoNotification).filter(models.AutoNotification.id == notif_id).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.delete(notif)
    db.commit()
    return {"message": "Deleted"}
