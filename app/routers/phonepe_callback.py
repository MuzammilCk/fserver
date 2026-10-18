from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session as DBSession
import razorpay

from app.database import get_db
from app.models import Session as SessionModel, TransactionLog

from app.routers.sessions import run_claimed_print

from app.config import settings

router = APIRouter(
    tags=["razorpay"],
    prefix="/fserver/razorpay"
)


@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    db: DBSession = Depends(get_db)
):
    # Get raw body
    payload_body = await request.body()

    signature = request.headers.get("X-Razorpay-Signature")

    if not signature:
        raise HTTPException(
            status_code=400,
            detail="Missing Signature"
        )

    # Verify webhook using CENTRAL secret
    try:
        client = razorpay.Client(
            auth=(
                settings.RAZORPAY_KEY_ID,
                settings.RAZORPAY_KEY_SECRET,
            )
        )

        client.utility.verify_webhook_signature(
            payload_body.decode("utf-8"),
            signature,
            settings.RAZORPAY_WEBHOOK_SECRET,
        )

    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(
            status_code=400,
            detail="Invalid Razorpay Signature"
        )

    payload = await request.json()
    event = payload.get("event")

    # ==================================
    # PAYMENT SUCCESS
    # ==================================
    if event in ["order.paid", "payment.captured"]:

        payment_entity = payload["payload"]["payment"]["entity"]

        razorpay_order_id = payment_entity["order_id"]
        provider_ref = payment_entity["id"]

        session = (
            db.query(SessionModel)
            .filter(
                SessionModel.phonepe_order_id == razorpay_order_id
            )
            .first()
        )

        if not session:
            return {
                "status": "ignored",
                "message": "Session not found"
            }

        # Duplicate protection (cheap pre-check; the UPDATE below is the real guard)
        if session.payment_status == "success":
            return {
                "status": "ignored",
                "message": "Already processed"
            }

        # Mark paid AND claim the print in ONE transaction, so nobody (the kiosk poll,
        # /payment-success, a webhook retry) can ever see "paid + ready_to_print".
        paid_now = (
            db.query(SessionModel)
            .filter(SessionModel.id == session.id, SessionModel.payment_status != "success")
            .update({"payment_status": "success"}, synchronize_session=False)
        )
        if not paid_now:
            db.rollback()
            return {"status": "ignored", "message": "Already processed"}

        # Anything that hasn't started printing is claimable (incl. 'failed' = paid after
        # the kiosk expired the session, same as the old behaviour).
        claimed = (
            db.query(SessionModel)
            .filter(SessionModel.id == session.id, SessionModel.status.notin_(("printing", "completed")))
            .update({"status": "printing"}, synchronize_session=False)
        )

        log = (
            db.query(TransactionLog)
            .filter_by(order_id=razorpay_order_id)
            .first()
        )

        if log:
            log.payment_status = "SUCCESS"
            log.provider_reference_id = provider_ref

        db.commit()

        if claimed:
            run_claimed_print(db, session.id)

    # ==================================
    # PAYMENT FAILED
    # ==================================
    elif event == "payment.failed":

        payment_entity = payload["payload"]["payment"]["entity"]

        razorpay_order_id = payment_entity.get("order_id")

        session = (
            db.query(SessionModel)
            .filter(
                SessionModel.phonepe_order_id == razorpay_order_id
            )
            .first()
        )

        # An earlier failed attempt can arrive AFTER the successful one - never downgrade a paid session.
        if session and session.payment_status != "success":
            session.payment_status = "failed"

        log = (
            db.query(TransactionLog)
            .filter_by(order_id=razorpay_order_id)
            .first()
        )

        if log and log.payment_status != "SUCCESS":
            log.payment_status = "FAILED"

        db.commit()

    return {"status": "ok"}
