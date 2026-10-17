# app/tasks.py
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Session as SessionModel, MachineStatus, AutoNotification, User, Machine, ConnectionLog, Franchise, TransactionLog
from datetime import datetime, timedelta
import pytz
import math
import io
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# PDF Generation Imports
from reportlab.lib import colors
from reportlab.lib.pagesizes import portrait, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT

from app.config import settings
from app.services.email_service import send_email_alert

# Telegram imports and sync helper functions
import asyncio
import httpx  # 🚀 Added for file uploads
from app.services.telegram_service import send_telegram_alert

def send_sync_telegram_alert(message: str):
    """Safely fires an async Telegram alert from a synchronous background task."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_telegram_alert(message))
    except RuntimeError:
        asyncio.run(send_telegram_alert(message))

def send_pdf_to_telegram_sync(pdf_bytes: bytes, filename: str, caption: str):
    """🚀 NEW: Sends a document directly to the primary Telegram admin chat."""
    try:
        bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
        chat_ids = getattr(settings, "TELEGRAM_ALLOWED_CHAT_IDS", None)
        
        if not bot_token or not chat_ids:
            return
            
        primary_chat_id = str(chat_ids).split(",")[0].strip()
        url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
        
        files = {'document': (filename, pdf_bytes, 'application/pdf')}
        data = {'chat_id': primary_chat_id, 'caption': caption, 'parse_mode': 'HTML'}
        
        with httpx.Client() as client:
            res = client.post(url, data=data, files=files)
            if res.status_code != 200:
                print(f"❌ Telegram PDF upload failed: {res.text}")
    except Exception as e:
        print(f"❌ Telegram PDF upload exception: {e}")

# Helper for UTC to IST
def to_ist(utc_dt):
    if not utc_dt:
        return None
    return utc_dt + timedelta(hours=5, minutes=30)

# Create the scheduler globally, forcing IST timezone
ist_tz = pytz.timezone('Asia/Kolkata')
scheduler = BackgroundScheduler(timezone=ist_tz)

def expire_stale_sessions():
    """Expires payment sessions that haven't been paid in 15 minutes."""
    db: Session = SessionLocal()
    try:
        cutoff_time = datetime.utcnow() - timedelta(minutes=15)
        sessions = db.query(SessionModel).filter(
            SessionModel.payment_status == "pending",
            SessionModel.created_at < cutoff_time
        ).all()
        for session in sessions:
            session.status = "failed"
            session.payment_status = "expired"
        db.commit()
        if sessions:
            print(f"🕒 Auto-expired {len(sessions)} stale sessions")
    finally:
        db.close()

def check_offline_machines():
    """
    Checks for machines that haven't sent a heartbeat in 2 minutes.
    Marks them as OFFLINE, records history, AND SENDS EMAILS.
    """
    db = SessionLocal()
    try:
        cutoff_time = datetime.utcnow() - timedelta(minutes=2)
        lost_machines = db.query(MachineStatus).filter(
            MachineStatus.is_online == True,
            MachineStatus.last_update < cutoff_time
        ).all()
        for status in lost_machines:
            print(f"📉 Machine {status.machine_id} went OFFLINE (Power/Net Cut)")
            status.is_online = False
            log = ConnectionLog(
                machine_id=status.machine_id, event_type="OFFLINE", timestamp=datetime.utcnow()
            )
            db.add(log)
            msg = f"Machine {status.machine_id} has gone OFFLINE (Possible Power Cut)."
            new_notif = AutoNotification(
                machine_id=status.machine_id, message=msg, created_at=datetime.utcnow(), is_read=False
            )
            db.add(new_notif)
            machine = db.query(Machine).filter(Machine.id == status.machine_id).first()
            if machine:
                recipients = set()
                super_admins = db.query(User).filter(User.role == "super_admin").all()
                for admin in super_admins:
                    if admin.email: recipients.add(admin.email)
                if machine.franchise_id:
                    owners = db.query(User).filter(
                        User.franchise_id == machine.franchise_id, User.role == "admin"
                    ).all()
                    for owner in owners:
                        if owner.email: recipients.add(owner.email)
                email_body = (
                    f"🚨 ALERT: Machine Offline 🚨\n\n"
                    f"Machine ID: {status.machine_id}\n"
                    f"Name: {machine.name}\n"
                    f"Location: {machine.location}\n\n"
                    f"The machine has stopped sending signals. It may be unplugged or have lost internet."
                )
                for email in recipients:
                    print(f"📧 Sending Offline Alert to {email}...")
                    send_email_alert(f"⚠️ Offline Alert: {machine.name}", email_body, to_email=email)
        db.commit()
    except Exception as e:
        print(f"Error in check_offline_machines: {e}")
    finally:
        db.close()

