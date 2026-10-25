"""Password reset, email verification and two-step verification (TOTP)."""
from __future__ import annotations

import hashlib
import io
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import OneTimeToken, Organization, User, UserSession
from app.security import hash_token, new_opaque_token
from app.services.messaging import queue_email

TTL = {"password_reset": timedelta(hours=1), "email_verify": timedelta(days=3)}
ISSUER = "StockPilot"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ one-time tokens
def issue_token(db: Session, user: User, purpose: str) -> str:
    # one live token per purpose: older links stop working
    db.execute(update(OneTimeToken).where(OneTimeToken.user_id == user.id, OneTimeToken.purpose == purpose,
                                          OneTimeToken.used_at.is_(None)).values(used_at=_now()))
    token, token_hash = new_opaque_token()
    db.add(OneTimeToken(user_id=user.id, purpose=purpose, token_hash=token_hash, expires_at=_now() + TTL[purpose]))
    return token


def consume_token(db: Session, token: str, purpose: str) -> User | None:
    row = db.execute(select(OneTimeToken).where(OneTimeToken.token_hash == hash_token(token or ""),
                                                OneTimeToken.purpose == purpose).with_for_update()).scalar_one_or_none()
    if not row or row.used_at or row.expires_at < _now():
        return None
    row.used_at = _now()
    user = db.get(User, row.user_id)
    return user if user and user.is_active else None


def _link(path: str, token: str) -> str:
    return f"{get_settings().APP_URL.rstrip('/')}{path}?token={token}"


def send_password_reset(db: Session, user: User) -> None:
    token = issue_token(db, user, "password_reset")
    queue_email(db, user.email, "Reset your StockPilot password",
                f"Hello {user.full_name.split(' ')[0]},\n\n"
                f"Someone (hopefully you) asked to reset the password for {user.email}.\n\n"
                f"Choose a new password here (the link works for 1 hour, once):\n{_link('/reset-password', token)}\n\n"
                "If you didn't ask for this, ignore this email. Your password stays the same.",
                org_id=user.org_id, user_id=user.id)


def send_verification(db: Session, user: User, *, welcome: bool = False, invited_by: str | None = None) -> None:
    token = issue_token(db, user, "email_verify")
    org = db.get(Organization, user.org_id)
    if invited_by:
        intro = f"{invited_by} has added you to {org.name} on StockPilot, the stock system your team uses.\n\nYour sign-in email is {user.email}. They will give you your first password.\n\n"
        subject = f"You've been added to {org.name} on StockPilot"
    elif welcome:
        intro = f"Welcome to StockPilot. {org.name} is set up and ready.\n\n"
        subject = "Confirm your email for StockPilot"
    else:
        intro = ""
        subject = "Confirm your email for StockPilot"
    queue_email(db, user.email, subject,
                f"Hello {user.full_name.split(' ')[0]},\n\n{intro}"
                f"Please confirm this is your email address:\n{_link('/verify-email', token)}\n\n"
                "Confirming lets us send you password-reset links and stock alerts.",
                org_id=user.org_id, user_id=user.id)


def revoke_sessions(db: Session, user: User) -> None:
    db.execute(update(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).values(revoked_at=_now()))


# ------------------------------------------------------------------ two-step verification
def _qr_svg(data: str) -> str:
    w = QrCodeWidget(data)
    b = w.getBounds()
    size = 200
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(w)
    out = io.StringIO()
    renderSVG.drawToFile(d, out)
    svg = out.getvalue()
    return svg[svg.index("<svg"):]


def start_totp(db: Session, user: User) -> dict:
    if user.totp_enabled:
        raise ValueError("Two-step verification is already on")
    user.totp_secret = pyotp.random_base32()
    org = db.get(Organization, user.org_id)
    uri = pyotp.TOTP(user.totp_secret).provisioning_uri(name=user.email, issuer_name=f"{ISSUER} ({org.name})"[:60])
    return {"secret": user.totp_secret, "otpauth_uri": uri, "qr_svg": _qr_svg(uri)}


def _totp_ok(user: User, code: str) -> bool:
    code = "".join(ch for ch in code if ch.isdigit())
    return bool(user.totp_secret) and len(code) == 6 and pyotp.TOTP(user.totp_secret).verify(code, valid_window=1)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").lower().encode()).hexdigest()


def enable_totp(db: Session, user: User, code: str) -> list[str]:
    if not user.totp_secret:
        raise ValueError("Start the set-up again")
    if not _totp_ok(user, code):
        raise ValueError("That code isn't right. Check the time on your phone is set automatically, then try the newest code.")
    user.totp_enabled = True
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(8)]
    user.recovery_codes = [_hash_code(c) for c in codes]
    return codes


def disable_totp(user: User) -> None:
    user.totp_enabled = False
    user.totp_secret = None
    user.recovery_codes = []


def verify_second_factor(db: Session, user: User, code: str) -> str | None:
    """'totp' or 'recovery' if the code is good, else None. Recovery codes work once."""
    if _totp_ok(user, code):
        return "totp"
    h = _hash_code(code)
    if h in (user.recovery_codes or []):
        user.recovery_codes = [c for c in user.recovery_codes if c != h]
        return "recovery"
    return None
