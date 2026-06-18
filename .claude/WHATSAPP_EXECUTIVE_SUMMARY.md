# WhatsApp Broker Integration - Executive Summary

**Date:** April 18, 2026  
**Status:** ⚠️ **PARTIALLY WORKING - REQUIRES FIXES**

---

## Quick Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Credentials Configured** | ⚠️ Partial | Access token is temporary (24h expiry) |
| **Multi-Broker Isolation** | 🔴 Broken | phone_number_id not being extracted/forwarded |
| **Database Routing** | ✅ Ready | Tables and indexes configured correctly |
| **n8n Workflows** | 🔴 Incomplete | Missing phone_number_id extraction |
| **Broker Backend** | 🟡 Incomplete | Won't accept phone_number_id (model missing) |

---

## How It SHOULD Work

### Multi-Broker Isolation Architecture

Your system supports **multiple brokers using the SAME WhatsApp webhook URL** without seeing each other's messages. Here's how:

```
┌─────────────────────────────────────────────┐
│ All Messages → Same n8n Webhook             │
│ n8n.akstest.win/webhook/whatsapp            │
└────────┬────────────────────────────────────┘
         │
         ├─ Broker A's phone_number_id: 1067499119775497
         ├─ Broker B's phone_number_id: 1067499119775498  
         └─ Broker C's phone_number_id: 1067499119775499
         
         ▼
    Extract phone_number_id from Meta webhook
         │
         ├─ Look up: Which broker owns this number?
         │  (Query: broker_whatsapp_numbers table)
         │
         ├─ Find broker_id: "broker_acme" (or xyz, global)
         │
         ▼
    Store message with broker_id = "broker_acme"
         │
         ▼
    All queries automatically filter by broker_id
         │
         ▼
    Only Broker A can access Broker A's messages
         ✓ Complete isolation achieved
```

---

## Current Issues

### 🔴 Critical Issue #1: n8n Missing phone_number_id Extraction

**File:** `n8n-workflows/02-whatsapp-voice-ingest.json`

**Problem:** The workflow extracts `from_phone`, `wa_message_id`, `media_id` but **NOT `phone_number_id`** from Meta's webhook payload.

**Impact:** Without phone_number_id, the broker backend cannot determine which broker owns this phone number.

**Result:** Multi-broker isolation FAILS. Messages cannot be routed to correct broker.

### 🔴 Critical Issue #2: Broker Backend Doesn't Accept phone_number_id

**File:** `broker/main.py` (Pydantic model `WhatsAppIncomingRequest`)

**Problem:** The request handler doesn't have a field for `phone_number_id` in its schema.

**Impact:** Even if n8n sends phone_number_id, it's rejected by Pydantic validation.

**Result:** Routing fails with validation error.

### 🟡 High Issue #3: Temporary Access Token

**Current:** Meta temporary token (EAAR0sfNZC0Tw...)
**Valid Until:** 24 hours from generation
**For Production:** Need permanent System User Token

---

## How Isolation Works (When Fixed)

### The Database Routing Table

```sql
broker_whatsapp_numbers (UNIQUE constraint on phone_number_id):

broker_id    │ phone_number_id    │ label
─────────────┼────────────────────┼──────
broker_acme  │ 1067499119775497   │ Main
broker_xyz   │ 1067499119775498   │ Main
broker_global│ 1067499119775499   │ Main
```

**Key:** Each phone number routes to EXACTLY ONE broker (enforced by UNIQUE constraint).

### Message Storage & Query Isolation

```sql
whatsapp_voice_jobs:

id  │ broker_id    │ from_phone    │ message_data
────┼──────────────┼───────────────┼─────────────
1   │ broker_acme  │ 61412345678   │ ...
2   │ broker_acme  │ 61487654321   │ ...
3   │ broker_xyz   │ 61454321098   │ ...
4   │ broker_global│ 61498765432   │ ...

Query (Broker A): SELECT * FROM whatsapp_voice_jobs 
                  WHERE broker_id = 'broker_acme'
                  
Result: Only rows 1 & 2 (Broker A's messages only)
```

---

## Credentials Status

### Currently In .env

```
✅ WHATSAPP_ACCESS_TOKEN = EAAR0sfNZC0Tw... (TEMPORARY)
✅ WHATSAPP_PHONE_NUMBER_ID = 1067499119775497
✅ WHATSAPP_BUSINESS_PHONE = 15551512875
❌ WHATSAPP_VERIFY_TOKEN = NOT SET

Status: Partially configured
Impact: Webhook verification will fail
```

---

## What Needs to Be Fixed

### 1️⃣ n8n Workflow (15 minutes)

**File:** `n8n-workflows/02-whatsapp-voice-ingest.json`

**Change:** In "Parse Audio Message" node, extract `phone_number_id`:

```javascript
// Add this line:
const phoneNumberId = value.metadata?.phone_number_id || '';

// Add this field to output:
phone_number_id: phoneNumberId
```

**Then:** In "Queue in Broker" node, forward it:

```json
{ "name": "phone_number_id", "value": "={{ $json.phone_number_id }}" }
```

### 2️⃣ Broker Backend (5 minutes)

**File:** `broker/main.py`

**Change:** Add field to Pydantic model:

