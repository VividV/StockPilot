"""The inventory engine.

Rules this module guarantees
----------------------------
1. Stock quantities are never written directly. Every change inserts a
   ``StockMovement`` and updates the matching ``StockBalance`` in the *same*
   database transaction.
2. The balance row is locked (``SELECT ... FOR UPDATE``) before it is read, so
   two people issuing the same stock at the same time are serialised by
   PostgreSQL: the second one sees the first one's result and is refused if
   there is not enough left.
3. Negative stock is refused unless the organisation enables
   ``allow_negative_stock``. Batch- and serial-tracked stock can never go negative.
4. Batch-tracked products are picked First-Expired-First-Out unless the user
   names a batch. Expired batches are never picked automatically.
5. Serial-tracked products move one serial at a time and every serial keeps its
   own movement history.

Callers (routers) are responsible for committing; wrap calls in
``run_with_retry`` so the rare deadlock between opposite transfers is retried.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import TypeVar

from psycopg import errors as pg_errors
from sqlalchemy import String, cast, func, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.models import (
    INBOUND_TYPES, DocumentSequence, Location, MovementSerial, MovementType, Notification, Product,
    ProductUnitConversion, SerialNumber, SerialStatus, StockAdjustment, StockAdjustmentItem, StockBalance,
    StockBatch, StockMovement, Transfer, TransferItem, TransferStatus, UnitOfMeasure,
)

Q = Decimal("0.0001")
ZERO = Decimal("0")


# --------------------------------------------------------------------------- errors
class InventoryError(Exception):
    status_code = 400

    def __init__(self, message: str, code: str = "inventory_error", **details):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


class InsufficientStock(InventoryError):
    status_code = 409

    def __init__(self, product: Product, available: Decimal, requested: Decimal, where: str = ""):
        unit = product.base_unit.abbreviation if product.base_unit else ""
        super().__init__(
            f"Not enough stock of {product.name}{' at ' + where if where else ''}: "
            f"{fmt(available)} {unit} available, {fmt(requested)} {unit} requested.",
            code="insufficient_stock", available=str(available), requested=str(requested), product_id=str(product.id),
        )


class NotFound(InventoryError):
    status_code = 404


def fmt(d: Decimal) -> str:
    d = Decimal(d).normalize()
    return f"{d:f}"


def q(v) -> Decimal:
    return Decimal(str(v)).quantize(Q, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- context
@dataclass
class Ctx:
    """Who is doing what, for which organisation."""
    org_id: uuid.UUID
    user_id: uuid.UUID | None
    org_settings: dict = field(default_factory=dict)
    allowed_warehouse_ids: set[uuid.UUID] = field(default_factory=set)  # empty = all

    @property
    def allow_negative(self) -> bool:
        return bool(self.org_settings.get("allow_negative_stock", False))

    def check_location(self, loc: Location) -> None:
        if loc.org_id != self.org_id:
            raise NotFound("Location not found", code="not_found")
        if not loc.is_active:
            raise InventoryError(f"Location {loc.name} is inactive", code="inactive_location")
        if self.allowed_warehouse_ids and loc.warehouse_id not in self.allowed_warehouse_ids:
            raise InventoryError("You are not allowed to work in this location", code="location_forbidden")


T = TypeVar("T")


def run_with_retry(db: Session, fn: Callable[[], T], attempts: int = 3) -> T:
    """Run ``fn`` and commit; retry on deadlock / serialization failure."""
    for i in range(attempts):
        try:
            result = fn()
            db.commit()
            return result
        except (OperationalError, DBAPIError) as e:
            db.rollback()
            if isinstance(getattr(e, "orig", None), (pg_errors.DeadlockDetected, pg_errors.SerializationFailure)) and i < attempts - 1:
                continue
            raise
        except Exception:
            db.rollback()
            raise
    raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- lookups
def get_product(db: Session, ctx: Ctx, product_id: uuid.UUID, *, lock: bool = False) -> Product:
    stmt = select(Product).where(Product.id == product_id, Product.org_id == ctx.org_id)
    if lock:
        stmt = stmt.with_for_update(of=Product).execution_options(populate_existing=True)
    p = db.execute(stmt).unique().scalar_one_or_none()
    if not p:
        raise NotFound("Product not found", code="not_found")
    return p


def get_location(db: Session, ctx: Ctx, location_id: uuid.UUID) -> Location:
    loc = db.execute(select(Location).where(Location.id == location_id, Location.org_id == ctx.org_id)).unique().scalar_one_or_none()
    if not loc:
        raise NotFound("Location not found", code="not_found")
    ctx.check_location(loc)
    return loc


def to_base_qty(db: Session, product: Product, qty, unit_id: uuid.UUID | None) -> tuple[Decimal, str]:
    """Convert a quantity typed in any unit into the product's base unit."""
    qty = q(qty)
    if qty <= 0:
        raise InventoryError("Quantity must be greater than zero", code="invalid_quantity")
    if unit_id is None or unit_id == product.base_unit_id:
        unit = product.base_unit
        base = qty
    else:
        conv = db.execute(select(ProductUnitConversion).where(
            ProductUnitConversion.product_id == product.id, ProductUnitConversion.unit_id == unit_id)).unique().scalar_one_or_none()
        if not conv:
            raise InventoryError(f"No unit conversion set up for {product.name} in that unit", code="no_conversion")
        unit = conv.unit
        base = q(qty * conv.factor)
    if not unit.allow_decimals and qty != qty.to_integral_value():
        raise InventoryError(f"{unit.name} must be a whole number", code="invalid_quantity")
    if (product.track_serials or not product.base_unit.allow_decimals) and base != base.to_integral_value():
        raise InventoryError(f"{product.name} is counted in whole {product.base_unit.name.lower()}s", code="invalid_quantity")
    return base, unit.abbreviation


