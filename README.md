# ASX Stock Predictor

A web application that predicts ASX share prices for the next 3 months using statistical analysis and dual LLM providers (local + OpenAI).

## Features

- 📈 **Real-time ASX Data** - Fetches live stock prices using yfinance
- 🔮 **3-Month Price Prediction** - Statistical forecasting with confidence intervals  
- 🤖 **AI-Powered Analysis** - Local LLM with OpenAI fallback for investment insights
- 📊 **Interactive Dashboard** - Beautiful React UI with charts
- 📉 **Weekly Tracking** - Compare predicted vs actual performance
- 🐳 **Docker Ready** - Easy deployment with Docker Compose

## Tech Stack

- **Frontend:** React 18, Vite, Chart.js, Framer Motion
- **Backend:** FastAPI, Python 3.11
- **Database:** PostgreSQL 16
- **Data:** yfinance, pandas, scikit-learn
- **AI:** Local OpenAI-compatible endpoint (for example LM Studio) + OpenAI API
- **Deployment:** Docker, Nginx

## Quick Start

### Prerequisites

- Docker Desktop (or Docker Engine + Compose)
- OpenAI API Key (optional if local LLM is primary)

### Setup

1. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit PostgreSQL password and LLM settings
   ```

2. **Run with Docker:**
   ```bash
   docker compose up --build
   ```

3. **Access the app:**
   - Frontend: http://localhost
   - Backend API: http://localhost:8000
   - API Docs: http://localhost:8000/docs

### Running Locally (Development)

**Backend:**
```bash
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
export OPENAI_API_KEY=your_key  # or set in .env
uvicorn main:app --reload
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

### LLM Provider Order

The backend tries providers in configured order:

- `LLM_PROVIDER_ORDER=local,openai` (default)
- `LLM_PROVIDER_ORDER=openai,local`

Local provider config:
- `LOCAL_LLM_URL`
- `LOCAL_LLM_MODEL`

OpenAI provider config:
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Register a user and return JWT |
| POST | `/api/auth/login` | Login and return JWT |
| GET | `/api/auth/me` | Get current user profile |
| GET | `/api/shares` | Get all tracked shares |
| POST | `/api/shares?symbol=BHP` | Add a share |
| DELETE | `/api/shares/BHP` | Remove a share |
| GET | `/api/shares/BHP` | Get share details with prediction |
| GET | `/api/search?query=BHP` | Search ASX shares |
| GET | `/api/health` | Health check |

All share APIs are user-scoped and require `Authorization: Bearer <token>`.

## Supported ASX Shares

The app includes 50+ major ASX companies including:
- BHP, RIO, FMG (Mining)
- CBA, ANZ, WBC, NAB (Banking)
- WOW, WES, TLS, CSL (Diversified)

You can add any ASX symbol - it will fetch data from Yahoo Finance.

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key for LLM | (empty) |
| `OPENAI_MODEL` | OpenAI model for market reasoning | gpt-4.1 |
| `LOCAL_LLM_URL` | Local OpenAI-compatible endpoint | http://host.docker.internal:1234/v1 |
| `LOCAL_LLM_MODEL` | Local model ID | qwen2.5-7b-instruct |
| `LLM_PROVIDER_ORDER` | Provider fallback order | local,openai |
| `POSTGRES_DB` | PostgreSQL database name | asx |
| `POSTGRES_USER` | PostgreSQL user | asx_user |
| `POSTGRES_PASSWORD` | PostgreSQL password | change_me |

### Optional: Without OpenAI

The app works without an OpenAI key if your local LLM endpoint is available.

## Project Structure

```
├── backend/
│   ├── main.py          # FastAPI application
│   ├── requirements.txt # Python dependencies
│   └── Dockerfile      # Backend container
├── frontend/
│   ├── src/
│   │   ├── App.jsx     # Main React component
│   │   ├── main.jsx    # Entry point
│   │   └── index.css   # Styles
│   ├── package.json
│   ├── vite.config.js
│   ├── nginx.conf      # Nginx config
│   └── Dockerfile      # Frontend container
├── docker-compose.yml
├── .env.example
└── README.md
```

## License

MIT License - Feel free to use and modify!