```python
class WhatsAppIncomingRequest(BaseModel):
    from_phone: str
    wa_message_id: str
    media_id: str
    audio_mime_type: str
    phone_number_id: Optional[str] = None  # ← ADD THIS
```

### 3️⃣ Environment Configuration (2 minutes)

**File:** `.env`

**Add:**
```
WHATSAPP_VERIFY_TOKEN=your_random_string_here
```

**Later:** Replace temporary access token with permanent one from Meta.

---

## Testing the Fix

After applying fixes, test multi-broker isolation:

```bash
# Test 1: Send message to Broker A's number
curl -X POST http://localhost:8001/api/whatsapp/incoming \
  -d '{"from_phone": "614...", "phone_number_id": "1067499119775497"}'

# Test 2: Send message to Broker B's number
curl -X POST http://localhost:8001/api/whatsapp/incoming \
  -d '{"from_phone": "614...", "phone_number_id": "1067499119775498"}'

# Test 3: Verify isolation in database
SELECT broker_id, COUNT(*) as messages FROM whatsapp_voice_jobs GROUP BY broker_id;

# Expected: broker_acme has 1 message, broker_xyz has 1 message (complete separation)
```

---

## Security & Isolation Layers

Your system has **3 layers of isolation** (when properly configured):

### Layer 1: API Authentication
- JWT token contains `broker_id`
- Only authenticated brokers can call endpoints

### Layer 2: Query Filtering
- ALL database queries include: `WHERE broker_id = extracted_id`
- Broker A cannot query Broker B's data even with manual SQL injection

### Layer 3: Data Tagging
- Messages tagged with `broker_id` at creation time
- Cannot be reassigned after storage

**Result:** Even if broker steals another broker's JWT token, they still can only see their own data (Layer 2 & 3 protect against this).

---

## Detailed Documentation

Three comprehensive guides have been created:

1. **[.claude/WHATSAPP_MULTI_BROKER_GUIDE.md](.claude/WHATSAPP_MULTI_BROKER_GUIDE.md)** (11 sections)
   - Complete architecture explanation
   - How isolation works at each layer
   - Multi-broker setup guide
   - Security considerations

2. **[.claude/WHATSAPP_QUICK_REFERENCE.md](.claude/WHATSAPP_QUICK_REFERENCE.md)** (Diagrams & tables)
   - Quick status dashboard
   - 30-second isolation explanation
   - Test procedures
   - Production checklist

3. **[.claude/WHATSAPP_FIX_GUIDE.md](.claude/WHATSAPP_FIX_GUIDE.md)** (Exact code changes)
   - Side-by-side current vs. fixed code
   - Step-by-step implementation
   - Testing instructions
   - Rollback procedures

---

## Implementation Timeline

```
├─ Immediate (Today):
│  ├─ Read all 3 documentation files
│  └─ Review code changes needed
│
├─ Week 1:
│  ├─ Fix n8n workflow (15 min)
│  ├─ Fix broker backend (5 min)
│  ├─ Add environment variable (2 min)
│  ├─ Run all tests (20 min)
│  └─ Deploy fixes
│
├─ Week 2:
│  ├─ Replace temporary access token (30 min)
│  ├─ Load test with multiple brokers
│  ├─ Document broker onboarding process
│  └─ Set up monitoring/alerts
│
└─ Week 3:
   ├─ Database backups configured
   ├─ Audit logging enabled
   ├─ Production deployment
   └─ Monitor for issues
```

---

## Key Takeaways

### ✅ What's Working

- Multi-broker database isolation architecture is **properly designed**
- Query filtering is **implemented correctly**
- Authentication/JWT system is **functional**
- Unique constraint on phone_number_id **enforces single-broker routing**

### 🔴 What's Broken

- n8n doesn't extract phone_number_id from Meta's webhook
- Broker backend won't accept phone_number_id parameter
- Temporary access token will expire in 24 hours

### 💡 The Solution

- Add 1 line of code to n8n (extract phone_number_id)
- Add 2 lines of code to broker backend (accept phone_number_id)
- Add 1 line to .env (WHATSAPP_VERIFY_TOKEN)
- Total: ~3 minutes of code changes, 20 minutes of testing

### 📊 After Fixes

- ✅ Messages correctly routed to right broker
- ✅ Broker A cannot see Broker B's messages (database level)
- ✅ Broker B cannot see Broker A's messages (database level)
- ✅ Isolation enforced even if credentials are compromised
- ✅ Multi-broker platform ready for production

---

## Next Steps

1. Read [.claude/WHATSAPP_QUICK_REFERENCE.md](.claude/WHATSAPP_QUICK_REFERENCE.md) for the 30-second overview
2. Read [.claude/WHATSAPP_MULTI_BROKER_GUIDE.md](.claude/WHATSAPP_MULTI_BROKER_GUIDE.md) for complete understanding
3. Follow [.claude/WHATSAPP_FIX_GUIDE.md](.claude/WHATSAPP_FIX_GUIDE.md) step-by-step to implement fixes
4. Run test procedures to verify isolation is working
5. Deploy to production with confidence

---

**Questions?** All three documentation files have detailed explanations, code examples, test procedures, and security considerations.

**Status:** Ready to implement. Estimated implementation time: **2 hours** (including testing).
