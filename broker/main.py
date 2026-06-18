from fastapi import FastAPI, HTTPException, Depends, status, File, UploadFile, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, func, LargeBinary, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
from pydantic import BaseModel
import jwt
import os
import threading
import time
import base64
import tempfile
import logging
import uuid
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional, List
import requests
import json
import io
import csv
import re
from collections import Counter
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://asx_user:asx_password_dev@db:5432/asx")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# JWT Configuration
SECRET_KEY = "broker-secret-key-change-in-production"
ALGORITHM = "HS256"

# FastAPI App
app = FastAPI(title="Inbox Intelligence API")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database Models
class BrokerUser(Base):
    __tablename__ = "broker_users"
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    email = Column(String(255), unique=True, index=True)
    username = Column(String(100))
    password = Column(String(255))
    profile_phone = Column(String(30), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class BrokerCustomer(Base):
    __tablename__ = "broker_customers"
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    name = Column(String(255))
    email = Column(String(255))
    phone = Column(String(20))
    company = Column(String(255))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class BrokerMeeting(Base):
    __tablename__ = "broker_meetings"
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    customer_id = Column(Integer)
    title = Column(String(255))
    description = Column(Text)
    scheduled_at = Column(DateTime)
    duration_minutes = Column(Integer)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class BrokerCSVImport(Base):
    __tablename__ = "broker_csv_imports"
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    filename = Column(String(255))
    status = Column(String(50))  # pending, processing, completed, failed
    imported_records = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class BrokerCalendarIntegration(Base):
    __tablename__ = "broker_calendar_integrations"
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True, unique=True)
    calendar_type = Column(String(50))  # google, outlook
    access_token = Column(Text)  # Encrypted in production
    refresh_token = Column(Text)
    calendar_id = Column(String(255))  # Google calendar ID or Outlook mailbox ID
    email = Column(String(255))  # Broker's calendar email
    is_enabled = Column(Boolean, default=False)
    auto_sync = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class BrokerCalendarSync(Base):
    __tablename__ = "broker_calendar_syncs"
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    meeting_id = Column(Integer, index=True)
    calendar_event_id = Column(String(255))  # Google event ID or Outlook event ID
    calendar_type = Column(String(50))  # google, outlook
    synced_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class BrokerVoiceRecording(Base):
    """Individual voice recordings from broker"""
    __tablename__ = "broker_voice_recordings"
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    customer_id = Column(Integer, nullable=True)
    meeting_id = Column(Integer, nullable=True)
    transcript = Column(Text)
    customer_name = Column(String(255), nullable=True)
    discussion_summary = Column(Text)
    next_meeting = Column(String(255), nullable=True)
    action_items = Column(Text)  # JSON string
    key_points = Column(Text)  # JSON string
    follow_ups = Column(Text)
    status = Column(String(50), default='pending')
    priority = Column(String(20), default='medium')
    category = Column(String(50), nullable=True)
    subcategory = Column(String(100), nullable=True)
    sentiment = Column(String(20), nullable=True)
    tags = Column(Text, nullable=True)  # JSON array of strings
    entities = Column(Text, nullable=True)  # JSON object
    reminder_sent_at = Column(DateTime, nullable=True)  # when daily reminder was last sent
    recorded_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

class BrokerBulkUpload(Base):
    """Bulk CSV uploads separate from voice recordings"""
    __tablename__ = "broker_bulk_uploads"
    
    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    filename = Column(String(255))
    status = Column(String(50))  # pending, processing, completed, failed
    imported_records = Column(Integer, default=0)
    upload_type = Column(String(50))  # csv_import, bulk_upload
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class WhatsAppVoiceJob(Base):
    __tablename__ = "whatsapp_voice_jobs"

    id = Column(Integer, primary_key=True, index=True)
    from_phone = Column(String(30), index=True)
    media_id = Column(String(255), nullable=True)
    audio_mime_type = Column(String(100), nullable=True)
    audio_data = Column(LargeBinary, nullable=True)
    message_type = Column(String(10), default="audio")
    text_content = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    extracted_data = Column(Text, nullable=True)
    reply_text = Column(Text, nullable=True)
    status = Column(String(20), default="pending")
    replied = Column(Boolean, default=False)
    wa_message_id = Column(String(255), nullable=True)
    broker_id = Column(String(50), index=True)
    received_phone_number_id = Column(String(100), nullable=True)
    auto_meeting_id = Column(Integer, nullable=True)
    error_msg = Column(Text, nullable=True)
    wa_replied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    category = Column(String(50), nullable=True)

class BrokerProfile(Base):
    __tablename__ = "broker_profiles"

    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True, unique=True)
    calendar_email = Column(String(255), nullable=True)
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(Integer, default=587)
    smtp_user = Column(String(255), nullable=True)
    smtp_password = Column(String(512), nullable=True)
    smtp_from = Column(String(255), nullable=True)
    whatsapp_from_phone = Column(String(30), nullable=True)
    setup_complete = Column(Boolean, default=False)
    verified = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class BrokerWhatsAppNumber(Base):
    __tablename__ = "broker_whatsapp_numbers"

    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    phone_number_id = Column(String(100), index=True, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class BrokerCustomerPhone(Base):
    __tablename__ = "broker_customer_phones"

    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    customer_phone = Column(String(30), index=True)
    customer_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('broker_id', 'customer_phone', name='uq_broker_customer_phone'),
    )

class BrokerTelegramRecipient(Base):
    """Telegram chat IDs to receive broker notifications."""
    __tablename__ = "broker_telegram_recipients"

    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    chat_id = Column(String(50))
    name = Column(String(255), nullable=True)  # friendly label e.g. "@akspatbot"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('broker_id', 'chat_id', name='uq_broker_telegram_chat'),
    )


class BrokerTelegramState(Base):
    """Per-chat command state shared between broker and ASX notifications."""
    __tablename__ = "broker_telegram_state"

    id = Column(Integer, primary_key=True, index=True)
    broker_id = Column(String(50), index=True)
    chat_id = Column(String(50), index=True)
    snoozed_until = Column(DateTime, nullable=True)
    last_ack_at = Column(DateTime, nullable=True)
    last_command = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('broker_id', 'chat_id', name='uq_broker_telegram_state_chat'),
    )

# faster-whisper model loader (4-8x faster than openai-whisper on CPU)
_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logging.info("Loading faster-whisper 'small' model...")
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
        logging.info("faster-whisper 'small' model loaded.")
    return _whisper_model