def next_number(db: Session, org_id: uuid.UUID, prefix: str) -> str:
    stmt = (
        pg_insert(DocumentSequence).values(org_id=org_id, prefix=prefix, next_value=2)
        .on_conflict_do_update(index_elements=["org_id", "prefix"], set_={"next_value": DocumentSequence.next_value + 1})
        .returning(DocumentSequence.next_value)
    )
    n = db.execute(stmt).scalar_one() - 1
    return f"{prefix}-{n:05d}"


# --------------------------------------------------------------------------- core primitive
def _lock_balance(db: Session, org_id, product_id, location_id, batch_id) -> StockBalance:
    db.execute(
        pg_insert(StockBalance)
        .values(id=uuid.uuid4(), org_id=org_id, product_id=product_id,
                location_id=location_id, batch_id=batch_id, quantity=0)
        .on_conflict_do_nothing(constraint="uq_stock_balance_key")
    )
    return db.execute(
        select(StockBalance).where(
            StockBalance.org_id == org_id, StockBalance.product_id == product_id,
            StockBalance.location_id == location_id,
            StockBalance.batch_id.is_not_distinct_from(batch_id),
        ).with_for_update(of=StockBalance).execution_options(populate_existing=True)
    ).unique().scalar_one()


def _post(
    db: Session, ctx: Ctx, *, mtype: MovementType, product: Product, location: Location, quantity: Decimal,
    batch: StockBatch | None = None, unit_cost: Decimal | None = None, serial_ids: list[uuid.UUID] | None = None,
    reference_type: str | None = None, reference_id: uuid.UUID | None = None, reference_no: str | None = None,
    note: str | None = None, counterparty: str | None = None, entered_quantity=None, entered_unit=None,
) -> StockMovement:
    """Lock the balance, check it, change it, write the movement. Nothing else
    in the code base changes stock."""
    if quantity == 0:
        raise InventoryError("Quantity must not be zero", code="invalid_quantity")
    if product.has_variants:
        raise InventoryError(f"{product.name} comes in several sizes/colours. Choose the exact one.", code="variant_required")
    inbound = mtype in INBOUND_TYPES
    if inbound != (quantity > 0):
        raise InventoryError("Movement direction does not match its type", code="invalid_direction")
    if product.track_batches and batch is None:
        raise InventoryError(f"{product.name} needs a batch number", code="batch_required")

    bal = _lock_balance(db, ctx.org_id, product.id, location.id, batch.id if batch else None)
    new_qty = q(bal.quantity + quantity)
    strict = product.track_batches or product.track_serials or not ctx.allow_negative
    if new_qty < 0 and strict:
        where = location.warehouse.name if location.warehouse else location.name
        if batch:
            where += f" (batch {batch.batch_number})"
        raise InsufficientStock(product, bal.quantity, -quantity, where)
    bal.quantity = new_qty

    mv = StockMovement(
        org_id=ctx.org_id, movement_type=mtype, product_id=product.id, location_id=location.id,
        batch_id=batch.id if batch else None, quantity=quantity, balance_after=new_qty,
        unit_cost=unit_cost if unit_cost is not None else product.average_cost,
        entered_quantity=entered_quantity, entered_unit=entered_unit,
        reference_type=reference_type, reference_id=reference_id, reference_no=reference_no,
        note=note, counterparty=counterparty, created_by=ctx.user_id,
    )
    db.add(mv)
    db.flush()
    for sid in serial_ids or []:
        db.add(MovementSerial(movement_id=mv.id, serial_id=sid))
    return mv


