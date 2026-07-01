#!/bin/sh
set -e

# Named volumes mount as root; app runs as uid 1000 after gosu
mkdir -p /app/db /app/static/uploads/resumes /app/static/uploads/avatars /app/staticfiles
chown -R app:app /app/db /app/static/uploads /app/staticfiles

echo "Running migrations..."
gosu app python manage.py migrate --noinput

    echo "Collecting static files..."
gosu app python manage.py collectstatic --noinput

if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "WARNING: GEMINI_API_KEY is not set — resume analysis will fail until you add it to .env and recreate the web container."
fi

exec gosu app "$@"
