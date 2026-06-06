FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system app \
    && adduser --system --ingroup app --home /home/app app

WORKDIR /app

COPY service/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app service/app ./app
COPY --chown=app:app ai_job_search ./ai_job_search

RUN mkdir -p /app/data \
    && chown app:app /app/data

USER app

EXPOSE 3927

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; port=os.environ.get('APP_PORT', '3927'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3)"]

CMD ["python", "-m", "app.main"]
