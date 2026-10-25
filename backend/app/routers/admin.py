"""Users, roles, organisation settings and the audit log."""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import Principal, current_principal, require, scoped_get
from app.models import AuditLog, Permission, Role, RolePermission, User, UserSession, UserWarehouse, Warehouse
from app.permissions import PERMISSIONS
from app.routers.common import PageParams, paginate
from app.schemas import AuditOut, Message, OrgOut, Page, RoleIn, RoleOut, SettingsIn, UserCreate, UserOut, UserUpdate
from app.security import hash_password, validate_password_strength
from app.services.industry import BASE_SETTINGS

router = APIRouter(prefix="/api", tags=["administration"])


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, full_name=u.full_name, role_id=u.role_id, role=u.role.name, is_active=u.is_active,
                   email_verified=u.email_verified, totp_enabled=u.totp_enabled,
                   warehouse_ids=sorted(u.warehouse_ids, key=str), last_login_at=u.last_login_at, created_at=u.created_at)


def _owner_count(db: Session, org_id) -> int:
    return db.execute(select(func.count()).select_from(User).join(Role, Role.id == User.role_id).where(
        User.org_id == org_id, Role.name == "Owner", Role.is_system.is_(True), User.is_active.is_(True))).scalar_one()


def _check_role_assignment(p: Principal, role: Role) -> None:
    # Nobody can hand out permissions they don't hold themselves.
    extra = role.permission_codes - p.permissions
    if extra:
        raise HTTPException(403, "You cannot assign a role with more access than your own")


def _set_warehouses(db: Session, p: Principal, user: User, ids: list[uuid.UUID]) -> None:
    for wid in ids:
        scoped_get(db, Warehouse, wid, p.org_id, "Warehouse")
    user.warehouse_links = [UserWarehouse(warehouse_id=w) for w in dict.fromkeys(ids)]


# ------------------------------------------------------------------ users
@router.get("/users", response_model=Page[UserOut])
def list_users(q: str | None = None, params: PageParams = Depends(), p: Principal = Depends(require("users.manage")),
               db: Session = Depends(get_db)):
    stmt = select(User).where(User.org_id == p.org_id)
    if q:
        stmt = stmt.where(or_(User.full_name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    rows, total = paginate(db, stmt.order_by(User.full_name), params)
    return Page(items=[user_out(u) for u in rows], total=total, page=params.page, page_size=params.page_size)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, p: Principal = Depends(require("users.manage")), db: Session = Depends(get_db)):
    try:
        validate_password_strength(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    role = scoped_get(db, Role, body.role_id, p.org_id, "Role")
    _check_role_assignment(p, role)
    from app.services import billing
    billing.check_limit(db, p.org_id, "users")
    user = User(org_id=p.org_id, email=body.email.lower(), full_name=body.full_name.strip(),
                password_hash=hash_password(body.password), role_id=role.id)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A user with this email already exists")
    _set_warehouses(db, p, user, body.warehouse_ids)
    db.flush()
    p.audit(db, "user.create", entity_type="user", entity_id=user.id, summary=f"Added {user.full_name} as {role.name}",
            after={"email": user.email, "role": role.name, "warehouse_ids": [str(w) for w in body.warehouse_ids]})
    from app.services.account import send_verification
    send_verification(db, user, invited_by=p.user.full_name)
    db.commit()
    db.refresh(user)
    return user_out(user)


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, p: Principal = Depends(require("users.manage")), db: Session = Depends(get_db)):
    user = scoped_get(db, User, user_id, p.org_id, "User")
    before = {"full_name": user.full_name, "role": user.role.name, "is_active": user.is_active,
              "warehouse_ids": sorted(str(w) for w in user.warehouse_ids)}
    _check_role_assignment(p, user.role)  # can't edit someone more powerful than you
    was_owner = user.role.name == "Owner" and user.role.is_system and user.is_active
    if body.role_id and body.role_id != user.role_id:
        role = scoped_get(db, Role, body.role_id, p.org_id, "Role")
        _check_role_assignment(p, role)
        user.role_id = role.id
        user.role = role
    if body.full_name is not None:
        user.full_name = body.full_name.strip()
    if body.is_active is not None:
        if user.id == p.user.id and not body.is_active:
            raise HTTPException(422, "You cannot deactivate yourself")
        if body.is_active and not user.is_active:
            from app.services import billing
            billing.check_limit(db, p.org_id, "users")
        user.is_active = body.is_active
    if body.warehouse_ids is not None:
        _set_warehouses(db, p, user, body.warehouse_ids)
    if body.password:
        try:
            validate_password_strength(body.password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        user.password_hash = hash_password(body.password)
    if body.reset_two_factor and user.totp_enabled:
        from app.services.account import disable_totp
        disable_totp(user)
    db.flush()
    if was_owner and _owner_count(db, p.org_id) == 0:
        db.rollback()
        raise HTTPException(422, "The organisation must keep at least one active Owner")
    if not user.is_active or body.password or body.reset_two_factor:
        for s in db.execute(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))).scalars():
            s.revoked_at = datetime.now(timezone.utc)
    after = {"full_name": user.full_name, "role": user.role.name, "is_active": user.is_active,
             "warehouse_ids": sorted(str(w) for w in user.warehouse_ids)}
    b, a = audit.diff(before, after)
    if body.password:
        a["password"] = "reset"
    if body.reset_two_factor:
        a["two_factor"] = "reset"
    if a:
        action = "user.permission_change" if ("role" in a or "warehouse_ids" in a) else "user.update"
        p.audit(db, action, entity_type="user", entity_id=user.id, summary=f"Updated {user.full_name}", before=b, after=a)
    db.commit()
    return user_out(user)


