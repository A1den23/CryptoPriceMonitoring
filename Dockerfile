# Multi-stage Dockerfile for Crypto Price Monitoring Bot
# Stage 1: Builder - Compile dependencies
FROM python:3.11-slim AS builder

# Set working directory
WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies to system location (accessible by all users)
RUN pip install --no-cache-dir --target=/install -r requirements.txt

# Stage 2: Runtime - Minimal production image
FROM python:3.11-slim

# Set labels
LABEL maintainer="Crypto Price Monitor"
LABEL description="Cryptocurrency price monitoring bot with Telegram notifications"
LABEL version="2.1"

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set working directory
WORKDIR /app

# Install runtime timezone data so ZoneInfo handles DST correctly
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder to system location
COPY --from=builder /install /usr/local/lib/python3.11/site-packages

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Copy application code
COPY common/ ./common/
COPY monitor/ ./monitor/
COPY bot/ ./bot/
COPY monitor.py .
COPY bot.py .

# Create logs directory with proper permissions
RUN mkdir -p /app/logs && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Health check - verify the active service heartbeat is fresh
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import pathlib,time,sys; c=pathlib.Path('/proc/1/cmdline').read_bytes().replace(b'\\x00', b' '); now=time.time(); ok=any(marker in c and (h:=pathlib.Path(path)).exists() and (now-h.stat().st_mtime)<180 for marker,path in ((b'monitor.py','/tmp/monitor_heartbeat'),(b'bot.py','/tmp/bot_heartbeat'))); sys.exit(0 if ok else 1)" || exit 1

# Default command (can be overridden in docker-compose.yml)
CMD ["python", "monitor.py"]
