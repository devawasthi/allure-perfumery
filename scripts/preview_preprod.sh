#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export APP_ENV=preprod
export PREVIEW_LABEL="${PREVIEW_LABEL:-PRE-PROD LOCAL}"
export SITE_NAME="${SITE_NAME:-The Scentist}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8792}"
export BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"

# Force a local SQLite database so preview never touches Neon/production.
export DATABASE_URL=""
export DB_HOST=""
export DB_NAME=""
export DB_USER=""
export DB_PASSWORD=""
export SQLITE_DATABASE_PATH="${SQLITE_DATABASE_PATH:-data/preprod.sqlite3}"

export AUTO_SEED_CATALOG="${AUTO_SEED_CATALOG:-true}"
export ENABLE_MANUAL_CHECKOUT="${ENABLE_MANUAL_CHECKOUT:-true}"
export STATIC_CACHE_MAX_AGE_SECONDS="${STATIC_CACHE_MAX_AGE_SECONDS:-0}"
export LOG_LEVEL="${LOG_LEVEL:-info}"

echo "Starting The Scentist pre-prod preview"
echo "URL: http://127.0.0.1:${PORT}"
echo "Database: ${SQLITE_DATABASE_PATH}"
echo

exec python3 server.py