def _update_average_cost(db: Session, product: Product, incoming_qty: Decimal, unit_cost: Decimal | None) -> None:
    """Moving weighted average. Caller must hold the product row lock."""
    if unit_cost is None:
        return
    on_hand = db.execute(select(func.coalesce(func.sum(StockBalance.quantity), 0)).where(
        StockBalance.product_id == product.id, StockBalance.org_id == product.org_id)).scalar_one()
    on_hand = max(Decimal(on_hand), ZERO)
    total = on_hand + incoming_qty
    if total > 0:
        product.average_cost = q((on_hand * product.average_cost + incoming_qty * Decimal(unit_cost)) / total)
        db.flush()  # write it now: later SELECTs with populate_existing would otherwise reload the old value


# --------------------------------------------------------------------------- batches & serials
def get_or_create_batch(db: Session, ctx: Ctx, product: Product, batch_number: str, *, expires_on: date | None,
                        manufactured_on: date | None, supplier_id=None, purchase_reference=None) -> StockBatch:
    batch_number = (batch_number or "").strip()
    if not batch_number:
        raise InventoryError(f"{product.name} needs a batch number", code="batch_required")
    db.execute(pg_insert(StockBatch).values(
        id=uuid.uuid4(), org_id=ctx.org_id, product_id=product.id, batch_number=batch_number,
        expires_on=expires_on, manufactured_on=manufactured_on, supplier_id=supplier_id,
        purchase_reference=purchase_reference,
    ).on_conflict_do_nothing(index_elements=["org_id", "product_id", "batch_number"]))
    batch = db.execute(select(StockBatch).where(
        StockBatch.org_id == ctx.org_id, StockBatch.product_id == product.id,
        StockBatch.batch_number == batch_number).execution_options(populate_existing=True)).scalar_one()
    if product.track_expiry and batch.expires_on is None:
        if expires_on is None:
            raise InventoryError(f"{product.name} needs an expiry date", code="expiry_required")
        batch.expires_on = expires_on
    elif expires_on and batch.expires_on and expires_on != batch.expires_on:
        raise InventoryError(
            f"Batch {batch_number} already exists with expiry {batch.expires_on.isoformat()}", code="batch_expiry_mismatch")
    return batch


def _get_batch(db: Session, ctx: Ctx, product: Product, batch_id: uuid.UUID) -> StockBatch:
    b = db.get(StockBatch, batch_id)
    if not b or b.org_id != ctx.org_id or b.product_id != product.id:
        raise NotFound("Batch not found for this product", code="not_found")
    return b


def _clean_serials(serials: list[str] | None) -> list[str]:
    out = [s.strip() for s in (serials or []) if s and s.strip()]
    if len(out) != len(set(out)):
        raise InventoryError("The same serial number appears twice", code="duplicate_serial")
    return out


def _lock_serials(db: Session, ctx: Ctx, product: Product, serials: list[str]) -> list[SerialNumber]:
    rows = db.execute(select(SerialNumber).where(
        SerialNumber.org_id == ctx.org_id, SerialNumber.product_id == product.id, SerialNumber.serial.in_(serials),
    ).order_by(SerialNumber.id).with_for_update().execution_options(populate_existing=True)).scalars().all()
    found = {r.serial for r in rows}
    missing = [s for s in serials if s not in found]
    if missing:
        raise InventoryError(f"Unknown serial number(s): {', '.join(missing[:5])}", code="unknown_serial")
    return list(rows)


# --------------------------------------------------------------------------- operations
INBOUND_ALLOWED = {MovementType.PURCHASE_RECEIPT, MovementType.OPENING_BALANCE, MovementType.RETURN,
                   MovementType.ADJUSTMENT_IN, MovementType.PRODUCTION_OUTPUT}
OUTBOUND_ALLOWED = {MovementType.ISSUE, MovementType.SALE, MovementType.DAMAGED, MovementType.LOST,
                    MovementType.EXPIRED, MovementType.ADJUSTMENT_OUT, MovementType.PRODUCTION_CONSUMPTION}


