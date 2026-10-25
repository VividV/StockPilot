# StockPilot REST API

Interactive docs run with the API: **`/api/docs`** (Swagger) and **`/api/redoc`**.
The full machine-readable spec is in [`openapi.json`](openapi.json).

## Authentication

```http
POST /api/auth/login
{"email": "owner@demo.stockpilot.app", "password": "Demo-Password-2026"}
→ {"access_token": "eyJ…", "token_type": "bearer", "expires_in": 1800}
```

If the user has two-step verification on, the login response is instead
`{"two_factor_required": true, "challenge_token": "…"}`; finish with
`POST /api/auth/login/2fa {"challenge_token": "…", "code": "123456"}` (a recovery code also works).

Send `Authorization: Bearer <access_token>` on every request. The login response also sets an
httpOnly `sp_refresh` cookie; `POST /api/auth/refresh` (with header `X-Requested-With: StockPilot`)
returns a new access token and rotates the cookie. `POST /api/auth/logout` revokes it.

Everything is scoped to the signed-in user's organisation. IDs from another organisation return `404`.

## Conventions

- **Pagination**: `?page=1&page_size=25` (max 200) → `{items, total, page, page_size}`
- **Sorting**: `?sort=name` or `?sort=-on_hand` (allowed fields listed in the error if wrong)
- **Search / filters**: `?q=` on list endpoints, plus endpoint-specific filters
- **Quantities** are decimals in the product's base unit unless `unit_id` is given
- **Errors**: `{"detail": "human readable message", "code": "machine_code", …}`
  - `401` not signed in · `403` missing permission · `404` not found (or another tenant's)
  - `409` conflict — e.g. `insufficient_stock` (with `available` and `requested`), duplicate SKU
  - `422` validation — `errors: [{field, message}]` · `429` rate limited

## Main endpoints

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/signup` · `POST /api/auth/login` · `POST /api/auth/refresh` · `POST /api/auth/logout` · `GET /api/auth/me` · `POST /api/auth/change-password` · `GET /api/auth/industries` |
| Products | `GET/POST /api/products` · `GET/PUT/DELETE /api/products/{id}` · `GET /api/products/lookup?code=` (barcode, SKU, serial or batch) · `GET /api/products/{id}/movements` · `GET /api/products/{id}/batches` · `POST/DELETE /api/products/{id}/conversions` |
| Catalogue | `GET/POST /api/categories` · `PUT/DELETE /api/categories/{id}` · `GET/POST /api/units` |
| Places | `GET/POST /api/warehouses` · `PUT /api/warehouses/{id}` · `GET/POST /api/warehouses/{id}/locations` · `DELETE /api/locations/{id}` · `GET /api/locations` |
| Stock | `GET /api/stock` · `GET /api/movements` · `GET /api/batches?expiring_within=30` · `GET /api/serials` · `GET /api/serials/{id}/history` · `GET /api/adjustments` |
| Operations | `POST /api/inventory/receive` · `POST /api/inventory/issue` · `POST /api/inventory/transfer` · `POST /api/inventory/adjust` · `POST /api/inventory/opening-stock` |
| Transfers | `GET/POST /api/transfers` · `GET /api/transfers/{id}` · `POST …/{id}/submit` · `…/approve` · `…/dispatch` · `…/receive` · `…/cancel` |
| Partners | `GET/POST /api/suppliers` · `GET/PUT /api/suppliers/{id}` · same for `/api/customers` |
| Reports | `GET /api/reports` · `GET /api/reports/{name}?format=json\|csv\|xlsx\|pdf` — `valuation`, `stock-on-hand`, `low-stock`, `out-of-stock`, `expiry`, `movements`, `batches`, `serials`, `locations`, `adjustments`, `purchases`, `suppliers`, `sales`, `sales-by-product` |
| Overview | `GET /api/dashboard` · `GET /api/search?q=` · `GET /api/notifications` · `GET /api/notifications/unread-count` · `POST /api/notifications/{id}/read` · `POST /api/notifications/read-all` |
| Admin | `GET/POST /api/users` · `PUT /api/users/{id}` · `GET/POST /api/roles` · `PUT /api/roles/{id}` · `GET /api/permissions` · `GET/PUT /api/settings` · `GET /api/audit-logs` |
| Purchase orders | `GET/POST /api/purchase-orders` · `GET/PUT /api/purchase-orders/{id}` · `POST …/{id}/confirm` · `…/receive` · `…/close` · `GET /api/purchase-orders/suggestions` · `POST /api/purchase-orders/from-low-stock` |
| Sales orders | `GET/POST /api/sales-orders` · `GET/PUT /api/sales-orders/{id}` · `POST …/{id}/confirm` · `…/fulfil` · `…/payments` · `…/close` |
| Variants | `POST /api/products` with `variant_options` · `POST /api/products/{id}/variants` · `PUT /api/products/{id}` with `apply_to_variants` |
| Barcodes & labels | `POST /api/products/barcodes/generate` · `GET /api/labels/layouts` · `POST /api/labels` (returns a PDF) |
| Import | `GET /api/imports` · `GET /api/imports/{kind}/template?format=xlsx\|csv` · `POST …/{kind}/preview` (file) · `POST …/{kind}/validate` · `POST …/{kind}/commit` — kinds: `products`, `suppliers`, `customers`, `opening-stock` |
| Production | `GET/PUT /api/products/{id}/components` · `GET /api/production/availability` · `GET/POST /api/production-runs` · `GET /api/production-runs/{id}` |
| Files | `GET /api/attachments?entity_type=&entity_id=` · `POST /api/attachments` (multipart) · `GET /api/attachments/{id}/file` · `DELETE /api/attachments/{id}` |
| Account | `POST /api/auth/forgot-password` · `POST /api/auth/reset-password` · `POST /api/auth/verify-email` · `POST /api/auth/resend-verification` · `POST /api/auth/2fa/setup` · `…/2fa/enable` · `…/2fa/disable` · `POST /api/auth/login/2fa` · `GET/PUT /api/me/notifications` |
| Till (counter sales) | `GET /api/pos/bootstrap` · `GET /api/pos/catalog?location_id=` · `GET/POST /api/pos/shifts` · `GET /api/pos/shifts/current` · `GET /api/pos/shifts/{id}` · `POST …/{id}/cash` · `POST …/{id}/close` |
| Counter receipts | `GET/POST /api/pos/sales` · `GET /api/pos/sales/{id or number}` · `POST …/reviewed` · `POST …/send` · `POST /api/pos/returns` · `GET /api/pos/summary?date=&warehouse_id=` |
| Promotions | `GET/POST /api/promotions` · `PUT/DELETE /api/promotions/{id}` |
| Subscription | `GET /api/billing` · `POST /api/billing/checkout` · `POST /api/billing/verify` · `POST /api/billing/cancel` · `…/resume` · `PUT /api/billing/email` · `POST /api/billing/webhook` (Paystack, signed) |
| Public | `GET /api/public/receipts/{token}` (no sign-in; the customer's receipt link) |
| Ops | `GET /api/health` · `GET /api/ready` |

## Examples

Receive 3 cartons (1 carton = 24 pieces) at €2.40 per carton into a batch:

```http
POST /api/inventory/receive
{
  "product_id": "…", "location_id": "…", "quantity": 3, "unit_id": "<carton unit id>",
  "unit_cost": 2.40, "batch_number": "LOT-0915", "expires_on": "2027-03-31",
  "supplier_id": "…", "reference_no": "INV-88213"
}
→ 201 {"message": "Received 3 ctn of …", "on_hand": "72", "movements": [ … ]}
```

Sell 7 units; batches are picked earliest-expiry-first unless `batch_id` is given:

```http
POST /api/inventory/issue
{"product_id": "…", "location_id": "…", "quantity": 7, "type": "sale", "customer_id": "…"}
```

`type` is one of `issue`, `sale`, `damaged`, `lost`, `expired`, `production_consumption`.
For serial-tracked products pass `"serials": ["SN1", "SN2"]` (count must equal `quantity`).

Count correction (sets stock to what was counted):

```http
POST /api/inventory/adjust
{"reason": "count_correction", "notes": "Monthly count",
 "lines": [{"product_id": "…", "location_id": "…", "counted_qty": 37}]}
```

Move stock in one step:

```http
POST /api/inventory/transfer
{"from_location_id": "…", "to_location_id": "…", "product_id": "…", "quantity": 12}
```

Low-stock report as Excel:

```http
GET /api/reports/low-stock?format=xlsx
```

Order from a supplier, then receive part of it with a batch:

```http
POST /api/purchase-orders
{"supplier_id": "…", "destination_location_id": "…", "expected_date": "2026-11-15", "confirm": true,
 "lines": [{"product_id": "…", "quantity": 10, "unit_id": "<carton>", "unit_cost": 12000}]}

POST /api/purchase-orders/{id}/receive
{"lines": [{"item_id": "…", "quantity": 120, "batch_number": "LOT-77", "expires_on": "2027-06-30"}]}
```

Sell, send everything, and mark as paid:

```http
POST /api/sales-orders
{"customer_id": "…", "source_location_id": "…", "confirm": true,
 "lines": [{"product_id": "…", "quantity": 3, "unit_price": 9000, "discount_percent": 10}]}

POST /api/sales-orders/{id}/fulfil          (no body = send everything left)
POST /api/sales-orders/{id}/payments {}     (no amount = pay the balance)
```

Counter sale: start a shift, then sell 2 items, part transfer, part cash with change:

```http
POST /api/pos/shifts {"location_id": "…", "opening_float": 10000}

POST /api/pos/sales
{"client_id": "<uuid made by the till>", "shift_id": "…", "customer_phone": "0803 555 0101",
 "lines": [{"product_id": "…", "quantity": 2}, {"product_id": "…", "quantity": 1, "discount": {"type": "percent", "value": 5}}],
 "payments": [{"method": "transfer", "amount": 5000, "reference": "GTB 2231"}, {"method": "cash", "amount": 10000}]}
→ 201 {"number": "R-00012", "total": 13700, "change_given": 1300, "receipt_url": "https://…/r/…", …}
```

Prices, promotions and VAT are worked out on the server. A till that was offline sends the same
request with `"offline": true` and its original `sold_at`; sending it again returns the first sale
(`200`, `"duplicate": true`). Methods: `cash`, `transfer`, `card`, `other`, `credit` (store credit,
with `"credit_ref": "RET-00004"`).

Return part of a receipt as store credit, then close the shift:

```http
POST /api/pos/returns
{"shift_id": "…", "original_sale_id": "R-00012", "refund_method": "credit",
 "lines": [{"original_item_id": "…", "quantity": 1, "restock": true}]}

POST /api/pos/shifts/{id}/close {"counted_cash": 23500, "note": "Short 200"}
```
