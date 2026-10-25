import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import Principal, require, scoped_get
from app.models import Location, LocationKind, Product, StockBalance, Warehouse
from app.routers.common import location_label
from app.schemas import LocationIn, LocationOut, Message, WarehouseIn, WarehouseOut, WarehouseUpdate
from app.services.org import create_warehouse

router = APIRouter(prefix="/api", tags=["warehouses"])


def _visible(p: Principal, stmt, col):
    ids = p.user.warehouse_ids
    return stmt.where(col.in_(ids)) if ids else stmt


@router.get("/warehouses", response_model=list[WarehouseOut])
def list_warehouses(include_inactive: bool = False, p: Principal = Depends(require("locations.view")), db: Session = Depends(get_db)):
    stmt = select(Warehouse).where(Warehouse.org_id == p.org_id)
    if not include_inactive:
        stmt = stmt.where(Warehouse.is_active.is_(True))
    whs = db.execute(_visible(p, stmt, Warehouse.id).order_by(Warehouse.name)).scalars().all()
    stock = dict()
    for wid, units, value in db.execute(
        select(Location.warehouse_id, func.sum(StockBalance.quantity), func.sum(StockBalance.quantity * Product.average_cost))
        .join(Location, Location.id == StockBalance.location_id).join(Product, Product.id == StockBalance.product_id)
        .where(StockBalance.org_id == p.org_id).group_by(Location.warehouse_id)
    ):
        stock[wid] = (units, value)
    locs = dict(db.execute(select(Location.warehouse_id, func.count()).where(Location.org_id == p.org_id)
                           .group_by(Location.warehouse_id)).all())
    defaults = dict(db.execute(select(Location.warehouse_id, Location.id).where(
        Location.org_id == p.org_id, Location.kind == LocationKind.DEFAULT)).all())
    out = []
    for w in whs:
        o = WarehouseOut.model_validate(w)
        o.default_location_id = defaults.get(w.id)
        o.location_count = locs.get(w.id, 0)
        units, value = stock.get(w.id, (0, 0))
        o.stock_units, o.stock_value = Decimal(units or 0), Decimal(value or 0).quantize(Decimal("0.01"))
        out.append(o)
    return out


@router.post("/warehouses", response_model=WarehouseOut, status_code=201)
def add_warehouse(body: WarehouseIn, p: Principal = Depends(require("locations.manage")), db: Session = Depends(get_db)):
    from app.services import billing
    billing.check_limit(db, p.org_id, "locations")
    try:
        wh = create_warehouse(db, p.org_id, body.name.strip(), body.code, body.type, body.address)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That code is already used")
    p.audit(db, "warehouse.create", entity_type="warehouse", entity_id=wh.id, summary=f"Created {wh.name}", after=audit.snapshot(wh))
    db.commit()
    out = WarehouseOut.model_validate(wh)
    out.default_location_id = db.execute(select(Location.id).where(Location.warehouse_id == wh.id, Location.kind == LocationKind.DEFAULT)).scalar_one()
    out.location_count = 1
    return out


@router.put("/warehouses/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(warehouse_id: uuid.UUID, body: WarehouseUpdate, p: Principal = Depends(require("locations.manage")),
                     db: Session = Depends(get_db)):
    wh = scoped_get(db, Warehouse, warehouse_id, p.org_id, "Warehouse")
    before = audit.snapshot(wh)
    data = body.model_dump(exclude_unset=True)
    if data.get("is_active") is True and not wh.is_active:
        from app.services import billing
        billing.check_limit(db, p.org_id, "locations")
    if data.get("is_active") is False:
        has_stock = db.execute(select(func.sum(StockBalance.quantity)).join(Location, Location.id == StockBalance.location_id)
                               .where(Location.warehouse_id == wh.id)).scalar() or 0
        if has_stock != 0:
            raise HTTPException(409, "Move or write off the stock in this location before deactivating it")
    if "code" in data and data["code"]:
        data["code"] = data["code"].upper()
    for k, v in data.items():
        setattr(wh, k, v)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That code is already used")
    b, a = audit.diff(before, audit.snapshot(wh))
    if a:
        p.audit(db, "warehouse.update", entity_type="warehouse", entity_id=wh.id, summary=f"Updated {wh.name}", before=b, after=a)
    db.commit()
    return WarehouseOut.model_validate(wh)


