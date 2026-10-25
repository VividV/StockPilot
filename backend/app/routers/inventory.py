import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, require, require_any, scoped_get
from app.models import (
    Customer, Location, MovementSerial, MovementType, Product, SerialNumber, StockAdjustment, StockBalance, StockBatch,
    StockMovement, Supplier, Warehouse,
)
from app.routers.common import PageParams, apply_sort, location_label, movements_out, paginate
from app.schemas import AdjustIn, IssueIn, OpeningStockIn, OperationResult, Page, QuickTransferIn, ReceiveIn, StockRow
from app.services import inventory as inv

router = APIRouter(prefix="/api", tags=["inventory"])


def _result(db: Session, p: Principal, message: str, movements, product_id) -> OperationResult:
    total = inv.product_totals(db, p.org_id, [product_id]).get(product_id, 0)
    return OperationResult(message=message, movements=movements_out(db, movements), on_hand=total)


def _unit_label(db, product: Product, unit_id) -> str:
    if not unit_id or unit_id == product.base_unit_id:
        return product.base_unit.abbreviation
    from app.models import UnitOfMeasure
    u = db.get(UnitOfMeasure, unit_id)
    return u.abbreviation if u else ""


# ------------------------------------------------------------------ operations
@router.post("/inventory/receive", response_model=OperationResult, status_code=201)
def receive(body: ReceiveIn, p: Principal = Depends(require("inventory.receive")), db: Session = Depends(get_db)):
    def op():
        supplier_name = scoped_get(db, Supplier, body.supplier_id, p.org_id, "Supplier").name if body.supplier_id else None
        mvs = inv.receive(db, p.ctx, product_id=body.product_id, location_id=body.location_id, quantity=body.quantity,
                          unit_id=body.unit_id, unit_cost=body.unit_cost, batch_number=body.batch_number,
                          expires_on=body.expires_on, manufactured_on=body.manufactured_on, serials=body.serials,
                          supplier_id=body.supplier_id, reference_no=body.reference_no, note=body.note,
                          mtype=MovementType(body.type), counterparty=supplier_name)
        product = mvs[0].product
        msg = f"Received {inv.fmt(body.quantity)} {_unit_label(db, product, body.unit_id)} of {product.name}"
        p.audit(db, "inventory.receive", entity_type="product", entity_id=product.id, summary=msg,
                after={"movement_ids": [m.id for m in mvs], "location_id": str(body.location_id), "quantity": str(mvs[0].quantity)})
        return _result(db, p, msg, mvs, product.id)
    return inv.run_with_retry(db, op)


@router.post("/inventory/issue", response_model=OperationResult, status_code=201)
def issue(body: IssueIn, p: Principal = Depends(require("inventory.issue")), db: Session = Depends(get_db)):
    def op():
        counterparty = body.issued_to
        if body.customer_id:
            counterparty = scoped_get(db, Customer, body.customer_id, p.org_id, "Customer").name
        mvs = inv.issue(db, p.ctx, product_id=body.product_id, location_id=body.location_id, quantity=body.quantity,
                        unit_id=body.unit_id, batch_id=body.batch_id, serials=body.serials, mtype=MovementType(body.type),
                        reference_no=body.reference_no, note=body.note, counterparty=counterparty)
        product = mvs[0].product
        verb = {"sale": "Sold", "damaged": "Wrote off (damaged)", "lost": "Wrote off (lost)", "expired": "Wrote off (expired)",
                "production_consumption": "Used in production"}.get(body.type, "Issued")
        msg = f"{verb} {inv.fmt(body.quantity)} {_unit_label(db, product, body.unit_id)} of {product.name}"
        if len(mvs) > 1:
            msg += f" from {len(mvs)} batches (earliest expiry first)"
        p.audit(db, f"inventory.{body.type}", entity_type="product", entity_id=product.id, summary=msg,
                after={"movement_ids": [m.id for m in mvs], "location_id": str(body.location_id)})
        return _result(db, p, msg, mvs, product.id)
    return inv.run_with_retry(db, op)


