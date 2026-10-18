from app.models import CloudPrintJob
from fastapi import (
    APIRouter, UploadFile, File, Form, HTTPException, Depends, status, Request, Response

)
from app.customer_auth import get_current_customer
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession
from uuid import uuid4
from pathlib import Path
import shutil
import os
import subprocess  
import io          
import paho.mqtt.publish as mqtt_publish
import json
from datetime import date, datetime
import fitz  # PyMuPDF
import logging
import math
from PIL import Image
from app.models import Franchise  
from app.services.payment_gateway import check_payment_status, create_order
from app.auth import verify_machine_token, verify_internal_api_key
from app import crud
from app.models import (
    PrintJob,
    Machine,
    MachineStatus,
    Session as SessionModel,
    DailyEarnings,
    TonerConsumption,
    TransactionLog,
    MachineTemplate,
    AutoNotification,
    User
)
from app.schemas import SessionRequest, Session, PrintJobData, SessionStatus
from app.config import settings
from app.database import get_db

logger = logging.getLogger("sessions")
router = APIRouter(tags=["sessions"])

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB

templates = Jinja2Templates(directory="static/upload-ui")


def parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if not value:
        return False
    return str(value).strip().lower() in ("1", "true", "yes")

# =======================================================================
# 🚀 MQTT HELPER
# =======================================================================
def push_mqtt_command(machine_id: str, action: str, session_data: dict) -> bool:
    try:
        topic = f"kiosks/{machine_id}/commands"

        payload = json.dumps({
            "action": action,
            "session_data": session_data
        })

        mqtt_publish.single(
            topic,
            payload=payload,
            hostname=settings.MQTT_BROKER,
            port=settings.MQTT_PORT,
            qos=1,
            retain=False,
            auth={
                "username": settings.MQTT_USERNAME,
                "password": settings.MQTT_PASSWORD
            },
            client_id=f"fastapi_pub_{machine_id[-6:]}_{action}"
        )

        print(f"🚀 SUCCESS: MQTT '{action}' command sent to {topic}!")
        return True

    except Exception as e:
        print(f"❌ MQTT Publish Error: {e}")
        return False

def get_file_url(file_path):
    file_name = os.path.basename(file_path) if file_path else ""
    return f"{settings.BASE_URL}/fserver/files/{file_name}"

