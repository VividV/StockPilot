"""Counter sales (point of sale): till shifts, checkout, payments, returns,
promotions and cash-up.

Built on the inventory engine like everything else: a counter sale calls
``inventory.issue`` (type ``sale``) and a return calls ``inventory.receive``
(type ``return``), so stock rules, FEFO, serials and the ledger are unchanged.

Offline sales
-------------
A till that loses the internet keeps selling and uploads its sales later with
``offline=True`` and its own ``client_id``. Uploading the same sale twice
returns the first one (``client_id`` is unique per company). By the time an
offline sale arrives the goods have already left the shop, so it is never
refused for business reasons: if stock, a price or a discount doesn't add up,
the sale is saved, marked "needs checking" and the manager is alerted.
"""
from __future__ import annotations

import secrets
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Customer, MovementType, Organization, PosCashMovement, PosPayment, PosSale, PosSaleItem, PosShift, Product, Promotion,
    StockBatch, StockMovement,
)
from app.services import inventory as inv
from app.services.inventory import Ctx, InventoryError, NotFound, fmt, q

ZERO = Decimal(0)
CENT = Decimal("0.01")
METHODS = {"cash": "Cash", "transfer": "Bank transfer", "card": "POS / card", "other": "Other", "credit": "Store credit"}
DEFAULTS = {
    "prices_include_tax": True,
    "payment_methods": ["cash", "transfer", "card"],
    "max_discount_percent": 20,
    "receipt_header": "",
    "receipt_footer": "Thank you for shopping with us!",
    "paper_width": 80,
}


