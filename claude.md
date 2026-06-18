# ASX Prediction Workspace - Project Status & Issues

**Last Updated:** April 18, 2026  
**Workspace Root:** `/home/aksai/projects/asx-prediction`

---

## Overview

This is a comprehensive ASX (Australian Securities Exchange) stock prediction platform with multiple integrated services running in Docker containers. The system provides real-time ASX data analysis, 3-month price predictions, AI-powered insights, and a broker-backend system with WhatsApp integration.

---

## Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    ASX Prediction Platform                  │
└─────────────────────────────────────────────────────────────┘
                              │
                ┌─────────────┼─────────────┐
                │             │             │
          [Frontend]     [Broker Frontend] [n8n Workflows]
          React/Vite     React/Vite        WhatsApp Integration
                │             │             │
                └─────────────┼─────────────┘
                              │
                        ┌─────┴─────┐
                        │           │
                    [Backend]  [Broker Backend]
                    FastAPI    FastAPI
                        │           │
                        └─────┬─────┘
                              │
                    ┌─────────┼─────────┐
                    │         │         │
                [PostgreSQL] [Ollama]  [n8n]
                (DB)         (Local)    (Workflows)
                             (LLM)
```

---

## Running Services (Docker Containers)

### 1. **asx-db** (PostgreSQL 16)
- **Container:** `asx-db`
- **Image:** `postgres:16-alpine`
- **Port:** 5432
- **Database:** `asx` (configurable via `POSTGRES_DB`)
- **User:** `asx_user` (configurable via `POSTGRES_USER`)
- **Password:** `asx_password` (configurable via `POSTGRES_PASSWORD`)
- **Memory Limit:** 512MB
- **Volume:** `postgres_data:/var/lib/postgresql/data`
- **Status:** ✅ Core database for all applications
- **Health Check:** PostgreSQL ready check every 10s

### 2. **asx-backend** (ASX Prediction Engine)
- **Container:** `asx-backend`
- **Build Context:** `./backend`
- **Framework:** FastAPI (Python 3.11)
- **Port:** 8000
- **Memory Limit:** 2GB (reservation: 512MB)
- **Depends On:** PostgreSQL (`asx-db`)
- **Status:** ✅ Main prediction engine and API server
- **Environment Variables:**
  - `DATABASE_URL`: PostgreSQL connection string
  - `OPENAI_API_KEY`: OpenAI API key (optional)
  - `OPENAI_MODEL`: gpt-4.1 (default)
  - `GROQ_API_KEY`: Groq API key (optional)
  - `GROQ_MODEL`: llama-3.3-70b-versatile (default)
  - `LOCAL_LLM_URL`: http://ollama:11434/v1
  - `LOCAL_LLM_MODEL`: deepseek-r1:7b
  - `LLM_PROVIDER_ORDER`: local,groq,openai (fallback order)
  - `JWT_SECRET`: JWT authentication secret
  - `FRED_API_KEY`: Federal Reserve Economic Data API key
- **Health Check:** HTTP GET to `/` every 30s

### 3. **asx-frontend** (ASX Dashboard UI)
- **Container:** `asx-frontend`
- **Build Context:** `./frontend`
- **Technology:** React 18 + Vite
- **Port:** 80 (main app)
- **Memory Limit:** 256MB (reservation: 128MB)
- **Depends On:** `asx-backend`
- **Status:** ✅ Main user-facing dashboard
- **Features:**
  - Real-time stock charts (Chart.js)
  - 14-day tracking dashboard
  - AI-powered analysis chat
  - Multi-user access with JWT authentication

### 4. **broker-backend** (Broker Integration Engine)
- **Container:** `broker-backend`
- **Build Context:** `./broker`
- **Framework:** FastAPI (Python 3.11)
- **Port:** 8001
- **Memory Limit:** 2GB (reservation: 512MB)
- **Depends On:** PostgreSQL (`asx-db`)
- **Status:** ✅ WhatsApp integration & lead management
- **Environment Variables:**
  - `DATABASE_URL`: PostgreSQL connection
  - `WHATSAPP_ACCESS_TOKEN`: Meta Cloud API token
  - `WHATSAPP_PHONE_NUMBER_ID`: WhatsApp Business phone number ID
  - `WHATSAPP_BUSINESS_PHONE`: Business phone number
  - `WHATSAPP_VERIFY_TOKEN`: Webhook verification token
  - `LOCAL_LLM_URL`: http://ollama:11434/v1
  - `LOCAL_LLM_MODEL`: deepseek-r1:7b
- **Health Check:** HTTP GET to `/health` every 15s

### 5. **broker-frontend** (Broker Dashboard UI)
- **Container:** `broker-frontend`
- **Build Context:** `./broker-frontend`
- **Technology:** React + Vite
- **Port:** 8080
- **Memory Limit:** 256MB (reservation: 128MB)
- **Depends On:** `broker-backend`
- **Status:** ✅ Broker management dashboard
- **Features:**
  - Customer/lead management
  - Meeting scheduling
  - WhatsApp inbox
  - Voice recorder integration
  - All-leads dashboard

### 6. **asx-ollama** (Local LLM Engine)
- **Container:** `asx-ollama`
- **Image:** `ollama/ollama:latest`
- **Port:** 11434
- **Memory Limit:** 6GB (reservation: 2GB)
- **Volume:** `ollama_data:/root/.ollama`
- **Status:** ✅ Local LLM inference engine
- **Configuration:**
  - Model: `deepseek-r1:7b` (configured)
  - OpenAI-compatible API endpoint
  - Parallel requests: 1
  - Max loaded models: 1
  - Keep-alive: 24h
- **Purpose:** Provides local AI inference without external API dependency
- **Health Check:** `ollama list` every 30s

### 7. **broker-n8n** (Workflow Automation)
- **Container:** `broker-n8n`
- **Image:** `docker.n8n.io/n8nio/n8n:latest`
- **Port:** 5678
- **Memory Limit:** 512MB (reservation: 256MB)
- **Depends On:** PostgreSQL (`asx-db`)
- **Status:** ✅ WhatsApp workflow automation
- **Environment Variables:**
  - `N8N_HOST`: n8n.akstest.win (Cloudflare tunnel)
  - `N8N_PROTOCOL`: https
  - `DB_TYPE`: postgresdb
  - `DB_POSTGRESDB_HOST`: db
  - `DB_POSTGRESDB_SCHEMA`: n8n
  - `BROKER_API_URL`: http://broker-backend:8001
  - `GENERIC_TIMEZONE`: Australia/Sydney
- **Volumes:** `n8n_data:/home/node/.n8n`
- **Health Check:** Wget to `/healthz` endpoint
- **Purpose:** Automated WhatsApp message workflows, lead capture, reply generation

---

## Database Schema

### PostgreSQL Configuration
- **Version:** 16 (Alpine - lightweight)
- **Port:** 5432
- **Database Name:** `asx` (default)
- **Backup:** Persistent volume `postgres_data`
- **Schemas Used:**
  - `public` - Main application data (users, shares, predictions, tracking)
  - `n8n` - n8n workflow automation data (managed separately)

### Key Tables (Estimated)
- `users` - User accounts with JWT authentication
- `shares` - ASX share symbols and metadata
- `predictions` - 3-month price predictions
- `tracking_windows` - 14-day tracking buckets
- `predictions_daily` - Daily tracking snapshots
- `analysis_cache` - LLM analysis results
- `broker_customers` - Customer CRM data
- `broker_leads` - Lead management
- `broker_meetings` - Meeting scheduler
- `whatsapp_messages` - Message history
- `whatsapp_contacts` - Customer contacts

---

## API Endpoints

### ASX Backend (Port 8000)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/auth/register` | User registration |
| POST | `/api/auth/login` | User login (returns JWT) |
| GET | `/api/auth/me` | Current user profile |
| GET | `/api/shares` | List tracked shares |
| POST | `/api/shares?symbol=BHP` | Add share to tracking |
| DELETE | `/api/shares/BHP` | Remove share |
| GET | `/api/shares/BHP` | Share details + prediction |
| GET | `/api/search?query=BHP` | Search ASX shares |
| GET | `/api/health` | Health check |
| GET | `/docs` | Swagger API documentation |