def receive(
    db: Session, ctx: Ctx, *, product_id, location_id, quantity, unit_id=None, unit_cost=None,
    batch_number: str | None = None, expires_on: date | None = None, manufactured_on: date | None = None,
    serials: list[str] | None = None, supplier_id=None, reference_no: str | None = None, note: str | None = None,
    mtype: MovementType = MovementType.PURCHASE_RECEIPT, reference_type: str | None = None, reference_id=None,
    counterparty: str | None = None,
) -> list[StockMovement]:
    if mtype not in INBOUND_ALLOWED:
        raise InventoryError("Not an inbound movement type", code="invalid_type")
    product = get_product(db, ctx, product_id, lock=True)  # lock: serialises average-cost updates
    if not product.is_active:
        raise InventoryError(f"{product.name} is inactive", code="inactive_product")
    location = get_location(db, ctx, location_id)
    base_qty, entered_unit = to_base_qty(db, product, quantity, unit_id)
    cost_per_base = None
    if unit_cost is not None:
        cost_per_base = q(Decimal(str(unit_cost)) * Decimal(str(quantity)) / base_qty)

    batch = None
    if product.track_batches:
        batch = get_or_create_batch(db, ctx, product, batch_number or "", expires_on=expires_on,
                                    manufactured_on=manufactured_on, supplier_id=supplier_id,
                                    purchase_reference=reference_no)

    serial_ids: list[uuid.UUID] = []
    if product.track_serials:
        serials = _clean_serials(serials)
        if Decimal(len(serials)) != base_qty:
            raise InventoryError(f"Enter exactly {fmt(base_qty)} serial number(s) for {product.name}", code="serial_count")
        existing = {s.serial: s for s in db.execute(select(SerialNumber).where(
            SerialNumber.org_id == ctx.org_id, SerialNumber.product_id == product.id,
            SerialNumber.serial.in_(serials)).with_for_update()).scalars()}
        for s in serials:
            row = existing.get(s)
            if row and row.status in (SerialStatus.IN_STOCK, SerialStatus.IN_TRANSIT):
                raise InventoryError(f"Serial {s} is already in stock", code="duplicate_serial")
            if row is None:
                row = SerialNumber(org_id=ctx.org_id, product_id=product.id, serial=s)
                db.add(row)
            row.status = SerialStatus.IN_STOCK
            row.location_id = location.id
            row.batch_id = batch.id if batch else None
        db.flush()
        serial_ids = [r.id for r in db.execute(select(SerialNumber).where(
            SerialNumber.org_id == ctx.org_id, SerialNumber.product_id == product.id,
            SerialNumber.serial.in_(serials))).scalars()]

    if mtype in (MovementType.PURCHASE_RECEIPT, MovementType.OPENING_BALANCE, MovementType.PRODUCTION_OUTPUT):
        _update_average_cost(db, product, base_qty, cost_per_base)
    mv = _post(db, ctx, mtype=mtype, product=product, location=location, quantity=base_qty, batch=batch,
               unit_cost=cost_per_base, serial_ids=serial_ids, reference_no=reference_no, note=note,
               reference_type=reference_type, reference_id=reference_id, counterparty=counterparty,
               entered_quantity=q(quantity), entered_unit=entered_unit)
    refresh_stock_alerts(db, ctx, [product.id])
    return [mv]


@dataclass
class Pick:
    batch: StockBatch | None
    qty: Decimal
    serial_ids: list[uuid.UUID] = field(default_factory=list)


def _allocate(db: Session, ctx: Ctx, product: Product, location: Location, base_qty: Decimal, *,
              batch_id=None, serials: list[str] | None = None, allow_expired: bool = False) -> list[Pick]:
    """Decide which batches / serials an outbound quantity comes from."""
    today = date.today()
    if product.track_serials:
        serials = _clean_serials(serials)
        if Decimal(len(serials)) != base_qty:
            raise InventoryError(f"Select exactly {fmt(base_qty)} serial number(s) for {product.name}", code="serial_count")
        rows = _lock_serials(db, ctx, product, serials)
        for r in rows:
            if r.status != SerialStatus.IN_STOCK or r.location_id != location.id:
                raise InventoryError(f"Serial {r.serial} is not in stock at this location", code="serial_unavailable")
        by_batch: dict[uuid.UUID | None, list[SerialNumber]] = {}
        for r in rows:
            by_batch.setdefault(r.batch_id, []).append(r)
        return [Pick(db.get(StockBatch, b) if b else None, Decimal(len(rs)), [r.id for r in rs]) for b, rs in by_batch.items()]

    if not product.track_batches:
        return [Pick(None, base_qty)]

    if batch_id:
        batch = _get_batch(db, ctx, product, batch_id)
        if batch.expires_on and batch.expires_on < today and not allow_expired:
            raise InventoryError(f"Batch {batch.batch_number} expired on {batch.expires_on.isoformat()}. "
                                 "Record it as Expired instead.", code="batch_expired")
        return [Pick(batch, base_qty)]

    # FEFO: lock every candidate balance in a consistent order, then allocate.
    rows = db.execute(
        select(StockBalance, StockBatch).join(StockBatch, StockBatch.id == StockBalance.batch_id)
        .where(StockBalance.org_id == ctx.org_id, StockBalance.product_id == product.id,
               StockBalance.location_id == location.id, StockBalance.quantity > 0,
               or_(StockBatch.expires_on.is_(None), StockBatch.expires_on >= today) if not allow_expired else true())
        .order_by(StockBatch.expires_on.asc().nulls_last(), StockBatch.created_at, StockBatch.id)
        .with_for_update(of=StockBalance).execution_options(populate_existing=True)
    ).unique().all()
    picks, remaining = [], base_qty
    for bal, batch in rows:
        if remaining <= 0:
            break
        take = min(bal.quantity, remaining)
        picks.append(Pick(batch, take))
        remaining -= take
    if remaining > 0:
        raise InsufficientStock(product, base_qty - remaining, base_qty, location.warehouse.name)
    return picks