# =======================================================================
# 🚀 PRINT ACCOUNTING HELPER 
# =======================================================================
def process_print_accounting(db: DBSession, session: SessionModel):
    existing_job = db.query(PrintJob).filter_by(session_id=session.id).first()
    if existing_job: return

    current_machine = db.query(Machine).filter_by(id=session.machine_id).first()
    machine_status = db.query(MachineStatus).filter_by(machine_id=session.machine_id).first()
    toner = db.query(TonerConsumption).filter_by(machine_id=session.machine_id).first()

    if not current_machine or not machine_status: return
    if not toner:
        # Un-configured toner must not skip accounting (no PrintJob / earnings / paper deduction).
        # Unset rates fall back to the 0.02 / 0.05 defaults below.
        toner = TonerConsumption(machine_id=session.machine_id)
        db.add(toner)

    printed_pages = int(session.total_pages or 0)
    copies = int(session.copies or 1)

    if getattr(session, "duplex", False):
        pages_per_copy = printed_pages // copies if copies > 0 else 0
        sheets_per_copy = math.ceil(pages_per_copy / 2.0)
        sheets_used = sheets_per_copy * copies
    else:
        sheets_used = printed_pages

    # 1. Deduct Paper
    old_paper_count = machine_status.paper_count or 0
    new_paper_count = max(0, old_paper_count - sheets_used)
    machine_status.paper_count = new_paper_count

    # 📧 + 📱 PAPER ALERTS
    if old_paper_count >= 10 and new_paper_count < 10 and new_paper_count > 0:
        from app.services.email_service import send_email_alert
        send_email_alert(
            subject=f"⚠️ Low Paper: {session.machine_id}",
            body=f"Machine {session.machine_id} has {new_paper_count} physical sheets left. Refill soon."
        )
    elif old_paper_count > 0 and new_paper_count == 0:
        from app.services.email_service import send_email_alert
        from app.services.fcm_service import send_push_notification

        send_email_alert(
            subject=f"🚨 PAPER EMPTY: {session.machine_id}",
            body=f"Machine {session.machine_id} physical paper counter hit 0. Immediate refill required."
        )

        new_alert = AutoNotification(machine_id=session.machine_id, message="Physical Paper Empty")
        db.add(new_alert)
        db.commit()

        admins = db.query(User).filter(User.fcm_token.isnot(None)).all()
        for admin in admins:
            send_push_notification(
                token=admin.fcm_token,
                title=f"🚨 PAPER EMPTY: {session.machine_id}",
                body="Database physical paper counter hit 0.",
                data={"machine_id": session.machine_id}
            )

    # 2. Deduct Ink
    if session.color:
        machine_status.ink_cyan = max(0, (machine_status.ink_cyan or 0) - (printed_pages * (toner.color_cyan or 0.05)))
        machine_status.ink_magenta = max(0, (machine_status.ink_magenta or 0) - (printed_pages * (toner.color_magenta or 0.05)))
        machine_status.ink_yellow = max(0, (machine_status.ink_yellow or 0) - (printed_pages * (toner.color_yellow or 0.05)))
        machine_status.ink_black = max(0, (machine_status.ink_black or 0) - (printed_pages * (toner.color_black or 0.02)))
    else:
        machine_status.ink_black = max(0, (machine_status.ink_black or 0) - (printed_pages * (toner.bw_per_page or 0.02)))

    # 3. Add Earnings
    earned_amount = getattr(session, "total_amount", 0.0)
    if not earned_amount:
        earned_amount = printed_pages * (current_machine.price_color if session.color else current_machine.price_bw)

    earnings = db.query(DailyEarnings).filter_by(machine_id=session.machine_id, record_date=date.today()).first()
    if earnings:
        earnings.amount += earned_amount
    else:
        db.add(DailyEarnings(machine_id=session.machine_id, amount=earned_amount, record_date=date.today()))

    # 4. Save Job
    print_job = PrintJob(
        machine_id=session.machine_id, session_id=session.id, timestamp=datetime.utcnow(),
        amount=earned_amount, bw_copies=0 if session.color else printed_pages,
        color_copies=printed_pages if session.color else 0, duplex=getattr(session, "duplex", False)
    )
    db.add(print_job)
    session.status = "printing"
    db.commit()

# =======================================================================
# 🚀 EXACTLY-ONCE START_PRINT
# Whoever atomically flips the session to 'printing' owns the dispatch.
# Webhook retries, order.paid + payment.captured, /payment-success reloads,
# the cloud link and /pending can all race - only one of them wins.
# =======================================================================
def build_start_print_data(session: SessionModel) -> dict:
    return {
        "id": session.id,
        "file_url": get_file_url(session.file_path),
        "start_page": session.start_page,
        "end_page": session.end_page,
        "copies": session.copies,
        "color": session.color,
        "duplex": getattr(session, "duplex", False),
        "orientation": session.orientation or "portrait",
    }


def run_claimed_print(db: DBSession, session_id: str) -> bool:
    """Accounting + MQTT publish. Call ONLY after YOU flipped the session to 'printing'."""
    session = db.query(SessionModel).filter_by(id=session_id).first()
    try:
        process_print_accounting(db, session)  # idempotent (PrintJob guard)
    except Exception as e:
        db.rollback()
        logger.error(f"Accounting failed for session {session_id}: {e}")
        session = db.query(SessionModel).filter_by(id=session_id).first()

    if push_mqtt_command(session.machine_id, "start_print", build_start_print_data(session)):
        return True

    # Nothing reached the kiosk: hand the job back so /pending or the next retry can serve it.
    db.query(SessionModel).filter(
        SessionModel.id == session_id, SessionModel.status == "printing"
    ).update({"status": "ready_to_print"}, synchronize_session=False)
    db.commit()
    return False


