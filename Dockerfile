FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MEDIA_ROOT=/app/media
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev libjpeg-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/media
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    DATABASE_URL=sqlite:///tmp/fake.db \
    SECRET_KEY=build-only \
    python manage.py collectstatic --noinput
EXPOSE 8000
CMD python manage.py migrate --fake-initial --noinput && python manage.py ensure_quality_schema && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
