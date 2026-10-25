# StockPilot

Multi-tenant inventory management that a non-technical employee can run from a phone.
Receive, issue, transfer and count stock; see exactly what is left, what to reorder and
what is expiring; and see who changed what, and when.

**Status: Phase 3 complete.** Inventory engine, multi-tenancy and security (Phase 1); purchase and
sales orders, spreadsheet import, sizes/colours, barcode labels, camera scanning, account security,
email/SMS/WhatsApp alerts, attachments and production (Phase 2); and for shops (Phase 3): a till
that works offline, Nigerian payment methods and cash-up, printed and WhatsApp receipts, returns,
exchanges and store credit, promotions, the owner's "Sales today" screen, and subscriptions with
Paystack so other businesses can pay to use it. See [Known limits](#known-limits) before going live.

---

## Run it

```bash
cp .env.example .env          # optional for a local try-out; required before going live
docker compose up --build
```

| What | Where |
|---|---|
| Web app | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/api/docs |
| API docs (ReDoc) | http://localhost:8000/api/redoc |
| Health / readiness | `/api/health`, `/api/ready` |

On first start the API container applies database migrations and (when
`SEED_DEMO_DATA=true`, the default for local use) creates a demo company. A `worker` container
sends emails and alerts and runs scheduled checks.

### Upgrading from an earlier phase

Replace the project folder with the new one (keep the folder name `stockpilot`, so Docker finds
your existing data), keep your `.env` file, and run `docker compose up --build`. The database
upgrades itself and your data is kept. Phase 3 adds a **Cashier** role to every company and switches
**counter sales** on for shops, restaurants, e-commerce and healthcare businesses (others can turn
it on in Settings → Business → Features). New demo examples are only added to a fresh database.

If you created a `.env` file after first starting StockPilot, keep `POSTGRES_PASSWORD` the same as
the database was created with (`stockpilot` if there was no `.env` at first), or the API can't sign
in to the database.

### Demo logins

Company: **Acme Supplies Ltd** (wholesale, NGN, three locations, batches/expiry and serials switched on).
Password for every demo user: `Demo-Password-2026`

| Role | Email | Can |
|---|---|---|
| Owner | owner@demo.stockpilot.app | everything |
| Administrator | admin@demo.stockpilot.app | everything |
| Manager | manager@demo.stockpilot.app | operations, reports; not users/settings |
| Warehouse Staff | warehouse@demo.stockpilot.app | receive, issue, transfer |
| Sales Staff | sales@demo.stockpilot.app | issue/sell — **restricted to Ikeja Shop** |
| Purchasing Staff | purchasing@demo.stockpilot.app | receive, suppliers |
| Viewer | viewer@demo.stockpilot.app | read-only |
| Cashier | cashier@demo.stockpilot.app | the till only — **restricted to Ikeja Shop**, has a shift open |

The demo data includes low-stock items, an out-of-stock item, a batch expiring this week, an
expired batch, serial-numbered laptops and phones, unit conversions (1 carton = 12 bottles),
transfers, sales and a damage write-off, plus a T-shirt in 3 sizes × 2 colours, a food pack made
from rice and oil, a late part-delivered purchase order, a draft purchase order, and a paid and a
part-paid sales order. For the till: a "10% off clothing" promotion, yesterday's closed shift (₦200
short), today's open shift with sales, and a return refunded as store credit.

---

## What's in Phase 3 (for shops)

**The till** (`/pos`, "Sell (till)" in the menu) — works on a phone, tablet or computer. Scan with a
USB/Bluetooth scanner or the phone camera, or tap products by category; sizes/colours and serial
numbers are handled; stock left is shown on every product. Promotions apply by themselves; staff
with permission can give discounts up to the shop's limit, managers can change prices.

**Payments the way Nigerian shops are paid** — cash (with quick buttons for common notes and the
change to give), bank transfer, POS/card, store credit, or split across several. Every sale belongs
to a **shift**: the cashier counts the float in, records cash taken out (diesel, change), and counts
the drawer at closing; StockPilot shows what should be there and records any shortage. A manager is
alerted when a drawer is short or over.

