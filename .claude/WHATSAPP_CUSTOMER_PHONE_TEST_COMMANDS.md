# WhatsApp Customer Phone Routing - Quick Test Commands

**Save this file for quick testing reference**

---

## 1️⃣ Restart Broker Backend

```bash
cd /home/aksai/projects/asx-prediction

# Restart the broker service
docker compose restart broker-backend

# Wait for health check (should show "healthy" in ~10 seconds)
docker compose ps broker-backend

# View logs if needed
docker logs broker-backend | tail -50
```

---

## 2️⃣ Authenticate Broker

```bash
# Get JWT token for broker_acme
TOKEN_A=$(curl -s -X POST http://localhost:8001/api/broker/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "broker@acme.com",
    "password": "your_password"
  }' | jq -r '.access_token')

echo "Broker A Token: $TOKEN_A"

# Get JWT token for broker_xyz (if you have second broker)
TOKEN_B=$(curl -s -X POST http://localhost:8001/api/broker/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "broker@xyz.com",
    "password": "your_password"
  }' | jq -r '.access_token')

echo "Broker B Token: $TOKEN_B"
```

---

## 3️⃣ Link Customer Phones

```bash
# Link customer 1 to broker_acme
curl -X POST http://localhost:8001/api/broker/customer-phone/link \
  -H "Authorization: Bearer $TOKEN_A" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_phone": "61412345678",
    "customer_name": "John Smith"
  }'

# Link customer 2 to broker_acme
curl -X POST http://localhost:8001/api/broker/customer-phone/link \
  -H "Authorization: Bearer $TOKEN_A" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_phone": "61487654321",
    "customer_name": "Jane Doe"
  }'

# Link customer 3 to broker_xyz
curl -X POST http://localhost:8001/api/broker/customer-phone/link \
  -H "Authorization: Bearer $TOKEN_B" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_phone": "61498765432",
    "customer_name": "Tom Johnson"
  }'
```

---

## 4️⃣ List Customer Phones

```bash
# List customers for broker_acme
echo "Broker A customers:"
curl -s -X GET http://localhost:8001/api/broker/customer-phone/list \
  -H "Authorization: Bearer $TOKEN_A" | jq '.'

# List customers for broker_xyz
echo "Broker B customers:"
curl -s -X GET http://localhost:8001/api/broker/customer-phone/list \
  -H "Authorization: Bearer $TOKEN_B" | jq '.'
```

---

## 5️⃣ Verify Database Isolation

```bash
# Connect to PostgreSQL
psql -h localhost -U asx_user -d asx

# Check if table was created
SELECT * FROM broker_customer_phones;

# Should show:
# id | broker_id    | customer_phone | customer_name
# ---|--------------|----------------|---------------
# 1  | broker_acme  | 61412345678    | John Smith
# 2  | broker_acme  | 61487654321    | Jane Doe
# 3  | broker_xyz   | 61498765432    | Tom Johnson

# Check indexes were created
\d broker_customer_phones

# Check unique constraint
SELECT constraint_name FROM information_schema.table_constraints 
WHERE table_name = 'broker_customer_phones' AND constraint_type = 'UNIQUE';
```

---

## 6️⃣ Send Test WhatsApp Message

```bash
# Send from customer 61412345678 (linked to broker_acme)
# to your WhatsApp phone number +1 (555) 151-2875

# This will trigger Meta's webhook which posts to:
# POST /api/whatsapp/webhook

# Then verify the message was routed correctly:
psql -h localhost -U asx_user -d asx

SELECT id, broker_id, from_phone, message_type, status, created_at 
FROM whatsapp_voice_jobs 
WHERE from_phone = '61412345678'
ORDER BY created_at DESC LIMIT 5;

# Should show: broker_id = 'broker_acme'
```

---

## 7️⃣ Unlink Customer Phone

```bash
# Unlink customer from broker_acme
curl -X DELETE http://localhost:8001/api/broker/customer-phone/61412345678 \
  -H "Authorization: Bearer $TOKEN_A"

# Verify it's removed
curl -s -X GET http://localhost:8001/api/broker/customer-phone/list \
  -H "Authorization: Bearer $TOKEN_A" | jq '.'
```

---

## 🔍 Verify Multi-Broker Isolation

### Test 1: Verify Broker A only sees Broker A's customers

```bash
curl -s -X GET http://localhost:8001/api/broker/customer-phone/list \
  -H "Authorization: Bearer $TOKEN_A" | jq '.'

# Output should have ONLY broker_acme customers:
# [
#   {"id": 1, "customer_phone": "61412345678", "customer_name": "John Smith", ...},
#   {"id": 2, "customer_phone": "61487654321", "customer_name": "Jane Doe", ...}
# ]
```

