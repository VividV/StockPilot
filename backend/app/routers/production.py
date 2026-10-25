"""Bills of materials (/api/products/{id}/components) and production runs (/api/production-runs)."""
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, require
from app.models import ProductionRun, StockMovement
from app.routers.common import PageParams, location_label, movements_out, paginate
from app.services import inventory as inv
from app.services import production as svc

router = APIRouter(prefix="/api", tags=["production"])


class ComponentIn(BaseModel):
    component_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


class ComponentsIn(BaseModel):
    components: list[ComponentIn] = Field(max_length=200)


class RunIn(BaseModel):
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    components_location_id: uuid.UUID | None = None
    batch_number: str | None = Field(None, max_length=80)
    expires_on: date | None = None
    serials: list[str] | None = None
    notes: str | None = Field(None, max_length=1000)
    actual: dict[uuid.UUID, Decimal] | None = Field(None, description="Override how much of each material was really used")


def _components_out(rows):
    return [{"id": c.id, "component_id": c.component_id, "name": c.component.name, "sku": c.component.sku,
             "unit": c.component.base_unit.abbreviation, "quantity": c.quantity, "unit_cost": c.component.average_cost} for c in rows]


@router.get("/products/{product_id}/components")
def get_components(product_id: uuid.UUID, p: Principal = Depends(require("products.view")), db: Session = Depends(get_db)):
    inv.get_product(db, p.ctx, product_id)
    return _components_out(svc.components(db, p.ctx, product_id))


@router.put("/products/{product_id}/components")
def put_components(product_id: uuid.UUID, body: ComponentsIn, p: Principal = Depends(require("products.edit")), db: Session = Depends(get_db)):
    def op():
        rows = svc.set_components(db, p.ctx, product_id, [c.model_dump() for c in body.components])
        p.audit(db, "product.components", entity_type="product", entity_id=product_id,
                summary=f"Set {len(rows)} material(s)", after={"components": [{"id": str(c.component_id), "qty": str(c.quantity)} for c in rows]})
        return _components_out(rows)
    return inv.run_with_retry(db, op)


@router.get("/production/availability")
def availability(product_id: uuid.UUID, location_id: uuid.UUID, quantity: Decimal | None = Query(None, gt=0),
                 p: Principal = Depends(require("production.run")), db: Session = Depends(get_db)):
    """What making this many needs, what's in stock there, and the most you could make."""
    inv.get_product(db, p.ctx, product_id)
    inv.get_location(db, p.ctx, location_id)
    return svc.availability(db, p.ctx, product_id, location_id, quantity)


def run_out(db: Session, r: ProductionRun, movements=None) -> dict:
    from app.models import Location
    loc = db.get(Location, r.location_id)
    d = {"id": r.id, "number": r.number, "product_id": r.product_id, "product_name": r.product.name, "sku": r.product.sku,
         "unit": r.product.base_unit.abbreviation, "quantity": r.quantity, "unit_cost": r.unit_cost,
         "total_cost": (r.quantity * r.unit_cost).quantize(Decimal("0.01")), "location": location_label(loc),
         "notes": r.notes, "created_at": r.created_at}
    if movements is not None:
        d["movements"] = movements_out(db, movements)
    return d


@router.get("/production-runs")
def list_runs(product_id: uuid.UUID | None = None, params: PageParams = Depends(),
              p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    stmt = select(ProductionRun).where(ProductionRun.org_id == p.org_id)
    if product_id:
        stmt = stmt.where(ProductionRun.product_id == product_id)
    rows, total = paginate(db, stmt.order_by(ProductionRun.created_at.desc()), params)
    return {"items": [run_out(db, r) for r in rows], "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/production-runs/{run_id}")
def get_run(run_id: uuid.UUID, p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    r = svc.get_run(db, p.ctx, run_id)
    mvs = db.execute(select(StockMovement).where(StockMovement.org_id == p.org_id, StockMovement.reference_id == r.id)
                     .order_by(StockMovement.id)).unique().scalars().all()
    return run_out(db, r, mvs)


@router.post("/production-runs", status_code=201)
def create_run(body: RunIn, p: Principal = Depends(require("production.run")), db: Session = Depends(get_db)):
    def op():
        r = svc.run(db, p.ctx, product_id=body.product_id, location_id=body.location_id, quantity=body.quantity,
                    components_location_id=body.components_location_id, batch_number=body.batch_number,
                    expires_on=body.expires_on, serials=body.serials, notes=body.notes,
                    actual={str(k): v for k, v in body.actual.items()} if body.actual else None)
        p.audit(db, "production.run", entity_type="production_run", entity_id=r.id,
                summary=f"{r.number}: made {inv.fmt(r.quantity)} × {r.product.name} at {inv.fmt(r.unit_cost)} each")
        mvs = db.execute(select(StockMovement).where(StockMovement.reference_id == r.id).order_by(StockMovement.id)).unique().scalars().all()
        out = run_out(db, r, mvs)
        out["message"] = f"Recorded {r.number}: {inv.fmt(r.quantity)} {r.product.base_unit.abbreviation} of {r.product.name} made"
        return out
    return inv.run_with_retry(db, op)