def check_low_supplies():
    """Checks for low ink/paper and notifies Owners & Super Admins every 24 hours if below threshold."""
    db: Session = SessionLocal()
    try:
        LOW_INK_THRESHOLD = 15  # Alert if ink < 15%
        LOW_PAPER_THRESHOLD = 20 # Alert if paper < 20 sheets
        
        machines_status = db.query(MachineStatus).all()
        now = datetime.utcnow()
        
        for status in machines_status:
            alerts = []
            if status.ink_black < LOW_INK_THRESHOLD: alerts.append("Black Ink Low")
            if status.ink_cyan < LOW_INK_THRESHOLD: alerts.append("Cyan Ink Low")
            if status.ink_magenta < LOW_INK_THRESHOLD: alerts.append("Magenta Ink Low")
            if status.ink_yellow < LOW_INK_THRESHOLD: alerts.append("Yellow Ink Low")
            
            # Trigger if paper drops below 20
            if status.paper_count < LOW_PAPER_THRESHOLD: 
                alerts.append(f"Paper Low ({status.paper_count} sheets left)")
            
            if alerts:
                message = f"⚠️ Low Supplies: {', '.join(alerts)}"
                
                # Fetch the most recent notification for this machine, ignoring 'is_read'
                last_alert = db.query(AutoNotification).filter(
                    AutoNotification.machine_id == status.machine_id
                ).order_by(AutoNotification.created_at.desc()).first()
                
                # Determine if 24 hours have passed since the last alert
                should_send = False
                if not last_alert:
                    should_send = True
                else:
                    time_since_last = now - last_alert.created_at
                    if time_since_last >= timedelta(hours=24):
                        should_send = True
                
                if should_send:
                    # 1. Log the new alert in DB to reset the 24-hour timer
                    new_alert = AutoNotification(
                        message=message, 
                        machine_id=status.machine_id, 
                        created_at=now, 
                        is_read=False 
                    )
                    db.add(new_alert)
                    db.commit() # Commit immediately to prevent duplicate triggers
                    
                    print(f"🔔 Generated 24h interval alert for {status.machine_id}: {message}")
                    
                    # 2. Gather Recipients
                    machine = db.query(Machine).filter(Machine.id == status.machine_id).first()
                    recipients = set()
                    
                    super_admins = db.query(User).filter(User.role == "super_admin").all()
                    for admin in super_admins:
                        if admin.email: recipients.add(admin.email)
                        
                    if machine and machine.franchise_id:
                        owners = db.query(User).filter(
                            User.franchise_id == machine.franchise_id, User.role == "admin"
                        ).all()
                        for owner in owners:
                            if owner.email: recipients.add(owner.email)
                            
                    # 3. Send Email
                    location_str = machine.location if machine else 'Unknown'
                    email_body = (
                        f"Machine ID: {status.machine_id}\n"
                        f"Location: {location_str}\n\n"
                        f"Issues Detected:\n{', '.join(alerts)}\n\n"
                        f"Please refill supplies immediately."
                    )
                    
                    for email in recipients:
                        send_email_alert(f"Supply Alert: {status.machine_id}", email_body, to_email=email)
                        
                    # 4. Send Telegram Notification
                    telegram_msg = f"⚠️ <b>Supply Alert</b>\nMachine: {status.machine_id}\nLocation: {location_str}\nAlerts: {', '.join(alerts)}"
                    send_sync_telegram_alert(telegram_msg)

    except Exception as e:
        print(f"Error in check_low_supplies: {e}")
    finally:
        db.close()


