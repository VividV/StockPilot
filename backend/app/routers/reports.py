import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, require
from app.models import (
    Customer, OrderStatus, PurchaseOrder, PurchaseOrderItem, SalesOrder, SalesOrderItem,
    Category, Location, MovementType, Product, SerialNumber, StockAdjustment, StockAdjustmentItem, StockBalance,
    StockBatch, StockMovement, Supplier, Transfer, TransferItem, TransferStatus, User, Warehouse,
)
from app.routers.common import location_label, movements_out, money
from app.routers.inventory import expiry_bucket, movement_filters
from app.services import export

router = APIRouter(prefix="/api/reports", tags=["reports"])
MAX_ROWS = 100_000


def col(key, label, type_="text"):
    return {"key": key, "label": label, "type": type_}


def _wh_filter(stmt, p: Principal, warehouse_id):
    ids = set(p.user.warehouse_ids)
    if warehouse_id:
        if ids and warehouse_id not in ids:
            raise HTTPException(403, "You are not allowed to see this location")
        return stmt.where(Location.warehouse_id == warehouse_id)
    if ids:
        return stmt.where(Location.warehouse_id.in_(ids))
    return stmt


def _threshold():
    return func.greatest(Product.reorder_level, Product.min_stock)


# ------------------------------------------------------------------ report builders
def r_valuation(db, p, warehouse_id=None, category_id=None, **_):
    qty = func.sum(StockBalance.quantity)
    stmt = (select(Product, Category.name, qty).join(StockBalance, StockBalance.product_id == Product.id)
            .join(Location, Location.id == StockBalance.location_id).outerjoin(Category, Category.id == Product.category_id)
            .where(Product.org_id == p.org_id).group_by(Product.id, Category.name).having(qty != 0).order_by(Product.name))
    stmt = _wh_filter(stmt, p, warehouse_id)
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    rows, total_value, total_units = [], Decimal(0), Decimal(0)
    for prod, cat, q in db.execute(stmt.limit(MAX_ROWS)).unique().all():
        value = money(q * prod.average_cost)
        total_value += value
        total_units += q
        rows.append({"sku": prod.sku, "name": prod.name, "category": cat, "unit": prod.base_unit.abbreviation,
                     "quantity": q, "average_cost": money(prod.average_cost), "value": value,
                     "retail_value": money(q * prod.selling_price)})
    in_transit = Decimal(0)
    if not warehouse_id and not category_id:
        for item in db.execute(select(TransferItem).join(Transfer).where(
                Transfer.org_id == p.org_id, Transfer.status == TransferStatus.IN_TRANSIT)).unique().scalars():
            for d in item.dispatch_detail:
                in_transit += (Decimal(d["qty"]) - Decimal(d["received"])) * Decimal(d["unit_cost"])
    cols = [col("sku", "SKU"), col("name", "Product"), col("category", "Category"), col("quantity", "On hand", "number"),
            col("unit", "Unit"), col("average_cost", "Avg cost", "money"), col("value", "Stock value", "money"),
            col("retail_value", "Retail value", "money")]
    summary = {"total_value": money(total_value), "total_units": total_units, "in_transit_value": money(in_transit),
               "products": len(rows)}
    return "Inventory valuation", cols, rows, summary


def r_stock_on_hand(db, p, warehouse_id=None, category_id=None, **_):
    stmt = (select(StockBalance).join(Product, Product.id == StockBalance.product_id)
            .join(Location, Location.id == StockBalance.location_id).join(Warehouse, Warehouse.id == Location.warehouse_id)
            .outerjoin(StockBatch, StockBatch.id == StockBalance.batch_id)
            .where(StockBalance.org_id == p.org_id, StockBalance.quantity != 0)
            .order_by(Product.name, Warehouse.name, StockBatch.expires_on.nulls_last()))
    stmt = _wh_filter(stmt, p, warehouse_id)
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    rows = [{"sku": b.product.sku, "name": b.product.name, "location": location_label(b.location),
             "batch": b.batch.batch_number if b.batch else "", "expires_on": b.batch.expires_on if b.batch else None,
             "quantity": b.quantity, "unit": b.product.base_unit.abbreviation,
             "value": money(b.quantity * b.product.average_cost)} for b in db.execute(stmt.limit(MAX_ROWS)).unique().scalars()]
    cols = [col("sku", "SKU"), col("name", "Product"), col("location", "Location"), col("batch", "Batch"),
            col("expires_on", "Expires"), col("quantity", "On hand", "number"), col("unit", "Unit"), col("value", "Value", "money")]
    return "Stock on hand", cols, rows, {"lines": len(rows)}