# ------------------------------------------------------------------ roles
def role_out(db: Session, r: Role) -> RoleOut:
    n = db.execute(select(func.count()).where(User.role_id == r.id)).scalar_one()
    return RoleOut(id=r.id, name=r.name, description=r.description, is_system=r.is_system,
                   permissions=sorted(r.permission_codes), user_count=n)


@router.get("/roles", response_model=list[RoleOut])
def list_roles(p: Principal = Depends(require("users.manage")), db: Session = Depends(get_db)):
    return [role_out(db, r) for r in db.execute(select(Role).where(Role.org_id == p.org_id).order_by(Role.is_system.desc(), Role.name)).scalars()]


@router.get("/permissions")
def list_permissions(p: Principal = Depends(require("users.manage"))):
    return [{"code": k, "description": v} for k, v in PERMISSIONS.items()]


@router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(body: RoleIn, p: Principal = Depends(require("roles.manage")), db: Session = Depends(get_db)):
    perms = set(body.permissions)
    unknown = perms - set(PERMISSIONS)
    if unknown:
        raise HTTPException(422, f"Unknown permissions: {', '.join(sorted(unknown))}")
    if perms - p.permissions:
        raise HTTPException(403, "You cannot grant permissions you don't have")
    r = Role(org_id=p.org_id, name=body.name.strip(), description=body.description)
    r.permissions = [RolePermission(permission_code=c) for c in sorted(perms)]
    db.add(r)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A role with this name exists")
    p.audit(db, "role.create", entity_type="role", entity_id=r.id, summary=f"Created role {r.name}", after={"permissions": sorted(perms)})
    db.commit()
    return role_out(db, r)


@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(role_id: uuid.UUID, body: RoleIn, p: Principal = Depends(require("roles.manage")), db: Session = Depends(get_db)):
    r = scoped_get(db, Role, role_id, p.org_id, "Role")
    if r.is_system and r.name == "Owner":
        raise HTTPException(422, "The Owner role always has full access")
    perms = set(body.permissions)
    if perms - set(PERMISSIONS):
        raise HTTPException(422, "Unknown permissions")
    if (perms ^ r.permission_codes) - p.permissions:
        raise HTTPException(403, "You cannot change permissions you don't have")
    before = sorted(r.permission_codes)
    r.name = body.name.strip() if not r.is_system else r.name
    r.description = body.description
    r.permissions = [RolePermission(permission_code=c) for c in sorted(perms)]
    db.flush()
    p.audit(db, "role.permission_change", entity_type="role", entity_id=r.id, summary=f"Changed permissions of {r.name}",
            before={"permissions": before}, after={"permissions": sorted(perms)})
    db.commit()
    return role_out(db, r)


