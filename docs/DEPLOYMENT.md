# Deploying StockPilot to production

StockPilot is four pieces:

| Service | Image | Port | Needs |
|---|---|---|---|
| `db` | PostgreSQL 15+ (16 recommended) | 5432 | a persistent volume, daily backups |
| `api` | `backend/Dockerfile` (FastAPI) | 8000 | `DATABASE_URL`, `SECRET_KEY`, a volume for uploaded files |
| `worker` | same image as `api`, command `worker` | — | same settings as `api`, plus email/SMS settings |
| `web` | `frontend/Dockerfile` (Next.js) | 3000 | `BACKEND_URL` **at build time** |

The worker sends emails, SMS and WhatsApp messages and runs the scheduled checks (late purchase
orders, waiting transfers, expiry). Run one copy; more are safe (they share work through the
database) but rarely needed. On platforms without a separate "worker" type, run it as a second
service from the same image with the start command `/app/entrypoint.sh worker`.

The browser only talks to `web`. `web` forwards `/api/*` to `api`, so the refresh cookie is
first-party and you don't need CORS. Only `web` needs to be public; keep `api` private if your
platform allows it (it still works if public).

The API container runs `alembic upgrade head` on every start, so deploying a new version
migrates the database automatically. Set `RUN_MIGRATIONS=false` if you prefer to run
migrations as a separate release step (`alembic upgrade head` in the api image).

---

## Before you go live — checklist

1. **`SECRET_KEY`**: `openssl rand -hex 32`. With `ENVIRONMENT=production` the API refuses to
   start with the default or a short key.
2. **`ENVIRONMENT=production`**, **`SEED_DEMO_DATA=false`**.
3. **HTTPS** in front of `web` (every platform below does this for you), then
   **`COOKIE_SECURE=true`** so the session cookie is HTTPS-only and HSTS is sent.