def _product_totals_stmt(p, warehouse_id):
    qty = func.coalesce(func.sum(StockBalance.quantity), 0)
    sub = select(StockBalance.product_id, func.sum(StockBalance.quantity).label("qty")).join(
        Location, Location.id == StockBalance.location_id).where(StockBalance.org_id == p.org_id)
    sub = _wh_filter(sub, p, warehouse_id).group_by(StockBalance.product_id).subquery()
    return sub


def r_low_stock(db, p, warehouse_id=None, **_):
    sub = _product_totals_stmt(p, warehouse_id)
    qty = func.coalesce(sub.c.qty, 0)
    stmt = (select(Product, qty, Supplier.name).outerjoin(sub, sub.c.product_id == Product.id)
            .outerjoin(Supplier, Supplier.id == Product.preferred_supplier_id)
            .where(Product.org_id == p.org_id, Product.is_active.is_(True), Product.has_variants.is_(False), _threshold() > 0, qty > 0, qty <= _threshold())
            .order_by((qty / _threshold()).asc(), Product.name))
    rows = []
    for prod, q, supplier in db.execute(stmt.limit(MAX_ROWS)).unique().all():
        thr = max(prod.reorder_level, prod.min_stock)
        suggest = prod.reorder_qty if prod.reorder_qty else max(thr * 2 - q, Decimal(1))
        rows.append({"sku": prod.sku, "name": prod.name, "quantity": q, "reorder_level": thr, "unit": prod.base_unit.abbreviation,
                     "suggested_order": suggest, "supplier": supplier or "", "product_id": prod.id})
    cols = [col("sku", "SKU"), col("name", "Product"), col("quantity", "On hand", "number"), col("reorder_level", "Reorder level", "number"),
            col("unit", "Unit"), col("suggested_order", "Suggested order", "number"), col("supplier", "Preferred supplier")]
    return "Low stock", cols, rows, {"products": len(rows)}


def r_out_of_stock(db, p, warehouse_id=None, **_):
    sub = _product_totals_stmt(p, warehouse_id)
    qty = func.coalesce(sub.c.qty, 0)
    stmt = (select(Product, qty, Supplier.name).outerjoin(sub, sub.c.product_id == Product.id)
            .outerjoin(Supplier, Supplier.id == Product.preferred_supplier_id)
            .where(Product.org_id == p.org_id, Product.is_active.is_(True), Product.has_variants.is_(False), qty <= 0).order_by(Product.name))
    rows = [{"sku": prod.sku, "name": prod.name, "quantity": q, "unit": prod.base_unit.abbreviation,
             "reorder_qty": prod.reorder_qty, "supplier": supplier or "", "product_id": prod.id}
            for prod, q, supplier in db.execute(stmt.limit(MAX_ROWS)).unique().all()]
    cols = [col("sku", "SKU"), col("name", "Product"), col("quantity", "On hand", "number"), col("unit", "Unit"),
            col("reorder_qty", "Reorder qty", "number"), col("supplier", "Preferred supplier")]
    return "Out of stock", cols, rows, {"products": len(rows)}


def r_expiry(db, p, warehouse_id=None, days: int = 90, **_):
    today = date.today()
    stmt = (select(StockBalance).join(StockBatch, StockBatch.id == StockBalance.batch_id)
            .join(Location, Location.id == StockBalance.location_id)
            .where(StockBalance.org_id == p.org_id, StockBalance.quantity > 0, StockBatch.expires_on.is_not(None),
                   StockBatch.expires_on <= today + timedelta(days=days))
            .order_by(StockBatch.expires_on))
    stmt = _wh_filter(stmt, p, warehouse_id)
    labels = {"expired": "Expired", "7_days": "Within 7 days", "30_days": "Within 30 days", "90_days": "Within 90 days", "ok": "Later"}
    rows, counts = [], {"expired": 0, "7_days": 0, "30_days": 0, "90_days": 0}
    for b in db.execute(stmt.limit(MAX_ROWS)).unique().scalars():
        d = (b.batch.expires_on - today).days
        bucket = expiry_bucket(d)
        counts[bucket] = counts.get(bucket, 0) + 1
        rows.append({"status": labels.get(bucket, bucket), "bucket": bucket, "expires_on": b.batch.expires_on, "days": d,
                     "sku": b.product.sku, "name": b.product.name, "batch": b.batch.batch_number,
                     "location": location_label(b.location), "quantity": b.quantity, "unit": b.product.base_unit.abbreviation,
                     "value": money(b.quantity * b.product.average_cost)})
    cols = [col("status", "Status"), col("expires_on", "Expires"), col("days", "Days left", "number"), col("sku", "SKU"),
            col("name", "Product"), col("batch", "Batch"), col("location", "Location"), col("quantity", "Quantity", "number"),
            col("unit", "Unit"), col("value", "Value", "money")]
    return f"Expiry report (next {days} days)", cols, rows, counts