def dispatch_start_print(db: DBSession, session_id: str, from_statuses=("ready_to_print",)) -> bool:
    """Claim a paid session and start it. Returns False if someone else already did."""
    claimed = db.query(SessionModel).filter(
        SessionModel.id == session_id,
        SessionModel.payment_status == "success",
        SessionModel.status.in_(from_statuses),
    ).update({"status": "printing"}, synchronize_session=False)
    db.commit()
    return claimed == 1 and run_claimed_print(db, session_id)

# =======================================================================
# 1) SERVE UPLOAD PAGE (GET REQUEST)
# =======================================================================
@router.get("/{session_id}/upload")
def get_upload_page(
    session_id: str,
    request: Request,
    db: DBSession = Depends(get_db)
):
    session = db.query(SessionModel).filter_by(id=session_id).first()
    if not session:
        return HTMLResponse(content="<h2 style='color:red; text-align:center; margin-top:50px;'>Invalid or Expired Session</h2>", status_code=404)

    client_cookie = request.cookies.get("hyna_session_owner")

    if session.status == "waiting":
        session.status = "active"
        db.commit()
        response = FileResponse("static/upload-ui/index.html")
        response.set_cookie(key="hyna_session_owner", value=session_id, max_age=300)
        return response
    elif session.status == "active":
        if client_cookie == session_id:
            return FileResponse("static/upload-ui/index.html")
        else:
            return HTMLResponse(content="<h2 style='color:orange; text-align:center; margin-top:50px;'>Session in use by another user.<br>Please wait or scan a new QR code.</h2>", status_code=403)
    elif session.status == "ready_to_print":
        if client_cookie == session_id:
            return FileResponse("static/upload-ui/index.html")
        else:
             return HTMLResponse(content="<h2 style='color:green; text-align:center; margin-top:50px;'>File uploaded. Proceeding to payment...</h2>", status_code=200)

    return HTMLResponse(content="<h2 style='color:red; text-align:center; margin-top:50px;'>Session Expired or Completed</h2>", status_code=410)

