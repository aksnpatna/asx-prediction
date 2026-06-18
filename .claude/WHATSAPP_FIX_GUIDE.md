# WhatsApp Multi-Broker Integration - Fix Guide

## Required Fixes to Enable Multi-Broker Isolation

---

## Fix #1: n8n Workflow — Extract phone_number_id

**File:** `n8n-workflows/02-whatsapp-voice-ingest.json`

**Node:** "Parse Audio Message" (Code node)

### Current Code (BROKEN)
```javascript
const body = $input.first().json.body || $input.first().json;
const results = [];

for (const entry of (body.entry || [])) {
  for (const change of (entry.changes || [])) {
    const value = change.value || {};
    for (const msg of (value.messages || [])) {
      if (msg.type === 'audio' && msg.audio?.id) {
        results.push({
          json: {
            from_phone: msg.from,
            wa_message_id: msg.id,
            media_id: msg.audio.id,
            audio_mime_type: msg.audio.mime_type || 'audio/ogg; codecs=opus',
            // ❌ MISSING: phone_number_id
          }
        });
      }
    }
  }
}

if (results.length === 0) {
  return [{ json: { skip: true } }];
}
return results;
```

### Fixed Code (WORKING)
```javascript
const body = $input.first().json.body || $input.first().json;
const results = [];

for (const entry of (body.entry || [])) {
  for (const change of (entry.changes || [])) {
    const value = change.value || {};
    
    // ✅ Extract phone_number_id from Meta's metadata
    const phoneNumberId = value.metadata?.phone_number_id || '';
    
    for (const msg of (value.messages || [])) {
      if (msg.type === 'audio' && msg.audio?.id) {
        results.push({
          json: {
            from_phone: msg.from,
            wa_message_id: msg.id,
            media_id: msg.audio.id,
            audio_mime_type: msg.audio.mime_type || 'audio/ogg; codecs=opus',
            phone_number_id: phoneNumberId,  // ✅ ADDED
          }
        });
      }
    }
  }
}

if (results.length === 0) {
  return [{ json: { skip: true } }];
}
return results;
```

### What Changed
- Added: `const phoneNumberId = value.metadata?.phone_number_id || '';`
- Added field to JSON output: `phone_number_id: phoneNumberId`

### Why This Matters
Without this, the broker backend receives no `phone_number_id`, so it cannot look up which broker owns this phone number. Messages won't be routed correctly.

---

## Fix #2: Broker Backend — Accept phone_number_id Parameter

**File:** `broker/main.py`

**Location:** Around line 2507 (after `WhatsAppIncomingRequest` class definition)

### Current Code (BROKEN)
```python
class WhatsAppIncomingRequest(BaseModel):
    from_phone: str           # "61412345678"
    wa_message_id: str        # Meta wamid
    media_id: str             # Media ID
    audio_mime_type: str      # MIME type
    # ❌ MISSING: phone_number_id
```

### Fixed Code (WORKING)
```python
class WhatsAppIncomingRequest(BaseModel):
    from_phone: str           # "61412345678"
    wa_message_id: str        # Meta wamid
    media_id: str             # Media ID
    audio_mime_type: str      # MIME type
    phone_number_id: Optional[str] = None  # ✅ ADDED — Meta phone_number_id for broker routing
```

### Also Update Webhook Handler

**Location:** Around line 2500 (in `whatsapp_webhook_post()` function)

The webhook handler already looks for `phone_number_id`, so it should work automatically:

```python
@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook_post(request: Request):
    payload = await request.json()
    # ... existing code ...
    
    # This already exists and will use phone_number_id when n8n provides it:
    phone_number_id = payload.get("phone_number_id", "")
    wa_broker_id = _lookup_broker_by_phone_number_id(phone_number_id) if phone_number_id else None
```

### What Changed
- Added: `phone_number_id: Optional[str] = None` to Pydantic model

### Why This Matters
When n8n sends the request with `phone_number_id`, the broker backend needs to accept this parameter. Without it, Pydantic validation fails and the request is rejected.

---

## Fix #3: n8n Workflow — Forward phone_number_id to Broker

**File:** `n8n-workflows/02-whatsapp-voice-ingest.json`

**Node:** "Queue in Broker" (HTTP Request node)

