FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PPE_DB_PATH=/app/runtime-db/ppe_system.db \
    PPE_LOCAL_PROCESSING=false
WORKDIR /app
COPY backend/requirements-api.txt /tmp/requirements-api.txt
RUN pip install --no-cache-dir -r /tmp/requirements-api.txt \
    && groupadd --gid 10001 ppe \
    && useradd --uid 10001 --gid ppe --no-create-home ppe \
    && mkdir -p /app/runtime-db /app/storage /app/.private_media \
    && chown -R ppe:ppe /app
COPY --chown=ppe:ppe backend /app/backend
USER ppe
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).close()"
CMD ["python", "-m", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
