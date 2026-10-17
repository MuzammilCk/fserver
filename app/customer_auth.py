# Open app/customer_auth.py on your server

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
import jwt
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Customer
from app.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/fserver/app-auth/login/send-otp")

def get_current_customer(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        # 🚀 THE FIX: Pad the secret key here so it matches the encoder perfectly!
        safe_secret = settings.SECRET_KEY.ljust(32, '0')
        
        # Now decode using the safe_secret
        payload = jwt.decode(token, safe_secret, algorithms=["HS256"])
        customer_id: str = payload.get("sub")
        
        if customer_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
            
    except jwt.PyJWTError: # This is what was causing your 401!
        raise HTTPException(status_code=401, detail="Could not validate credentials")
        
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer is None:
        raise HTTPException(status_code=401, detail="User not found")
        
    return customer
