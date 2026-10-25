import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import AuditLog


def _jsonable(v):
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")  # 18 and 18.0000 are the same price
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Enum):
        return v.value
    return v


SKIP = {"password_hash", "totp_secret", "created_at", "updated_at"}


def snapshot(obj) -> dict:
    """Column values of an ORM object, JSON-safe, secrets removed."""
    return {c.key: _jsonable(getattr(obj, c.key)) for c in inspect(obj).mapper.column_attrs if c.key not in SKIP}


def diff(before: dict, after: dict) -> tuple[dict, dict]:
    keys = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


def record(db: Session, *, org_id, user_id, action: str, entity_type: str | None = None, entity_id=None,
           summary: str | None = None, before: dict | None = None, after: dict | None = None,
           ip: str | None = None, user_agent: str | None = None) -> None:
    db.add(AuditLog(
        org_id=org_id, user_id=user_id, action=action, entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None, summary=summary,
        before=before, after=after, ip_address=ip, user_agent=(user_agent or "")[:255] or None,
    ))