def _run_db_migrations():
    migrations = [
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS auto_meeting_id INTEGER",
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS broker_id VARCHAR(50)",
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS message_type VARCHAR(10) DEFAULT 'audio'",
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS text_content TEXT",
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS received_phone_number_id VARCHAR(100)",
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS replied BOOLEAN DEFAULT FALSE",
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS wa_replied_at TIMESTAMP",
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS error_msg TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS uix_wa_num_phone_number_id ON broker_whatsapp_numbers(phone_number_id)",
        """CREATE TABLE IF NOT EXISTS broker_customer_phones (
            id SERIAL PRIMARY KEY,
            broker_id VARCHAR(50) NOT NULL,
            customer_phone VARCHAR(30) NOT NULL,
            customer_name VARCHAR(255),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_broker_customer_phone ON broker_customer_phones(customer_phone)",
        "CREATE INDEX IF NOT EXISTS idx_broker_id_customer ON broker_customer_phones(broker_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_customer_phone ON broker_customer_phones(broker_id, customer_phone)",
        "ALTER TABLE broker_profiles ADD COLUMN IF NOT EXISTS smtp_host VARCHAR(255)",
        "ALTER TABLE broker_profiles ADD COLUMN IF NOT EXISTS smtp_port INTEGER DEFAULT 587",
        "ALTER TABLE broker_profiles ADD COLUMN IF NOT EXISTS smtp_user VARCHAR(255)",
        "ALTER TABLE broker_profiles ADD COLUMN IF NOT EXISTS smtp_password VARCHAR(512)",
        "ALTER TABLE broker_profiles ADD COLUMN IF NOT EXISTS smtp_from VARCHAR(255)",
        "ALTER TABLE broker_profiles ADD COLUMN IF NOT EXISTS whatsapp_from_phone VARCHAR(30)",
        "ALTER TABLE broker_profiles ADD COLUMN IF NOT EXISTS setup_complete BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE broker_profiles ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE broker_users ADD COLUMN IF NOT EXISTS profile_phone VARCHAR(30)",
        "ALTER TABLE broker_voice_recordings ADD COLUMN IF NOT EXISTS category VARCHAR(50)",
        "ALTER TABLE broker_voice_recordings ADD COLUMN IF NOT EXISTS subcategory VARCHAR(100)",
        "ALTER TABLE broker_voice_recordings ADD COLUMN IF NOT EXISTS sentiment VARCHAR(20)",
        "ALTER TABLE broker_voice_recordings ADD COLUMN IF NOT EXISTS tags TEXT",
        "ALTER TABLE broker_voice_recordings ADD COLUMN IF NOT EXISTS entities TEXT",
        "ALTER TABLE whatsapp_voice_jobs ADD COLUMN IF NOT EXISTS category VARCHAR(50)",
        """CREATE TABLE IF NOT EXISTS broker_telegram_recipients (
            id SERIAL PRIMARY KEY,
            broker_id VARCHAR(50) NOT NULL,
            chat_id VARCHAR(50) NOT NULL,
            name VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_broker_telegram ON broker_telegram_recipients(broker_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_telegram_chat ON broker_telegram_recipients(broker_id, chat_id)",
        """CREATE TABLE IF NOT EXISTS broker_telegram_state (
            id SERIAL PRIMARY KEY,
            broker_id VARCHAR(50) NOT NULL,
            chat_id VARCHAR(50) NOT NULL,
            snoozed_until TIMESTAMP NULL,
            last_ack_at TIMESTAMP NULL,
            last_command VARCHAR(50) NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(broker_id, chat_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_broker_telegram_state ON broker_telegram_state(broker_id, chat_id)",
        "ALTER TABLE broker_voice_recordings ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMP",
    ]
    try:
        with engine.connect() as conn:
            for sql in migrations:
                try:
                    conn.execute(text(sql))
                except Exception as e:
                    logging.warning(f"Migration skipped: {e}")
            conn.commit()
    except Exception as e:
        logging.warning(f"Migration block failed: {e}")

@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    _run_db_migrations()
    try:
        get_whisper_model()
    except Exception as e:
        logging.warning(f"Whisper pre-load failed (will retry on first request): {e}")
    _start_whatsapp_worker()
    _check_whatsapp_health()
    _configure_telegram_webhook()

# Pydantic Models
class BrokerUserCreate(BaseModel):
    email: str
    username: str
    password: str
    broker_id: Optional[str] = None  # auto-generated from email

class BrokerUserLogin(BaseModel):
    identifier: str   # email or broker_id
    password: str

class BrokerCustomerCreate(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None

class BrokerCustomerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    notes: Optional[str] = None

class BrokerMeetingCreate(BaseModel):
    customer_id: int
    title: str
    description: Optional[str] = None
    scheduled_at: datetime
    duration_minutes: int
    notes: Optional[str] = None

class BrokerMeetingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    notes: Optional[str] = None

class TranscriptionRequest(BaseModel):
    audio_data: str  # base64 encoded audio data
    audio_format: str = "webm"

class ExtractionRequest(BaseModel):
    transcript: str
    meeting_id: Optional[int] = None

class ExtractedData(BaseModel):
    action_items: list
    key_points: list
    follow_ups: str
    customer_name: Optional[str] = None
    discussion_summary: Optional[str] = None
    next_meeting: Optional[str] = None

class VoiceRecordingResponse(BaseModel):
    id: int
    broker_id: str
    customer_name: Optional[str]
    discussion_summary: str
    next_meeting: Optional[str]
    action_items: list
    key_points: list
    follow_ups: str
    recorded_at: datetime
    
    class Config:
        from_attributes = True

class BulkUploadResponse(BaseModel):
    id: int
    filename: str
    status: str
    imported_records: int
    upload_type: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str
    broker_id: str

# Phase 4: Advanced Features Models
class CSVImportResponse(BaseModel):
    id: int
    filename: str
    status: str
    imported_records: int
    created_at: datetime

class CSVImportData(BaseModel):
    name: str
    email: str
    phone: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    duration_minutes: Optional[int] = None

class DashboardMetrics(BaseModel):
    total_meetings: int
    total_customers: int
    meetings_this_month: int
    avg_meeting_duration: float
    busiest_day: str
    upcoming_meetings: int

class TranscriptItem(BaseModel):
    id: int
    meeting_id: int
    transcript: str
    created_at: datetime
    action_items: Optional[list] = None
    key_points: Optional[list] = None

class MeetingAnalytics(BaseModel):
    meeting_id: int
    title: str
    duration_minutes: int
    transcript: Optional[str] = None
    action_items: list
    key_points: list
    sentiment: str  # "positive", "neutral", "negative"
    sentiment_score: float
    keyword_frequency: dict
    call_duration_secs: int

# Phase 5: Calendar Sync Models
class CalendarIntegrationSetup(BaseModel):
    calendar_type: str  # "google" or "outlook"
    access_token: str
    refresh_token: str
    calendar_id: str
    email: str

class CalendarIntegrationResponse(BaseModel):
    id: int
    broker_id: str
    calendar_type: str
    email: str
    is_enabled: bool
    auto_sync: bool
    created_at: datetime

class CalendarEventData(BaseModel):
    title: str
    description: str
    start_time: datetime
    end_time: datetime
    calendar_id: str

class MeetingCustomerMatch(BaseModel):
    row_index: int
    customer_name: str
    email: str
    phone: Optional[str] = None
    matched_customer_id: Optional[int] = None
    match_score: float  # 0-100
    match_type: str  # "exact", "fuzzy", "email", "phone", "none"

class ImprovedCSVImportResponse(BaseModel):
    imported_records: int
    matched_customers: int
    new_customers: int
    unmatched_rows: List[MeetingCustomerMatch]
    filename: str
    status: str

# Database Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# JWT Functions
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str, db: Session):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        broker_id: str = payload.get("sub")
        if broker_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return broker_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def get_token_from_request(token: Optional[str] = None, authorization: Optional[str] = None) -> str:
    """
    Extract token from either query parameter or Authorization header.
    Supports both formats:
    - Query: ?token=...
    - Header: Authorization: Bearer <token>
    """
    # Try query parameter first (for backwards compatibility)
    if token:
        return token
    
    # Try Authorization header (Bearer token format)
    if authorization:
        if authorization.startswith("Bearer "):
            return authorization[7:]  # Remove "Bearer " prefix
        else:
            return authorization
    
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")

# Auth Endpoints
@app.get("/health")
async def health():
    return {"status": "ok", "service": "broker-backend"}

@app.get("/health/whatsapp")
async def health_whatsapp():
    """Check WhatsApp connectivity with Meta Graph API"""
    return _whatsapp_health

@app.post("/api/broker/auth/register", response_model=Token)
async def register(user: BrokerUserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(BrokerUser).filter(BrokerUser.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    broker_id = user.broker_id or user.email.split("@")[0]
    
    db_user = BrokerUser(
        broker_id=broker_id,
        email=user.email,
        username=user.username,
        password=user.password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    token = create_access_token({"sub": broker_id})
    return {"access_token": token, "token_type": "bearer", "broker_id": broker_id}

@app.post("/api/broker/auth/login", response_model=Token)
async def login(credentials: BrokerUserLogin, db: Session = Depends(get_db)):
    # Try matching by email first, then by broker_id
    user = db.query(BrokerUser).filter(BrokerUser.email == credentials.identifier).first()
    if not user:
        user = db.query(BrokerUser).filter(BrokerUser.broker_id == credentials.identifier).first()
    if not user or user.password != credentials.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_access_token({"sub": user.broker_id})
    return {"access_token": token, "token_type": "bearer", "broker_id": user.broker_id}

# Customer Endpoints
@app.post("/api/broker/customers")
async def create_customer(
    customer: BrokerCustomerCreate,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    db_customer = BrokerCustomer(
        broker_id=broker_id,
        name=customer.name,
        email=customer.email,
        phone=customer.phone,
        company=customer.company,
        notes=customer.notes
    )
    db.add(db_customer)
    db.commit()
    db.refresh(db_customer)
    return db_customer

@app.get("/api/broker/customers")
async def get_customers(token: str = None, authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    customers = db.query(BrokerCustomer).filter(BrokerCustomer.broker_id == broker_id).all()
    return customers

@app.get("/api/broker/customers/{customer_id}")
async def get_customer(customer_id: int, token: str = None, authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    customer = db.query(BrokerCustomer).filter(
        BrokerCustomer.id == customer_id,
        BrokerCustomer.broker_id == broker_id
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer

@app.put("/api/broker/customers/{customer_id}")
async def update_customer(
    customer_id: int,
    customer: BrokerCustomerUpdate,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    db_customer = db.query(BrokerCustomer).filter(
        BrokerCustomer.id == customer_id,
        BrokerCustomer.broker_id == broker_id
    ).first()
    
    if not db_customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    update_data = customer.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_customer, field, value)
    
    db_customer.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_customer)
    return db_customer

@app.delete("/api/broker/customers/{customer_id}")
async def delete_customer(customer_id: int, token: str = None, authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    db_customer = db.query(BrokerCustomer).filter(
        BrokerCustomer.id == customer_id,
        BrokerCustomer.broker_id == broker_id
    ).first()
    
    if not db_customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    db.delete(db_customer)
    db.commit()
    return {"detail": "Customer deleted"}

# Meeting Endpoints
@app.get("/api/broker/meetings")
async def get_meetings(token: str = None, authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    meetings = db.query(BrokerMeeting).filter(BrokerMeeting.broker_id == broker_id).all()
    return meetings

@app.post("/api/broker/meetings")
async def create_meeting(
    meeting: BrokerMeetingCreate,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    db_meeting = BrokerMeeting(
        broker_id=broker_id,
        customer_id=meeting.customer_id,
        title=meeting.title,
        description=meeting.description,
        scheduled_at=meeting.scheduled_at,
        duration_minutes=meeting.duration_minutes,
        notes=meeting.notes
    )
    db.add(db_meeting)
    db.commit()
    db.refresh(db_meeting)
    return db_meeting

@app.get("/api/broker/meetings/{meeting_id}")
async def get_meeting(meeting_id: int, token: str = None, authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    meeting = db.query(BrokerMeeting).filter(
        BrokerMeeting.id == meeting_id,
        BrokerMeeting.broker_id == broker_id
    ).first()
    
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting

@app.put("/api/broker/meetings/{meeting_id}")
async def update_meeting(
    meeting_id: int,
    meeting: BrokerMeetingUpdate,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    db_meeting = db.query(BrokerMeeting).filter(
        BrokerMeeting.id == meeting_id,
        BrokerMeeting.broker_id == broker_id
    ).first()
    
    if not db_meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    update_data = meeting.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_meeting, field, value)
    
    db.commit()
    db.refresh(db_meeting)
    return db_meeting

@app.get("/api/broker/dashboard/today")
async def get_today_meetings(token: str = None, authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    today = datetime.utcnow().date()
    start_of_day = datetime.combine(today, datetime.min.time())
    end_of_day = datetime.combine(today, datetime.max.time())
    
    meetings = db.query(BrokerMeeting).filter(
        BrokerMeeting.broker_id == broker_id,
        BrokerMeeting.scheduled_at >= start_of_day,
        BrokerMeeting.scheduled_at <= end_of_day
    ).all()
    
    return {
        "date": today.isoformat(),
        "meetings_count": len(meetings),
        "meetings": meetings
    }

# AI/ML Endpoints
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://ollama:11434")
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://ollama:11434/v1")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "deepseek-r1:7b")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# n8n / Telegram Configuration
N8N_URL = os.getenv("N8N_URL", "http://broker-n8n:5678")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
ASX_BACKEND_URL = os.getenv("ASX_BACKEND_URL", "http://backend:8000").strip().rstrip("/")
TELEGRAM_ACTION_INGEST_SECRET = os.getenv("TELEGRAM_ACTION_INGEST_SECRET", "").strip()
_TELEGRAM_CHAT_IDS_ENV = [
    cid.strip() for cid in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if cid.strip()
]

_groq_client = None
try:
    from groq import Groq as GroqClient
    if GROQ_API_KEY:
        _groq_client = GroqClient(api_key=GROQ_API_KEY)
        logging.info("Groq client initialized for broker backend")
    else:
        logging.info("GROQ_API_KEY not set — Groq summarization disabled")
except ImportError:
    logging.info("groq SDK not available — Groq summarization disabled")

@app.post("/api/broker/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Transcribe audio using local OpenAI Whisper model (no internet required)"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    import tempfile
    import subprocess
    
    audio_content = await file.read()
    
    # Save uploaded file (webm/ogg/mp4 etc)
    suffix = ".webm"
    if file.filename:
        ext = os.path.splitext(file.filename)[-1]
        if ext:
            suffix = ext
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_content)
        tmp_path = tmp.name
    
    wav_path = tmp_path + ".wav"
    
    try:
        # Convert to 16kHz mono WAV using ffmpeg (handles webm/ogg/mp4/mp3 etc.)
        convert = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_path,
             "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
            capture_output=True,
            timeout=60
        )
        
        if convert.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Audio conversion failed: {convert.stderr.decode(errors='replace')}"
            )
        
        # Run local faster-whisper model (tiny, int8 quantized, ~5-15s on CPU)
        model = get_whisper_model()
        segments, _ = model.transcribe(wav_path, beam_size=1, language="en")
        transcript = " ".join(seg.text.strip() for seg in segments).strip()
        
        if not transcript:
            transcript = "[No speech detected in recording]"
    
    finally:
        for p in [tmp_path, wav_path]:
            try:
                os.unlink(p)
            except:
                pass
    
    return {
        "transcript": transcript,
        "audio_file": file.filename,
        "size_bytes": len(audio_content),
        "broker_id": broker_id
    }

@app.post("/api/broker/extract-data")
async def extract_meeting_data(
    request: ExtractionRequest,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Extract structured data from meeting transcript using Groq (primary) with Ollama fallback"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    try:
        extracted = _extract_with_llm(request.transcript)

        # Map the extended Groq keys to the narrower ExtractionRequest response fields
        follow_ups_val = extracted.get("follow_ups", [])
        if isinstance(follow_ups_val, list):
            follow_ups_str = ", ".join(follow_ups_val) if follow_ups_val else extracted.get("follow_ups", "")
        else:
            follow_ups_str = follow_ups_val

        response_extracted = {
            "customer_name": extracted.get("customer_name"),
            "discussion_summary": extracted.get("summary") or extracted.get("discussion_summary"),
            "next_meeting": extracted.get("next_meeting_date") or extracted.get("next_meeting"),
            "action_items": extracted.get("action_items", []),
            "key_points": extracted.get("discussed_topics") or extracted.get("key_points", []),
            "follow_ups": follow_ups_str,
            "context": extracted.get("context", ""),
            "next_best_actions": extracted.get("next_best_actions", []),
            "meeting_suggestion": extracted.get("meeting_suggestion"),
            "urgency": extracted.get("urgency", "medium"),
            "_llm_provider": extracted.get("_llm_provider", "unknown"),
        }
        
        if request.meeting_id:
            meeting = db.query(BrokerMeeting).filter(
                BrokerMeeting.id == request.meeting_id,
                BrokerMeeting.broker_id == broker_id
            ).first()
            if meeting:
                meeting.notes = json.dumps(response_extracted)
                db.commit()
        
        recording = BrokerVoiceRecording(
            broker_id=broker_id,
            meeting_id=request.meeting_id,
            transcript=request.transcript,
            customer_name=response_extracted.get('customer_name'),
            discussion_summary=response_extracted.get('discussion_summary'),
            next_meeting=response_extracted.get('next_meeting'),
            action_items=json.dumps(response_extracted.get('action_items', [])),
            key_points=json.dumps(response_extracted.get('key_points', [])),
            follow_ups=response_extracted.get('follow_ups', ''),
            status='pending',
            priority=extracted.get("urgency", "medium")
        )
        db.add(recording)
        db.commit()
        db.refresh(recording)

        # Fire-and-forget Telegram notification via n8n
        _send_telegram_notification(extracted, source="dashboard", broker_id=broker_id, db=db)

        return {
            "extracted_data": response_extracted,
            "broker_id": broker_id,
            "meeting_id": request.meeting_id,
            "recording_id": recording.id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

@app.post("/api/broker/save-meeting-notes")
async def save_meeting_notes(
    meeting_id: int,
    notes: str,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Save or update meeting notes"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    meeting = db.query(BrokerMeeting).filter(
        BrokerMeeting.id == meeting_id,
        BrokerMeeting.broker_id == broker_id
    ).first()
    
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    meeting.notes = notes
    db.commit()
    db.refresh(meeting)
    
    return {"message": "Notes saved", "meeting_id": meeting_id, "notes": meeting.notes}


# ===== LEADS =====

@app.get("/api/broker/leads")
async def get_leads(
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Return all voice recording leads for the broker"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")

    broker_id = verify_token(extract_token, db)

    query = db.query(BrokerVoiceRecording).filter(BrokerVoiceRecording.broker_id == broker_id)

    if search:
        query = query.filter(
            (BrokerVoiceRecording.customer_name.ilike(f"%{search}%")) |
            (BrokerVoiceRecording.discussion_summary.ilike(f"%{search}%")) |
            (BrokerVoiceRecording.follow_ups.ilike(f"%{search}%"))
        )
    if status_filter and status_filter != 'all':
        query = query.filter(BrokerVoiceRecording.status == status_filter)

    recordings = query.order_by(BrokerVoiceRecording.created_at.desc()).limit(200).all()

    results = []
    for r in recordings:
        try:
            action_items = json.loads(r.action_items) if r.action_items else []
        except Exception:
            action_items = []
        try:
            key_points = json.loads(r.key_points) if r.key_points else []
        except Exception:
            key_points = []

        results.append({
            "id": r.id,
            "customer_name": r.customer_name or "Unknown",
            "discussion_summary": r.discussion_summary or "",
            "next_meeting": r.next_meeting,
            "action_items": action_items,
            "key_points": key_points,
            "follow_ups": r.follow_ups or "",
            "status": r.status or "pending",
            "priority": r.priority or "medium",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return results


@app.patch("/api/broker/leads/{lead_id}")
async def update_lead(
    lead_id: int,
    body: dict,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Update status or priority of a lead"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")

    broker_id = verify_token(extract_token, db)

    recording = db.query(BrokerVoiceRecording).filter(
        BrokerVoiceRecording.id == lead_id,
        BrokerVoiceRecording.broker_id == broker_id
    ).first()

    if not recording:
        raise HTTPException(status_code=404, detail="Lead not found")

    allowed = {"status", "priority", "customer_name", "next_meeting"}
    for field, value in body.items():
        if field in allowed:
            setattr(recording, field, value)

    db.commit()
    db.refresh(recording)
    return {"message": "Lead updated", "id": recording.id}


@app.get("/api/broker/leads/upcoming")
async def get_upcoming_leads(
    days: int = 7,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Return leads with next_meeting within the next N days"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")

    broker_id = verify_token(extract_token, db)

    recordings = db.query(BrokerVoiceRecording).filter(
        BrokerVoiceRecording.broker_id == broker_id,
        BrokerVoiceRecording.next_meeting.isnot(None),
        BrokerVoiceRecording.next_meeting != ''
    ).order_by(BrokerVoiceRecording.created_at.desc()).limit(200).all()

    from datetime import date, timedelta
    today = date.today()
    cutoff = today + timedelta(days=days)

    upcoming = []
    for r in recordings:
        try:
            action_items = json.loads(r.action_items) if r.action_items else []
        except Exception:
            action_items = []

        upcoming.append({
            "id": r.id,
            "customer_name": r.customer_name or "Unknown",
            "discussion_summary": r.discussion_summary or "",
            "next_meeting": r.next_meeting,
            "action_items": action_items,
            "follow_ups": r.follow_ups or "",
            "status": r.status or "pending",
            "priority": r.priority or "medium",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return upcoming


# ===== PHASE 4: ADVANCED FEATURES =====

@app.post("/api/broker/csv/import")
async def import_csv(
    file: UploadFile = File(...),
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Import customers and meetings from CSV file"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    try:
        contents = await file.read()
        text_contents = contents.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(text_contents))
        
        imported_count = 0
        for row in csv_reader:
            if row.get("name") and row.get("email"):
                # Create customer
                customer = BrokerCustomer(
                    broker_id=broker_id,
                    name=row.get("name"),
                    email=row.get("email"),
                    phone=row.get("phone"),
                    company=row.get("company"),
                    notes=f"Imported from CSV: {file.filename}"
                )
                db.add(customer)
                db.flush()
                
                # Create meeting if title provided
                if row.get("title"):
                    meeting = BrokerMeeting(
                        broker_id=broker_id,
                        customer_id=customer.id,
                        title=row.get("title"),
                        description=f"Imported from CSV: {file.filename}",
                        scheduled_at=datetime.utcnow() + timedelta(days=1),
                        duration_minutes=int(row.get("duration_minutes", 60)),
                        notes=""
                    )
                    db.add(meeting)
                
                imported_count += 1
        
        db.commit()
        
        # Log import
        csv_import_log = BrokerCSVImport(
            broker_id=broker_id,
            filename=file.filename,
            status="completed",
            imported_records=imported_count
        )
        db.add(csv_import_log)
        db.commit()
        
        return {
            "message": f"Successfully imported {imported_count} records",
            "imported_records": imported_count,
            "filename": file.filename,
            "status": "completed"
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV import failed: {str(e)}")

@app.get("/api/broker/csv/imports")
async def get_csv_imports(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Get CSV import history"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    imports = db.query(BrokerCSVImport).filter(
        BrokerCSVImport.broker_id == broker_id
    ).order_by(BrokerCSVImport.created_at.desc()).all()
    
    return imports

@app.get("/api/broker/dashboard/metrics")
async def get_dashboard_metrics(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Get comprehensive dashboard metrics including WhatsApp messages and AI summaries"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_meetings = db.query(func.count(BrokerMeeting.id)).filter(
        BrokerMeeting.broker_id == broker_id
    ).scalar() or 0
    
    total_customers = db.query(func.count(BrokerCustomer.id)).filter(
        BrokerCustomer.broker_id == broker_id
    ).scalar() or 0
    
    meetings_this_month = db.query(func.count(BrokerMeeting.id)).filter(
        BrokerMeeting.broker_id == broker_id,
        BrokerMeeting.scheduled_at >= month_start
    ).scalar() or 0
    
    avg_duration = db.query(func.avg(BrokerMeeting.duration_minutes)).filter(
        BrokerMeeting.broker_id == broker_id
    ).scalar() or 0
    
    meetings = db.query(BrokerMeeting).filter(
        BrokerMeeting.broker_id == broker_id
    ).all()
    if meetings:
        day_counts = Counter([m.scheduled_at.strftime("%A") for m in meetings])
        busiest_day = day_counts.most_common(1)[0][0]
    else:
        busiest_day = "N/A"
    
    upcoming = db.query(func.count(BrokerMeeting.id)).filter(
        BrokerMeeting.broker_id == broker_id,
        BrokerMeeting.scheduled_at > now
    ).scalar() or 0

    # WhatsApp / Incoming message stats
    total_messages = db.query(func.count(WhatsAppVoiceJob.id)).filter(
        WhatsAppVoiceJob.broker_id == broker_id
    ).scalar() or 0

    messages_today = db.query(func.count(WhatsAppVoiceJob.id)).filter(
        WhatsAppVoiceJob.broker_id == broker_id,
        WhatsAppVoiceJob.created_at >= today_start
    ).scalar() or 0

    done_messages = db.query(func.count(WhatsAppVoiceJob.id)).filter(
        WhatsAppVoiceJob.broker_id == broker_id,
        WhatsAppVoiceJob.status == "done"
    ).scalar() or 0

    pending_messages = db.query(func.count(WhatsAppVoiceJob.id)).filter(
        WhatsAppVoiceJob.broker_id == broker_id,
        WhatsAppVoiceJob.status.in_(["pending", "processing"])
    ).scalar() or 0

    # Voice recording leads with action items
    open_leads = db.query(func.count(BrokerVoiceRecording.id)).filter(
        BrokerVoiceRecording.broker_id == broker_id,
        BrokerVoiceRecording.status == "pending"
    ).scalar() or 0

    # Recent summaries (last 10 done jobs with extracted data)
    recent_jobs = db.query(WhatsAppVoiceJob).filter(
        WhatsAppVoiceJob.broker_id == broker_id,
        WhatsAppVoiceJob.status == "done",
        WhatsAppVoiceJob.extracted_data.isnot(None)
    ).order_by(WhatsAppVoiceJob.processed_at.desc()).limit(10).all()

    recent_summaries = []
    for j in recent_jobs:
        try:
            ed = json.loads(j.extracted_data) if j.extracted_data else {}
        except Exception:
            ed = {}
        recent_summaries.append({
            "id": j.id,
            "from_phone": j.from_phone,
            "message_type": j.message_type or "audio",
            "customer_name": ed.get("customer_name", ed.get("entities", {}).get("people", ["Unknown"])[0] if ed.get("entities", {}).get("people") else "Unknown"),
            "category": j.category or ed.get("category", "general"),
            "intent": ed.get("intent", ""),
            "summary": ed.get("summary", "")[:400] or ed.get("detailed_summary", {}).get("overview", "")[:400],
            "context": ed.get("context", "")[:200],
            "detailed_summary": ed.get("detailed_summary", {}),
            "entities": ed.get("entities", {}),
            "sentiment": ed.get("sentiment", ""),
            "tags": ed.get("tags", []),
            "next_best_actions": ed.get("next_best_actions", [])[:5],
            "meeting_suggestion": ed.get("meeting_suggestion"),
            "action_items": ed.get("action_items", [])[:5],
            "urgency": ed.get("urgency", "medium"),
            "provider": ed.get("_llm_provider", "unknown"),
            "processed_at": j.processed_at.isoformat() if j.processed_at else None,
        })

    return {
        "total_meetings": total_meetings,
        "total_customers": total_customers,
        "meetings_this_month": meetings_this_month,
        "avg_meeting_duration": round(float(avg_duration), 2),
        "busiest_day": busiest_day,
        "upcoming_meetings": upcoming,
        "total_messages": total_messages,
        "messages_today": messages_today,
        "done_messages": done_messages,
        "pending_messages": pending_messages,
        "open_leads": open_leads,
        "recent_summaries": recent_summaries,
    }

@app.get("/api/broker/dashboard/monthly-stats")
async def get_monthly_stats(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Get monthly statistics for charts"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    meetings = db.query(BrokerMeeting).filter(
        BrokerMeeting.broker_id == broker_id,
        BrokerMeeting.scheduled_at >= (datetime.utcnow() - timedelta(days=90))
    ).all()
    
    stats_by_day = Counter()
    for meeting in meetings:
        day = meeting.scheduled_at.strftime("%Y-%m-%d")
        stats_by_day[day] += 1
    
    data = [
        {"date": day, "meetings": count}
        for day, count in sorted(stats_by_day.items())
    ]
    
    return data

@app.get("/api/broker/dashboard/categories")
async def get_dashboard_categories(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Get category breakdown for the dashboard"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    recordings = db.query(BrokerVoiceRecording).filter(
        BrokerVoiceRecording.broker_id == broker_id,
        BrokerVoiceRecording.category.isnot(None)
    ).all()

    cat_counts = Counter()
    for r in recordings:
        cat_counts[r.category or "general"] += 1

    categories = []
    for cat, icon_config in CATEGORY_CONFIG.items():
        categories.append({
            "category": cat,
            "label": cat.capitalize(),
            "icon": icon_config["icon"],
            "color": icon_config["color"],
            "count": cat_counts.get(cat, 0),
        })

    total = sum(c["count"] for c in categories)
    for c in categories:
        c["pct"] = round(c["count"] / total * 100, 1) if total > 0 else 0

    return {"categories": sorted(categories, key=lambda x: -x["count"]), "total": total}

@app.get("/api/broker/transcripts")
async def get_transcripts(
    search: Optional[str] = None,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Get meeting transcripts with optional search"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    query = db.query(BrokerMeeting).filter(
        BrokerMeeting.broker_id == broker_id,
        BrokerMeeting.notes.isnot(None)
    )
    
    if search:
        query = query.filter(
            (BrokerMeeting.title.ilike(f"%{search}%")) |
            (BrokerMeeting.notes.ilike(f"%{search}%"))
        )
    
    meetings = query.order_by(BrokerMeeting.created_at.desc()).limit(100).all()
    
    transcripts = []
    for meeting in meetings:
        action_items = []
        key_points = []
        
        if meeting.notes and meeting.notes.startswith("{"):
            try:
                data = json.loads(meeting.notes)
                action_items = data.get("action_items", [])
                key_points = data.get("key_points", [])
            except:
                pass
        
        transcripts.append({
            "id": meeting.id,
            "meeting_id": meeting.id,
            "title": meeting.title,
            "transcript": meeting.notes or "No transcript",
            "created_at": meeting.created_at,
            "action_items": action_items,
            "key_points": key_points
        })
    
    return transcripts

@app.get("/api/broker/meetings/{meeting_id}/analytics")
async def get_meeting_analytics(
    meeting_id: int,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Get comprehensive analytics for a meeting"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    meeting = db.query(BrokerMeeting).filter(
        BrokerMeeting.id == meeting_id,
        BrokerMeeting.broker_id == broker_id
    ).first()
    
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    # Parse extracted data
    action_items = []
    key_points = []
    sentiment = "neutral"
    sentiment_score = 0.5
    keyword_frequency = {}
    
    if meeting.notes and meeting.notes.startswith("{"):
        try:
            data = json.loads(meeting.notes)
            action_items = data.get("action_items", [])
            key_points = data.get("key_points", [])
            
            # Simple sentiment analysis
            negative_words = ["problem", "issue", "concern", "fail", "bad", "worst", "angry", "frustrated"]
            positive_words = ["good", "excellent", "great", "success", "happy", "excited", "awesome", "perfect"]
            
            notes_lower = (meeting.notes + meeting.title).lower()
            neg_count = sum(1 for word in negative_words if word in notes_lower)
            pos_count = sum(1 for word in positive_words if word in notes_lower)
            
            if pos_count > neg_count:
                sentiment = "positive"
                sentiment_score = 0.7 + (pos_count * 0.1)
            elif neg_count > pos_count:
                sentiment = "negative"
                sentiment_score = 0.3 - (neg_count * 0.1)
            else:
                sentiment = "neutral"
                sentiment_score = 0.5
            
            # Keyword frequency
            words = re.findall(r'\b\w+\b', notes_lower)
            stopped_words = set(['the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from'])
            filtered_words = [w for w in words if len(w) > 3 and w not in stopped_words]
            keyword_frequency = dict(Counter(filtered_words).most_common(10))
        
        except:
            pass
    
    return {
        "meeting_id": meeting_id,
        "title": meeting.title,
        "duration_minutes": meeting.duration_minutes,
        "transcript": meeting.notes or "No transcript available",
        "action_items": action_items,
        "key_points": key_points,
        "sentiment": sentiment,
        "sentiment_score": round(sentiment_score, 2),
        "keyword_frequency": keyword_frequency,
        "call_duration_secs": meeting.duration_minutes * 60,
        "created_at": meeting.created_at
    }

# ===== PHASE 5: CALENDAR INTEGRATION =====

def fuzzy_match_customer(search_name: str, search_email: str, customers: list) -> tuple:
    """Smart customer matching using name, email, and phone matching."""
    from fuzzywuzzy import fuzz
    
    best_match = None
    best_score = 0
    match_type = "none"
    
    for customer in customers:
        # Exact email match (highest priority)
        if search_email and customer.email and search_email.lower() == customer.email.lower():
            return customer.id, 100, "email"
        
        # Fuzzy name matching
        name_ratio = fuzz.ratio(search_name.lower(), customer.name.lower())
        if name_ratio > best_score and name_ratio >= 80:
            best_score = name_ratio
            best_match = customer.id
            match_type = "fuzzy" if name_ratio < 95 else "exact"
    
    return best_match, best_score, match_type

@app.post("/api/broker/calendar/setup")
async def setup_calendar_integration(
    setup: CalendarIntegrationSetup,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Setup calendar integration (Google Calendar or Outlook)"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    # Check if integration already exists
    existing = db.query(BrokerCalendarIntegration).filter(
        BrokerCalendarIntegration.broker_id == broker_id
    ).first()
    
    if existing:
        # Update existing
        existing.calendar_type = setup.calendar_type
        existing.access_token = setup.access_token
        existing.refresh_token = setup.refresh_token
        existing.calendar_id = setup.calendar_id
        existing.email = setup.email
        existing.is_enabled = True
        db.commit()
        db.refresh(existing)
        return existing
    else:
        # Create new
        integration = BrokerCalendarIntegration(
            broker_id=broker_id,
            calendar_type=setup.calendar_type,
            access_token=setup.access_token,
            refresh_token=setup.refresh_token,
            calendar_id=setup.calendar_id,
            email=setup.email,
            is_enabled=True,
            auto_sync=True
        )
        db.add(integration)
        db.commit()
        db.refresh(integration)
        return integration

@app.get("/api/broker/calendar/status")
async def get_calendar_status(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Get current calendar integration status"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    integration = db.query(BrokerCalendarIntegration).filter(
        BrokerCalendarIntegration.broker_id == broker_id
    ).first()
    
    if not integration:
        return {
            "connected": False,
            "calendar_type": None,
            "email": None,
            "auto_sync": False
        }
    
    # Count synced meetings
    synced = db.query(func.count(BrokerCalendarSync.id)).filter(
        BrokerCalendarSync.broker_id == broker_id
    ).scalar() or 0
    
    return {
        "connected": integration.is_enabled,
        "calendar_type": integration.calendar_type,
        "email": integration.email,
        "auto_sync": integration.auto_sync,
        "synced_meetings": synced
    }

@app.post("/api/broker/calendar/sync-meeting")
async def sync_meeting_to_calendar(
    meeting_id: int,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Sync a specific meeting to broker's calendar"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    # Get meeting
    meeting = db.query(BrokerMeeting).filter(
        BrokerMeeting.id == meeting_id,
        BrokerMeeting.broker_id == broker_id
    ).first()
    
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    
    # Get calendar integration
    integration = db.query(BrokerCalendarIntegration).filter(
        BrokerCalendarIntegration.broker_id == broker_id,
        BrokerCalendarIntegration.is_enabled == True
    ).first()
    
    if not integration:
        raise HTTPException(status_code=400, detail="Calendar not configured")
    
    try:
        # Get customer for meeting details
        customer = db.query(BrokerCustomer).filter(
            BrokerCustomer.id == meeting.customer_id
        ).first()
        
        # Create calendar event data
        end_time = meeting.scheduled_at + timedelta(minutes=meeting.duration_minutes)
        event_description = f"Meeting with {customer.name if customer else 'Customer'}\n"
        if customer:
            event_description += f"Email: {customer.email}\n"
            if customer.phone:
                event_description += f"Phone: {customer.phone}\n"
        
        event_description += f"Notes: {meeting.notes or meeting.description or 'No additional notes'}"
        
        event_data = {
            "title": meeting.title,
            "description": event_description,
            "start_time": meeting.scheduled_at,
            "end_time": end_time,
            "calendar_id": integration.calendar_id
        }
        
        # In production, would call Google Calendar API or Microsoft Graph API
        # For now, we'll simulate the sync
        calendar_event_id = f"{integration.calendar_type}_{meeting.id}_{datetime.utcnow().timestamp()}"
        
        # Check if already synced
        existing_sync = db.query(BrokerCalendarSync).filter(
            BrokerCalendarSync.meeting_id == meeting.id,
            BrokerCalendarSync.broker_id == broker_id
        ).first()
        
        if existing_sync:
            existing_sync.last_updated = datetime.utcnow()
            db.commit()
            return {"message": "Already synced", "event_id": existing_sync.calendar_event_id}
        
        # Create new sync record
        sync_record = BrokerCalendarSync(
            broker_id=broker_id,
            meeting_id=meeting.id,
            calendar_event_id=calendar_event_id,
            calendar_type=integration.calendar_type,
            synced_at=datetime.utcnow()
        )
        db.add(sync_record)
        db.commit()
        
        return {
            "message": "Meeting synced to calendar",
            "event_id": calendar_event_id,
            "meeting_title": meeting.title,
            "calendar_type": integration.calendar_type
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

@app.post("/api/broker/csv/import-smart")
async def import_csv_with_smart_matching(
    file: UploadFile = File(...),
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Import CSV with smart customer matching"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    try:
        contents = await file.read()
        text_contents = contents.decode("utf-8")
        csv_reader = csv.DictReader(io.StringIO(text_contents))
        
        # Get existing customers for matching
        existing_customers = db.query(BrokerCustomer).filter(
            BrokerCustomer.broker_id == broker_id
        ).all()
        
        imported_count = 0
        matched_count = 0
        unmatched_rows = []
        
        for row_idx, row in enumerate(csv_reader, start=2):  # Start at 2 (header is row 1)
            if not row.get("name") or not row.get("email"):
                continue
            
            customer_name = row.get("name")
            customer_email = row.get("email")
            customer_phone = row.get("phone")
            
            # Try to match existing customer
            matched_id, match_score, match_type = fuzzy_match_customer(
                customer_name,
                customer_email,
                existing_customers
            )
            
            if matched_id:
                # Use existing customer
                customer_id = matched_id
                matched_count += 1
                match_info = MeetingCustomerMatch(
                    row_index=row_idx,
                    customer_name=customer_name,
                    email=customer_email,
                    phone=customer_phone,
                    matched_customer_id=customer_id,
                    match_score=match_score,
                    match_type=match_type if match_score >= 80 else "none"
                )
            else:
                # Create new customer
                new_customer = BrokerCustomer(
                    broker_id=broker_id,
                    name=customer_name,
                    email=customer_email,
                    phone=customer_phone,
                    company=row.get("company"),
                    notes=f"Imported from CSV: {file.filename}"
                )
                db.add(new_customer)
                db.flush()
                customer_id = new_customer.id
                match_info = MeetingCustomerMatch(
                    row_index=row_idx,
                    customer_name=customer_name,
                    email=customer_email,
                    phone=customer_phone,
                    matched_customer_id=customer_id,
                    match_score=0,
                    match_type="none"
                )
            
            # Create meeting if title provided
            if row.get("title"):
                meeting = BrokerMeeting(
                    broker_id=broker_id,
                    customer_id=customer_id,
                    title=row.get("title"),
                    description=f"Imported from CSV: {file.filename}",
                    scheduled_at=datetime.utcnow() + timedelta(days=1),
                    duration_minutes=int(row.get("duration_minutes", 60)),
                    notes=""
                )
                db.add(meeting)
            
            imported_count += 1
        
        db.commit()
        
        # Log import
        csv_import_log = BrokerCSVImport(
            broker_id=broker_id,
            filename=file.filename,
            status="completed",
            imported_records=imported_count
        )
        db.add(csv_import_log)
        db.commit()
        
        return ImprovedCSVImportResponse(
            imported_records=imported_count,
            matched_customers=matched_count,
            new_customers=imported_count - matched_count,
            unmatched_rows=unmatched_rows,
            filename=file.filename,
            status="completed"
        )
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV import failed: {str(e)}")

@app.get("/api/broker/meetings/auto-sync")
async def auto_sync_meetings_to_calendar(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Auto-sync all unsynced meetings to calendar"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    # Get calendar integration
    integration = db.query(BrokerCalendarIntegration).filter(
        BrokerCalendarIntegration.broker_id == broker_id,
        BrokerCalendarIntegration.is_enabled == True,
        BrokerCalendarIntegration.auto_sync == True
    ).first()
    
    if not integration:
        return {"synced_count": 0, "message": "Calendar not configured or auto-sync disabled"}
    
    # Get all meetings not yet synced
    synced_meeting_ids = db.query(BrokerCalendarSync.meeting_id).filter(
        BrokerCalendarSync.broker_id == broker_id
    ).all()
    synced_ids = [s[0] for s in synced_meeting_ids]
    
    unsynced_meetings = db.query(BrokerMeeting).filter(
        BrokerMeeting.broker_id == broker_id,
        BrokerMeeting.scheduled_at > datetime.utcnow()
    )
    if synced_ids:
        unsynced_meetings = unsynced_meetings.filter(~BrokerMeeting.id.in_(synced_ids))
    
    unsynced = unsynced_meetings.all()
    
    synced_count = 0
    for meeting in unsynced:
        try:
            customer = db.query(BrokerCustomer).filter(
                BrokerCustomer.id == meeting.customer_id
            ).first()
            
            calendar_event_id = f"{integration.calendar_type}_{meeting.id}_{datetime.utcnow().timestamp()}"
            sync_record = BrokerCalendarSync(
                broker_id=broker_id,
                meeting_id=meeting.id,
                calendar_event_id=calendar_event_id,
                calendar_type=integration.calendar_type,
                synced_at=datetime.utcnow()
            )
            db.add(sync_record)
            synced_count += 1
        except:
            pass
    
    db.commit()
    
    return {
        "synced_count": synced_count,
        "message": f"Auto-synced {synced_count} meetings to {integration.calendar_type} calendar"
    }

# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM RECIPIENT MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/broker/telegram/recipients")
async def list_telegram_recipients(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """List all Telegram chat IDs registered for this broker."""
    extract_token = get_token_from_request(token, authorization)
    broker_id = verify_token(extract_token, db)
    rows = db.query(BrokerTelegramRecipient).filter(
        BrokerTelegramRecipient.broker_id == broker_id
    ).all()
    return [
        {"id": r.id, "chat_id": r.chat_id, "name": r.name, "is_active": r.is_active, "created_at": r.created_at}
        for r in rows
    ]


@app.post("/api/broker/telegram/recipients", status_code=201)
async def add_telegram_recipient(
    body: dict,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Register a new Telegram chat ID for this broker.

    Body: { "chat_id": "123456789", "name": "@akspatbot" }

    To find your chat_id: message @userinfobot on Telegram.
    """
    extract_token = get_token_from_request(token, authorization)
    broker_id = verify_token(extract_token, db)

    chat_id = str(body.get("chat_id", "")).strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")

    existing = db.query(BrokerTelegramRecipient).filter(
        BrokerTelegramRecipient.broker_id == broker_id,
        BrokerTelegramRecipient.chat_id == chat_id,
    ).first()
    if existing:
        existing.is_active = True
        existing.name = body.get("name", existing.name)
        db.commit()
        return {"id": existing.id, "chat_id": existing.chat_id, "name": existing.name, "is_active": True}

    recipient = BrokerTelegramRecipient(
        broker_id=broker_id,
        chat_id=chat_id,
        name=body.get("name"),
        is_active=True,
    )
    db.add(recipient)
    db.commit()
    db.refresh(recipient)
    return {"id": recipient.id, "chat_id": recipient.chat_id, "name": recipient.name, "is_active": True}


@app.delete("/api/broker/telegram/recipients/{chat_id}")
async def remove_telegram_recipient(
    chat_id: str,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Remove (deactivate) a Telegram recipient."""
    extract_token = get_token_from_request(token, authorization)
    broker_id = verify_token(extract_token, db)
    row = db.query(BrokerTelegramRecipient).filter(
        BrokerTelegramRecipient.broker_id == broker_id,
        BrokerTelegramRecipient.chat_id == chat_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Recipient not found")
    db.delete(row)
    db.commit()
    return {"message": f"Recipient {chat_id} removed"}


@app.post("/api/broker/telegram/test")
async def test_telegram_notification(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Send a test Telegram notification to all registered recipients."""
    extract_token = get_token_from_request(token, authorization)
    broker_id = verify_token(extract_token, db)
    sent = _send_telegram_notification(
        extracted={
            "summary": "🧪 This is a test notification from your Broker CRM.",
            "action_items": ["Confirm this message was received"],
            "urgency": "low",
            "category": "general",
            "customer_name": "Test",
        },
        source="test",
        broker_id=broker_id,
        db=db,
    )
    if sent:
        return {"status": "sent", "message": "Test notification dispatched directly via Python"}
    raise HTTPException(status_code=502, detail="Telegram send failed — check TELEGRAM_BOT_TOKEN")


@app.post("/api/broker/telegram/webhook")
async def telegram_webhook(update: dict, db: Session = Depends(get_db)):
    """Telegram bot webhook for /ack, /snooze, and trade execution commands."""
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", "")).strip()
    text = str(message.get("text", "")).strip()

    if not chat_id or not text:
        return {"ok": True, "ignored": True}

    recipient = db.query(BrokerTelegramRecipient).filter(
        BrokerTelegramRecipient.chat_id == chat_id,
        BrokerTelegramRecipient.is_active == True,
    ).first()
    if not recipient:
        return {"ok": True, "ignored": True, "reason": "unregistered_chat"}

    lower = text.lower()
    now = datetime.utcnow()
    response_text = ""

    if lower.startswith("/ack"):
        _upsert_telegram_state(db, recipient.broker_id, chat_id, last_ack_at=now, last_command="ack")
        response_text = "Acknowledged. Alerts remain active."
    elif lower.startswith("/snooze"):
        delta = _parse_snooze_duration(lower)
        snoozed_until = now + delta
        _upsert_telegram_state(db, recipient.broker_id, chat_id, snoozed_until=snoozed_until, last_command="snooze")
        response_text = f"Alerts snoozed until {snoozed_until.strftime('%Y-%m-%d %H:%M UTC')}."
    elif lower.startswith("/help") or lower.startswith("/start"):
        response_text = (
            "Commands: /ack, /snooze 6h, /snooze 1d, "
            "and trade logs like BUY BHP 100 @44.20 COMM 10, "
            "BOUGHT 150 TLS@5.28, or SOLD 100 TLS@5.28"
        )
    else:
        parsed_action = _parse_trade_action_command(text)
        if parsed_action:
            ok, ingest_result = _forward_trade_action_to_asx(chat_id, text, parsed_action)
            if ok:
                holdings = (ingest_result or {}).get("holdings") or []
                held = next((item for item in holdings if item.get("symbol") == parsed_action["symbol"]), None)
                response_text = (
                    f"Logged {parsed_action['action_type']} {parsed_action['quantity']:.4g} "
                    f"{parsed_action['symbol']} @ {parsed_action['execution_price']:.2f}."
                )
                if parsed_action.get("commission") is not None:
                    response_text += f" Commission {parsed_action['commission']:.2f}."
                if held:
                    response_text += (
                        f" Holding now: {float(held.get('quantity') or 0):.4g} @ "
                        f"{float(held.get('avg_cost') or 0):.2f}."
                    )
            else:
                response_text = (
                    f"Could not log trade action ({ingest_result}). "
                    "Try: BUY BHP 100 @44.20 COMM 10"
                )

    if response_text:
        _send_telegram_reply(chat_id, response_text)

    return {
        "ok": True,
        "handled": bool(response_text),
        "chat_id": chat_id,
        "command": text,
        "response": response_text,
        "broker_id": recipient.broker_id,
    }


@app.get("/api/reminders/due-today")
def get_due_today_reminders(db: Session = Depends(get_db)):
    """Internal endpoint (called by n8n daily scheduler) — returns consolidated daily reports
    for every broker who has Telegram recipients configured.  No auth required since this is
    only reachable inside the Docker network."""
    today = datetime.utcnow().date()
    date_str = datetime.utcnow().strftime("%A, %d %B %Y")

    # Today's meetings across all brokers
    start_of_day = datetime.combine(today, datetime.min.time())
    end_of_day = datetime.combine(today, datetime.max.time())
    today_meetings = db.query(BrokerMeeting).filter(
        BrokerMeeting.scheduled_at >= start_of_day,
        BrokerMeeting.scheduled_at <= end_of_day,
    ).all()

    # Pending recordings from before today that haven't had a reminder sent
    pending_recordings = db.query(BrokerVoiceRecording).filter(
        BrokerVoiceRecording.status == "pending",
        BrokerVoiceRecording.reminder_sent_at == None,
        BrokerVoiceRecording.recorded_at < start_of_day,
    ).all()

    # Group by broker_id
    by_broker: dict = {}
    for m in today_meetings:
        by_broker.setdefault(m.broker_id, {"meetings": [], "recordings": []})["meetings"].append(m)
    for r in pending_recordings:
        by_broker.setdefault(r.broker_id, {"meetings": [], "recordings": []})["recordings"].append(r)

    results = []
    for broker_id, data in by_broker.items():
        recipients = db.query(BrokerTelegramRecipient).filter(
            BrokerTelegramRecipient.broker_id == broker_id,
            BrokerTelegramRecipient.is_active == True,
        ).all()
        if not recipients:
            continue

        text = _format_telegram_daily_report(
            data["meetings"], data["recordings"], date_str
        )
        recording_ids = [r.id for r in data["recordings"]]
        meeting_ids = [m.id for m in data["meetings"]]
        for recipient in recipients:
            results.append({
                "chat_id": recipient.chat_id,
                "broker_id": broker_id,
                "text": text,
                "recording_ids": recording_ids,
                "meeting_ids": meeting_ids,
            })

    return results


@app.post("/api/reminders/mark-sent")
def mark_reminders_sent(body: dict, db: Session = Depends(get_db)):
    """Called by n8n after delivering the daily report — marks recordings so they
    won't be repeated in tomorrow's report."""
    recording_ids = body.get("recording_ids") or []
    updated = 0
    if recording_ids:
        try:
            updated = db.query(BrokerVoiceRecording).filter(
                BrokerVoiceRecording.id.in_(recording_ids)
            ).update({"reminder_sent_at": datetime.utcnow()}, synchronize_session=False)
            db.commit()
        except Exception as exc:
            logging.warning(f"mark_reminders_sent error: {exc}")
            db.rollback()
    return {"status": "ok", "updated": updated}



# ═══════════════════════════════════════════════════════════════════════════════

WA_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WA_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "my_verify_token")
WA_BUSINESS_PHONE = os.getenv("WHATSAPP_BUSINESS_PHONE", "")
META_GRAPH_URL = "https://graph.facebook.com/v19.0"

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or SMTP_USER

class WhatsAppIncomingRequest(BaseModel):
    from_phone: str
    wa_message_id: str
    media_id: str
    audio_mime_type: str = "audio/ogg; codecs=opus"
    phone_number_id: Optional[str] = None

class WhatsAppJobResponse(BaseModel):
    id: int
    from_phone: str
    status: str
    transcript: Optional[str] = None
    extracted_data: Optional[dict] = None
    reply_text: Optional[str] = None
    wa_replied_at: Optional[datetime] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

class WhatsAppMarkRepliedRequest(BaseModel):
    reply_text: str

def _download_meta_audio(media_id: str) -> tuple:
    if not WA_ACCESS_TOKEN:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN not configured")
    headers = {"Authorization": f"Bearer {WA_ACCESS_TOKEN}"}
    resp = requests.get(f"{META_GRAPH_URL}/{media_id}", headers=headers, timeout=15)
    resp.raise_for_status()
    info = resp.json()
    download_url = info.get("url")
    mime_type = info.get("mime_type", "audio/ogg; codecs=opus")
    if not download_url:
        raise RuntimeError(f"Meta API did not return url for media_id={media_id}")
    audio_resp = requests.get(download_url, headers=headers, timeout=30)
    audio_resp.raise_for_status()
    return audio_resp.content, mime_type

def _transcribe_audio(audio_data: bytes, mime_type: str = "audio/ogg; codecs=opus") -> str:
    ext_map = {
        "audio/ogg": ".ogg", "audio/ogg; codecs=opus": ".ogg",
        "audio/mp4": ".m4a", "audio/mpeg": ".mp3",
        "audio/webm": ".webm", "audio/wav": ".wav",
    }
    ext = ext_map.get(mime_type.split(";")[0].strip(), ".ogg")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as fh:
            fh.write(audio_data)
            tmp_path = fh.name
        model = get_whisper_model()
        segments, _ = model.transcribe(
            tmp_path, beam_size=5,
            initial_prompt="Broker CRM meeting notes. Customer names, proper nouns, financial terms, Indian names like Amit, Arav, Rahul, Priya, Sharma, Gupta."
        )
        return " ".join(seg.text.strip() for seg in segments)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

_GROQ_SUMMARY_PROMPT = """You are an intelligent message processor. Analyze this transcript and produce a comprehensive structured summary.

The transcript may contain voice transcription errors — use context to infer correct names, terms, and intent.

OUTPUT: Valid JSON only, exactly these keys:

{
  "category": "one of: meeting, financial, planning, process, inquiry, update, general",
  "subcategory": "optional finer-grained label e.g. 'loan-application', 'status-check', 'scheduling'",
  "sentiment": "positive, neutral, negative, or urgent",
  "intent": "Single concise sentence: what does this person want or need?",
  "detailed_summary": {
    "overview": "2-4 sentence narrative of what was discussed",
    "key_points": ["key fact or topic mentioned", "..."],
    "decisions": ["any decision, commitment, or agreement made"],
    "risks_concerns": ["any risk, concern, blocker, or dependency mentioned"],
    "next_steps": ["concrete next actions, ranked by priority"]
  },
  "entities": {
    "people": ["names mentioned"],
    "dates": ["specific dates or deadlines"],
    "amounts": ["monetary amounts with currency"],
    "documents": ["documents, forms, references mentioned"],
    "locations": ["places, addresses, venue names"]
  },
  "meeting_suggestion": "when, purpose, duration — or null if not needed",
  "follow_up_required": true or false,
  "urgency": "low, medium, or high",
  "tags": ["3-5 keyword tags for filtering e.g. home-loan, refinancing, next-week"],
  "reply_suggestion": "professional 1-3 sentence reply acknowledging the message and stating what happens next"
}

CATEGORY GUIDE:
- meeting: scheduling, rescheduling, confirming appointments
- financial: money, payments, fees, pricing, quotes, invoices
- planning: strategy, roadmaps, future plans, goals, timelines
- process: status updates, workflows, paperwork, applications, approvals
- inquiry: questions, information requests, clarifications
- update: status reports, check-ins, progress updates
- general: anything that doesn't fit the above

If a field is unknown, use null for strings, [] for lists, {} for objects. No markdown, no explanation — ONLY JSON."""

def _extract_with_groq(transcript: str) -> Optional[dict]:
    """Try to extract structured data using Groq's fast cloud LLM. Returns None if unavailable/fails."""
    if not _groq_client or not transcript.strip():
        return None
    try:
        response = _groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _GROQ_SUMMARY_PROMPT},
                {"role": "user", "content": f"Transcript:\n{transcript}"},
            ],
            temperature=0.15,
            max_tokens=2048,
        )
        content = response.choices[0].message.content
        content = content.strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            result = json.loads(match.group())
            result["_llm_provider"] = "groq"
            logging.info(f"Groq extraction succeeded for transcript ({len(transcript)} chars)")
            return result
        return None
    except Exception as exc:
        logging.warning(f"Groq extraction failed: {exc}")
        return None

