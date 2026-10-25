"""Bills of materials and production runs.

A production run uses up the components (``production_consumption``
movements) and adds the finished product (``production_output``) in one
transaction. The finished product's cost is the actual cost of what was used,
so valuation stays right.
"""
from __future__ import annotations

import uuid
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MovementType, Product, ProductComponent, ProductionRun, StockBalance, StockBatch
from app.services import inventory as inv
from app.services.inventory import Ctx, InventoryError, NotFound, fmt, q

ZERO = Decimal(0)


def components(db: Session, ctx: Ctx, product_id) -> list[ProductComponent]:
    return db.execute(select(ProductComponent).join(Product, Product.id == ProductComponent.component_id)
                      .where(ProductComponent.org_id == ctx.org_id, ProductComponent.product_id == product_id)
                      .order_by(Product.name, Product.sku)).unique().scalars().all()


def _uses(db: Session, product_id, target, seen=None) -> bool:
    """Does ``product_id`` (directly or deeper) use ``target``?"""
    seen = seen or set()
    if product_id in seen:
        return False
    seen.add(product_id)
    for (cid,) in db.execute(select(ProductComponent.component_id).where(ProductComponent.product_id == product_id)):
        if cid == target or _uses(db, cid, target, seen):
            return True
    return False


def set_components(db: Session, ctx: Ctx, product_id, lines: list[dict]) -> list[ProductComponent]:
    product = inv.get_product(db, ctx, product_id)
    if product.has_variants:
        raise InventoryError("Set materials on each size/colour, not on the group", code="variant_required")
    seen = set()
    rows = []
    for ln in lines:
        comp = inv.get_product(db, ctx, ln["component_id"])
        if comp.id == product.id:
            raise InventoryError("A product can't be made from itself", code="bom_cycle")
        if comp.has_variants:
            raise InventoryError(f"{comp.name} has sizes/colours; choose the exact one", code="variant_required")
        if comp.id in seen:
            raise InventoryError(f"{comp.name} is listed twice", code="duplicate_line")
        if _uses(db, comp.id, product.id):
            raise InventoryError(f"{comp.name} is itself made from {product.name}", code="bom_cycle")
        seen.add(comp.id)
        qty = q(ln["quantity"])
        if qty <= 0:
            raise InventoryError("Quantities must be more than 0", code="invalid_quantity")
        rows.append(ProductComponent(org_id=ctx.org_id, product_id=product.id, component_id=comp.id, quantity=qty))
    for old in components(db, ctx, product.id):
        db.delete(old)
    db.flush()
    for r in rows:
        db.add(r)
    db.flush()
    return components(db, ctx, product.id)


def availability(db: Session, ctx: Ctx, product_id, location_id, quantity: Decimal | None = None) -> dict:
    """What making ``quantity`` needs, what's there, and how many could be made."""
    comps = components(db, ctx, product_id)
    ids = [c.component_id for c in comps]
    expired_batches = select(StockBatch.id).where(StockBatch.expires_on.is_not(None), StockBatch.expires_on < func.current_date())
    have = dict(db.execute(select(StockBalance.product_id, func.sum(StockBalance.quantity)).where(
        StockBalance.org_id == ctx.org_id, StockBalance.location_id == location_id, StockBalance.product_id.in_(ids),
        StockBalance.quantity > 0, (StockBalance.batch_id.is_(None)) | (StockBalance.batch_id.not_in(expired_batches)))
        .group_by(StockBalance.product_id)).all()) if ids else {}
    lines, max_make = [], None
    for c in comps:
        avail = Decimal(have.get(c.component_id, 0))
        can = (avail / c.quantity).to_integral_value(rounding=ROUND_FLOOR) if c.quantity else ZERO
        max_make = can if max_make is None else min(max_make, can)
        need = c.quantity * quantity if quantity else None
        lines.append({"component_id": c.component_id, "name": c.component.name, "sku": c.component.sku,
                      "unit": c.component.base_unit.abbreviation, "per_unit": c.quantity, "needed": need, "available": avail,
                      "short": max(need - avail, ZERO) if need is not None else None, "unit_cost": c.component.average_cost})
    return {"lines": lines, "max_quantity": max_make if max_make is not None else ZERO,
            "estimated_unit_cost": sum((c.quantity * c.component.average_cost for c in comps), ZERO)}


def run(db: Session, ctx: Ctx, *, product_id, location_id, quantity, components_location_id=None, batch_number=None,
        expires_on=None, serials=None, notes=None, actual: dict | None = None) -> ProductionRun:
    """Record making ``quantity`` of a product. ``actual`` (component id -> qty)
    overrides the bill of materials when more or less was really used."""
    if not ctx.org_settings.get("modules", {}).get("production"):
        raise InventoryError("Production is switched off. Turn it on in Settings → Business → Features.", code="module_off")
    product = inv.get_product(db, ctx, product_id)
    out_loc = inv.get_location(db, ctx, location_id)
    src_loc = inv.get_location(db, ctx, components_location_id or location_id)
    qty, _ = inv.to_base_qty(db, product, quantity, None)
    comps = components(db, ctx, product.id)
    if not comps and not actual:
        raise InventoryError(f"Add the materials {product.name} is made from first", code="no_bom")
    use: dict[uuid.UUID, Decimal] = {c.component_id: q(c.quantity * qty) for c in comps}
    for k, v in (actual or {}).items():
        use[uuid.UUID(str(k))] = q(v)
    pr = ProductionRun(org_id=ctx.org_id, number=inv.next_number(db, ctx.org_id, "MO"), product_id=product.id,
                       location_id=out_loc.id, quantity=qty, notes=notes, created_by=ctx.user_id)
    db.add(pr)
    db.flush()
    cost = ZERO
    for cid, amount in use.items():
        if amount <= 0:
            continue
        mvs = inv.issue(db, ctx, product_id=cid, location_id=src_loc.id, quantity=amount, mtype=MovementType.PRODUCTION_CONSUMPTION,
                        reference_type="production", reference_id=pr.id, reference_no=pr.number,
                        counterparty=f"Making {fmt(qty)} × {product.name}", note=notes)
        cost += sum((-m.quantity * (m.unit_cost or ZERO) for m in mvs), ZERO)
    unit_cost = q(cost / qty)
    mvs = inv.receive(db, ctx, product_id=product.id, location_id=out_loc.id, quantity=qty, unit_cost=unit_cost,
                      batch_number=batch_number or (pr.number if product.track_batches else None), expires_on=expires_on,
                      serials=serials, mtype=MovementType.PRODUCTION_OUTPUT, reference_type="production",
                      reference_id=pr.id, reference_no=pr.number, note=notes)
    pr.unit_cost = unit_cost
    pr.batch_id = mvs[0].batch_id
    db.flush()
    return pr


def get_run(db: Session, ctx: Ctx, run_id) -> ProductionRun:
    r = db.execute(select(ProductionRun).where(ProductionRun.id == run_id, ProductionRun.org_id == ctx.org_id)).unique().scalar_one_or_none()
    if not r:
        raise NotFound("Production run not found", code="not_found")
    return r
