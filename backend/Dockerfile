FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fail the BUILD (not the running system) if the dependency set is broken —
# e.g. an unpinned transitive dep pulling a numpy-incompatible pyarrow.
RUN python -c "import sklearn.metrics, lightgbm, joblib, pyarrow, edgar, statsmodels, xgboost, apscheduler, sqlalchemy, psycopg2, fastapi, yfinance; print('dependency smoke test OK')"

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Run FastAPI app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