_EXTRACT_SYSTEM_PROMPT = """You are an intelligent message processor. Analyze this transcript and produce a structured summary.

IMPORTANT: Voice transcription can mis-spell proper nouns. Use context to identify names correctly.

Respond with ONLY valid JSON (no markdown) using exactly these keys:
{
  "category": "meeting|financial|planning|process|inquiry|update|general",
  "sentiment": "positive|neutral|negative|urgent",
  "intent": "Single sentence: what does this person want?",
  "detailed_summary": {"overview": "2-3 sentences", "key_points": [], "decisions": [], "risks_concerns": [], "next_steps": []},
  "entities": {"people": [], "dates": [], "amounts": [], "documents": [], "locations": []},
  "meeting_suggestion": "meeting recommendation or null",
  "follow_up_required": true/false,
  "urgency": "low|medium|high",
  "tags": ["keyword tags"],
  "reply_suggestion": "professional reply message"
}
If unknown, use null for strings or [] for lists."""

def _extract_with_llm(transcript: str) -> dict:
    """Try Groq first, fall back to local Ollama. Returns structured data from the transcript."""
    if not transcript.strip():
        return {"summary": "Empty transcript", "action_items": [], "follow_up_required": False,
                "urgency": "low", "reply_suggestion": "Thanks for your message!", "context": "",
                "next_best_actions": [], "meeting_suggestion": None, "discussed_topics": [],
                "next_discussion_topics": [], "follow_ups": [], "key_details": {}}

    # Try Groq first
    groq_result = _extract_with_groq(transcript)
    if groq_result:
        return groq_result

    # Fall back to local Ollama
    try:
        resp = requests.post(
            f"{LOCAL_LLM_URL}/chat/completions",
            json={
                "model": LOCAL_LLM_MODEL,
                "messages": [
                    {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Transcript:\n{transcript}"},
                ],
                "temperature": 0.1,
                "max_tokens": 2048,
            },
            timeout=180,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        content = re.sub(r'<think>.*$', '', content, flags=re.DOTALL)
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            result = json.loads(match.group())
            result["_llm_provider"] = "ollama"
            return result
        return {"summary": content[:300], "action_items": [], "follow_up_required": True,
                "urgency": "medium", "reply_suggestion": "Thanks for your message, we will follow up shortly.",
                "context": "", "next_best_actions": [], "meeting_suggestion": None,
                "discussed_topics": [], "next_discussion_topics": [], "follow_ups": [], "key_details": {}}
    except Exception as exc:
        logging.warning(f"LLM extraction error: {exc}")
        return {
            "summary": f"Voice message received. Transcript: {transcript[:200]}",
            "action_items": [], "follow_up_required": True,
            "urgency": "medium", "context": "",
            "reply_suggestion": "Hi, thanks for your voice message! We've received it and will get back to you shortly.",
            "next_best_actions": [], "meeting_suggestion": None,
            "discussed_topics": [], "next_discussion_topics": [], "follow_ups": [], "key_details": {},
            "llm_error": str(exc)[:100],
            "_llm_provider": "error",
        }

# ── Telegram / n8n notification helper ────────────────────────────────────────

def _format_telegram_daily_report(meetings: list, recordings: list, date_str: str) -> str:
    """Build a consolidated morning report HTML message for Telegram."""
    lines: List[str] = []
    lines += [f"🌅 <b>Good Morning! — Daily Report</b>", f"<i>{date_str}</i>", ""]

    # ── Today's meetings ──────────────────────────────────────────
    if meetings:
        lines.append(f"📅 <b>TODAY'S MEETINGS ({len(meetings)})</b>")
        for m in meetings:
            time_str = m.scheduled_at.strftime("%I:%M %p") if m.scheduled_at else "TBD"
            title = m.title or "Meeting"
            dur = f" · {m.duration_minutes}min" if m.duration_minutes else ""
            lines.append(f"  🕐 {time_str} — {title}{dur}")
            if m.description:
                lines.append(f"     <i>{m.description[:80]}</i>")
        lines.append("")

    # ── Pending follow-ups ────────────────────────────────────────
    if recordings:
        lines.append(f"📋 <b>PENDING FOLLOW-UPS ({len(recordings)})</b>")
        for r in recordings:
            urgency = str(r.priority or "medium").upper()
            urgency_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(urgency, "🟡")
            age_days = (datetime.utcnow() - r.recorded_at).days if r.recorded_at else 0
            age_str = f"{age_days}d ago" if age_days > 0 else "today"
            customer = r.customer_name or "Unknown"
            category = (r.category or "General").capitalize()

            lines += [
                "",
                f"  {urgency_emoji} <b>{customer}</b> · {category} · <i>{age_str}</i>",
            ]
            if r.discussion_summary:
                # Show first 120 chars of summary
                summary = r.discussion_summary[:120] + ("…" if len(r.discussion_summary) > 120 else "")
                lines.append(f"  📝 {summary}")
            try:
                items = json.loads(r.action_items) if r.action_items else []
            except Exception:
                items = []
            for i, item in enumerate(items[:4], 1):
                lines.append(f"  ✅ {i}. {item}")
            if r.next_meeting:
                lines.append(f"  📆 Next: {r.next_meeting}")
        lines.append("")

    # ── Summary footer ────────────────────────────────────────────
    total_actions = sum(
        len(json.loads(r.action_items)) if r.action_items else 0
        for r in recordings
        if r.action_items
    )
    lines.append(f"📊 {len(meetings)} meeting(s) · {len(recordings)} follow-up(s) · {total_actions} action item(s)")

    return "\n".join(lines)


def _format_telegram_message(extracted: dict, source: str) -> str:
    """Build a clean, human-readable Telegram HTML message from LLM-extracted data.
    
    Includes: Source, Summary, Action Items, People, Dates
    Excludes: Intent, Key Points, Decisions, Meeting Suggestion, Reply Suggestion, Tags
    """
    lines: List[str] = []

    source_label = {
        "dashboard": "🎙️ Voice / Text Note",
        "whatsapp": "📱 WhatsApp Message",
        "test": "🧪 Test Notification",
    }.get(source, "🔔 New Message")
    lines += [f"<b>{source_label}</b>", ""]

    # Summary / overview
    summary = (
        extracted.get("summary") or
        (extracted.get("detailed_summary") or {}).get("overview") or
        extracted.get("discussion_summary") or ""
    )
    if summary:
        lines += ["📝 <b>Summary</b>", summary, ""]

    # Action items / next steps
    next_steps = (
        extracted.get("action_items") or
        (extracted.get("detailed_summary") or {}).get("next_steps") or []
    )
    if next_steps:
        lines.append("✅ <b>Action Items</b>")
        for i, step in enumerate(next_steps[:6], 1):
            lines.append(f"  {i}. {step}")
        lines.append("")

    # Entities — people, dates (amounts excluded per user request)
    entities = extracted.get("entities") or {}
    people = entities.get("people") or []
    dates = entities.get("dates") or []
    if people:
        lines.append(f"👤 <b>People:</b> {', '.join(people[:4])}")
    if dates:
        lines.append(f"📅 <b>Dates:</b> {', '.join(dates[:4])}")
    if people or dates:
        lines.append("")

    # Footer: urgency · category · sentiment
    urgency = str(extracted.get("urgency", "medium")).upper()
    category = str(extracted.get("category", "")).capitalize()
    sentiment = str(extracted.get("sentiment", "")).lower()
    urgency_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(urgency, "🟡")
    sentiment_emoji = {"positive": "😊", "negative": "😟", "urgent": "⚡", "neutral": "😐"}.get(sentiment, "")
    footer = urgency_emoji + " " + urgency
    if category:
        footer += f" · {category}"
    if sentiment_emoji:
        footer += f" · {sentiment_emoji} {sentiment.capitalize()}"
    lines.append(footer)

    return "\n".join(lines)


def _send_telegram_notification(extracted: dict, source: str, broker_id: str, db=None) -> bool:
    """Send a processed-message summary directly to the Telegram Bot API.

    Looks up active Telegram recipients for the broker from the database first;
    falls back to the TELEGRAM_CHAT_IDS env-var list.  Returns True on success.
    """
    try:
        # Resolve recipient chat IDs (DB first, env fallback)
        chat_ids: List[str] = []
        if db is not None:
            try:
                rows = db.query(BrokerTelegramRecipient).filter(
                    BrokerTelegramRecipient.broker_id == broker_id,
                    BrokerTelegramRecipient.is_active == True,
                ).all()
                chat_ids = [r.chat_id for r in rows]
            except Exception as db_exc:
                logging.warning(f"_send_telegram_notification: DB lookup failed: {db_exc}")

        if not chat_ids:
            chat_ids = list(_TELEGRAM_CHAT_IDS_ENV)  # env-var fallback

        if not chat_ids:
            logging.info("_send_telegram_notification: no Telegram recipients configured, skipping")
            return False

        text = _format_telegram_message(extracted, source)

        # Send directly to Telegram Bot API if token is available (most reliable)
        if TELEGRAM_BOT_TOKEN:
            success_count = 0
            for chat_id in chat_ids:
                try:
                    resp = requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        success_count += 1
                    else:
                        logging.warning(f"Telegram API error for {chat_id}: {resp.status_code} {resp.text[:200]}")
                except Exception as req_exc:
                    logging.warning(f"Telegram send failed for {chat_id}: {req_exc}")
            if success_count > 0:
                logging.info(f"Telegram sent directly (source={source}, broker={broker_id}, recipients={success_count})")
                return True
            return False


    except Exception as exc:
        logging.warning(f"_send_telegram_notification failed: {exc}")
        return False


def _send_telegram_reply(chat_id: str, text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _parse_snooze_duration(command_text: str) -> timedelta:
    match = re.search(r"/snooze(?:\s+(\d+))?\s*([mhd])?", command_text.lower())
    value = int(match.group(1)) if match and match.group(1) else 6
    unit = match.group(2) if match and match.group(2) else "h"
    if unit == "m":
        return timedelta(minutes=max(5, value))
    if unit == "d":
        return timedelta(days=max(1, value))
    return timedelta(hours=max(1, value))


def _parse_trade_action_command(command_text: str) -> Optional[dict]:
    text_value = str(command_text or "").strip()
    if not text_value:
        return None

    patterns = [
        # BUY BHP 100 @44.20 COMM 10
        re.compile(
            r"^/?(?P<action>buy|bought|add|sell|sold|reduce)\s+"
            r"(?P<symbol>[A-Za-z][A-Za-z0-9.\-]{0,11})\s+"
            r"(?P<quantity>\d+(?:\.\d+)?)"
            r"(?:\s*(?:@|at)?\s*(?P<price>\d+(?:\.\d+)?))"
            r"(?:.*?(?:comm|commission|fee)\s*(?P<commission>\d+(?:\.\d+)?))?"
            r"(?:\s+note[:=]?\s*(?P<note>.+))?$",
            re.IGNORECASE,
        ),
        # BUY TLS @5.28 OF 150 UNITS
        re.compile(
            r"^/?(?P<action>buy|bought|add|sell|sold|reduce)\s+"
            r"(?P<symbol>[A-Za-z][A-Za-z0-9.\-]{0,11})\s*"
            r"(?:@|at)\s*(?P<price>\d+(?:\.\d+)?)\s*"
            r"(?:of|for)\s*(?P<quantity>\d+(?:\.\d+)?)\s*"
            r"(?:unit|units|share|shares)?"
            r"(?:.*?(?:comm|commission|fee)\s*(?P<commission>\d+(?:\.\d+)?))?"
            r"(?:\s+note[:=]?\s*(?P<note>.+))?$",
            re.IGNORECASE,
        ),
        # BUY TLS 150 UNITS AT 5.28
        re.compile(
            r"^/?(?P<action>buy|bought|add|sell|sold|reduce)\s+"
            r"(?P<symbol>[A-Za-z][A-Za-z0-9.\-]{0,11})\s+"
            r"(?P<quantity>\d+(?:\.\d+)?)\s*(?:unit|units|share|shares)?\s*"
            r"(?:@|at)\s*(?P<price>\d+(?:\.\d+)?)"
            r"(?:.*?(?:comm|commission|fee)\s*(?P<commission>\d+(?:\.\d+)?))?"
            r"(?:\s+note[:=]?\s*(?P<note>.+))?$",
            re.IGNORECASE,
        ),
        # BOUGHT 150 TLS@5.28 / SOLD 100 TLS@5.28
        re.compile(
            r"^/?(?P<action>buy|bought|add|sell|sold|reduce)\s+"
            r"(?P<quantity>\d+(?:\.\d+)?)\s+"
            r"(?P<symbol>[A-Za-z][A-Za-z0-9.\-]{0,11})\s*"
            r"(?:@|at)\s*(?P<price>\d+(?:\.\d+)?)"
            r"(?:.*?(?:comm|commission|fee)\s*(?P<commission>\d+(?:\.\d+)?))?"
            r"(?:\s+note[:=]?\s*(?P<note>.+))?$",
            re.IGNORECASE,
        ),
    ]

    match = None
    for pattern in patterns:
        match = pattern.match(text_value)
        if match:
            break
    if not match:
        return None

    action = match.group("action").upper()
    symbol = match.group("symbol").upper()
    quantity = float(match.group("quantity"))
    price = float(match.group("price"))
    commission_raw = match.group("commission")
    note = (match.group("note") or "").strip() or None
    action_map = {
        "BUY": "BUY",
        "BOUGHT": "BUY",
        "ADD": "ADD",
        "SELL": "SELL",
        "SOLD": "SELL",
        "REDUCE": "REDUCE",
    }

    if quantity <= 0 or price <= 0:
        return None

    return {
        "action_type": action_map.get(action, "BUY"),
        "symbol": symbol,
        "quantity": quantity,
        "execution_price": price,
        "commission": float(commission_raw) if commission_raw is not None else None,
        "notes": note,
    }


def _forward_trade_action_to_asx(chat_id: str, raw_text: str, parsed_action: dict) -> tuple[bool, dict | str]:
    if not ASX_BACKEND_URL:
        return False, "ASX_BACKEND_URL is not configured"

    payload = {
        "chat_id": chat_id,
        "symbol": parsed_action["symbol"],
        "market": "AU",
        "action_type": parsed_action["action_type"],
        "quantity": parsed_action["quantity"],
        "execution_price": parsed_action["execution_price"],
        "commission": parsed_action.get("commission"),
        "source_message_type": "telegram_reply",
        "notes": parsed_action.get("notes"),
        "raw_text": raw_text,
    }
    headers = {"Content-Type": "application/json"}
    if TELEGRAM_ACTION_INGEST_SECRET:
        headers["x-telegram-action-secret"] = TELEGRAM_ACTION_INGEST_SECRET

    try:
        resp = requests.post(f"{ASX_BACKEND_URL}/api/telegram/action-ingest", json=payload, headers=headers, timeout=10)
        if resp.status_code in {200, 201}:
            try:
                return True, resp.json() if resp.content else {}
            except Exception:
                return True, {}
        try:
            detail = (resp.json() or {}).get("detail") or resp.text[:160]
        except Exception:
            detail = resp.text[:160] if resp.text else f"HTTP {resp.status_code}"
        return False, detail
    except Exception as exc:
        return False, str(exc)


def _upsert_telegram_state(db: Session, broker_id: str, chat_id: str, *, snoozed_until=None, last_ack_at=None, last_command=None):
    row = db.query(BrokerTelegramState).filter(
        BrokerTelegramState.broker_id == broker_id,
        BrokerTelegramState.chat_id == chat_id,
    ).first()
    if not row:
        row = BrokerTelegramState(broker_id=broker_id, chat_id=chat_id)
        db.add(row)
    if snoozed_until is not None:
        row.snoozed_until = snoozed_until
    if last_ack_at is not None:
        row.last_ack_at = last_ack_at
    if last_command is not None:
        row.last_command = last_command
    db.commit()
    db.refresh(row)
    return row


def _configure_telegram_webhook() -> bool:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_URL):
        return False
    payload = {"url": TELEGRAM_WEBHOOK_URL}
    if TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = TELEGRAM_WEBHOOK_SECRET
    try:
        resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook", json=payload, timeout=15)
        logging.info(f"Telegram webhook registration status: {resp.status_code} {resp.text[:200]}")
        return resp.status_code == 200
    except Exception as exc:
        logging.warning(f"Telegram webhook registration failed: {exc}")
        return False

# ── Calendar / ICS helpers ────────────────────────────────────────────────────────

def _parse_next_meeting_datetime(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        from dateutil import parser as dateutil_parser
        today = datetime.utcnow()
        s = date_str.strip().lower()
        day_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                   "friday": 4, "saturday": 5, "sunday": 6}
        for day_name, day_num in day_map.items():
            if day_name in s:
                days_ahead = (day_num - today.weekday() + 7) % 7 or 7
                return today.replace(hour=10, minute=0, second=0, microsecond=0) + \
                    timedelta(days=days_ahead)
        parsed = dateutil_parser.parse(date_str, default=today, dayfirst=False)
        if parsed < today:
            parsed = parsed + timedelta(days=7)
        return parsed
    except Exception:
        return datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0) + \
            timedelta(days=7)

def _generate_ics(title: str, start_dt: datetime, end_dt: datetime,
                  description: str, organizer_email: str) -> bytes:
    try:
        from icalendar import Calendar, Event as ICSEvent
        cal = Calendar()
        cal.add('PRODID', '-//Broker CRM//WhatsApp Auto-Meeting//EN')
        cal.add('VERSION', '2.0')
        cal.add('METHOD', 'REQUEST')
        event = ICSEvent()
        event.add('SUMMARY', title)
        event.add('DTSTART', start_dt)
        event.add('DTEND', end_dt)
        event.add('DESCRIPTION', description)
        event.add('UID', str(uuid.uuid4()) + '@broker-crm')
        cal.add_component(event)
        return cal.to_ical()
    except Exception as e:
        logging.warning(f"ICS generation error: {e}")
        return b""

def _send_ics_email(
    to_email: str, subject: str, body: str, ics_bytes: bytes,
    smtp_host: str = None, smtp_port: int = None,
    smtp_user: str = None, smtp_password: str = None, smtp_from: str = None
) -> bool:
    host = smtp_host or SMTP_HOST
    port = smtp_port or SMTP_PORT
    user = smtp_user or SMTP_USER
    password = smtp_password or SMTP_PASSWORD
    from_addr = smtp_from or SMTP_FROM or user
    if not host or not user or not to_email:
        logging.info(f"SMTP not configured — skipping calendar email to {to_email}")
        return False
    try:
        msg = MIMEMultipart('mixed')
        msg['From'] = from_addr
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        if ics_bytes:
            ics_part = MIMEBase('text', 'calendar', method='REQUEST', name='invite.ics')
            ics_part.set_payload(ics_bytes)
            encoders.encode_base64(ics_part)
            ics_part.add_header('Content-Disposition', 'attachment', filename='invite.ics')
            msg.attach(ics_part)
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_email], msg.as_string())
        logging.info(f"Calendar invite sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logging.warning(f"Failed to send calendar invite to {to_email}: {e}")
        return False

def _try_send_meeting_invite(meeting, customer, broker_id: str, db) -> bool:
    try:
        profile = db.query(BrokerProfile).filter(
            BrokerProfile.broker_id == broker_id
        ).first()
        to_email = (profile.calendar_email if profile else None) or None
        if not to_email:
            broker_user = db.query(BrokerUser).filter(
                BrokerUser.broker_id == broker_id
            ).first()
            to_email = broker_user.email if broker_user else None
        if not to_email:
            logging.info(f"No email for broker '{broker_id}' — skipping meeting notification")
            return False
        start = meeting.scheduled_at
        end = start + timedelta(minutes=meeting.duration_minutes)
        ics = _generate_ics(
            title=meeting.title, start_dt=start, end_dt=end,
            description=meeting.description or "",
            organizer_email=to_email,
        )
        return _send_ics_email(
            to_email=to_email,
            subject=f"New Meeting Request: {meeting.title}",
            body=(
                f"A meeting has been requested via WhatsApp.\n\n"
                f"Customer: {customer.name}\n"
                f"Customer Phone: {customer.phone or 'N/A'}\n"
                f"When: {start.strftime('%A, %B %d %Y at %I:%M %p')} (UTC)\n"
                f"Duration: {meeting.duration_minutes} minutes\n\n"
                f"{meeting.description or ''}\n\n"
                f"A calendar invite (.ics) is attached — open it to add to your calendar."
            ),
            ics_bytes=ics,
            smtp_host=profile.smtp_host if profile else None,
            smtp_port=profile.smtp_port if profile else None,
            smtp_user=profile.smtp_user if profile else None,
            smtp_password=profile.smtp_password if profile else None,
            smtp_from=profile.smtp_from if profile else None,
        )
    except Exception as e:
        logging.warning(f"_try_send_meeting_invite error: {e}")
        return False

# ── Background worker thread ─────────────────────────────────────────────────────
_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()

def _process_one_pending_job() -> bool:
    db = SessionLocal()
    job_id = None
    try:
        job = (
            db.query(WhatsAppVoiceJob)
            .filter(WhatsAppVoiceJob.status == "pending")
            .order_by(WhatsAppVoiceJob.created_at)
            .with_for_update(skip_locked=True)
            .first()
        )
        if not job:
            return False
        job_id = job.id
        job.status = "processing"
        db.commit()
        logging.info(f"WhatsApp worker: processing job id={job.id} from={job.from_phone}")

        if job.message_type == "text":
            transcript = job.text_content or ""
            logging.info(f"WhatsApp worker: job id={job.id} text message, len={len(transcript)}")
        else:
            audio_data = job.audio_data
            if not audio_data:
                audio_data, detected_mime = _download_meta_audio(job.media_id)
                if not job.audio_mime_type:
                    job.audio_mime_type = detected_mime
            if not audio_data:
                raise RuntimeError("Audio download returned empty bytes")
            if not job.audio_data:
                job.audio_data = audio_data
                db.commit()
            transcript = _transcribe_audio(audio_data, job.audio_mime_type or "audio/ogg; codecs=opus")
            logging.info(f"WhatsApp worker: job id={job.id} transcript length={len(transcript)}")

        # Extract structured data (Groq first, fallback to Ollama)
        extracted = _extract_with_llm(transcript)

        job.transcript = transcript
        job.extracted_data = json.dumps(extracted)
        job.reply_text = extracted.get("reply_suggestion", "Thanks for your message!")
        job.category = extracted.get("category", "general")
        job.status = "done"
        job.processed_at = datetime.utcnow()
        db.commit()
        logging.info(f"WhatsApp worker: job id={job.id} done (category={job.category}, provider={extracted.get('_llm_provider', 'unknown')})")

        # Create BrokerVoiceRecording
        try:
            _job_broker_id = job.broker_id or "whatsapp"
            _urgency = extracted.get("urgency", "medium")
            if _urgency not in ("low", "medium", "high"):
                _urgency = "medium"
            _detailed = extracted.get("detailed_summary", {})
            _details = extracted.get("key_details") or extracted.get("entities", {})
            _discussed = extracted.get("discussed_topics", []) or _detailed.get("key_points", [])
            _actions = extracted.get("action_items", []) or _detailed.get("next_steps", [])
            _has_useful_data = bool(
                extracted.get("customer_name") or extracted.get("intent") or
                extracted.get("summary") or _detailed.get("overview")
            )
            recording = BrokerVoiceRecording(
                broker_id=_job_broker_id,
                transcript=transcript,
                customer_name=extracted.get("customer_name") or job.from_phone,
                discussion_summary=extracted.get("summary") or _detailed.get("overview", ""),
                next_meeting=extracted.get("next_meeting_date"),
                action_items=json.dumps(_actions),
                key_points=json.dumps(_discussed),
                follow_ups=json.dumps(extracted.get("follow_ups", [])),
                status="pending" if _has_useful_data else "closed",
                priority=_urgency,
                category=extracted.get("category", "general"),
                subcategory=extracted.get("subcategory"),
                sentiment=extracted.get("sentiment"),
                tags=json.dumps(extracted.get("tags", [])),
                entities=json.dumps(_details),
            )
            db.add(recording)
            db.commit()
            logging.info(f"WhatsApp worker: created BrokerVoiceRecording id={recording.id} category={recording.category}")

            # Route by category
            _route_by_category(recording, job, extracted, db)

        except Exception as rec_exc:
            logging.warning(f"WhatsApp worker: BrokerVoiceRecording creation failed: {rec_exc}")

        # Send summary to Telegram via n8n
        _send_telegram_notification(
            extracted,
            source="whatsapp",
            broker_id=job.broker_id or "whatsapp",
            db=db,
        )

        # Auto-create meeting if next_meeting_date was extracted
        next_date_str = extracted.get("next_meeting_date")
        customer_name_extracted = extracted.get("customer_name") or job.from_phone

        if not next_date_str:
            date_pattern = re.compile(
                r'(\d{1,2}[\s\-/]+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{1,2})[\s\-/]+\d{2,4}|'
                r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}[\s,]+\d{4}|'
                r'(?:next\s+(?:week|monday|tuesday|wednesday|thursday|friday)|tomorrow))',
                re.IGNORECASE
            )
            for topic in extracted.get("next_discussion_topics", []):
                m = date_pattern.search(str(topic))
                if m:
                    next_date_str = m.group(0)
                    logging.info(f"WhatsApp worker: inferred meeting date from next_discussion_topics: '{next_date_str}'")
                    break

        if next_date_str:
            try:
                meeting_dt = _parse_next_meeting_datetime(next_date_str)
                if meeting_dt:
                    _bid = job.broker_id or "whatsapp"
                    customer = db.query(BrokerCustomer).filter(
                        BrokerCustomer.broker_id == _bid,
                        BrokerCustomer.phone == job.from_phone
                    ).first()
                    if not customer:
                        customer = db.query(BrokerCustomer).filter(
                            BrokerCustomer.broker_id == _bid,
                            BrokerCustomer.phone.like(f"%{job.from_phone[-7:]}%")
                        ).first()
                    if not customer:
                        customer = db.query(BrokerCustomer).filter(
                            BrokerCustomer.phone == job.from_phone
                        ).first()
                    if not customer:
                        customer = BrokerCustomer(
                            broker_id=_bid, name=customer_name_extracted,
                            email="", phone=job.from_phone,
                            notes="Auto-created from WhatsApp message"
                        )
                        db.add(customer)
                        db.flush()

                    discussed = ", ".join(extracted.get("discussed_topics", []))
                    next_topics = ", ".join(extracted.get("next_discussion_topics", []))
                    meeting_suggestion = extracted.get("meeting_suggestion", "")
                    meeting_description = (
                        f"Auto-created from WhatsApp message.\n"
                        f"Discussed: {discussed or 'See transcript'}\n"
                        f"Next agenda: {next_topics or 'To be confirmed'}\n"
                        f"Meeting suggestion: {meeting_suggestion}\n\n"
                        f"Transcript: {transcript[:500]}"
                    )
                    auto_meeting = BrokerMeeting(
                        broker_id=_bid, customer_id=customer.id,
                        title=f"Follow-up: {customer_name_extracted}",
                        description=meeting_description,
                        scheduled_at=meeting_dt,
                        duration_minutes=30,
                        notes=json.dumps(extracted)
                    )
                    db.add(auto_meeting)
                    db.flush()
                    job.auto_meeting_id = auto_meeting.id
                    db.commit()
                    logging.info(f"WhatsApp worker: auto-created meeting id={auto_meeting.id}")

                    _try_send_meeting_invite(auto_meeting, customer, _bid, db)

            except Exception as meet_exc:
                logging.warning(f"WhatsApp worker: auto-meeting creation failed: {meet_exc}")
                db.rollback()
                db.merge(job)
                job.status = "done"
                job.processed_at = datetime.utcnow()
                db.commit()

        return True

    except Exception as exc:
        logging.error(f"WhatsApp worker: job id={job_id} failed: {exc}")
        try:
            if job_id:
                db.rollback()
                fail_job = db.query(WhatsAppVoiceJob).filter(WhatsAppVoiceJob.id == job_id).first()
                if fail_job:
                    fail_job.status = "failed"
                    fail_job.error_msg = str(exc)[:500]
                    db.commit()
        except Exception:
            pass
        return True
    finally:
        db.close()

