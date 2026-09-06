# ──────────────────────────────────────────────────────────────
# Stage 1 — build do CSS Tailwind a partir dos templates atuais.
# Isso garante que o tailwind-built.css NUNCA fique desatualizado:
# toda classe (inclusive valores arbitrários como max-h-[88vh]) é
# gerada no deploy, escaneando templates e apps.
# ──────────────────────────────────────────────────────────────
FROM node:20-slim AS cssbuilder
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY tailwind.config.js ./
COPY static/css/tailwind-input.css ./static/css/tailwind-input.css
COPY templates ./templates
COPY apps ./apps
RUN npm run build:css

# ──────────────────────────────────────────────────────────────
# Stage 2 — aplicação Python
# ──────────────────────────────────────────────────────────────
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MEDIA_ROOT=/app/media
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev libjpeg-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Sobrescreve o CSS commitado pelo recém-buildado (sempre em dia com os templates).
COPY --from=cssbuilder /build/static/css/tailwind-built.css ./static/css/tailwind-built.css
RUN mkdir -p /app/media
RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    DATABASE_URL=sqlite:///tmp/fake.db \
    SECRET_KEY=build-only \
    python manage.py collectstatic --noinput
EXPOSE 8000
CMD python manage.py migrate --fake-initial --noinput && python manage.py migrate_tenant_databases && python manage.py ensure_quality_schema && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
