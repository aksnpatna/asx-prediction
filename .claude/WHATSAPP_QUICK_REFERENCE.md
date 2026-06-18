# WhatsApp Multi-Broker Quick Reference

## Credentials Status Dashboard

```
┌─────────────────────────────────────────────────────────────┐
│ WhatsApp Integration Status                                 │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│ WHATSAPP_ACCESS_TOKEN                                        │
│ Status: ⚠️ TEMPORARY (24h expiry)                            │
│ Current: EAAR0sfNZC0Tw...                                    │
│ Action: Replace with permanent System User Token             │
│                                                               │
│ WHATSAPP_PHONE_NUMBER_ID                                     │
│ Status: ✅ Configured                                       │
│ Current: 1067499119775497                                    │
│                                                               │
│ WHATSAPP_VERIFY_TOKEN                                        │
│ Status: ❌ NOT SET                                           │
│ Action: Add to .env (random string)                          │
│                                                               │
│ WHATSAPP_BUSINESS_PHONE                                      │
│ Status: ✅ Configured                                       │
│ Current: 15551512875                                         │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Multi-Broker Isolation in 30 Seconds

### The Question: How does the app know which broker sent a message?

```
┌──────────────────────────────────────────────────────────────┐
│ Answer: Via phone_number_id → broker_id mapping             │
├──────────────────────────────────────────────────────────────┤
│                                                                │
│ Step 1: Meta sends webhook with phone_number_id              │
│         └─ "Please deliver to phone 1067499119775497"        │
│                                                                │
│ Step 2: n8n forwards to broker backend                        │
│         └─ Includes phone_number_id in request               │
│                                                                │
│ Step 3: Broker backend queries: which broker owns this?       │
│         SELECT broker_id                                      │
│         FROM broker_whatsapp_numbers                          │
│         WHERE phone_number_id = '1067499119775497'            │
│         RESULT: "broker_acme"                                 │
│                                                                │
│ Step 4: Store message with broker_id = "broker_acme"         │
│         INSERT INTO whatsapp_voice_jobs                       │
│         (broker_id, from_phone, wa_message_id, ...)           │
│         VALUES ('broker_acme', ...)                           │
│                                                                │
│ Step 5: Query isolation enforced                              │
│         All queries automatically filter:                     │
│         WHERE broker_id = 'broker_acme'                       │
│         └─ Only Broker A sees Broker A's messages            │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

## Data Isolation Layers

```
┌─────────────────────────────────────────┐
│ Layer 1: JWT Authentication             │
│ Token contains: broker_id               │
│ Example: {"sub": "broker_acme", ...}   │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│ Layer 2: Database Query Filtering       │
│ WHERE broker_id = extracted_broker_id   │
│ Example: WHERE broker_id = 'broker_acme'│
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│ Layer 3: Physical Data Storage          │
│ Messages tagged with broker_id at       │
│ creation time (immutable)               │
└─────────────────────────────────────────┘
```

## Message Flow Diagram (Simplified)

```
┌─ Broker A's Customer ─┐    ┌─ Broker B's Customer ─┐
│  Sends voice msg      │    │  Sends voice msg      │
│  to +1(555)151-2875   │    │  to +1(555)151-2876   │
└─────────┬─────────────┘    └──────────┬────────────┘
          │                             │
          ├─ phone_number_id:           ├─ phone_number_id:
          │  1067499119775497           │  1067499119775498
          │                             │
          ▼                             ▼
     ┌─────────────────────────────────────┐
     │ Meta WhatsApp Webhook               │
     │ (n8n.akstest.win/webhook/whatsapp)  │
     └────────────┬────────────────────────┘
                  │
              n8n:02 parses
           Extracts phone_number_id
                  │
                  ▼
     ┌─────────────────────────────────────┐
     │ Broker Backend                      │
     │ /api/whatsapp/webhook               │
     │ (port 8001)                         │
     │                                     │
     │ _lookup_broker_by_phone_number_id() │
     │ 1067499119775497 → broker_acme      │
     │ 1067499119775498 → broker_xyz       │
     └────────┬────────────────┬───────────┘
              │                │
              ▼                ▼
    ┌──────────────────┐ ┌──────────────────┐
    │ Store with       │ │ Store with       │
    │ broker_id:       │ │ broker_id:       │
    │ broker_acme      │ │ broker_xyz       │
    │                  │ │                  │
    │ Query filter:    │ │ Query filter:    │
    │ WHERE            │ │ WHERE            │
    │ broker_id=       │ │ broker_id=       │
    │ 'broker_acme'    │ │ 'broker_xyz'     │
    │                  │ │                  │
    │ ✓ Isolated       │ │ ✓ Isolated       │
    │ ✗ A cannot see   │ │ ✗ B cannot see  │
    │   B's messages   │ │   A's messages   │
    └──────────────────┘ └──────────────────┘
```

