#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export APP_ENV=production
export PREVIEW_LABEL="${PREVIEW_LABEL:-PROD-LIKE LOCAL}"
export SITE_NAME="${SITE_NAME:-The Scentist}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8793}"
export BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"

# Force a local SQLite database so prod-like preview never touches live Neon.
export DATABASE_URL=""
export DB_HOST=""
export DB_NAME=""
export DB_USER=""
export DB_PASSWORD=""
export SQLITE_DATABASE_PATH="${SQLITE_DATABASE_PATH:-data/prod-like.sqlite3}"

export AUTO_SEED_CATALOG="${AUTO_SEED_CATALOG:-false}"
export ENABLE_MANUAL_CHECKOUT="${ENABLE_MANUAL_CHECKOUT:-true}"
export ADMIN_TOKEN="${ADMIN_TOKEN:-local-admin-token}"
export STATIC_CACHE_MAX_AGE_SECONDS="${STATIC_CACHE_MAX_AGE_SECONDS:-86400}"
export WEB_CONCURRENCY="${WEB_CONCURRENCY:-2}"
export WEB_THREADS="${WEB_THREADS:-4}"
export LOG_LEVEL="${LOG_LEVEL:-info}"

echo "Starting The Scentist prod-like local preview"
echo "URL: http://127.0.0.1:${PORT}"
echo "Database: ${SQLITE_DATABASE_PATH}"
echo

if python3 -c "import gunicorn" >/dev/null 2>&1; then
  exec python3 -m gunicorn server:application -c gunicorn.conf.py
fi

echo "Gunicorn is not installed locally; falling back to python3 server.py."
echo "Run 'python3 -m pip install -r requirements.txt' for a closer Render match."
echo
exec python3 server.py
