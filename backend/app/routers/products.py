import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import Principal, require, scoped_get
from app.models import (
    Category, MovementType, Product, ProductUnitConversion, SerialNumber, StockBalance, StockBatch, StockMovement,
    Supplier, UnitOfMeasure,
)
from app.routers.common import PageParams, apply_sort, location_label, movements_out, paginate
from app.schemas import (
    CategoryIn, CategoryOut, ConversionIn, ConversionOut, Message, Page, ProductCreate, ProductOut, ProductUpdate,
    UnitIn, UnitOut,
)
from app.services import barcodes as bc
from app.services import inventory as inv
from app.services import variants as var
from app.services.industry import product_defaults

router = APIRouter(prefix="/api", tags=["products"])


def on_hand_subquery(org_id):
    return (select(StockBalance.product_id, func.sum(StockBalance.quantity).label("qty"))
            .where(StockBalance.org_id == org_id).group_by(StockBalance.product_id).subquery())


def stock_status(qty: Decimal, p: Product) -> str:
    threshold = max(p.reorder_level or 0, p.min_stock or 0)
    if qty <= 0:
        return "out"
    if threshold and qty <= threshold:
        return "low"
    return "in_stock"


def product_out(p: Product, qty) -> ProductOut:
    out = ProductOut.model_validate(p)
    out.on_hand = Decimal(qty or 0)
    out.stock_status = stock_status(out.on_hand, p)
    return out


def _check_refs(db: Session, org_id, data: dict) -> None:
    for field, model, label in (("category_id", Category, "Category"), ("preferred_supplier_id", Supplier, "Supplier"),
                                ("base_unit_id", UnitOfMeasure, "Unit")):
        if data.get(field):
            scoped_get(db, model, data[field], org_id, label)


def _normalise_tracking(data: dict) -> None:
    # Expiry is stored per batch, so expiry tracking implies batch tracking.
    if data.get("track_expiry"):
        data["track_batches"] = True


def _has_movements(db: Session, p: Product) -> bool:
    return db.execute(select(StockMovement.id).where(StockMovement.product_id == p.id).limit(1)).first() is not None


