import re
from datetime import date, datetime, timedelta, timezone

import pyotp
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import Notification, NotificationDelivery, User
from app.services import messaging, monitors
from conftest import PASSWORD


def outbox(email: str) -> list[NotificationDelivery]:
    db = SessionLocal()
    try:
        return db.execute(select(NotificationDelivery).where(NotificationDelivery.recipient == email)
                          .order_by(NotificationDelivery.created_at)).scalars().all()
    finally:
        db.close()


def link_token(body: str, path: str) -> str:
    return re.search(rf"{path}\?token=([\w\-]+)", body).group(1)


# ------------------------------------------------------------------ verification & reset
def test_signup_sends_verification_and_link_confirms(client, org):
    assert org.owner.ok("get", "/api/auth/me")["email_verified"] is False
    mails = outbox(org.email)
    assert mails[-1].subject == "Confirm your email for StockPilot" and mails[-1].status == "pending"
    token = link_token(mails[-1].body, "/verify-email")
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert org.owner.ok("get", "/api/auth/me")["email_verified"] is True
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 400  # used once
    assert org.owner.ok("post", "/api/auth/resend-verification")["message"] == "Your email is already confirmed."


def test_invited_user_gets_email(org):
    org.user("Viewer")
    db = SessionLocal()
    invite = db.execute(select(NotificationDelivery).where(NotificationDelivery.subject.like("You've been added to%"))
                        .order_by(NotificationDelivery.created_at.desc())).scalars().first()
    db.close()
    assert invite and "Olu Owner has added you to Test Co" in invite.body


def test_password_reset_flow(client, org):
    r = client.post("/api/auth/forgot-password", json={"email": "nobody-here@example.com"})
    same = r.json()["message"]
    assert r.status_code == 200 and outbox("nobody-here@example.com") == []
    r = client.post("/api/auth/forgot-password", json={"email": org.email.upper()})
    assert r.json()["message"] == same   # no way to tell whether the account exists
    token = link_token(outbox(org.email)[-1].body, "/reset-password")
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "weakpassword"}).status_code == 422
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "New-Password-456"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"email": org.email, "password": "New-Password-456"}).status_code == 200
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "Other-Password-789"}).status_code == 400
    assert org.owner.ok("get", "/api/auth/me")["email_verified"] is True


def test_reset_link_expires(client, org):
    client.post("/api/auth/forgot-password", json={"email": org.email})
    token = link_token(outbox(org.email)[-1].body, "/reset-password")
    db = SessionLocal()
    from app.models import OneTimeToken
    db.execute(update(OneTimeToken).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    db.commit(); db.close()
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "New-Password-456"}).status_code == 400


# ------------------------------------------------------------------ two-step verification
def test_two_step_verification(client, org):
    setup = org.owner.ok("post", "/api/auth/2fa/setup")
    assert setup["qr_svg"].startswith("<svg") and setup["otpauth_uri"].startswith("otpauth://totp/")
    totp = pyotp.TOTP(setup["secret"])
    assert org.owner.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 400
    r = org.owner.ok("post", "/api/auth/2fa/enable", json={"code": totp.now()})
    codes = r["recovery_codes"]
    assert len(codes) == 8 and org.owner.ok("get", "/api/auth/me")["totp_enabled"] is True

    # password alone is no longer enough
    r = client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD}).json()
    assert r["two_factor_required"] is True and r["access_token"] is None
    bad = client.post("/api/auth/login/2fa", json={"challenge_token": r["challenge_token"], "code": "123456"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login/2fa", json={"challenge_token": r["challenge_token"], "code": totp.now()})
    assert ok.status_code == 200 and ok.json()["access_token"]
    # a recovery code works exactly once
    ch = client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD}).json()["challenge_token"]
    assert client.post("/api/auth/login/2fa", json={"challenge_token": ch, "code": codes[0]}).status_code == 200
    ch = client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD}).json()["challenge_token"]
    assert client.post("/api/auth/login/2fa", json={"challenge_token": ch, "code": codes[0]}).status_code == 401
    # the challenge token can't be used as an access token
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {ch}"}).status_code == 401

    assert org.owner.post("/api/auth/2fa/disable", json={"password": "wrong", "code": totp.now()}).status_code == 400
    org.owner.ok("post", "/api/auth/2fa/disable", json={"password": PASSWORD, "code": totp.now()})
    assert client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD}).json()["access_token"]