@router.get("/warehouses/{warehouse_id}/locations", response_model=list[LocationOut])
def list_locations(warehouse_id: uuid.UUID, p: Principal = Depends(require("locations.view")), db: Session = Depends(get_db)):
    scoped_get(db, Warehouse, warehouse_id, p.org_id, "Warehouse")
    rows = db.execute(select(Location).where(Location.warehouse_id == warehouse_id).order_by(Location.kind, Location.code)).unique().scalars().all()
    return [LocationOut.model_validate(l).model_copy(update={"full_name": location_label(l)}) for l in rows]


@router.post("/warehouses/{warehouse_id}/locations", response_model=LocationOut, status_code=201)
def add_location(warehouse_id: uuid.UUID, body: LocationIn, p: Principal = Depends(require("locations.manage")),
                 db: Session = Depends(get_db)):
    wh = scoped_get(db, Warehouse, warehouse_id, p.org_id, "Warehouse")
    if body.kind == LocationKind.DEFAULT:
        raise HTTPException(422, "Every warehouse already has its Main area")
    if body.parent_id:
        parent = scoped_get(db, Location, body.parent_id, p.org_id, "Parent location")
        if parent.warehouse_id != wh.id:
            raise HTTPException(422, "Parent must be in the same warehouse")
    code = (body.code or body.name).upper().replace(" ", "-")[:40]
    loc = Location(org_id=p.org_id, warehouse_id=wh.id, parent_id=body.parent_id, name=body.name.strip(), code=code, kind=body.kind)
    db.add(loc)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "That location code already exists in this warehouse")
    p.audit(db, "location.create", entity_type="location", entity_id=loc.id, summary=f"Created {wh.name} › {loc.name}")
    db.commit()
    db.refresh(loc)
    return LocationOut.model_validate(loc).model_copy(update={"full_name": location_label(loc)})


@router.delete("/locations/{location_id}", response_model=Message)
def deactivate_location(location_id: uuid.UUID, p: Principal = Depends(require("locations.manage")), db: Session = Depends(get_db)):
    loc = scoped_get(db, Location, location_id, p.org_id, "Location")
    if loc.kind == LocationKind.DEFAULT:
        raise HTTPException(422, "The Main area cannot be removed; deactivate the warehouse instead")
    qty = db.execute(select(func.sum(StockBalance.quantity)).where(StockBalance.location_id == loc.id)).scalar() or 0
    if qty != 0:
        raise HTTPException(409, "This location still holds stock")
    loc.is_active = False
    p.audit(db, "location.deactivate", entity_type="location", entity_id=loc.id, summary=f"Deactivated {loc.name}")
    db.commit()
    return Message(message="Location deactivated")


@router.get("/locations")
def all_locations(p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    """Flat list for pickers: every active place the user may work in."""
    stmt = (select(Location).join(Warehouse, Warehouse.id == Location.warehouse_id)
            .where(Location.org_id == p.org_id, Location.is_active.is_(True), Warehouse.is_active.is_(True)))
    stmt = _visible(p, stmt, Location.warehouse_id).order_by(Warehouse.name, Location.kind, Location.code)
    return [{"id": l.id, "label": location_label(l), "warehouse_id": l.warehouse_id, "warehouse_name": l.warehouse.name,
             "warehouse_type": l.warehouse.type.value, "kind": l.kind.value, "name": l.name}
            for l in db.execute(stmt).unique().scalars()]