def issue(
    db: Session, ctx: Ctx, *, product_id, location_id, quantity, unit_id=None, batch_id=None,
    serials: list[str] | None = None, mtype: MovementType = MovementType.ISSUE, reference_no: str | None = None,
    note: str | None = None, counterparty: str | None = None, reference_type: str | None = None, reference_id=None,
) -> list[StockMovement]:
    if mtype not in OUTBOUND_ALLOWED:
        raise InventoryError("Not an outbound movement type", code="invalid_type")
    product = get_product(db, ctx, product_id)
    location = get_location(db, ctx, location_id)
    base_qty, entered_unit = to_base_qty(db, product, quantity, unit_id)
    picks = _allocate(db, ctx, product, location, base_qty, batch_id=batch_id, serials=serials,
                      allow_expired=(mtype in (MovementType.EXPIRED, MovementType.DAMAGED, MovementType.LOST)))
    serial_status = SerialStatus.SOLD if mtype == MovementType.SALE else (
        SerialStatus.ISSUED if mtype in (MovementType.ISSUE, MovementType.PRODUCTION_CONSUMPTION) else SerialStatus.WRITTEN_OFF)
    out = []
    for p in picks:
        out.append(_post(db, ctx, mtype=mtype, product=product, location=location, quantity=-p.qty, batch=p.batch,
                         serial_ids=p.serial_ids, reference_no=reference_no, note=note, counterparty=counterparty,
                         reference_type=reference_type, reference_id=reference_id,
                         entered_quantity=q(quantity) if len(picks) == 1 else None,
                         entered_unit=entered_unit if len(picks) == 1 else None))
        if p.serial_ids:
            db.execute(update(SerialNumber).where(SerialNumber.id.in_(p.serial_ids))
                       .values(status=serial_status, location_id=None))
    refresh_stock_alerts(db, ctx, [product.id])
    return out


# --------------------------------------------------------------------------- adjustments
ADJUSTMENT_REASONS = {
    "count_correction": None,  # direction decided by sign
    "found": MovementType.ADJUSTMENT_IN,
    "damaged": MovementType.DAMAGED,
    "lost": MovementType.LOST,
    "expired": MovementType.EXPIRED,
    "other": None,
}


def adjust(db: Session, ctx: Ctx, *, reason: str, notes: str | None, lines: list[dict]) -> StockAdjustment:
    """Each line: product_id, location_id, batch_id?, and either ``counted_qty``
    (set stock to this) or ``change`` (+/-), plus serials for serial products."""
    if reason not in ADJUSTMENT_REASONS:
        raise InventoryError("Unknown adjustment reason", code="invalid_reason")
    if not lines:
        raise InventoryError("Add at least one line", code="empty")
    adj = StockAdjustment(org_id=ctx.org_id, number=next_number(db, ctx.org_id, "ADJ"), reason=reason,
                          notes=notes, created_by=ctx.user_id)
    db.add(adj)
    db.flush()
    touched = set()
    for ln in lines:
        product = get_product(db, ctx, ln["product_id"], lock=True)
        location = get_location(db, ctx, ln["location_id"])
        batch = _get_batch(db, ctx, product, ln["batch_id"]) if ln.get("batch_id") else None
        if product.track_batches and batch is None and ln.get("counted_qty") is not None:
            raise InventoryError(f"Choose a batch to count for {product.name}", code="batch_required")
        bal = _lock_balance(db, ctx.org_id, product.id, location.id, batch.id if batch else None)
        before = bal.quantity
        if ln.get("counted_qty") is not None:
            counted = q(ln["counted_qty"])
            if counted < 0:
                raise InventoryError("Counted quantity cannot be negative", code="invalid_quantity")
            change = counted - before
        else:
            change = q(ln.get("change", 0))
        if change == 0:
            continue
        mtype = ADJUSTMENT_REASONS[reason] or (MovementType.ADJUSTMENT_IN if change > 0 else MovementType.ADJUSTMENT_OUT)
        if (mtype in INBOUND_TYPES) != (change > 0):
            raise InventoryError(f"A '{reason}' adjustment must {'add' if mtype in INBOUND_TYPES else 'remove'} stock",
                                 code="invalid_direction")
        if product.track_serials:
            serials = _clean_serials(ln.get("serials"))
            if Decimal(len(serials)) != abs(change):
                raise InventoryError(f"List the {fmt(abs(change))} serial number(s) being adjusted", code="serial_count")
            if change > 0:
                receive(db, ctx, product_id=product.id, location_id=location.id, quantity=change,
                        batch_number=batch.batch_number if batch else None, serials=serials,
                        mtype=mtype, reference_type="adjustment", reference_id=adj.id, reference_no=adj.number, note=notes)
            else:
                issue(db, ctx, product_id=product.id, location_id=location.id, quantity=-change, serials=serials,
                      mtype=mtype, reference_type="adjustment", reference_id=adj.id, reference_no=adj.number, note=notes)
        else:
            if product.track_batches and batch is None:
                if change > 0:
                    raise InventoryError(f"Choose a batch for {product.name}", code="batch_required")
                issue(db, ctx, product_id=product.id, location_id=location.id, quantity=-change, mtype=mtype,
                      reference_type="adjustment", reference_id=adj.id, reference_no=adj.number, note=notes)
            else:
                _post(db, ctx, mtype=mtype, product=product, location=location, quantity=change, batch=batch,
                      reference_type="adjustment", reference_id=adj.id, reference_no=adj.number, note=notes)
        db.add(StockAdjustmentItem(adjustment_id=adj.id, product_id=product.id, location_id=location.id,
                                   batch_id=batch.id if batch else None, quantity_before=before,
                                   quantity_change=change, quantity_after=before + change))
        touched.add(product.id)
    if not touched:
        raise InventoryError("Nothing changed: the counted quantities match the system", code="no_change")
    db.flush()
    refresh_stock_alerts(db, ctx, list(touched))
    return adj