### Broker Backend (Port 8001)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| POST | `/webhook/whatsapp` | WhatsApp webhook receiver |
| GET | `/customers` | List all customers |
| POST | `/customers` | Add new customer |
| GET | `/leads` | List leads |
| POST | `/leads` | Create lead |
| GET | `/meetings` | List meetings |
| POST | `/meetings` | Schedule meeting |

---

## Current Issues & Status

### ✅ Working
- [x] Multi-container Docker Compose setup
- [x] PostgreSQL persistence working
- [x] JWT authentication on APIs
- [x] ASX data fetching (yfinance)
- [x] Basic price predictions (statistical)
- [x] Ollama local LLM integration
- [x] n8n WhatsApp workflows
- [x] Broker dashboard UI
- [x] LLM fallback chain (local → Groq → OpenAI)

### ⚠️ Potential Issues

#### 1. **Environment Configuration**
- **Issue:** `.env` file needs proper configuration before startup
- **Location:** `/home/aksai/projects/asx-prediction/.env`
- **Required Variables:**
  ```
  POSTGRES_PASSWORD=xxxxx
  OPENAI_API_KEY=sk-xxxxx (optional if using local LLM)
  GROQ_API_KEY=xxxxx (optional fallback)
  JWT_SECRET=long-random-secret-here
  WHATSAPP_ACCESS_TOKEN=xxxxx
  WHATSAPP_PHONE_NUMBER_ID=xxxxx
  ```
