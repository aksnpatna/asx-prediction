FROM python:3.11-slim

WORKDIR /app

# Install ffmpeg for audio conversion
RUN apt-get update && apt-get install -y ffmpeg && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install setuptools/wheel first
RUN pip install --no-cache-dir setuptools wheel

# Install faster-whisper (uses CTranslate2, 4-8x faster than openai-whisper on CPU)
RUN pip install --no-cache-dir faster-whisper==1.0.3

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8001

CMD ["python", "main.py"]
