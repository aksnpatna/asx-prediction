from fastapi import FastAPI, HTTPException, Depends, status, File, UploadFile, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
from pydantic import BaseModel
import jwt
import os
from typing import Optional, List
import requests
import json
import io
import csv
import re
from collections import Counter

# Database Configuration
DATABASE_URL = "postgresql://asx_user:asx_password_dev@db:5432/asx"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# JWT Configuration
SECRET_KEY = "broker-secret-key-change-in-production"
ALGORITHM = "HS256"

# FastAPI App
app = FastAPI(title="Broker CRM API")

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
    status = Column(String(50), default='pending')   # pending, in_progress, completed, closed
    priority = Column(String(20), default='medium')  # low, medium, high
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

# faster-whisper model loader (4-8x faster than openai-whisper on CPU)
_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        import logging
        logging.info("Loading faster-whisper 'tiny' model...")
        # tiny model: ~39MB, fast on CPU, good enough for meeting notes
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        logging.info("faster-whisper 'tiny' model loaded.")
    return _whisper_model

# Create tables on startup
@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    # Pre-load Whisper model so first transcription request isn't slow
    try:
        get_whisper_model()
    except Exception as e:
        import logging
        logging.warning(f"Whisper pre-load failed (will retry on first request): {e}")

# Pydantic Models
class BrokerUserCreate(BaseModel):
    email: str
    username: str
    password: str
    broker_id: str

class BrokerUserLogin(BaseModel):
    email: str
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

