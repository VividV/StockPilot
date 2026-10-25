"""Dashboard, global search and notifications."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, current_principal, require
from app.models import (
    INBOUND_TYPES, Customer, Location, MovementType, Notification, Product, SerialNumber, StockBalance, StockBatch,
    StockMovement, Supplier, Transfer, TransferStatus, Warehouse,
)
from app.routers.common import money, movements_out
from app.schemas import Message, NotificationOut, Page
from app.services import inventory as inv

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/dashboard")
def dashboard(days: int = Query(30, ge=1, le=365), p: Principal = Depends(require("inventory.view")),
              db: Session = Depends(get_db)):
    org = p.org_id
    wh_ids = p.user.warehouse_ids

    def scope(stmt):
        if wh_ids:
            return stmt.join(Location, Location.id == StockBalance.location_id).where(Location.warehouse_id.in_(wh_ids))
        return stmt

    per_product = scope(select(StockBalance.product_id, func.sum(StockBalance.quantity).label("qty"))
                        .where(StockBalance.org_id == org)).group_by(StockBalance.product_id).subquery()
    qty = func.coalesce(per_product.c.qty, 0)
    threshold = func.greatest(Product.reorder_level, Product.min_stock)
    base = select(Product).outerjoin(per_product, per_product.c.product_id == Product.id).where(
        Product.org_id == org, Product.is_active.is_(True), Product.has_variants.is_(False))
    product_count = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    out_count = db.execute(select(func.count()).select_from(base.where(qty <= 0).subquery())).scalar_one()
    low_count = db.execute(select(func.count()).select_from(
        base.where(qty > 0, threshold > 0, qty <= threshold).subquery())).scalar_one()
    totals = db.execute(scope(select(func.coalesce(func.sum(StockBalance.quantity), 0),
                                     func.coalesce(func.sum(StockBalance.quantity * Product.average_cost), 0))
                              .join(Product, Product.id == StockBalance.product_id).where(StockBalance.org_id == org))).one()

    today = date.today()
    expiring = db.execute(scope(
        select(func.count(func.distinct(StockBalance.batch_id))).join(StockBatch, StockBatch.id == StockBalance.batch_id)
        .where(StockBalance.org_id == org, StockBalance.quantity > 0, StockBatch.expires_on <= today + timedelta(days=30),
               StockBatch.expires_on >= today))).scalar_one()
    expired = db.execute(scope(
        select(func.count(func.distinct(StockBalance.batch_id))).join(StockBatch, StockBatch.id == StockBalance.batch_id)
        .where(StockBalance.org_id == org, StockBalance.quantity > 0, StockBatch.expires_on < today))).scalar_one()

    since = datetime.now(timezone.utc) - timedelta(days=days)
    mv_scope = select(StockMovement).where(StockMovement.org_id == org, StockMovement.created_at >= since)
    if wh_ids:
        mv_scope = mv_scope.join(Location, Location.id == StockMovement.location_id).where(Location.warehouse_id.in_(wh_ids))
    mv = mv_scope.subquery()
    received = db.execute(select(func.coalesce(func.sum(mv.c.quantity), 0)).where(
        mv.c.movement_type.in_([MovementType.PURCHASE_RECEIPT, MovementType.OPENING_BALANCE, MovementType.RETURN,
                                MovementType.PRODUCTION_OUTPUT]))).scalar_one()
    issued = db.execute(select(func.coalesce(-func.sum(mv.c.quantity), 0)).where(
        mv.c.movement_type.in_([MovementType.SALE, MovementType.ISSUE, MovementType.PRODUCTION_CONSUMPTION]))).scalar_one()
    top = db.execute(select(Product.id, Product.name, Product.sku, -func.sum(mv.c.quantity))
                     .join(mv, mv.c.product_id == Product.id)
                     .where(mv.c.movement_type.in_([MovementType.SALE, MovementType.ISSUE]))
                     .group_by(Product.id).order_by(func.sum(mv.c.quantity).asc()).limit(5)).all()

    by_loc_stmt = (select(Warehouse.id, Warehouse.name, func.coalesce(func.sum(StockBalance.quantity), 0),
                          func.coalesce(func.sum(StockBalance.quantity * Product.average_cost), 0))
                   .select_from(Warehouse).join(Location, Location.warehouse_id == Warehouse.id)
                   .join(StockBalance, StockBalance.location_id == Location.id)
                   .join(Product, Product.id == StockBalance.product_id)
                   .where(Warehouse.org_id == org).group_by(Warehouse.id).order_by(Warehouse.name))
    if wh_ids:
        by_loc_stmt = by_loc_stmt.where(Warehouse.id.in_(wh_ids))

    recent_stmt = select(StockMovement).where(StockMovement.org_id == org)
    if wh_ids:
        recent_stmt = recent_stmt.join(Location, Location.id == StockMovement.location_id).where(Location.warehouse_id.in_(wh_ids))
    recent = db.execute(recent_stmt.order_by(StockMovement.id.desc()).limit(8)).unique().scalars().all()

    pending_transfers = db.execute(select(func.count()).where(
        Transfer.org_id == org, Transfer.status.in_([TransferStatus.REQUESTED, TransferStatus.APPROVED, TransferStatus.IN_TRANSIT]))).scalar_one()

    return {
        "currency": p.org.currency,
        "inventory_value": money(totals[1]),
        "total_units": Decimal(totals[0]),
        "product_count": product_count,
        "low_stock_count": low_count,
        "out_of_stock_count": out_count,
        "expiring_count": expiring,
        "expired_count": expired,
        "pending_transfers": pending_transfers,
        "period_days": days,
        "received_units": Decimal(received),
        "issued_units": Decimal(issued),
        "top_products": [{"product_id": i, "name": n, "sku": s, "quantity": q} for i, n, s, q in top],
        "by_location": [{"warehouse_id": i, "name": n, "units": u, "value": money(v)} for i, n, u, v in db.execute(by_loc_stmt).all()],
        "recent_movements": movements_out(db, recent),
    }


@router.get("/search")
def search(q: str = Query(..., min_length=1, max_length=100), p: Principal = Depends(current_principal),
           db: Session = Depends(get_db)):
    """One box finds products (name/SKU/barcode), batches, serials, suppliers,
    customers, transfers and movement references."""
    term = q.strip()
    like = f"%{term}%"
    out: list[dict] = []
    org = p.org_id
    if p.can("products.view"):
        for prod in db.execute(select(Product).where(Product.org_id == org, or_(
                Product.name.ilike(like), Product.sku.ilike(like), Product.barcode == term)).order_by(Product.name).limit(8)).unique().scalars():
            out.append({"type": "product", "id": prod.id, "title": prod.name, "subtitle": f"SKU {prod.sku}" + (f" · {prod.barcode}" if prod.barcode else ""), "url": f"/products/{prod.id}"})
    if p.can("inventory.view"):
        for b, prod in db.execute(select(StockBatch, Product).join(Product, Product.id == StockBatch.product_id).where(
                StockBatch.org_id == org, StockBatch.batch_number.ilike(like)).limit(5)).unique().all():
            out.append({"type": "batch", "id": b.id, "title": f"Batch {b.batch_number}", "subtitle": f"{prod.name}" + (f" · expires {b.expires_on}" if b.expires_on else ""), "url": f"/products/{prod.id}"})
        for s, prod in db.execute(select(SerialNumber, Product).join(Product, Product.id == SerialNumber.product_id).where(
                SerialNumber.org_id == org, SerialNumber.serial.ilike(like)).limit(5)).unique().all():
            out.append({"type": "serial", "id": s.id, "title": f"Serial {s.serial}", "subtitle": f"{prod.name} · {s.status.value.replace('_', ' ')}", "url": f"/serials/{s.id}"})
        for t in db.execute(select(Transfer).where(Transfer.org_id == org, Transfer.number.ilike(like)).limit(5)).unique().scalars():
            out.append({"type": "transfer", "id": t.id, "title": t.number, "subtitle": f"Transfer · {t.status.value.replace('_', ' ')}", "url": f"/transfers/{t.id}"})
        refs = db.execute(select(StockMovement.reference_no, func.max(StockMovement.id)).where(
            StockMovement.org_id == org, StockMovement.reference_no.ilike(like)).group_by(StockMovement.reference_no).limit(5)).all()
        for ref, _ in refs:
            out.append({"type": "reference", "id": ref, "title": ref, "subtitle": "Reference on stock movements", "url": f"/movements?q={ref}"})
    if p.can("purchases.view"):
        from app.models import PurchaseOrder
        for po in db.execute(select(PurchaseOrder).join(Supplier, Supplier.id == PurchaseOrder.supplier_id).where(
                PurchaseOrder.org_id == org, or_(PurchaseOrder.number.ilike(like), PurchaseOrder.supplier_reference.ilike(like)))
                .order_by(PurchaseOrder.created_at.desc()).limit(5)).unique().scalars():
            out.append({"type": "purchase_order", "id": po.id, "title": po.number,
                        "subtitle": f"Purchase order · {po.supplier.name} · {po.status.value}", "url": f"/purchases/{po.id}"})
    if p.can("sales.view"):
        from app.models import SalesOrder
        for so in db.execute(select(SalesOrder).where(
                SalesOrder.org_id == org, or_(SalesOrder.number.ilike(like), SalesOrder.customer_reference.ilike(like)))
                .order_by(SalesOrder.created_at.desc()).limit(5)).unique().scalars():
            out.append({"type": "sales_order", "id": so.id, "title": so.number,
                        "subtitle": f"Sales order · {so.customer.name if so.customer else 'Walk-in'} · {so.status.value}",
                        "url": f"/sales/{so.id}"})
    if p.can("suppliers.view"):
        for s in db.execute(select(Supplier).where(Supplier.org_id == org, Supplier.name.ilike(like)).limit(5)).scalars():
            out.append({"type": "supplier", "id": s.id, "title": s.name, "subtitle": "Supplier", "url": f"/suppliers?id={s.id}"})
    if p.can("customers.view"):
        for c in db.execute(select(Customer).where(Customer.org_id == org, Customer.name.ilike(like)).limit(5)).scalars():
            out.append({"type": "customer", "id": c.id, "title": c.name, "subtitle": "Customer", "url": f"/customers?id={c.id}"})
    return {"query": term, "results": out}


@router.get("/notifications", response_model=Page[NotificationOut])
def notifications(unread_only: bool = False, include_resolved: bool = False, page: int = Query(1, ge=1),
                  page_size: int = Query(30, ge=1, le=100), p: Principal = Depends(current_principal),
                  db: Session = Depends(get_db)):
    inv.refresh_expiry_alerts(db, p.org_id, warn_days=30)
    db.commit()
    stmt = select(Notification).where(Notification.org_id == p.org_id)
    if not include_resolved:
        stmt = stmt.where(Notification.is_resolved.is_(False))
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Notification.is_read, Notification.created_at.desc())
                      .limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return Page(items=rows, total=total, page=page, page_size=page_size)


@router.get("/notifications/unread-count")
def unread_count(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    n = db.execute(select(func.count()).where(Notification.org_id == p.org_id, Notification.is_read.is_(False),
                                             Notification.is_resolved.is_(False))).scalar_one()
    return {"unread": n}


@router.post("/notifications/{notification_id}/read", response_model=Message)
def mark_read(notification_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.id == notification_id, Notification.org_id == p.org_id).values(is_read=True))
    db.commit()
    return Message(message="ok")


@router.post("/notifications/read-all", response_model=Message)
def mark_all_read(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.org_id == p.org_id, Notification.is_read.is_(False)).values(is_read=True))
    db.commit()
    return Message(message="All notifications marked as read")
