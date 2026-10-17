from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Request
from sqlalchemy.orm import Session as DBSession
from pathlib import Path
import os
import shutil
import subprocess  # 🚀 ADDED FOR COMPRESSION
import fitz  # PyMuPDF
import json
import math
from uuid import uuid4
from datetime import datetime

from app.database import get_db
from app.models import CloudPrintJob, Customer, Machine
from app.config import settings
from app.services.payment_gateway import create_order
from app.routers.sessions import parse_bool
from app.customer_auth import get_current_customer

router = APIRouter(tags=["cloud_jobs"])
UPLOAD_DIR = Path(settings.UPLOAD_DIR)

@router.post("/upload")
async def upload_cloud_job(
    manifest: str = Form(...),
    copies: int = Form(1),
    duplex: str = Form("false"),
    machine_id: str = Form(...),
    files: list[UploadFile] = File(None),
    db: DBSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer)
):
    if files is None: files = []
    job_id = f"CLD-{uuid4().hex[:8].upper()}"

    # 1. Fetch Target Machine Pricing
    machine = db.query(Machine).filter_by(id=machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Target machine not found")

    try:
        file_configs = json.loads(manifest)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid manifest format")

    master_doc = fitz.open()
    requires_color = False

    # 2. Process and Merge PDFs
    for cfg in file_configs:
        filename = cfg.get('filename')
        start = int(cfg.get('start_page', 1))
        end = int(cfg.get('end_page', 1))
        is_color = str(cfg.get('color', 'true')).lower() == "true"
        orientation = cfg.get('orientation', 'portrait')

        if is_color: requires_color = True

        uploaded_file = next((f for f in files if f.filename == filename), None)
        if not uploaded_file: continue

        temp_path = UPLOAD_DIR / f"temp_{job_id}_{filename}"
        try:
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(uploaded_file.file, buffer)

            src_doc = fitz.open(str(temp_path))
            extracted = fitz.open()

            # Check if it's a PDF. If it's an image, convert it first!
            if src_doc.is_pdf:
                extracted.insert_pdf(src_doc, from_page=start - 1, to_page=end - 1)
            else:
                pdf_bytes = src_doc.convert_to_pdf()
                img_pdf = fitz.open("pdf", pdf_bytes)
                extracted.insert_pdf(img_pdf)
                img_pdf.close()

            src_doc.close()

            if orientation == "landscape":
                baked = fitz.open()
                for page in extracted:
                    new_p = baked.new_page(width=min(page.rect.width, page.rect.height), height=max(page.rect.width, page.rect.height))
                    new_p.show_pdf_page(new_p.rect, extracted, page.number, rotate=(page.rotation + 270) % 360)
                extracted.close(); extracted = baked

            master_doc.insert_pdf(extracted); extracted.close()
        finally:
            if os.path.exists(temp_path): os.remove(temp_path)

    if master_doc.page_count == 0:
        raise HTTPException(status_code=400, detail="Empty PDF")

    # Save Final PDF
    total_master_pages = master_doc.page_count
    file_location = UPLOAD_DIR / f"{job_id}.pdf"
    master_doc.save(str(file_location), garbage=4, deflate=True)
    master_doc.close()

    # 🚀 --- NEW: INLINE GHOSTSCRIPT COMPRESSION --- 🚀
    try:
        compressed_loc = str(file_location).replace(".pdf", "_comp.pdf")
        subprocess.run([
            "/usr/bin/gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", 
            "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH", 
            f"-sOutputFile={compressed_loc}", str(file_location)
        ], check=True)
        
        # If compression succeeded and made the file smaller, replace the original
        if os.path.exists(compressed_loc) and os.path.getsize(compressed_loc) < os.path.getsize(str(file_location)):
            os.replace(compressed_loc, str(file_location))
            print(f"✅ Cloud Job {job_id} compressed successfully!")
        elif os.path.exists(compressed_loc):
            os.remove(compressed_loc) # Delete temp file if compression didn't help
    except Exception as e:
        print(f"⚠️ Compression failed for Cloud Job {job_id}: {e}")
    # --------------------------------------------------

    # 3. Calculate Targeted Pricing (Based on selected machine)
    duplex_bool = parse_bool(duplex)
    grand_total = 0.0

    for cfg in file_configs:
        pages = int(cfg.get('end_page', 1)) - int(cfg.get('start_page', 1)) + 1
        is_color = str(cfg.get('color', 'true')).lower() == "true"

        # Pull exact rates from the specific machine
        s_rate = machine.price_color if is_color else machine.price_bw
        d_rate = getattr(machine, 'price_color_duplex', 4.0) if is_color else getattr(machine, 'price_bw_duplex', 1.5)

        if duplex_bool:
            grand_total += (math.floor(pages / 2) * d_rate) + ((pages % 2) * s_rate)
        else:
            grand_total += (pages * s_rate)

    final_amount = grand_total * copies

    # 4. Save to Database
    new_job = CloudPrintJob(
        id=job_id,
        customer_id=current_customer.id,
        target_machine_id=machine.id,
        file_path=str(file_location),
        file_name=f"AppUpload_{job_id}",
        color=requires_color,
        duplex=duplex_bool,
        copies=copies,
        total_pages=total_master_pages,
        total_amount=final_amount,
        payment_status="pending"
    )
    db.add(new_job)
    db.commit()

    return {
        "cloud_job_id": job_id,
        "total_pages": total_master_pages * copies,
        "amount": final_amount,
        "target_machine": machine.name
    }

@router.post("/{job_id}/payment")
def generate_cloud_payment(
    job_id: str,
    request: Request,
    db: DBSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer)
):
    job = db.query(CloudPrintJob).filter_by(id=job_id, customer_id=current_customer.id).first()
    if not job: raise HTTPException(status_code=404, detail="Cloud job not found or access denied")

    if job.payment_status in ["PAYMENT_PENDING", "success"] and job.razorpay_order_id:
        return {
            "order_id": job.razorpay_order_id,
            "amount": job.total_amount,
            "currency": "INR",
            "razorpay_key": settings.RAZORPAY_KEY_ID
        }

    try:
        order = create_order(
            amount=job.total_amount,
            session_id=job.id,
            key_id=settings.RAZORPAY_KEY_ID,
            key_secret=settings.RAZORPAY_KEY_SECRET
        )

        job.razorpay_order_id = order["order_id"]
        job.payment_status = "PAYMENT_PENDING"
        db.commit()

        return {
            "order_id": order["order_id"],
            "amount": order["amount"],
            "currency": "INR",
            "razorpay_key": settings.RAZORPAY_KEY_ID
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Payment gateway failed.")

@router.post("/{job_id}/mark-paid")
def mark_cloud_paid(
    job_id: str,
    db: DBSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer)
):
    job = db.query(CloudPrintJob).filter_by(id=job_id, customer_id=current_customer.id).first()
    if not job: raise HTTPException(404, "Job not found or access denied")

    job.payment_status = "success"
    db.commit()
    return {"status": "success", "message": "Cloud job is ready for a kiosk."}

