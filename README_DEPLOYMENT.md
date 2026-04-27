# Deployment Guide

## Production Shape

The app is now structured for real deployment:

- WSGI application entrypoint at `server:application`
- Gunicorn worker process model for horizontal scaling
- PostgreSQL support for shared order/inventory storage across instances
- SQLite retained only as the local/development fallback
- Readiness and health endpoints:
  - `/healthz`
  - `/readyz`
- Safer request limits, cache headers, security headers, and transaction-safe stock deduction

## Recommended Stack

For sustained traffic around `10,000` users per hour, deploy with:

- App runtime: Gunicorn behind a load balancer or reverse proxy
- Database: managed PostgreSQL
- Static delivery: CDN in front of `/static`, `/assets`, and `/artwork`
- Payments: Razorpay with webhook configured
- Monitoring: platform logs plus DB metrics/alerts

`10,000/hour` is roughly `2.8` requests per second on average, but bursts matter. The app code is now ready for multi-worker, multi-instance deployment, but actual capacity still depends on infrastructure sizing.

## Environment Variables

Start from `.env.example`.

Critical production values:

- `APP_ENV=production`
- `BASE_URL=https://your-domain.example`
- `DATABASE_URL=postgresql://...`
- `RAZORPAY_KEY_ID=...`
- `RAZORPAY_KEY_SECRET=...`
- `RAZORPAY_WEBHOOK_SECRET=...`

Useful scaling values:

- `WEB_CONCURRENCY`
- `WEB_THREADS`
- `WEB_TIMEOUT_SECONDS`
- `DATABASE_POOL_MIN_SIZE`
- `DATABASE_POOL_MAX_SIZE`
- `DATABASE_STATEMENT_TIMEOUT_MS`

## Local Run

SQLite still works locally for development:

```bash
cd /Users/devawasthi/perfumery
python3 -m pip install -r requirements.txt
python3 server.py
```

Open:

- `http://127.0.0.1:8780`

## Production Run

Gunicorn entrypoint:

```bash
cd /Users/devawasthi/perfumery
gunicorn server:application -c gunicorn.conf.py
```

## Docker

```bash
docker build -t allure-alchemy .
docker run --env-file .env -p 8780:8780 allure-alchemy
```

## PostgreSQL Notes

- Set `DATABASE_URL` to a managed PostgreSQL instance in production.
- The app will create/update schema tables and indexes on startup.
- Catalog seed sync is idempotent and safe to run across repeated deploys.

## Razorpay Setup

1. Create API keys in Razorpay.
2. Put them into:
   - `RAZORPAY_KEY_ID`
   - `RAZORPAY_KEY_SECRET`
3. Create a webhook pointing to:
   - `https://your-domain.example/api/webhooks/razorpay`
4. Set the same secret in:
   - `RAZORPAY_WEBHOOK_SECRET`
5. Enable:
   - `payment.captured`
   - `order.paid`

## Operational Advice

- Put the app behind HTTPS only.
- Run more than one app instance in production.
- Use managed PostgreSQL backups and point-in-time recovery.
- Put CDN caching in front of static responses.
- Keep `ENABLE_MANUAL_CHECKOUT=true` only if you want non-Razorpay payment paths live.
