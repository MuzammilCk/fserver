import json
import os
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
from sqlalchemy.orm import Session
from datetime import datetime
from app.database import SessionLocal
from app.models import Machine, User, MachineStatus, AutoNotification, Session as SessionModel, Franchise
from app.services.fcm_service import send_push_notification
from app.services.email_service import send_email_alert
import asyncio
from app.services.telegram_service import send_telegram_alert
import logging

# 🚀 Import settings for MQTT credentials
from app.config import settings

# 🚀 Use Uvicorn's native logger to ensure immediate terminal output
logger = logging.getLogger("uvicorn.error")

MQTT_BROKER = settings.MQTT_BROKER
MQTT_PORT = settings.MQTT_PORT
MQTT_USERNAME = settings.MQTT_USERNAME
MQTT_PASSWORD = settings.MQTT_PASSWORD

def fire_mqtt_alert(message: str):
    """Safely fires an async Telegram alert from a synchronous MQTT callback."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_telegram_alert(message))
    except RuntimeError:
        asyncio.run(send_telegram_alert(message))

# =======================================================================
# 1) MQTT PUBLISHER (Send commands TO the Kiosk)
# =======================================================================
def send_print_job(machine_id: str, session_id: str):
    topic = f"kiosks/{machine_id}/print_jobs"
    payload = json.dumps({"action": "start_print", "session_id": session_id})
    try:
        publish.single(
            topic,
            payload=payload,
            hostname=MQTT_BROKER,
            port=MQTT_PORT,
            qos=1,
            retain=False,
            client_id="fastapi_backend_publisher",
            auth={
                "username": MQTT_USERNAME,
                "password": MQTT_PASSWORD
            }
        )
    except Exception as e:
        logger.error(f"❌ Failed to publish to MQTT: {e}")

# =======================================================================
# 2) ROUTINE HEARTBEAT LOGIC (Online/Offline & Paper Empty)
# =======================================================================
def handle_status_update(machine_id: str, data: dict):
    db = SessionLocal()
    try:
        status_record = db.query(MachineStatus).filter(MachineStatus.machine_id == machine_id).first()
        if not status_record:
            status_record = MachineStatus(machine_id=machine_id)
            db.add(status_record)

        raw_online = data.get("is_online")
        if raw_online is None:
            raw_online = data.get("online")

        was_online = status_record.is_online
        status_record.is_online = bool(raw_online)
        status_record.last_update = datetime.utcnow()

        status_text = data.get("status", "Ready")
        db.commit()

        # 🔌 TERMINAL & TELEGRAM: Machine Connected
        if was_online is not True and status_record.is_online is True:
            logger.info(f"🔌 {machine_id} connected and is now ONLINE.")
            fire_mqtt_alert(f"🔌 <b>Machine Connected:</b> Kiosk <code>{machine_id}</code> is now ONLINE.")

        # 📱 1. FCM, TELEGRAM & TERMINAL: Machine went completely Offline
        if was_online is True and status_record.is_online is False:
            logger.warning(f"⚠️ {machine_id} disconnected and went OFFLINE.")
            fire_mqtt_alert(f"⚠️ <b>Machine Disconnected:</b> Kiosk <code>{machine_id}</code> went OFFLINE.")

            new_alert = AutoNotification(machine_id=machine_id, message=f"Machine {machine_id} is Offline")
            db.add(new_alert)
            db.commit()

            admins = db.query(User).filter(User.fcm_token.isnot(None)).all()
            for admin in admins:
                send_push_notification(
                    token=admin.fcm_token,
                    title=f"⚠️ Kiosk Offline",
                    body=f"Machine {machine_id} has disconnected.",
                    data={"machine_id": machine_id}
                )

        # 📧 + 📱 2. PHYSICAL HARDWARE ALERTS (Idle Paper Empty)
        if status_text in ["Paper Empty", "Printer Error / Jam", "Cover Open"]:
            machine = db.query(Machine).filter_by(id=machine_id).first()
            alert_emails = [os.getenv("ADMIN_EMAIL", "otp.hyna@gmail.com")]

            if machine and machine.is_active:
                machine.is_active = False
                db.commit()
                logger.error(f"🚨 {machine_id} reported hardware fault: {status_text}. Machine disabled.")

                if machine.franchise_id:
                    franchise_users = db.query(User).filter(User.franchise_id == machine.franchise_id).all()
                    for f_user in franchise_users:
                        if f_user.email:
                            alert_emails.append(f_user.email)

                send_email_alert(
                    subject=f"🚨 Kiosk Alert: {status_text}",
                    body=f"Machine {machine_id} has reported a physical hardware issue: {status_text}.\n\nThe machine has been automatically disabled. Please inspect the kiosk.",
                    to_emails=alert_emails
                )

                new_alert = AutoNotification(machine_id=machine_id, message=f"Hardware Alert: {status_text}")
                db.add(new_alert)
                db.commit()

                admins = db.query(User).filter(User.fcm_token.isnot(None)).all()
                for admin in admins:
                    send_push_notification(
                        token=admin.fcm_token,
                        title=f"⚠️ Hardware Alert: {machine_id}",
                        body=status_text,
                        data={"machine_id": machine_id}
                    )

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error in handle_status_update: {e}")
    finally:
        db.close()

# =======================================================================
# 3) TRANSACTION LOGIC (Listen for Print Success/Failed)
# =======================================================================
def handle_print_feedback(payload: dict):
    action = payload.get("action")
    session_id = payload.get("session_id")
    machine_id = payload.get("machine_id")

    if not action or not session_id:
        return

    db = SessionLocal()
    try:
        from app.routers.sessions import process_print_accounting

        if action == "print_success":
            session = db.query(SessionModel).filter_by(id=session_id).first()
            if session and session.status != "completed":
                process_print_accounting(db, session)
                session.status = "completed"
                db.commit()
                logger.info(f"✅ Job {session_id} printed perfectly. Money added to earnings!")

                # 🚀 NEW: Successful Transaction Telegram Alert
                fire_mqtt_alert(f"💰 <b>New Payment & Print Success!</b>\nKiosk: <code>{machine_id}</code>\nPages Printed: {session.total_pages}")

        elif action == "print_failed":
            reason = payload.get("reason", "Unknown hardware error")
            session = db.query(SessionModel).filter_by(id=session_id).first()
            if session and session.status != "failed_hardware_fault":
                session.status = "failed_hardware_fault"
                machine = db.query(Machine).filter_by(id=machine_id).first()
                alert_emails = [os.getenv("ADMIN_EMAIL", "otp.hyna@gmail.com")]

                if machine:
                    machine.is_active = False
                    db.commit()
                    logger.error(f"🚨 Print failed on {machine_id}. Reason: {reason}. Machine disabled.")

                    if machine.franchise_id:
                        franchise_users = db.query(User).filter(User.franchise_id == machine.franchise_id).all()
                        for f_user in franchise_users:
                            if f_user.email:
                                alert_emails.append(f_user.email)

                    send_email_alert(
                        subject=f"🚨 Print Failed: {machine_id}",
                        body=f"Session {session_id} failed to print.\nError: {reason}\n\nMachine is disabled.",
                        to_emails=alert_emails
                    )

                    new_alert = AutoNotification(machine_id=machine_id, message=f"Print Failed: {reason}")
                    db.add(new_alert)
                    db.commit()

                    admins = db.query(User).filter(User.fcm_token.isnot(None)).all()
                    for admin in admins:
                        send_push_notification(
                            token=admin.fcm_token,
                            title=f"🚨 Print Failed: {machine_id}",
                            body=f"Error: {reason}",
                            data={"machine_id": machine_id}
                        )

    except Exception as e:
        logger.error(f"❌ Error in handle_print_feedback: {e}")
    finally:
        db.close()

# =======================================================================
# 4) MAIN MQTT LISTENER ROUTER
# =======================================================================
def on_connect(client, userdata, flags, reason_code, properties):
    # 🚀 v2 requires 5 arguments including reason_code and properties
    if reason_code == 0:
        logger.info(f"✅ Master MQTT Listener Connected.")
        client.subscribe("kiosks/+/status")
        client.subscribe("server/commands")
    else:
        logger.error(f"❌ Master MQTT Listener: Connection failed with code {reason_code}")

def on_message(client, userdata, msg):
    try:
        topic = msg.topic
        payload = json.loads(msg.payload.decode())
        if topic.endswith("/status"):
            handle_status_update(topic.split('/')[1], payload)
        elif topic == "server/commands":
            handle_print_feedback(payload)
    except Exception as e:
        logger.error(f"❌ MQTT_Listener Parse Error: {e}")

def start_mqtt_listener():
    # 🚀 Inject CallbackAPIVersion.VERSION2 here
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fastapi_master_listener")

    client.username_pw_set(
        MQTT_USERNAME,
        MQTT_PASSWORD
    )

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        logger.info("🎧 Master MQTT Background Listener initialized.")
    except Exception as e:
        logger.error(f"❌ Failed to start Master MQTT Listener: {e}")
