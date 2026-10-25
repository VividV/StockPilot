"""Point of sale: /api/pos/*, /api/promotions and the public receipt page."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import Principal, require, require_any
from app.models import (
    Category, Location, LocationKind, PosSale, PosShift, Product, Promotion, SerialNumber, SerialStatus, StockBalance, Warehouse,
)
from app.routers.common import PageParams, location_label, paginate
from app.services import inventory as inv
from app.services import messaging
from app.services import pos as svc

router = APIRouter(prefix="/api/pos", tags=["point of sale"])
promotions = APIRouter(prefix="/api/promotions", tags=["promotions"])
public = APIRouter(prefix="/api/public", tags=["public"])

MoneyIn = Field(ge=0, max_digits=14, decimal_places=4)
OFFLINE_UPLOAD_DAYS_AFTER_LOCK = 7


# ------------------------------------------------------------------ schemas
class DiscountIn(BaseModel):
    type: Literal["percent", "amount"]
    value: Decimal = Field(ge=0, max_digits=14, decimal_places=4)


class SaleLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=4)
    discount: DiscountIn | None = None
    serials: list[str] | None = Field(None, max_length=500)


class PaymentLineIn(BaseModel):
    method: str = Field(max_length=20)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    reference: str | None = Field(None, max_length=80)
    credit_ref: str | None = Field(None, max_length=60)


class SaleIn(BaseModel):
    client_id: uuid.UUID | None = None
    shift_id: uuid.UUID | None = None
    offline: bool = False
    sold_at: datetime | None = None
    customer_id: uuid.UUID | None = None
    customer_name: str | None = Field(None, max_length=200)
    customer_phone: str | None = Field(None, max_length=32)
    note: str | None = Field(None, max_length=500)
    lines: list[SaleLineIn] = Field(min_length=1, max_length=300)
    cart_discount: DiscountIn | None = None
    payments: list[PaymentLineIn] = Field(default_factory=list, max_length=10)

    @field_validator("sold_at")
    @classmethod
    def _tz(cls, v):
        if v is not None and v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v


class ReturnLineIn(BaseModel):
    original_item_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    quantity: Decimal = Field(Decimal(0), ge=0, max_digits=18, decimal_places=4)
    unit_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=4)
    restock: bool = True
    serials: list[str] | None = None
    batch_number: str | None = Field(None, max_length=80)


class ReturnIn(BaseModel):
    client_id: uuid.UUID | None = None
    shift_id: uuid.UUID
    original_sale_id: str | None = Field(None, max_length=60, description="Receipt id or number, e.g. R-00012")
    lines: list[ReturnLineIn] = Field(min_length=1, max_length=300)
    refund_method: str = Field("cash", max_length=20)
    reference: str | None = Field(None, max_length=80)
    reason: str | None = Field(None, max_length=500)
    customer_name: str | None = Field(None, max_length=200)
    customer_phone: str | None = Field(None, max_length=32)


class ShiftOpenIn(BaseModel):
    location_id: uuid.UUID
    opening_float: Decimal = Field(Decimal(0), ge=0, max_digits=14, decimal_places=2)


class ShiftCloseIn(BaseModel):
    counted_cash: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    note: str | None = Field(None, max_length=1000)


class CashIn(BaseModel):
    kind: Literal["in", "out"]
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    reason: str = Field(min_length=1, max_length=200)


class SendIn(BaseModel):
    channel: Literal["email", "sms", "whatsapp"]
    to: str = Field(min_length=3, max_length=255)


class PromotionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["percent", "amount"]
    value: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    scope: Literal["all", "category", "products"] = "all"
    category_id: uuid.UUID | None = None
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    min_qty: Decimal = Field(Decimal(1), gt=0, max_digits=18, decimal_places=4)
    starts_on: date | None = None
    ends_on: date | None = None
    is_active: bool = True


# ------------------------------------------------------------------ serialisers
def receipt_settings(p_org) -> dict:
    s = svc.settings_of(p_org)
    return {"business_name": p_org.name, "header": s["receipt_header"], "footer": s["receipt_footer"],
            "paper_width": s["paper_width"], "prices_include_tax": s["prices_include_tax"], "currency": p_org.currency,
            "timezone": str(svc.tz_of(p_org))}


def receipt_url(sale: PosSale) -> str:
    return f"{get_settings().APP_URL.rstrip('/')}/r/{sale.public_token}"


def sale_out(db: Session, sale: PosSale, org, *, show_cost: bool = False, public_view: bool = False) -> dict:
    lines = []
    for i in sale.items:
        line = {"id": i.id, "line_no": i.line_no, "product_id": i.product_id, "name": i.product.name, "sku": i.product.sku,
                "unit": i.product.base_unit.abbreviation, "quantity": i.quantity, "list_price": i.list_price,
                "unit_price": i.unit_price, "discount": i.discount, "promotion_name": i.promotion_name, "tax_rate": i.tax_rate,
                "tax": i.tax, "total": i.total, "serials": i.serials or [], "returned_qty": i.returned_qty,
                "returnable_qty": (i.quantity - i.returned_qty) if sale.kind == "sale" else Decimal(0),
                "restocked": i.restocked, "stock_deducted": i.stock_deducted, "track_serials": i.product.track_serials}
        if show_cost:
            line["cost_of_goods"] = i.cost_of_goods
        lines.append(line)
    out = {
        "id": sale.id, "number": sale.number, "kind": sale.kind, "client_id": sale.client_id, "sold_at": sale.sold_at,
        "created_at": sale.created_at, "location_id": sale.location_id, "location_name": location_label(sale.location),
        "location_address": sale.location.warehouse.address, "cashier": sale.user.full_name if sale.user else None,
        "customer_id": sale.customer_id, "customer_name": sale.customer_name, "customer_phone": sale.customer_phone,
        "lines": lines, "subtotal": sale.subtotal, "discount": sale.discount, "tax": sale.tax, "total": sale.total,
        "payments": [{"method": x.method, "label": svc.METHODS.get(x.method, x.method), "amount": x.amount, "reference": x.reference}
                     for x in sale.payments],
        "amount_tendered": sale.amount_tendered, "change_given": sale.change_given, "credit_remaining": sale.credit_remaining,
        "note": sale.note, "receipt": receipt_settings(org), "receipt_url": receipt_url(sale),
    }
    if public_view:
        out.pop("client_id")
        out.pop("customer_id")
        out.pop("customer_phone")
        out["cashier"] = (out["cashier"] or "").split(" ")[0] or None
        return out
    out.update({"shift_id": sale.shift_id, "offline": sale.offline, "needs_review": sale.needs_review,
                "review_note": sale.review_note, "user_id": sale.user_id})
    if show_cost:
        out["cost_of_goods"] = sale.cost_of_goods
        out["profit"] = svc.m2(sale.total - sale.tax - sale.cost_of_goods)
    if sale.original_sale_id:
        o = db.get(PosSale, sale.original_sale_id)
        out["original_sale"] = {"id": o.id, "number": o.number} if o else None
    if sale.kind == "sale":
        out["returns"] = [{"id": r.id, "number": r.number, "total": r.total, "sold_at": r.sold_at} for r in db.execute(
            select(PosSale).where(PosSale.original_sale_id == sale.id).order_by(PosSale.created_at)).unique().scalars()]
    return out


def shift_out(db: Session, s: PosShift, *, with_summary: bool = True) -> dict:
    d = {"id": s.id, "number": s.number, "status": s.status, "location_id": s.location_id,
         "location_name": location_label(s.location), "warehouse_id": s.location.warehouse_id, "user_id": s.user_id,
         "user_name": s.user.full_name if s.user else None, "opening_float": s.opening_float, "opened_at": s.opened_at,
         "closed_at": s.closed_at, "counted_cash": s.counted_cash, "expected_cash_at_close": s.expected_cash,
         "difference": svc.m2(s.counted_cash - s.expected_cash) if s.counted_cash is not None and s.expected_cash is not None else None,
         "closing_note": s.closing_note}
    if with_summary:
        d["summary"] = svc.shift_summary(db, s)
    return d


def _sees_all(p: Principal) -> bool:
    return p.can("pos.manage") or p.can("reports.view")


def _sale_for(db: Session, p: Principal, ref: str) -> PosSale:
    sale = svc.get_sale(db, p.ctx, ref)
    if not _sees_all(p) and sale.user_id != p.user.id and not p.can("pos.refund"):
        raise HTTPException(404, "Receipt not found")
    if p.user.warehouse_ids and sale.location.warehouse_id not in p.user.warehouse_ids:
        raise HTTPException(404, "Receipt not found")
    return sale


def _pos_on(p: Principal) -> None:
    if not (p.org.settings or {}).get("modules", {}).get("pos"):
        raise HTTPException(409, "Counter sales are switched off. Turn on 'Counter sales (till)' in Settings → Features.")


# ------------------------------------------------------------------ setup for the till
@router.get("/bootstrap")
def bootstrap(p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    """Everything the till needs to start, including working offline."""
    allowed = p.user.warehouse_ids
    whs = db.execute(select(Warehouse).where(Warehouse.org_id == p.org_id, Warehouse.is_active.is_(True)).order_by(Warehouse.name)).scalars().all()
    locs = []
    for w in whs:
        if allowed and w.id not in allowed:
            continue
        loc = db.execute(select(Location).where(Location.warehouse_id == w.id, Location.kind == LocationKind.DEFAULT)).unique().scalar_one_or_none()
        if loc:
            locs.append({"location_id": loc.id, "warehouse_id": w.id, "name": w.name, "type": w.type.value, "address": w.address})
    shift = svc.current_shift(db, p.ctx)
    s = svc.settings_of(p.org)
    today = svc.local_today(p.org)
    return {
        "enabled": bool((p.org.settings or {}).get("modules", {}).get("pos")),
        "settings": s, "receipt": receipt_settings(p.org),
        "methods": [{"key": k, "label": svc.METHODS[k]} for k in s["payment_methods"] if k in svc.METHODS],
        "locations": locs, "shift": shift_out(db, shift) if shift else None,
        "promotions": [promotion_out(x) for x in svc.active_promotions(db, p.org_id, today)],
        "categories": [{"id": c.id, "name": c.name} for c in db.execute(select(Category).where(Category.org_id == p.org_id).order_by(Category.name)).scalars()],
        "can": {"discount": p.can("pos.discount") or p.can("pos.price_override"), "price_override": p.can("pos.price_override"),
                "refund": p.can("pos.refund"), "manage": p.can("pos.manage"), "customers": p.can("customers.manage")},
        "server_time": datetime.now(timezone.utc), "today": today,
    }


@router.get("/catalog")
def catalog(location_id: uuid.UUID, page: int = Query(1, ge=1), page_size: int = Query(2000, ge=1, le=5000),
            p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    """Every product that can be sold, with stock at this till's location. The till keeps a copy for offline use."""
    loc = inv.get_location(db, p.ctx, location_id)
    stock = (select(StockBalance.product_id, func.sum(StockBalance.quantity).label("qty"))
             .where(StockBalance.org_id == p.org_id, StockBalance.location_id == loc.id).group_by(StockBalance.product_id).subquery())
    base = (select(Product, func.coalesce(stock.c.qty, 0)).outerjoin(stock, stock.c.product_id == Product.id)
            .where(Product.org_id == p.org_id, Product.is_active.is_(True), Product.has_variants.is_(False)))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(Product.name, Product.id).limit(page_size).offset((page - 1) * page_size)).unique().all()
    serial_ids = [pr.id for pr, _ in rows if pr.track_serials]
    serials: dict = {}
    if serial_ids:
        for pid, sn in db.execute(select(SerialNumber.product_id, SerialNumber.serial).where(
                SerialNumber.org_id == p.org_id, SerialNumber.product_id.in_(serial_ids), SerialNumber.status == SerialStatus.IN_STOCK,
                SerialNumber.location_id == loc.id).order_by(SerialNumber.serial)):
            serials.setdefault(pid, []).append(sn)
    parents = {}
    pids = {pr.parent_id for pr, _ in rows if pr.parent_id}
    if pids:
        parents = {x.id: x.name for x in db.execute(select(Product).where(Product.id.in_(pids))).unique().scalars()}
    return {"items": [{
        "id": pr.id, "sku": pr.sku, "barcode": pr.barcode, "name": pr.name, "category_id": pr.category_id,
        "category": pr.category.name if pr.category else None, "price": svc.m2(pr.selling_price), "tax_rate": pr.tax_rate,
        "unit": pr.base_unit.abbreviation, "allow_decimals": pr.base_unit.allow_decimals, "track_serials": pr.track_serials,
        "track_batches": pr.track_batches, "parent_id": pr.parent_id, "parent_name": parents.get(pr.parent_id),
        "attributes": pr.variant_attributes or {}, "image_url": pr.image_url, "on_hand": qty,
        "serials": serials.get(pr.id, []) if pr.track_serials else [],
    } for pr, qty in rows], "total": total, "page": page, "page_size": page_size, "location_id": loc.id,
        "generated_at": datetime.now(timezone.utc)}


