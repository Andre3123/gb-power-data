# Calibra server image: the API at /v1, the web app at /app, health check at /health.
# Customer ledgers and API-key hashes live in /data, which must be a persistent disk.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# 1. Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# 2. Then our code.
COPY src ./src
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    CALIBRA_HOME=/data \
    PORT=8080

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8080'), timeout=4)" || exit 1

CMD ["calibra", "serve", "--host", "0.0.0.0", "--port", "8080"]