@router.post("/inventory/adjust", status_code=201)
def adjust(body: AdjustIn, p: Principal = Depends(require("inventory.adjust")), db: Session = Depends(get_db)):
    def op():
        adj = inv.adjust(db, p.ctx, reason=body.reason, notes=body.notes, lines=[l.model_dump() for l in body.lines])
        p.audit(db, "inventory.adjust", entity_type="adjustment", entity_id=adj.id,
                summary=f"Adjustment {adj.number} ({body.reason.replace('_', ' ')}), {len(adj.items)} line(s)",
                after={"lines": [{"product_id": str(i.product_id), "before": str(i.quantity_before),
                                  "change": str(i.quantity_change), "after": str(i.quantity_after)} for i in adj.items]})
        mvs = db.execute(select(StockMovement).where(StockMovement.org_id == p.org_id, StockMovement.reference_id == adj.id)
                         .order_by(StockMovement.id)).unique().scalars().all()
        return {"message": f"Saved adjustment {adj.number}", "id": adj.id, "number": adj.number,
                "movements": movements_out(db, mvs)}
    return inv.run_with_retry(db, op)


@router.post("/inventory/transfer", status_code=201)
def quick_transfer(body: QuickTransferIn, p: Principal = Depends(require("inventory.transfer")), db: Session = Depends(get_db)):
    """One-step transfer: stock leaves A and arrives at B immediately (or waits
    for approval if the organisation requires transfer approval)."""
    from app.routers.transfers import transfer_out

    def op():
        t = inv.quick_transfer(db, p.ctx, from_location_id=body.from_location_id, to_location_id=body.to_location_id,
                               notes=body.notes, items=[{"product_id": body.product_id, "quantity": body.quantity,
                                                         "unit_id": body.unit_id, "batch_id": body.batch_id,
                                                         "serials": body.serials}])
        item = t.items[0]
        done = t.status.value == "received"
        msg = (f"Moved {inv.fmt(item.requested_qty)} {item.product.base_unit.abbreviation} of {item.product.name} "
               f"from {location_label(t.from_location)} to {location_label(t.to_location)}") if done else \
              f"Transfer {t.number} is waiting for approval"
        p.audit(db, "inventory.transfer", entity_type="transfer", entity_id=t.id, summary=f"{t.number}: {msg}")
        return {"message": msg, "transfer": transfer_out(db, t)}
    return inv.run_with_retry(db, op)


@router.post("/inventory/opening-stock", status_code=201)
def opening_stock(body: OpeningStockIn, p: Principal = Depends(require("inventory.adjust")), db: Session = Depends(get_db)):
    """Guided opening-stock wizard posts here: one opening-balance movement per line."""
    def op():
        count = 0
        for ln in body.lines:
            inv.receive(db, p.ctx, product_id=ln.product_id, location_id=ln.location_id, quantity=ln.quantity,
                        unit_cost=ln.unit_cost, batch_number=ln.batch_number, expires_on=ln.expires_on,
                        serials=ln.serials, mtype=MovementType.OPENING_BALANCE, note="Opening stock")
            count += 1
        p.audit(db, "inventory.opening_stock", summary=f"Opening stock entered for {count} line(s)")
        return {"message": f"Opening stock saved for {count} line(s)", "lines": count}
    return inv.run_with_retry(db, op)


