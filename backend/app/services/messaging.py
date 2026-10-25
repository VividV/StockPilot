"""Outgoing messages: email (SMTP), SMS and WhatsApp (Twilio).

Nothing is sent inside a web request. Messages are written to the
``notification_deliveries`` outbox in the same transaction as the change that
caused them, and the worker (``python -m app.worker``) sends them, retrying
with back-off. A message that keeps failing raises an in-app alert.
"""
from __future__ import annotations

import base64
import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Notification, NotificationDelivery, Organization, Role, RolePermission, User

log = logging.getLogger("stockpilot.messaging")
MAX_ATTEMPTS = 6

# Which permission a person needs before they're told about an alert type.
ALERT_TYPES = {
    "low_stock": ("Low stock", "inventory.view"),
    "out_of_stock": ("Out of stock", "inventory.view"),
    "expiring": ("Expiring soon", "inventory.view"),
    "expired": ("Expired", "inventory.view"),
    "po_overdue": ("Late purchase orders", "purchases.view"),
    "transfer_pending": ("Transfers waiting for approval", "inventory.transfer_approve"),
    "transfer_late": ("Transfers on the way too long", "inventory.view"),
    "failed_operation": ("Problems sending messages", "settings.manage"),
    "pos_review": ("Counter sales that need checking", "pos.manage"),
    "billing": ("Subscription and payments", "billing.manage"),
}


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ queueing
def queue(db: Session, *, channel: str, recipient: str, subject: str | None, body: str, org_id=None, user_id=None,
          notification_id=None) -> NotificationDelivery:
    d = NotificationDelivery(org_id=org_id, user_id=user_id, notification_id=notification_id, channel=channel,
                             recipient=recipient, subject=subject, body=body, status="pending", attempts=0,
                             next_attempt_at=now())
    db.add(d)
    return d


def queue_email(db: Session, to: str, subject: str, body: str, **kw) -> NotificationDelivery:
    return queue(db, channel="email", recipient=to, subject=subject, body=body, **kw)


# ------------------------------------------------------------------ transports
class SendError(Exception):
    def __init__(self, msg: str, permanent: bool = False):
        super().__init__(msg)
        self.permanent = permanent


