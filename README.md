# ASX Stock Predictor

A web application that predicts ASX share prices for the next 3 months using statistical analysis and OpenAI LLM.

## Features

- 📈 **Real-time ASX Data** - Fetches live stock prices using yfinance
- 🔮 **3-Month Price Prediction** - Statistical forecasting with confidence intervals  
- 🤖 **AI-Powered Analysis** - OpenAI GPT integration for investment insights
- 📊 **Interactive Dashboard** - Beautiful React UI with charts
- 📉 **Weekly Tracking** - Compare predicted vs actual performance
- 🐳 **Docker Ready** - Easy deployment with Docker Compose

## Tech Stack

- **Frontend:** React 18, Vite, Chart.js, Framer Motion
- **Backend:** FastAPI, Python 3.11
- **Data:** yfinance, pandas, scikit-learn
- **AI:** OpenAI API (GPT-4)
- **Deployment:** Docker, Nginx

## Quick Start

### Prerequisites

- Docker & Docker Compose
- OpenAI API Key (for LLM predictions)

### Setup

1. **Clone and configure:**
   ```bash
   cp .env.example .env
   # Edit .env and add your OpenAI API key
   ```

2. **Run with Docker:**
   ```bash
   docker-compose up --build
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

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/shares` | Get all tracked shares |
| POST | `/api/shares?symbol=BHP` | Add a share |
| DELETE | `/api/shares/BHP` | Remove a share |
| GET | `/api/shares/BHP` | Get share details with prediction |
| GET | `/api/search?query=BHP` | Search ASX shares |
| GET | `/api/health` | Health check |

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

### Optional: Without OpenAI

The app works without an API key - it will use statistical analysis only and show a simplified prediction without AI insights.

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
