# Slim Python base matching the local version (3.14).
FROM python:3.14-slim

# Keep the image small and the logs unbuffered so Render shows output live.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so code edits do not invalidate the cached layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# $PORT is supplied by the host; 8000 is the local default.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