def _whatsapp_worker_loop():
    while not _worker_stop.is_set():
        try:
            processed = _process_one_pending_job()
            if not processed:
                time.sleep(15)
        except Exception as e:
            logging.error(f"WhatsApp worker loop error: {e}")
            time.sleep(15)

def _start_whatsapp_worker():
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_whatsapp_worker_loop, daemon=True)
    _worker_thread.start()
    logging.info("WhatsApp background worker started")

# ── Category Routing ─────────────────────────────────────────────────────────

CATEGORY_CONFIG = {
    "meeting":   {"icon": "📅", "color": "#3B82F6", "table": "meetings"},
    "financial": {"icon": "💰", "color": "#10B981", "table": "recordings"},
    "planning":  {"icon": "📋", "color": "#F59E0B", "table": "recordings"},
    "process":   {"icon": "⚙️", "color": "#06B6D4", "table": "recordings"},
    "inquiry":   {"icon": "❓", "color": "#EC4899", "table": "recordings"},
    "update":    {"icon": "📝", "color": "#64748B", "table": "recordings"},
    "general":   {"icon": "💬", "color": "#94A3B8", "table": "recordings"},
}

def _route_by_category(recording, job, extracted, db):
    """Route categorized messages to appropriate tables and create action items."""
    category = extracted.get("category", "general")
    intent = extracted.get("intent", "")
    detailed = extracted.get("detailed_summary", {})
    entities = extracted.get("entities", {})

    # If category is meeting, or meeting_suggestion present, auto-create meeting
    meeting_suggestion = extracted.get("meeting_suggestion")
    next_date_str = extracted.get("next_meeting_date")
    if category == "meeting" and (meeting_suggestion or next_date_str):
        try:
            _bid = recording.broker_id
            customer = db.query(BrokerCustomer).filter(
                BrokerCustomer.broker_id == _bid,
                BrokerCustomer.phone == job.from_phone
            ).first()
            if not customer:
                customer = BrokerCustomer(
                    broker_id=_bid,
                    name=extracted.get("customer_name") or job.from_phone,
                    email="", phone=job.from_phone,
                    notes="Auto-created from message"
                )
                db.add(customer)
                db.flush()
                db.commit()

            meeting_dt = _parse_next_meeting_datetime(next_date_str) if next_date_str else datetime.utcnow() + timedelta(days=1)
            meeting_dt = meeting_dt.replace(hour=10, minute=0, second=0, microsecond=0)

            auto_meeting = BrokerMeeting(
                broker_id=_bid, customer_id=customer.id,
                title=meeting_suggestion or f"Meeting: {intent}" or "Follow-up",
                description=json.dumps({"intent": intent, "detailed_summary": detailed, "entities": entities}),
                scheduled_at=meeting_dt, duration_minutes=30,
                notes=json.dumps(extracted)
            )
            db.add(auto_meeting)
            db.commit()
            logging.info(f"Category routing: auto-created meeting id={auto_meeting.id} from {category} message")
            _try_send_meeting_invite(auto_meeting, customer, _bid, db)
        except Exception as e:
            logging.warning(f"Category routing: meeting creation failed: {e}")