def test_admin_can_reset_lost_authenticator(client, org):
    staff = org.user("Warehouse Staff")
    me = staff.ok("get", "/api/auth/me")
    secret = staff.ok("post", "/api/auth/2fa/setup")["secret"]
    staff.ok("post", "/api/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()})
    org.owner.ok("put", f"/api/users/{me['id']}", json={"reset_two_factor": True})
    db = SessionLocal()
    u = db.get(User, me["id"]); db.close()
    assert u.totp_enabled is False and u.totp_secret is None
    log = org.owner.ok("get", f"/api/audit-logs?entity_id={me['id']}")["items"][0]
    assert log["after"]["two_factor"] == "reset"


# ------------------------------------------------------------------ alert delivery
def _verify(email):
    db = SessionLocal()
    db.execute(update(User).where(User.email == email).values(email_verified=True))
    db.commit(); db.close()


def test_alert_preferences_and_digest(org, monkeypatch):
    prefs = org.owner.ok("get", "/api/me/notifications")
    assert prefs["channels"]["sms"] is False and {"low_stock", "po_overdue"} <= {t["key"] for t in prefs["types_available"]}
    r = org.owner.put("/api/me/notifications", json={"email": True, "sms": True, "phone": "+2348012345678"})
    assert r.status_code == 422  # SMS isn't configured on this server
    org.owner.ok("put", "/api/me/notifications", json={"email": True, "types": ["low_stock", "out_of_stock"]})
    _verify(org.email)
    a = org.product(name="Alpha", reorder_level=5)
    b = org.product(name="Beta", reorder_level=5)
    org.receive(a["id"], 2)
    org.receive(b["id"], 1)
    org.owner.ok("post", "/api/inventory/issue", json={"product_id": b["id"], "location_id": org.main, "quantity": 1})

    db = SessionLocal()
    db.execute(update(Notification).where(Notification.org_id != org.org_id).values(dispatched_at=datetime.now(timezone.utc)))
    db.commit()
    before = len(outbox(org.email))
    assert messaging.dispatch_alerts(db) >= 1
    mails = outbox(org.email)[before:]
    assert len(mails) == 1   # one digest, not one email per alert
    assert "2 new alerts at Test Co" in mails[0].subject
    assert "Alpha is running low" in mails[0].body and "Beta is out of stock" in mails[0].body
    assert messaging.dispatch_alerts(db) == 0  # each alert goes out once
    # SMTP isn't configured in tests -> written to the log and marked sent
    stats = messaging.send_pending(db, limit=500)
    assert stats["failed"] == 0
    assert outbox(org.email)[-1].status == "sent"
    db.close()


def test_failed_delivery_retries_then_alerts(org, monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise messaging.SendError("connection refused")
    monkeypatch.setattr(messaging, "send_email", boom)
    db = SessionLocal()
    db.execute(update(NotificationDelivery).where(NotificationDelivery.status == "pending").values(status="sent"))
    d = messaging.queue_email(db, "x@example.com", "Hi", "Body", org_id=org.org_id)
    db.commit()
    for i in range(messaging.MAX_ATTEMPTS):
        db.execute(update(NotificationDelivery).where(NotificationDelivery.id == d.id).values(next_attempt_at=datetime.now(timezone.utc)))
        db.commit()
        messaging.send_pending(db)
        db.refresh(d)
        if i < messaging.MAX_ATTEMPTS - 1:
            assert d.status == "pending" and d.next_attempt_at > datetime.now(timezone.utc)
    assert d.status == "failed" and d.attempts == messaging.MAX_ATTEMPTS and "connection refused" in d.last_error
    db.close()
    titles = [n["title"] for n in org.owner.ok("get", "/api/notifications")["items"]]
    assert "An email couldn't be sent" in titles


def test_scheduled_checks_late_po_and_waiting_transfer(org):
    sup = org.owner.ok("post", "/api/suppliers", json={"name": "Slow Supplies"})
    p = org.product()
    po = org.owner.ok("post", "/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": org.main,
        "expected_date": (date.today() - timedelta(days=3)).isoformat(), "lines": [{"product_id": p["id"], "quantity": 1}], "confirm": True})
    org.receive(p["id"], 5)
    shop = org.warehouse("Shop")["default_location_id"]
    t = org.owner.ok("post", "/api/transfers", json={"from_location_id": org.main, "to_location_id": shop,
                                                     "items": [{"product_id": p["id"], "quantity": 1}], "submit": True})
    db = SessionLocal()
    from uuid import UUID
    monitors.check_org(db, UUID(org.org_id)); db.commit()
    titles = {n["title"] for n in org.owner.ok("get", "/api/notifications")["items"]}
    assert f"{po['number']} from Slow Supplies is 3 days late" in titles
    assert f"{t['number']} is waiting for approval" in titles
    org.owner.ok("post", f"/api/purchase-orders/{po['id']}/close")
    org.owner.ok("post", f"/api/transfers/{t['id']}/cancel")
    monitors.check_org(db, UUID(org.org_id)); db.commit(); db.close()
    titles = {n["title"] for n in org.owner.ok("get", "/api/notifications")["items"]}
    assert not any(po["number"] in x or t["number"] in x for x in titles)


def test_worker_runs(org):
    from app import worker
    assert worker.run_checks() >= 1
    out = worker.run_once()
    assert set(out) == {"queued", "sent", "retry", "failed"}


def test_twilio_requests_are_well_formed(monkeypatch):
    import io
    import urllib.parse
    from app.config import get_settings
    s = get_settings()
    for k, v in {"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "tok", "TWILIO_SMS_FROM": "+15550001111",
                 "TWILIO_WHATSAPP_FROM": "+14155238886"}.items():
        monkeypatch.setattr(s, k, v)
    sent = []

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=0):
        sent.append(req)
        return Resp(b'{"sid": "SM1"}')
    monkeypatch.setattr(messaging.urllib.request, "urlopen", fake_urlopen)
    assert messaging.send_twilio("whatsapp", "+2348012345678", "Rice is running low") == "sent"
    assert messaging.send_twilio("sms", "+2348012345678", "Rice is running low") == "sent"
    wa, sms = (dict(urllib.parse.parse_qsl(r.data.decode())) for r in sent)
    assert sent[0].full_url == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert wa == {"To": "whatsapp:+2348012345678", "From": "whatsapp:+14155238886", "Body": "Rice is running low"}
    assert sms["To"] == "+2348012345678" and sms["From"] == "+15550001111"
    assert sent[0].headers["Authorization"].startswith("Basic ")
    assert messaging.channels()["whatsapp"] is True


def test_real_smtp_delivery(monkeypatch):
    """Sends through an actual SMTP server (aiosmtpd) to prove the wire format."""
    import socket
    import time
    from aiosmtpd.controller import Controller
    from app.config import get_settings
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    got = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            got.append(envelope)
            return "250 OK"
    ctl = Controller(Handler(), hostname="127.0.0.1", port=port)
    ctl.start()
    try:
        s = get_settings()
        for k, v in {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": port, "SMTP_SECURITY": "none",
                     "EMAIL_FROM": "Dye Lab Stock <stock@dyelab.example>"}.items():
            monkeypatch.setattr(s, k, v)
        assert messaging.send_email("vive@example.com", "StockPilot: test", "Hello ✓") == "sent"
        time.sleep(0.2)
    finally:
        ctl.stop()
    env = got[0]
    assert env.mail_from == "stock@dyelab.example" and env.rcpt_tos == ["vive@example.com"]
    text = env.content.decode()
    assert "Subject: StockPilot: test" in text and "From: Dye Lab Stock <stock@dyelab.example>" in text