def m2(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def settings_of(org: Organization) -> dict:
    s = dict(DEFAULTS)
    s.update((org.settings or {}).get("pos") or {})
    return s


def tz_of(org: Organization) -> ZoneInfo:
    try:
        return ZoneInfo((org.settings or {}).get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def day_bounds(org: Organization, d: date) -> tuple[datetime, datetime]:
    tz = tz_of(org)
    start = datetime.combine(d, time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def local_today(org: Organization) -> date:
    return datetime.now(tz_of(org)).date()


# =========================================================================== shifts
def current_shift(db: Session, ctx: Ctx) -> PosShift | None:
    return db.execute(select(PosShift).where(PosShift.org_id == ctx.org_id, PosShift.user_id == ctx.user_id,
                                             PosShift.status == "open")).unique().scalar_one_or_none()


def get_shift(db: Session, ctx: Ctx, shift_id, *, lock: bool = False) -> PosShift:
    stmt = select(PosShift).where(PosShift.id == shift_id, PosShift.org_id == ctx.org_id)
    if lock:
        stmt = stmt.with_for_update(of=PosShift).execution_options(populate_existing=True)
    s = db.execute(stmt).unique().scalar_one_or_none()
    if not s:
        raise NotFound("Shift not found", code="not_found")
    return s


def open_shift(db: Session, ctx: Ctx, *, location_id, opening_float) -> PosShift:
    loc = inv.get_location(db, ctx, location_id)
    if current_shift(db, ctx):
        raise InventoryError("You already have a shift open. Close it before starting another.", code="shift_open")
    if Decimal(str(opening_float or 0)) < 0:
        raise InventoryError("Cash in the drawer can't be negative", code="invalid_amount")
    s = PosShift(org_id=ctx.org_id, number=inv.next_number(db, ctx.org_id, "SH"), location_id=loc.id, user_id=ctx.user_id,
                 status="open", opening_float=m2(opening_float), closing_summary={})
    db.add(s)
    db.flush()
    return s


def _own_or_manager(shift: PosShift, ctx: Ctx, perms: set[str]) -> None:
    if shift.user_id != ctx.user_id and "pos.manage" not in perms:
        raise InventoryError("This is someone else's shift", code="not_your_shift")


def cash_movement(db: Session, ctx: Ctx, perms: set[str], shift: PosShift, *, kind: str, amount, reason: str) -> PosCashMovement:
    _own_or_manager(shift, ctx, perms)
    if shift.status != "open":
        raise InventoryError("This shift is closed", code="shift_closed")
    if kind not in ("in", "out"):
        raise InventoryError("Choose money in or money out", code="invalid_kind")
    amt = m2(amount)
    if amt <= 0:
        raise InventoryError("Enter an amount greater than zero", code="invalid_amount")
    if not (reason or "").strip():
        raise InventoryError("Say what the money was for", code="reason_required")
    mv = PosCashMovement(org_id=ctx.org_id, shift_id=shift.id, kind=kind, amount=amt, reason=reason.strip()[:200], user_id=ctx.user_id)
    db.add(mv)
    db.flush()
    return mv


def shift_summary(db: Session, shift: PosShift) -> dict:
    by_method: dict[str, dict] = defaultdict(lambda: {"in": ZERO, "out": ZERO})
    for method, amount in db.execute(select(PosPayment.method, PosPayment.amount).where(PosPayment.shift_id == shift.id)):
        by_method[method]["in" if amount > 0 else "out"] += amount
    sales = db.execute(select(PosSale.kind, func.count(), func.coalesce(func.sum(PosSale.total), 0),
                              func.coalesce(func.sum(PosSale.discount), 0), func.coalesce(func.sum(PosSale.tax), 0),
                              func.coalesce(func.sum(PosSale.cost_of_goods), 0))
                       .where(PosSale.shift_id == shift.id).group_by(PosSale.kind)).all()
    k = {kind: {"count": n, "total": m2(t), "discount": m2(d), "tax": m2(tx), "cogs": m2(c)} for kind, n, t, d, tx, c in sales}
    s, r = k.get("sale", {"count": 0, "total": ZERO, "discount": ZERO, "tax": ZERO, "cogs": ZERO}), \
        k.get("return", {"count": 0, "total": ZERO, "discount": ZERO, "tax": ZERO, "cogs": ZERO})
    moves = db.execute(select(PosCashMovement).where(PosCashMovement.shift_id == shift.id).order_by(PosCashMovement.created_at)).unique().scalars().all()
    cash_in = sum((x.amount for x in moves if x.kind == "in"), ZERO)
    cash_out = sum((x.amount for x in moves if x.kind == "out"), ZERO)
    cash = by_method.get("cash", {"in": ZERO, "out": ZERO})
    expected = m2(shift.opening_float + cash["in"] + cash["out"] + cash_in - cash_out)
    review = db.execute(select(func.count()).select_from(PosSale).where(PosSale.shift_id == shift.id, PosSale.needs_review.is_(True))).scalar_one()
    late = 0
    if shift.closed_at:
        late = db.execute(select(func.count()).select_from(PosSale).where(PosSale.shift_id == shift.id,
                                                                         PosSale.created_at > shift.closed_at)).scalar_one()
    return {
        "sales_count": s["count"], "sales_total": s["total"], "discounts": s["discount"], "tax": m2(s["tax"] - r["tax"]),
        "returns_count": r["count"], "returns_total": r["total"], "net_sales": m2(s["total"] - r["total"]),
        "cost_of_goods": m2(s["cogs"] - r["cogs"]),
        "methods": [{"method": mth, "label": METHODS.get(mth, mth), "received": m2(v["in"]), "refunded": m2(-v["out"]),
                     "net": m2(v["in"] + v["out"])} for mth, v in sorted(by_method.items())],
        "opening_float": m2(shift.opening_float), "cash_sales": m2(cash["in"]), "cash_refunds": m2(-cash["out"]),
        "cash_in": m2(cash_in), "cash_out": m2(cash_out), "expected_cash": expected,
        "cash_movements": [{"id": x.id, "kind": x.kind, "amount": x.amount, "reason": x.reason, "created_at": x.created_at,
                            "user_name": x.user.full_name if x.user else None} for x in moves],
        "needs_review": review, "synced_after_close": late,
    }


def close_shift(db: Session, ctx: Ctx, perms: set[str], shift: PosShift, *, counted_cash, note: str | None) -> PosShift:
    _own_or_manager(shift, ctx, perms)
    if shift.status != "open":
        raise InventoryError("This shift is already closed", code="shift_closed")
    counted = m2(counted_cash)
    if counted < 0:
        raise InventoryError("Counted cash can't be negative", code="invalid_amount")
    summary = shift_summary(db, shift)
    shift.status = "closed"
    shift.closed_at = datetime.now(timezone.utc)
    shift.closed_by = ctx.user_id
    shift.counted_cash = counted
    shift.expected_cash = summary["expected_cash"]
    shift.closing_note = (note or "").strip() or None
    shift.closing_summary = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in summary.items()
                             if k not in ("cash_movements", "methods")} | {
        "methods": [{k2: str(v2) if isinstance(v2, Decimal) else v2 for k2, v2 in m_.items()} for m_ in summary["methods"]]}
    db.flush()
    return shift


# =========================================================================== promotions
def active_promotions(db: Session, org_id, today: date) -> list[Promotion]:
    return db.execute(select(Promotion).where(
        Promotion.org_id == org_id, Promotion.is_active.is_(True),
        (Promotion.starts_on.is_(None)) | (Promotion.starts_on <= today),
        (Promotion.ends_on.is_(None)) | (Promotion.ends_on >= today)).order_by(Promotion.created_at)).scalars().all()


def promo_applies(pr: Promotion, product: Product, qty: Decimal) -> bool:
    if qty < (pr.min_qty or 1):
        return False
    if pr.scope == "all":
        return True
    if pr.scope == "category":
        return pr.category_id is not None and product.category_id == pr.category_id
    ids = {str(x) for x in (pr.product_ids or [])}
    return str(product.id) in ids or (product.parent_id is not None and str(product.parent_id) in ids)


def promo_amount(pr: Promotion, gross: Decimal, qty: Decimal) -> Decimal:
    if pr.kind == "percent":
        return m2(gross * min(pr.value, Decimal(100)) / 100)
    return m2(min(pr.value * qty, gross))


# =========================================================================== pricing
def price_lines(lines: list[dict], products: dict, promos: list[Promotion], *, inclusive: bool,
                cart_discount: dict | None) -> dict:
    """Work out every amount on a sale. ``lines``: product_id, quantity, unit_price?, discount?{type,value}.
    The same arithmetic runs in the till (frontend/src/lib/pos.ts) so receipts printed offline match."""
    out = []
    for ln in lines:
        p: Product = products[str(ln["product_id"])]
        qty = q(ln["quantity"])
        price = m2(ln["unit_price"]) if ln.get("unit_price") is not None else m2(p.selling_price)
        gross = m2(qty * price)
        best, best_amt = None, ZERO
        for pr in promos:
            if promo_applies(pr, p, qty):
                a = promo_amount(pr, gross, qty)
                if a > best_amt:
                    best, best_amt = pr, a
        after_promo = gross - best_amt
        manual = ZERO
        d = ln.get("discount") or None
        if d and Decimal(str(d.get("value") or 0)) > 0:
            v = Decimal(str(d["value"]))
            manual = m2(after_promo * min(v, Decimal(100)) / 100) if d.get("type") == "percent" else m2(min(v, after_promo))
        out.append({"product": p, "qty": qty, "list_price": m2(p.selling_price), "unit_price": price, "gross": gross,
                    "promotion": best, "promo": best_amt, "after_promo": after_promo, "manual": manual,
                    "net": after_promo - manual, "serials": ln.get("serials") or [], "batch_number": ln.get("batch_number")})
    base = sum((x["net"] for x in out), ZERO)
    cart = ZERO
    if cart_discount and Decimal(str(cart_discount.get("value") or 0)) > 0 and base > 0:
        v = Decimal(str(cart_discount["value"]))
        cart = m2(base * min(v, Decimal(100)) / 100) if cart_discount.get("type") == "percent" else m2(min(v, base))
    left = cart
    for i, x in enumerate(out):
        share = left if i == len(out) - 1 else (m2(cart * x["net"] / base) if base else ZERO)
        share = min(share, x["net"], left)
        left -= share
        x["cart_share"] = share
        x["discount"] = x["promo"] + x["manual"] + share
        amount = x["gross"] - x["discount"]
        rate = Decimal(x["product"].tax_rate or 0)
        if inclusive:
            x["tax"] = m2(amount * rate / (100 + rate)) if rate else ZERO
            x["total"] = amount
        else:
            x["tax"] = m2(amount * rate / 100)
            x["total"] = amount + x["tax"]
        x["tax_rate"] = rate
    totals = {"subtotal": sum((x["gross"] for x in out), ZERO), "discount": sum((x["discount"] for x in out), ZERO),
              "tax": sum((x["tax"] for x in out), ZERO), "total": sum((x["total"] for x in out), ZERO),
              "manual_discount": sum((x["manual"] + x["cart_share"] for x in out), ZERO),
              "after_promo": sum((x["after_promo"] for x in out), ZERO)}
    return {"lines": out, **totals}


# =========================================================================== sales
def _flag(sale: PosSale, note: str) -> None:
    sale.needs_review = True
    sale.review_note = f"{sale.review_note}\n{note}" if sale.review_note else note


def _sellable(db: Session, ctx: Ctx, product_id) -> Product:
    p = inv.get_product(db, ctx, product_id)
    if p.has_variants:
        raise InventoryError(f"{p.name} comes in several sizes/colours. Choose the exact one.", code="variant_required")
    return p


def _require_shift(db: Session, ctx: Ctx, shift_id, offline: bool) -> PosShift:
    if not shift_id:
        raise InventoryError("Start your shift first", code="no_shift")
    shift = get_shift(db, ctx, shift_id)
    if shift.user_id != ctx.user_id:
        raise InventoryError("This is someone else's shift", code="not_your_shift")
    if shift.status != "open" and not offline:
        raise InventoryError("Your shift is closed. Start a new shift to keep selling.", code="shift_closed")
    return shift


def _use_credit(db: Session, ctx: Ctx, ref: str, amount: Decimal, offline: bool, sale: PosSale) -> PosSale | None:
    ref = (ref or "").strip()
    cond = PosSale.number == ref.upper()
    try:
        cond = (PosSale.id == uuid.UUID(ref)) | cond
    except ValueError:
        pass
    ret = db.execute(select(PosSale).where(PosSale.org_id == ctx.org_id, PosSale.kind == "return", cond)
                     .with_for_update(of=PosSale)).unique().scalar_one_or_none()
    if not ret:
        if offline:
            _flag(sale, f"Store credit {ref} was not found")
            return None
        raise InventoryError(f"No store credit found for {ref}", code="credit_not_found")
    if ret.credit_remaining < amount:
        if not offline:
            raise InventoryError(f"{ret.number} only has {fmt(ret.credit_remaining)} of store credit left", code="credit_insufficient")
        _flag(sale, f"Store credit {ret.number} had only {fmt(ret.credit_remaining)} left")
        amount = ret.credit_remaining
    ret.credit_remaining = m2(ret.credit_remaining - amount)
    return ret


def create_sale(db: Session, ctx: Ctx, perms: set[str], org: Organization, data: dict) -> tuple[PosSale, bool]:
    offline = bool(data.get("offline"))
    client_id = data.get("client_id")
    if client_id:
        existing = db.execute(select(PosSale).where(PosSale.org_id == ctx.org_id, PosSale.client_id == client_id)).unique().scalar_one_or_none()
        if existing:
            return existing, False
    shift = _require_shift(db, ctx, data.get("shift_id"), offline)
    location = inv.get_location(db, ctx, shift.location_id)
    settings = settings_of(org)
    lines = data.get("lines") or []
    if not lines:
        raise InventoryError("The basket is empty", code="empty")
    products = {str(ln["product_id"]): _sellable(db, ctx, ln["product_id"]) for ln in lines}
    for ln in lines:
        p = products[str(ln["product_id"])]
        if p.track_serials:
            serials = [s_.strip() for s_ in (ln.get("serials") or []) if s_ and s_.strip()]
            if not serials:
                raise InventoryError(f"Scan the serial number of {p.name}", code="serial_required")
            ln["serials"] = serials
            ln["quantity"] = len(serials)
        inv.to_base_qty(db, p, ln["quantity"], None)  # validates whole units etc.

    sold_at = data.get("sold_at") or datetime.now(timezone.utc)
    if offline and sold_at > datetime.now(timezone.utc) + timedelta(minutes=10):
        sold_at = datetime.now(timezone.utc)
    sale = PosSale(org_id=ctx.org_id, number=inv.next_number(db, ctx.org_id, "R"), kind="sale", client_id=client_id,
                   shift_id=shift.id, location_id=location.id, user_id=ctx.user_id, offline=offline, sold_at=sold_at,
                   note=(data.get("note") or None), public_token=secrets.token_urlsafe(18))
    if shift.status != "open":
        _flag(sale, f"Uploaded after shift {shift.number} was closed")
    cust_label = None
    if data.get("customer_id"):
        c = db.get(Customer, data["customer_id"])
        if not c or c.org_id != ctx.org_id:
            raise NotFound("Customer not found", code="not_found")
        sale.customer_id = c.id
        sale.customer_name = c.name
        sale.customer_phone = (data.get("customer_phone") or c.phone or None)
        cust_label = c.name
    else:
        sale.customer_name = (data.get("customer_name") or "").strip()[:200] or None
        sale.customer_phone = (data.get("customer_phone") or "").strip()[:32] or None
        cust_label = sale.customer_name
    db.add(sale)
    db.flush()

    promos = active_promotions(db, ctx.org_id, (sold_at.astimezone(tz_of(org))).date())
    priced = price_lines(lines, products, promos, inclusive=bool(settings["prices_include_tax"]),
                         cart_discount=data.get("cart_discount"))

    # --- permission rules for prices and discounts
    problems = []
    if any(x["unit_price"] != x["list_price"] for x in priced["lines"]) and "pos.price_override" not in perms:
        problems.append("A price was changed without permission to change prices")
    if priced["manual_discount"] > 0:
        if "pos.discount" not in perms and "pos.price_override" not in perms:
            problems.append("A discount was given without permission to give discounts")
        elif "pos.price_override" not in perms and priced["after_promo"] > 0:
            pct = priced["manual_discount"] / priced["after_promo"] * 100
            if pct > Decimal(str(settings["max_discount_percent"])) + Decimal("0.01"):
                problems.append(f"Discount of {pct:.0f}% is above the shop's limit of {settings['max_discount_percent']}%")
    if problems:
        if not offline:
            raise InventoryError(problems[0] + ".", code="discount_not_allowed")
        for pr in problems:
            _flag(sale, pr)
    if offline:
        changed = [x["product"].name for x in priced["lines"] if x["unit_price"] != x["list_price"]]
        if changed and "pos.price_override" in perms:
            pass
        elif changed:
            _flag(sale, "Sold at a different price from today's: " + ", ".join(changed[:5]))

    # --- stock
    stock_ctx = replace(ctx, org_settings={**(ctx.org_settings or {}), "allow_negative_stock": True}) if offline else ctx
    counterparty = cust_label or "Walk-in customer"
    for n, x in enumerate(priced["lines"], 1):
        p: Product = x["product"]
        item = PosSaleItem(sale_id=sale.id, line_no=n, product_id=p.id, quantity=x["qty"], list_price=x["list_price"],
                           unit_price=x["unit_price"], discount=x["discount"], promotion_id=x["promotion"].id if x["promotion"] else None,
                           promotion_name=x["promotion"].name if x["promotion"] else None, tax_rate=x["tax_rate"], tax=x["tax"],
                           total=x["total"], serials=x["serials"], returned_qty=0, stock_deducted=True, restocked=True)
        kw = dict(product_id=p.id, location_id=location.id, quantity=x["qty"], serials=x["serials"] or None, mtype=MovementType.SALE,
                  reference_type="pos_sale", reference_id=sale.id, reference_no=sale.number, counterparty=counterparty)
        if offline:
            sp = db.begin_nested()
            try:
                mvs = inv.issue(db, stock_ctx, **kw)
                sp.commit()
                if any(mv.balance_after < 0 for mv in mvs) and not ctx.allow_negative:
                    _flag(sale, f"{p.name} went below zero in stock after this sale. Count it and correct the stock.")
            except InventoryError as e:
                sp.rollback()
                mvs = []
                item.stock_deducted = False
                _flag(sale, f"{fmt(x['qty'])} × {p.name} not taken off stock: {e.message}")
        else:
            mvs = inv.issue(db, stock_ctx, **kw)
        item.cost_of_goods = m2(sum((-mv.quantity * (mv.unit_cost or ZERO) for mv in mvs), ZERO)) if mvs \
            else m2(x["qty"] * (p.average_cost or ZERO))
        sale.items.append(item)

    sale.subtotal = m2(priced["subtotal"])
    sale.discount = m2(priced["discount"])
    sale.tax = m2(priced["tax"])
    sale.total = m2(priced["total"])
    sale.cost_of_goods = m2(sum((i.cost_of_goods for i in sale.items), ZERO))

    # --- payments
    allowed = set(settings["payment_methods"]) | {"credit"}
    pays = data.get("payments") or []
    if not pays and sale.total > 0:
        if offline:
            _flag(sale, "No payment was recorded")
        else:
            raise InventoryError("Choose how the customer is paying", code="payment_required")
    tendered = ZERO
    cash_tendered = ZERO
    rows = []
    for py in pays:
        method = py.get("method")
        amount = m2(py.get("amount"))
        if amount <= 0:
            continue
        if method not in allowed:
            if offline:
                _flag(sale, f"Payment method '{method}' is switched off")
            else:
                raise InventoryError(f"{METHODS.get(method, method)} is not accepted in this shop", code="invalid_method")
        if method == "credit":
            ret = _use_credit(db, ctx, py.get("credit_ref") or "", amount, offline, sale)
            if ret is None:
                continue
            rows.append({"method": "credit", "amount": amount, "reference": ret.number, "credit_sale_id": ret.id})
        elif method == "cash":
            cash_tendered += amount
        else:
            rows.append({"method": method, "amount": amount, "reference": (py.get("reference") or "").strip()[:80] or None})
        tendered += amount
    non_cash = tendered - cash_tendered
    if non_cash > sale.total:
        if not offline:
            raise InventoryError("Transfer, card and credit payments add up to more than the total. Only cash can be more (for change).",
                                 code="overpaid")
        _flag(sale, "Non-cash payments were more than the total")
    if tendered < sale.total:
        if not offline:
            raise InventoryError(f"{fmt(sale.total - tendered)} still to pay", code="underpaid")
        _flag(sale, f"Paid {fmt(tendered)} of {fmt(sale.total)}")
    change = max(ZERO, min(tendered - sale.total, cash_tendered))
    if cash_tendered - change > 0:
        rows.insert(0, {"method": "cash", "amount": cash_tendered - change, "reference": None})
    for r_ in rows:
        sale.payments.append(PosPayment(org_id=ctx.org_id, shift_id=shift.id, **r_))
    sale.amount_tendered = m2(tendered)
    sale.change_given = m2(change)
    db.flush()
    if sale.needs_review:
        inv._upsert_alert(db, ctx.org_id, type_="pos_review", severity="warning", title=f"Sale {sale.number} needs checking",
                          body=sale.review_note or "", entity_type="pos_sale", entity_id=str(sale.id))
    return sale, True


def get_sale(db: Session, ctx: Ctx, sale_id_or_number, *, lock: bool = False) -> PosSale:
    cond = PosSale.number == str(sale_id_or_number).strip().upper()
    try:
        cond = (PosSale.id == uuid.UUID(str(sale_id_or_number))) | cond
    except ValueError:
        pass
    stmt = select(PosSale).where(PosSale.org_id == ctx.org_id, cond)
    if lock:
        stmt = stmt.with_for_update(of=PosSale).execution_options(populate_existing=True)
    s = db.execute(stmt).unique().scalar_one_or_none()
    if not s:
        raise NotFound("Receipt not found", code="not_found")
    return s


# =========================================================================== returns
def _original_batch(db: Session, sale: PosSale, product_id) -> StockBatch | None:
    mv = db.execute(select(StockMovement).where(StockMovement.org_id == sale.org_id, StockMovement.reference_id == sale.id,
                                                StockMovement.product_id == product_id, StockMovement.batch_id.is_not(None))
                    .order_by(StockMovement.id)).unique().scalars().first()
    return mv.batch if mv else None


def create_return(db: Session, ctx: Ctx, perms: set[str], org: Organization, data: dict) -> tuple[PosSale, bool]:
    client_id = data.get("client_id")
    if client_id:
        existing = db.execute(select(PosSale).where(PosSale.org_id == ctx.org_id, PosSale.client_id == client_id)).unique().scalar_one_or_none()
        if existing:
            return existing, False
    shift = _require_shift(db, ctx, data.get("shift_id"), False)
    location = inv.get_location(db, ctx, shift.location_id)
    settings = settings_of(org)
    method = data.get("refund_method") or "cash"
    if method not in set(settings["payment_methods"]) | {"credit"}:
        raise InventoryError(f"{METHODS.get(method, method)} is not accepted in this shop", code="invalid_method")
    lines = data.get("lines") or []
    if not lines:
        raise InventoryError("Choose what is being returned", code="empty")
    original = get_sale(db, ctx, data["original_sale_id"], lock=True) if data.get("original_sale_id") else None
    if original and original.kind != "sale":
        raise InventoryError("That receipt is itself a return", code="invalid_original")

    ret = PosSale(org_id=ctx.org_id, number=inv.next_number(db, ctx.org_id, "RET"), kind="return", client_id=client_id,
                  shift_id=shift.id, location_id=location.id, user_id=ctx.user_id, original_sale_id=original.id if original else None,
                  customer_id=original.customer_id if original else None,
                  customer_name=original.customer_name if original else (data.get("customer_name") or None),
                  customer_phone=original.customer_phone if original else (data.get("customer_phone") or None),
                  note=(data.get("reason") or "").strip()[:500] or None, public_token=secrets.token_urlsafe(18),
                  sold_at=datetime.now(timezone.utc))
    db.add(ret)
    db.flush()
    items_by_id = {i.id: i for i in original.items} if original else {}
    for n, ln in enumerate(lines, 1):
        restock = ln.get("restock", True)
        if original:
            oi = items_by_id.get(uuid.UUID(str(ln.get("original_item_id"))) if ln.get("original_item_id") else None)
            if not oi:
                raise NotFound("That item is not on the receipt", code="not_found")
            p = oi.product
            serials = [s_.strip() for s_ in (ln.get("serials") or []) if s_ and s_.strip()]
            qty = Decimal(len(serials)) if p.track_serials else q(ln.get("quantity") or 0)
            if p.track_serials:
                if not serials:
                    raise InventoryError(f"Choose which serial numbers of {p.name} are coming back", code="serial_required")
                bad = [s_ for s_ in serials if s_ not in (oi.serials or [])]
                if bad:
                    raise InventoryError(f"Serial {bad[0]} was not sold on {original.number}", code="serial_not_on_receipt")
            if qty <= 0:
                continue
            left = oi.quantity - oi.returned_qty
            if qty > left:
                raise InventoryError(f"Only {fmt(left)} × {p.name} can still be returned on {original.number}", code="over_return")
            done, after = oi.returned_qty, oi.returned_qty + qty
            total = m2(oi.total * after / oi.quantity) - m2(oi.total * done / oi.quantity)
            tax = m2(oi.tax * after / oi.quantity) - m2(oi.tax * done / oi.quantity)
            cost_each = (oi.cost_of_goods / oi.quantity) if oi.quantity else p.average_cost
            oi.returned_qty = after
            unit_price = oi.unit_price
            discount = m2(oi.discount * after / oi.quantity) - m2(oi.discount * done / oi.quantity)
            batch = _original_batch(db, original, p.id) if p.track_batches else None
            original_item_id = oi.id
        else:
            p = _sellable(db, ctx, ln["product_id"])
            serials = [s_.strip() for s_ in (ln.get("serials") or []) if s_ and s_.strip()]
            qty = Decimal(len(serials)) if p.track_serials else q(ln.get("quantity") or 0)
            if qty <= 0:
                continue
            inv.to_base_qty(db, p, qty, None)
            unit_price = m2(ln["unit_price"]) if ln.get("unit_price") is not None else m2(p.selling_price)
            if unit_price != m2(p.selling_price) and "pos.price_override" not in perms:
                raise InventoryError("Changing the refund price needs permission to change prices", code="discount_not_allowed")
            total = m2(unit_price * qty)
            rate = Decimal(p.tax_rate or 0)
            if settings["prices_include_tax"]:
                tax = m2(total * rate / (100 + rate)) if rate else ZERO
            else:
                tax = m2(total * rate / 100)
                total += tax
            cost_each = p.average_cost
            discount = ZERO
            batch = None
            original_item_id = None
            if p.track_batches and restock:
                bn = (ln.get("batch_number") or "").strip()
                if not bn:
                    raise InventoryError(f"Enter the batch number of the {p.name} coming back", code="batch_required")
                batch = db.execute(select(StockBatch).where(StockBatch.org_id == ctx.org_id, StockBatch.product_id == p.id,
                                                            StockBatch.batch_number == bn)).scalar_one_or_none()
                if not batch:
                    raise InventoryError(f"Batch {bn} of {p.name} is not known", code="batch_unknown")
        cost = m2(cost_each * qty) if restock else ZERO
        item = PosSaleItem(sale_id=ret.id, line_no=n, product_id=p.id, quantity=qty, list_price=m2(p.selling_price),
                           unit_price=unit_price, discount=discount, tax_rate=Decimal(p.tax_rate or 0), tax=tax, total=total,
                           cost_of_goods=cost, returned_qty=0, original_item_id=original_item_id, restocked=bool(restock),
                           stock_deducted=False, serials=serials)
        if restock:
            inv.receive(db, ctx, product_id=p.id, location_id=location.id, quantity=qty, unit_cost=m2(cost_each),
                        batch_number=batch.batch_number if batch else None, expires_on=batch.expires_on if batch else None,
                        serials=serials or None, mtype=MovementType.RETURN, reference_type="pos_return", reference_id=ret.id,
                        reference_no=ret.number, counterparty=ret.customer_name or "Walk-in customer",
                        note=f"Returned from {original.number}" if original else "Returned without a receipt")
        elif p.track_serials and serials:
            # not resellable: the serial stays out of stock, mark it written off
            from sqlalchemy import update
            from app.models import SerialNumber, SerialStatus
            db.execute(update(SerialNumber).where(SerialNumber.org_id == ctx.org_id, SerialNumber.product_id == p.id,
                                                  SerialNumber.serial.in_(serials)).values(status=SerialStatus.WRITTEN_OFF))
        ret.items.append(item)
    if not ret.items:
        raise InventoryError("Enter how many are coming back", code="empty")
    ret.subtotal = m2(sum((i.unit_price * i.quantity for i in ret.items), ZERO))
    ret.discount = m2(sum((i.discount for i in ret.items), ZERO))
    ret.tax = m2(sum((i.tax for i in ret.items), ZERO))
    ret.total = m2(sum((i.total for i in ret.items), ZERO))
    ret.cost_of_goods = m2(sum((i.cost_of_goods for i in ret.items), ZERO))
    if method == "credit":
        ret.credit_remaining = ret.total
    ret.payments.append(PosPayment(org_id=ctx.org_id, shift_id=shift.id, method=method, amount=-ret.total,
                                   reference=(data.get("reference") or "").strip()[:80] or None))
    db.flush()
    return ret, True


# =========================================================================== reporting
def summary(db: Session, org: Organization, day: date, warehouse_ids: set | None) -> dict:
    """Everything on the owner's 'Today' screen."""
    from app.models import Location, StockBalance, User, Warehouse
    start, end = day_bounds(org, day)

    def scoped(stmt):
        stmt = stmt.join(Location, Location.id == PosSale.location_id)
        return stmt.where(Location.warehouse_id.in_(warehouse_ids)) if warehouse_ids else stmt

    def totals(a, b):
        rows = db.execute(scoped(select(PosSale.kind, func.count(), func.coalesce(func.sum(PosSale.total), 0),
                                        func.coalesce(func.sum(PosSale.tax), 0), func.coalesce(func.sum(PosSale.cost_of_goods), 0),
                                        func.coalesce(func.sum(PosSale.discount), 0))
                                 .where(PosSale.org_id == org.id, PosSale.sold_at >= a, PosSale.sold_at < b))
                          .group_by(PosSale.kind)).all()
        t = {k: (n, Decimal(tot), Decimal(tx), Decimal(c), Decimal(d)) for k, n, tot, tx, c, d in rows}
        s_ = t.get("sale", (0, ZERO, ZERO, ZERO, ZERO))
        r_ = t.get("return", (0, ZERO, ZERO, ZERO, ZERO))
        net = s_[1] - r_[1]
        net_ex_tax = net - (s_[2] - r_[2])
        cogs = s_[3] - r_[3]
        return {"sales_count": s_[0], "sales_total": m2(s_[1]), "returns_count": r_[0], "returns_total": m2(r_[1]),
                "net_sales": m2(net), "tax": m2(s_[2] - r_[2]), "discounts": m2(s_[4]), "cost_of_goods": m2(cogs),
                "profit": m2(net_ex_tax - cogs), "average_sale": m2(s_[1] / s_[0]) if s_[0] else ZERO}

    t = totals(start, end)
    y = totals(start - timedelta(days=1), start)
    w = totals(start - timedelta(days=7), end - timedelta(days=7))

    by_branch = []
    for wid, wname, kind, n, tot, tx, c in db.execute(
            scoped(select(Warehouse.id, Warehouse.name, PosSale.kind, func.count(), func.coalesce(func.sum(PosSale.total), 0),
                          func.coalesce(func.sum(PosSale.tax), 0), func.coalesce(func.sum(PosSale.cost_of_goods), 0))
                   .where(PosSale.org_id == org.id, PosSale.sold_at >= start, PosSale.sold_at < end))
            .join(Warehouse, Warehouse.id == Location.warehouse_id).group_by(Warehouse.id, Warehouse.name, PosSale.kind)):
        b = next((x for x in by_branch if x["warehouse_id"] == wid), None)
        if not b:
            b = {"warehouse_id": wid, "name": wname, "sales_count": 0, "net_sales": ZERO, "profit": ZERO}
            by_branch.append(b)
        sign = 1 if kind == "sale" else -1
        if kind == "sale":
            b["sales_count"] += n
        b["net_sales"] = m2(b["net_sales"] + sign * Decimal(tot))
        b["profit"] = m2(b["profit"] + sign * (Decimal(tot) - Decimal(tx) - Decimal(c)))
    by_branch.sort(key=lambda x: -x["net_sales"])

    methods: dict = defaultdict(lambda: ZERO)
    for mth, amt in db.execute(scoped(select(PosPayment.method, func.sum(PosPayment.amount))
                                      .join(PosSale, PosSale.id == PosPayment.sale_id)
                                      .where(PosSale.org_id == org.id, PosSale.sold_at >= start, PosSale.sold_at < end))
                               .group_by(PosPayment.method)):
        methods[mth] += Decimal(amt)
    by_method = [{"method": k, "label": METHODS.get(k, k), "amount": m2(v)} for k, v in sorted(methods.items(), key=lambda x: -x[1])]

    tz = tz_of(org)
    hours = [ZERO] * 24
    cashiers: dict = {}
    for sold_at, total, kind, uid, uname in db.execute(
            scoped(select(PosSale.sold_at, PosSale.total, PosSale.kind, PosSale.user_id, User.full_name)
                   .outerjoin(User, User.id == PosSale.user_id)
                   .where(PosSale.org_id == org.id, PosSale.sold_at >= start, PosSale.sold_at < end))):
        sign = 1 if kind == "sale" else -1
        hours[sold_at.astimezone(tz).hour] += sign * total
        c_ = cashiers.setdefault(uid, {"user_id": uid, "name": uname or "Former staff", "sales_count": 0, "net_sales": ZERO})
        c_["net_sales"] = m2(c_["net_sales"] + sign * total)
        if kind == "sale":
            c_["sales_count"] += 1

    top = []
    for pid, name, sku, kind, qty, tot in db.execute(
            scoped(select(Product.id, Product.name, Product.sku, PosSale.kind, func.sum(PosSaleItem.quantity), func.sum(PosSaleItem.total))
                   .join(PosSaleItem, PosSaleItem.sale_id == PosSale.id).join(Product, Product.id == PosSaleItem.product_id)
                   .where(PosSale.org_id == org.id, PosSale.sold_at >= start, PosSale.sold_at < end))
            .group_by(Product.id, Product.name, Product.sku, PosSale.kind)):
        e = next((x for x in top if x["product_id"] == pid), None)
        if not e:
            e = {"product_id": pid, "name": name, "sku": sku, "quantity": ZERO, "revenue": ZERO}
            top.append(e)
        sign = 1 if kind == "sale" else -1
        e["quantity"] += sign * qty
        e["revenue"] = m2(e["revenue"] + sign * tot)
    top.sort(key=lambda x: -x["revenue"])

    # Slow movers: in stock here, nothing sold in the last 30 days, biggest money tied up first.
    since = end - timedelta(days=30)
    sold_recently = select(PosSaleItem.product_id).join(PosSale, PosSale.id == PosSaleItem.sale_id).where(
        PosSale.org_id == org.id, PosSale.kind == "sale", PosSale.sold_at >= since)
    stock = select(StockBalance.product_id, func.sum(StockBalance.quantity).label("qty")).join(
        Location, Location.id == StockBalance.location_id).where(StockBalance.org_id == org.id)
    if warehouse_ids:
        stock = stock.where(Location.warehouse_id.in_(warehouse_ids))
    stock = stock.group_by(StockBalance.product_id).subquery()
    slow = [{"product_id": p_.id, "name": p_.name, "sku": p_.sku, "on_hand": qty_, "value": m2(qty_ * p_.average_cost)}
            for p_, qty_ in db.execute(select(Product, stock.c.qty).join(stock, stock.c.product_id == Product.id)
                                      .where(Product.org_id == org.id, Product.is_active.is_(True), stock.c.qty > 0,
                                             Product.id.not_in(sold_recently))
                                      .order_by((stock.c.qty * Product.average_cost).desc()).limit(10)).unique().all()]

    open_shifts = []
    stmt = select(PosShift).where(PosShift.org_id == org.id, PosShift.status == "open").order_by(PosShift.opened_at)
    for sh in db.execute(stmt).unique().scalars():
        if warehouse_ids and sh.location.warehouse_id not in warehouse_ids:
            continue
        sm = shift_summary(db, sh)
        open_shifts.append({"id": sh.id, "number": sh.number, "user_name": sh.user.full_name if sh.user else None,
                            "location_name": sh.location.warehouse.name, "opened_at": sh.opened_at,
                            "sales_count": sm["sales_count"], "net_sales": sm["net_sales"], "expected_cash": sm["expected_cash"]})
    review = db.execute(select(func.count()).select_from(PosSale).where(PosSale.org_id == org.id, PosSale.needs_review.is_(True),
                                                                       PosSale.sold_at >= start - timedelta(days=7))).scalar_one()
    return {"date": day, "timezone": str(tz), "totals": t, "yesterday": y, "same_day_last_week": w, "by_branch": by_branch,
            "by_method": by_method, "by_hour": [m2(h) for h in hours],
            "by_cashier": sorted(cashiers.values(), key=lambda x: -x["net_sales"]),
            "top_products": top[:10], "slow_movers": slow, "open_shifts": open_shifts, "needs_review": review}
