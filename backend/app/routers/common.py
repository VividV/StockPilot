from decimal import Decimal

from fastapi import HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MovementSerial, SerialNumber, StockMovement


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
                 sort: str | None = Query(None, description="Field name; prefix with - for descending")):
        self.page, self.page_size, self.sort = page, page_size, sort

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def apply_sort(stmt, params: PageParams, allowed: dict, default):
    if params.sort:
        key = params.sort.lstrip("-")
        col = allowed.get(key)
        if col is None:
            raise HTTPException(400, f"Cannot sort by '{key}'. Use one of: {', '.join(allowed)}")
        return stmt.order_by(col.desc() if params.sort.startswith("-") else col.asc())
    return stmt.order_by(default)


def paginate(db: Session, stmt, params: PageParams, *, scalars: bool = True):
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    res = db.execute(stmt.limit(params.page_size).offset(params.offset))
    rows = res.unique().scalars().all() if scalars else res.all()
    return rows, total


def movement_out(m: StockMovement, serials: list[str] | None = None) -> dict:
    loc = m.location
    return {
        "id": m.id, "created_at": m.created_at, "movement_type": m.movement_type, "product_id": m.product_id,
        "product_name": m.product.name, "sku": m.product.sku, "unit": m.product.base_unit.abbreviation,
        "location_id": m.location_id,
        "location_name": loc.name if loc.kind.value != "default" else loc.warehouse.name,
        "warehouse_name": loc.warehouse.name, "batch_number": m.batch.batch_number if m.batch else None,
        "quantity": m.quantity, "balance_after": m.balance_after, "unit_cost": m.unit_cost,
        "entered_quantity": m.entered_quantity, "entered_unit": m.entered_unit,
        "reference_type": m.reference_type, "reference_id": m.reference_id, "reference_no": m.reference_no,
        "counterparty": m.counterparty, "note": m.note, "user_name": m.user.full_name if m.user else None,
        "serials": serials or [],
    }


def movements_out(db: Session, movements: list[StockMovement]) -> list[dict]:
    ids = [m.id for m in movements]
    serial_map: dict[int, list[str]] = {}
    if ids:
        for mid, serial in db.execute(select(MovementSerial.movement_id, SerialNumber.serial)
                                      .join(SerialNumber, SerialNumber.id == MovementSerial.serial_id)
                                      .where(MovementSerial.movement_id.in_(ids))):
            serial_map.setdefault(mid, []).append(serial)
    return [movement_out(m, serial_map.get(m.id)) for m in movements]


def location_label(loc) -> str:
    if loc.kind.value == "default":
        return loc.warehouse.name
    return f"{loc.warehouse.name} › {loc.name}"


def money(v) -> Decimal:
    return Decimal(v or 0).quantize(Decimal("0.01"))