def r_batches(db, p, **_):
    qty = func.coalesce(func.sum(StockBalance.quantity), 0)
    stmt = (select(StockBatch, Product, qty, Supplier.name).join(Product, Product.id == StockBatch.product_id)
            .outerjoin(StockBalance, StockBalance.batch_id == StockBatch.id)
            .outerjoin(Supplier, Supplier.id == StockBatch.supplier_id)
            .where(StockBatch.org_id == p.org_id).group_by(StockBatch.id, Product.id, Supplier.name)
            .order_by(Product.name, StockBatch.expires_on.nulls_last()))
    rows = [{"sku": prod.sku, "name": prod.name, "batch": b.batch_number, "manufactured_on": b.manufactured_on,
             "expires_on": b.expires_on, "quantity": q, "unit": prod.base_unit.abbreviation, "supplier": sup or "",
             "reference": b.purchase_reference or ""} for b, prod, q, sup in db.execute(stmt.limit(MAX_ROWS)).unique().all()]
    cols = [col("sku", "SKU"), col("name", "Product"), col("batch", "Batch"), col("manufactured_on", "Made"),
            col("expires_on", "Expires"), col("quantity", "On hand", "number"), col("unit", "Unit"),
            col("supplier", "Supplier"), col("reference", "Purchase ref")]
    return "Batches", cols, rows, {"batches": len(rows)}


def r_serials(db, p, **_):
    stmt = (select(SerialNumber, Product).join(Product, Product.id == SerialNumber.product_id)
            .where(SerialNumber.org_id == p.org_id).order_by(Product.name, SerialNumber.serial))
    res = db.execute(stmt.limit(MAX_ROWS)).unique().all()
    loc_ids = {s.location_id for s, _ in res if s.location_id}
    labels = {l.id: location_label(l) for l in db.execute(select(Location).where(Location.id.in_(loc_ids))).unique().scalars()} if loc_ids else {}
    rows = [{"sku": prod.sku, "name": prod.name, "serial": s.serial, "status": s.status.value.replace("_", " "),
             "location": labels.get(s.location_id, ""), "updated_at": s.updated_at} for s, prod in res]
    cols = [col("sku", "SKU"), col("name", "Product"), col("serial", "Serial number"), col("status", "Status"),
            col("location", "Location"), col("updated_at", "Last change")]
    return "Serial numbers", cols, rows, {"serials": len(rows)}


def r_movements(db, p, warehouse_id=None, product_id=None, type=None, date_from=None, date_to=None, **_):
    stmt = movement_filters(select(StockMovement), p, product_id=product_id, warehouse_id=warehouse_id,
                            type_=type, date_from=date_from, date_to=date_to).order_by(StockMovement.id.desc())
    mvs = db.execute(stmt.limit(MAX_ROWS)).unique().scalars().all()
    rows = []
    for m in movements_out(db, mvs):
        m["movement_type"] = m["movement_type"].value.replace("_", " ")
        m["serials"] = ", ".join(m["serials"])
        rows.append(m)
    cols = [col("created_at", "Date"), col("movement_type", "Type"), col("sku", "SKU"), col("product_name", "Product"),
            col("location_name", "Location"), col("batch_number", "Batch"), col("quantity", "Qty", "number"),
            col("unit", "Unit"), col("balance_after", "Balance after", "number"), col("reference_no", "Reference"),
            col("counterparty", "To / from"), col("user_name", "By"), col("note", "Note"), col("serials", "Serials")]
    received = sum((r["quantity"] for r in rows if r["quantity"] > 0), Decimal(0))
    issued = -sum((r["quantity"] for r in rows if r["quantity"] < 0), Decimal(0))
    return "Stock movements", cols, rows, {"movements": len(rows), "units_in": received, "units_out": issued}