## Critical Issues Summary

| Issue | Severity | Location | Fix |
|-------|----------|----------|-----|
| n8n not extracting phone_number_id | 🔴 Critical | 02-whatsapp-voice-ingest.json | Add phone_number_id to JSON output |
| Broker backend doesn't accept phone_number_id | 🔴 Critical | broker/main.py line ~2507 | Add phone_number_id field to Pydantic model |
| Temporary access token | 🟡 High | .env | Replace with permanent System User Token |
| WHATSAPP_VERIFY_TOKEN missing | 🟡 High | .env | Set to random secure string |

## How to Test Isolation

### Test 1: Verify Phone Number Mapping
```bash
# Check database mapping
SELECT * FROM broker_whatsapp_numbers;

# Should show:
# broker_acme    → 1067499119775497
# broker_xyz     → 1067499119775498
```

### Test 2: Verify Query Isolation
```bash
# Query as Broker A
curl -X GET http://localhost:8001/api/broker/customers \
  -H "Authorization: Bearer <token_broker_a>"
# Result: Only Broker A customers

# Query as Broker B  
curl -X GET http://localhost:8001/api/broker/customers \
  -H "Authorization: Bearer <token_broker_b>"
# Result: Only Broker B customers (completely different list)
```

### Test 3: Verify Message Routing
```bash
# Send message to Broker A's number (1067499119775497)
# Check database:
SELECT broker_id FROM whatsapp_voice_jobs 
WHERE received_phone_number_id = '1067499119775497';
# Result: broker_acme

# Send message to Broker B's number (1067499119775498)
# Check database:
SELECT broker_id FROM whatsapp_voice_jobs 
WHERE received_phone_number_id = '1067499119775498';
# Result: broker_xyz
```

## Architecture Overview

```
Single Webhook → Multiple Brokers (No Cross-Contamination)

  n8n Webhook URL (1 endpoint)
         ↓
    Accepts all messages
         ↓
   Extract phone_number_id
         ↓
  Database lookup table
  (phone_number_id → broker_id)
         ↓
    Store with broker_id
         ↓
  Query filtering
  (always WHERE broker_id = X)
         ↓
  Complete Isolation
  (Broker A cannot access Broker B's data)
```

## Key Tables for Multi-Broker

### broker_whatsapp_numbers (Routing Table)
```sql
┌──────┬───────────────┬─────────────────────┬────────┐
│ id   │ broker_id     │ phone_number_id     │ label  │
├──────┼───────────────┼─────────────────────┼────────┤
│ 1    │ broker_acme   │ 1067499119775497    │ Main   │
│ 2    │ broker_xyz    │ 1067499119775498    │ Main   │
│ 3    │ broker_global │ 1067499119775499    │ Main   │
└──────┴───────────────┴─────────────────────┴────────┘

⚠️ UNIQUE(phone_number_id) → Each number routes to exactly 1 broker
```

### whatsapp_voice_jobs (Message Storage with Isolation)
```sql
┌────┬────────────────┬─────────┬────────┬────────────┐
│ id │ broker_id      │ from... │ status │ created_at │
├────┼────────────────┼─────────┼────────┼────────────┤
│ 1  │ broker_acme    │ 614...  │ done   │ 2024-01-01 │
│ 2  │ broker_acme    │ 614...  │ pending│ 2024-01-02 │
│ 3  │ broker_xyz     │ 614...  │ done   │ 2024-01-01 │
│ 4  │ broker_global  │ 614...  │ pending│ 2024-01-02 │
└────┴────────────────┴─────────┴────────┴────────────┘

All queries automatically filter:
WHERE broker_id = 'broker_acme' (or xyz, or global)
```

## Production Readiness Checklist

- [ ] Replace temporary access token with permanent token
- [ ] Set WHATSAPP_VERIFY_TOKEN to strong random value
- [ ] Fix n8n workflow to extract phone_number_id
- [ ] Update broker/main.py to accept phone_number_id
- [ ] Test multi-broker isolation with real data
- [ ] Verify query filtering on all endpoints
- [ ] Set up database backups
- [ ] Enable audit logging for compliance
- [ ] Load test with multiple brokers simultaneously
- [ ] Document broker onboarding process

---

**Created:** April 18, 2026  
**Status:** ⚠️ REQUIRES FIXES (see Critical Issues above)
