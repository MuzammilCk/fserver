from fastapi import APIRouter, Request, HTTPException
from telegram import Update
import logging

# 🚀 FIX: Import the whole module so we reference the live 'application' object
from app.services import telegram_service

logger = logging.getLogger("uvicorn.error")
router = APIRouter(tags=["telegram_webhook"])

WEBHOOK_SECRET = "achangamachangapichanga"

@router.post(f"/telegram-webhook/{WEBHOOK_SECRET}")
async def telegram_webhook(request: Request):
    # Check if the bot initialized properly on startup
    if not telegram_service.application:
        logger.error("❌ Webhook hit, but Telegram application is None!")
        raise HTTPException(status_code=503, detail="Bot not initialized")

    try:
        json_data = await request.json()
        update = Update.de_json(json_data, telegram_service.application.bot)
        await telegram_service.application.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        # 🚀 FIX: Print the ACTUAL error to the console so we aren't blind
        logger.error(f"❌ Telegram Webhook Error: {e}")
        raise HTTPException(status_code=400, detail="Invalid request")