# ------------------------------------------------------------------ stock
@router.get("/stock", response_model=Page[StockRow])
def stock(q: str | None = None, product_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None,
          location_id: uuid.UUID | None = None, include_zero: bool = False, params: PageParams = Depends(),
          p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    stmt = (select(StockBalance).join(Product, Product.id == StockBalance.product_id)
            .join(Location, Location.id == StockBalance.location_id).join(Warehouse, Warehouse.id == Location.warehouse_id)
            .outerjoin(StockBatch, StockBatch.id == StockBalance.batch_id)
            .where(StockBalance.org_id == p.org_id))
    if not include_zero:
        stmt = stmt.where(StockBalance.quantity != 0)
    if p.user.warehouse_ids:
        stmt = stmt.where(Location.warehouse_id.in_(p.user.warehouse_ids))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like), Product.barcode == q.strip(),
                              StockBatch.batch_number.ilike(like)))
    if product_id:
        stmt = stmt.where(StockBalance.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(Location.warehouse_id == warehouse_id)
    if location_id:
        stmt = stmt.where(StockBalance.location_id == location_id)
    stmt = apply_sort(stmt, params, {"product": Product.name, "warehouse": Warehouse.name, "quantity": StockBalance.quantity,
                                     "expires_on": StockBatch.expires_on}, Product.name)
    rows, total = paginate(db, stmt, params)
    items = [StockRow(product_id=b.product_id, sku=b.product.sku, barcode=b.product.barcode, product_name=b.product.name,
                      unit=b.product.base_unit.abbreviation, warehouse_id=b.location.warehouse_id,
                      warehouse_name=b.location.warehouse.name, location_id=b.location_id, location_name=location_label(b.location),
                      batch_id=b.batch_id, batch_number=b.batch.batch_number if b.batch else None,
                      expires_on=b.batch.expires_on if b.batch else None, quantity=b.quantity,
                      value=(b.quantity * b.product.average_cost).quantize(inv.Q)) for b in rows]
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


def movement_filters(stmt, p: Principal, *, product_id=None, location_id=None, warehouse_id=None, type_=None,
                     user_id=None, date_from: date | None = None, date_to: date | None = None, q: str | None = None,
                     reference_id=None):
    stmt = stmt.where(StockMovement.org_id == p.org_id)
    if product_id:
        stmt = stmt.where(StockMovement.product_id == product_id)
    if location_id:
        stmt = stmt.where(StockMovement.location_id == location_id)
    if warehouse_id or p.user.warehouse_ids:
        stmt = stmt.join(Location, Location.id == StockMovement.location_id)
        if warehouse_id:
            stmt = stmt.where(Location.warehouse_id == warehouse_id)
        if p.user.warehouse_ids:
            stmt = stmt.where(Location.warehouse_id.in_(p.user.warehouse_ids))
    if type_:
        stmt = stmt.where(StockMovement.movement_type.in_(type_))
    if user_id:
        stmt = stmt.where(StockMovement.created_by == user_id)
    if reference_id:
        stmt = stmt.where(StockMovement.reference_id == reference_id)
    if date_from:
        stmt = stmt.where(StockMovement.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(StockMovement.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.join(Product, Product.id == StockMovement.product_id).where(
            or_(Product.name.ilike(like), Product.sku.ilike(like), StockMovement.reference_no.ilike(like),
                StockMovement.counterparty.ilike(like)))
    return stmt


@router.get("/movements")
def list_movements(product_id: uuid.UUID | None = None, location_id: uuid.UUID | None = None,
                   warehouse_id: uuid.UUID | None = None, type: list[MovementType] | None = Query(None),
                   user_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
                   q: str | None = None, reference_id: uuid.UUID | None = None, params: PageParams = Depends(),
                   p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    stmt = movement_filters(select(StockMovement), p, product_id=product_id, location_id=location_id,
                            warehouse_id=warehouse_id, type_=type, user_id=user_id, date_from=date_from,
                            date_to=date_to, q=q, reference_id=reference_id).order_by(StockMovement.id.desc())
    rows, total = paginate(db, stmt, params)
    return {"items": movements_out(db, rows), "total": total, "page": params.page, "page_size": params.page_size}


# ------------------------------------------------------------------ batches & serials
@router.get("/batches")
def list_batches(q: str | None = None, product_id: uuid.UUID | None = None,
                 expiring_within: int | None = Query(None, ge=0, le=3650, description="days; 0 = already expired"),
                 with_stock: bool = True, params: PageParams = Depends(),
                 p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    qty = func.coalesce(func.sum(StockBalance.quantity), 0)
    stmt = (select(StockBatch, Product, qty.label("qty")).join(Product, Product.id == StockBatch.product_id)
            .outerjoin(StockBalance, StockBalance.batch_id == StockBatch.id).where(StockBatch.org_id == p.org_id)
            .group_by(StockBatch.id, Product.id))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(StockBatch.batch_number.ilike(like), Product.name.ilike(like), Product.sku.ilike(like)))
    if product_id:
        stmt = stmt.where(StockBatch.product_id == product_id)
    if expiring_within is not None:
        stmt = stmt.where(StockBatch.expires_on <= date.today() + timedelta(days=expiring_within))
    if with_stock:
        stmt = stmt.having(qty > 0)
    stmt = stmt.order_by(StockBatch.expires_on.asc().nulls_last(), StockBatch.batch_number)
    rows, total = paginate(db, stmt, params, scalars=False)
    today = date.today()
    items = []
    for b, prod, qty_ in rows:
        days = (b.expires_on - today).days if b.expires_on else None
        items.append({"id": b.id, "batch_number": b.batch_number, "product_id": prod.id, "product_name": prod.name,
                      "sku": prod.sku, "unit": prod.base_unit.abbreviation, "manufactured_on": b.manufactured_on,
                      "expires_on": b.expires_on, "days_to_expiry": days, "quantity": qty_,
                      "expiry_status": expiry_bucket(days), "purchase_reference": b.purchase_reference})
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size}


def expiry_bucket(days: int | None) -> str:
    if days is None:
        return "no_expiry"
    if days < 0:
        return "expired"
    if days <= 7:
        return "7_days"
    if days <= 30:
        return "30_days"
    if days <= 90:
        return "90_days"
    return "ok"


@router.get("/serials")
def list_serials(q: str | None = None, product_id: uuid.UUID | None = None, status: str | None = None,
                 params: PageParams = Depends(), p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    stmt = (select(SerialNumber, Product).join(Product, Product.id == SerialNumber.product_id)
            .where(SerialNumber.org_id == p.org_id))
    if q:
        stmt = stmt.where(or_(SerialNumber.serial.ilike(f"%{q.strip()}%"), Product.name.ilike(f"%{q.strip()}%")))
    if product_id:
        stmt = stmt.where(SerialNumber.product_id == product_id)
    if status:
        stmt = stmt.where(SerialNumber.status == status)
    stmt = stmt.order_by(Product.name, SerialNumber.serial)
    rows, total = paginate(db, stmt, params, scalars=False)
    loc_ids = {s.location_id for s, _ in rows if s.location_id}
    labels = {l.id: location_label(l) for l in db.execute(select(Location).where(Location.id.in_(loc_ids))).unique().scalars()} if loc_ids else {}
    return {"items": [{"id": s.id, "serial": s.serial, "status": s.status.value, "product_id": prod.id,
                       "product_name": prod.name, "sku": prod.sku, "location": labels.get(s.location_id),
                       "updated_at": s.updated_at} for s, prod in rows],
            "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/serials/{serial_id}/history")
def serial_history(serial_id: uuid.UUID, p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    s = scoped_get(db, SerialNumber, serial_id, p.org_id, "Serial number")
    mvs = db.execute(select(StockMovement).join(MovementSerial, MovementSerial.movement_id == StockMovement.id)
                     .where(MovementSerial.serial_id == s.id).order_by(StockMovement.id)).unique().scalars().all()
    product = db.get(Product, s.product_id)
    return {"serial": s.serial, "status": s.status.value, "product_id": product.id, "product_name": product.name,
            "history": movements_out(db, mvs)}


@router.get("/adjustments")
def list_adjustments(params: PageParams = Depends(), p: Principal = Depends(require_any("inventory.adjust", "reports.view")),
                     db: Session = Depends(get_db)):
    stmt = select(StockAdjustment).where(StockAdjustment.org_id == p.org_id).order_by(StockAdjustment.created_at.desc())
    rows, total = paginate(db, stmt, params)
    return {"items": [{"id": a.id, "number": a.number, "reason": a.reason, "notes": a.notes, "created_at": a.created_at,
                       "lines": len(a.items)} for a in rows], "total": total, "page": params.page, "page_size": params.page_size}
