from app import models
import httpx
import asyncio
import logging
import json
import paho.mqtt.publish as publish
from datetime import datetime
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.database import SessionLocal
from app.models import Machine, Customer, Franchise, RefillLog, Notification, TransactionLog

logger = logging.getLogger("uvicorn.error")

# 🚀 Use settings for MQTT credentials
MQTT_BROKER = settings.MQTT_BROKER
MQTT_PORT = settings.MQTT_PORT
MQTT_USERNAME = settings.MQTT_USERNAME
MQTT_PASSWORD = settings.MQTT_PASSWORD

IST = pytz.timezone('Asia/Kolkata')

def mqtt_publish(topic: str, payload: str):
    """Helper to publish authenticated MQTT messages from Telegram commands."""
    publish.single(
        topic,
        payload=payload,
        hostname=MQTT_BROKER,
        port=MQTT_PORT,
        qos=1,
        retain=False,
        auth={
            "username": MQTT_USERNAME,
            "password": MQTT_PASSWORD
        }
    )

# ==========================================
# 1) OUTBOUND PUSH ALERTS
# ==========================================
async def send_telegram_alert(message: str):
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_ALLOWED_CHAT_IDS: return
    primary_chat_id = settings.TELEGRAM_ALLOWED_CHAT_IDS.split(",")[0].strip()
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": primary_chat_id, "text": message, "parse_mode": "HTML"}
    async with httpx.AsyncClient() as client:
        try: await client.post(url, json=payload)
        except Exception as e: logger.error(f"Failed to send Telegram alert: {e}")

def is_authorized(update: Update) -> bool:
    if not settings.TELEGRAM_ALLOWED_CHAT_IDS: return False
    allowed_ids = [i.strip() for i in settings.TELEGRAM_ALLOWED_CHAT_IDS.split(",")]
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if chat_id in allowed_ids or user_id in allowed_ids:
        return True

    # --- INTRUDER DETECTED ---
    username = update.effective_user.username or "No username"
    full_name = update.effective_user.full_name or "Unknown"
    message_text = update.message.text if update.message else "Unknown action"

    logger.warning(f"🚨 UNAUTHORIZED TELEGRAM ATTEMPT: User {user_id} (@{username}) in Chat {chat_id}.")

    alert_msg = (
        f"🚨 <b>UNAUTHORIZED BOT ATTEMPT</b> 🚨\n\n"
        f"<b>Name:</b> {full_name} (@{username})\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n"
        f"<b>Chat ID:</b> <code>{chat_id}</code>\n"
        f"<b>Message/Action:</b> {message_text}"
    )
    asyncio.create_task(send_telegram_alert(alert_msg))
    return False

