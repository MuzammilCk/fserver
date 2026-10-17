from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel

from app import models, schemas, crud, auth
from app.database import get_db
from app.utils.hashing import get_password_hash 

router = APIRouter(tags=["users"])

# --- Pydantic Schema for the Token Payload ---
class FCMTokenUpdate(BaseModel):
    fcm_token: str

# Create User (Super Admin only)
@router.post("/", response_model=schemas.User)
def create_user(
    user: schemas.UserCreate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.role_required(["super_admin"]))
):
    db_user = crud.get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Generate default password (or handle it via logic)
    hashed_password = get_password_hash("123456") 
    return crud.create_user(db=db, user=user, hashed_password=hashed_password)

# Read Users (Admin sees own franchise, Super Admin sees all)
@router.get("/", response_model=List[schemas.User])
def read_users(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if current_user.role == "super_admin":
        users = crud.get_users(db, skip=skip, limit=limit)
    else:
        # Admins only see users in their franchise
        users = db.query(models.User).filter(
            models.User.franchise_id == current_user.franchise_id
        ).offset(skip).limit(limit).all()
    return users

# Read Me (Current User Profile)
@router.get("/me", response_model=schemas.User)
def read_users_me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user

# Update User
@router.put("/{user_id}", response_model=schemas.User)
def update_user(
    user_id: int,
    user_update: schemas.UserUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.role_required(["super_admin"]))
):
    updated_user = crud.update_user(db, user_id, user_update)
    if not updated_user:
        raise HTTPException(status_code=404, detail="User not found")
    return updated_user

# Delete User
@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.role_required(["super_admin"]))
):
    deleted = crud.delete_user(db, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}

# Register FCM Token (For Mobile Push Notifications)
@router.post("/fcm-token")
def register_device_token(
    data: FCMTokenUpdate, # ✅ FIXED: Uses strict Pydantic model to catch {"fcm_token": "..."}
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user) # ✅ FIXED: Uses SQLAlchemy model
):
    if data.fcm_token:
        current_user.fcm_token = data.fcm_token
        db.commit()
    return {"message": "Device registered successfully"}