# --------------------------------------------------------------------------- transfers
TRANSFER_FLOW = {
    TransferStatus.DRAFT: {TransferStatus.REQUESTED, TransferStatus.APPROVED, TransferStatus.IN_TRANSIT, TransferStatus.CANCELLED},
    TransferStatus.REQUESTED: {TransferStatus.APPROVED, TransferStatus.IN_TRANSIT, TransferStatus.CANCELLED},
    TransferStatus.APPROVED: {TransferStatus.IN_TRANSIT, TransferStatus.CANCELLED},
    TransferStatus.IN_TRANSIT: {TransferStatus.RECEIVED},
    TransferStatus.RECEIVED: set(),
    TransferStatus.CANCELLED: set(),
}


def _require_status(t: Transfer, target: TransferStatus) -> None:
    if target not in TRANSFER_FLOW[t.status]:
        raise InventoryError(f"A {t.status.value.replace('_', ' ')} transfer cannot be moved to "
                             f"{target.value.replace('_', ' ')}", code="invalid_status")


def create_transfer(db: Session, ctx: Ctx, *, from_location_id, to_location_id, items: list[dict],
                    notes: str | None = None, submit: bool = False) -> Transfer:
    src = get_location(db, ctx, from_location_id)
    dst = get_location(db, ctx, to_location_id)
    if src.id == dst.id:
        raise InventoryError("Choose two different locations", code="same_location")
    if not items:
        raise InventoryError("Add at least one product", code="empty")
    t = Transfer(org_id=ctx.org_id, number=next_number(db, ctx.org_id, "TRF"), from_location_id=src.id,
                 to_location_id=dst.id, notes=notes, created_by=ctx.user_id,
                 status=TransferStatus.REQUESTED if submit else TransferStatus.DRAFT)
    db.add(t)
    db.flush()
    for i, it in enumerate(items, 1):
        product = get_product(db, ctx, it["product_id"])
        base_qty, _ = to_base_qty(db, product, it["quantity"], it.get("unit_id"))
        batch_id = it.get("batch_id")
        if batch_id:
            _get_batch(db, ctx, product, batch_id)
        t.items.append(TransferItem(line_no=i, product_id=product.id, batch_id=batch_id, requested_qty=base_qty,
                                    dispatch_detail=[{"serials": _clean_serials(it.get("serials"))}] if it.get("serials") else []))
    db.flush()
    return t


def get_transfer(db: Session, ctx: Ctx, transfer_id, *, lock: bool = False) -> Transfer:
    stmt = select(Transfer).where(Transfer.id == transfer_id, Transfer.org_id == ctx.org_id)
    if lock:
        stmt = stmt.with_for_update(of=Transfer).execution_options(populate_existing=True)
    t = db.execute(stmt).unique().scalar_one_or_none()
    if not t:
        raise NotFound("Transfer not found", code="not_found")
    return t


def set_transfer_status(db: Session, ctx: Ctx, t: Transfer, target: TransferStatus) -> Transfer:
    _require_status(t, target)
    if target == TransferStatus.APPROVED:
        t.approved_by = ctx.user_id
    t.status = target
    db.flush()
    return t


