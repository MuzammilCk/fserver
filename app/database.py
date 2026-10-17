# app/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from app.config import settings

# 1. USE SQLITE DIRECTLY
# This creates a file named 'printvending.db' in your project folder
DB_URL = "sqlite:///./printvending.db"

# 2. CREATE ENGINE (SQLite specific settings)
engine = create_engine(
    DB_URL, 
    connect_args={"check_same_thread": False},  # Critical for SQLite in FastAPI
    pool_pre_ping=True, 
    pool_recycle=3600
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