### Current Code (BROKEN)
```json
{
  "parameters": {
    "method": "POST",
    "url": "={{ $env.BROKER_API_URL }}/api/whatsapp/incoming",
    "sendBody": true,
    "bodyParameters": {
      "parameters": [
        { "name": "from_phone", "value": "={{ $json.from_phone }}" },
        { "name": "wa_message_id", "value": "={{ $json.wa_message_id }}" },
        { "name": "media_id", "value": "={{ $json.media_id }}" },
        { "name": "audio_mime_type", "value": "={{ $json.audio_mime_type }}" }
        // ❌ MISSING: phone_number_id parameter
      ]
    },
    "options": { "timeout": 10000 }
  }
}
```

### Fixed Code (WORKING)
```json
{
  "parameters": {
    "method": "POST",
    "url": "={{ $env.BROKER_API_URL }}/api/whatsapp/incoming",
    "sendBody": true,
    "bodyParameters": {
      "parameters": [
        { "name": "from_phone", "value": "={{ $json.from_phone }}" },
        { "name": "wa_message_id", "value": "={{ $json.wa_message_id }}" },
        { "name": "media_id", "value": "={{ $json.media_id }}" },
        { "name": "audio_mime_type", "value": "={{ $json.audio_mime_type }}" },
        { "name": "phone_number_id", "value": "={{ $json.phone_number_id }}" }  // ✅ ADDED
      ]
    },
    "options": { "timeout": 10000 }
  }
}
```

### What Changed
- Added new parameter: `{ "name": "phone_number_id", "value": "={{ $json.phone_number_id }}" }`

### Why This Matters
The "Parse Audio Message" node now extracts `phone_number_id`, but it needs to be forwarded to the broker backend in the HTTP request. Without this, the value is lost in transit.

---

## Fix #4: Environment Configuration

**File:** `.env`

### Add Missing Credentials

```bash
# Add this line (if not already present):
WHATSAPP_VERIFY_TOKEN=your_random_secret_string_here

# Example (use a REAL random string, not this example):
WHATSAPP_VERIFY_TOKEN=broker_webhook_sec_abc123_xyz789_2024

# For production, also add:
# WHATSAPP_ACCESS_TOKEN=<permanent-token-from-meta>
# (Replace the current temporary token with permanent System User Token)
```

### Generate Secure Random Token

```bash
# Linux/Mac:
openssl rand -hex 32

# Example output:
# 7f2d9c1a8b4e6f3d2c1a9b8e7f4d3c2b1a9f8e7d6c5b4a3f2e1d0c9b8a7f6e

# Use this as WHATSAPP_VERIFY_TOKEN in .env
```

---

## Testing the Fixes

### Test 1: Verify n8n Extracts phone_number_id

1. Open n8n dashboard: https://n8n.akstest.win
2. Edit workflow "02-whatsapp-voice-ingest"
3. Click on "Parse Audio Message" node
4. Click "Test" button
5. Check output: should contain `phone_number_id` field

**Expected output:**
```json
{
  "from_phone": "61412345678",
  "wa_message_id": "wamid_ABC123...",
  "media_id": "media_XYZ789...",
  "audio_mime_type": "audio/ogg; codecs=opus",
  "phone_number_id": "1067499119775497"  ← Should appear here
}
```

### Test 2: Verify Broker Backend Accepts Request

```bash
# Send test request to broker backend
curl -X POST http://localhost:8001/api/whatsapp/incoming \
  -H "Content-Type: application/json" \
  -d '{
    "from_phone": "61412345678",
    "wa_message_id": "test_msg_001",
    "media_id": "test_media_001",
    "audio_mime_type": "audio/ogg; codecs=opus",
    "phone_number_id": "1067499119775497"
  }'

# Expected response:
# { "job_id": 123, "status": "pending" }
```

### Test 3: Verify Database Isolation

```bash
# Connect to PostgreSQL
psql -h localhost -U asx_user -d asx

# Check if message was stored with broker_id
SELECT id, broker_id, from_phone, received_phone_number_id 
FROM whatsapp_voice_jobs 
WHERE wa_message_id = 'test_msg_001';

# Expected output:
# id  | broker_id    | from_phone    | received_phone_number_id
# 123 | broker_acme  | 61412345678   | 1067499119775497
```