- **Status:** ⚠️ Must be configured before `docker compose up`

#### 2. **LLM Provider Availability**
- **Issue:** If Ollama is not running or model not loaded, fallback to Groq/OpenAI
- **Default Fallback Chain:** `local → groq → openai`
- **Status:** ⚠️ Requires API keys for fallback providers
- **Fix:** Configure `LLM_PROVIDER_ORDER` in `.env`

#### 3. **Memory Usage**
- **Issue:** Ollama container reserved 2GB, limited to 6GB
- **Risk:** High memory pressure if model is large (7B+)
- **Status:** ⚠️ Monitor on low-memory systems
- **Docker Desktop Recommendation:** Allocate 16GB+ to Docker

#### 4. **Cloudflare Tunnel Configuration**
- **Issue:** n8n configured for `n8n.akstest.win` domain
- **File:** `/home/aksai/projects/asx-prediction/cloudflared-config.yml`
- **Status:** ⚠️ Requires Cloudflare tunnel setup
- **Note:** Tunnel credentials in `tunnel-creds.json`

#### 5. **JWT Authentication**
- **Issue:** Default JWT_SECRET = `change-this-secret-in-env`
- **Risk:** 🔴 **Security Issue** - Must change before production
- **Fix:** Set strong random secret in `.env`
  ```bash
  JWT_SECRET=$(openssl rand -hex 32)
  ```

#### 6. **FRED API Key**
- **Issue:** Hard-coded FRED API key in `docker-compose.yml`
- **Risk:** 🔴 **Security Issue** - Key exposed in source
- **File:** `docker-compose.yml` line 54
- **Current Value:** `55689724e13717f08cb5c36bf7d20921`
- **Fix:** Move to `.env` and remove from repo

#### 7. **WhatsApp Integration**
- **Issue:** WhatsApp credentials not configured
- **Status:** ⚠️ Feature incomplete without Meta API credentials
- **Required:** 
  - Business Account ID
  - Phone Number ID
  - Access Token (permanent)
  - Webhook verification token

#### 8. **n8n Database Schema**
- **Issue:** n8n uses separate schema (`n8n`) in same PostgreSQL instance
- **Risk:** ⚠️ Potential namespace collision
- **Status:** ✅ Currently isolated, but monitor

#### 9. **Frontend-Backend Communication**
- **Issue:** Frontend hardcoded to `http://localhost:8001` for broker API
- **File:** `broker-frontend/Dockerfile`
- **Status:** ⚠️ Works locally but fails on remote deployments
- **Fix:** Use environment variables or API gateway

#### 10. **CORS Configuration**
- **Issue:** FastAPI backends may need CORS headers
- **Risk:** ⚠️ Frontend requests might be blocked
- **Files:** `backend/main.py`, `broker/main.py`
- **Fix:** Add FastAPI CORS middleware

### 🔴 Known Bugs

#### Bug #1: Dockerfile in Backend
- **File:** `backend/Dockerfile` (line 1)
- **Issue:** Recent commit changed from FastAPI to Streamlit
- **Commit:** `db20e15 - "Fix Dockerfile to run Streamlit app instead of FastAPI"`
- **Impact:** Backend may be running Streamlit instead of FastAPI
- **Status:** 🔴 Critical - Need to verify which framework is correct

#### Bug #2: Database Connection Strings
- **Issue:** Backend uses `psycopg2` but Broker uses `psycopg` (v3)
- **Backend:** `postgresql+psycopg2://...`
- **Broker:** `postgresql://...` (psycopg3 format)
- **Impact:** ⚠️ Version mismatch could cause issues
- **File:** `docker-compose.yml` lines 38 & 68

---

## Project Structure

