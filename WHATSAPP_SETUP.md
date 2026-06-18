# WhatsApp Voice Pipeline — Setup Guide

## Cost

| Component | Cost |
|---|---|
| Meta WhatsApp Cloud API | **$0** — 1,000 free service conversations/month |
| n8n (self-hosted Docker) | **$0** |
| faster-whisper CPU STT | **$0** |
| DeepSeek via Ollama | **$0** |
| **Total for 3-4 test users** | **$0/month** |

---

## Step 1 — Add secrets to your `.env` file

```bash
# WhatsApp
WHATSAPP_ACCESS_TOKEN=your_token_here
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_VERIFY_TOKEN=pick_any_random_string_e.g._secret_abc123

# n8n admin password (change this!)
N8N_USER=admin
N8N_PASSWORD=a_strong_password_here
N8N_ENCRYPTION_KEY=exactly-32-characters-long-keyxx
```

---

## Step 2 — Meta Business Setup (one-time, ~20 min)

### 2a. Create Meta Developer App

1. Go to <https://developers.facebook.com>
2. **My Apps → Create App → Business** type
3. Give it a name (e.g. "BrokerCRM")
4. Add **WhatsApp** product to the app

### 2b. Get your credentials

In the WhatsApp → API Setup page:

- **Temporary access token** — copy this (valid 24h for testing)
- **Phone Number ID** — copy this (permanent)
- For production: create a **Permanent System User Token** via Business Manager → System Users

### 2c. Add a test phone number

Meta provides a free sandbox test number. You can add up to 5 recipient phone numbers for testing.

Go to **WhatsApp → API Setup → To** and add your WhatsApp number.

---

## Step 3 — Start the stack

```bash
# Bring up everything including n8n
sg docker -c "docker compose up -d"

# Wait ~60 seconds, then check
sg docker -c "docker compose ps"
```

All services including `broker-n8n` should be healthy.

---

## Step 4 — Set up the Cloudflare DNS

Add a CNAME in your Cloudflare DNS dashboard:

| Name | Target | Proxy |
|---|---|---|
| `n8n` | `<your-tunnel-id>.cfargotunnel.com` | Proxied |

The cloudflared config already routes `n8n.akstest.win → n8n:5678`.

---

## Step 5 — Import n8n workflows

1. Open <https://n8n.akstest.win> in your browser
2. Login with the user/password from your `.env`
3. Click **+** → **Import from file** — import these three files in order:
   - `n8n-workflows/01-whatsapp-webhook-verify.json`
   - `n8n-workflows/02-whatsapp-voice-ingest.json`
   - `n8n-workflows/03-whatsapp-send-replies.json`
4. Activate all three workflows (toggle the switch)

### Set n8n environment variables

In n8n Settings → **Variables** (or use `$env` in workflows):

| Variable | Value |
|---|---|
| `WHATSAPP_ACCESS_TOKEN` | Your Meta token |
| `WHATSAPP_PHONE_NUMBER_ID` | Your phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Same value as in `.env` |
| `BROKER_API_URL` | `http://broker-backend:8001` |

---

## Step 6 — Register webhook with Meta

In **Meta Developer Console → WhatsApp → Configuration → Webhook**:

| Field | Value |
|---|---|
| Callback URL | `https://n8n.akstest.win/webhook/whatsapp` |
| Verify token | The value you set as `WHATSAPP_VERIFY_TOKEN` |

Click **Verify and Save**. Meta will send a GET request to n8n; Workflow 01 responds with the challenge — should show ✅.

Then under **Webhook fields**, subscribe to: `messages`

---

## Step 7 — Test it

1. On your WhatsApp, send a **voice message** to your test number
2. Within 15 seconds, check the broker dashboard at <https://broker.akstest.win> → **📱 WhatsApp** tab
3. The message will go:
   - `pending` → `processing` → `done` → `replied`
4. You'll receive a WhatsApp reply with the AI-generated summary

---

## Architecture Reference

```
Customer WhatsApp voice note
        │
        ▼
Meta Cloud API (webhook POST)
        │
        ▼
n8n Workflow 02 — extracts: from_phone, media_id, message_id
        │
        ▼
broker-backend POST /api/whatsapp/incoming
(stores job, status=pending)
        │
        ▼
Background thread (every 15s):
  1. Downloads audio from Meta CDN
  2. faster-whisper → transcript
  3. DeepSeek via Ollama → structured JSON
  4. Marks status=done
        │
        ▼
n8n Workflow 03 (every 60s):
  → GET /api/whatsapp/jobs?status=done&replied=false
  → POST reply via Meta Graph API
  → POST /api/whatsapp/mark-replied/{id}
        │
        ▼
Customer receives WhatsApp reply ✅
```

---

## Broker Dashboard

Log into <https://broker.akstest.win> and click the **📱 WhatsApp** tab to:
- See all incoming voice messages and their status
- View AI-extracted summaries, action items, key details
- Manually trigger reply sending (if auto-send is off)
- Retry failed jobs

---

## Troubleshooting

### Webhook verification fails
- Check `WHATSAPP_VERIFY_TOKEN` matches in both n8n Variables and `.env`
- Check Workflow 01 is active in n8n
- Check `https://n8n.akstest.win/webhook/whatsapp` is reachable from the internet

### Jobs stuck in `pending`
```bash
sg docker -c "docker compose logs broker-backend --tail=50" | grep -i whatsapp
```
- If `WHATSAPP_ACCESS_TOKEN not configured` — add the token to `.env` and rebuild
- If download fails — token may be expired (use permanent system user token)

### faster-whisper model not loading
```bash
sg docker -c "docker compose logs broker-backend --tail=30" | grep -i whisper
```
The tiny model downloads on first start (~39 MB). Add more memory if needed.

### LLM extraction returns error
```bash
sg docker -c "docker logs asx-ollama --tail=20"
```
Make sure deepseek-r1:7b (or your configured model) is downloaded in Ollama:
```bash
sg docker -c "docker exec asx-ollama ollama pull deepseek-r1:7b"
```
