#!/bin/sh
set -e
# "worker" runs the background worker (emails, SMS/WhatsApp, scheduled checks).
# Anything else starts the API. Only the API applies migrations.
if [ "$1" = "worker" ]; then
  exec python -m app.worker
fi
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Running database migrations…"
  alembic upgrade head
fi
if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
  python seed.py
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-2}" --proxy-headers --forwarded-allow-ips="*"
