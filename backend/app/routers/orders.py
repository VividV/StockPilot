"""Purchase orders (/api/purchase-orders) and sales orders (/api/sales-orders)."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, require
from app.models import Customer, OrderStatus, PaymentStatus, PurchaseOrder, StockBalance, SalesOrder, Supplier
from app.routers.common import PageParams, location_label, movements_out, paginate
from app.services import inventory as inv
from app.services import orders as svc

Qty = Field(gt=0, max_digits=18, decimal_places=4)
Money = Field(None, ge=0, max_digits=14, decimal_places=4)


# ------------------------------------------------------------------ schemas
class POLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Qty
    unit_id: uuid.UUID | None = None
    unit_cost: Decimal | None = Money
    tax_rate: Decimal | None = Field(None, ge=0, le=100)


class POIn(BaseModel):
    supplier_id: uuid.UUID
    destination_location_id: uuid.UUID
    expected_date: date | None = None
    supplier_reference: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=2000)
    lines: list[POLineIn] = Field(min_length=1, max_length=500)
    confirm: bool = False


class POUpdate(BaseModel):
    supplier_id: uuid.UUID | None = None
    destination_location_id: uuid.UUID | None = None
    expected_date: date | None = None
    supplier_reference: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=2000)
    lines: list[POLineIn] | None = Field(None, min_length=1, max_length=500)


class POReceiveLine(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    batch_number: str | None = Field(None, max_length=80)
    expires_on: date | None = None
    manufactured_on: date | None = None
    serials: list[str] | None = None


class POReceiveIn(BaseModel):
    lines: list[POReceiveLine] | None = None   # omit = receive everything outstanding
    note: str | None = Field(None, max_length=1000)


class FromLowStockIn(BaseModel):
    destination_location_id: uuid.UUID
    product_ids: list[uuid.UUID] | None = None


class SOLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Qty
    unit_id: uuid.UUID | None = None
    unit_price: Decimal | None = Money
    discount_percent: Decimal = Field(Decimal(0), ge=0, le=100)
    tax_rate: Decimal | None = Field(None, ge=0, le=100)


class SOIn(BaseModel):
    customer_id: uuid.UUID | None = None
    source_location_id: uuid.UUID
    customer_reference: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=2000)
    lines: list[SOLineIn] = Field(min_length=1, max_length=500)
    confirm: bool = False


class SOUpdate(BaseModel):
    customer_id: uuid.UUID | None = None
    source_location_id: uuid.UUID | None = None
    customer_reference: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=2000)
    lines: list[SOLineIn] | None = Field(None, min_length=1, max_length=500)


class SOFulfilLine(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    batch_id: uuid.UUID | None = None
    serials: list[str] | None = None


class SOFulfilIn(BaseModel):
    lines: list[SOFulfilLine] | None = None   # omit = send everything left
    note: str | None = Field(None, max_length=1000)


class PaymentIn(BaseModel):
    amount: Decimal | None = Field(None, max_digits=14, decimal_places=2, description="Omit to pay the full balance; negative = refund")


# ------------------------------------------------------------------ serialisers
def _available(db: Session, location_id, product_ids) -> dict:
    if not product_ids:
        return {}
    rows = db.execute(select(StockBalance.product_id, StockBalance.quantity).where(
        StockBalance.location_id == location_id, StockBalance.product_id.in_(product_ids))).all()
    out: dict = {}
    for pid, qty in rows:
        out[pid] = out.get(pid, Decimal(0)) + qty
    return out


def po_out(db: Session, po: PurchaseOrder, detail: bool = False) -> dict:
    closed = po.status in (OrderStatus.COMPLETED, OrderStatus.CANCELLED)
    today = date.today()
    d = {
        "id": po.id, "number": po.number, "status": po.status.value, "supplier_id": po.supplier_id,
        "supplier_name": po.supplier.name, "destination_location_id": po.destination_location_id,
        "destination_name": location_label(po.destination), "order_date": po.order_date, "expected_date": po.expected_date,
        "overdue": bool(po.expected_date and po.expected_date < today and po.status in (OrderStatus.CONFIRMED, OrderStatus.PARTIAL)),
        "supplier_reference": po.supplier_reference, "notes": po.notes, "created_at": po.created_at,
        "confirmed_at": po.confirmed_at, "closed_at": po.closed_at, "line_count": len(po.items), **svc.po_totals(po),
    }
    if detail:
        d["items"] = [{
            "id": i.id, "line_no": i.line_no, "product_id": i.product_id, "product_name": i.product.name, "sku": i.product.sku,
            "unit": i.product.base_unit.abbreviation, "track_batches": i.product.track_batches,
            "track_expiry": i.product.track_expiry, "track_serials": i.product.track_serials,
            "ordered_qty": i.ordered_qty, "received_qty": i.received_qty,
            "backorder_qty": Decimal(0) if closed else max(i.ordered_qty - i.received_qty, Decimal(0)),
            "unit_cost": i.unit_cost, "tax_rate": i.tax_rate, "line_total": svc.money(i.ordered_qty * i.unit_cost),
        } for i in po.items]
    return d


def so_out(db: Session, so: SalesOrder, detail: bool = False) -> dict:
    closed = so.status in (OrderStatus.COMPLETED, OrderStatus.CANCELLED)
    d = {
        "id": so.id, "number": so.number, "status": so.status.value, "payment_status": so.payment_status.value,
        "customer_id": so.customer_id, "customer_name": so.customer.name if so.customer else None,
        "source_location_id": so.source_location_id, "source_name": location_label(so.source),
        "order_date": so.order_date, "customer_reference": so.customer_reference, "notes": so.notes,
        "created_at": so.created_at, "confirmed_at": so.confirmed_at, "closed_at": so.closed_at,
        "line_count": len(so.items), **svc.so_totals(so),
    }
    if detail:
        avail = _available(db, so.source_location_id, [i.product_id for i in so.items]) if not closed else {}
        d["items"] = []
        for i in so.items:
            a = svc.so_line_amounts(i)
            d["items"].append({
                "id": i.id, "line_no": i.line_no, "product_id": i.product_id, "product_name": i.product.name,
                "sku": i.product.sku, "unit": i.product.base_unit.abbreviation, "track_batches": i.product.track_batches,
                "track_serials": i.product.track_serials, "ordered_qty": i.ordered_qty, "fulfilled_qty": i.fulfilled_qty,
                "remaining_qty": Decimal(0) if closed else max(i.ordered_qty - i.fulfilled_qty, Decimal(0)),
                "available_qty": avail.get(i.product_id, Decimal(0)) if not closed else None,
                "unit_price": i.unit_price, "discount_percent": i.discount_percent, "tax_rate": i.tax_rate,
                "line_total": svc.money(a["total"]), "cost_of_goods": svc.money(i.cost_of_goods),
            })
    return d


# ================================================================== purchase orders
purchases = APIRouter(prefix="/api/purchase-orders", tags=["purchase orders"])


@purchases.get("")
def list_pos(status: list[OrderStatus] | None = Query(None), open_only: bool = False, supplier_id: uuid.UUID | None = None,
             q: str | None = None, overdue: bool = False, params: PageParams = Depends(),
             p: Principal = Depends(require("purchases.view")), db: Session = Depends(get_db)):
    stmt = select(PurchaseOrder).join(Supplier, Supplier.id == PurchaseOrder.supplier_id).where(PurchaseOrder.org_id == p.org_id)
    if open_only:
        stmt = stmt.where(PurchaseOrder.status.in_(svc.OPEN))
    if status:
        stmt = stmt.where(PurchaseOrder.status.in_(status))
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    if overdue:
        stmt = stmt.where(PurchaseOrder.status.in_([OrderStatus.CONFIRMED, OrderStatus.PARTIAL]),
                          PurchaseOrder.expected_date < date.today())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(PurchaseOrder.number.ilike(like), Supplier.name.ilike(like), PurchaseOrder.supplier_reference.ilike(like)))
    rows, total = paginate(db, stmt.order_by(PurchaseOrder.created_at.desc()), params)
    return {"items": [po_out(db, po) for po in rows], "total": total, "page": params.page, "page_size": params.page_size}


@purchases.get("/suggestions")
def reorder_suggestions(p: Principal = Depends(require("purchases.view")), db: Session = Depends(get_db)):
    """What to order: everything at or below its reorder level, counting stock already on order."""
    return [{"product_id": s["product"].id, "name": s["product"].name, "sku": s["product"].sku,
             "unit": s["product"].base_unit.abbreviation, "on_hand": s["on_hand"], "on_order": s["on_order"],
             "reorder_level": s["threshold"], "suggested_qty": s["suggest"], "unit_cost": s["product"].cost_price,
             "supplier_id": s["product"].preferred_supplier_id,
             "supplier_name": s["product"].preferred_supplier.name if s["product"].preferred_supplier else None}
            for s in svc.suggested_reorders(db, p.ctx)]


@purchases.post("/from-low-stock", status_code=201)
def from_low_stock(body: FromLowStockIn, p: Principal = Depends(require("purchases.create")), db: Session = Depends(get_db)):
    def op():
        pos, skipped = svc.pos_from_low_stock(db, p.ctx, body.destination_location_id, body.product_ids)
        for po in pos:
            p.audit(db, "purchase.create", entity_type="purchase_order", entity_id=po.id,
                    summary=f"{po.number} drafted for {po.supplier.name} from the low-stock list")
        msg = (f"Drafted {len(pos)} purchase order{'s' if len(pos) != 1 else ''}" if pos else "Nothing to order")
        if skipped:
            msg += f". {len(skipped)} product{'s have' if len(skipped) != 1 else ' has'} no preferred supplier"
        return {"message": msg, "purchase_orders": [po_out(db, po) for po in pos],
                "skipped": [{"product_id": x.id, "name": x.name, "sku": x.sku} for x in skipped]}
    return inv.run_with_retry(db, op)


@purchases.post("", status_code=201)
def create_po(body: POIn, p: Principal = Depends(require("purchases.create")), db: Session = Depends(get_db)):
    def op():
        po = svc.create_po(db, p.ctx, supplier_id=body.supplier_id, destination_location_id=body.destination_location_id,
                           lines=[l.model_dump() for l in body.lines], expected_date=body.expected_date,
                           supplier_reference=body.supplier_reference, notes=body.notes, confirm=body.confirm)
        t = svc.po_totals(po)
        p.audit(db, "purchase.create", entity_type="purchase_order", entity_id=po.id,
                summary=f"{po.number} for {po.supplier.name}, {len(po.items)} line(s), total {t['total']}")
        return po_out(db, po, detail=True)
    return inv.run_with_retry(db, op)


@purchases.get("/{po_id}")
def get_po(po_id: uuid.UUID, p: Principal = Depends(require("purchases.view")), db: Session = Depends(get_db)):
    po = svc.get_po(db, p.ctx, po_id)
    out = po_out(db, po, detail=True)
    out["movements"] = movements_out(db, _ref_movements(db, p, po.id))
    return out


def _ref_movements(db, p, ref_id):
    from app.models import StockMovement
    return db.execute(select(StockMovement).where(StockMovement.org_id == p.org_id, StockMovement.reference_id == ref_id)
                      .order_by(StockMovement.id.desc()).limit(500)).unique().scalars().all()


@purchases.put("/{po_id}")
def update_po(po_id: uuid.UUID, body: POUpdate, p: Principal = Depends(require("purchases.create")), db: Session = Depends(get_db)):
    def op():
        po = svc.get_po(db, p.ctx, po_id, lock=True)
        before = svc.po_totals(po)["total"]
        data = body.model_dump(exclude_unset=True)
        if data.get("lines") is not None:
            data["lines"] = [l.model_dump() for l in body.lines]
        svc.update_po(db, p.ctx, po, data)
        p.audit(db, "purchase.update", entity_type="purchase_order", entity_id=po.id, summary=f"Updated {po.number}",
                before={"total": str(before)}, after={"total": str(svc.po_totals(po)["total"])})
        return po_out(db, po, detail=True)
    return inv.run_with_retry(db, op)


@purchases.post("/{po_id}/confirm")
def confirm_po(po_id: uuid.UUID, p: Principal = Depends(require("purchases.create")), db: Session = Depends(get_db)):
    def op():
        po = svc.confirm_po(db, p.ctx, svc.get_po(db, p.ctx, po_id, lock=True))
        p.audit(db, "purchase.confirm", entity_type="purchase_order", entity_id=po.id, summary=f"{po.number} sent to {po.supplier.name}")
        return po_out(db, po, detail=True)
    return inv.run_with_retry(db, op)


@purchases.post("/{po_id}/receive")
def receive_po(po_id: uuid.UUID, body: POReceiveIn | None = None, p: Principal = Depends(require("purchases.receive", "inventory.receive")),
               db: Session = Depends(get_db)):
    def op():
        po = svc.get_po(db, p.ctx, po_id, lock=True)
        lines = [l.model_dump() for l in body.lines] if body and body.lines is not None else None
        mvs = svc.receive_po(db, p.ctx, po, lines, note=body.note if body else None)
        units = sum((m.quantity for m in mvs), Decimal(0))
        p.audit(db, "purchase.receive", entity_type="purchase_order", entity_id=po.id,
                summary=f"Received {inv.fmt(units)} unit(s) on {po.number}; now {po.status.value}",
                after={"movement_ids": [m.id for m in mvs]})
        out = po_out(db, po, detail=True)
        out["message"] = (f"{po.number} fully received" if po.status == OrderStatus.COMPLETED
                          else f"Received. {po.number} is waiting for the rest")
        out["movements"] = movements_out(db, mvs)
        return out
    return inv.run_with_retry(db, op)


@purchases.post("/{po_id}/close")
def close_po(po_id: uuid.UUID, p: Principal = Depends(require("purchases.create")), db: Session = Depends(get_db)):
    def op():
        po = svc.close_po(db, p.ctx, svc.get_po(db, p.ctx, po_id, lock=True))
        p.audit(db, "purchase.close", entity_type="purchase_order", entity_id=po.id, summary=f"{po.number} {po.status.value}")
        return po_out(db, po, detail=True)
    return inv.run_with_retry(db, op)


# ================================================================== sales orders
sales = APIRouter(prefix="/api/sales-orders", tags=["sales orders"])


@sales.get("")
def list_sos(status: list[OrderStatus] | None = Query(None), open_only: bool = False,
             payment_status: list[PaymentStatus] | None = Query(None), customer_id: uuid.UUID | None = None,
             q: str | None = None, params: PageParams = Depends(), p: Principal = Depends(require("sales.view")),
             db: Session = Depends(get_db)):
    stmt = select(SalesOrder).outerjoin(Customer, Customer.id == SalesOrder.customer_id).where(SalesOrder.org_id == p.org_id)
    if open_only:
        stmt = stmt.where(SalesOrder.status.in_(svc.OPEN))
    if status:
        stmt = stmt.where(SalesOrder.status.in_(status))
    if payment_status:
        stmt = stmt.where(SalesOrder.payment_status.in_(payment_status))
    if customer_id:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(SalesOrder.number.ilike(like), Customer.name.ilike(like), SalesOrder.customer_reference.ilike(like)))
    rows, total = paginate(db, stmt.order_by(SalesOrder.created_at.desc()), params)
    return {"items": [so_out(db, so) for so in rows], "total": total, "page": params.page, "page_size": params.page_size}


@sales.post("", status_code=201)
def create_so(body: SOIn, p: Principal = Depends(require("sales.create")), db: Session = Depends(get_db)):
    def op():
        so = svc.create_so(db, p.ctx, source_location_id=body.source_location_id, customer_id=body.customer_id,
                           lines=[l.model_dump() for l in body.lines], customer_reference=body.customer_reference,
                           notes=body.notes, confirm=body.confirm)
        t = svc.so_totals(so)
        p.audit(db, "sale.create", entity_type="sales_order", entity_id=so.id,
                summary=f"{so.number}{' for ' + so.customer.name if so.customer else ''}, total {t['total']}")
        return so_out(db, so, detail=True)
    return inv.run_with_retry(db, op)


@sales.get("/{so_id}")
def get_so(so_id: uuid.UUID, p: Principal = Depends(require("sales.view")), db: Session = Depends(get_db)):
    so = svc.get_so(db, p.ctx, so_id)
    out = so_out(db, so, detail=True)
    out["movements"] = movements_out(db, _ref_movements(db, p, so.id))
    return out


@sales.put("/{so_id}")
def update_so(so_id: uuid.UUID, body: SOUpdate, p: Principal = Depends(require("sales.create")), db: Session = Depends(get_db)):
    def op():
        so = svc.get_so(db, p.ctx, so_id, lock=True)
        before = svc.so_totals(so)["total"]
        data = body.model_dump(exclude_unset=True)
        if data.get("lines") is not None:
            data["lines"] = [l.model_dump() for l in body.lines]
        svc.update_so(db, p.ctx, so, data)
        p.audit(db, "sale.update", entity_type="sales_order", entity_id=so.id, summary=f"Updated {so.number}",
                before={"total": str(before)}, after={"total": str(svc.so_totals(so)["total"])})
        return so_out(db, so, detail=True)
    return inv.run_with_retry(db, op)


@sales.post("/{so_id}/confirm")
def confirm_so(so_id: uuid.UUID, p: Principal = Depends(require("sales.create")), db: Session = Depends(get_db)):
    def op():
        so = svc.confirm_so(db, p.ctx, svc.get_so(db, p.ctx, so_id, lock=True))
        p.audit(db, "sale.confirm", entity_type="sales_order", entity_id=so.id, summary=f"{so.number} confirmed")
        return so_out(db, so, detail=True)
    return inv.run_with_retry(db, op)


@sales.post("/{so_id}/fulfil")
def fulfil_so(so_id: uuid.UUID, body: SOFulfilIn | None = None, p: Principal = Depends(require("sales.fulfill", "inventory.issue")),
              db: Session = Depends(get_db)):
    def op():
        so = svc.get_so(db, p.ctx, so_id, lock=True)
        lines = [l.model_dump() for l in body.lines] if body and body.lines is not None else None
        mvs = svc.fulfil_so(db, p.ctx, so, lines, note=body.note if body else None)
        units = -sum((m.quantity for m in mvs), Decimal(0))
        p.audit(db, "sale.fulfil", entity_type="sales_order", entity_id=so.id,
                summary=f"Sent {inv.fmt(units)} unit(s) on {so.number}; now {so.status.value}",
                after={"movement_ids": [m.id for m in mvs]})
        out = so_out(db, so, detail=True)
        out["message"] = f"{so.number} fully sent" if so.status == OrderStatus.COMPLETED else f"Sent. {so.number} still has items to send"
        out["movements"] = movements_out(db, mvs)
        return out
    return inv.run_with_retry(db, op)


@sales.post("/{so_id}/payments")
def payment(so_id: uuid.UUID, body: PaymentIn, p: Principal = Depends(require("sales.create")), db: Session = Depends(get_db)):
    def op():
        so = svc.get_so(db, p.ctx, so_id, lock=True)
        before = svc.money(so.amount_paid)
        svc.record_payment(db, p.ctx, so, body.amount)
        p.audit(db, "sale.payment", entity_type="sales_order", entity_id=so.id,
                summary=f"Payment on {so.number}: {svc.money(so.amount_paid) - before}",
                before={"amount_paid": str(before)}, after={"amount_paid": str(svc.money(so.amount_paid)),
                                                           "payment_status": so.payment_status.value})
        return so_out(db, so, detail=True)
    return inv.run_with_retry(db, op)


@sales.post("/{so_id}/close")
def close_so(so_id: uuid.UUID, p: Principal = Depends(require("sales.create")), db: Session = Depends(get_db)):
    def op():
        so = svc.close_so(db, p.ctx, svc.get_so(db, p.ctx, so_id, lock=True))
        p.audit(db, "sale.close", entity_type="sales_order", entity_id=so.id, summary=f"{so.number} {so.status.value}")
        return so_out(db, so, detail=True)
    return inv.run_with_retry(db, op)