def dispatch_transfer(db: Session, ctx: Ctx, t: Transfer) -> Transfer:
    """Stock leaves the source location and is 'in transit'."""
    _require_status(t, TransferStatus.IN_TRANSIT)
    if ctx.org_settings.get("modules", {}).get("transfer_approval") and t.status != TransferStatus.APPROVED:
        raise InventoryError("This transfer must be approved before it is sent", code="approval_required")
    src = get_location(db, ctx, t.from_location_id)
    get_location(db, ctx, t.to_location_id)
    for item in t.items:
        product = get_product(db, ctx, item.product_id)
        requested_serials = item.dispatch_detail[0].get("serials") if item.dispatch_detail else None
        picks = _allocate(db, ctx, product, src, item.requested_qty, batch_id=item.batch_id, serials=requested_serials)
        detail = []
        for p in picks:
            _post(db, ctx, mtype=MovementType.TRANSFER_OUT, product=product, location=src, quantity=-p.qty, batch=p.batch,
                  serial_ids=p.serial_ids, reference_type="transfer", reference_id=t.id, reference_no=t.number,
                  counterparty=t.to_location.warehouse.name)
            if p.serial_ids:
                db.execute(update(SerialNumber).where(SerialNumber.id.in_(p.serial_ids))
                           .values(status=SerialStatus.IN_TRANSIT, location_id=None))
            detail.append({"batch_id": str(p.batch.id) if p.batch else None, "qty": str(p.qty), "received": "0",
                           "serial_ids": [str(s) for s in p.serial_ids], "unit_cost": str(product.average_cost)})
        item.dispatch_detail = detail
        item.dispatched_qty = item.requested_qty
    t.status = TransferStatus.IN_TRANSIT
    t.dispatched_at = datetime.now(timezone.utc)
    db.flush()
    refresh_stock_alerts(db, ctx, [i.product_id for i in t.items])
    return t


def receive_transfer(db: Session, ctx: Ctx, t: Transfer, quantities: dict[uuid.UUID, Decimal] | None = None) -> Transfer:
    """Stock arrives at the destination. ``quantities`` maps item id -> qty to
    receive now (partial receiving); default = everything outstanding."""
    if t.status != TransferStatus.IN_TRANSIT:
        raise InventoryError("Only a transfer that is in transit can be received", code="invalid_status")
    dst = get_location(db, ctx, t.to_location_id)
    touched = []
    for item in t.items:
        outstanding = item.dispatched_qty - item.received_qty
        want = q(quantities.get(item.id, ZERO)) if quantities is not None else outstanding
        if want <= 0:
            continue
        if want > outstanding:
            raise InventoryError(f"Only {fmt(outstanding)} of {item.product.name} is still in transit", code="over_receive")
        product = get_product(db, ctx, item.product_id)
        remaining = want
        detail = [dict(d) for d in item.dispatch_detail]
        for d in detail:
            if remaining <= 0:
                break
            left = Decimal(d["qty"]) - Decimal(d["received"])
            take = min(left, remaining)
            if take <= 0:
                continue
            serial_ids = [uuid.UUID(s) for s in d["serial_ids"]]
            if serial_ids:
                # receive serials in order; the ones already received are at the front
                done = int(Decimal(d["received"]))
                serial_ids = serial_ids[done:done + int(take)]
            batch = db.get(StockBatch, uuid.UUID(d["batch_id"])) if d["batch_id"] else None
            _post(db, ctx, mtype=MovementType.TRANSFER_IN, product=product, location=dst, quantity=take, batch=batch,
                  unit_cost=Decimal(d["unit_cost"]), serial_ids=serial_ids, reference_type="transfer",
                  reference_id=t.id, reference_no=t.number, counterparty=t.from_location.warehouse.name)
            if serial_ids:
                db.execute(update(SerialNumber).where(SerialNumber.id.in_(serial_ids))
                           .values(status=SerialStatus.IN_STOCK, location_id=dst.id))
            d["received"] = str(Decimal(d["received"]) + take)
            remaining -= take
        item.dispatch_detail = detail
        item.received_qty = item.received_qty + want
        touched.append(item.product_id)
    if not touched:
        raise InventoryError("Nothing to receive", code="no_change")
    if all(i.received_qty >= i.dispatched_qty for i in t.items):
        t.status = TransferStatus.RECEIVED
        t.received_at = datetime.now(timezone.utc)
    db.flush()
    refresh_stock_alerts(db, ctx, touched)
    return t


def quick_transfer(db: Session, ctx: Ctx, **kwargs) -> Transfer:
    """The one-screen 'Transfer Stock' action: create, send and receive at once."""
    t = create_transfer(db, ctx, **kwargs)
    if ctx.org_settings.get("modules", {}).get("transfer_approval"):
        t.status = TransferStatus.REQUESTED
        db.flush()
        return t  # waits for a manager
    dispatch_transfer(db, ctx, t)
    receive_transfer(db, ctx, t)
    return t


# --------------------------------------------------------------------------- alerts
def product_totals(db: Session, org_id, product_ids) -> dict[uuid.UUID, Decimal]:
    rows = db.execute(select(StockBalance.product_id, func.sum(StockBalance.quantity)).where(
        StockBalance.org_id == org_id, StockBalance.product_id.in_(product_ids)).group_by(StockBalance.product_id)).all()
    return {pid: Decimal(qty) for pid, qty in rows}


