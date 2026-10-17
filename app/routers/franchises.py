from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app import schemas, models, crud, auth
from app.database import get_db

# If you map this in your main.py using a prefix (e.g., prefix="/server/franchises"), 
# you don't need to add it here.
router = APIRouter(tags=["franchises"])

@router.post("/", response_model=schemas.Franchise, status_code=status.HTTP_201_CREATED)
async def create_franchise(
    franchise: schemas.FranchiseCreate,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    """
    Creates a new franchise. 
    Requires the Razorpay Key ID, Secret, and Webhook Secret in the payload.
    """
    return crud.create_franchise(db, franchise=franchise)

@router.get("/", response_model=List[schemas.Franchise])
async def read_franchises(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    """
    Lists all franchises. 
    The response_model (schemas.Franchise) automatically strips out 
    the razorpay_key_secret so it doesn't leak to the frontend.
    """
    return db.query(models.Franchise).offset(skip).limit(limit).all()

@router.get("/{franchise_id}", response_model=schemas.Franchise)
async def read_franchise(
    franchise_id: int,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    """
    Fetches a specific franchise by its ID.
    """
    franchise = crud.get_franchise(db, franchise_id)
    if not franchise:
        raise HTTPException(status_code=404, detail="Franchise not found")
    return franchise

@router.delete("/{franchise_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_franchise(
    franchise_id: int,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    """
    Deletes a franchise permanently.
    """
    franchise_obj = crud.get_franchise(db, franchise_id)
    if not franchise_obj:
        raise HTTPException(status_code=404, detail="Franchise not found")
    
    db.delete(franchise_obj)
    db.commit()
    
    return {"message": "Franchise deleted successfully"}
    
@router.put("/{franchise_id}", response_model=schemas.Franchise)
async def update_franchise(
    franchise_id: int,
    franchise_update: schemas.FranchiseUpdate,
    db: Session = Depends(get_db),
    current_user: schemas.User = Depends(auth.role_required(["super_admin"]))
):
    """
    Updates an existing franchise. 
    Only fields provided in the request body will be updated.
    """
    updated_franchise = crud.update_franchise(db, franchise_id=franchise_id, franchise_update=franchise_update)
    
    if not updated_franchise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Franchise not found"
        )
    
    return updated_franchise    
