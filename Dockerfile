# ViDownload on Fly.io — long-running Flask app (ffmpeg + background downloads)
FROM python:3.12-slim

# ffmpeg is required: yt-dlp merges separate video+audio streams into one mp4.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# One worker: download jobs live in that worker's memory, so a second worker
# would not see them (status polls would 404). Threads handle concurrency.
# Timeout 300s: the direct /api/download route streams the file in-request.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "300", "app:app"]
