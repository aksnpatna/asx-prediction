# WhatsApp Multi-Broker Integration Guide

**Last Updated:** April 18, 2026  
**Status:** ⚠️ REQUIRES CONFIGURATION

---

## Part 1: WhatsApp Credentials Status

### Current Environment Configuration

From `.env` file analysis:

```
✅ WHATSAPP_ACCESS_TOKEN = EAAR0sfNZC0TwBRP6odGMvJwCZCwZChTJ6ZATlrbYPmP4QPt2UHX9Hue35OpZBtQrQEMZAW0WdiuyLIOtXGZAg50ygjwBDy1lSNwiC8lju97I8rNGTlutMNOZADdX0PRwBnVSA3bHGjv5XywgbGFBtd0BT4LDKwvUGJZAvZB7xZChqlfZCPEO0YJayzaV1TbEWoMj62OaM0Wuzp0yd9yGQbASTDC7dK4GJaJHkpy2A6VQta4Jisujn4ZA1psdPNMByN5dEQ3gYVrkTf8Mc0SvpMYGSmf7DZBRDTIdgJ53CPRlkpowZDZD
✅ WHATSAPP_PHONE_NUMBER_ID = 1067499119775497
✅ WHATSAPP_BUSINESS_PHONE = 15551512875
❓ WHATSAPP_VERIFY_TOKEN = NOT SET IN .env (Check docker-compose.yml)
```

### Credential Status Analysis

| Credential | Status | Details |
|-----------|--------|---------|
| **Access Token** | ⚠️ TEMPORARY | Starts with `EAAR0sf...` — Meta temporary token (valid 24h for testing) |
| **Phone Number ID** | ✅ CONFIGURED | `1067499119775497` — Meta sandbox phone ID |
| **Business Phone** | ✅ CONFIGURED | `15551512875` — Test/sandbox number |
| **Verify Token** | ⚠️ MISSING | Must be set for webhook verification |

### To Make Credentials Permanent (Production)

1. **Access Token** (currently temporary, valid 24h only)
   ```
   Go to: Meta Business Manager → System Users → Create permanent token
   Valid for: Until revoked (no expiration)
   Replace in .env: WHATSAPP_ACCESS_TOKEN=<new-permanent-token>
   ```

2. **Verify Token**
   ```env
   # In .env, add:
   WHATSAPP_VERIFY_TOKEN=your-random-secret-string-here
   # Example: WHATSAPP_VERIFY_TOKEN=broker_webhook_secret_abc123_xyz789
   ```

3. **Test Phone Numbers**
   ```
   Go to: Meta WhatsApp → API Setup → To
   Add your actual phone numbers to send test messages
   (up to 5 numbers in sandbox, unlimited for production)
   ```

---

## Part 2: Multi-Broker Isolation Architecture

### The Problem Being Solved

```
Single WhatsApp Number (1 phone_number_id)
         ↓
Multiple Brokers (Broker A, Broker B, Broker C)
         ↓
Each broker needs TOTAL ISOLATION:
  ❌ Broker A cannot see Broker B's customer messages
  ❌ Broker A cannot see Broker B's voice recordings
  ❌ Broker A cannot trigger Broker B's workflows
```

### The Solution: phone_number_id → broker_id Mapping

Your system uses **BrokerWhatsAppNumber** table as the routing layer:

#### Database Schema (PostgreSQL)

```sql
CREATE TABLE broker_whatsapp_numbers (
    id SERIAL PRIMARY KEY,
    broker_id VARCHAR(50) NOT NULL INDEX,
    phone_number_id VARCHAR(100) NOT NULL UNIQUE,
    label VARCHAR(100),  -- e.g., "Main", "Secondary"
    created_at TIMESTAMP DEFAULT NOW()
);

-- Example data:
| id | broker_id    | phone_number_id      | label     |
|----|--------------|--------------------|-----------|
| 1  | broker_acme  | 1067499119775497    | Main      |
| 2  | broker_xyz   | 1067499119775498    | Secondary |
| 3  | broker_global| 1067499119775499    | Main      |
```

**Key Constraint:** `UNIQUE(phone_number_id)` — each phone number routes to exactly ONE broker.

