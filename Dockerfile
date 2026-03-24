FROM python:3.14-slim AS base

# Install libolm for E2E encryption support
RUN apt-get update && apt-get install -y --no-install-recommends \
        libolm-dev \
        libolm3 \
        gcc \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# ---- builder stage: install dependencies into a venv ----
FROM base AS builder

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ---- final stage ----
FROM base AS final

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create a non-root user to run the bot
RUN useradd --create-home --shell /bin/bash botuser

WORKDIR /app

# Copy the venv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application source
COPY --chown=botuser:botuser \
    bot.py \
    commands.py \
    database.py \
    feed_manager.py \
    llm_client.py \
    scraper.py \
    ./

# Directories for persistent data — mount these as volumes
RUN mkdir -p /data/store && chown -R botuser:botuser /data

USER botuser

# The bot stores its SQLite DB and nio E2E store under /data.
# Mount a named volume at /data to persist state across container restarts.
VOLUME ["/data"]

# Pass the config path as a build arg or override the CMD at runtime.
# The recommended pattern is to mount config.yml into /app/config.yml.
CMD ["python", "bot.py", "/app/config.yml"]
