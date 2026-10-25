"""Product variants (size, colour, …).

A parent product holds the shared details and the option lists. Each
combination is a normal product row (parent_id set) with its own SKU, barcode,
price and stock, so receiving, selling, transfers, batches and reports all work
on variants without special cases. The engine refuses stock on a parent.
"""
from __future__ import annotations

import itertools
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, StockBalance, StockMovement
from app.services.inventory import Ctx, InventoryError

MAX_OPTIONS = 3
MAX_VALUES = 30
MAX_COMBINATIONS = 300
COPY_FIELDS = ("category_id", "brand", "base_unit_id", "cost_price", "selling_price", "average_cost", "tax_rate",
               "min_stock", "reorder_level", "reorder_qty", "preferred_supplier_id", "track_batches", "track_expiry",
               "track_serials", "image_url", "description")
SHARED_FIELDS = ("category_id", "brand", "cost_price", "selling_price", "tax_rate", "min_stock", "reorder_level",
                 "reorder_qty", "preferred_supplier_id")


def clean_options(options: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, values in (options or {}).items():
        name = str(name).strip()[:40]
        vals = []
        for v in values or []:
            v = str(v).strip()[:40]
            if v and v.lower() not in {x.lower() for x in vals}:
                vals.append(v)
        if name and vals:
            if name.lower() in {k.lower() for k in out}:
                raise InventoryError(f"Option '{name}' is listed twice", code="invalid_options")
            if len(vals) > MAX_VALUES:
                raise InventoryError(f"Up to {MAX_VALUES} values per option", code="invalid_options")
            out[name] = vals
    if not out:
        raise InventoryError("Add at least one option with values, e.g. Size: S, M, L", code="invalid_options")
    if len(out) > MAX_OPTIONS:
        raise InventoryError(f"Up to {MAX_OPTIONS} options (e.g. Size, Colour, Material)", code="invalid_options")
    combos = 1
    for v in out.values():
        combos *= len(v)
    if combos > MAX_COMBINATIONS:
        raise InventoryError(f"That makes {combos} variants; the limit is {MAX_COMBINATIONS}", code="invalid_options")
    return out


def _code(v: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", v.upper())[:6] or "X"


def _unique_sku(db: Session, org_id, base: str) -> str:
    sku, n = base[:64], 2
    while db.execute(select(Product.id).where(Product.org_id == org_id, Product.sku == sku)).first():
        suffix = f"-{n}"
        sku = base[:64 - len(suffix)] + suffix
        n += 1
    return sku


def has_stock_history(db: Session, product: Product) -> bool:
    return bool(db.execute(select(StockMovement.id).where(StockMovement.product_id == product.id).limit(1)).first()
                or db.execute(select(StockBalance.id).where(StockBalance.product_id == product.id, StockBalance.quantity != 0).limit(1)).first())


def add_variants(db: Session, ctx: Ctx, parent: Product, options: dict) -> list[Product]:
    """Turn ``parent`` into a variant group (if it isn't one yet) and create every
    missing combination. Existing variants are kept; new option values add more."""
    if parent.parent_id:
        raise InventoryError("This is already a variant. Add options on its main product.", code="invalid_options")
    options = clean_options(options)
    if not parent.has_variants:
        if has_stock_history(db, parent):
            raise InventoryError(f"{parent.name} already has stock. Create a new product for the sized/coloured version, "
                                 "or adjust its stock to zero first.", code="has_stock")
        parent.has_variants = True
        parent.variant_options = {}
    # merge with existing option lists (order kept, new values appended)
    merged = {k: list(v) for k, v in (parent.variant_options or {}).items()}
    for name, vals in options.items():
        key = next((k for k in merged if k.lower() == name.lower()), name)
        merged.setdefault(key, [])
        for v in vals:
            if v.lower() not in {x.lower() for x in merged[key]}:
                merged[key].append(v)
    if set(merged) != set(parent.variant_options or {}) and parent.variant_options:
        missing = set(parent.variant_options) ^ set(merged)
        raise InventoryError(f"Options can't be added or removed once variants exist ({', '.join(missing)}). "
                             "You can add new values to the existing options.", code="invalid_options")
    clean_options(merged)  # re-check limits on the merged result
    parent.variant_options = merged

    existing = db.execute(select(Product).where(Product.parent_id == parent.id)).unique().scalars().all()
    have = {tuple(sorted((k.lower(), str(v).lower()) for k, v in (c.variant_attributes or {}).items())) for c in existing}
    names = list(merged)
    created = []
    for combo in itertools.product(*(merged[n] for n in names)):
        attrs = dict(zip(names, combo))
        key = tuple(sorted((k.lower(), v.lower()) for k, v in attrs.items()))
        if key in have:
            continue
        child = Product(org_id=ctx.org_id, parent_id=parent.id, has_variants=False, variant_attributes=attrs, is_active=True,
                        variant_options={}, sku=_unique_sku(db, ctx.org_id, "-".join([parent.sku] + [_code(v) for v in combo])),
                        name=f"{parent.name} — {' / '.join(combo)}")
        for f in COPY_FIELDS:
            setattr(child, f, getattr(parent, f))
        db.add(child)
        db.flush()
        created.append(child)
    db.flush()
    return created


def apply_to_variants(db: Session, parent: Product, changed: dict) -> int:
    """Copy shared fields (price, category, reorder level…) from parent to all variants."""
    fields = {k: v for k, v in changed.items() if k in SHARED_FIELDS}
    if not fields:
        return 0
    children = db.execute(select(Product).where(Product.parent_id == parent.id)).unique().scalars().all()
    for c in children:
        for k, v in fields.items():
            setattr(c, k, v)
    db.flush()
    return len(children)