# =======================================================================
# 2) HANDLE FILE UPLOAD (POST REQUEST) - FULLY OPTIMIZED & SECURE
# =======================================================================
@router.post("/{session_id}/upload")
async def upload_file(
    session_id: str,
    manifest: str = Form(...),
    copies: int = Form(1),
    duplex: str = Form("false"),
    files: list[UploadFile] = File(None),
    db: DBSession = Depends(get_db)
):
    if files is None: files = []

    # 1. Size Check
    MAX_FILE_SIZE = 200 * 1024 * 1024
    for uploaded_file in files:
        uploaded_file.file.seek(0, 2)
        if uploaded_file.file.tell() > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large (Max 200MB)")
        uploaded_file.file.seek(0)

    session = db.query(SessionModel).filter_by(id=session_id).first()
    if not session: raise HTTPException(status_code=404, detail="Not found")
    
    if session.payment_status in ["PAYMENT_PENDING", "success"]:
        raise HTTPException(status_code=400, detail="Cart is locked. Payment already initiated.")
    if session.status == "failed":
        raise HTTPException(status_code=400, detail="Session expired. Please start a new session.")
    
    try:
        file_configs = json.loads(manifest)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid manifest format")

    master_doc = fitz.open()
    TEMPLATE_DIR = Path(settings.UPLOAD_DIR) / "templates" / str(session.machine_id)
    session_requires_color = False
    
    for cfg in file_configs:
        is_template = cfg.get('is_template', False)
        filename = cfg.get('filename')
        start, end = int(cfg.get('start_page', 1)), int(cfg.get('end_page', 1))
        file_color = str(cfg.get('color', 'true')).lower() == "true"
        file_orientation = cfg.get('orientation', 'portrait')

        if file_color: session_requires_color = True

        src_doc = None
        temp_path = None

        if is_template:
            src_doc = fitz.open(str(TEMPLATE_DIR / filename))
        else:
            uploaded_file = next((f for f in files if f.filename == filename), None)
            if not uploaded_file: continue
            
            temp_path = UPLOAD_DIR / f"temp_{session_id}_{filename}"
            try:
                if uploaded_file.content_type.startswith("image/"):
                    img = Image.open(uploaded_file.file).convert("RGB")
                    pdf_bytes = io.BytesIO()
                    img.save(pdf_bytes, format='PDF')
                    src_doc = fitz.open("pdf", pdf_bytes.getvalue())
                else:
                    with open(temp_path, "wb") as buffer: shutil.copyfileobj(uploaded_file.file, buffer)
                    src_doc = fitz.open(str(temp_path))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"File processing error: {str(e)}")

        try:
            extracted = fitz.open()
            extracted.insert_pdf(src_doc, from_page=start - 1, to_page=end - 1)
            src_doc.close()

            if file_orientation == "landscape":
                baked = fitz.open()
                for page in extracted:
                    new_p = baked.new_page(width=min(page.rect.width, page.rect.height), height=max(page.rect.width, page.rect.height))
                    new_p.show_pdf_page(new_p.rect, extracted, page.number, rotate=(page.rotation + 270) % 360)
                extracted.close(); extracted = baked
            
            master_doc.insert_pdf(extracted); extracted.close()
        finally:
            if temp_path and os.path.exists(temp_path): os.remove(temp_path)

    # 2. Finalize and Save 
    if master_doc.page_count == 0: raise HTTPException(status_code=400, detail="Empty PDF")
    
    total_master_pages = master_doc.page_count
    duplex_bool = parse_bool(duplex)

    # 🚀 NEW: PAPER AVAILABILITY CHECK (Web Upload)
    sheets_needed = (math.ceil(total_master_pages / 2) * copies) if duplex_bool else (total_master_pages * copies)
    machine_status = db.query(MachineStatus).filter_by(machine_id=session.machine_id).first()
    
    if not machine_status or sheets_needed > (machine_status.paper_count or 0):
        available = machine_status.paper_count if machine_status else 0
        master_doc.close() # Clean up memory
        raise HTTPException(
            status_code=400, 
            detail=f"Not enough paper. You need {sheets_needed} sheets, but only {available} are left."
        )

    file_location = UPLOAD_DIR / f"{session_id}.pdf"
    master_doc.save(str(file_location), garbage=4, deflate=True, clean=True)
    master_doc.close()

    # 🚀 Ghostscript Compression
    try:
        compressed_loc = str(file_location).replace(".pdf", "_comp.pdf")
        subprocess.run(["/usr/bin/gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={compressed_loc}", str(file_location)], check=True)
        if os.path.exists(compressed_loc) and os.path.getsize(compressed_loc) < os.path.getsize(str(file_location)):
            os.replace(compressed_loc, str(file_location))
        elif os.path.exists(compressed_loc): os.remove(compressed_loc)
    except Exception as e: logger.error(f"Compression failed: {e}")

    # 3. Pricing Math
    machine = db.query(Machine).filter_by(id=session.machine_id).first()
    grand_total = 0.0
    for cfg in file_configs:
        pages = int(cfg.get('end_page', 1)) - int(cfg.get('start_page', 1)) + 1
        is_color = str(cfg.get('color', 'true')).lower() == "true"
        s_rate = machine.price_color if is_color else machine.price_bw
        d_rate = getattr(machine, 'price_color_duplex', 4.0) if is_color else getattr(machine, 'price_bw_duplex', 1.5)
        
        if duplex_bool:
            grand_total += (math.floor(pages / 2) * d_rate) + ((pages % 2) * s_rate)
        else:
            grand_total += (pages * s_rate)

    session.total_amount = grand_total * copies
    session.total_pages = total_master_pages * copies
    session.file_path = str(file_location)
    session.color = session_requires_color
    session.duplex = duplex_bool
    session.copies = copies
    session.status = "ready_to_print"
    db.commit(); db.refresh(session)

    push_mqtt_command(session.machine_id, "pre_download", {"id": session.id, "file_url": get_file_url(session.file_path)})
    return session

