#!/usr/bin/env bash
set -e

echo "[Bitfuse Entrypoint] Starting Django backend..."

# Run database migrations
echo "[Bitfuse Entrypoint] Running database migrations..."
python manage.py migrate --noinput

# Collect static files for WhiteNoise
echo "[Bitfuse Entrypoint] Collecting static files..."
python manage.py collectstatic --noinput

# Execute Gunicorn WSGI server
echo "[Bitfuse Entrypoint] Starting Gunicorn server..."
exec gunicorn Bitfuse.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --threads "${GUNICORN_THREADS:-2}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --access-logfile - \
    --error-logfile -
