"""Scheduled checks that raise (and clear) in-app alerts which no single
stock movement triggers: expiry dates passing, purchase orders running late,
transfers waiting for approval or stuck on the road."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Notification, OrderStatus, PurchaseOrder, Transfer, TransferStatus
from app.services.inventory import _upsert_alert, refresh_expiry_alerts

TRANSFER_LATE_DAYS = 2


def _resolve_missing(db: Session, org_id, type_: str, keep: set[str]) -> None:
    stmt = update(Notification).where(Notification.org_id == org_id, Notification.type == type_,
                                      Notification.is_resolved.is_(False))
    if keep:
        stmt = stmt.where(Notification.entity_id.not_in(keep))
    db.execute(stmt.values(is_resolved=True))


def check_org(db: Session, org_id) -> None:
    refresh_expiry_alerts(db, org_id, warn_days=30)

    today = date.today()
    late = db.execute(select(PurchaseOrder).where(
        PurchaseOrder.org_id == org_id, PurchaseOrder.status.in_([OrderStatus.CONFIRMED, OrderStatus.PARTIAL]),
        PurchaseOrder.expected_date < today)).unique().scalars().all()
    for po in late:
        days = (today - po.expected_date).days
        _upsert_alert(db, org_id, type_="po_overdue", severity="warning",
                      title=f"{po.number} from {po.supplier.name} is {days} day{'s' if days != 1 else ''} late",
                      body=f"It was expected on {po.expected_date.isoformat()}. Chase the supplier or update the date.",
                      entity_type="purchase_order", entity_id=str(po.id))
    _resolve_missing(db, org_id, "po_overdue", {str(p.id) for p in late})

    waiting = db.execute(select(Transfer).where(Transfer.org_id == org_id, Transfer.status == TransferStatus.REQUESTED)).unique().scalars().all()
    for t in waiting:
        _upsert_alert(db, org_id, type_="transfer_pending", severity="info",
                      title=f"{t.number} is waiting for approval",
                      body=f"{len(t.items)} product(s) from {t.from_location.warehouse.name} to {t.to_location.warehouse.name}.",
                      entity_type="transfer", entity_id=str(t.id))
    _resolve_missing(db, org_id, "transfer_pending", {str(t.id) for t in waiting})

    cutoff = datetime.now(timezone.utc) - timedelta(days=TRANSFER_LATE_DAYS)
    stuck = db.execute(select(Transfer).where(Transfer.org_id == org_id, Transfer.status == TransferStatus.IN_TRANSIT,
                                              Transfer.dispatched_at < cutoff)).unique().scalars().all()
    for t in stuck:
        _upsert_alert(db, org_id, type_="transfer_late", severity="warning",
                      title=f"{t.number} has been on the way for more than {TRANSFER_LATE_DAYS} days",
                      body=f"Sent from {t.from_location.warehouse.name} to {t.to_location.warehouse.name}. Receive it, or check where it is.",
                      entity_type="transfer", entity_id=str(t.id))
    _resolve_missing(db, org_id, "transfer_late", {str(t.id) for t in stuck})
