FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DQ_CONFIG=/app/config/rules.yaml \
    DQ_ENV=production \
    HOME=/tmp \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 dqmonitor \
    && useradd --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin dqmonitor \
    && mkdir -p /app/data \
    && chown dqmonitor:dqmonitor /app/data

COPY --chown=dqmonitor:dqmonitor config ./config
COPY --chown=dqmonitor:dqmonitor src ./src
COPY --chown=dqmonitor:dqmonitor data/sample ./data/sample

# The image does not copy .env, local databases, or secret files.
USER dqmonitor

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/quality/status')"

# Bind all container interfaces so the published port works. Compose publishes
# that port on 127.0.0.1 only.
CMD ["python", "-m", "dqmonitor", "serve", "--host", "0.0.0.0", "--port", "8000"]