def r_locations(db, p, **_):
    stmt = (select(Warehouse, func.count(func.distinct(StockBalance.product_id)).filter(StockBalance.quantity > 0),
                   func.coalesce(func.sum(StockBalance.quantity), 0),
                   func.coalesce(func.sum(StockBalance.quantity * Product.average_cost), 0))
            .select_from(Warehouse).outerjoin(Location, Location.warehouse_id == Warehouse.id)
            .outerjoin(StockBalance, StockBalance.location_id == Location.id)
            .outerjoin(Product, Product.id == StockBalance.product_id)
            .where(Warehouse.org_id == p.org_id).group_by(Warehouse.id).order_by(Warehouse.name))
    if p.user.warehouse_ids:
        stmt = stmt.where(Warehouse.id.in_(p.user.warehouse_ids))
    rows = [{"name": w.name, "code": w.code, "type": w.type.value, "products": n, "units": u, "value": money(v),
             "active": "Yes" if w.is_active else "No"} for w, n, u, v in db.execute(stmt).all()]
    cols = [col("name", "Location"), col("code", "Code"), col("type", "Type"), col("products", "Products", "number"),
            col("units", "Units", "number"), col("value", "Stock value", "money"), col("active", "Active")]
    return "Stock by location", cols, rows, {"locations": len(rows)}


