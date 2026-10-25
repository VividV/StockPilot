"""Request-scoped dependencies: who is calling, for which organisation, and
what they may do. Every router uses ``require("perm.code")``; tenant scoping is
taken from the authenticated user, never from the request body or URL."""
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.models import Organization, Subscription, User
from app.security import decode_access_token
from app.services.inventory import Ctx

bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@dataclass
class Principal:
    user: User
    org: Organization
    permissions: set[str]
    ip: str | None
    user_agent: str | None
    locked: bool = False  # subscription ended: read-only

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def ctx(self) -> Ctx:
        return Ctx(org_id=self.org.id, user_id=self.user.id, org_settings=self.org.settings or {},
                   allowed_warehouse_ids=self.user.warehouse_ids)

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def audit(self, db: Session, action: str, **kw) -> None:
        audit.record(db, org_id=self.org.id, user_id=self.user.id, action=action, ip=self.ip,
                     user_agent=self.user_agent, **kw)


def current_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_access_token(creds.credentials)
        user_id = uuid.UUID(payload["sub"])
        org_id = uuid.UUID(payload["org"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again",
                            headers={"WWW-Authenticate": "Bearer"})
    user = db.execute(select(User).where(User.id == user_id, User.org_id == org_id)).unique().scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
    org = db.get(Organization, org_id)
    if not org or not org.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Organisation disabled")
    locked = False
    from app.services import billing
    if billing.enabled():
        locked = billing.is_locked(db.get(Subscription, org.id))
    return Principal(user=user, org=org, permissions=user.role.permission_codes, ip=client_ip(request),
                     user_agent=request.headers.get("user-agent"), locked=locked)


# Still allowed when the subscription has ended, so the owner can pay and the
# tills can upload sales they made while offline.
LOCK_EXEMPT = ("/api/billing", "/api/auth", "/api/me", "/api/pos/sales")


def _check_lock(request: Request, p: Principal) -> None:
    if p.locked and request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith(LOCK_EXEMPT):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            "Your StockPilot subscription has ended, so changes are paused. Your data is safe: "
                            "the owner can choose a plan on the Billing page to carry on.")


def require(*perms: str):
    """Dependency: caller must hold *all* listed permissions."""
    def checker(request: Request, p: Principal = Depends(current_principal)) -> Principal:
        missing = [x for x in perms if x not in p.permissions]
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"You don't have permission to do this ({', '.join(missing)})")
        _check_lock(request, p)
        return p
    return checker


def require_any(*perms: str):
    def checker(request: Request, p: Principal = Depends(current_principal)) -> Principal:
        if not any(x in p.permissions for x in perms):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission to do this")
        _check_lock(request, p)
        return p
    return checker


def scoped_get(db: Session, model, obj_id, org_id, label: str = "Record"):
    """Fetch by id *and* organisation; anything from another tenant is a 404."""
    obj = db.execute(select(model).where(model.id == obj_id, model.org_id == org_id)).unique().scalar_one_or_none()
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found")
    return obj