4. A **strong database password** and a managed or backed-up PostgreSQL.
5. **Backups**: daily `pg_dump` (or the provider's automatic backups) with point-in-time recovery
   if available. Stock movements are the system of record — test a restore.
6. **Rate limiting with several API instances**: the built-in limiter is per process. With more
   than one API replica, also rate-limit at the load balancer / CDN (e.g. Cloudflare, AWS WAF,
   nginx `limit_req`), especially `POST /api/auth/login`.
7. **Email** for password resets, invitations and alerts: set `SMTP_*`, `EMAIL_FROM` and `APP_URL`
   (see [Email and text messages](#email-and-text-messages)).
8. **Uploaded files** live in `ATTACHMENTS_DIR` (`/app/data/attachments` in the image). Mount a
   persistent volume there and back it up with the database. With several API replicas, all must
   share the same volume.
9. **Time zone**: Settings → Business → Time zone decides when "today" starts in sales reports.
10. **Subscriptions** (only if you sell StockPilot): Paystack keys and webhook, see below.
11. `WEB_CONCURRENCY` = 2 × CPU cores for the API is a good start. Each worker opens up to
   `DB_POOL_SIZE + DB_MAX_OVERFLOW` (default 10 + 20) connections — keep the total under the
   database's `max_connections`, or put PgBouncer in front.

### Environment variables (API)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | local dev URL | `postgresql+psycopg://USER:PASS@HOST:5432/DB` (add `?sslmode=require` for managed DBs) |
| `SECRET_KEY` | dev value | signs access tokens; changing it signs everyone out |
| `ENVIRONMENT` | `development` | `production` enforces a strong key |
| `ACCESS_TOKEN_MINUTES` | 30 | access-token lifetime |
| `REFRESH_TOKEN_DAYS` | 14 | how long "stay signed in" lasts |
| `COOKIE_SECURE` | `false` | `true` behind HTTPS |
| `CORS_ORIGINS` | `http://localhost:3000` | comma-separated; only if a browser calls the API directly |
| `RATE_LIMIT_PER_MINUTE` | 300 | per IP, all API calls |
| `LOGIN_RATE_LIMIT_PER_MINUTE` | 10 | per email (×3 per IP) |
| `WEB_CONCURRENCY` | 2 | uvicorn workers |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 10 / 20 | per worker |
| `RUN_MIGRATIONS` | `true` | run Alembic on start |
| `APP_URL` | `http://localhost:3000` | public web address, used in email links |
| `ATTACHMENTS_DIR` | `/app/data/attachments` | where uploaded files are stored |
| `MAX_UPLOAD_MB` | 10 | largest file people can attach |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | empty, 587 | email server; empty host = emails go to the worker log |
| `SMTP_SECURITY` | `starttls` | `starttls` (587), `ssl` (465) or `none` |
| `EMAIL_FROM` | `StockPilot <no-reply@stockpilot.local>` | sender shown in emails |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | empty | turn on SMS / WhatsApp |
| `TWILIO_SMS_FROM` | empty | your Twilio number (`+1…`) or Messaging Service SID (`MG…`) |
| `TWILIO_WHATSAPP_FROM` | empty | your WhatsApp-enabled Twilio number |
| `SEED_DEMO_DATA` | `false` in image, `true` in compose | demo company |
| `BILLING_ENABLED` | `false` | `true` only when you sell StockPilot to other businesses (see [Subscriptions](#selling-stockpilot-subscriptions-with-paystack)) |
| `PAYSTACK_SECRET_KEY` | empty | `sk_test_…` / `sk_live_…` |
| `BILLING_CURRENCY` | `NGN` | currency of plan prices |
| `TRIAL_DAYS` / `GRACE_DAYS` | 14 / 7 | free trial; days after a missed payment before read-only |
| `PLAN_PRICES` | `starter=15000,business=35000,pro=75000` | monthly prices; yearly = 10 × monthly |
| `PORT` | 8000 | |

### Web

| Variable | When | Notes |
|---|---|---|
| `BACKEND_URL` | **build** (Docker `--build-arg`) | internal URL of the API, e.g. `http://api:8000` or `https://api.internal.example.com` |
| `PORT` | run | default 3000 |

Next.js bakes the proxy target in at build time, so rebuild `web` if the API address changes.

---

## Email and text messages

**Email** works with any SMTP service. Set these on both `api` and `worker`:

| Provider | `SMTP_HOST` | `SMTP_PORT` / `SMTP_SECURITY` | Username / password |
|---|---|---|---|
| Google Workspace / Gmail | `smtp.gmail.com` | 587 / `starttls` | the address + an [app password](https://support.google.com/accounts/answer/185833) |
| Zoho Mail | `smtp.zoho.com` | 587 / `starttls` | the address + password (or app password) |
| Microsoft 365 | `smtp.office365.com` | 587 / `starttls` | the address + password (SMTP AUTH must be on for the mailbox) |
| SendGrid | `smtp.sendgrid.net` | 587 / `starttls` | `apikey` + your API key |
| Mailgun / Postmark / Amazon SES | from their dashboard | 587 / `starttls` | from their dashboard |

Set `EMAIL_FROM` to an address that provider is allowed to send from, and `APP_URL` to the address
people open StockPilot at (e.g. `https://stock.dyelab.co`), so links in emails work.
To test: Settings → My account → *Alerts outside the app* → tick Email, or use "Forgot password".
Check `docker compose logs worker` if nothing arrives.

**SMS and WhatsApp** use [Twilio](https://www.twilio.com). Create an account, buy a number (or set
up a Messaging Service), and set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_FROM` and/or
`TWILIO_WHATSAPP_FROM`. The options then appear for everyone under Settings → My account.

WhatsApp rules: outside Twilio's sandbox, a business can only send free-form WhatsApp messages to
someone who messaged it in the last 24 hours. To send alerts at any time you need an approved
WhatsApp Business sender in Twilio and an approved message template; until then use SMS or email.

**Camera scanning** in phone browsers only works over HTTPS. Every platform below provides HTTPS.

---

## Counter sales (the till)

Counter sales are switched on per company in **Settings → Business → Features → Counter sales (till)**
(on by default for shops, restaurants, e-commerce and healthcare). Staff open it from **Sell (till)**
in the menu, or go straight to `https://YOUR-ADDRESS/pos`.

* **HTTPS is required on phones and tablets** for the till to work offline and for the camera.
  On `http://` (except `localhost`) the till still sells, but it can't open without internet.
* **Install it like an app**: in Chrome on Android, menu → *Add to Home screen*; on iPhone, Share →
  *Add to Home Screen*. It opens straight on the till, full screen.
* **Offline**: start the shift while online. After that the till keeps selling with no internet, keeps
  the product list and the sales on the device, and uploads them by itself when the connection comes
  back (safe to retry; nothing is counted twice). Opening a shift, closing it, returns and cash in/out
  need the internet. Offline sales are never refused when they arrive: if stock ran out or a price or
  discount doesn't add up, the sale is saved, marked *Needs checking* and the manager is alerted.
* **Barcode scanners**: any USB or Bluetooth scanner that "types" the code works (scan into the
  search box). Phones can use the camera button.
* **Receipt printers**: the till prints through the normal print dialog, so any printer the device
  can print to works. Choose 80 mm or 58 mm paper in Settings → Business → Counter sales.
  - Windows / Mac with a USB thermal printer (Epson TM-T20, Xprinter…): install the printer's
    driver, set its paper size to 80 mm (or 58 mm) roll, and set it as the default printer.
  - Android with a Bluetooth thermal printer: install a print-service app such as *RawBT*, pair the
    printer, and choose it in the print dialog.
  - iPhone/iPad: AirPrint printers work directly.
  Browsers always show the print dialog; silent printing and opening the cash drawer from the
  browser are not possible.
* **Receipts by WhatsApp** open WhatsApp on the cashier's device with the receipt typed in (no
  WhatsApp Business account needed). **SMS and email receipts** use the Twilio and SMTP settings above.

---

## Selling StockPilot: subscriptions with Paystack

Leave this off (`BILLING_ENABLED=false`) when StockPilot runs your own business: every feature is
available, with no limits and no payments. Switch it on when other businesses sign up and pay you.

**What customers get**: a 14-day free trial with everything, then a choice of plans on the
**Subscription** page (Starter: 1 location, 3 staff; Business: 3 locations, 10 staff; Pro: unlimited),
monthly or yearly (2 months free). They pay on Paystack by card, bank transfer or USSD. Card payments
renew automatically; for transfer/USSD they get a reminder to pay again. If a payment is missed they
have `GRACE_DAYS` (7) more days, then the account becomes **read-only**: they can see everything and
download reports, but can't change anything until they pay. Nothing is ever deleted.

**Set it up**

1. Create a [Paystack](https://paystack.com) business account and complete verification (needed for
   live payments).
2. Dashboard → *Settings → API Keys & Webhooks*:
   - copy the **Secret key** (`sk_test_…` while testing, `sk_live_…` for real money) into
     `PAYSTACK_SECRET_KEY`;
   - set the **Webhook URL** to `https://YOUR-ADDRESS/api/billing/webhook`
     (the web address people use; `web` passes it to the API).
3. Set on **both `api` and `worker`**: `BILLING_ENABLED=true`, `PAYSTACK_SECRET_KEY`, and if you
   want different prices `PLAN_PRICES=starter=20000,business=45000,pro=90000`. Make sure `APP_URL`
   is your real address: Paystack sends customers back to `APP_URL/billing`.
4. Restart: `docker compose up -d`.
5. **Give your own company (and any pilot customers) a plan for free**, otherwise they start a trial too:

   ```bash
   docker compose exec api python -m app.cli companies                     # list companies and their plans
   docker compose exec api python -m app.cli set-plan you@yourbusiness.com pro 120   # 120 months
   docker compose exec api python -m app.cli extend-trial owner@theirshop.com 14     # 14 more trial days
   ```

6. Test with the `sk_test_…` key and one of Paystack's test cards from
   <https://paystack.com/docs/payments/test-payments/>, then switch to the live key.

Notes: changing plan starts the new plan from the day of payment (no part-month credit). Plan limits
are checked when someone adds a team member or a location; a customer already above a smaller plan's
limit is asked to choose a bigger plan. Payments and plan changes appear in the activity log.

---

## Any server with Docker (DigitalOcean Droplet, AWS EC2/Lightsail, Azure VM, GCP VM, Hetzner…)

```bash
git clone <your repo> stockpilot && cd stockpilot
cp .env.example .env    # set SECRET_KEY, POSTGRES_PASSWORD, ENVIRONMENT=production,
                        # SEED_DEMO_DATA=false, COOKIE_SECURE=true
docker compose up -d --build
```

Put Caddy or nginx in front for HTTPS. Caddy is one line:

```
inventory.example.com {
    reverse_proxy localhost:3000
}
```

Remove the `ports:` entry for `api` and `db` in `docker-compose.yml` so only the web app is
reachable from outside. Upgrade with `git pull && docker compose up -d --build`.

## Render

1. **PostgreSQL** → New → PostgreSQL. Copy the *internal* connection string and change the scheme
   to `postgresql+psycopg://`.
2. **API** → New Web Service (private service is fine) → root `backend`, Docker. Env: `DATABASE_URL`,
   `SECRET_KEY`, `ENVIRONMENT=production`, `COOKIE_SECURE=true`, `APP_URL`, `SMTP_*`. Health check
   path `/api/ready`. Add a **Disk** mounted at `/app/data/attachments`.
   **Worker** → New Background Worker → same repo and root, Docker command `/app/entrypoint.sh worker`,
   same environment (use an Environment Group).
3. **Web** → New Web Service → root `frontend`, Docker. Docker build arg
   `BACKEND_URL=http://<api-service-name>:8000` (Render's private network hostname).

## Railway

1. Add a **PostgreSQL** plugin.
2. Add a service from the repo with root directory `backend`; set `DATABASE_URL` to
   `postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}`,
   plus `SECRET_KEY`, `ENVIRONMENT=production`, `COOKIE_SECURE=true`.
   Add a volume mounted at `/app/data/attachments`. Duplicate the service as `worker` with start
   command `/app/entrypoint.sh worker`.
3. Add a service with root `frontend`; set the build variable
   `BACKEND_URL=http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000`. Generate a public domain for `web` only.

## Fly.io

```bash
fly postgres create --name stockpilot-db
cd backend && fly launch --name stockpilot-api --no-deploy
fly postgres attach stockpilot-db -a stockpilot-api     # sets DATABASE_URL (change scheme to postgresql+psycopg://)
fly secrets set -a stockpilot-api SECRET_KEY=$(openssl rand -hex 32) ENVIRONMENT=production COOKIE_SECURE=true
fly deploy
cd ../frontend && fly launch --name stockpilot-web --no-deploy
fly deploy --build-arg BACKEND_URL=http://stockpilot-api.internal:8000
```

Set the API's `internal_port = 8000` and the web's `internal_port = 3000` in each `fly.toml`.
Add a `[processes]` section to the API app (`app = ""`, `worker = "worker"`) so Fly runs the worker
from the same image, and a `fly volumes create attachments` mounted at `/app/data/attachments`.

## AWS (ECS Fargate + RDS)

- **RDS PostgreSQL 16** in private subnets; enable automated backups.
- Push both images to **ECR** (build `web` with `--build-arg BACKEND_URL=http://api.stockpilot.local:8000`).
- **ECS service `api`** (private, Cloud Map name `api.stockpilot.local`), secrets from
  **Secrets Manager** (`DATABASE_URL`, `SECRET_KEY`). Health check `/api/ready`.
- **ECS service `worker`**: same task definition as `api` with command `["worker"]`, desired count 1.
- Uploaded files: an **EFS** volume mounted at `/app/data/attachments` on `api`.
- Email: **Amazon SES** SMTP credentials work as `SMTP_*`.
- **ECS service `web`** behind an **Application Load Balancer** with an ACM certificate.
  Add **AWS WAF** rate-based rules for login.

## Google Cloud (Cloud Run + Cloud SQL)

- **Cloud SQL for PostgreSQL**; connect the API with the Cloud SQL connector or private IP.
- Deploy `api` to Cloud Run with `--no-allow-unauthenticated` *only if* you add a service-to-service
  identity token in front; simplest is to allow it and rely on the app's auth.
- Build `web` with `BACKEND_URL=https://<api-url>` and deploy to Cloud Run (public).
- Secrets in **Secret Manager**. Set min instances ≥ 1 on `api` to avoid cold starts.
- Run the worker as a second Cloud Run service with `--no-cpu-throttling --min-instances=1` and
  command `/app/entrypoint.sh worker`. Mount a Cloud Storage FUSE or Filestore volume at
  `/app/data/attachments`.

## Azure (Container Apps + Flexible Server)

- **Azure Database for PostgreSQL – Flexible Server** (`?sslmode=require` in `DATABASE_URL`).
- A **Container Apps environment** with two apps: `api` (internal ingress, port 8000) and `web`
  (external ingress, port 3000, build arg `BACKEND_URL=http://api`). Secrets via Key Vault references.
  Add a third app `worker` (no ingress, args `worker`) and an Azure Files share mounted at
  `/app/data/attachments` on `api`.

---

## Operations

- **Logs**: one line per request with method, path, status, duration and request id
  (`X-Request-ID` header, echoed back).
- **Health**: `/api/health` (process up) for liveness, `/api/ready` (DB reachable and migrated) for readiness.
- **Messages**: every email/SMS/WhatsApp is a row in `notification_deliveries` with its status,
  attempts and last error. `SELECT status, count(*) FROM notification_deliveries GROUP BY 1` is a
  quick health check.
- **Scaling**: the API is stateless; run as many replicas as you like. Stock correctness comes
  from PostgreSQL row locks, not from the app process, so it holds across replicas.
- **Large organisations**: all list endpoints paginate; product search uses trigram indexes; movement
  and audit tables are indexed by organisation and date. For hundreds of millions of movements,
  partition `stock_movements` by month (PostgreSQL declarative partitioning) — no code changes needed.
- **Never** edit `stock_movements` by hand. Corrections are adjustments; the database will refuse
  updates and deletes anyway.