@router.get("/my-jobs")
def get_my_cloud_jobs(
    db: DBSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer)
):
    jobs = db.query(CloudPrintJob).filter(
        CloudPrintJob.customer_id == current_customer.id,
        CloudPrintJob.payment_status == "success"
    ).order_by(CloudPrintJob.created_at.desc()).all()

    return jobs

@router.get("/my-live-jobs")
def get_live_jobs(
    db: DBSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer)
):
    jobs = db.query(CloudPrintJob).filter(
        CloudPrintJob.customer_id == current_customer.id,
        CloudPrintJob.payment_status == "success"
    ).order_by(CloudPrintJob.created_at.desc()).all()

    return [
        {
            "job_id": job.id,
            "file_name": job.file_name,
            "total_pages": job.total_pages,
            "amount": job.total_amount,
            "date": job.created_at.strftime("%Y-%m-%d %H:%M")
        } for job in jobs
    ]

@router.get("/my-history")
def get_job_history(
    db: DBSession = Depends(get_db),
    current_customer: Customer = Depends(get_current_customer)
):
    jobs = db.query(CloudPrintJob).filter(
        CloudPrintJob.customer_id == current_customer.id,
        CloudPrintJob.payment_status == "used"
    ).order_by(CloudPrintJob.created_at.desc()).limit(20).all()

    return [
        {
            "job_id": job.id,
            "file_name": job.file_name,
            "total_pages": job.total_pages,
            "amount": job.total_amount,
            "date": job.created_at.strftime("%Y-%m-%d %H:%M")
        } for job in jobs
    ]