# =======================================================================
# 3) CREATE SESSION
# =======================================================================
@router.post("/", response_model=dict, status_code=201)
def create_session(
    data: SessionRequest,
    db: DBSession = Depends(get_db),
    current_machine: Machine = Depends(verify_machine_token),
):
    if hasattr(current_machine, "is_active") and not current_machine.is_active:
         raise HTTPException(status_code=403, detail="Machine disabled.")

    machine_id = current_machine.id

    old_sessions = db.query(SessionModel).filter(
        SessionModel.machine_id == machine_id,
        SessionModel.status.in_(["waiting", "active", "ready_to_print"]),
        SessionModel.payment_status != "success"
    ).all()

    if old_sessions:
        for old in old_sessions:
            old.status = SessionStatus.failed
            old_log = db.query(TransactionLog).filter_by(session_id=old.id).first()
            if old_log and old_log.payment_status == "INITIATED":
                old_log.payment_status = "FAILED"
        db.commit()

    session = SessionModel(
        id=str(uuid4()),
        machine_id=machine_id,
        status="waiting",
        payment_status="pending",
        session_type=data.session_type,
        created_at=datetime.utcnow()
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    if data.session_type == "scan":
        qr_url = f"{settings.BASE_URL}/fserver/sessions/{session.id}/scan-control"
    else:
        qr_url = f"{settings.BASE_URL}/fserver/sessions/{session.id}/upload"

    return {"id": session.id, "qr_url": qr_url}

# =======================================================================
# 4) GET SESSION
# =======================================================================
@router.get("/{session_id}")
def get_session(
    session_id: str,
    db: DBSession = Depends(get_db),
):
    session = db.query(SessionModel).filter_by(id=session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    machine = db.query(Machine).filter_by(id=session.machine_id).first()

    price_bw = machine.price_bw if machine else 2.0
    price_color = machine.price_color if machine else 5.0
    price_bw_duplex = getattr(machine, 'price_bw_duplex', 1.5)
    price_color_duplex = getattr(machine, 'price_color_duplex', 4.0)
    supports_duplex = getattr(machine, 'supports_duplex', False)

    templates = db.query(MachineTemplate).filter_by(machine_id=session.machine_id).all()
    template_list = [{"id": t.id, "name": t.name, "file_name": t.file_name, "pages": t.pages} for t in templates]

    return {
        "id": session.id,
        "machine_id": session.machine_id,
        "status": session.status,
        "payment_status": session.payment_status,
        "phonepe_order_id": session.phonepe_order_id,
        "start_page": session.start_page,
        "end_page": session.end_page,
        "copies": session.copies,
        "color": session.color,
        "total_pages": session.total_pages,
        "file_path": session.file_path,
        "price_bw": price_bw,
        "price_color": price_color,
        "price_bw_duplex": price_bw_duplex,
        "price_color_duplex": price_color_duplex,
        "supports_duplex": supports_duplex,
        "templates": template_list
    }

# =======================================================================
# 5) START PRINT JOB (Manual)
# =======================================================================
@router.post("/{session_id}/print", response_model=PrintJobData)
async def start_print_job(
    session_id: str,
    db: DBSession = Depends(get_db),
):
    session = db.query(SessionModel).filter_by(id=session_id).first()
    if not session: raise HTTPException(status_code=404, detail="Not found")

    current_machine = db.query(Machine).filter_by(id=session.machine_id).first()
    if not current_machine: raise HTTPException(status_code=404, detail="Machine not found")

    if not current_machine.is_active:
         raise HTTPException(status_code=403, detail="Machine disabled")

    if session.payment_status != "success":
        raise HTTPException(status_code=402, detail="Payment not completed")

    file_path = Path(session.file_path or "")
    if not file_path.exists(): raise HTTPException(status_code=400, detail="File missing")

    existing_job = db.query(PrintJob).filter_by(session_id=session_id).first()
    if existing_job or session.status in ["printing", "completed"]:
        raise HTTPException(status_code=400, detail="BLOCKED: This job has already been printed.")

    process_print_accounting(db, session)

    return PrintJobData(
        file_url=f"{settings.BASE_URL}/fserver/files/{file_path.name}",
        start_page=session.start_page,
        end_page=session.end_page,
        color=session.color,
        total_pages=session.total_pages,
        duplex=getattr(session, "duplex", False),
        copies=session.copies or 1
    )

# =======================================================================
# 6) PAYMENT (Centralized Razorpay)
# =======================================================================
@router.post("/{session_id}/payment")
def generate_payment_for_session(
    session_id: str,
    request: Request,
    db: DBSession = Depends(get_db)
):
    session = db.query(SessionModel).filter_by(id=session_id).first()
    if not session: raise HTTPException(status_code=404, detail="Not found")

    if session.status == "failed":
        raise HTTPException(status_code=400, detail="Session expired. Please scan a new QR code.")

    total = getattr(session, 'total_amount', 0)
    if total <= 0:
        raise HTTPException(status_code=400, detail="Cart total is invalid or zero.")

    if session.payment_status in ["PAYMENT_PENDING", "success"]:
        if session.phonepe_order_id:
            return {
                "order_id": session.phonepe_order_id,
                "amount": total,
                "currency": "INR",
                "razorpay_key": settings.RAZORPAY_KEY_ID, 
                "message": "Resumed existing payment order"
            }
        else:
            raise HTTPException(status_code=400, detail="Payment state invalid.")

    try:
        order = create_order(
            amount=total,
            session_id=session.id,
            key_id=settings.RAZORPAY_KEY_ID,           
            key_secret=settings.RAZORPAY_KEY_SECRET    
        )

        session.phonepe_order_id = order["order_id"]
        session.payment_status = "PAYMENT_PENDING"

        new_log = TransactionLog(
            transaction_id=f"TXN-{uuid4().hex[:10].upper()}",
            session_id=session.id,
            machine_id=session.machine_id,
            amount=total,
            payment_status="INITIATED",
            ip_address=request.client.host if request.client else "unknown",
            order_id=order["order_id"]
        )
        db.add(new_log)
        db.commit()
        db.refresh(session)

        return {
            "order_id": order["order_id"],
            "amount": order["amount"],
            "currency": "INR",
            "razorpay_key": settings.RAZORPAY_KEY_ID 
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Order Creation Failed: {e}")
        raise HTTPException(status_code=500, detail="Payment gateway initialization failed.")

# =======================================================================
# 7) EXPIRE SESSION
# =======================================================================
@router.post("/{session_id}/expire")
def expire_session(
    session_id: str,
    db: DBSession = Depends(get_db),
    current_machine: Machine = Depends(verify_machine_token),
):
    session = db.query(SessionModel).filter_by(id=session_id).first()
    if session and session.machine_id == current_machine.id:
        session.status = "failed"
        log = db.query(TransactionLog).filter_by(session_id=session.id).first()
        if log and log.payment_status == "INITIATED":
            log.payment_status = "FAILED"
        db.commit()
    return {"message": "Expired"}

# =======================================================================
# 8) INTERNAL MARK PAID
# =======================================================================
@router.post("/{session_id}/mark-paid")
def mark_payment_success(
    session_id: str,
    db: DBSession = Depends(get_db),
    _: None = Depends(verify_internal_api_key)
):
    session = crud.get_session(db, session_id)
    if not session: raise HTTPException(status_code=404, detail="Not found")

    if session.payment_status == "success":
        return {"message": "Already paid. Ignoring duplicate."}

    session.payment_status = "success"
    db.commit()

    process_print_accounting(db, session)

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
            "orientation": session.orientation
        }
    )

    return {"message": "Paid"}

# =======================================================================
# 9) CHECK PENDING JOBS
# =======================================================================
@router.get("/pending/{machine_id}")
def get_pending_jobs(machine_id: str, db: DBSession = Depends(get_db)):
    job = db.query(SessionModel).filter(
        SessionModel.machine_id == machine_id,
        SessionModel.status == "ready_to_print",
        SessionModel.payment_status == "success"
    ).order_by(SessionModel.created_at.asc()).first()

    if not job:
        return {"pending": False}

    rows_affected = db.query(SessionModel).filter(
        SessionModel.id == job.id,
        SessionModel.status == "ready_to_print"
    ).update({"status": "printing"})

    db.commit()

    if rows_affected == 0:
        return {"pending": False}

    return {
        "pending": True,
        "session_data": {
            "id": job.id,
            "file_path": job.file_path,
            "start_page": job.start_page,
            "end_page": job.end_page,
            "copies": job.copies,
            "color": job.color,
            "duplex": getattr(job, "duplex", False),
            "orientation": job.orientation,
            "file_url": f"{settings.BASE_URL}/fserver/files/{os.path.basename(job.file_path)}" if job.file_path else None
        }
    }

# =======================================================================
# 🚀 SCANNER GALLERY & PDF MERGE ENDPOINTS
# =======================================================================
@router.get("/{session_id}/scan-control", response_class=HTMLResponse)
def get_scan_control_page(
    session_id: str,
    request: Request,
    db: DBSession = Depends(get_db)
):
    session = db.query(SessionModel).filter_by(id=session_id).first()

    if not session:
        return HTMLResponse(
            content="<h2 style='color:red; text-align:center; margin-top:50px;'>Invalid Session</h2>",
            status_code=404
        )

    client_cookie = request.cookies.get("hyna_session_owner")

    if session.status == "waiting":
        session.status = "active"
        db.commit()

        response = FileResponse("static/upload-ui/scan_control.html")
        response.set_cookie(
            key="hyna_session_owner",
            value=session_id,
            max_age=300,
            httponly=True,
            samesite="lax"
        )
        return response

    elif session.status == "active":
        if client_cookie == session_id:
            return FileResponse("static/upload-ui/scan_control.html")
        else:
            return HTMLResponse(
                content="""
                <body style="background:#0f172a; color:white; font-family:sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; text-align:center;">
                    <div style="padding:20px; border:1px solid #334155; border-radius:12px; background:#1e293b;">
                        <h2 style="color:#f59e0b;">Scanner is Busy</h2>
                        <p>Another user is currently controlling this kiosk.<br>Please wait or use a different machine.</p>
                        <button onclick="window.location.reload()" style="margin-top:15px; background:#6D28D9; color:white; border:none; padding:10px 20px; border-radius:8px; cursor:pointer;">Try Again</button>
                    </div>
                </body>
                """,
                status_code=403
            )

    return HTMLResponse(
        content="<h2 style='color:gray; text-align:center; margin-top:50px;'>This session has ended.</h2>",
        status_code=410
    )

@router.post("/{session_id}/finalize-scan")
def finalize_scan(
    session_id: str,
    color: str = Form("false"),
    copies: int = Form(1),
    duplex: str = Form("false"),
    db: DBSession = Depends(get_db)
):
    session = db.query(SessionModel).filter_by(id=session_id).first()
    if not session: raise HTTPException(404, "Session not found")

    scan_dir = Path(f"uploaded_files/{session_id}/")
    images = sorted([f for f in scan_dir.glob("*.jpg")])

    if not images:
        raise HTTPException(400, "No pages scanned yet.")

    pdf_path = UPLOAD_DIR / f"{session_id}.pdf"
    try:
        img_list = [Image.open(img).convert("RGB") for img in images]
        img_list[0].save(pdf_path, save_all=True, append_images=img_list[1:])
    except Exception as e:
        raise HTTPException(500, "Failed to generate PDF from scans.")

    color_bool = parse_bool(color)
    duplex_bool = parse_bool(duplex)
    total_pdf_pages = len(images)
    total_billable_pages = total_pdf_pages * copies

    machine = db.query(Machine).filter_by(id=session.machine_id).first()
    if duplex_bool and not getattr(machine, 'supports_duplex', False):
        raise HTTPException(400, "Duplex not supported by this machine.")

    sheets_needed = math.ceil(total_pdf_pages / 2) * copies if duplex_bool else total_billable_pages

    machine_status = db.query(MachineStatus).filter_by(machine_id=session.machine_id).first()
    if not machine_status or sheets_needed > machine_status.paper_count:
        available = machine_status.paper_count if machine_status else 0
        raise HTTPException(400, f"Not enough paper. You need {sheets_needed} sheets, but only {available} are left.")

    session.file_path = str(pdf_path)
    session.start_page = 1
    session.end_page = total_pdf_pages
    session.copies = copies
    session.total_pages = total_billable_pages
    session.color = color_bool
    session.duplex = duplex_bool
    session.status = "ready_to_print"

    db.commit()

    push_mqtt_command(
        machine_id=session.machine_id,
        action="pre_download",
        session_data={
            "id": session.id,
            "file_url": get_file_url(session.file_path)
        }
    )

    return {"status": "success", "redirect_url": f"/fserver/upload/{session_id}"}


# =======================================================================
# 10) LINK CLOUD JOB (The "Tap & Go" Trigger)
# =======================================================================
@router.post("/{session_id}/link-cloud-job")
def link_cloud_job(
    session_id: str,
    request: Request,
    cloud_job_id: str = Form(...),
    db: DBSession = Depends(get_db),
    current_customer = Depends(get_current_customer) 
):
    session = db.query(SessionModel).filter_by(id=session_id).first()
    if not session or session.status != "waiting":
        raise HTTPException(status_code=400, detail="Invalid or expired kiosk session.")

    cloud_job = db.query(CloudPrintJob).filter_by(
        id=cloud_job_id, 
        customer_id=current_customer.id 
    ).first()

    if not cloud_job:
        raise HTTPException(status_code=404, detail="Cloud job not found or access denied.")

    if cloud_job.payment_status != "success":
        raise HTTPException(status_code=402, detail="Cloud job is not paid.")

    if cloud_job.target_machine_id and cloud_job.target_machine_id != session.machine_id:
        raise HTTPException(
            status_code=403,
            detail=f"Location Mismatch: Job was booked for kiosk '{cloud_job.target_machine_id}', but you scanned '{session.machine_id}'."
        )

    # 🚀 NEW: PAPER AVAILABILITY CHECK (App Cloud Job)
    sheets_needed = (math.ceil(cloud_job.total_pages / 2) * cloud_job.copies) if cloud_job.duplex else (cloud_job.total_pages * cloud_job.copies)
    machine_status = db.query(MachineStatus).filter_by(machine_id=session.machine_id).first()
    
    if not machine_status or sheets_needed > (machine_status.paper_count or 0):
        available = machine_status.paper_count if machine_status else 0
        raise HTTPException(
            status_code=400, 
            detail=f"Not enough paper in this machine. Job requires {sheets_needed} sheets, but only {available} are available."
        )

    session.file_path = cloud_job.file_path
    session.start_page = 1
    session.end_page = cloud_job.total_pages
    session.copies = cloud_job.copies
    session.color = cloud_job.color
    session.duplex = cloud_job.duplex
    session.total_pages = cloud_job.total_pages * cloud_job.copies
    session.total_amount = cloud_job.total_amount
    session.payment_status = "success"
    session.status = "ready_to_print"

    cloud_job.payment_status = "used"

    new_log = TransactionLog(
        transaction_id=f"TXN-C-{uuid4().hex[:8].upper()}", 
        session_id=session.id,
        machine_id=session.machine_id,
        amount=cloud_job.total_amount,
        payment_status="SUCCESS",
        ip_address=request.client.host if request.client else "cloud_app",
        order_id=cloud_job.id 
    )
    db.add(new_log)
    db.commit()
    db.refresh(session)

    try:
        dispatch_start_print(db, session.id)  # paid + ready_to_print -> claim, account, publish once
    except Exception as e:
        print(f"❌ Error triggering MQTT print job: {e}")

    return {"message": "Success! Printing started.", "machine_id": session.machine_id}