# ------------------------------------------------------------------ shifts
@router.get("/shifts/current")
def current(p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    s = svc.current_shift(db, p.ctx)
    return shift_out(db, s) if s else None


@router.post("/shifts", status_code=201)
def open_shift(body: ShiftOpenIn, p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    _pos_on(p)

    def op():
        s = svc.open_shift(db, p.ctx, location_id=body.location_id, opening_float=body.opening_float)
        p.audit(db, "pos.shift_open", entity_type="pos_shift", entity_id=s.id,
                summary=f"Opened {s.number} at {location_label(s.location)} with {svc.m2(s.opening_float)} in the drawer")
        return shift_out(db, s)
    return inv.run_with_retry(db, op)


@router.get("/shifts")
def list_shifts(status: Literal["open", "closed"] | None = None, user_id: uuid.UUID | None = None,
                date_from: date | None = None, date_to: date | None = None, params: PageParams = Depends(),
                p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    stmt = select(PosShift).join(Location, Location.id == PosShift.location_id).where(PosShift.org_id == p.org_id)
    if not _sees_all(p):
        stmt = stmt.where(PosShift.user_id == p.user.id)
    elif user_id:
        stmt = stmt.where(PosShift.user_id == user_id)
    if p.user.warehouse_ids:
        stmt = stmt.where(Location.warehouse_id.in_(p.user.warehouse_ids))
    if status:
        stmt = stmt.where(PosShift.status == status)
    if date_from:
        stmt = stmt.where(PosShift.opened_at >= svc.day_bounds(p.org, date_from)[0])
    if date_to:
        stmt = stmt.where(PosShift.opened_at < svc.day_bounds(p.org, date_to)[1])
    rows, total = paginate(db, stmt.order_by(PosShift.opened_at.desc()), params)
    return {"items": [shift_out(db, s) for s in rows], "total": total, "page": params.page, "page_size": params.page_size}


def _shift_for(db, p: Principal, shift_id, lock=False) -> PosShift:
    s = svc.get_shift(db, p.ctx, shift_id, lock=lock)
    if s.user_id != p.user.id and not _sees_all(p):
        raise HTTPException(404, "Shift not found")
    return s


@router.get("/shifts/{shift_id}")
def get_shift(shift_id: uuid.UUID, p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    s = _shift_for(db, p, shift_id)
    out = shift_out(db, s)
    out["sales"] = [{"id": x.id, "number": x.number, "kind": x.kind, "total": x.total, "sold_at": x.sold_at,
                     "customer_name": x.customer_name, "needs_review": x.needs_review, "offline": x.offline,
                     "methods": sorted({py.method for py in x.payments})}
                    for x in db.execute(select(PosSale).where(PosSale.shift_id == s.id).order_by(PosSale.sold_at.desc()).limit(1000)).unique().scalars()]
    return out


@router.post("/shifts/{shift_id}/cash", status_code=201)
def cash_move(shift_id: uuid.UUID, body: CashIn, p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    def op():
        s = svc.get_shift(db, p.ctx, shift_id, lock=True)
        mv = svc.cash_movement(db, p.ctx, p.permissions, s, kind=body.kind, amount=body.amount, reason=body.reason)
        p.audit(db, "pos.cash_" + body.kind, entity_type="pos_shift", entity_id=s.id,
                summary=f"Cash {'into' if body.kind == 'in' else 'out of'} the drawer on {s.number}: {mv.amount} ({mv.reason})")
        return shift_out(db, s)
    return inv.run_with_retry(db, op)


@router.post("/shifts/{shift_id}/close")
def close_shift(shift_id: uuid.UUID, body: ShiftCloseIn, p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    def op():
        s = svc.get_shift(db, p.ctx, shift_id, lock=True)
        svc.close_shift(db, p.ctx, p.permissions, s, counted_cash=body.counted_cash, note=body.note)
        diff = svc.m2(s.counted_cash - s.expected_cash)
        p.audit(db, "pos.shift_close", entity_type="pos_shift", entity_id=s.id,
                summary=f"Closed {s.number}: expected {s.expected_cash}, counted {s.counted_cash}, difference {diff}",
                after={"expected_cash": str(s.expected_cash), "counted_cash": str(s.counted_cash), "difference": str(diff)})
        if diff != 0:
            inv._upsert_alert(db, p.org_id, type_="pos_review", severity="warning",
                              title=f"Cash {'short' if diff < 0 else 'over'} by {abs(diff)} on {s.number}",
                              body=f"{s.user.full_name if s.user else 'Cashier'} at {location_label(s.location)}: expected "
                                   f"{s.expected_cash}, counted {s.counted_cash}." + (f" Note: {s.closing_note}" if s.closing_note else ""),
                              entity_type="pos_shift", entity_id=str(s.id))
        return shift_out(db, s)
    return inv.run_with_retry(db, op)


# ------------------------------------------------------------------ sales
@router.post("/sales", status_code=201)
def create_sale(body: SaleIn, response: Response, p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    if not body.offline:
        _pos_on(p)
    if p.locked:
        # Sales a till made while offline before the subscription ended still upload for a while;
        # after that, an "offline" sale is just a way round the lock.
        from app.models import Subscription
        sub = db.get(Subscription, p.org_id)
        late = sub is None or datetime.now(timezone.utc) - sub.updated_at > timedelta(days=OFFLINE_UPLOAD_DAYS_AFTER_LOCK)
        if not body.offline or late:
            raise HTTPException(402, "Your StockPilot subscription has ended, so new sales are paused. The owner can choose a plan on the Billing page.")
    data = body.model_dump()
    data["lines"] = [l.model_dump() for l in body.lines]
    data["payments"] = [x.model_dump() for x in body.payments]
    data["cart_discount"] = body.cart_discount.model_dump() if body.cart_discount else None

    def op():
        sale, created = svc.create_sale(db, p.ctx, p.permissions, p.org, data)
        if created:
            p.audit(db, "pos.sale", entity_type="pos_sale", entity_id=sale.id,
                    summary=f"{sale.number}: {len(sale.items)} line(s), total {sale.total}"
                            + (" (offline, uploaded later)" if sale.offline else "") + (" — needs checking" if sale.needs_review else ""))
        else:
            response.status_code = 200
        out = sale_out(db, sale, p.org, show_cost=p.can("reports.view"))
        out["duplicate"] = not created
        return out
    return inv.run_with_retry(db, op)


@router.get("/sales")
def list_sales(q: str | None = None, shift_id: uuid.UUID | None = None, kind: Literal["sale", "return"] | None = None,
               needs_review: bool | None = None, date_from: date | None = None, date_to: date | None = None,
               warehouse_id: uuid.UUID | None = None, params: PageParams = Depends(),
               p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    stmt = select(PosSale).join(Location, Location.id == PosSale.location_id).where(PosSale.org_id == p.org_id)
    if not _sees_all(p) and not p.can("pos.refund"):
        stmt = stmt.where(PosSale.user_id == p.user.id)
    if p.user.warehouse_ids:
        stmt = stmt.where(Location.warehouse_id.in_(p.user.warehouse_ids))
    if warehouse_id:
        stmt = stmt.where(Location.warehouse_id == warehouse_id)
    if shift_id:
        stmt = stmt.where(PosSale.shift_id == shift_id)
    if kind:
        stmt = stmt.where(PosSale.kind == kind)
    if needs_review is not None:
        stmt = stmt.where(PosSale.needs_review.is_(needs_review))
    if date_from:
        stmt = stmt.where(PosSale.sold_at >= svc.day_bounds(p.org, date_from)[0])
    if date_to:
        stmt = stmt.where(PosSale.sold_at < svc.day_bounds(p.org, date_to)[1])
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(PosSale.number.ilike(like), PosSale.customer_name.ilike(like), PosSale.customer_phone.ilike(like)))
    rows, total = paginate(db, stmt.order_by(PosSale.sold_at.desc()), params)
    return {"items": [{"id": s.id, "number": s.number, "kind": s.kind, "sold_at": s.sold_at, "total": s.total,
                       "customer_name": s.customer_name, "cashier": s.user.full_name if s.user else None,
                       "location_name": location_label(s.location), "lines": len(s.items), "needs_review": s.needs_review,
                       "offline": s.offline, "methods": sorted({py.method for py in s.payments})} for s in rows],
            "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/sales/{ref}")
def get_sale(ref: str, p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    return sale_out(db, _sale_for(db, p, ref), p.org, show_cost=p.can("reports.view"))


@router.post("/sales/{ref}/reviewed")
def mark_reviewed(ref: str, p: Principal = Depends(require("pos.manage")), db: Session = Depends(get_db)):
    def op():
        sale = svc.get_sale(db, p.ctx, ref, lock=True)
        sale.needs_review = False
        inv._resolve_alerts(db, p.org_id, ["pos_review"], str(sale.id))
        p.audit(db, "pos.reviewed", entity_type="pos_sale", entity_id=sale.id, summary=f"Checked {sale.number}: {sale.review_note or ''}"[:500])
        return sale_out(db, sale, p.org, show_cost=p.can("reports.view"))
    return inv.run_with_retry(db, op)


def receipt_text(sale: PosSale, org) -> str:
    cur = org.currency
    lines = [org.name, f"{'Return' if sale.kind == 'return' else 'Receipt'} {sale.number}",
             sale.sold_at.astimezone(svc.tz_of(org)).strftime("%d %b %Y %H:%M"), ""]
    for i in sale.items:
        lines.append(f"{inv.fmt(i.quantity)} x {i.product.name}  {cur} {i.total:,.2f}")
    if sale.discount:
        lines.append(f"Discount  -{cur} {sale.discount:,.2f}")
    lines.append(f"TOTAL  {cur} {sale.total:,.2f}")
    if sale.change_given:
        lines.append(f"Change  {cur} {sale.change_given:,.2f}")
    lines += ["", f"View your receipt: {receipt_url(sale)}"]
    footer = svc.settings_of(org)["receipt_footer"]
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def normalise_phone(raw: str, currency: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if digits.startswith("+"):
        return digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    codes = {"NGN": "234", "GHS": "233", "KES": "254", "ZAR": "27", "UGX": "256", "TZS": "255", "RWF": "250", "XOF": "225"}
    if digits.startswith("0") and currency in codes:
        return "+" + codes[currency] + digits[1:]
    return "+" + digits


@router.post("/sales/{ref}/send")
def send_receipt(ref: str, body: SendIn, p: Principal = Depends(require("pos.sell")), db: Session = Depends(get_db)):
    sale = _sale_for(db, p, ref)
    text_ = receipt_text(sale, p.org)
    if body.channel == "email":
        if "@" not in body.to:
            raise HTTPException(422, "Enter an email address")
        messaging.queue_email(db, body.to.strip(), f"Your receipt from {p.org.name} ({sale.number})", text_, org_id=p.org_id)
        to = body.to.strip()
    else:
        to = normalise_phone(body.to, p.org.currency)
        if len(to) < 8:
            raise HTTPException(422, "Enter a phone number")
        messaging.queue(db, channel=body.channel, recipient=to, subject=None, body=text_[:1500], org_id=p.org_id)
    if not sale.customer_phone and body.channel != "email":
        sale.customer_phone = to
    p.audit(db, "pos.receipt_sent", entity_type="pos_sale", entity_id=sale.id, summary=f"Sent {sale.number} by {body.channel} to {to}")
    db.commit()
    ch = messaging.channels()
    configured = ch["email_configured"] if body.channel == "email" else ch.get(body.channel)
    return {"message": f"Receipt queued to {to}" if configured else
            f"Receipt saved, but {body.channel} sending is not set up yet, so it was only written to the log."}


# ------------------------------------------------------------------ returns
@router.post("/returns", status_code=201)
def create_return(body: ReturnIn, response: Response, p: Principal = Depends(require("pos.sell", "pos.refund")),
                  db: Session = Depends(get_db)):
    _pos_on(p)
    data = body.model_dump()
    data["lines"] = [l.model_dump() for l in body.lines]

    def op():
        ret, created = svc.create_return(db, p.ctx, p.permissions, p.org, data)
        if created:
            o = db.get(PosSale, ret.original_sale_id) if ret.original_sale_id else None
            p.audit(db, "pos.return", entity_type="pos_sale", entity_id=ret.id,
                    summary=f"{ret.number}: refunded {ret.total} by {body.refund_method}" + (f" against {o.number}" if o else " (no receipt)"))
        else:
            response.status_code = 200
        return sale_out(db, ret, p.org, show_cost=p.can("reports.view"))
    return inv.run_with_retry(db, op)


# ------------------------------------------------------------------ owner's view
@router.get("/summary")
def summary(day: date | None = Query(None, alias="date"), warehouse_id: uuid.UUID | None = None,
            p: Principal = Depends(require("reports.view")), db: Session = Depends(get_db)):
    ids = set(p.user.warehouse_ids)
    if warehouse_id:
        if ids and warehouse_id not in ids:
            raise HTTPException(403, "You are not allowed to see this location")
        ids = {warehouse_id}
    return svc.summary(db, p.org, day or svc.local_today(p.org), ids or None)


# ------------------------------------------------------------------ promotions
def promotion_out(x: Promotion) -> dict:
    return {"id": x.id, "name": x.name, "kind": x.kind, "value": x.value, "scope": x.scope, "category_id": x.category_id,
            "product_ids": x.product_ids or [], "min_qty": x.min_qty, "starts_on": x.starts_on, "ends_on": x.ends_on,
            "is_active": x.is_active, "created_at": x.created_at}


def _check_promo(db: Session, p: Principal, body: PromotionIn) -> None:
    if body.kind == "percent" and body.value > 100:
        raise HTTPException(422, "A percentage can't be more than 100")
    if body.scope == "category":
        if not body.category_id:
            raise HTTPException(422, "Choose a category")
        c = db.get(Category, body.category_id)
        if not c or c.org_id != p.org_id:
            raise HTTPException(404, "Category not found")
    if body.scope == "products":
        if not body.product_ids:
            raise HTTPException(422, "Choose at least one product")
        n = db.execute(select(func.count()).select_from(Product).where(Product.org_id == p.org_id, Product.id.in_(body.product_ids))).scalar_one()
        if n != len(set(body.product_ids)):
            raise HTTPException(404, "Product not found")
    if body.starts_on and body.ends_on and body.ends_on < body.starts_on:
        raise HTTPException(422, "The end date is before the start date")


@promotions.get("")
def list_promotions(p: Principal = Depends(require_any("promotions.manage", "pos.sell")), db: Session = Depends(get_db)):
    today = svc.local_today(p.org)
    rows = db.execute(select(Promotion).where(Promotion.org_id == p.org_id).order_by(Promotion.is_active.desc(), Promotion.created_at.desc())).scalars().all()
    out = []
    for x in rows:
        d = promotion_out(x)
        d["running"] = x.is_active and (x.starts_on is None or x.starts_on <= today) and (x.ends_on is None or x.ends_on >= today)
        out.append(d)
    return out


@promotions.post("", status_code=201)
def create_promotion(body: PromotionIn, p: Principal = Depends(require("promotions.manage")), db: Session = Depends(get_db)):
    _check_promo(db, p, body)
    x = Promotion(org_id=p.org_id, **body.model_dump(exclude={"product_ids"}), product_ids=[str(i) for i in body.product_ids])
    db.add(x)
    db.flush()
    p.audit(db, "promotion.create", entity_type="promotion", entity_id=x.id, summary=f"Promotion '{x.name}'")
    db.commit()
    return promotion_out(x)


@promotions.put("/{promo_id}")
def update_promotion(promo_id: uuid.UUID, body: PromotionIn, p: Principal = Depends(require("promotions.manage")), db: Session = Depends(get_db)):
    x = db.get(Promotion, promo_id)
    if not x or x.org_id != p.org_id:
        raise HTTPException(404, "Promotion not found")
    _check_promo(db, p, body)
    for k, v in body.model_dump(exclude={"product_ids"}).items():
        setattr(x, k, v)
    x.product_ids = [str(i) for i in body.product_ids]
    p.audit(db, "promotion.update", entity_type="promotion", entity_id=x.id, summary=f"Changed promotion '{x.name}'")
    db.commit()
    return promotion_out(x)


@promotions.delete("/{promo_id}", status_code=204)
def delete_promotion(promo_id: uuid.UUID, p: Principal = Depends(require("promotions.manage")), db: Session = Depends(get_db)):
    x = db.get(Promotion, promo_id)
    if not x or x.org_id != p.org_id:
        raise HTTPException(404, "Promotion not found")
    p.audit(db, "promotion.delete", entity_type="promotion", entity_id=x.id, summary=f"Deleted promotion '{x.name}'")
    db.delete(x)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ public receipt (no sign-in)
@public.get("/receipts/{token}")
def public_receipt(token: str, db: Session = Depends(get_db)):
    from app.models import Organization
    if len(token) < 16 or len(token) > 40:
        raise HTTPException(404, "Receipt not found")
    sale = db.execute(select(PosSale).where(PosSale.public_token == token)).unique().scalar_one_or_none()
    if not sale:
        raise HTTPException(404, "Receipt not found")
    org = db.get(Organization, sale.org_id)
    return sale_out(db, sale, org, public_view=True)
