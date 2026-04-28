# Local Preview Workflow

Use this before pushing to GitHub/Render so homepage and catalog changes can be reviewed in a browser first.

## Pre-prod local preview

```bash
./scripts/preview_preprod.sh
```

Open:

```text
http://127.0.0.1:8792
```

This is the safest everyday preview. It uses:

- `APP_ENV=preprod`
- local SQLite database: `data/preprod.sqlite3`
- no production `DATABASE_URL`
- no live Neon database
- a visible `PRE-PROD LOCAL` badge
- static cache disabled so CSS/image changes show quickly

If the port is busy:

```bash
PORT=8794 ./scripts/preview_preprod.sh
```

## Prod-like local preview

```bash
./scripts/preview_prod_like.sh
```

Open:

```text
http://127.0.0.1:8793
```

This is for a final sanity check before deployment. It uses production-like settings, a separate local SQLite database, and Gunicorn if it is installed locally. It still does not touch Neon or Render.

## Production

Production is the Render service connected to GitHub `main`. Only push after the local preview looks right:

```bash
git push origin main
```

Suggested workflow:

1. Make edits locally.
2. Run `./scripts/preview_preprod.sh`.
3. Review `http://127.0.0.1:8792`.
4. Commit only after it looks right.
5. Push to GitHub when ready for Render production.