# ── WhatsApp Health Check ─────────────────────────────────────────────────────

_whatsapp_health = {"connected": False, "phone_number_id": "", "display_number": "", "error": "", "checked_at": None}

def _check_whatsapp_health():
    global _whatsapp_health
    try:
        if not WA_ACCESS_TOKEN:
            _whatsapp_health = {"connected": False, "error": "WHATSAPP_ACCESS_TOKEN not configured"}
            return
        phone_id = WA_PHONE_NUMBER_ID
        if not phone_id:
            _whatsapp_health = {"connected": False, "error": "WHATSAPP_PHONE_NUMBER_ID not configured"}
            return

        resp = requests.get(f"{META_GRAPH_URL}/{phone_id}", headers={"Authorization": f"Bearer {WA_ACCESS_TOKEN}"}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            display = data.get("display_phone_number", "")
            _whatsapp_health = {
                "connected": True,
                "phone_number_id": phone_id,
                "display_number": display,
                "error": "",
                "checked_at": datetime.utcnow().isoformat(),
            }
            logging.info(f"WhatsApp health check OK: connected to {display} (token permanent)")
        elif resp.status_code == 401:
            _whatsapp_health = {"connected": False, "phone_number_id": phone_id, "error": "Token invalid or expired — regenerate in Business Settings"}
            logging.warning("WhatsApp health check FAILED: token invalid/expired")
        else:
            _whatsapp_health = {"connected": False, "phone_number_id": phone_id, "error": f"API error {resp.status_code}: {resp.text[:200]}"}
            logging.warning(f"WhatsApp health check FAILED: HTTP {resp.status_code}")
    except Exception as e:
        _whatsapp_health = {"connected": False, "error": str(e)[:200]}
        logging.warning(f"WhatsApp health check error: {e}")

# ── WhatsApp Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/whatsapp/webhook")
async def whatsapp_verify(
    hub_mode: str = None,
    hub_challenge: str = None,
    hub_verify_token: str = None,
):
    if hub_verify_token == WA_VERIFY_TOKEN:
        return int(hub_challenge) if hub_challenge else {"status": "ok"}
    raise HTTPException(status_code=403, detail="Invalid verify token")

@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        entries = body.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])
                phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
                for msg in messages:
                    from_phone = msg.get("from", "")
                    msg_id = msg.get("id", "")

                    # Route to broker by phone_number_id
                    broker_id = "whatsapp"
                    if phone_number_id:
                        wa_num = db.query(BrokerWhatsAppNumber).filter(
                            BrokerWhatsAppNumber.phone_number_id == phone_number_id
                        ).first()
                        if wa_num:
                            broker_id = wa_num.broker_id
                    # Also try routing by customer phone
                    if broker_id == "whatsapp":
                        cp = db.query(BrokerCustomerPhone).filter(
                            BrokerCustomerPhone.customer_phone == from_phone
                        ).first()
                        if cp:
                            broker_id = cp.broker_id

                    if msg.get("type") == "text":
                        text_body = msg.get("text", {}).get("body", "")
                        job = WhatsAppVoiceJob(
                            from_phone=from_phone, wa_message_id=msg_id,
                            message_type="text", text_content=text_body,
                            status="pending", broker_id=broker_id,
                            received_phone_number_id=phone_number_id,
                        )
                        db.add(job)
                        db.commit()
                    elif msg.get("type") == "audio":
                        audio_obj = msg.get("audio", {})
                        media_id = audio_obj.get("id", "")
                        mime_type = audio_obj.get("mime_type", "audio/ogg; codecs=opus")
                        job = WhatsAppVoiceJob(
                            from_phone=from_phone, wa_message_id=msg_id,
                            media_id=media_id, audio_mime_type=mime_type,
                            message_type="audio", status="pending",
                            broker_id=broker_id, received_phone_number_id=phone_number_id,
                        )
                        db.add(job)
                        db.commit()

        return {"status": "received"}
    except Exception as e:
        logging.error(f"WhatsApp webhook error: {e}")
        return {"status": "error", "detail": str(e)[:200]}

