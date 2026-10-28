FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend frontend
RUN useradd -m app && chown -R app /app
USER app
WORKDIR /app/backend
ENV ENV=prod PORT=10000
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import os,urllib.request as u;u.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/healthz',timeout=4)" || exit 1
# one worker on purpose: chat state and the reminder loop live in-process. --proxy-headers because Render terminates TLS in front of us.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
