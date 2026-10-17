# app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from pathlib import Path
import os

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

print("📂 Loading .env from:", env_path)

class Settings(BaseSettings):
    # --- DATABASE (Given defaults so it won't crash without Docker) ---
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "user"
    POSTGRES_PASSWORD: str = "password"
    POSTGRES_DB: str = "printvending"

    # --- SECURITY ---
    SECRET_KEY: str = "your-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # --- APP SETTINGS ---
    BASE_URL: str = "http://localhost:8000"
    DEFAULT_AD_URL: str = "/static/default-ad.jpg"
    UPLOAD_DIR: str = "uploaded_files"


    RAZORPAY_KEY_ID: str
    RAZORPAY_KEY_SECRET: str
    RAZORPAY_WEBHOOK_SECRET: str = "placeholder_secret"

    # --- MQTT (Disabled/Optional) ---
    MQTT_BROKER: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str = "admin"
    MQTT_PASSWORD: str = "mqttpassword"

    # --- PHONEPE ---
    PHONEPE_CLIENT_ID: str = "dummy_id"
    PHONEPE_CLIENT_SECRET: str = "dummy_secret"
    PHONEPE_CLIENT_VERSION: int = 1
    PHONEPE_ENV: str = "SANDBOX"
    PHONEPE_MERCHANT_ID: str = "dummy_merchant"
    PHONEPE_WEBHOOK_USERNAME: str = "user"
    PHONEPE_WEBHOOK_PASSWORD: str = "pass"

    # --- INITIAL SETUP ---
    FIRST_SUPERUSER: str = "admin@example.com"
    FIRST_SUPERUSER_PASSWORD: str = "admin"
    INTERNAL_API_KEY: str = "secret"
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    ADMIN_EMAIL: str = ""
    # --- RECAPTCHA ---
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_ALLOWED_CHAT_IDS: str | None = None

    ENABLE_DOCS_LOGIN: bool = True

    model_config = SettingsConfigDict(
        env_file=str(env_path),
        extra="ignore"
    )

settings = Settings()