### Test 2: Verify Broker B only sees Broker B's customers

```bash
curl -s -X GET http://localhost:8001/api/broker/customer-phone/list \
  -H "Authorization: Bearer $TOKEN_B" | jq '.'

# Output should have ONLY broker_xyz customers:
# [
#   {"id": 3, "customer_phone": "61498765432", "customer_name": "Tom Johnson", ...}
# ]
```

### Test 3: Verify messages route to correct broker

```bash
psql -h localhost -U asx_user -d asx

-- Send message from 61412345678 (broker_acme's customer)
-- Check it routes to broker_acme
SELECT broker_id, from_phone, status FROM whatsapp_voice_jobs 
WHERE from_phone = '61412345678' LIMIT 1;
-- Should show: broker_id = 'broker_acme'

-- Send message from 61498765432 (broker_xyz's customer)
-- Check it routes to broker_xyz
SELECT broker_id, from_phone, status FROM whatsapp_voice_jobs 
WHERE from_phone = '61498765432' LIMIT 1;
-- Should show: broker_id = 'broker_xyz'
```

---

## 🧪 Test Error Scenarios

### Test: Try to link duplicate customer

```bash
# Try to link same customer twice
curl -X POST http://localhost:8001/api/broker/customer-phone/link \
  -H "Authorization: Bearer $TOKEN_A" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_phone": "61412345678",
    "customer_name": "John Smith"
  }'

# Should return: status = "already_linked"
```

### Test: Try to unlink non-existent customer

```bash
curl -X DELETE http://localhost:8001/api/broker/customer-phone/61999999999 \
  -H "Authorization: Bearer $TOKEN_A"

# Should return: 404 Not Found
```

### Test: Try to list without authentication

```bash
curl -X GET http://localhost:8001/api/broker/customer-phone/list

# Should return: 401 Unauthorized
```

---

## 📊 Database Queries for Verification

### See all customer phone links

```sql
SELECT id, broker_id, customer_phone, customer_name, created_at 
FROM broker_customer_phones 
ORDER BY broker_id, created_at;
```

### See messages from specific customer

```sql
SELECT id, broker_id, from_phone, message_type, status, created_at 
FROM whatsapp_voice_jobs 
WHERE from_phone = '61412345678' 
ORDER BY created_at DESC;
```

### Count messages per broker

```sql
SELECT broker_id, COUNT(*) as message_count 
FROM whatsapp_voice_jobs 
GROUP BY broker_id 
ORDER BY message_count DESC;
```

### See unassigned messages (if any)

```sql
SELECT id, from_phone, message_type, status, created_at 
FROM whatsapp_voice_jobs 
WHERE broker_id = 'unassigned' 
ORDER BY created_at DESC;
```

---

## 🚀 Quick Start (All Commands)

```bash
# 1. Restart
docker compose restart broker-backend && sleep 10

# 2. Get tokens
TOKEN_A=$(curl -s -X POST http://localhost:8001/api/broker/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"broker@acme.com","password":"password"}' | jq -r '.access_token')

TOKEN_B=$(curl -s -X POST http://localhost:8001/api/broker/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"broker@xyz.com","password":"password"}' | jq -r '.access_token')

# 3. Link customers
curl -X POST http://localhost:8001/api/broker/customer-phone/link \
  -H "Authorization: Bearer $TOKEN_A" \
  -H "Content-Type: application/json" \
  -d '{"customer_phone":"61412345678","customer_name":"John"}' && echo ""

curl -X POST http://localhost:8001/api/broker/customer-phone/link \
  -H "Authorization: Bearer $TOKEN_B" \
  -H "Content-Type: application/json" \
  -d '{"customer_phone":"61498765432","customer_name":"Tom"}' && echo ""

# 4. List
curl -s -X GET http://localhost:8001/api/broker/customer-phone/list \
  -H "Authorization: Bearer $TOKEN_A" | jq '.'

curl -s -X GET http://localhost:8001/api/broker/customer-phone/list \
  -H "Authorization: Bearer $TOKEN_B" | jq '.'

# 5. Verify database
psql -h localhost -U asx_user -d asx \
  -c "SELECT broker_id, customer_phone, customer_name FROM broker_customer_phones;"
```

---

## 📋 Checklist

- [ ] Broker backend restarted successfully
- [ ] Both brokers authenticated and tokens obtained
- [ ] Customers linked to correct brokers
- [ ] Customer list shows only own broker's customers
- [ ] Database shows correct isolation
- [ ] WhatsApp message routes to correct broker
- [ ] Error scenarios tested
- [ ] Ready for production deployment

---

**Generated:** April 18, 2026