#### Example Multi-Broker Setup

```
Company: ACME Brokerage
├─ Broker ID: broker_acme
│  └─ WhatsApp Number: 1067499119775497 (Meta phone_number_id)
│     └─ Customers: Only ACME's customers see this number
│     └─ Messages: Stored with broker_id=broker_acme
│
Company: XYZ Brokerage
├─ Broker ID: broker_xyz
│  └─ WhatsApp Number: 1067499119775498 (different Meta phone_number_id)
│     └─ Customers: Only XYZ's customers see this number
│     └─ Messages: Stored with broker_id=broker_xyz
│
Company: Global Finance
├─ Broker ID: broker_global
│  └─ WhatsApp Number: 1067499119775499 (another phone_number_id)
│     └─ Customers: Only Global's customers see this number
│     └─ Messages: Stored with broker_id=broker_global
```

---

## Part 3: Message Routing Flow & Isolation

### How the App Knows Which Broker Sent a Message

#### Step 1: Meta Webhook Arrives at n8n

```
Meta WhatsApp → n8n (n8n.akstest.win/webhook/whatsapp)
Payload structure:
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "...",
    "changes": [{
      "value": {
        "metadata": {
          "phone_number_id": "1067499119775497"  ← ROUTING KEY
        },
        "messages": [{
          "from": "61412345678",      ← Customer phone
          "id": "wamid_...",          ← Message ID
          "type": "audio",
          "audio": {"id": "media_id", ...}
        }]
      }
    }]
  }]
}
```

#### Step 2: n8n Parses & Forwards to Broker Backend

**Workflow: 02-whatsapp-voice-ingest.json**

```
n8n:02 "Parse Audio Message"
├─ Extract: from_phone, wa_message_id, media_id
├─ BUT: Currently MISSING phone_number_id from output
└─ Forward to: POST /api/whatsapp/incoming
```

**⚠️ ISSUE FOUND:** The n8n workflow doesn't extract `phone_number_id` from Meta's payload!

**Current n8n payload sent to broker:**
```json
{
  "from_phone": "61412345678",
  "wa_message_id": "wamid_...",
  "media_id": "media_...",
  "audio_mime_type": "audio/ogg; codecs=opus"
  // ❌ Missing: phone_number_id
}
```

**Should be:**
```json
{
  "from_phone": "61412345678",
  "wa_message_id": "wamid_...",
  "media_id": "media_...",
  "audio_mime_type": "audio/ogg; codecs=opus",
  "phone_number_id": "1067499119775497"  ✅ ADD THIS
}
```

#### Step 3: Broker Backend Routes Message

**Code: broker/main.py → whatsapp_webhook_post()**

```python
def whatsapp_webhook_post(request: Request):
    payload = await request.json()
    
    # Route to broker via phone_number_id
    phone_number_id = payload.get("phone_number_id", "")
    wa_broker_id = _lookup_broker_by_phone_number_id(phone_number_id)
    
    # Create WhatsApp job with broker_id attached
    return await _save_wa_job(
        from_phone=payload.get("from_phone"),
        wa_message_id=payload.get("wa_message_id"),
        media_id=payload.get("media_id"),
        broker_id=wa_broker_id,  ← ISOLATION KEY
        received_phone_number_id=phone_number_id
    )
```

#### Step 4: Lookup Broker ID from Phone Number

```python
def _lookup_broker_by_phone_number_id(phone_number_id: str) -> Optional[str]:
    """Find which broker_id has this Meta phone_number_id registered."""
    db = SessionLocal()
    try:
        # Query: which broker owns this phone number?
        rec = db.query(BrokerWhatsAppNumber).filter(
            BrokerWhatsAppNumber.phone_number_id == phone_number_id
        ).first()
        return rec.broker_id if rec else None  # Returns: "broker_acme", "broker_xyz", etc.
    finally:
        db.close()
```

#### Step 5: Message Stored with broker_id

**Database storage (PostgreSQL):**

```sql
INSERT INTO whatsapp_voice_jobs (
    from_phone,
    wa_message_id,
    media_id,
    broker_id,              ← ISOLATION
    received_phone_number_id,
    message_type,
    status,
    created_at
) VALUES (
    '61412345678',
    'wamid_ABC123...',
    'media_XYZ789...',
    'broker_acme',          ← Only broker_acme sees this message
    '1067499119775497',
    'audio',
    'pending',
    NOW()
);
```

