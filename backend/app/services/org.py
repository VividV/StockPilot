"""Creating a new organisation with sensible defaults."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Location, LocationKind, Organization, Permission, Role, RolePermission, UnitOfMeasure, User, Warehouse, WarehouseType,
)
from app.permissions import PERMISSIONS, ROLE_TEMPLATES
from app.security import hash_password
from app.services.industry import COMMON_UNITS, INDUSTRIES, settings_for


def ensure_permissions(db: Session) -> None:
    existing = set(db.execute(select(Permission.code)).scalars())
    for code, desc in PERMISSIONS.items():
        if code not in existing:
            db.add(Permission(code=code, description=desc))
    db.flush()


def make_code(name: str) -> str:
    code = re.sub(r"[^A-Z0-9]", "", name.upper())[:6]
    return code or "WH"


def create_warehouse(db: Session, org_id, name: str, code: str | None = None,
                     type_: WarehouseType = WarehouseType.WAREHOUSE, address: str | None = None) -> Warehouse:
    base = (code or make_code(name)).upper()
    code_, n = base, 2
    while db.execute(select(Warehouse.id).where(Warehouse.org_id == org_id, Warehouse.code == code_)).first():
        code_ = f"{base}{n}"
        n += 1
    wh = Warehouse(org_id=org_id, name=name, code=code_, type=type_, address=address)
    db.add(wh)
    db.flush()
    db.add(Location(org_id=org_id, warehouse_id=wh.id, name="Main", code="MAIN", kind=LocationKind.DEFAULT))
    db.flush()
    return wh


def valid_timezone(name: str | None) -> str | None:
    from zoneinfo import ZoneInfo
    if not name:
        return None
    try:
        ZoneInfo(name)
        return name
    except Exception:  # noqa: BLE001 - unknown or malformed name
        return None


def bootstrap_organization(db: Session, *, org_name: str, industry: str, currency: str, owner_name: str,
                           owner_email: str, owner_password: str, timezone: str | None = None) -> tuple[Organization, User]:
    ensure_permissions(db)
    industry = industry if industry in INDUSTRIES else "other"
    settings = settings_for(industry)
    settings["timezone"] = valid_timezone(timezone) or ("Africa/Lagos" if currency.upper() == "NGN" else "UTC")
    org = Organization(name=org_name, industry=industry, currency=currency.upper(), settings=settings)
    db.add(org)
    db.flush()

    roles = {}
    for name, (desc, perms) in ROLE_TEMPLATES.items():
        r = Role(org_id=org.id, name=name, description=desc, is_system=True)
        r.permissions = [RolePermission(permission_code=p) for p in sorted(perms)]
        db.add(r)
        roles[name] = r
    for abbr, name, dec in COMMON_UNITS:
        db.add(UnitOfMeasure(org_id=org.id, name=name, abbreviation=abbr, allow_decimals=dec))
    db.flush()

    # Everyone starts with one place to keep stock; more can be added any time.
    create_warehouse(db, org.id, "Main Store", "MAIN", WarehouseType.STORE)

    owner = User(org_id=org.id, email=owner_email.lower().strip(), full_name=owner_name.strip(),
                 password_hash=hash_password(owner_password), role_id=roles["Owner"].id, email_verified=False)
    db.add(owner)
    db.flush()
    from app.services import billing
    if billing.enabled():
        billing.start_trial(db, org.id, owner.email)
    return org, owner