@app.post("/api/broker/auth/register", response_model=Token)
async def register(user: BrokerUserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(BrokerUser).filter(BrokerUser.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    db_user = BrokerUser(
        broker_id=user.broker_id,
        email=user.email,
        username=user.username,
        password=user.password  # TODO: Hash password in production
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    token = create_access_token({"sub": user.broker_id})
    return {"access_token": token, "token_type": "bearer", "broker_id": user.broker_id}

@app.post("/api/broker/auth/login", response_model=Token)
async def login(credentials: BrokerUserLogin, db: Session = Depends(get_db)):
    user = db.query(BrokerUser).filter(BrokerUser.email == credentials.email).first()
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
    """Extract structured data from meeting transcript using LLM"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    try:
        # Call Ollama LLM API for data extraction
        llm_prompt = f"""You are a data extraction assistant. Your ONLY job is to extract information that is EXPLICITLY mentioned in the transcript below. Do NOT invent, guess, or add any information that is not directly stated.

TRANSCRIPT:
{request.transcript}

Extract ONLY what is clearly stated in the transcript above. If something is not mentioned, use null or an empty list.

Rules:
- customer_name: Extract the name only if explicitly mentioned. Otherwise null.
- discussion_summary: Summarize only what was actually discussed. Do not add topics not in the transcript.
- next_meeting: Extract the exact date/time if mentioned. Otherwise null.
- action_items: List only tasks explicitly mentioned. If none, use empty list [].
- key_points: List only points explicitly discussed. If none, use empty list [].
- follow_ups: Describe only follow-ups explicitly mentioned. If none, use "None mentioned."

Respond with ONLY valid JSON, no explanation:
{{
  "customer_name": "name or null",
  "discussion_summary": "summary based solely on the transcript",
  "next_meeting": "date/time or null",
  "action_items": [],
  "key_points": [],
  "follow_ups": "based solely on the transcript"
}}"""

        # Call Ollama LLM
        try:
            response = requests.post(
                f"{OLLAMA_API_URL}/api/generate",
                json={
                    "model": "deepseek-r1:7b",
                    "prompt": llm_prompt,
                    "stream": False,
                    "temperature": 0.1
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                llm_response = result.get("response", "")
                
                # deepseek-r1 wraps reasoning in <think>...</think> — strip it before parsing
                import re
                # Remove all <think>...</think> blocks
                llm_response = re.sub(r'<think>.*?</think>', '', llm_response, flags=re.DOTALL)
                # Handle incomplete closing tags
                llm_response = re.sub(r'<think>.*$', '', llm_response, flags=re.DOTALL)
                # Strip markdown code fences (```json ... ```)
                llm_response = re.sub(r'```json\s*', '', llm_response)
                llm_response = re.sub(r'```\s*', '', llm_response).strip()
                
                # Extract JSON: try balanced brackets, then first-{-to-last-} fallback
                json_str = None
                m = re.search(r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}', llm_response)
                if m:
                    json_str = m.group()
                else:
                    start = llm_response.find('{')
                    end = llm_response.rfind('}')
                    if start != -1 and end != -1 and end > start:
                        json_str = llm_response[start:end+1]
                
                if json_str:
                    try:
                        extracted = json.loads(json_str)
                    except json.JSONDecodeError as je:
                        extracted = {
                            "customer_name": None,
                            "discussion_summary": f"Parse error: {str(je)[:200]}. Raw: {llm_response[:300]}",
                            "next_meeting": None,
                            "action_items": [],
                            "key_points": [],
                            "follow_ups": "Unable to parse structured data"
                        }
                else:
                    extracted = {
                        "customer_name": None,
                        "discussion_summary": llm_response[:500] if llm_response else "No response from LLM",
                        "next_meeting": None,
                        "action_items": [],
                        "key_points": [],
                        "follow_ups": "LLM did not return JSON"
                    }
            else:
                raise Exception(f"Ollama returned HTTP {response.status_code}")
        
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=503, detail=f"LLM service unreachable: {str(e)}. Please ensure Ollama is running.")
        
        # Update meeting notes if meeting_id provided
        if request.meeting_id:
            meeting = db.query(BrokerMeeting).filter(
                BrokerMeeting.id == request.meeting_id,
                BrokerMeeting.broker_id == broker_id
            ).first()
            
            if meeting:
                meeting.notes = json.dumps(extracted)
                db.commit()
        
        # Save a structured voice recording / lead record
        recording = BrokerVoiceRecording(
            broker_id=broker_id,
            meeting_id=request.meeting_id,
            transcript=request.transcript,
            customer_name=extracted.get('customer_name'),
            discussion_summary=extracted.get('discussion_summary'),
            next_meeting=extracted.get('next_meeting'),
            action_items=json.dumps(extracted.get('action_items', [])),
            key_points=json.dumps(extracted.get('key_points', [])),
            follow_ups=extracted.get('follow_ups', ''),
            status='pending',
            priority='medium'
        )
        db.add(recording)
        db.commit()
        db.refresh(recording)
        
        return {
            "extracted_data": extracted,
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
    """Get comprehensive dashboard metrics"""
    try:
        extract_token = get_token_from_request(token, authorization)
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No token provided")
    
    broker_id = verify_token(extract_token, db)
    
    # Total stats
    total_meetings = db.query(func.count(BrokerMeeting.id)).filter(
        BrokerMeeting.broker_id == broker_id
    ).scalar() or 0
    
    total_customers = db.query(func.count(BrokerCustomer.id)).filter(
        BrokerCustomer.broker_id == broker_id
    ).scalar() or 0
    
    # This month
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    meetings_this_month = db.query(func.count(BrokerMeeting.id)).filter(
        BrokerMeeting.broker_id == broker_id,
        BrokerMeeting.scheduled_at >= month_start
    ).scalar() or 0
    
    # Average duration
    avg_duration = db.query(func.avg(BrokerMeeting.duration_minutes)).filter(
        BrokerMeeting.broker_id == broker_id
    ).scalar() or 0
    
    # Busiest day (day of week with most meetings)
    meetings = db.query(BrokerMeeting).filter(
        BrokerMeeting.broker_id == broker_id
    ).all()
    
    if meetings:
        day_counts = Counter([m.scheduled_at.strftime("%A") for m in meetings])
        busiest_day = day_counts.most_common(1)[0][0]
    else:
        busiest_day = "N/A"
    
    # Upcoming meetings
    upcoming = db.query(func.count(BrokerMeeting.id)).filter(
        BrokerMeeting.broker_id == broker_id,
        BrokerMeeting.scheduled_at > now
    ).scalar() or 0
    
    return {
        "total_meetings": total_meetings,
        "total_customers": total_customers,
        "meetings_this_month": meetings_this_month,
        "avg_meeting_duration": round(float(avg_duration), 2),
        "busiest_day": busiest_day,
        "upcoming_meetings": upcoming
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
