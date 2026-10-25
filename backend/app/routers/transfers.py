import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, require
from app.models import Location, StockBalance, Transfer, TransferStatus
from app.routers.common import PageParams, location_label, paginate
from app.schemas import Page, ReceiveTransferIn, TransferIn, TransferOut
from app.services import inventory as inv

router = APIRouter(prefix="/api/transfers", tags=["transfers"])


def transfer_out(db: Session, t: Transfer, with_available: bool = False) -> TransferOut:
    available: dict = {}
    if with_available and t.status in (TransferStatus.DRAFT, TransferStatus.REQUESTED, TransferStatus.APPROVED):
        for pid, bid, qty in db.execute(select(StockBalance.product_id, StockBalance.batch_id, StockBalance.quantity).where(
                StockBalance.location_id == t.from_location_id, StockBalance.product_id.in_([i.product_id for i in t.items]))):
            available[(pid, None)] = available.get((pid, None), Decimal(0)) + qty
            if bid:
                available[(pid, bid)] = qty
    return TransferOut(
        id=t.id, number=t.number, status=t.status, from_location_id=t.from_location_id, from_name=location_label(t.from_location),
        to_location_id=t.to_location_id, to_name=location_label(t.to_location), notes=t.notes, created_at=t.created_at,
        dispatched_at=t.dispatched_at, received_at=t.received_at,
        items=[{
            "id": i.id, "product_id": i.product_id, "product_name": i.product.name, "sku": i.product.sku,
            "unit": i.product.base_unit.abbreviation, "batch_id": i.batch_id, "requested_qty": i.requested_qty,
            "dispatched_qty": i.dispatched_qty, "received_qty": i.received_qty,
            "remaining_qty": i.requested_qty - i.received_qty,
            "available_qty": available.get((i.product_id, i.batch_id)) if with_available else None,
        } for i in t.items],
    )


@router.get("", response_model=Page[TransferOut])
def list_transfers(status: list[TransferStatus] | None = Query(None), q: str | None = None, params: PageParams = Depends(),
                   p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    stmt = select(Transfer).where(Transfer.org_id == p.org_id)
    if status:
        stmt = stmt.where(Transfer.status.in_(status))
    if q:
        stmt = stmt.where(Transfer.number.ilike(f"%{q.strip()}%"))
    if p.user.warehouse_ids:
        locs = select(Location.id).where(Location.warehouse_id.in_(p.user.warehouse_ids))
        stmt = stmt.where(or_(Transfer.from_location_id.in_(locs), Transfer.to_location_id.in_(locs)))
    rows, total = paginate(db, stmt.order_by(Transfer.created_at.desc()), params)
    return Page(items=[transfer_out(db, t) for t in rows], total=total, page=params.page, page_size=params.page_size)


@router.post("", response_model=TransferOut, status_code=201)
def create(body: TransferIn, p: Principal = Depends(require("inventory.transfer")), db: Session = Depends(get_db)):
    def op():
        t = inv.create_transfer(db, p.ctx, from_location_id=body.from_location_id, to_location_id=body.to_location_id,
                                items=[i.model_dump() for i in body.items], notes=body.notes, submit=body.submit)
        p.audit(db, "transfer.create", entity_type="transfer", entity_id=t.id, summary=f"Created {t.number}")
        return transfer_out(db, t, with_available=True)
    return inv.run_with_retry(db, op)


@router.get("/{transfer_id}", response_model=TransferOut)
def get(transfer_id: uuid.UUID, p: Principal = Depends(require("inventory.view")), db: Session = Depends(get_db)):
    return transfer_out(db, inv.get_transfer(db, p.ctx, transfer_id), with_available=True)


def _action(db: Session, p: Principal, transfer_id, fn, label: str) -> TransferOut:
    def op():
        t = inv.get_transfer(db, p.ctx, transfer_id, lock=True)
        before = t.status.value
        fn(t)
        p.audit(db, f"transfer.{label}", entity_type="transfer", entity_id=t.id, summary=f"{t.number}: {label}",
                before={"status": before}, after={"status": t.status.value})
        return transfer_out(db, t)
    return inv.run_with_retry(db, op)


@router.post("/{transfer_id}/submit", response_model=TransferOut)
def submit(transfer_id: uuid.UUID, p: Principal = Depends(require("inventory.transfer")), db: Session = Depends(get_db)):
    return _action(db, p, transfer_id, lambda t: inv.set_transfer_status(db, p.ctx, t, TransferStatus.REQUESTED), "submitted")


@router.post("/{transfer_id}/approve", response_model=TransferOut)
def approve(transfer_id: uuid.UUID, p: Principal = Depends(require("inventory.transfer_approve")), db: Session = Depends(get_db)):
    return _action(db, p, transfer_id, lambda t: inv.set_transfer_status(db, p.ctx, t, TransferStatus.APPROVED), "approved")


@router.post("/{transfer_id}/dispatch", response_model=TransferOut)
def dispatch(transfer_id: uuid.UUID, p: Principal = Depends(require("inventory.transfer")), db: Session = Depends(get_db)):
    return _action(db, p, transfer_id, lambda t: inv.dispatch_transfer(db, p.ctx, t), "dispatched")


@router.post("/{transfer_id}/receive", response_model=TransferOut)
def receive(transfer_id: uuid.UUID, body: ReceiveTransferIn | None = None,
            p: Principal = Depends(require("inventory.transfer")), db: Session = Depends(get_db)):
    qtys = body.quantities if body else None
    return _action(db, p, transfer_id, lambda t: inv.receive_transfer(db, p.ctx, t, qtys), "received")


@router.post("/{transfer_id}/cancel", response_model=TransferOut)
def cancel(transfer_id: uuid.UUID, p: Principal = Depends(require("inventory.transfer")), db: Session = Depends(get_db)):
    return _action(db, p, transfer_id, lambda t: inv.set_transfer_status(db, p.ctx, t, TransferStatus.CANCELLED), "cancelled")
