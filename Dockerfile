# syntax=docker/dockerfile:1
# Production image: the API serves the built React client on the same origin.

# ── 1. Client build ─────────────────────────────────────────────────────────
FROM node:26-alpine AS client-build
WORKDIR /client
COPY client/package.json client/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY client/ ./
RUN npm run build

# ── 2. Python dependencies (cached unless pyproject.toml changes) ──────────
FROM python:3.12-slim AS python-deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# ── 3. Runtime ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENVIRONMENT=production \
    DEBUG=false \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    HF_HOME=/home/app/.cache/huggingface
WORKDIR /app
RUN useradd --create-home --uid 10001 app
COPY --from=python-deps /opt/venv /opt/venv
COPY --chown=app:app app ./app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app --from=client-build /client/dist ./client/dist
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1
# Migrations run as a separate release step (Railway pre-deploy, or the compose
# command), never inside every web replica. --proxy-headers trusts the platform
# proxy for client IPs, which the rate limiter relies on.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]