def send_email(to: str, subject: str, body: str) -> str:
    s = get_settings()
    if not s.email_enabled:
        log.warning("EMAIL (not sent: SMTP_HOST is not set)\nTo: %s\nSubject: %s\n\n%s", to, subject, body)
        return "logged"
    msg = EmailMessage()
    name, addr = parseaddr(s.EMAIL_FROM)
    msg["From"] = formataddr((name or "StockPilot", addr or s.EMAIL_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=(addr.split("@")[-1] if "@" in addr else None))
    msg.set_content(body)
    try:
        if s.SMTP_SECURITY == "ssl":
            server = smtplib.SMTP_SSL(s.SMTP_HOST, s.SMTP_PORT, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s.SMTP_HOST, s.SMTP_PORT, timeout=20)
            if s.SMTP_SECURITY == "starttls":
                server.starttls(context=ssl.create_default_context())
        with server:
            if s.SMTP_USER:
                server.login(s.SMTP_USER, s.SMTP_PASSWORD)
            server.send_message(msg)
    except smtplib.SMTPRecipientsRefused as e:
        raise SendError(f"Address refused: {e}", permanent=True)
    except smtplib.SMTPAuthenticationError as e:
        raise SendError(f"SMTP login failed: {e.smtp_code}")
    except (smtplib.SMTPException, OSError) as e:
        raise SendError(f"SMTP error: {e}")
    return "sent"


def send_twilio(channel: str, to: str, body: str) -> str:
    s = get_settings()
    sender = s.TWILIO_WHATSAPP_FROM if channel == "whatsapp" else s.TWILIO_SMS_FROM
    if not (s.TWILIO_ACCOUNT_SID and s.TWILIO_AUTH_TOKEN and sender):
        log.warning("%s (not sent: Twilio is not configured) to %s: %s", channel.upper(), to, body)
        return "logged"
    data = {"To": f"whatsapp:{to}" if channel == "whatsapp" else to, "Body": body[:1500]}
    if sender.startswith("MG"):
        data["MessagingServiceSid"] = sender
    else:
        data["From"] = f"whatsapp:{sender}" if channel == "whatsapp" else sender
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{s.TWILIO_ACCOUNT_SID}/Messages.json",
        data=urllib.parse.urlencode(data).encode(), method="POST",
        headers={"Authorization": "Basic " + base64.b64encode(f"{s.TWILIO_ACCOUNT_SID}:{s.TWILIO_AUTH_TOKEN}".encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        raise SendError(f"Twilio {e.code}: {detail}", permanent=e.code in (400, 404))
    except OSError as e:
        raise SendError(f"Twilio unreachable: {e}")
    return "sent"


def deliver(d: NotificationDelivery) -> str:
    if d.channel == "email":
        return send_email(d.recipient, d.subject or "StockPilot", d.body or "")
    if d.channel in ("sms", "whatsapp"):
        return send_twilio(d.channel, d.recipient, d.body or "")
    raise SendError(f"Unknown channel {d.channel}", permanent=True)


def send_pending(db: Session, limit: int = 50) -> dict:
    """Send what is due. Safe with several workers: rows are claimed with SKIP LOCKED."""
    rows = db.execute(select(NotificationDelivery).where(
        NotificationDelivery.status == "pending", NotificationDelivery.next_attempt_at <= now())
        .order_by(NotificationDelivery.next_attempt_at).limit(limit).with_for_update(skip_locked=True)).scalars().all()
    stats = {"sent": 0, "retry": 0, "failed": 0}
    for d in rows:
        d.attempts += 1
        try:
            deliver(d)
            d.status, d.sent_at, d.last_error = "sent", now(), None
            stats["sent"] += 1
        except SendError as e:
            d.last_error = str(e)[:1000]
            if e.permanent or d.attempts >= MAX_ATTEMPTS:
                d.status = "failed"
                stats["failed"] += 1
                if d.org_id:
                    _failure_alert(db, d)
            else:
                d.next_attempt_at = now() + timedelta(minutes=2 ** d.attempts)  # 2, 4, 8, 16, 32 min
                stats["retry"] += 1
            log.warning("delivery %s via %s failed (attempt %s): %s", d.id, d.channel, d.attempts, e)
    db.commit()
    return stats


def _failure_alert(db: Session, d: NotificationDelivery) -> None:
    from app.services.inventory import _upsert_alert
    _upsert_alert(db, d.org_id, type_="failed_operation", severity="warning",
                  title=f"{ {'email': 'An email', 'sms': 'A text message', 'whatsapp': 'A WhatsApp message'}.get(d.channel, 'A message')} couldn't be sent",
                  body=f"To {d.recipient}: {d.last_error}. Check the {d.channel} settings and the address.",
                  entity_type="delivery", entity_id=f"{d.channel}:{d.recipient}")


# ------------------------------------------------------------------ alert fan-out
def _prefs(u: User) -> dict:
    p = dict(u.notification_prefs or {})
    p.setdefault("email", False)
    p.setdefault("sms", False)
    p.setdefault("whatsapp", False)
    p.setdefault("types", list(ALERT_TYPES))
    return p


def dispatch_alerts(db: Session) -> int:
    """Turn new in-app alerts into one email / SMS / WhatsApp digest per person."""
    s = get_settings()
    app_url = s.APP_URL.rstrip("/")
    new = db.execute(select(Notification).where(Notification.dispatched_at.is_(None))
                     .order_by(Notification.org_id, Notification.created_at).limit(500)
                     .with_for_update(skip_locked=True)).scalars().all()
    if not new:
        return 0
    by_org: dict = defaultdict(list)
    for n in new:
        n.dispatched_at = now()
        if not n.is_resolved:
            by_org[n.org_id].append(n)
    queued = 0
    for org_id, alerts in by_org.items():
        org = db.get(Organization, org_id)
        users = db.execute(select(User).where(User.org_id == org_id, User.is_active.is_(True))).unique().scalars().all()
        perms = defaultdict(set)
        for role_id, code in db.execute(select(RolePermission.role_id, RolePermission.permission_code)
                                        .join(Role, Role.id == RolePermission.role_id).where(Role.org_id == org_id)):
            perms[role_id].add(code)
        for u in users:
            pr = _prefs(u)
            if not (pr["email"] or (pr["sms"] and u.phone) or (pr["whatsapp"] and u.phone)):
                continue
            mine = [a for a in alerts if a.type in pr["types"] and ALERT_TYPES.get(a.type, ("", "inventory.view"))[1] in perms[u.role_id]]
            if not mine:
                continue
            title = f"{len(mine)} new alert{'s' if len(mine) != 1 else ''} at {org.name}" if len(mine) > 1 else mine[0].title
            lines = [f"• {a.title}" + (f" — {a.body}" if a.body else "") for a in mine[:50]]
            if len(mine) > 50:
                lines.append(f"…and {len(mine) - 50} more.")
            if pr["email"] and u.email_verified:
                body = (f"Hello {u.full_name.split(' ')[0]},\n\n" + "\n".join(lines) +
                        f"\n\nSee all alerts: {app_url}/notifications\n\n"
                        f"You get these because alert emails are switched on for your StockPilot account. "
                        f"Change it under Settings → My account: {app_url}/settings")
                queue_email(db, u.email, f"StockPilot: {title}", body, org_id=org_id, user_id=u.id, notification_id=mine[0].id)
                queued += 1
            short = f"StockPilot: {title}. " + "; ".join(a.title for a in mine[:5]) + (" …" if len(mine) > 5 else "")
            for ch in ("sms", "whatsapp"):
                if pr[ch] and u.phone:
                    queue(db, channel=ch, recipient=u.phone, subject=None, body=short[:600], org_id=org_id, user_id=u.id,
                          notification_id=mine[0].id)
                    queued += 1
    db.commit()
    return queued


def channels() -> dict:
    s = get_settings()
    return {"email": True, "email_configured": s.email_enabled, "sms": s.sms_enabled, "whatsapp": s.whatsapp_enabled}