@app.post("/api/whatsapp/incoming")
async def whatsapp_incoming(request: WhatsAppIncomingRequest, db: Session = Depends(get_db)):
    broker_id = "whatsapp"
    if request.phone_number_id:
        wa_num = db.query(BrokerWhatsAppNumber).filter(
            BrokerWhatsAppNumber.phone_number_id == request.phone_number_id
        ).first()
        if wa_num:
            broker_id = wa_num.broker_id
    if broker_id == "whatsapp":
        cp = db.query(BrokerCustomerPhone).filter(
            BrokerCustomerPhone.customer_phone == request.from_phone
        ).first()
        if cp:
            broker_id = cp.broker_id

    job = WhatsAppVoiceJob(
        from_phone=request.from_phone,
        wa_message_id=request.wa_message_id,
        media_id=request.media_id,
        audio_mime_type=request.audio_mime_type,
        status="pending",
        broker_id=broker_id,
        received_phone_number_id=request.phone_number_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"id": job.id, "status": "pending"}

@app.post("/api/whatsapp/incoming-text")
async def whatsapp_incoming_text(request: dict, db: Session = Depends(get_db)):
    from_phone = request.get("from_phone", "")
    text_content = request.get("text_content", "")
    wa_message_id = request.get("wa_message_id", "")
    phone_number_id = request.get("phone_number_id", "")

    broker_id = "whatsapp"
    if phone_number_id:
        wa_num = db.query(BrokerWhatsAppNumber).filter(
            BrokerWhatsAppNumber.phone_number_id == phone_number_id
        ).first()
        if wa_num:
            broker_id = wa_num.broker_id

    job = WhatsAppVoiceJob(
        from_phone=from_phone, wa_message_id=wa_message_id,
        message_type="text", text_content=text_content,
        status="pending", broker_id=broker_id,
        received_phone_number_id=phone_number_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"id": job.id, "status": "pending"}