def _upsert_alert(db: Session, org_id, *, type_: str, severity: str, title: str, body: str, entity_type: str, entity_id: str):
    db.execute(pg_insert(Notification).values(
        id=uuid.uuid4(), org_id=org_id, type=type_, severity=severity, title=title, body=body,
        entity_type=entity_type, entity_id=entity_id, is_read=False, is_resolved=False,
    ).on_conflict_do_nothing(index_elements=["org_id", "type", "entity_id"], index_where=text("is_resolved = false")))


def _resolve_alerts(db: Session, org_id, types: list[str], entity_id: str):
    db.execute(update(Notification).where(Notification.org_id == org_id, Notification.type.in_(types),
                                          Notification.entity_id == entity_id, Notification.is_resolved.is_(False))
               .values(is_resolved=True))


def refresh_stock_alerts(db: Session, ctx: Ctx, product_ids: list[uuid.UUID]) -> None:
    if not product_ids:
        return
    db.flush()
    totals = product_totals(db, ctx.org_id, product_ids)
    products = db.execute(select(Product).where(Product.id.in_(set(product_ids)), Product.org_id == ctx.org_id)).unique().scalars()
    for p in products:
        qty = totals.get(p.id, ZERO)
        threshold = max(p.reorder_level or ZERO, p.min_stock or ZERO)
        pid = str(p.id)
        unit = p.base_unit.abbreviation
        if qty <= 0:
            _resolve_alerts(db, ctx.org_id, ["low_stock"], pid)
            _upsert_alert(db, ctx.org_id, type_="out_of_stock", severity="critical", title=f"{p.name} is out of stock",
                          body=f"SKU {p.sku}. Reorder {fmt(p.reorder_qty)} {unit}." if p.reorder_qty else f"SKU {p.sku}.",
                          entity_type="product", entity_id=pid)
        elif threshold > 0 and qty <= threshold:
            _resolve_alerts(db, ctx.org_id, ["out_of_stock"], pid)
            _upsert_alert(db, ctx.org_id, type_="low_stock", severity="warning", title=f"{p.name} is running low",
                          body=f"{fmt(qty)} {unit} left (reorder level {fmt(threshold)}).",
                          entity_type="product", entity_id=pid)
        else:
            _resolve_alerts(db, ctx.org_id, ["low_stock", "out_of_stock"], pid)


def refresh_expiry_alerts(db: Session, org_id, warn_days: int = 30) -> None:
    """Raise an alert for every batch that still has stock and is expired or
    expiring soon. Cheap: uses ix_batches_org_expiry. Safe to call often."""
    today = date.today()
    rows = db.execute(
        select(StockBatch, Product, func.sum(StockBalance.quantity))
        .join(Product, Product.id == StockBatch.product_id)
        .join(StockBalance, StockBalance.batch_id == StockBatch.id)
        .where(StockBatch.org_id == org_id, StockBatch.expires_on.is_not(None),
               StockBatch.expires_on <= date.fromordinal(today.toordinal() + warn_days))
        .group_by(StockBatch.id, Product.id).having(func.sum(StockBalance.quantity) > 0)
    ).unique().all()
    for batch, product, qty in rows:
        bid = str(batch.id)
        if batch.expires_on < today:
            _resolve_alerts(db, org_id, ["expiring"], bid)
            _upsert_alert(db, org_id, type_="expired", severity="critical",
                          title=f"{product.name} batch {batch.batch_number} has expired",
                          body=f"{fmt(qty)} {product.base_unit.abbreviation} expired on {batch.expires_on.isoformat()}. "
                               "Remove it from sale and record it as Expired.", entity_type="batch", entity_id=bid)
        else:
            days = (batch.expires_on - today).days
            _upsert_alert(db, org_id, type_="expiring", severity="warning",
                          title=f"{product.name} batch {batch.batch_number} expires in {days} day{'s' if days != 1 else ''}",
                          body=f"{fmt(qty)} {product.base_unit.abbreviation} expire on {batch.expires_on.isoformat()}.",
                          entity_type="batch", entity_id=bid)
    # resolve alerts for batches that are now empty
    empty = select(StockBalance.batch_id).where(StockBalance.org_id == org_id, StockBalance.batch_id.is_not(None)) \
        .group_by(StockBalance.batch_id).having(func.sum(StockBalance.quantity) <= 0)
    db.execute(update(Notification).where(
        Notification.org_id == org_id, Notification.type.in_(["expiring", "expired"]), Notification.is_resolved.is_(False),
        Notification.entity_id.in_(select(cast(empty.subquery().c.batch_id, String)))
    ).values(is_resolved=True))