```
/home/aksai/projects/asx-prediction/
├── backend/                      # ASX Prediction Engine (FastAPI/Streamlit)
│   ├── app.py                    # Main application
│   ├── main.py                   # Entry point
│   ├── requirements.txt           # Python dependencies
│   ├── Dockerfile                 # Backend container build
│   ├── streamlit.toml            # Streamlit config (if used)
│   ├── test_extract.py           # Testing utilities
│   └── data/                     # Data directory
│
├── broker/                        # Broker Integration Engine
│   ├── main.py                   # FastAPI server
│   ├── requirements.txt
│   └── Dockerfile
│
├── broker-frontend/               # Broker Dashboard UI
│   ├── src/                      # React components
│   │   ├── components/           # Reusable components
│   │   │   ├── AllLeads.jsx
│   │   │   ├── Customers.jsx
│   │   │   ├── Dashboard.jsx
│   │   │   ├── Meetings.jsx
│   │   │   ├── VoiceRecorder.jsx
│   │   │   └── WhatsAppInbox.jsx
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── package.json
│   ├── vite.config.js
│   ├── Dockerfile
│   ├── nginx.conf
│   └── index.html
│
├── frontend/                      # ASX Dashboard UI
│   ├── src/
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── package.json
│   ├── vite.config.js
│   ├── Dockerfile
│   ├── nginx.conf
│   └── index.html
│
├── n8n-workflows/                 # WhatsApp Automation Workflows
│   ├── 01-whatsapp-webhook-verify.json
│   ├── 02-whatsapp-voice-ingest.json
│   └── 03-whatsapp-send-replies.json
│
├── plans/                         # Enhancement Plans
│   └── phase5_enhancement_plan.md
│
├── docker-compose.yml             # Container orchestration
├── .env.example                   # Environment template
├── .env                           # Actual environment (⚠️ not in git)
├── SPEC.md                        # Technical specification
├── design.md                      # Product design document
├── README.md                      # User-facing readme
├── WHATSAPP_SETUP.md             # WhatsApp integration guide
│
├── Test Files:
│   ├── test_llm.py               # LLM connectivity test
│   ├── test_llm_provider.py      # LLM provider test
│   ├── test_ollama.py            # Ollama test
│   ├── test_llm_ready.py         # LLM readiness check
│   ├── test_llm_ips.py           # Network debugging
│   ├── test_host_llm.py          # Host LLM test
│   ├── check_llm_final.py        # Final LLM check
│   ├── check_bhp_llm.py          # BHP stock test
│   ├── debug_analyze.py          # API debug tool
│   ├── network_debug.py          # Network debugging
│   └── test_weekly_predictions.py # Weekly pred validation
│
├── Configuration Files:
│   ├── cloudflared-config.yml    # Cloudflare tunnel config
│   ├── daemon.json               # Docker daemon config
│   ├── tunnel-creds.json         # 🔴 Secret credentials file
│   └── cert.pem                  # SSL certificate
│
└── Deployment Scripts:
    ├── setup-docker.sh           # Linux setup
    ├── run-docker.ps1            # PowerShell run (Windows)
    ├── run-docker.bat            # Batch run (Windows)
    ├── fix-network.ps1           # Windows network fix
    ├── fix-portproxy.ps1         # Port proxy setup
    ├── restore-network.ps1       # Restore network
    └── start-asx-app.ps1         # Start app
```

---

## LLM Configuration

### Current Setup
- **Primary Model:** DeepSeek R1 7B
- **Primary Provider:** Local Ollama
- **Fallback Providers:** Groq → OpenAI
- **Endpoint:** http://ollama:11434/v1 (docker internal)

### Environment Variables
```env
LOCAL_LLM_URL=http://ollama:11434/v1
LOCAL_LLM_MODEL=deepseek-r1:7b
LLM_PROVIDER_ORDER=local,groq,openai

# Fallback providers
GROQ_API_KEY=xxxxx
GROQ_MODEL=llama-3.3-70b-versatile
OPENAI_API_KEY=sk-xxxxx
OPENAI_MODEL=gpt-4.1
```

### Model Capabilities
- ✅ Financial analysis (DeepSeek R1 specialized)
- ✅ Chain-of-thought reasoning
- ✅ Structured data handling
- ✅ Long coherent explanations
- ✅ ~7GB VRAM requirement (fits in 6GB container)

---

## Network Configuration

### Port Mappings
```
Host Port  →  Container Port  →  Service
───────────────────────────────────────────
80        →  80              →  asx-frontend (Nginx)
8000      →  8000            →  asx-backend (FastAPI)
8001      →  8001            →  broker-backend (FastAPI)
8080      →  80              →  broker-frontend (Nginx)
5432      →  5432            →  asx-db (PostgreSQL)
11434     →  11434            →  asx-ollama (Ollama)
5678      →  5678            →  broker-n8n (n8n)
```

### DNS Configuration
- **Domain:** `n8n.akstest.win` (Cloudflare tunnel)
- **Local Access:** Use `host.docker.internal` from containers
- **Container-to-Container:** Use service name (e.g., `db`, `ollama`)