# ------------------------------------------------------------------ products
@router.get("/products", response_model=Page[ProductOut])
def list_products(
    q: str | None = Query(None, description="Search name, SKU, barcode, brand"),
    category_id: uuid.UUID | None = None, status: str | None = Query(None, pattern="^(in_stock|low|out)$"),
    active: bool | None = True, include_variants: bool | None = Query(None, description="Default: only when searching"),
    stockable_only: bool = Query(False, description="Hide size/colour groups, which hold no stock themselves"),
    params: PageParams = Depends(),
    p: Principal = Depends(require("products.view")), db: Session = Depends(get_db),
):
    oh = on_hand_subquery(p.org_id)
    kids = (select(Product.parent_id.label("pid"), func.sum(oh.c.qty).label("qty"), func.count(Product.id).label("n"))
            .join(oh, oh.c.product_id == Product.id, isouter=True)
            .where(Product.org_id == p.org_id, Product.parent_id.is_not(None)).group_by(Product.parent_id).subquery())
    qty = func.coalesce(oh.c.qty, 0) + func.coalesce(kids.c.qty, 0)
    threshold = func.greatest(Product.reorder_level, Product.min_stock)
    stmt = (select(Product, qty.label("on_hand"), kids.c.n).outerjoin(oh, oh.c.product_id == Product.id)
            .outerjoin(kids, kids.c.pid == Product.id).where(Product.org_id == p.org_id))
    if include_variants is None:
        include_variants = bool(q) or stockable_only
    if not include_variants:
        stmt = stmt.where(Product.parent_id.is_(None))
    if stockable_only:
        stmt = stmt.where(Product.has_variants.is_(False))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like), Product.barcode == q.strip(), Product.brand.ilike(like)))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if active is not None:
        stmt = stmt.where(Product.is_active.is_(active))
    if status == "out":
        stmt = stmt.where(qty <= 0)
    elif status == "low":
        stmt = stmt.where(qty > 0, threshold > 0, qty <= threshold)
    elif status == "in_stock":
        stmt = stmt.where(qty > 0, or_(threshold == 0, qty > threshold))
    stmt = apply_sort(stmt, params, {"name": Product.name, "sku": Product.sku, "on_hand": qty,
                                     "selling_price": Product.selling_price, "created_at": Product.created_at}, Product.name)
    rows, total = paginate(db, stmt, params, scalars=False)
    items = []
    for prod, on_hand, n in rows:
        o = product_out(prod, on_hand)
        o.variant_count = n
        items.append(o)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(body: ProductCreate, p: Principal = Depends(require("products.create")), db: Session = Depends(get_db)):
    data = body.model_dump(exclude={"opening_quantity", "opening_location_id", "variant_options", "has_variants"})
    defaults = product_defaults(p.org.settings or {})
    for k, v in defaults.items():
        if data.get(k) is None:
            data[k] = v
    data["track_batches"] = bool(data["track_batches"])
    data["track_expiry"] = bool(data["track_expiry"])
    _normalise_tracking(data)
    if not data.get("base_unit_id"):
        pcs = db.execute(select(UnitOfMeasure).where(UnitOfMeasure.org_id == p.org_id, UnitOfMeasure.abbreviation == "pcs")).scalar_one_or_none()
        if not pcs:
            raise HTTPException(422, "Choose a unit of measure")
        data["base_unit_id"] = pcs.id
    _check_refs(db, p.org_id, data)
    product = Product(org_id=p.org_id, average_cost=data.get("cost_price") or 0, **data)
    db.add(product)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Another product already uses this SKU or barcode")
    p.audit(db, "product.create", entity_type="product", entity_id=product.id, summary=f"Created {product.name} ({product.sku})",
            after=audit.snapshot(product))
    if body.variant_options:
        if body.opening_quantity:
            raise HTTPException(422, "Add opening stock to each size/colour after creating them")
        try:
            created = var.add_variants(db, p.ctx, product, body.variant_options)
        except inv.InventoryError as e:
            db.rollback()
            raise HTTPException(e.status_code, e.message)
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "A generated variant SKU is already taken")
        p.audit(db, "product.variants", entity_type="product", entity_id=product.id,
                summary=f"Created {len(created)} variants of {product.name}", after={"options": product.variant_options})
        db.commit()
        out = product_out(product, 0)
        out.variant_count = len(created)
        return out
    if body.opening_quantity:
        if not p.can("inventory.receive"):
            raise HTTPException(403, "You can create the product but not add stock to it")
        if not body.opening_location_id:
            raise HTTPException(422, "Choose where the opening stock is kept")
        try:
            inv.receive(db, p.ctx, product_id=product.id, location_id=body.opening_location_id, quantity=body.opening_quantity,
                        unit_cost=product.cost_price, mtype=MovementType.OPENING_BALANCE, note="Opening stock")
        except inv.InventoryError as e:
            raise HTTPException(e.status_code, e.message)
    db.commit()
    return product_out(product, body.opening_quantity or 0)


@router.get("/products/lookup")
def lookup(code: str = Query(..., min_length=1, description="Barcode, SKU, serial or batch number"),
           p: Principal = Depends(require("products.view")), db: Session = Depends(get_db)):
    """What a scanner calls: everything about one item in one response."""
    code = code.strip()
    product = db.execute(select(Product).where(Product.org_id == p.org_id, or_(Product.barcode == code, Product.sku == code))).unique().scalar_one_or_none()
    matched_by, serial = "barcode_or_sku", None
    if not product:
        serial = db.execute(select(SerialNumber).where(SerialNumber.org_id == p.org_id, SerialNumber.serial == code)).scalars().first()
        if serial:
            product, matched_by = db.get(Product, serial.product_id), "serial"
    if not product:
        batch = db.execute(select(StockBatch).where(StockBatch.org_id == p.org_id, StockBatch.batch_number == code)).scalars().first()
        if batch:
            product, matched_by = db.get(Product, batch.product_id), "batch"
    if not product:
        raise HTTPException(404, f"Nothing found for '{code}'")
    return product_detail(db, p, product, matched_by=matched_by, serial=serial)