### Test 4: Verify Multi-Broker Isolation

```bash
# Simulate messages from different brokers

# Message 1: Send to Broker A's number (1067499119775497)
curl -X POST http://localhost:8001/api/whatsapp/incoming \
  -H "Content-Type: application/json" \
  -d '{
    "from_phone": "61412345678",
    "wa_message_id": "msg_a_001",
    "media_id": "media_a_001",
    "audio_mime_type": "audio/ogg; codecs=opus",
    "phone_number_id": "1067499119775497"
  }'

# Message 2: Send to Broker B's number (1067499119775498)
curl -X POST http://localhost:8001/api/whatsapp/incoming \
  -H "Content-Type: application/json" \
  -d '{
    "from_phone": "61412345678",
    "wa_message_id": "msg_b_001",
    "media_id": "media_b_001",
    "audio_mime_type": "audio/ogg; codecs=opus",
    "phone_number_id": "1067499119775498"
  }'

# Verify isolation in database
SELECT broker_id, COUNT(*) as messages 
FROM whatsapp_voice_jobs 
WHERE wa_message_id IN ('msg_a_001', 'msg_b_001') 
GROUP BY broker_id;

# Expected:
# broker_id    | messages
# broker_acme  | 1
# broker_xyz   | 1
```

---

## Implementation Checklist

### Step 1: Update n8n Workflow
- [ ] Edit `n8n-workflows/02-whatsapp-voice-ingest.json`
- [ ] Update "Parse Audio Message" code node with fixed JavaScript
- [ ] Update "Queue in Broker" HTTP node with phone_number_id parameter
- [ ] Test workflow with sample payload
- [ ] Re-deploy in n8n

### Step 2: Update Broker Backend
- [ ] Update `broker/main.py`
- [ ] Add `phone_number_id: Optional[str] = None` to `WhatsAppIncomingRequest`
- [ ] Save file (no other changes needed in handler)
- [ ] Restart docker container: `docker compose restart broker-backend`

### Step 3: Configure Environment
- [ ] Add `WHATSAPP_VERIFY_TOKEN` to `.env`
- [ ] (Optional) Replace temporary access token with permanent token from Meta
- [ ] Restart all containers: `docker compose up -d`

### Step 4: Test All Fixes
- [ ] Run Test 1: n8n extracts phone_number_id
- [ ] Run Test 2: Broker accepts request
- [ ] Run Test 3: Database isolation confirmed
- [ ] Run Test 4: Multi-broker isolation verified

### Step 5: Validate Production Readiness
- [ ] All tests passing
- [ ] Access token is permanent (not temporary)
- [ ] WHATSAPP_VERIFY_TOKEN is set and secure
- [ ] Query filtering confirmed on all broker endpoints
- [ ] Documentation updated

---

## Rollback Instructions

If something breaks after applying fixes:

### Rollback n8n
```bash
# Simply restore the original JSON from git
git checkout n8n-workflows/02-whatsapp-voice-ingest.json

# Or manually revert the Parse Audio Message code
# (remove phone_number_id extraction)
```

### Rollback Broker Backend
```bash
# Restore original Python file
git checkout broker/main.py

# Restart container
docker compose restart broker-backend
```

### Rollback .env
```bash
# Remove WHATSAPP_VERIFY_TOKEN line
# Restore WHATSAPP_ACCESS_TOKEN to original value
```

---

## Verification Commands

```bash
# After all fixes are applied, run these commands to verify:

# 1. Check broker backend is running
curl http://localhost:8001/health

# 2. Check n8n is running
curl http://localhost:5678/healthz

# 3. Verify PostgreSQL connection
docker exec asx-db pg_isready -U asx_user

# 4. Check for any error logs
docker compose logs broker-backend | tail -50
docker logs broker-n8n | tail -50

# 5. Verify database has the mapping table
docker exec asx-db psql -U asx_user -d asx -c "SELECT COUNT(*) FROM broker_whatsapp_numbers;"

# Should return: 1 or more rows
```

---

**Fixes Required:** 3 code changes + 1 env variable  
**Estimated Time:** 15-30 minutes  
**Risk Level:** Low (non-destructive, tested in sandbox first)
**Rollback Difficulty:** Easy (revert files from git)
