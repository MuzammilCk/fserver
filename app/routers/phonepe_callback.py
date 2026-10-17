from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session as DBSession
import razorpay

from app.database import get_db
from app.models import Session as SessionModel, TransactionLog

from app.routers.sessions import (
    push_mqtt_command,
    get_file_url,
    process_print_accounting,
)

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

        # Duplicate protection
        if session.payment_status == "success":
            return {
                "status": "ignored",
                "message": "Already processed"
            }

        session.payment_status = "success"

        if session.status not in [
            "printing",
            "completed",
            "failed",
        ]:
            session.status = "ready_to_print"

        log = (
            db.query(TransactionLog)
            .filter_by(order_id=razorpay_order_id)
            .first()
        )

        if log:
            log.payment_status = "SUCCESS"
            log.provider_reference_id = provider_ref

        db.commit()

        try:
            process_print_accounting(db, session)
        except Exception as e:
            print(
                f"❌ Accounting Error "
                f"(Session {session.id}): {e}"
            )

        push_mqtt_command(
            machine_id=session.machine_id,
            action="start_print",
            session_data={
                "id": session.id,
                "file_url": get_file_url(session.file_path),
                "start_page": session.start_page,
                "end_page": session.end_page,
                "copies": session.copies,
                "color": session.color,
                "duplex": getattr(session, "duplex", False),
                "orientation": session.orientation,
            },
        )

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

        if session:
            session.payment_status = "failed"

        log = (
            db.query(TransactionLog)
            .filter_by(order_id=razorpay_order_id)
            .first()
        )

        if log:
            log.payment_status = "FAILED"

        db.commit()

    return {"status": "ok"}