@app.get("/api/whatsapp/jobs")
async def whatsapp_get_jobs(
    status: Optional[str] = None,
    replied: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    q = db.query(WhatsAppVoiceJob)
    if status:
        q = q.filter(WhatsAppVoiceJob.status == status)
    if replied is not None:
        q = q.filter(WhatsAppVoiceJob.replied == (replied.lower() == "true"))
    jobs = q.order_by(WhatsAppVoiceJob.created_at.desc()).limit(limit).all()

    results = []
    for j in jobs:
        extracted = None
        if j.extracted_data:
            try:
                extracted = json.loads(j.extracted_data)
            except Exception:
                pass
        results.append({
            "id": j.id, "from_phone": j.from_phone, "status": j.status,
            "transcript": j.transcript, "extracted_data": extracted,
            "reply_text": j.reply_text, "replied": j.replied,
            "wa_replied_at": j.wa_replied_at and j.wa_replied_at.isoformat(),
            "created_at": j.created_at.isoformat(),
            "processed_at": j.processed_at and j.processed_at.isoformat(),
            "broker_id": j.broker_id, "message_type": j.message_type,
        })
    return results

@app.post("/api/whatsapp/mark-replied/{job_id}")
async def whatsapp_mark_replied(job_id: int, request: WhatsAppMarkRepliedRequest, db: Session = Depends(get_db)):
    job = db.query(WhatsAppVoiceJob).filter(WhatsAppVoiceJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.replied = True
    job.wa_replied_at = datetime.utcnow()
    job.reply_text = request.reply_text
    db.commit()
    return {"id": job.id, "replied": True}

# ── Voice Recording Summarization Endpoint ───────────────────────────────────────

class SummarizeRequest(BaseModel):
    transcript: str

# ── Broker Profile & Setup Endpoints ───────────────────────────────────────────

class WhatsAppPhoneRequest(BaseModel):
    phone: str

class SmtpSetupRequest(BaseModel):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

@app.get("/api/broker/profile")
async def get_broker_profile(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    profile = db.query(BrokerProfile).filter(BrokerProfile.broker_id == broker_id).first()
    return {
        "broker_id": broker_id,
        "setup_complete": profile.setup_complete if profile else False,
        "whatsapp_from_phone": profile.whatsapp_from_phone if profile else None,
        "calendar_email": profile.calendar_email if profile else None,
        "smtp_host": profile.smtp_host if profile else None,
        "smtp_port": profile.smtp_port if profile else 587,
        "smtp_user": profile.smtp_user if profile else None,
        "smtp_from": profile.smtp_from if profile else None,
        "meta_whatsapp_number": f"+1 555-164-8052" if WA_PHONE_NUMBER_ID else None,
        "phone_number_id": WA_PHONE_NUMBER_ID,
    }

@app.put("/api/broker/profile/whatsapp-phone")
async def set_whatsapp_phone(
    request: WhatsAppPhoneRequest,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    phone = request.phone.strip().replace("+", "")
    if not phone or len(phone) < 7:
        raise HTTPException(status_code=400, detail="Invalid phone number")

    # Check not already claimed by another broker
    existing = db.query(BrokerCustomerPhone).filter(
        BrokerCustomerPhone.customer_phone == phone,
        BrokerCustomerPhone.broker_id != broker_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Phone already claimed by another broker")

    # Update or create profile
    profile = db.query(BrokerProfile).filter(BrokerProfile.broker_id == broker_id).first()
    if not profile:
        profile = BrokerProfile(broker_id=broker_id)
        db.add(profile)
    profile.whatsapp_from_phone = phone

    # Ensure broker_customer_phones entry exists
    cp = db.query(BrokerCustomerPhone).filter(
        BrokerCustomerPhone.broker_id == broker_id,
        BrokerCustomerPhone.customer_phone == phone,
    ).first()
    if not cp:
        cp = BrokerCustomerPhone(
            broker_id=broker_id,
            customer_phone=phone,
            customer_name=f"[Broker] {broker_id}",
        )
        db.add(cp)

    db.commit()
    return {"status": "ok", "phone": phone}

@app.get("/api/broker/profile/wa-verify")
async def whatsapp_verify_connection(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    profile = db.query(BrokerProfile).filter(BrokerProfile.broker_id == broker_id).first()
    phone = profile.whatsapp_from_phone if profile else None
    if not phone:
        return {"verified": False, "message": "No phone configured"}

    # Check if any WhatsApp message came from this broker's phone
    job = db.query(WhatsAppVoiceJob).filter(
        WhatsAppVoiceJob.from_phone == phone
    ).first()
    if job:
        profile.verified = True
        db.commit()
        return {"verified": True, "from_phone": phone}
    return {"verified": False, "message": "No message received yet"}

@app.put("/api/broker/profile/smtp")
async def set_smtp_settings(
    request: SmtpSetupRequest,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    profile = db.query(BrokerProfile).filter(BrokerProfile.broker_id == broker_id).first()
    if not profile:
        profile = BrokerProfile(broker_id=broker_id)
        db.add(profile)
    profile.smtp_host = request.smtp_host
    profile.smtp_port = request.smtp_port
    profile.smtp_user = request.smtp_user
    profile.smtp_password = request.smtp_password
    profile.smtp_from = request.smtp_from or request.smtp_user
    db.commit()
    return {"status": "ok"}

@app.post("/api/broker/profile/test-email")
async def test_smtp_email(
    request: SmtpSetupRequest,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    sent = _send_ics_email(
        to_email=request.smtp_user,
        subject="Broker CRM — Test Email",
        body="If you received this, your email notification setup works! You'll get calendar invite emails whenever a customer books a meeting via WhatsApp.",
        ics_bytes=b"",
        smtp_host=request.smtp_host,
        smtp_port=request.smtp_port,
        smtp_user=request.smtp_user,
        smtp_password=request.smtp_password,
        smtp_from=request.smtp_from or request.smtp_user,
    )
    if sent:
        return {"status": "ok", "sent_to": request.smtp_user}
    else:
        raise HTTPException(status_code=500, detail="Failed to send test email. Check SMTP credentials.")

@app.post("/api/broker/profile/setup-complete")
async def mark_setup_complete(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    profile = db.query(BrokerProfile).filter(BrokerProfile.broker_id == broker_id).first()
    if not profile:
        profile = BrokerProfile(broker_id=broker_id)
        db.add(profile)
    profile.setup_complete = True
    db.commit()
    return {"status": "ok", "setup_complete": True}

class WhatsAppSetupRequest(BaseModel):
    broker_phone: str
    phone_number_id: Optional[str] = None

@app.post("/api/broker/whatsapp/setup")
async def setup_whatsapp_routing(
    request: WhatsAppSetupRequest,
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    phone = request.broker_phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number required")

    # Check phone not already claimed by another broker
    existing = db.query(BrokerCustomerPhone).filter(
        BrokerCustomerPhone.customer_phone == phone,
        BrokerCustomerPhone.broker_id != broker_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Phone already claimed by another broker")

    # Update broker profile phone
    broker_user = db.query(BrokerUser).filter(BrokerUser.broker_id == broker_id).first()
    if broker_user:
        broker_user.profile_phone = phone

    # Add/update broker_customer_phones
    cp = db.query(BrokerCustomerPhone).filter(
        BrokerCustomerPhone.broker_id == broker_id,
        BrokerCustomerPhone.customer_phone == phone,
    ).first()
    if not cp:
        cp = BrokerCustomerPhone(
            broker_id=broker_id,
            customer_phone=phone,
            customer_name=f"[Broker] {broker_id}",
        )
        db.add(cp)

    # If phone_number_id provided, bind it to this broker
    if request.phone_number_id:
        existing_wa = db.query(BrokerWhatsAppNumber).filter(
            BrokerWhatsAppNumber.phone_number_id == request.phone_number_id
        ).first()
        if existing_wa:
            if existing_wa.broker_id != broker_id:
                raise HTTPException(status_code=409, detail="Phone number ID already assigned to another broker")
        else:
            wa = BrokerWhatsAppNumber(
                broker_id=broker_id,
                phone_number_id=request.phone_number_id,
            )
            db.add(wa)

    db.commit()
    return {
        "status": "ok",
        "broker_id": broker_id,
        "broker_phone": phone,
        "phone_number_id": request.phone_number_id,
    }

@app.get("/api/broker/whatsapp/status")
async def get_whatsapp_status(
    token: str = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    broker_id = verify_token(extract_token, db)

    phone = db.query(BrokerCustomerPhone).filter(
        BrokerCustomerPhone.broker_id == broker_id
    ).first()

    wa_num = db.query(BrokerWhatsAppNumber).filter(
        BrokerWhatsAppNumber.broker_id == broker_id
    ).first()

    return {
        "broker_id": broker_id,
        "broker_phone": phone.customer_phone if phone else None,
        "phone_number_id": wa_num.phone_number_id if wa_num else None,
        "routing_active": bool(wa_num),
    }

@app.post("/api/broker/summarize")
async def summarize_transcript(
    request: SummarizeRequest,
    db: Session = Depends(get_db)
):
    """Summarize a voice transcript using Groq (primary) with Ollama fallback."""
    result = _extract_with_llm(request.transcript)
    return {
        "extracted_data": result,
        "provider": result.get("_llm_provider", "unknown"),
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