# ==========================================
# 2) BASIC DASHBOARD COMMANDS
# ==========================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    msg = (
        "🛡️ <b>HyNa Admin Bot Online</b>\n\n"
        "<b>Dashboards:</b>\n"
        "/today - Live revenue & print stats\n"
        "/stats - System overview\n"
        "/customers - Latest 10 app signups\n\n"
        "<b>Hardware Control:</b>\n"
        "/enable [id] - Turn kiosk online\n"
        "/disable [id] - Emergency offline\n"
        "/adrefresh [id] - Force UI reload\n"
        "/power [id] [restart|off] - Reboot Pi\n\n"
        "<b>Logistics:</b>\n"
        "/refill [id] [paper_count] - Log refill\n"
        "/togglereport [f_id] [on|off] - Nightly PDFs\n"
        "/notify [message] - Global admin alert"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    db: Session = SessionLocal()
    try:
        total_users = db.query(Customer).count()
        verified_users = db.query(Customer).filter_by(is_verified=True).count()
        total_machines = db.query(Machine).count()
        online_machines = db.query(Machine).filter_by(is_active=True).count()

        msg = (
            "📊 <b>System Overview</b>\n\n"
            f"👥 Users: {total_users} ({verified_users} Verified)\n"
            f"🖨️ Kiosks: {total_machines} ({online_machines} Active)"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
    finally: db.close()

# ==========================================
# 3) HARDWARE CONTROL COMMANDS
# ==========================================
async def enable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if not context.args: return await update.message.reply_text("Usage: /enable [machine_id]")
    m_id = context.args[0]
    db: Session = SessionLocal()
    try:
        m = db.query(Machine).filter_by(id=m_id).first()
        if not m: return await update.message.reply_text(f"❌ Kiosk '{m_id}' not found.")
        m.is_active = True
        db.commit()
        await update.message.reply_text(f"✅ <b>{m_id}</b> is now ENABLED.", parse_mode="HTML")
    finally: db.close()

async def disable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if not context.args: return await update.message.reply_text("Usage: /disable [machine_id]")
    m_id = context.args[0]
    db: Session = SessionLocal()
    try:
        m = db.query(Machine).filter_by(id=m_id).first()
        if not m: return await update.message.reply_text(f"❌ Kiosk '{m_id}' not found.")
        m.is_active = False
        db.commit()
        await update.message.reply_text(f"🚫 <b>{m_id}</b> is now DISABLED.", parse_mode="HTML")
    finally: db.close()

async def ad_refresh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if not context.args: return await update.message.reply_text("Usage: /adrefresh [machine_id]")
    m_id = context.args[0]
    topic = f"kiosks/{m_id}/commands"
    payload = json.dumps({"action": "refresh_ads"})
    try:
        mqtt_publish(topic, payload)
        await update.message.reply_text(f"🔄 Sent Ad Refresh command to <b>{m_id}</b>.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ MQTT Error: {e}")

async def power_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if len(context.args) < 2: return await update.message.reply_text("Usage: /power [machine_id] [restart|off]")
    m_id, action = context.args[0], context.args[1].lower()
    if action not in ["restart", "off"]: return await update.message.reply_text("Action must be 'restart' or 'off'")
    topic = f"kiosks/{m_id}/commands"
    payload = json.dumps({"action": "power_control", "command": action})
    try:
        mqtt_publish(topic, payload)
        await update.message.reply_text(f"⚡ Sent '{action}' command to <b>{m_id}</b>.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ MQTT Error: {e}")

# ==========================================
# 4) DATA & NOTIFICATION COMMANDS
# ==========================================
async def refill_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /refill [machine_id] [paper_added_count]")

    m_id = context.args[0]
    try:
        paper_added = int(context.args[1])
    except ValueError:
        return await update.message.reply_text("❌ Paper count must be a number.")

    db: Session = SessionLocal()
    try:
        # 1. Update Machine Status (The real count)
        status = db.query(models.MachineStatus).filter_by(machine_id=m_id).first()
        if not status:
            return await update.message.reply_text(f"❌ Machine status not found for {m_id}.")

        # Increase existing paper count
        status.paper_count = (status.paper_count or 0) + paper_added
        status.last_update = datetime.utcnow()
        db.add(status)

        # 2. Create the Log Entry (The history)
        # We pass 0.0 for ink to satisfy the schema, but since this is
        # a log entry, it doesn't overwrite the live machine status.
        new_log = RefillLog(
            machine_id=m_id,
            paper_count=paper_added,
            ink_black=0.0,
            ink_cyan=0.0,
            ink_magenta=0.0,
            ink_yellow=0.0,
            timestamp=datetime.now(IST)
        )
        db.add(new_log)
        db.commit()

        await update.message.reply_text(
            f"✅ <b>Refill Successful!</b>\n"
            f"Added {paper_added} sheets to {m_id}.\n"
            f"New Total Paper: {status.paper_count}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error logging refill: {e}")
        await update.message.reply_text("❌ Failed to update machine status.")
    finally:
        db.close()


async def toggle_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if len(context.args) < 2: return await update.message.reply_text("Usage: /togglereport [franchise_id] [on|off]")
    f_id, status = context.args[0], context.args[1].lower()
    db: Session = SessionLocal()
    try:
        franchise = db.query(Franchise).filter_by(id=int(f_id)).first()
        if not franchise: return await update.message.reply_text("❌ Franchise not found.")
        franchise.daily_report_enabled = (status == "on")
        db.commit()
        await update.message.reply_text(f"📧 Daily reports for <b>{franchise.name}</b> set to: {status.upper()}", parse_mode="HTML")
    finally: db.close()

async def notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    if not context.args: return await update.message.reply_text("Usage: /notify [message to send]")
    message = " ".join(context.args)
    db: Session = SessionLocal()
    try:
        notif = Notification(message=message, created_at=datetime.utcnow(), is_read=False, is_dismissed=False)
        db.add(notif)
        db.commit()
        await update.message.reply_text("📢 Global Admin Notification created successfully.")
    finally: db.close()

async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Overview of today's transactions"""
    if not is_authorized(update): return
    db: Session = SessionLocal()
    try:
        start_of_day = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)

        # 🚀 FIX: Join with Session to access total_pages and calculate sheets
        # We query the Session model because that's where the page/duplex data lives
        results = db.query(
            func.sum(TransactionLog.amount).label("total_rev"),
            func.sum(models.Session.total_pages).label("total_pages"),
            func.count(TransactionLog.id).label("total_txns")
        ).outerjoin(models.Session, TransactionLog.session_id == models.Session.id)\
         .filter(
            TransactionLog.created_at >= start_of_day,
            TransactionLog.payment_status == "SUCCESS"
        ).first()

        revenue = results.total_rev or 0.0
        pages = results.total_pages or 0
        txns = results.total_txns or 0

        msg = (
            f"📈 <b>Today's Live Overview</b>\n\n"
            f"💰 Revenue: <b>Rs. {revenue:.2f}</b>\n"
            f"📄 Pages Printed: {pages}\n"
            f"✅ Successful Prints: {txns}"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
    finally:
        db.close()


async def customers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    db: Session = SessionLocal()
    try:
        customers = db.query(Customer).order_by(Customer.id.desc()).limit(10).all()
        if not customers: return await update.message.reply_text("No customers found.")
        msg = "👥 <b>10 Latest App Customers:</b>\n\n"
        for c in customers:
            status = "✅" if c.is_verified else "❌"
            msg += f"{status} {c.name or 'Unknown'} ({c.phone or 'No Phone'})\n"
        await update.message.reply_text(msg, parse_mode="HTML")
    finally: db.close()

# ==========================================
# 5) BACKGROUND RUNNER (WEBHOOK MODE)
# ==========================================
# Make application global so the webhook router can access it
application = None

async def start_telegram_bot():
    global application
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.info("⚠️ Telegram Bot Token missing. Bot disabled.")
        return

    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Register all commands
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("enable", enable_cmd))
    application.add_handler(CommandHandler("disable", disable_cmd))
    application.add_handler(CommandHandler("adrefresh", ad_refresh_cmd))
    application.add_handler(CommandHandler("power", power_cmd))
    application.add_handler(CommandHandler("refill", refill_cmd))
    application.add_handler(CommandHandler("togglereport", toggle_report_cmd))
    application.add_handler(CommandHandler("notify", notify_cmd))
    application.add_handler(CommandHandler("today", today_cmd))
    application.add_handler(CommandHandler("customers", customers_cmd))

    await application.initialize()
    await application.start()

    # 🚀 Polling is REMOVED. The bot is now waiting for Webhooks!
    logger.info("✅ Telegram Admin Bot initialized for Webhooks.")