def product_detail(db: Session, p: Principal, product: Product, matched_by: str | None = None, serial=None) -> dict:
    balances = db.execute(select(StockBalance).where(StockBalance.org_id == p.org_id, StockBalance.product_id == product.id,
                                                     StockBalance.quantity != 0)).unique().scalars().all()
    by_loc: dict[uuid.UUID, dict] = {}
    for b in balances:
        e = by_loc.setdefault(b.location_id, {"location_id": b.location_id, "location_name": location_label(b.location),
                                              "warehouse_id": b.location.warehouse_id, "quantity": Decimal(0), "batches": []})
        e["quantity"] += b.quantity
        if b.batch:
            e["batches"].append({"batch_id": b.batch.id, "batch_number": b.batch.batch_number,
                                 "expires_on": b.batch.expires_on, "quantity": b.quantity})
    for e in by_loc.values():
        e["batches"].sort(key=lambda x: (x["expires_on"] is None, x["expires_on"] or 0))
    total = sum((e["quantity"] for e in by_loc.values()), Decimal(0))
    recent = db.execute(select(StockMovement).where(StockMovement.org_id == p.org_id, StockMovement.product_id == product.id)
                        .order_by(StockMovement.id.desc()).limit(10)).unique().scalars().all()
    conversions = db.execute(select(ProductUnitConversion).where(ProductUnitConversion.product_id == product.id)).unique().scalars().all()
    serials = []
    if product.track_serials:
        serials = [{"id": s.id, "serial": s.serial, "location_id": s.location_id} for s in db.execute(
            select(SerialNumber).where(SerialNumber.product_id == product.id, SerialNumber.status == "in_stock")
            .order_by(SerialNumber.serial).limit(500)).scalars()]
    variants, parent = [], None
    if product.has_variants:
        kids = db.execute(select(Product).where(Product.parent_id == product.id).order_by(Product.created_at, Product.sku)).unique().scalars().all()
        totals = inv.product_totals(db, p.org_id, [k.id for k in kids]) if kids else {}
        for k in kids:
            o = product_out(k, totals.get(k.id, 0))
            variants.append(o)
        total = sum((Decimal(v.on_hand) for v in variants), Decimal(0))
        recent = db.execute(select(StockMovement).where(StockMovement.org_id == p.org_id, StockMovement.product_id.in_([k.id for k in kids]))
                            .order_by(StockMovement.id.desc()).limit(10)).unique().scalars().all() if kids else []
    if product.parent_id:
        par = db.get(Product, product.parent_id)
        parent = {"id": par.id, "name": par.name, "sku": par.sku, "variant_options": par.variant_options}
    out_product = product_out(product, total)
    out_product.variant_count = len(variants) if product.has_variants else None
    return {
        "product": out_product,
        "variants": variants,
        "parent": parent,
        "matched_by": matched_by,
        "serial": {"serial": serial.serial, "status": serial.status.value} if serial else None,
        "on_hand": total,
        "locations": sorted(by_loc.values(), key=lambda x: x["location_name"]),
        "conversions": [ConversionOut.model_validate(c) for c in conversions],
        "serials_in_stock": serials,
        "recent_movements": movements_out(db, recent),
    }


@router.get("/products/{product_id}")
def get_product(product_id: uuid.UUID, p: Principal = Depends(require("products.view")), db: Session = Depends(get_db)):
    return product_detail(db, p, scoped_get(db, Product, product_id, p.org_id, "Product"))


PRICE_FIELDS = {"cost_price", "selling_price", "tax_rate"}


