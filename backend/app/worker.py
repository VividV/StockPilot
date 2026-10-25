"""Background worker: sends queued emails / SMS / WhatsApp and runs the
scheduled checks. Run one or more copies with ``python -m app.worker``.

* every 10 s: turn new alerts into message digests, then send what is due
* every 15 min: expiry, late purchase orders, waiting/stuck transfers,
  subscription renewals and trial endings
  (guarded by a PostgreSQL advisory lock, so only one worker does it)
"""
import logging
import signal
import time

from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import Organization
from app.services import billing, messaging, monitors

log = logging.getLogger("stockpilot.worker")
SEND_EVERY = 10
CHECK_EVERY = 15 * 60
ADVISORY_KEY = 74_201_337  # any constant; identifies "scheduled checks" in pg_locks

_running = True


def _stop(*_):
    global _running
    _running = False


def run_checks() -> int:
    db = SessionLocal()
    try:
        if not db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY}).scalar():
            return 0  # another worker is doing it
        orgs = db.execute(select(Organization.id).where(Organization.is_active.is_(True))).scalars().all()
        for org_id in orgs:
            try:
                monitors.check_org(db, org_id)
                db.flush()
            except Exception:  # one bad organisation must not stop the others
                log.exception("scheduled checks failed for org %s", org_id)
                db.rollback()
                db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY})
        try:
            stats = billing.run_renewals(db)
            if any(stats.values()):
                log.info("subscriptions: %s", stats)
        except Exception:
            log.exception("subscription renewals failed")
            db.rollback()
        db.commit()
        return len(orgs)
    finally:
        db.close()


def run_once() -> dict:
    db = SessionLocal()
    try:
        queued = messaging.dispatch_alerts(db)
        stats = messaging.send_pending(db)
        return {"queued": queued, **stats}
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker started")
    last_check = 0.0
    while _running:
        try:
            if time.monotonic() - last_check >= CHECK_EVERY:
                n = run_checks()
                last_check = time.monotonic()
                if n:
                    log.info("scheduled checks done for %s organisation(s)", n)
            out = run_once()
            if any(out.values()):
                log.info("messages: %s", out)
        except Exception:
            log.exception("worker loop error")
        for _ in range(SEND_EVERY):
            if not _running:
                break
            time.sleep(1)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
