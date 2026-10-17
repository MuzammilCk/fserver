import razorpay
from fastapi import HTTPException

# ✅ FIXED: Changed first argument name from 'amount_in_rupees' to 'amount' 
# to match your router's call: create_order(amount=total, ...)
def create_order(amount: float, session_id: str, key_id: str, key_secret: str):
    try:
        # ✅ DYNAMIC CLIENT
        client = razorpay.Client(auth=(key_id, key_secret))
        
        # Razorpay expects amount in Paise
        amount_paise = int(amount * 100)
        
        data = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": str(session_id),
            "notes": {
                "session_id": str(session_id)
            }
        }
        
        razorpay_order = client.order.create(data=data)

        return {
            "order_id": razorpay_order["id"],
            "amount": amount_paise,
            "currency": "INR",
            "razorpay_key": key_id 
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Razorpay SDK Error: {str(e)}"
        )

def check_payment_status(session, key_id: str, key_secret: str):
    # Retrieve the Razorpay Order ID from your DB session
    order_id = getattr(session, 'phonepe_order_id', session) if not isinstance(session, str) else session

    if not order_id:
        return "PENDING"

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        order = client.order.fetch(order_id)
        
        if order["status"] == "paid":
            return "SUCCESS"
        return "PENDING"
    except Exception as e:
        print(f"Razorpay Check Error: {e}")
        return "PENDING"
