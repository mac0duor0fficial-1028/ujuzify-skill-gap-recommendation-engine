# syntax=docker/dockerfile:1

# ---- Stage 1: build the web interface -------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY app/package.json app/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY app/ ./
RUN npm run build

# ---- Stage 2: API + static UI ---------------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000 APP_ENV=production
WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY src ./src
COPY api ./api
COPY monitoring ./monitoring
COPY scripts ./scripts
COPY database ./database
COPY data ./data
COPY --from=ui /ui/dist ./app/dist

# Non-root runtime user; only the feedback and processed-data folders are writable.
RUN useradd --system --uid 10001 --no-create-home ujuzify \
    && mkdir -p /app/data/feedback /app/data/processed /app/data/raw \
    && chown -R ujuzify:ujuzify /app/data/feedback /app/data/processed /app/data/raw
USER ujuzify

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "monitoring/healthcheck.py"]

CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]
