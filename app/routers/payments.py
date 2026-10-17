import uuid
from fastapi import APIRouter, HTTPException, Depends, Request
from app.services.payment_gateway import create_order
from app.models import Session as SessionModel, Machine, TransactionLog
from app.database import get_db
from sqlalchemy.orm import Session as DBSession
from app.schemas import PaymentRequest
import razorpay

router = APIRouter(tags=["payments"])

@router.post("/create")

def create_order(amount: float, session_id: str, key_id: str, key_secret: str):
    # Initialize the client with the specific franchise keys passed from the router
    client = razorpay.Client(auth=(key_id, key_secret))
    
    data = {
        "amount": int(amount * 100),  # Convert to paise
        "currency": "INR",
        "receipt": f"rcpt_{session_id}",
        "payment_capture": 1
    }
    
    order = client.order.create(data=data)
    return {
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"]
    }