---

## Part 4: Complete Message Flow Diagram

### Multi-Broker Isolation in Action

```
┌─────────────────────────────────────────────────────────────────┐
│                     WhatsApp Business Platform                   │
│                                                                   │
│  Broker ACME                    │  Broker XYZ                    │
│  Phone: +1 (555) 151-2875       │  Phone: +1 (555) 151-2876    │
│  Number ID: 1067499119775497    │  Number ID: 1067499119775498 │
│                                                                   │
│  Customers send voice msg to ACME number                         │
│  ├─ Meta receives on 1067499119775497                            │
│  └─ Posts webhook with phone_number_id=1067499119775497         │
│                                 │                                │
│                                 ▼                                │
│                    ┌──────────────────────┐                      │
│                    │   n8n (Cloudflare    │                      │
│                    │   n8n.akstest.win)   │                      │
│                    └──────────────────────┘                      │
│                           │                                       │
│                           ├─ Parse payload                        │
│                           ├─ Extract phone_number_id             │
│                           └─ POST /api/whatsapp/incoming         │
│                                 │                                │
│                                 ▼                                │
│              ┌────────────────────────────────────┐              │
│              │   Broker Backend (FastAPI)         │              │
│              │   Port 8001                        │              │
│              │                                    │              │
│              │  _lookup_broker_by_phone_number_id│              │
│              │  phone_number_id → broker_id      │              │
│              │                                    │              │
│              │  Result: "broker_acme"            │              │
│              └────────────────────────────────────┘              │
│                                 │                                │
│                                 ▼                                │
│              ┌────────────────────────────────────┐              │
│              │   PostgreSQL (whatsapp_voice_jobs) │              │
│              │   + broker_whatsapp_numbers        │              │
│              │                                    │              │
│              │  Store with broker_id = broker_acme
│              │                                    │              │
│              │  ✓ Query filtering: always         │              │
│              │    WHERE broker_id = 'broker_acme' │              │
│              │  ✓ Only ACME sees ACME messages   │              │
│              │  ✓ Only XYZ sees XYZ messages     │              │
│              └────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Part 5: Data Isolation Enforcement

### Query-Level Isolation (Backend Always Filters)

**All queries in broker/main.py include broker_id filter:**

```python
# Customer queries
customers = db.query(BrokerCustomer).filter(
    BrokerCustomer.broker_id == broker_id  ← ALWAYS
).all()

# WhatsApp jobs
jobs = db.query(WhatsAppVoiceJob).filter(
    WhatsAppVoiceJob.broker_id == broker_id,  ← ALWAYS
    WhatsAppVoiceJob.status == "pending"
).all()

# Meetings
meetings = db.query(BrokerMeeting).filter(
    BrokerMeeting.broker_id == broker_id  ← ALWAYS
).all()

# Voice recordings
recordings = db.query(BrokerVoiceRecording).filter(
    BrokerVoiceRecording.broker_id == broker_id  ← ALWAYS
).all()
```

### Three-Layer Isolation

```
Layer 1: API Authentication
├─ JWT Token contains broker_id
├─ Verify token → extract broker_id
└─ All requests scoped to extracted broker_id

Layer 2: Database Query Filtering
├─ Every query includes: WHERE broker_id = $1
├─ Broker A cannot query Broker B's data even if they try
└─ SQL-level protection

Layer 3: Data Storage
├─ WhatsApp messages tagged with broker_id at creation
├─ Cannot be reassigned to another broker
└─ Permanent isolation
```

---

## Part 6: How to Add a New Broker

### Step 1: Register Broker in Database

```sql
-- Create broker user
INSERT INTO broker_users (broker_id, email, username, password)
VALUES ('broker_xyz', 'broker@xyzfin.com', 'xyz_admin', 'hashed_password');

-- Add broker WhatsApp number (after getting new phone_number_id from Meta)
INSERT INTO broker_whatsapp_numbers (broker_id, phone_number_id, label)
VALUES ('broker_xyz', '1067499119775498', 'Main');