def r_adjustments(db, p, date_from=None, date_to=None, **_):
    stmt = (select(StockAdjustmentItem, StockAdjustment, Product, Location, User.full_name)
            .join(StockAdjustment, StockAdjustment.id == StockAdjustmentItem.adjustment_id)
            .join(Product, Product.id == StockAdjustmentItem.product_id)
            .join(Location, Location.id == StockAdjustmentItem.location_id)
            .outerjoin(User, User.id == StockAdjustment.created_by)
            .where(StockAdjustment.org_id == p.org_id).order_by(StockAdjustment.created_at.desc()))
    if date_from:
        stmt = stmt.where(StockAdjustment.created_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(StockAdjustment.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    rows = [{"date": a.created_at, "number": a.number, "reason": a.reason.replace("_", " "), "sku": prod.sku, "name": prod.name,
             "location": location_label(loc), "before": i.quantity_before, "change": i.quantity_change, "after": i.quantity_after,
             "value": money(i.quantity_change * prod.average_cost), "user": user or "", "notes": a.notes or ""}
            for i, a, prod, loc, user in db.execute(stmt.limit(MAX_ROWS)).unique().all()]
    cols = [col("date", "Date"), col("number", "No."), col("reason", "Reason"), col("sku", "SKU"), col("name", "Product"),
            col("location", "Location"), col("before", "Before", "number"), col("change", "Change", "number"),
            col("after", "After", "number"), col("value", "Value impact", "money"), col("user", "By"), col("notes", "Notes")]
    return "Inventory adjustments", cols, rows, {"lines": len(rows), "value_impact": money(sum((r["value"] for r in rows), Decimal(0)))}


def _date_range(stmt, col, date_from, date_to):
    if date_from:
        stmt = stmt.where(col >= date_from)
    if date_to:
        stmt = stmt.where(col <= date_to)
    return stmt


def r_purchases(db, p, date_from=None, date_to=None, **_):
    from app.services.orders import po_totals
    stmt = _date_range(select(PurchaseOrder).where(PurchaseOrder.org_id == p.org_id), PurchaseOrder.order_date, date_from, date_to)
    rows, tot = [], {"ordered": Decimal(0), "received": Decimal(0), "outstanding": Decimal(0)}
    today = date.today()
    for po in db.execute(stmt.order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.number.desc()).limit(MAX_ROWS)).unique().scalars():
        t = po_totals(po)
        if po.status != OrderStatus.CANCELLED:
            tot["ordered"] += t["total"]
        tot["received"] += t["received_value"]
        tot["outstanding"] += t["outstanding_value"]
        late = po.expected_date and po.expected_date < today and po.status in (OrderStatus.CONFIRMED, OrderStatus.PARTIAL)
        rows.append({"number": po.number, "order_date": po.order_date, "supplier": po.supplier.name,
                     "status": po.status.value + (" (late)" if late else ""), "expected_on": po.expected_date,
                     "total": t["total"], "received_value": t["received_value"], "outstanding_value": t["outstanding_value"],
                     "purchase_order_id": po.id})
    cols = [col("number", "PO"), col("order_date", "Ordered"), col("supplier", "Supplier"), col("status", "Status"),
            col("expected_on", "Expected"), col("total", "Total", "money"), col("received_value", "Received", "money"),
            col("outstanding_value", "Still to come", "money")]
    return "Purchases", cols, rows, {"orders": len(rows), "ordered_value": money(tot["ordered"]),
                                     "received_value": money(tot["received"]), "outstanding_value": money(tot["outstanding"])}


def r_suppliers(db, p, date_from=None, date_to=None, **_):
    from app.services.orders import po_totals
    stats: dict = {}
    stmt = _date_range(select(PurchaseOrder).where(PurchaseOrder.org_id == p.org_id), PurchaseOrder.order_date, date_from, date_to)
    today = date.today()
    for po in db.execute(stmt.limit(MAX_ROWS)).unique().scalars():
        s_ = stats.setdefault(po.supplier_id, {"orders": 0, "open": 0, "late": 0, "ordered": Decimal(0), "received": Decimal(0),
                                                "outstanding": Decimal(0), "lead": []})
        t = po_totals(po)
        if po.status != OrderStatus.CANCELLED:
            s_["orders"] += 1
            s_["ordered"] += t["total"]
        s_["received"] += t["received_value"]
        s_["outstanding"] += t["outstanding_value"]
        if po.status in (OrderStatus.CONFIRMED, OrderStatus.PARTIAL, OrderStatus.DRAFT):
            s_["open"] += 1
            if po.expected_date and po.expected_date < today and po.status != OrderStatus.DRAFT:
                s_["late"] += 1
        if po.status == OrderStatus.COMPLETED and po.closed_at:
            s_["lead"].append((po.closed_at.date() - po.order_date).days)
    products = dict(db.execute(select(Product.preferred_supplier_id, func.count()).where(
        Product.org_id == p.org_id, Product.preferred_supplier_id.is_not(None)).group_by(Product.preferred_supplier_id)).all())
    rows = []
    for sup in db.execute(select(Supplier).where(Supplier.org_id == p.org_id).order_by(Supplier.name)).scalars():
        s_ = stats.get(sup.id, {"orders": 0, "open": 0, "late": 0, "ordered": 0, "received": 0, "outstanding": 0, "lead": []})
        rows.append({"name": sup.name, "contact": sup.contact_name or "", "phone": sup.phone or "", "products": products.get(sup.id, 0),
                     "orders": s_["orders"], "open_orders": s_["open"], "late_orders": s_["late"],
                     "ordered_value": money(s_["ordered"]), "received_value": money(s_["received"]),
                     "outstanding_value": money(s_["outstanding"]),
                     "avg_lead_days": round(sum(s_["lead"]) / len(s_["lead"]), 1) if s_["lead"] else None})
    cols = [col("name", "Supplier"), col("contact", "Contact"), col("phone", "Phone"), col("products", "Products", "number"),
            col("orders", "Orders", "number"), col("open_orders", "Open", "number"), col("late_orders", "Late", "number"),
            col("ordered_value", "Ordered", "money"), col("received_value", "Received", "money"),
            col("outstanding_value", "Still to come", "money"), col("avg_lead_days", "Avg days to deliver", "number")]
    return "Suppliers", cols, rows, {"suppliers": len(rows)}


def r_sales(db, p, date_from=None, date_to=None, **_):
    from app.services.orders import so_totals
    stmt = _date_range(select(SalesOrder).where(SalesOrder.org_id == p.org_id), SalesOrder.order_date, date_from, date_to)
    rows = []
    tot = {k: Decimal(0) for k in ("total", "paid", "due", "cogs", "profit")}
    for so in db.execute(stmt.order_by(SalesOrder.order_date.desc(), SalesOrder.number.desc()).limit(MAX_ROWS)).unique().scalars():
        t = so_totals(so)
        if so.status != OrderStatus.CANCELLED:
            tot["total"] += t["total"]
            tot["due"] += t["balance_due"]
        tot["paid"] += t["amount_paid"]
        tot["cogs"] += t["cost_of_goods"]
        tot["profit"] += t["gross_profit"]
        rows.append({"number": so.number, "order_date": so.order_date, "customer": so.customer.name if so.customer else "Walk-in",
                     "status": so.status.value, "payment": so.payment_status.value, "total": t["total"],
                     "amount_paid": t["amount_paid"], "balance_due": t["balance_due"] if so.status != OrderStatus.CANCELLED else Decimal(0),
                     "cost_of_goods": t["cost_of_goods"], "gross_profit": t["gross_profit"], "sales_order_id": so.id})
    cols = [col("number", "Order"), col("order_date", "Date"), col("customer", "Customer"), col("status", "Status"),
            col("payment", "Payment"), col("total", "Total", "money"), col("amount_paid", "Paid", "money"),
            col("balance_due", "Owed", "money"), col("cost_of_goods", "Cost of goods", "money"), col("gross_profit", "Gross profit", "money")]
    return "Sales", cols, rows, {"orders": len(rows), "sales_value": money(tot["total"]), "amount_paid": money(tot["paid"]),
                                 "balance_due": money(tot["due"]), "gross_profit": money(tot["profit"])}


def r_sales_by_product(db, p, date_from=None, date_to=None, **_):
    """Revenue counts what was actually shipped, so it lines up with cost of goods."""
    stmt = (select(SalesOrderItem, Product).join(SalesOrder, SalesOrder.id == SalesOrderItem.sales_order_id)
            .join(Product, Product.id == SalesOrderItem.product_id)
            .where(SalesOrder.org_id == p.org_id, SalesOrderItem.fulfilled_qty > 0))
    stmt = _date_range(stmt, SalesOrder.order_date, date_from, date_to)
    agg: dict = {}
    for item, prod in db.execute(stmt.limit(MAX_ROWS)).unique().all():
        a = agg.setdefault(prod.id, {"sku": prod.sku, "name": prod.name, "unit": prod.base_unit.abbreviation,
                                     "units": Decimal(0), "revenue": Decimal(0), "cogs": Decimal(0), "product_id": prod.id})
        gross = item.fulfilled_qty * item.unit_price
        a["units"] += item.fulfilled_qty
        a["revenue"] += gross - gross * item.discount_percent / 100
        a["cogs"] += item.cost_of_goods
    rows = []
    for a in sorted(agg.values(), key=lambda x: -x["revenue"]):
        profit = a["revenue"] - a["cogs"]
        rows.append({**a, "revenue": money(a["revenue"]), "cogs": money(a["cogs"]), "profit": money(profit),
                     "margin": round(float(profit / a["revenue"] * 100), 1) if a["revenue"] else None})
    cols = [col("sku", "SKU"), col("name", "Product"), col("units", "Units sold", "number"), col("unit", "Unit"),
            col("revenue", "Revenue (excl. tax)", "money"), col("cogs", "Cost of goods", "money"), col("profit", "Gross profit", "money"),
            col("margin", "Margin %", "number")]
    rev = sum((r["revenue"] for r in rows), Decimal(0))
    prof = sum((r["profit"] for r in rows), Decimal(0))
    return "Sales by product", cols, rows, {"products": len(rows), "revenue": money(rev), "gross_profit": money(prof)}


# ------------------------------------------------------------------ counter sales (Phase 3)
def _pos_range(stmt, p, col, date_from, date_to):
    from app.services.pos import day_bounds
    if date_from:
        stmt = stmt.where(col >= day_bounds(p.org, date_from)[0])
    if date_to:
        stmt = stmt.where(col < day_bounds(p.org, date_to)[1])
    return stmt


def r_pos_sales(db, p, warehouse_id=None, date_from=None, date_to=None, **_):
    from app.models import PosSale
    from app.services.pos import METHODS, tz_of
    tz = tz_of(p.org)
    stmt = select(PosSale).join(Location, Location.id == PosSale.location_id).where(PosSale.org_id == p.org_id)
    stmt = _pos_range(_wh_filter(stmt, p, warehouse_id), p, PosSale.sold_at, date_from, date_to)
    rows, t = [], {"sales": Decimal(0), "returns": Decimal(0), "tax": Decimal(0), "cogs": Decimal(0), "discount": Decimal(0)}
    for x in db.execute(stmt.order_by(PosSale.sold_at.desc()).limit(MAX_ROWS)).unique().scalars():
        sign = 1 if x.kind == "sale" else -1
        t["sales" if x.kind == "sale" else "returns"] += x.total
        t["tax"] += sign * x.tax
        t["cogs"] += sign * x.cost_of_goods
        if x.kind == "sale":
            t["discount"] += x.discount
        rows.append({"number": x.number, "sold_at": x.sold_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                     "kind": "Return" if x.kind == "return" else "Sale", "location": location_label(x.location),
                     "cashier": x.user.full_name if x.user else "", "customer": x.customer_name or "",
                     "payment": ", ".join(sorted({METHODS.get(py.method, py.method) for py in x.payments})),
                     "discount": money(x.discount), "tax": money(sign * x.tax), "total": money(sign * x.total),
                     "cost_of_goods": money(sign * x.cost_of_goods), "profit": money(sign * (x.total - x.tax - x.cost_of_goods)),
                     "flag": "Needs checking" if x.needs_review else ("Offline" if x.offline else ""), "pos_sale_id": x.id})
    cols = [col("number", "Receipt"), col("sold_at", "Date & time"), col("kind", "Type"), col("location", "Shop"),
            col("cashier", "Cashier"), col("customer", "Customer"), col("payment", "Paid by"), col("discount", "Discount", "money"),
            col("tax", "Tax", "money"), col("total", "Total", "money"), col("cost_of_goods", "Cost of goods", "money"),
            col("profit", "Profit", "money"), col("flag", "Note")]
    net = t["sales"] - t["returns"]
    return "Counter sales", cols, rows, {"receipts": len(rows), "sales": money(t["sales"]), "returns": money(t["returns"]),
                                         "net_sales": money(net), "discounts": money(t["discount"]),
                                         "gross_profit": money(net - t["tax"] - t["cogs"])}


def r_pos_shifts(db, p, warehouse_id=None, date_from=None, date_to=None, **_):
    from app.models import PosShift
    from app.services.pos import shift_summary, tz_of
    tz = tz_of(p.org)
    stmt = select(PosShift).join(Location, Location.id == PosShift.location_id).where(PosShift.org_id == p.org_id)
    stmt = _pos_range(_wh_filter(stmt, p, warehouse_id), p, PosShift.opened_at, date_from, date_to)
    rows, short = [], Decimal(0)
    for sh in db.execute(stmt.order_by(PosShift.opened_at.desc()).limit(5000)).unique().scalars():
        sm = shift_summary(db, sh)
        diff = money(sh.counted_cash - sh.expected_cash) if sh.counted_cash is not None and sh.expected_cash is not None else None
        if diff:
            short += diff
        rows.append({"number": sh.number, "cashier": sh.user.full_name if sh.user else "", "location": location_label(sh.location),
                     "opened_at": sh.opened_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                     "closed_at": sh.closed_at.astimezone(tz).strftime("%Y-%m-%d %H:%M") if sh.closed_at else "Still open",
                     "sales_count": sm["sales_count"], "net_sales": sm["net_sales"], "opening_float": sm["opening_float"],
                     "cash_sales": sm["cash_sales"], "cash_refunds": sm["cash_refunds"], "cash_in": sm["cash_in"],
                     "cash_out": sm["cash_out"], "expected_cash": sh.expected_cash if sh.expected_cash is not None else sm["expected_cash"],
                     "counted_cash": sh.counted_cash, "difference": diff, "note": sh.closing_note or "", "pos_shift_id": sh.id})
    cols = [col("number", "Shift"), col("cashier", "Cashier"), col("location", "Shop"), col("opened_at", "Opened"),
            col("closed_at", "Closed"), col("sales_count", "Sales", "number"), col("net_sales", "Net sales", "money"),
            col("opening_float", "Float", "money"), col("cash_sales", "Cash taken", "money"), col("cash_refunds", "Cash refunds", "money"),
            col("cash_in", "Cash in", "money"), col("cash_out", "Cash out", "money"), col("expected_cash", "Expected cash", "money"),
            col("counted_cash", "Counted", "money"), col("difference", "Over / short", "money"), col("note", "Note")]
    return "Till shifts (cash-up)", cols, rows, {"shifts": len(rows), "total_over_short": money(short)}


def r_pos_products(db, p, warehouse_id=None, date_from=None, date_to=None, category_id=None, **_):
    from app.models import PosSale, PosSaleItem
    stmt = (select(PosSaleItem, PosSale.kind, Product).join(PosSale, PosSale.id == PosSaleItem.sale_id)
            .join(Product, Product.id == PosSaleItem.product_id).join(Location, Location.id == PosSale.location_id)
            .where(PosSale.org_id == p.org_id))
    stmt = _pos_range(_wh_filter(stmt, p, warehouse_id), p, PosSale.sold_at, date_from, date_to)
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    agg: dict = {}
    for item, kind, prod in db.execute(stmt.limit(MAX_ROWS)).unique().all():
        a = agg.setdefault(prod.id, {"sku": prod.sku, "name": prod.name, "unit": prod.base_unit.abbreviation, "sold": Decimal(0),
                                     "returned": Decimal(0), "revenue": Decimal(0), "cogs": Decimal(0), "discount": Decimal(0),
                                     "product_id": prod.id})
        sign = 1 if kind == "sale" else -1
        a["sold" if kind == "sale" else "returned"] += item.quantity
        a["revenue"] += sign * (item.total - item.tax)
        a["cogs"] += sign * item.cost_of_goods
        if kind == "sale":
            a["discount"] += item.discount
    rows = []
    for a in sorted(agg.values(), key=lambda x: -x["revenue"]):
        profit = a["revenue"] - a["cogs"]
        rows.append({**a, "net_units": a["sold"] - a["returned"], "revenue": money(a["revenue"]), "cogs": money(a["cogs"]),
                     "discount": money(a["discount"]), "profit": money(profit),
                     "margin": round(float(profit / a["revenue"] * 100), 1) if a["revenue"] else None})
    cols = [col("sku", "SKU"), col("name", "Product"), col("sold", "Sold", "number"), col("returned", "Returned", "number"),
            col("net_units", "Net units", "number"), col("unit", "Unit"), col("discount", "Discounts", "money"),
            col("revenue", "Revenue (excl. tax)", "money"), col("cogs", "Cost of goods", "money"), col("profit", "Gross profit", "money"),
            col("margin", "Margin %", "number")]
    return "Counter sales by product", cols, rows, {"products": len(rows), "revenue": money(sum((r["revenue"] for r in rows), Decimal(0))),
                                                    "gross_profit": money(sum((r["profit"] for r in rows), Decimal(0)))}


REPORTS = {
    "valuation": r_valuation, "stock-on-hand": r_stock_on_hand, "low-stock": r_low_stock, "out-of-stock": r_out_of_stock,
    "expiry": r_expiry, "batches": r_batches, "serials": r_serials, "movements": r_movements, "locations": r_locations,
    "adjustments": r_adjustments, "purchases": r_purchases, "suppliers": r_suppliers, "sales": r_sales,
    "sales-by-product": r_sales_by_product, "pos-sales": r_pos_sales, "pos-shifts": r_pos_shifts, "pos-products": r_pos_products,
}
TITLES = {
    "valuation": "What your stock is worth", "stock-on-hand": "Every item, where it is", "low-stock": "What to reorder",
    "out-of-stock": "Items you have run out of", "expiry": "Batches expiring soon", "batches": "All batches / lots",
    "serials": "Every serial number and where it is", "movements": "Every stock change, who and when",
    "locations": "Stock and value per location", "adjustments": "Counts, damage, loss and write-offs",
    "purchases": "Every purchase order, what arrived and what is still to come",
    "suppliers": "Orders, value, late deliveries and delivery times per supplier",
    "sales": "Every sales order with payments, amounts owed and profit",
    "sales-by-product": "Units, revenue and profit per product",
    "pos-sales": "Every counter sale and return, with payments and profit",
    "pos-shifts": "Every till shift: cash expected, counted, over or short",
    "pos-products": "What sold at the counter: units, returns, discounts and profit per product",
}
# reports that need a permission beyond reports.view
REPORT_PERMS = {"purchases": "purchases.view", "suppliers": "suppliers.view", "sales": "sales.view", "sales-by-product": "sales.view",
                "pos-sales": "sales.view", "pos-shifts": "sales.view", "pos-products": "sales.view"}


@router.get("")
def list_reports(p: Principal = Depends(require("reports.view"))):
    return [{"key": k, "description": TITLES[k]} for k in REPORTS if k not in REPORT_PERMS or p.can(REPORT_PERMS[k])]


@router.get("/{name}")
def run_report(name: str, format: str = Query("json", pattern="^(json|csv|xlsx|pdf)$"),
               warehouse_id: uuid.UUID | None = None, category_id: uuid.UUID | None = None,
               product_id: uuid.UUID | None = None, type: list[MovementType] | None = Query(None),
               date_from: date | None = None, date_to: date | None = None, days: int = Query(90, ge=0, le=3650),
               limit: int = Query(500, ge=1, le=5000),
               p: Principal = Depends(require("reports.view")), db: Session = Depends(get_db)):
    fn = REPORTS.get(name)
    if not fn:
        raise HTTPException(404, "Unknown report")
    if name in REPORT_PERMS and not p.can(REPORT_PERMS[name]):
        raise HTTPException(403, "You don't have permission to see this report")
    title, columns, rows, summary = fn(db, p, warehouse_id=warehouse_id, category_id=category_id, product_id=product_id,
                                       type=type, date_from=date_from, date_to=date_to, days=days)
    if format == "json":
        return {"key": name, "title": title, "columns": columns, "rows": rows[:limit], "total_rows": len(rows),
                "summary": summary, "generated_at": datetime.now(timezone.utc)}
    if not p.can("reports.export"):
        raise HTTPException(403, "You don't have permission to export reports")
    subtitle = f"{p.org.name} · generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by {p.user.full_name}"
    body = export.render(format, title, columns, rows, subtitle)
    p.audit(db, "report.export", entity_type="report", entity_id=name, summary=f"Exported {title} as {format.upper()} ({len(rows)} rows)")
    db.commit()
    filename = f"stockpilot-{name}-{date.today().isoformat()}.{format}"
    return Response(content=body, media_type=export.CONTENT_TYPES[format],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