# ------------------------------------------------------------------ settings
def _clean_pos_settings(raw: dict) -> dict:
    from app.services.pos import DEFAULTS, METHODS
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise HTTPException(422, f"Unknown counter setting: {', '.join(sorted(unknown))}")
    out = {}
    if "prices_include_tax" in raw:
        out["prices_include_tax"] = bool(raw["prices_include_tax"])
    if "payment_methods" in raw:
        ms = [m for m in raw["payment_methods"] or [] if m in METHODS and m != "credit"]
        if not ms:
            raise HTTPException(422, "Keep at least one way to pay")
        out["payment_methods"] = list(dict.fromkeys(ms))
    if "max_discount_percent" in raw:
        try:
            v = float(raw["max_discount_percent"])
        except (TypeError, ValueError):
            raise HTTPException(422, "The discount limit must be a number")
        if not 0 <= v <= 100:
            raise HTTPException(422, "The discount limit must be between 0 and 100")
        out["max_discount_percent"] = v
    for k, n in (("receipt_header", 500), ("receipt_footer", 300)):
        if k in raw:
            out[k] = str(raw[k] or "")[:n]
    if "paper_width" in raw:
        if raw["paper_width"] not in (58, 80, "58", "80"):
            raise HTTPException(422, "Receipt paper is 58 or 80 mm")
        out["paper_width"] = int(raw["paper_width"])
    return out


@router.get("/settings", response_model=OrgOut)
def get_settings_(p: Principal = Depends(current_principal)):
    return p.org


@router.put("/settings", response_model=OrgOut)
def update_settings(body: SettingsIn, p: Principal = Depends(require("settings.manage")), db: Session = Depends(get_db)):
    org = p.org
    before = {"name": org.name, "currency": org.currency, "settings": dict(org.settings or {})}
    s = dict(org.settings or {})
    s.setdefault("modules", dict(BASE_SETTINGS["modules"]))
    if body.allow_negative_stock is not None:
        s["allow_negative_stock"] = body.allow_negative_stock
    if body.modules:
        unknown = set(body.modules) - set(BASE_SETTINGS["modules"])
        if unknown:
            raise HTTPException(422, f"Unknown modules: {', '.join(sorted(unknown))}")
        s["modules"] = {**s["modules"], **body.modules}
    if body.timezone is not None:
        from app.services.org import valid_timezone
        tz = valid_timezone(body.timezone)
        if not tz:
            raise HTTPException(422, "Unknown time zone")
        s["timezone"] = tz
    if body.pos is not None:
        s["pos"] = _clean_pos_settings({**(s.get("pos") or {}), **body.pos})
    org.settings = s
    if body.name:
        org.name = body.name.strip()
    if body.currency:
        org.currency = body.currency.upper()
    after = {"name": org.name, "currency": org.currency, "settings": s}
    b, a = audit.diff(before, after)
    if a:
        p.audit(db, "settings.update", entity_type="organization", entity_id=org.id, summary="Changed organisation settings", before=b, after=a)
    db.commit()
    return org


# ------------------------------------------------------------------ audit log
@router.get("/audit-logs", response_model=Page[AuditOut])
def audit_logs(action: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
               user_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
               q: str | None = None, params: PageParams = Depends(), p: Principal = Depends(require("audit.view")),
               db: Session = Depends(get_db)):
    stmt = select(AuditLog).where(AuditLog.org_id == p.org_id)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(AuditLog.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    if q:
        stmt = stmt.where(AuditLog.summary.ilike(f"%{q}%"))
    rows, total = paginate(db, stmt.order_by(AuditLog.id.desc()), params)
    return Page(items=[AuditOut(id=a.id, created_at=a.created_at, user_name=a.user.full_name if a.user else None,
                                action=a.action, entity_type=a.entity_type, entity_id=a.entity_id, summary=a.summary,
                                before=a.before, after=a.after, ip_address=a.ip_address) for a in rows],
                total=total, page=params.page, page_size=params.page_size)