**Works when the internet goes down** — the till keeps the product list on the device, keeps
selling, prints receipts, and uploads the sales when the connection is back (each sale exactly once).
Once added to the home screen it opens with no connection at all. Sales that arrive with a problem
(stock ran out meanwhile, a changed price) are kept and flagged for the manager, never lost.

**Receipts** — print on 80 mm or 58 mm receipt printers (or any printer), send by WhatsApp, SMS or
email; each receipt has a private link the customer can open. Your address and a footer are set in
Settings.

**Returns and exchanges** — find the receipt (number, customer name or phone), choose what comes
back, decide whether it can be sold again, refund in cash, transfer, card, or **store credit** that
pays for the exchange. Returns without a receipt are allowed for managers. Refunds never exceed what
was paid, and serial numbers go back into stock.

**Sales today** (`/today`) — for the owner on the phone: net sales, profit, compared with yesterday
and the same day last week; money in by payment method; sales hour by hour; by shop and by cashier;
best sellers; stock that hasn't sold in 30 days; tills open now with the cash each should hold.
Three new reports: Receipts, Counter sales by product, and Cash-ups (all exportable).

**Promotions** — percentage or amount off, on everything, a category or chosen products, with
start/end dates and "buy N or more"; the bigger saving wins.

**Subscriptions** (off unless you switch them on) — sell StockPilot to other businesses: 14-day free
trial, Starter / Business / Pro plans monthly or yearly, paid on Paystack, automatic card renewal,
grace period, then read-only (never deleted). Plan limits on team members and locations. An operator
command gives any company a plan for free. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## What's in Phase 2

**Purchase orders** — order from a supplier in any unit (cartons → pieces), receive part of a
delivery (with batch, expiry and serials per line), see what's still to come, close an order when
the rest won't arrive. Late orders are flagged and alerted. **Reorder low stock** drafts one order
per preferred supplier in one click and counts stock already on order, so nothing is ordered twice.
The last purchase price becomes the product's cost.

**Sales orders** — customer, prices, discounts and tax per line; confirm, then send all or part
(earliest-expiry batches first, or chosen serials); record payments and refunds. Stock goes down
only when goods are sent. Cost of goods is captured at the moment of sending, so profit is exact.