def send_daily_transaction_report():
    """Generates a PDF report for each active franchise and emails it."""
    db = SessionLocal()
    try:
        # Get today's date in IST
        target_date = datetime.now(ist_tz).date()
        start_of_day = datetime(target_date.year, target_date.month, target_date.day)
        end_of_day = start_of_day + timedelta(days=1)

        # Get Franchises with reports enabled
        active_franchises = db.query(Franchise).filter(
            Franchise.daily_report_enabled == True,
            Franchise.contact_email.isnot(None)
        ).all()

        if not active_franchises:
            print("No franchises have daily reports enabled. Skipping.")
            return

        for franchise in active_franchises:
            machines = db.query(Machine.id).filter(Machine.franchise_id == franchise.id).all()
            machine_ids = [m.id for m in machines]
            if not machine_ids: continue

            # Get today's transactions for this franchise
            results = db.query(TransactionLog, SessionModel)\
                .outerjoin(SessionModel, TransactionLog.session_id == SessionModel.id)\
                .filter(
                    TransactionLog.created_at >= start_of_day,
                    TransactionLog.created_at < end_of_day,
                    TransactionLog.machine_id.in_(machine_ids)
                )\
                .order_by(TransactionLog.created_at.desc())\
                .all()

            if not results:
                print(f"No transactions today for Franchise: {franchise.name}. Skipping email.")
                continue

            # Generate PDF in memory
            pdf_buffer = io.BytesIO()
            doc = SimpleDocTemplate(pdf_buffer, pagesize=portrait(A4), rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
            elements = []

            # Styles
            styles = getSampleStyleSheet()

            # Custom Styles exactly matching the image
            company_style = ParagraphStyle(name='Company', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=16, alignment=0)
            report_title_style = ParagraphStyle(name='Title', parent=styles['Normal'], alignment=TA_RIGHT, fontSize=14, fontName='Helvetica-Bold')
            right_text_style = ParagraphStyle(name='RightText', parent=styles['Normal'], alignment=TA_RIGHT, fontSize=10)

            # The green color used in the image for TOTAL EARNINGS
            total_style = ParagraphStyle(name='TotalStyle', parent=styles['Normal'], textColor=colors.HexColor("#4CAF50"), fontSize=14, fontName='Helvetica-Bold')
            detail_cell_style = ParagraphStyle(name='DetailCell', parent=styles['Normal'], fontSize=9, alignment=1) # 1 is TA_CENTER

            # --- DATA PREPARATION AND CALCULATION ---
            table_data = [["Date & Time", "Order ID", "Print Details", "Status", "Amount"]]
            total_revenue = 0.0

            for trans, sess in results:
                is_success = trans.payment_status.upper() == "SUCCESS"
                if is_success:
                    total_revenue += trans.amount

                # Format Date to DD/MM  HH:MM
                dt = to_ist(trans.created_at)
                formatted_datetime = dt.strftime("%d/%m  %H:%M") if dt else "N/A"

                # Truncate Order ID to 8 characters
                final_order_id = trans.order_id if trans.order_id else (sess.phonepe_order_id if sess else "N/A")
                short_order_id = final_order_id[:8] if len(final_order_id) > 8 else final_order_id

                # Format Dynamic Print Details
                details_content = "Other"
                if sess and sess.total_pages > 0:
                    print_mode = "Color" if sess.color else "B&W"
                    side_mode = "Duplex" if sess.duplex else "Simplex"
                    copy_word = "copy" if sess.copies == 1 else "copies"

                    if sess.duplex:
                        pages_per_copy = (sess.total_pages // sess.copies) if sess.copies > 0 else 0
                        total_sheets = math.ceil(pages_per_copy / 2.0) * sess.copies
                    else:
                        total_sheets = sess.total_pages

                    # Using Paragraph for the cell so <br/> renders exactly like \n in Dart
                    details_str = f"{sess.total_pages} pgs | {sess.copies} {copy_word} | {side_mode} {print_mode}<br/>({total_sheets} Sheets used)"
                    details_content = Paragraph(details_str, detail_cell_style)

                table_data.append([
                    formatted_datetime,
                    short_order_id,
                    details_content,
                    trans.payment_status,
                    f"{trans.amount:.2f}"
                ])

            # --- 1. HEADER ROW (With Logo) ---

            # Place your logo image in your static folder and update this path if needed.
            logo_path = "static/logo.png"

            if os.path.exists(logo_path):
                # Create a mini-table to hold the logo and text side-by-side
                logo_img = Image(logo_path, width=45, height=45)
                left_header = Table([[logo_img, Paragraph("HyNa Technologies", company_style)]], colWidths=[55, 212])
                left_header.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
            else:
                # Fallback if no image is found
                left_header = Paragraph("HyNa Technologies", company_style)

            # Build the right side text stack
            right_header_content = [
                Paragraph("EARNINGS REPORT", report_title_style),
                Paragraph(f"Franchise: {franchise.name}", right_text_style),
                Paragraph(f"Date: {target_date.strftime('%d/%m/%Y')}", right_text_style)
            ]

            # 535 total width (595 A4 width - 60 margins). Split 50/50.
            header_table = Table([[left_header, right_header_content]], colWidths=[267, 267])
            header_table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ]))
            elements.append(header_table)
            elements.append(Spacer(1, 20))

            # --- 2. SUMMARY TOTALS ---
            elements.append(Paragraph(f"TOTAL EARNINGS: Rs. {total_revenue:.2f}", total_style))
            elements.append(Spacer(1, 15))

            # --- 3. DATA TABLE ---
            if len(table_data) == 1:
                elements.append(Paragraph("No transactions found for this period.", styles['Normal']))
            else:
                # Adjusted column widths to perfectly match the proportions in the image
                # Total pts = 535
                col_widths = [90, 80, 205, 80, 80]

                t = Table(table_data, colWidths=col_widths)
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#270a4b")), # Brand Header Color
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                    ('TOPPADDING', (0, 0), (-1, 0), 8),
                    ('BOTTOMPADDING', (0, 1), (-1, -1), 10), # Added padding for spacious rows
                    ('TOPPADDING', (0, 1), (-1, -1), 10),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black), # Changed to solid black borders
                    ('FONTSIZE', (0, 1), (-1, -1), 9),
                ]))

                # Alternating Row Colors
                for i in range(1, len(table_data)):
                    bg_color = colors.HexColor("#F5F5F5") if i % 2 != 0 else colors.white
                    t.setStyle(TableStyle([('BACKGROUND', (0, i), (-1, i), bg_color)]))

                elements.append(t)

            # Build PDF
            doc.build(elements)

            # --- EMAIL SENDING LOGIC ---
            msg = MIMEMultipart()
            msg['Subject'] = f"📊 Daily Print Report ({franchise.name}) - {target_date.strftime('%b %d, %Y')}"
            msg['From'] = "Printiok <noreply@hynatechnologies.in>"
            msg['To'] = franchise.contact_email

            body = f"""
            <p>Hi,</p>
            <p>Please find the attached transaction report of {target_date.strftime('%d/%m/%y')} for your reference.</p>
            <p>For any queries or clarifications, feel free to contact us.</p>
            <p><strong>- HyNa Technologies Accounting Team</strong></p>
            """
            msg.attach(MIMEText(body, 'html'))

            pdf_filename = f"Transactions_{franchise.name.replace(' ', '_')}_{target_date}.pdf"
            part = MIMEApplication(pdf_buffer.getvalue(), _subtype="pdf")
            part.add_header('Content-Disposition', 'attachment', filename=pdf_filename)
            msg.attach(part)

            try:
                with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
                    server.starttls()
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    server.send_message(msg)
                print(f"✅ Sent PDF report to {franchise.name} ({franchise.contact_email}).")

                # 🚀 Telegram confirmation WITH PDF ATTACHMENT
                telegram_caption = f"📊 <b>Daily Report</b>\nFranchise: {franchise.name}\nTotal Revenue: Rs. {total_revenue:.2f}\nDate: {target_date.strftime('%d/%m/%Y')}"
                send_pdf_to_telegram_sync(pdf_buffer.getvalue(), pdf_filename, telegram_caption)

            except Exception as e:
                print(f"❌ Failed to email {franchise.name}: {e}")

                # Telegram failure alert
                telegram_err_msg = f"❌ <b>Report Failure</b>\nFailed to send daily PDF report for Franchise: {franchise.name}\nError: {str(e)}"
                send_sync_telegram_alert(telegram_err_msg)

    except Exception as e:
        print(f"❌ Critical failure in daily report job: {e}")
        send_sync_telegram_alert(f"❌ <b>CRITICAL ERROR:</b> Daily Report Scheduler crashed.\nError: {str(e)}")
    finally:
        db.close()

def start_scheduler():
    # Only add jobs if they haven't been added
    if not scheduler.get_jobs():
        scheduler.add_job(expire_stale_sessions, "interval", minutes=2)
        # scheduler.add_job(check_offline_machines, "interval", minutes=1)
        scheduler.add_job(check_low_supplies, "interval", minutes=1)

        # Schedule the PDF report at exactly 11:59 PM (IST)
        scheduler.add_job(send_daily_transaction_report, "cron", hour=23, minute=59)

    scheduler.start()