@router.put("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: uuid.UUID, body: ProductUpdate, p: Principal = Depends(require("products.edit")),
                   db: Session = Depends(get_db)):
    product = scoped_get(db, Product, product_id, p.org_id, "Product")
    data = body.model_dump(exclude_unset=True)
    cascade = data.pop("apply_to_variants", False)
    _check_refs(db, p.org_id, data)
    if product.has_variants and {"track_batches", "track_serials", "track_expiry"} & set(data):
        cascade = True  # tracking must match across a variant group
    if data.get("track_expiry"):
        data["track_batches"] = True
    changing_tracking = {k for k in ("track_batches", "track_serials") if k in data and data[k] != getattr(product, k)}
    if changing_tracking and _has_movements(db, product):
        raise HTTPException(409, "Batch or serial tracking can only be changed before the product has any stock history")
    before = audit.snapshot(product)
    for k, v in data.items():
        setattr(product, k, v)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Another product already uses this SKU or barcode")
    b, a = audit.diff(before, audit.snapshot(product))
    if a:
        action = "product.price_change" if PRICE_FIELDS & set(a) else "product.update"
        summary = f"Updated {product.name}"
        if product.has_variants and cascade:
            changed = {k: getattr(product, k) for k in a}
            kids = db.execute(select(Product).where(Product.parent_id == product.id)).unique().scalars().all()
            tracking = {k: changed[k] for k in ("track_batches", "track_serials", "track_expiry") if k in changed}
            if tracking and any(_has_movements(db, k) for k in kids):
                db.rollback()
                raise HTTPException(409, "Tracking can't change once any size/colour has stock history")
            for k in kids:
                for f, v in tracking.items():
                    setattr(k, f, v)
            n = var.apply_to_variants(db, product, changed)
            summary += f" and {max(n, len(kids) if tracking else 0)} variants"
            if {"reorder_level", "min_stock"} & set(a) and kids:
                inv.refresh_stock_alerts(db, p.ctx, [k.id for k in kids])
        p.audit(db, action, entity_type="product", entity_id=product.id, summary=summary, before=b, after=a)
        if {"reorder_level", "min_stock"} & set(a) and not product.has_variants:
            inv.refresh_stock_alerts(db, p.ctx, [product.id])
    db.commit()
    qty = inv.product_totals(db, p.org_id, [product.id]).get(product.id, 0)
    return product_out(product, qty)


@router.delete("/products/{product_id}", response_model=Message)
def delete_product(product_id: uuid.UUID, p: Principal = Depends(require("products.delete")), db: Session = Depends(get_db)):
    product = scoped_get(db, Product, product_id, p.org_id, "Product")
    if product.has_variants:
        kids = db.execute(select(Product).where(Product.parent_id == product.id)).unique().scalars().all()
        if any(_has_movements(db, k) for k in kids):
            product.is_active = False
            for k in kids:
                k.is_active = False
            p.audit(db, "product.deactivate", entity_type="product", entity_id=product.id,
                    summary=f"Deactivated {product.name} and its {len(kids)} variants")
            db.commit()
            return Message(message=f"{product.name} has stock history, so it and its sizes/colours were deactivated instead of deleted.")
    if _has_movements(db, product):
        product.is_active = False
        p.audit(db, "product.deactivate", entity_type="product", entity_id=product.id, summary=f"Deactivated {product.name}")
        db.commit()
        return Message(message=f"{product.name} has stock history, so it was deactivated instead of deleted.")
    snap = audit.snapshot(product)
    db.delete(product)
    p.audit(db, "product.delete", entity_type="product", entity_id=product_id, summary=f"Deleted {snap['name']}", before=snap)
    db.commit()
    return Message(message="Product deleted")


