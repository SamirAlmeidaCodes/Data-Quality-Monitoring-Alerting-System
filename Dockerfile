FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DQ_CONFIG=/app/config/rules.yaml \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config ./config
COPY src ./src
COPY data/sample ./data/sample

EXPOSE 8000

# The process stays root so a mounted ./data directory is writable for the
# SQLite monitoring database on a normal developer checkout.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/quality/status')"

CMD ["python", "-m", "dqmonitor", "serve", "--host", "0.0.0.0", "--port", "8000"]
