from app.database import SessionLocal
from app.models import TransactionLog, Session

# This is the order_id from your JSON (e.g., "order_SsjENTFJoimEdR")
# NOT the TXN string.
TARGET_ORDER_ID = "order_TnefTdQ3J69Fvc" 

db = SessionLocal()

try:
    print(f"Searching for Order ID: {TARGET_ORDER_ID}...")

    # 1. Update Transaction Log
    txn = db.query(TransactionLog).filter(TransactionLog.order_id == TARGET_ORDER_ID).first()
    if txn:
        print(f"Found Transaction! Changing status from {txn.payment_status} to FAILED")
        txn.payment_status = "FAILED"
    else:
        print("Transaction NOT found in logs.")

    # 2. Update Session
    # Note: Check if your model uses 'phonepe_order_id' or 'order_id' column
    session = db.query(Session).filter(Session.phonepe_order_id == TARGET_ORDER_ID).first()
    if session:
        print(f"Found Session! Changing status to 'failed'")
        session.payment_status = "failed"
        session.status = "failed"
    else:
        print("Session NOT found with that Order ID.")

    db.commit()
    print("✅ Done. Changes saved.")

except Exception as e:
    print(f"❌ Error: {e}")
    db.rollback()
finally:
    db.close()