@router.get("/products/{product_id}/movements")
def product_movements(product_id: uuid.UUID, params: PageParams = Depends(),
                      p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    scoped_get(db, Product, product_id, p.org_id, "Product")
    stmt = select(StockMovement).where(StockMovement.org_id == p.org_id, StockMovement.product_id == product_id).order_by(StockMovement.id.desc())
    rows, total = paginate(db, stmt, params)
    return {"items": movements_out(db, rows), "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/products/{product_id}/batches")
def product_batches(product_id: uuid.UUID, location_id: uuid.UUID | None = None,
                    p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    scoped_get(db, Product, product_id, p.org_id, "Product")
    stmt = (select(StockBatch, func.coalesce(func.sum(StockBalance.quantity), 0))
            .outerjoin(StockBalance, StockBalance.batch_id == StockBatch.id)
            .where(StockBatch.org_id == p.org_id, StockBatch.product_id == product_id))
    if location_id:
        stmt = stmt.where(StockBalance.location_id == location_id)
    stmt = stmt.group_by(StockBatch.id).order_by(StockBatch.expires_on.asc().nulls_last(), StockBatch.batch_number)
    return [{"id": b.id, "batch_number": b.batch_number, "expires_on": b.expires_on, "manufactured_on": b.manufactured_on,
             "quantity": qty} for b, qty in db.execute(stmt).all()]


# ------------------------------------------------------------------ conversions
@router.post("/products/{product_id}/conversions", response_model=ConversionOut, status_code=201)
def add_conversion(product_id: uuid.UUID, body: ConversionIn, p: Principal = Depends(require("products.edit")),
                   db: Session = Depends(get_db)):
    product = scoped_get(db, Product, product_id, p.org_id, "Product")
    unit = scoped_get(db, UnitOfMeasure, body.unit_id, p.org_id, "Unit")
    if unit.id == product.base_unit_id:
        raise HTTPException(422, "That is already the product's base unit")
    conv = db.execute(select(ProductUnitConversion).where(ProductUnitConversion.product_id == product.id,
                                                          ProductUnitConversion.unit_id == unit.id)).unique().scalar_one_or_none()
    if conv:
        conv.factor = body.factor
    else:
        conv = ProductUnitConversion(org_id=p.org_id, product_id=product.id, unit_id=unit.id, factor=body.factor)
        db.add(conv)
    db.flush()
    p.audit(db, "product.conversion", entity_type="product", entity_id=product.id,
            summary=f"1 {unit.abbreviation} = {inv.fmt(body.factor)} {product.base_unit.abbreviation}")
    db.commit()
    db.refresh(conv)
    return conv


@router.delete("/products/{product_id}/conversions/{conversion_id}", response_model=Message)
def delete_conversion(product_id: uuid.UUID, conversion_id: uuid.UUID, p: Principal = Depends(require("products.edit")),
                      db: Session = Depends(get_db)):
    conv = scoped_get(db, ProductUnitConversion, conversion_id, p.org_id, "Conversion")
    if conv.product_id != product_id:
        raise HTTPException(404, "Conversion not found")
    db.delete(conv)
    db.commit()
    return Message(message="Conversion removed")


# ------------------------------------------------------------------ categories & units
@router.get("/categories", response_model=list[CategoryOut])
def list_categories(p: Principal = Depends(require("products.view")), db: Session = Depends(get_db)):
    return db.execute(select(Category).where(Category.org_id == p.org_id).order_by(Category.name)).scalars().all()


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryIn, p: Principal = Depends(require("products.create")), db: Session = Depends(get_db)):
    if body.parent_id:
        scoped_get(db, Category, body.parent_id, p.org_id, "Category")
    c = Category(org_id=p.org_id, name=body.name.strip(), parent_id=body.parent_id)
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A category with this name already exists")
    return c


@router.put("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: uuid.UUID, body: CategoryIn, p: Principal = Depends(require("products.edit")),
                    db: Session = Depends(get_db)):
    c = scoped_get(db, Category, category_id, p.org_id, "Category")
    c.name = body.name.strip()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A category with this name already exists")
    return c


@router.delete("/categories/{category_id}", response_model=Message)
def delete_category(category_id: uuid.UUID, p: Principal = Depends(require("products.delete")), db: Session = Depends(get_db)):
    c = scoped_get(db, Category, category_id, p.org_id, "Category")
    db.delete(c)
    db.commit()
    return Message(message="Category deleted; its products are now uncategorised")


@router.get("/units", response_model=list[UnitOut])
def list_units(p: Principal = Depends(require("products.view")), db: Session = Depends(get_db)):
    return db.execute(select(UnitOfMeasure).where(UnitOfMeasure.org_id == p.org_id).order_by(UnitOfMeasure.name)).scalars().all()


@router.post("/units", response_model=UnitOut, status_code=201)
def create_unit(body: UnitIn, p: Principal = Depends(require("products.create")), db: Session = Depends(get_db)):
    u = UnitOfMeasure(org_id=p.org_id, name=body.name.strip(), abbreviation=body.abbreviation.strip().lower(),
                      allow_decimals=body.allow_decimals)
    db.add(u)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A unit with this abbreviation already exists")
    return u


# ------------------------------------------------------------------ variants
class VariantsIn(BaseModel):
    options: dict[str, list[str]] = Field(description='e.g. {"Size": ["S", "M", "L"], "Colour": ["Red", "Blue"]}')


@router.post("/products/{product_id}/variants", status_code=201)
def add_variants(product_id: uuid.UUID, body: VariantsIn, p: Principal = Depends(require("products.create")),
                 db: Session = Depends(get_db)):
    """Create sizes/colours. Run again with new values (e.g. "XL") to add more; existing ones are kept."""
    product = scoped_get(db, Product, product_id, p.org_id, "Product")
    try:
        created = var.add_variants(db, p.ctx, product, body.options)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A generated variant SKU is already taken")
    p.audit(db, "product.variants", entity_type="product", entity_id=product.id,
            summary=f"Added {len(created)} variants to {product.name}", after={"options": product.variant_options})
    db.commit()
    return {"message": f"Added {len(created)} variant{'s' if len(created) != 1 else ''}" if created else "Those variants already exist",
            "created": [product_out(c, 0) for c in created]}


# ------------------------------------------------------------------ barcodes & labels
class BarcodesIn(BaseModel):
    product_ids: list[uuid.UUID] | None = Field(None, max_length=5000)
    all_missing: bool = False


@router.post("/products/barcodes/generate")
def generate_barcodes(body: BarcodesIn, p: Principal = Depends(require("products.edit")), db: Session = Depends(get_db)):
    """Give products without a barcode an in-store EAN-13 (starts with 2, never clashes with real brands)."""
    stmt = select(Product).where(Product.org_id == p.org_id, Product.barcode.is_(None), Product.has_variants.is_(False))
    if not body.all_missing:
        if not body.product_ids:
            raise HTTPException(422, "Choose products, or ask for all products without a barcode")
        stmt = stmt.where(Product.id.in_(body.product_ids))
    done = bc.assign_barcodes(db, p.org_id, db.execute(stmt.order_by(Product.name)).unique().scalars().all())
    if done:
        p.audit(db, "product.barcodes", entity_type="product", entity_id=None, summary=f"Generated barcodes for {len(done)} product(s)",
                after={"products": {str(x.id): x.barcode for x in done[:200]}})
    db.commit()
    return {"message": f"Generated {len(done)} barcode{'s' if len(done) != 1 else ''}" if done else "Every selected product already has a barcode",
            "products": [{"id": x.id, "name": x.name, "barcode": x.barcode} for x in done]}


class LabelItem(BaseModel):
    product_id: uuid.UUID
    copies: int = Field(1, ge=1, le=1000)


class LabelsIn(BaseModel):
    items: list[LabelItem] = Field(min_length=1, max_length=2000)
    layout: Literal["a4-3x8", "a4-2x7", "a4-4x10", "thermal-50x25"] = "a4-3x8"
    show_price: bool = True
    start_at: int = Field(1, ge=1, le=100, description="First free position on a part-used sheet")
    generate_missing: bool = True


@router.get("/labels/layouts")
def label_layouts(p: Principal = Depends(require("products.view"))):
    return [{"key": k, "title": l.title, "per_page": l.cols * l.rows} for k, l in bc.LAYOUTS.items()]


@router.post("/labels")
def print_labels(body: LabelsIn, p: Principal = Depends(require("products.view")), db: Session = Depends(get_db)):
    """A PDF of barcode labels, ready to print."""
    ids = [i.product_id for i in body.items]
    found = {x.id: x for x in db.execute(select(Product).where(Product.org_id == p.org_id, Product.id.in_(ids))).unique().scalars()}
    if len(found) != len(set(ids)):
        raise HTTPException(404, "Product not found")
    items = []
    for i in body.items:
        prod = found[i.product_id]
        if prod.has_variants:
            raise HTTPException(422, f"{prod.name} has sizes/colours; print labels for each size/colour")
        items.append((prod, i.copies))
    if body.generate_missing and p.can("products.edit"):
        if bc.assign_barcodes(db, p.org_id, [x for x, _ in items if not x.barcode]):
            db.commit()
    pdf = bc.labels_pdf(items, body.layout, show_price=body.show_price, currency=p.org.currency, start_at=body.start_at)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="stockpilot-labels.pdf"'})
