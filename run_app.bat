@echo off
REM Run ASX Prediction Engine
REM Make sure LM Studio is running with Qwen 2.5 7B model

cd /d "%~dp0backend"

echo Starting ASX Prediction Engine...
echo.

REM Check if virtual environment exists
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Creating...
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
)

REM Run Streamlit
echo Starting Streamlit on http://localhost:8501
echo Make sure LM Studio is running with Qwen 2.5 7B on port 1234
echo.

.venv\Scripts\streamlit.exe run app.py