-- (Optional) Create broker profile
INSERT INTO broker_profiles (broker_id)
VALUES ('broker_xyz');
```

### Step 2: Get New Phone Number ID from Meta

1. Go to Meta Business Manager
2. Create new WhatsApp number or use existing one
3. Get the `phone_number_id` (e.g., `1067499119775498`)
4. Add to `broker_whatsapp_numbers` table

### Step 3: Test Isolation

```bash
# Test Broker A can only see Broker A data
curl -X GET http://localhost:8001/api/broker/customers \
  -H "Authorization: Bearer <broker_a_token>" \
  # Result: Only Broker A's customers

# Test Broker B cannot see Broker A data
curl -X GET http://localhost:8001/api/broker/customers \
  -H "Authorization: Bearer <broker_b_token>" \
  # Result: Only Broker B's customers (different data)

# Verify message routing
# Send WhatsApp message to Broker A's number → stored with broker_id=broker_a
# Send WhatsApp message to Broker B's number → stored with broker_id=broker_b
```

---

## Part 7: Critical Issues & Fixes

### 🔴 Issue #1: n8n Workflow Missing phone_number_id

**File:** n8n-workflows/02-whatsapp-voice-ingest.json

**Problem:**
```javascript
// Current n8n code extracts:
results.push({
  json: {
    from_phone: msg.from,
    wa_message_id: msg.id,
    media_id: msg.audio.id,
    audio_mime_type: msg.audio.mime_type,
    // ❌ MISSING: phone_number_id
  }
});
```

**Fix:**
```javascript
// Updated n8n code should extract from metadata:
const phoneNumberId = value.metadata?.phone_number_id || "";
results.push({
  json: {
    from_phone: msg.from,
    wa_message_id: msg.id,
    media_id: msg.audio.id,
    audio_mime_type: msg.audio.mime_type,
    phone_number_id: phoneNumberId,  ✅ ADD THIS
  }
});
```

### 🔴 Issue #2: broker/main.py WhatsAppIncomingRequest Missing phone_number_id

**File:** broker/main.py (line ~2507)

**Problem:**
```python
class WhatsAppIncomingRequest(BaseModel):
    from_phone: str
    wa_message_id: str
    media_id: str
    audio_mime_type: str
    # ❌ MISSING: phone_number_id: Optional[str]
```

**Fix:**
```python
class WhatsAppIncomingRequest(BaseModel):
    from_phone: str
    wa_message_id: str
    media_id: str
    audio_mime_type: str
    phone_number_id: Optional[str] = None  ✅ ADD THIS
```

### 🔴 Issue #3: Bearer Token Extraction in n8n

**File:** n8n-workflows/03-whatsapp-send-replies.json

**Problem:** n8n sends requests to `/api/whatsapp/jobs` without authentication

**Fix:** Add Bearer token to GET request headers

---

## Part 8: Security Checklist

- [ ] **JWT_SECRET** — Changed from default `broker-secret-key-change-in-production`
- [ ] **WHATSAPP_VERIFY_TOKEN** — Set to strong random value (not exposed in code)
- [ ] **WHATSAPP_ACCESS_TOKEN** — Replaced with permanent token (not temporary 24h token)
- [ ] **Database Passwords** — Changed from defaults in production
- [ ] **n8n Authentication** — N8N_BASIC_AUTH enabled with strong password
- [ ] **Broker ID Validation** — All queries verified to include broker_id filter
- [ ] **Phone Number ID Uniqueness** — Confirmed UNIQUE constraint exists in broker_whatsapp_numbers
- [ ] **Message Routing** — Confirmed phone_number_id extraction in both n8n and broker backend
- [ ] **Query Auditing** — Logged all queries to detect isolation breaches
- [ ] **SSL/TLS** — Cloudflare tunnel configured for HTTPS to n8n

---

## Part 9: Testing Multi-Broker Isolation

### Test Case 1: Phone Number Routing

```bash
# Set up: Two brokers with two numbers

# Broker A sends message to phone 1067499119775497
# → Stored with broker_id = broker_acme
# → GET /api/broker/customers returns ACME customers only