### Cloudflare Tunnel
- **Purpose:** Expose n8n without port forwarding
- **Config:** `cloudflared-config.yml`
- **Credentials:** `tunnel-creds.json` (🔴 DO NOT COMMIT)
- **Status:** ⚠️ Requires running `cloudflared` service

---

## Getting Started

### 1. Install Docker
```bash
# Ubuntu/Debian
sudo apt-get install docker.io docker-compose-plugin

# MacOS
brew install docker-desktop
```

### 2. Configure Environment
```bash
cd /home/aksai/projects/asx-prediction
cp .env.example .env
nano .env  # Edit with your API keys and secrets
```

### 3. Start All Services
```bash
docker compose up --build
```

### 4. Access Applications
- **ASX Dashboard:** http://localhost
- **Broker Dashboard:** http://localhost:8080
- **Backend API Docs:** http://localhost:8000/docs
- **Broker API Docs:** http://localhost:8001/docs
- **Ollama API:** http://localhost:11434
- **n8n:** http://localhost:5678 (or https://n8n.akstest.win with tunnel)

### 5. Test LLM Integration
```bash
python test_llm_ready.py
python test_ollama.py
python test_llm_provider.py
```

---

## Troubleshooting

### Container Won't Start
```bash
# Check logs
docker logs asx-backend
docker logs broker-backend
docker logs asx-ollama

# Restart specific container
docker compose restart asx-backend
```

### Database Connection Failed
```bash
# Verify PostgreSQL is running
docker exec asx-db pg_isready

# Check connection string in .env
grep DATABASE_URL .env
```

### LLM Not Responding
```bash
# Check Ollama status
curl http://localhost:11434/api/tags

# Reload model
docker exec asx-ollama ollama pull deepseek-r1:7b
```

### High Memory Usage
```bash
# Monitor container memory
docker stats asx-ollama

# Reduce model size or increase Docker memory allocation
```

---

## Security Checklist

- [ ] Change `JWT_SECRET` to random 32-char hex string
- [ ] Set `POSTGRES_PASSWORD` to strong password
- [ ] Remove API keys from `docker-compose.yml` (use `.env`)
- [ ] Set `WHATSAPP_VERIFY_TOKEN` to unique value
- [ ] Rotate `FRED_API_KEY` if exposed
- [ ] Delete or rotate `tunnel-creds.json` if exposed
- [ ] Enable HTTPS for Cloudflare tunnel
- [ ] Add firewall rules for ports 8000, 8001, 8080
- [ ] Regular database backups of `postgres_data` volume
- [ ] Implement API rate limiting on FastAPI endpoints

---

## Performance Optimization

### Memory Allocation (Recommended)
- **Docker Desktop:** 16GB RAM minimum
- **Ollama Container:** 6GB limit (adjustable)
- **PostgreSQL:** 512MB (suitable for dev)
- **FastAPI Backends:** 2GB each (reservation 512MB)

### Database Optimization
```sql
-- Create indexes on frequently queried columns
CREATE INDEX idx_shares_symbol ON shares(symbol);
CREATE INDEX idx_tracking_user_id ON tracking_windows(user_id);
CREATE INDEX idx_predictions_share_id ON predictions(share_id);
```

### LLM Performance
- Use `deepseek-r1:7b` quantized model (Q4_K_M)
- Set `OLLAMA_KEEP_ALIVE=24h` for persistent model loading
- Limit `OLLAMA_NUM_PARALLEL=1` to reduce memory

---

## Maintenance

### Weekly Tasks
- [ ] Check container health: `docker compose ps`
- [ ] Monitor database size: `docker exec asx-db psql -U asx_user -d asx -c "\l+"`
- [ ] Review error logs: `docker compose logs --tail 100`

### Monthly Tasks
- [ ] Backup PostgreSQL volume
- [ ] Update base images: `docker compose pull`
- [ ] Review API usage metrics
- [ ] Clean unused Docker resources: `docker system prune`

### Quarterly Tasks
- [ ] Rotate API keys
- [ ] Update dependencies
- [ ] Performance audit
- [ ] Security patch review

---

## References

- **SPEC:** [SPEC.md](SPEC.md) - Technical specifications
- **Design:** [design.md](design.md) - Product design
- **Phase 5 Plan:** [plans/phase5_enhancement_plan.md](plans/phase5_enhancement_plan.md)
- **WhatsApp Setup:** [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md)
- **README:** [README.md](README.md) - User documentation

---

**Document Version:** 1.0  
**Last Verified:** April 18, 2026  
**Status:** Active Development
