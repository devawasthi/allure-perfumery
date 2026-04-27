# Render + Neon Deployment

This is the low-cost deployment path for Allure Alchemy.

- Render runs the Dockerized Python/Gunicorn web app.
- Neon provides managed PostgreSQL.
- Render provides the public URL, HTTPS, logs, and automatic deploys from Git.

Use this first when you want the store online without paying for the full AWS ECS/RDS/ALB stack.

## Cost Shape

Start with:

- Render Web Service: Free while testing, or Starter for a live payment-enabled store.
- Neon Postgres: Free while the database is small.

For a real store, upgrade the Render service from `free` to `starter` so the app does not sleep after idle traffic.

## 1. Create Neon Postgres

1. Create a Neon account.
2. Create a new project.
3. In the Neon project dashboard, click `Connect`.
4. Select:
   - Branch: `main`
   - Database: the default database, or create `allurealchemy`
   - Role: the app role Neon created
   - Connection pooling: off
5. Copy the direct connection string.

It should look like:

```text
postgresql://USER:PASSWORD@HOST.neon.tech/DBNAME?sslmode=require&channel_binding=require
```

Use the direct connection string first. Do not use the `-pooler` hostname for the initial deploy.

## 2. Deploy on Render

1. Push this repo to GitHub or GitLab.
2. In Render, choose `New` -> `Blueprint`.
3. Connect the repo.
4. Render will detect `render.yaml`.
5. When Render asks for secret values, enter:

```text
BASE_URL=https://allure-alchemy.onrender.com
DATABASE_URL=<your Neon direct connection string>
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
```

Leave the Razorpay values blank for the first test deploy if you only want manual checkout. Add live Razorpay values before taking real payments.

If Render gives the service a different `.onrender.com` URL, update `BASE_URL` in the Render service environment after the first deploy.

If this folder is not already a Git repo, initialize it before pushing:

```bash
git init
git add .
git commit -m "Add Render and Neon deployment"
```

Then create an empty GitHub/GitLab repository and follow its push instructions.

## 3. Verify the App

After deploy, open:

```text
https://your-render-url.onrender.com/healthz
https://your-render-url.onrender.com/readyz
https://your-render-url.onrender.com/
```

Expected:

- `/healthz` returns a healthy app response.
- `/readyz` confirms the app can talk to Neon.
- The home page loads product data seeded into PostgreSQL.

The app creates/updates its database schema on startup, and the catalog seed is idempotent.

## 4. Add a Custom Domain

In Render:

1. Open the web service.
2. Go to `Settings` -> `Custom Domains`.
3. Add your domain.
4. Follow Render's DNS instructions.
5. Update the service environment variable:

```text
BASE_URL=https://your-domain.com
```

Render will manage HTTPS certificates for the custom domain.

## 5. Enable Razorpay

In Razorpay:

1. Create live API keys.
2. Add a webhook URL:

```text
https://your-domain.com/api/webhooks/razorpay
```

3. Enable events:

```text
payment.captured
order.paid
```

4. In Render service environment variables, set:

```text
RAZORPAY_KEY_ID=rzp_live_xxxxx
RAZORPAY_KEY_SECRET=<live key secret>
RAZORPAY_WEBHOOK_SECRET=<same webhook secret configured in Razorpay>
```

5. Redeploy the Render service.

## 6. Production Tweaks

For the cheapest test deploy, keep `plan: free` in `render.yaml`.

For a live store, change:

```yaml
plan: starter
```

Then sync the Blueprint or update the service plan in Render.

Keep these environment values small on low-cost Render plans:

```text
WEB_CONCURRENCY=2
WEB_THREADS=4
DATABASE_POOL_MIN_SIZE=1
DATABASE_POOL_MAX_SIZE=4
```

That keeps database connections conservative for Neon.

## 7. Updating the Store

After this is connected, normal deploys are simple:

1. Push code to the connected branch.
2. Render builds the Docker image.
3. Render replaces the running service.
4. The app checks schema and seed data on startup.