# Broker B sends message to phone 1067499119775498
# → Stored with broker_id = broker_xyz
# → GET /api/broker/customers returns XYZ customers only
```

### Test Case 2: Message Isolation

```bash
# Broker A token queries WhatsApp jobs
GET /api/whatsapp/jobs?broker_id=broker_acme
Authorization: Bearer <broker_a_token>

# Should return: Only messages from 1067499119775497
# Should NOT return: Messages from 1067499119775498

# Broker B token queries WhatsApp jobs
GET /api/whatsapp/jobs?broker_id=broker_xyz
Authorization: Bearer <broker_b_token>

# Should return: Only messages from 1067499119775498
# Should NOT return: Messages from 1067499119775497
```

### Test Case 3: Try to Access Another Broker's Data

```bash
# Broker A tries to manually access Broker B's data
curl -X GET http://localhost:8001/api/broker/customers/123 \
  -H "Authorization: Bearer <broker_a_token>" \
  -H "X-Broker-ID: broker_xyz"  ← Spoofed header

# Should fail: Token contains broker_a
# Database query filters: WHERE broker_id = broker_a
# Result: Customer not found (because belongs to broker_xyz)
```

---

## Part 10: Recommendations

### Immediate Actions (Critical)

1. **Fix n8n workflow** — Add phone_number_id extraction to 02-whatsapp-voice-ingest.json
2. **Update broker/main.py** — Add phone_number_id to WhatsAppIncomingRequest
3. **Create PERMANENT access token** — Replace current temporary token
4. **Set WHATSAPP_VERIFY_TOKEN** — Add to .env with strong random value
5. **Test isolation** — Run multi-broker test cases

### Short Term (Weeks 1-2)

1. **Database backups** — Regular PostgreSQL backups of all broker data
2. **Audit logging** — Log all API requests with broker_id for compliance
3. **Rate limiting** — Prevent brute force attacks on /api/whatsapp/webhook
4. **Message retention policy** — Define how long WhatsApp jobs are stored

### Long Term (Months)

1. **Multi-tenancy dashboard** — Admin panel to manage brokers
2. **WhatsApp API v20+** — Update from v19 to latest API
3. **End-to-end encryption** — Encrypt sensitive data in PostgreSQL
4. **SOC2/ISO compliance** — Document isolation controls for audits

---

## Part 11: Reference Architecture

### Complete Message Flow with Isolation

```
                        ┌──────────────────┐
                        │ Meta WhatsApp    │
                        │ Server           │
                        └────────┬─────────┘
                                 │
                ┌────────────────┴────────────────┐
                │                                 │
         Broker A                          Broker B
         (number: 1067...)                 (number: 1067...)
                │                                 │
                ▼                                 ▼
        ┌──────────────┐                 ┌──────────────┐
        │ n8n Webhook  │                 │ n8n Webhook  │
        │ (same URL    │                 │ (same URL    │
        │  for both)   │                 │  for both)   │
        └──────┬───────┘                 └──────┬───────┘
               │                                │
               └────────────────┬───────────────┘
                                │
                    Extract phone_number_id
                                │
                ┌───────────────▼──────────────┐
                │  /api/whatsapp/webhook       │
                │  (broker-backend:8001)       │
                │                              │
                │  _lookup_broker_by_id()      │
                │  broker_acme ←─┐             │
                │                │ Mapping     │
                │  broker_xyz  ←─┘  Table      │
                └───────────────┬──────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
    ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
    │ Store Job      │  │ Store Job      │  │ Store Job      │
    │ broker_id:     │  │ broker_id:     │  │ broker_id:     │
    │ "broker_acme"  │  │ "broker_xyz"   │  │ "broker_global"│
    │                │  │                │  │                │
    │ Isolated from  │  │ Isolated from  │  │ Isolated from  │
    │ all others     │  │ all others     │  │ all others     │
    └─────┬──────────┘  └─────┬──────────┘  └─────┬──────────┘
          │                   │                   │
          ▼                   ▼                   ▼
    Only broker_acme   Only broker_xyz    Only broker_global
    can query/view     can query/view     can query/view
    their messages     their messages     their messages
```

---

**Document Status:** Complete  
**Next Steps:** Implement n8n fixes and test multi-broker isolation
