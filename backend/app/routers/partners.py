"""Suppliers and customers share one shape, so one factory builds both routers."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import Principal, require, scoped_get
from app.models import Customer, Supplier
from app.routers.common import PageParams, apply_sort, paginate
from app.schemas import Page, PartnerIn, PartnerOut, PartnerUpdate


def build(model, path: str, label: str, perm: str) -> APIRouter:
    r = APIRouter(prefix=f"/api/{path}", tags=[path])
    entity = label.lower()

    @r.get("", response_model=Page[PartnerOut], name=f"list_{path}")
    def list_(q: str | None = Query(None), active: bool | None = True, params: PageParams = Depends(),
              p: Principal = Depends(require(f"{perm}.view")), db: Session = Depends(get_db)):
        stmt = select(model).where(model.org_id == p.org_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(model.name.ilike(like), model.email.ilike(like), model.phone.ilike(like), model.contact_name.ilike(like)))
        if active is not None:
            stmt = stmt.where(model.is_active.is_(active))
        stmt = apply_sort(stmt, params, {"name": model.name, "created_at": model.created_at}, model.name)
        rows, total = paginate(db, stmt, params)
        return Page(items=rows, total=total, page=params.page, page_size=params.page_size)

    @r.post("", response_model=PartnerOut, status_code=201, name=f"create_{path}")
    def create(body: PartnerIn, p: Principal = Depends(require(f"{perm}.manage")), db: Session = Depends(get_db)):
        obj = model(org_id=p.org_id, **body.model_dump())
        db.add(obj)
        db.flush()
        p.audit(db, f"{entity}.create", entity_type=entity, entity_id=obj.id, summary=f"Created {label.lower()} {obj.name}",
                after=audit.snapshot(obj))
        db.commit()
        return obj

    @r.get("/{obj_id}", response_model=PartnerOut, name=f"get_{path}")
    def get(obj_id: uuid.UUID, p: Principal = Depends(require(f"{perm}.view")), db: Session = Depends(get_db)):
        return scoped_get(db, model, obj_id, p.org_id, label)

    @r.put("/{obj_id}", response_model=PartnerOut, name=f"update_{path}")
    def update(obj_id: uuid.UUID, body: PartnerUpdate, p: Principal = Depends(require(f"{perm}.manage")), db: Session = Depends(get_db)):
        obj = scoped_get(db, model, obj_id, p.org_id, label)
        before = audit.snapshot(obj)
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(obj, k, v)
        db.flush()
        b, a = audit.diff(before, audit.snapshot(obj))
        if a:
            p.audit(db, f"{entity}.update", entity_type=entity, entity_id=obj.id, summary=f"Updated {obj.name}", before=b, after=a)
        db.commit()
        return obj

    return r


suppliers = build(Supplier, "suppliers", "Supplier", "suppliers")
customers = build(Customer, "customers", "Customer", "customers")
