"""Purchase orders and sales orders.

Both sit on top of the inventory engine: receiving a PO line calls
``inventory.receive`` and fulfilling an SO line calls ``inventory.issue``, so
all the stock rules (row locks, FEFO, serials, negative-stock setting) apply
unchanged. Quantities on order lines are stored in the product's base unit;
prices are per base unit.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Customer, MovementType, OrderStatus, PaymentStatus, Product, PurchaseOrder, PurchaseOrderItem, SalesOrder,
    SalesOrderItem, StockBalance, Supplier,
)
from app.services import inventory as inv
from app.services.inventory import Ctx, InventoryError, NotFound, fmt, q

ZERO = Decimal(0)
CENT = Decimal("0.01")
OPEN = (OrderStatus.DRAFT, OrderStatus.CONFIRMED, OrderStatus.PARTIAL)


def money(v) -> Decimal:
    return Decimal(v or 0).quantize(CENT)


def _price_per_base(entered_qty, entered_price, base_qty) -> Decimal:
    """Price typed per carton -> price per piece."""
    return q(Decimal(str(entered_price)) * Decimal(str(entered_qty)) / base_qty)


def _sellable(db: Session, ctx: Ctx, product_id) -> Product:
    p = inv.get_product(db, ctx, product_id)
    if p.has_variants:
        raise InventoryError(f"{p.name} comes in several sizes/colours. Choose the exact one.", code="variant_required")
    if not p.is_active:
        raise InventoryError(f"{p.name} is inactive", code="inactive_product")
    return p


# =========================================================================== purchase orders
def po_totals(po: PurchaseOrder) -> dict:
    subtotal = tax = received_value = outstanding_value = ZERO
    closed = po.status in (OrderStatus.COMPLETED, OrderStatus.CANCELLED)
    for i in po.items:
        line = i.ordered_qty * i.unit_cost
        subtotal += line
        tax += line * i.tax_rate / 100
        received_value += i.received_qty * i.unit_cost
        if not closed:
            outstanding_value += max(i.ordered_qty - i.received_qty, ZERO) * i.unit_cost
    return {"subtotal": money(subtotal), "tax": money(tax), "total": money(subtotal + tax),
            "received_value": money(received_value), "outstanding_value": money(outstanding_value)}


def _po_lines(db: Session, ctx: Ctx, lines: list[dict]) -> list[PurchaseOrderItem]:
    if not lines:
        raise InventoryError("Add at least one product", code="empty")
    items, seen = [], set()
    for n, ln in enumerate(lines, 1):
        p = _sellable(db, ctx, ln["product_id"])
        if p.id in seen:
            raise InventoryError(f"{p.name} is on the order twice. Combine the lines.", code="duplicate_line")
        seen.add(p.id)
        base_qty, _ = inv.to_base_qty(db, p, ln["quantity"], ln.get("unit_id"))
        cost = ln.get("unit_cost")
        unit_cost = _price_per_base(ln["quantity"], cost, base_qty) if cost is not None else p.cost_price
        tax = ln.get("tax_rate")
        items.append(PurchaseOrderItem(line_no=n, product_id=p.id, ordered_qty=base_qty, unit_cost=unit_cost,
                                       tax_rate=Decimal(str(tax)) if tax is not None else p.tax_rate))
    return items


def create_po(db: Session, ctx: Ctx, *, supplier_id, destination_location_id, lines: list[dict], expected_date=None,
              supplier_reference=None, notes=None, confirm: bool = False) -> PurchaseOrder:
    supplier = db.get(Supplier, supplier_id)
    if not supplier or supplier.org_id != ctx.org_id:
        raise NotFound("Supplier not found", code="not_found")
    inv.get_location(db, ctx, destination_location_id)
    po = PurchaseOrder(org_id=ctx.org_id, number=inv.next_number(db, ctx.org_id, "PO"), supplier_id=supplier.id,
                       destination_location_id=destination_location_id, expected_date=expected_date,
                       supplier_reference=supplier_reference, notes=notes, created_by=ctx.user_id,
                       status=OrderStatus.DRAFT)
    po.items = _po_lines(db, ctx, lines)
    db.add(po)
    db.flush()
    if confirm:
        confirm_po(db, ctx, po)
    return po


def get_po(db: Session, ctx: Ctx, po_id, *, lock: bool = False) -> PurchaseOrder:
    stmt = select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.org_id == ctx.org_id)
    if lock:
        stmt = stmt.with_for_update(of=PurchaseOrder).execution_options(populate_existing=True)
    po = db.execute(stmt).unique().scalar_one_or_none()
    if not po:
        raise NotFound("Purchase order not found", code="not_found")
    return po


def update_po(db: Session, ctx: Ctx, po: PurchaseOrder, data: dict) -> PurchaseOrder:
    if po.status not in OPEN:
        raise InventoryError("This order is closed", code="invalid_status")
    for k in ("expected_date", "supplier_reference", "notes"):
        if k in data:
            setattr(po, k, data[k])
    if "lines" in data or "supplier_id" in data or "destination_location_id" in data:
        if po.status != OrderStatus.DRAFT:
            raise InventoryError("Products, supplier and delivery location can only be changed on a draft", code="invalid_status")
        if data.get("supplier_id"):
            s = db.get(Supplier, data["supplier_id"])
            if not s or s.org_id != ctx.org_id:
                raise NotFound("Supplier not found", code="not_found")
            po.supplier_id = s.id
        if data.get("destination_location_id"):
            inv.get_location(db, ctx, data["destination_location_id"])
            po.destination_location_id = data["destination_location_id"]
        if data.get("lines") is not None:
            po.items = _po_lines(db, ctx, data["lines"])
    db.flush()
    return po


def confirm_po(db: Session, ctx: Ctx, po: PurchaseOrder) -> PurchaseOrder:
    if po.status != OrderStatus.DRAFT:
        raise InventoryError("Only a draft can be confirmed", code="invalid_status")
    po.status = OrderStatus.CONFIRMED
    po.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    return po


def receive_po(db: Session, ctx: Ctx, po: PurchaseOrder, lines: list[dict] | None, *, note: str | None = None):
    """``lines``: [{item_id, quantity, batch_number?, expires_on?, manufactured_on?, serials?}].
    ``None`` = receive everything still outstanding (not allowed for batch/serial items)."""
    if po.status not in OPEN:
        raise InventoryError("This order is closed", code="invalid_status")
    items = {i.id: i for i in po.items}
    if lines is None:
        lines = [{"item_id": i.id, "quantity": i.ordered_qty - i.received_qty} for i in po.items if i.ordered_qty > i.received_qty]
    movements = []
    for ln in lines:
        item = items.get(uuid.UUID(str(ln["item_id"])))
        if not item:
            raise NotFound("That line is not on this order", code="not_found")
        qty = q(ln.get("quantity") or 0)
        if qty <= 0:
            continue
        outstanding = item.ordered_qty - item.received_qty
        if qty > outstanding:
            raise InventoryError(f"Only {fmt(outstanding)} of {item.product.name} is still expected on {po.number}",
                                 code="over_receive")
        mvs = inv.receive(db, ctx, product_id=item.product_id, location_id=po.destination_location_id, quantity=qty,
                          unit_cost=item.unit_cost, batch_number=ln.get("batch_number"), expires_on=ln.get("expires_on"),
                          manufactured_on=ln.get("manufactured_on"), serials=ln.get("serials"),
                          supplier_id=po.supplier_id, reference_type="purchase_order", reference_id=po.id,
                          reference_no=po.number, counterparty=po.supplier.name, note=note,
                          mtype=MovementType.PURCHASE_RECEIPT)
        item.received_qty = item.received_qty + qty
        item.product.cost_price = item.unit_cost  # last purchase price becomes the default next time
        movements.extend(mvs)
    if not movements:
        raise InventoryError("Enter a quantity for at least one line", code="no_change")
    if po.status == OrderStatus.DRAFT:
        po.confirmed_at = datetime.now(timezone.utc)
    if all(i.received_qty >= i.ordered_qty for i in po.items):
        po.status = OrderStatus.COMPLETED
        po.closed_at = datetime.now(timezone.utc)
    else:
        po.status = OrderStatus.PARTIAL
    db.flush()
    return movements


def close_po(db: Session, ctx: Ctx, po: PurchaseOrder) -> PurchaseOrder:
    """Stop waiting for the rest. Nothing received -> cancelled; else completed short."""
    if po.status not in OPEN:
        raise InventoryError("This order is already closed", code="invalid_status")
    received = any(i.received_qty > 0 for i in po.items)
    po.status = OrderStatus.COMPLETED if received else OrderStatus.CANCELLED
    po.closed_at = datetime.now(timezone.utc)
    db.flush()
    return po


def suggested_reorders(db: Session, ctx: Ctx) -> list[dict]:
    """Every active product at or below its reorder level, with how many to order."""
    qty = func.coalesce(func.sum(StockBalance.quantity), 0)
    on_order = (select(PurchaseOrderItem.product_id, func.sum(PurchaseOrderItem.ordered_qty - PurchaseOrderItem.received_qty).label("q"))
                .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
                .where(PurchaseOrder.org_id == ctx.org_id, PurchaseOrder.status.in_([OrderStatus.CONFIRMED, OrderStatus.PARTIAL, OrderStatus.DRAFT]))
                .group_by(PurchaseOrderItem.product_id).subquery())
    threshold = func.greatest(Product.reorder_level, Product.min_stock)
    rows = db.execute(
        select(Product, qty, func.coalesce(on_order.c.q, 0))
        .outerjoin(StockBalance, StockBalance.product_id == Product.id)
        .outerjoin(on_order, on_order.c.product_id == Product.id)
        .where(Product.org_id == ctx.org_id, Product.is_active.is_(True), Product.has_variants.is_(False), threshold > 0)
        .group_by(Product.id, on_order.c.q)
        .having(qty + func.coalesce(on_order.c.q, 0) <= threshold)
        .order_by(Product.name)
    ).unique().all()
    out = []
    for p, on_hand, ordered in rows:
        thr = max(p.reorder_level, p.min_stock)
        suggest = p.reorder_qty if p.reorder_qty > 0 else max(thr * 2 - on_hand - ordered, Decimal(1))
        if not p.base_unit.allow_decimals:
            suggest = suggest.to_integral_value(rounding="ROUND_CEILING")
        out.append({"product": p, "on_hand": Decimal(on_hand), "on_order": Decimal(ordered), "threshold": thr, "suggest": suggest})
    return out


def pos_from_low_stock(db: Session, ctx: Ctx, destination_location_id, product_ids: list | None = None) -> tuple[list[PurchaseOrder], list[Product]]:
    """Draft one PO per preferred supplier for everything that needs reordering."""
    inv.get_location(db, ctx, destination_location_id)
    wanted = {uuid.UUID(str(x)) for x in product_ids} if product_ids else None
    by_supplier: dict[uuid.UUID, list[dict]] = defaultdict(list)
    no_supplier = []
    for s in suggested_reorders(db, ctx):
        p = s["product"]
        if wanted is not None and p.id not in wanted:
            continue
        if not p.preferred_supplier_id:
            no_supplier.append(p)
            continue
        by_supplier[p.preferred_supplier_id].append({"product_id": p.id, "quantity": s["suggest"], "unit_cost": p.cost_price})
    pos = [create_po(db, ctx, supplier_id=sid, destination_location_id=destination_location_id, lines=lines,
                     notes="Created from the low-stock list") for sid, lines in by_supplier.items()]
    return pos, no_supplier


# =========================================================================== sales orders
def so_line_amounts(i: SalesOrderItem, qty: Decimal | None = None) -> dict:
    qty = i.ordered_qty if qty is None else qty
    gross = qty * i.unit_price
    discount = gross * i.discount_percent / 100
    net = gross - discount
    tax = net * i.tax_rate / 100
    return {"gross": gross, "discount": discount, "net": net, "tax": tax, "total": net + tax}


def so_totals(so: SalesOrder) -> dict:
    t = defaultdict(lambda: ZERO)
    for i in so.items:
        for k, v in so_line_amounts(i).items():
            t[k] += v
        t["cogs"] += i.cost_of_goods
        t["fulfilled_net"] += so_line_amounts(i, i.fulfilled_qty)["net"]
    total = money(t["total"])
    return {"subtotal": money(t["gross"]), "discount": money(t["discount"]), "net": money(t["net"]), "tax": money(t["tax"]),
            "total": total, "amount_paid": money(so.amount_paid), "balance_due": money(total - so.amount_paid),
            "cost_of_goods": money(t["cogs"]), "gross_profit": money(t["fulfilled_net"] - t["cogs"])}


def _so_lines(db: Session, ctx: Ctx, lines: list[dict]) -> list[SalesOrderItem]:
    if not lines:
        raise InventoryError("Add at least one product", code="empty")
    items, seen = [], set()
    for n, ln in enumerate(lines, 1):
        p = _sellable(db, ctx, ln["product_id"])
        if p.id in seen:
            raise InventoryError(f"{p.name} is on the order twice. Combine the lines.", code="duplicate_line")
        seen.add(p.id)
        base_qty, _ = inv.to_base_qty(db, p, ln["quantity"], ln.get("unit_id"))
        price = ln.get("unit_price")
        disc = Decimal(str(ln.get("discount_percent") or 0))
        if not (0 <= disc <= 100):
            raise InventoryError("Discount must be between 0 and 100%", code="invalid_discount")
        tax = ln.get("tax_rate")
        items.append(SalesOrderItem(
            line_no=n, product_id=p.id, ordered_qty=base_qty,
            unit_price=_price_per_base(ln["quantity"], price, base_qty) if price is not None else p.selling_price,
            discount_percent=disc, tax_rate=Decimal(str(tax)) if tax is not None else p.tax_rate))
    return items


def create_so(db: Session, ctx: Ctx, *, source_location_id, lines: list[dict], customer_id=None, customer_reference=None,
              notes=None, confirm: bool = False) -> SalesOrder:
    if customer_id:
        c = db.get(Customer, customer_id)
        if not c or c.org_id != ctx.org_id:
            raise NotFound("Customer not found", code="not_found")
    inv.get_location(db, ctx, source_location_id)
    so = SalesOrder(org_id=ctx.org_id, number=inv.next_number(db, ctx.org_id, "SO"), customer_id=customer_id,
                    source_location_id=source_location_id, customer_reference=customer_reference, notes=notes,
                    created_by=ctx.user_id, status=OrderStatus.DRAFT, amount_paid=0)
    so.items = _so_lines(db, ctx, lines)
    db.add(so)
    db.flush()
    if confirm:
        confirm_so(db, ctx, so)
    return so


def get_so(db: Session, ctx: Ctx, so_id, *, lock: bool = False) -> SalesOrder:
    stmt = select(SalesOrder).where(SalesOrder.id == so_id, SalesOrder.org_id == ctx.org_id)
    if lock:
        stmt = stmt.with_for_update(of=SalesOrder).execution_options(populate_existing=True)
    so = db.execute(stmt).unique().scalar_one_or_none()
    if not so:
        raise NotFound("Sales order not found", code="not_found")
    return so


def update_so(db: Session, ctx: Ctx, so: SalesOrder, data: dict) -> SalesOrder:
    if so.status not in OPEN:
        raise InventoryError("This order is closed", code="invalid_status")
    for k in ("customer_reference", "notes"):
        if k in data:
            setattr(so, k, data[k])
    if any(k in data for k in ("lines", "customer_id", "source_location_id")):
        if so.status != OrderStatus.DRAFT:
            raise InventoryError("Products, customer and location can only be changed on a draft", code="invalid_status")
        if "customer_id" in data:
            if data["customer_id"]:
                c = db.get(Customer, data["customer_id"])
                if not c or c.org_id != ctx.org_id:
                    raise NotFound("Customer not found", code="not_found")
            so.customer_id = data["customer_id"]
        if data.get("source_location_id"):
            inv.get_location(db, ctx, data["source_location_id"])
            so.source_location_id = data["source_location_id"]
        if data.get("lines") is not None:
            so.items = _so_lines(db, ctx, data["lines"])
    db.flush()
    _refresh_payment_status(so)
    return so


def confirm_so(db: Session, ctx: Ctx, so: SalesOrder) -> SalesOrder:
    if so.status != OrderStatus.DRAFT:
        raise InventoryError("Only a draft can be confirmed", code="invalid_status")
    so.status = OrderStatus.CONFIRMED
    so.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    return so


def fulfil_so(db: Session, ctx: Ctx, so: SalesOrder, lines: list[dict] | None, *, note: str | None = None):
    """Ship goods. ``lines``: [{item_id, quantity, batch_id?, serials?}]; ``None`` = ship everything left
    (batches picked earliest-expiry-first; not possible for serial items, which need the serials named)."""
    if so.status not in OPEN:
        raise InventoryError("This order is closed", code="invalid_status")
    items = {i.id: i for i in so.items}
    if lines is None:
        lines = [{"item_id": i.id, "quantity": i.ordered_qty - i.fulfilled_qty} for i in so.items if i.ordered_qty > i.fulfilled_qty]
    customer = so.customer.name if so.customer else None
    movements = []
    for ln in lines:
        item = items.get(uuid.UUID(str(ln["item_id"])))
        if not item:
            raise NotFound("That line is not on this order", code="not_found")
        qty = q(ln.get("quantity") or 0)
        if ln.get("serials"):
            qty = Decimal(len(ln["serials"]))
        if qty <= 0:
            continue
        remaining = item.ordered_qty - item.fulfilled_qty
        if qty > remaining:
            raise InventoryError(f"Only {fmt(remaining)} of {item.product.name} is left to send on {so.number}",
                                 code="over_fulfil")
        mvs = inv.issue(db, ctx, product_id=item.product_id, location_id=so.source_location_id, quantity=qty,
                        batch_id=ln.get("batch_id"), serials=ln.get("serials"), mtype=MovementType.SALE,
                        reference_type="sales_order", reference_id=so.id, reference_no=so.number,
                        counterparty=customer, note=note)
        item.fulfilled_qty = item.fulfilled_qty + qty
        item.cost_of_goods = item.cost_of_goods + sum((-m.quantity * (m.unit_cost or ZERO) for m in mvs), ZERO)
        movements.extend(mvs)
    if not movements:
        raise InventoryError("Enter a quantity for at least one line", code="no_change")
    if so.status == OrderStatus.DRAFT:
        so.confirmed_at = datetime.now(timezone.utc)
    if all(i.fulfilled_qty >= i.ordered_qty for i in so.items):
        so.status = OrderStatus.COMPLETED
        so.closed_at = datetime.now(timezone.utc)
    else:
        so.status = OrderStatus.PARTIAL
    db.flush()
    return movements


def _refresh_payment_status(so: SalesOrder) -> None:
    total = so_totals(so)["total"]
    paid = money(so.amount_paid)
    so.payment_status = PaymentStatus.UNPAID if paid <= 0 else PaymentStatus.PAID if paid >= total else PaymentStatus.PARTIAL


def record_payment(db: Session, ctx: Ctx, so: SalesOrder, amount: Decimal | None) -> SalesOrder:
    """``amount`` None = pay the remaining balance. Negative = refund."""
    if so.status == OrderStatus.CANCELLED:
        raise InventoryError("This order was cancelled", code="invalid_status")
    t = so_totals(so)
    amount = t["balance_due"] if amount is None else money(amount)
    if amount == 0:
        raise InventoryError("Nothing to record", code="no_change")
    new_paid = money(so.amount_paid) + amount
    if new_paid < 0:
        raise InventoryError("Refund is more than was paid", code="invalid_amount")
    so.amount_paid = new_paid
    _refresh_payment_status(so)
    db.flush()
    return so


def close_so(db: Session, ctx: Ctx, so: SalesOrder) -> SalesOrder:
    if so.status not in OPEN:
        raise InventoryError("This order is already closed", code="invalid_status")
    shipped = any(i.fulfilled_qty > 0 for i in so.items)
    so.status = OrderStatus.COMPLETED if shipped else OrderStatus.CANCELLED
    so.closed_at = datetime.now(timezone.utc)
    db.flush()
    return so


def overdue_pos(db: Session, org_id) -> list[PurchaseOrder]:
    return db.execute(select(PurchaseOrder).where(
        PurchaseOrder.org_id == org_id, PurchaseOrder.status.in_([OrderStatus.CONFIRMED, OrderStatus.PARTIAL]),
        PurchaseOrder.expected_date < date.today())).unique().scalars().all()
