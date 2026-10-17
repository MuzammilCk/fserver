from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import asyncio
import logging
from app.routers import telegram_webhook
from app.database import engine, get_db
from app import models
from app.routers import (
    admin, auth, users, machines, franchises, sessions,
    payments, transaction_logs, machine_status, franchise_stats, 
    earnings, refill_logs, notifications, phonepe_callback, 
    payment_success, toner, history, cloud_jobs, app_auth
)
from app.config import settings
from app.tasks import start_scheduler, scheduler
from app.services import websocket_service

from app.mqtt_listener import start_mqtt_listener

# 🚀 IMPORT 'application' to handle clean shutdown
from app.services.telegram_service import start_telegram_bot, send_telegram_alert, application

# Use Uvicorn's native logger so logs bypass Python's buffer and show instantly
logger = logging.getLogger("uvicorn.error")

# =======================================================================
# 🚀 MODERN LIFESPAN (Startup & Shutdown)
# =======================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Code here runs ON STARTUP ---
    logger.info("🚀 Initializing Database...")
    models.Base.metadata.create_all(bind=engine)
    
    logger.info("⏱️ Starting background scheduler...")
    start_scheduler()
    
    logger.info("📡 Starting background MQTT Listener...")
    start_mqtt_listener()
    
    logger.info("🚀 Initializing Telegram Bot (Webhook Mode)...")
    asyncio.create_task(start_telegram_bot())
    await send_telegram_alert("🟢 <b>HyNa Server Started!</b> Telegram Bot is online.")
    
    yield # Hand control back to FastAPI to run the web server
    
    # --- Code here runs ON SHUTDOWN ---
    logger.info("🛑 Shutting down scheduler...")
    if scheduler.running:
        scheduler.shutdown()
        
    await send_telegram_alert("🔴 <b>HyNa Server Shutting Down!</b>")
    
    logger.info("🛑 Shutting down Telegram Bot...")
    if application:
        await application.stop()
        await application.shutdown()

# =======================================================================
# 🌐 FASTAPI APP INIT (Docs Enabled!)
# =======================================================================
# Removing docs_url=None enables the Swagger UI at /docs and ReDoc at /redoc
app = FastAPI(
    title="Print Vending Management System",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,
    #docs_url="/fserver/docs",
    redoc_url=None,
    #redoc_url="/fserver/redoc",
    openapi_url=None,
    #openapi_url="/fserver/openapi.json"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =======================================================================
# 🛣️ ROUTERS
# =======================================================================
app.include_router(auth.router, prefix="/fserver/auth", tags=["auth"])
app.include_router(users.router, prefix="/fserver/users", tags=["users"])
app.include_router(machines.router, prefix="/fserver/machines", tags=["machines"])
app.include_router(franchises.router, prefix="/fserver/franchises", tags=["franchises"])
app.include_router(sessions.router, prefix="/fserver/sessions", tags=["sessions"])
app.include_router(payments.router, prefix="/fserver/payments", tags=["payments"])
app.include_router(machine_status.router)
app.include_router(franchise_stats.router)
app.include_router(earnings.router, prefix="/fserver/earnings", tags=["earnings"])
app.include_router(refill_logs.router, prefix="/fserver")
app.include_router(admin.router, prefix="/fserver/admin", tags=["admin"])
app.include_router(toner.router)
app.include_router(notifications.router, prefix="/fserver", tags=["Notifications"])
app.include_router(phonepe_callback.router)
app.include_router(payment_success.router, prefix="/fserver")
app.include_router(transaction_logs.router)
app.include_router(websocket_service.router)
app.include_router(history.router)
app.include_router(cloud_jobs.router, prefix="/fserver/cloud-jobs", tags=["cloud_jobs"])
app.include_router(app_auth.router, prefix="/fserver/app-auth", tags=["app_auth"])
app.include_router(telegram_webhook.router, prefix="/fserver")

# =======================================================================
# 📂 STATIC FILES & TEMPLATES
# =======================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

app.mount("/fserver/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "static/upload-ui")
app.mount("/assets", StaticFiles(directory=BASE_DIR / "static/upload-ui/assets"), name="upload_assets")
#app.mount("/fserver/static/upload-ui/assets", StaticFiles(directory=BASE_DIR / "static/upload-ui/assets"), name="upload_assets_prefixed")
upload_dir = Path(settings.UPLOAD_DIR)
upload_dir.mkdir(exist_ok=True)
app.mount("/fserver/files", StaticFiles(directory=upload_dir), name="files")
# =======================================================================
# 🌐 ENDPOINTS
# =======================================================================
@app.get("/")
async def root():
    return {"message": "Print Vending Management System v2.0"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}

# ✅ LOCKED UPLOAD UI
@app.get("/fserver/upload/{session_id}")
async def serve_upload_ui(request: Request, session_id: str, db: Session = Depends(get_db)):
    session = db.query(models.Session).filter(models.Session.id == session_id).first()
    
    if not session:
        return JSONResponse(status_code=404, content={"message": "Session not found"})
        
    if session.status not in ["created", "uploaded", "payment_pending"]:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "This session has expired or is currently in use."
        })
        
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "session_id": session_id,
            "base_url": settings.BASE_URL
        }
    )

@app.exception_handler(500)
async def server_error_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=500,
        content={"message": "Internal server error"}
    )


@app.get("/assets/images/{image_name}")
async def get_kiosk_image(image_name: str):
    image_path = BASE_DIR / "static" / "upload-ui" / "assets" / "images" / image_name
    if image_path.is_file():
        return FileResponse(image_path)
    return JSONResponse(status_code=404, content={"message": "Image not found"})
