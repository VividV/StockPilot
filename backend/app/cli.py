"""Operator commands. Run inside the API container, for example:

    docker compose exec api python -m app.cli companies
    docker compose exec api python -m app.cli set-plan owner@shop.com pro 12
    docker compose exec api python -m app.cli extend-trial owner@shop.com 14

``set-plan`` gives a company a plan for N months without payment (your own
company, pilot customers, a refund). It does not renew by itself.
"""
import sys
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Organization, Subscription, User
from app.services import billing


def _org_by_email(db, email: str) -> Organization:
    u = db.execute(select(User).where(User.email == email.lower().strip())).unique().scalar_one_or_none()
    if not u:
        sys.exit(f"No user with email {email}")
    return db.get(Organization, u.org_id)


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    db = SessionLocal()
    try:
        cmd = argv[0]
        if cmd == "companies":
            for org in db.execute(select(Organization).order_by(Organization.created_at)).scalars():
                sub = db.get(Subscription, org.id)
                owner = db.execute(select(User).where(User.org_id == org.id).order_by(User.created_at)).unique().scalars().first()
                state = f"{sub.plan}/{sub.status} until {(sub.current_period_end or sub.trial_ends_at):%Y-%m-%d}" if sub else "no subscription yet"
                print(f"{org.created_at:%Y-%m-%d}  {org.name:<30} {owner.email if owner else '':<35} {state}")
        elif cmd == "set-plan" and len(argv) == 4:
            org = _org_by_email(db, argv[1])
            sub = billing.set_plan(db, org.id, argv[2], int(argv[3]))
            db.commit()
            print(f"{org.name}: {sub.plan} until {sub.current_period_end:%Y-%m-%d} (no automatic renewal)")
        elif cmd == "extend-trial" and len(argv) == 3:
            org = _org_by_email(db, argv[1])
            sub = billing.get_sub(db, org.id)
            base = max(sub.trial_ends_at or billing.now(), billing.now())
            sub.plan, sub.status, sub.trial_ends_at, sub.reminded_at = "trial", "trialing", base + timedelta(days=int(argv[2])), None
            db.commit()
            print(f"{org.name}: trial until {sub.trial_ends_at:%Y-%m-%d}")
        else:
            print(__doc__)
            sys.exit(2)
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