**Reports** — four more: Sales (with amounts owed and profit), Sales by product (margin %),
Purchases (what arrived, what's still to come) and Suppliers (late orders, average days to deliver).

**Spreadsheet import** — products, suppliers, customers and opening stock. Download an Excel or CSV
template, upload it, see every problem row by row, fix it in the on-screen grid, preview what will
be added or updated, then import. All-or-nothing: if any row is wrong, nothing is saved.

**Sizes and colours** — create a product with options (e.g. Size S/M/L × Colour Indigo/Rust) and
every combination gets its own SKU, barcode, price and stock. Add new sizes later; change the price
of all variants at once. Scanning or picking the group asks "which one?".

**Barcodes and labels** — generate in-store EAN-13 barcodes (prefix 20, never clashes with real
brands) and print labels as a PDF: A4 sheets (24, 14 or 40 per page, with "start at label N" to
reuse a half-used sheet) or a 50 × 25 mm label printer. Products without a standard barcode print
their SKU as Code 128.

**Camera scanning on every phone** — Android Chrome uses the built-in reader; iPhone Safari and
other browsers use ZXing. A camera button sits next to every product field, so staff can scan
straight into Receive, Issue, Transfer, orders and labels.

**Account security** — "Forgot password" by email (1-hour single-use link, signs out everywhere),
email confirmation, two-step sign-in with any authenticator app plus 8 single-use recovery codes,
and an admin reset for a lost phone. New team members get a welcome email.

**Alerts outside the app** — each person chooses email, SMS and/or WhatsApp and which alert types.
New alerts are bundled into one message per minute. Sending happens in the worker from a database
outbox with retries (2, 4, 8, 16, 32 minutes); a message that keeps failing raises an in-app alert.
Scheduled checks add alerts for late purchase orders, transfers waiting for approval, and transfers
on the road for more than 2 days.

**Files** — attach delivery notes, invoices and photos (PDF, images, Excel, Word; up to 10 MB) to
products, purchase and sales orders, transfers, adjustments, suppliers and customers. Files are
checked by content, stored privately per company and only served to people allowed to see the record.

**Production** — list what each product is made from, see how many you can make with the stock
you have, and record production: materials go down, the product goes up at the real cost of what
was used (or what you say was really used, for waste).

## What's in Phase 1

**Inventory engine** (`backend/app/services/inventory.py`) — the part everything else relies on.
- Stock is never overwritten. Every change writes an immutable `stock_movements` row and updates
  the `stock_balances` cache in the same transaction. A PostgreSQL trigger rejects any `UPDATE` or
  `DELETE` on movements (and on the audit log).
- Balance rows are locked with `SELECT … FOR UPDATE`, so two people issuing the last units at the
  same moment are serialised: one succeeds, the other is told how many are left. Deadlocks between
  opposite transfers are retried automatically.
- Negative stock is refused by default; owners can allow it. Batch- and serial-tracked stock can
  never go negative.
- Movement types: opening stock, purchase receipt, sale, issue, return, adjustment in/out,
  transfer out/in, damaged, lost, expired, production consumption/output.
- Units and conversions (receive 3 cartons → 72 pieces), decimal units (kg, l, m), moving
  weighted-average cost for valuation, including stock in transit.
- Batches/lots with expiry, **FEFO** picking (first expired, first out), expired batches never
  picked for sale. Serial numbers with per-serial history.
- Transfers: one-step quick transfer, or the full draft → requested → approved → in transit →
  received/cancelled flow with partial receiving and optional approval.

**Product and places** — products (SKU, barcode, category, brand, prices, tax, min stock,
reorder level/qty, preferred supplier, image, tracking flags), categories, units, warehouses of any
type (store, branch, site, vehicle, technician, production) with optional aisle/rack/shelf/bin
hierarchy. A simple business only ever sees "Main Store".

**People and safety** — organisations (tenants), 7 built-in roles with 29 granular permissions,
custom roles, users restricted to specific locations, argon2id password hashing, short-lived JWT
access tokens + rotating httpOnly refresh cookie, CSRF header on cookie endpoints, rate limiting,
security headers, input validation, privilege-escalation guards, full audit log (who, what, when,
before/after, IP).

**Screens** — dashboard, quick actions (receive, issue, transfer, add product, adjust) in under 5
taps, inventory (stock, history, batches & expiry, serials), products, transfers, warehouses,
suppliers, customers, reports, alerts, global search, barcode scan page (USB scanner and phone camera), opening-stock wizard, settings (business, features,
team, roles, activity log, password). Phone layout has a bottom bar with the stock actions under
the thumb.

**Reports** (screen + CSV / Excel / PDF): valuation, stock on hand, low stock, out of stock,
expiry (expired / 7 / 30 / 90 days), movements, batches, serial numbers, stock by location,
adjustments.

**Industry presets** — sign-up asks what kind of business you run and switches modules on
(healthcare → batches + expiry + serials, retail → barcodes + variants, construction → transfer
approval, …). Nothing is hard-coded per industry; every module can be toggled later.

---

## Project layout

```
backend/                FastAPI + SQLAlchemy 2 + PostgreSQL
  app/models.py         schema (36 tables)
  app/services/         inventory engine, orders, imports, variants, barcodes, production,
                        messaging, account security, scheduled checks, pos (till), billing
  app/routers/          REST endpoints
  app/worker.py         background worker (messages + scheduled checks)
  app/cli.py            operator commands (companies, set-plan, extend-trial)
  alembic/              migrations (0001 Phase 1, 0002 Phase 2, 0003 Phase 3 — each upgrades existing data)
  tests/                137 tests against real PostgreSQL
  seed.py               demo data
frontend/               Next.js 16 + TypeScript (App Router)
  src/app/(till)/       the till: full screen, works offline
  src/lib/pos.ts        till pricing (matches the server to the kobo), offline storage and upload
  public/sw.js          service worker that lets the till open offline
docs/                   DEPLOYMENT.md, API.md, openapi.json
docker-compose.yml
```

## Development without Docker

```bash
# PostgreSQL 15+ running locally
cd backend
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/stockpilot
alembic upgrade head && python seed.py
uvicorn app.main:app --reload --port 8000
python -m app.worker          # in another terminal: emails, alerts, scheduled checks

cd ../frontend
npm install
npm run dev                  # http://localhost:3000, proxies /api to :8000
```

## Tests

```bash
cd backend
createdb stockpilot_test
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/stockpilot_test pytest
```

Covers authentication, permissions, tenant isolation (read and write across organisations),
inventory arithmetic, units, average cost, ledger immutability, balance-equals-ledger, adjustments,
transfers (incl. partial receipt and approval), batches/FEFO/expiry, serial lifecycle, reports and
exports (incl. spreadsheet-injection protection), and concurrency: parallel transactions and
30 parallel HTTP requests never oversell, and opposite transfers never deadlock. Phase 2 adds
purchase and sales orders, imports, variants, barcodes and labels, attachments (incl. disguised
files), production costing, password reset, email verification, two-step sign-in, alert digests,
delivery retries, a real SMTP send and the Twilio request format. Phase 3 adds the till (VAT
inside or on top, promotions, discounts and who may give them, split payments and change, offline
uploads that are safe to repeat, offline sales with too little stock, serial numbers), returns and
store credit, cash-up arithmetic, the day summary across time zones, and subscriptions (Paystack
checkout and webhook signature, amount checks, limits, renewals, grace period, read-only lock).

Inside Docker: `docker compose run --rm -e TEST_DATABASE_URL=… api pytest` after building the API
image with `--build-arg INSTALL_DEV=true`.

---

## Known limits

- **Docker:** the files are checked, and every container start-up step was run by hand, but the
  images were not built where this was written (Docker Hub was unreachable there). The first
  `docker compose up --build` on your machine is the first real build.
- **Camera scanning on phones needs HTTPS.** Browsers only allow the camera on `https://` sites
  (or `localhost`). It works once StockPilot is deployed with a certificate (every platform in
  `docs/DEPLOYMENT.md` gives you one). A USB/Bluetooth scanner works everywhere.
- **WhatsApp alerts** go through Twilio. WhatsApp only lets a business send free-form messages in
  Twilio's sandbox, or to someone who messaged you in the last 24 hours. For alerts to anyone at any
  time you need a WhatsApp Business sender approved in Twilio and a message template. SMS has no
  such rule. The Twilio request format is tested; a live Twilio account was not.
- **Emails** need an SMTP account (see `docs/DEPLOYMENT.md`). Without one they are written to the
  worker log (`docker compose logs worker`), which is fine for trying StockPilot out.
- **Rate limiting** is per API process; add a limit at the load balancer when running several.
- **The till offline needs HTTPS on phones and tablets** (or `localhost`), like the camera. Open and
  close shifts, returns and cash in/out need the internet; selling and printing don't.
- **Printing** goes through the browser's print dialog: browsers can't print silently or open a cash
  drawer. See docs/DEPLOYMENT.md for USB and Bluetooth receipt printers.
- **Offline sales can oversell**: two tills offline at once can both sell the last item. When they
  upload, stock goes below zero and the sale is flagged so someone counts and corrects it.
- **Subscriptions**: the Paystack integration is tested against Paystack's documented request and
  webhook formats with a stand-in server, not a live Paystack account. Plan prices are placeholders:
  set yours with `PLAN_PRICES`. Changing plan doesn't credit unused days.
