"""Password reset, email verification, two-step verification and alert preferences."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import Principal, client_ip, current_principal
from app.models import User
from app.schemas import Message
from app.security import hash_password, limiter, validate_password_strength, verify_password
from app.services import account as svc
from app.services import messaging

router = APIRouter(prefix="/api", tags=["account"])


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=10, max_length=128)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class DisableIn(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=20)


class PrefsIn(BaseModel):
    email: bool = False
    sms: bool = False
    whatsapp: bool = False
    phone: str | None = Field(None, max_length=32)
    types: list[str] | None = None


# ------------------------------------------------------------------ password reset
@router.post("/auth/forgot-password", response_model=Message)
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower()
    if not (limiter.hit(f"forgot-ip:{client_ip(request)}", 10) and limiter.hit(f"forgot:{email}", 3)):
        raise HTTPException(429, "Too many requests. Wait a few minutes and try again.")
    user = db.execute(select(User).where(User.email == email)).unique().scalar_one_or_none()
    if user and user.is_active:
        svc.send_password_reset(db, user)
        audit.record(db, org_id=user.org_id, user_id=user.id, action="auth.password_reset_requested", entity_type="user",
                     entity_id=user.id, summary="Asked for a password-reset link", ip=client_ip(request),
                     user_agent=request.headers.get("user-agent"))
        db.commit()
    # same answer either way, so nobody can find out which emails have accounts
    return Message(message="If that email has a StockPilot account, a reset link is on its way. Check your inbox and spam folder.")


@router.post("/auth/reset-password", response_model=Message)
def reset(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    if not limiter.hit(f"reset-ip:{client_ip(request)}", 10):
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    try:
        validate_password_strength(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    user = svc.consume_token(db, body.token, "password_reset")
    if not user:
        raise HTTPException(400, "This link has expired or was already used. Ask for a new one.")
    user.password_hash = hash_password(body.password)
    user.email_verified = True  # they proved they own the inbox
    svc.revoke_sessions(db, user)
    audit.record(db, org_id=user.org_id, user_id=user.id, action="user.password_reset", entity_type="user", entity_id=user.id,
                 summary="Password reset by email link; signed out everywhere", ip=client_ip(request),
                 user_agent=request.headers.get("user-agent"))
    db.commit()
    return Message(message="Password changed. Sign in with your new password.")


# ------------------------------------------------------------------ email verification
@router.post("/auth/verify-email", response_model=Message)
def verify(body: TokenIn, request: Request, db: Session = Depends(get_db)):
    if not limiter.hit(f"verify-ip:{client_ip(request)}", 20):
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    user = svc.consume_token(db, body.token, "email_verify")
    if not user:
        raise HTTPException(400, "This link has expired or was already used. Send a new one from StockPilot.")
    user.email_verified = True
    audit.record(db, org_id=user.org_id, user_id=user.id, action="user.email_verified", entity_type="user", entity_id=user.id,
                 summary=f"Confirmed {user.email}")
    db.commit()
    return Message(message="Email confirmed. Thank you.")


@router.post("/auth/resend-verification", response_model=Message)
def resend(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if p.user.email_verified:
        return Message(message="Your email is already confirmed.")
    if not limiter.hit(f"resend:{p.user.id}", 3):
        raise HTTPException(429, "We just sent one. Check your inbox and spam folder.")
    svc.send_verification(db, p.user)
    db.commit()
    return Message(message=f"Sent a new link to {p.user.email}.")


# ------------------------------------------------------------------ two-step verification
@router.post("/auth/2fa/setup")
def totp_setup(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Step 1: show a QR code to scan with an authenticator app."""
    try:
        out = svc.start_totp(db, p.user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return out


@router.post("/auth/2fa/enable")
def totp_enable(body: CodeIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Step 2: confirm with a code from the app. Returns recovery codes, shown once."""
    if not limiter.hit(f"2fa-enable:{p.user.id}", 6):
        raise HTTPException(429, "Too many codes tried. Wait a minute.")
    try:
        codes = svc.enable_totp(db, p.user, body.code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    p.audit(db, "user.2fa_enabled", entity_type="user", entity_id=p.user.id, summary="Turned on two-step verification")
    db.commit()
    return {"message": "Two-step verification is on.", "recovery_codes": codes}


@router.post("/auth/2fa/disable", response_model=Message)
def totp_disable(body: DisableIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if not limiter.hit(f"2fa-disable:{p.user.id}", 6):
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    if not verify_password(body.password, p.user.password_hash):
        raise HTTPException(400, "Password is incorrect")
    if not svc.verify_second_factor(db, p.user, body.code):
        raise HTTPException(400, "That code isn't right")
    svc.disable_totp(p.user)
    p.audit(db, "user.2fa_disabled", entity_type="user", entity_id=p.user.id, summary="Turned off two-step verification")
    db.commit()
    return Message(message="Two-step verification is off.")


# ------------------------------------------------------------------ alert preferences
@router.get("/me/notifications")
def get_prefs(p: Principal = Depends(current_principal)):
    prefs = messaging._prefs(p.user)
    return {**prefs, "phone": p.user.phone, "channels": messaging.channels(), "email_verified": p.user.email_verified,
            "types_available": [{"key": k, "label": v[0]} for k, v in messaging.ALERT_TYPES.items() if p.can(v[1])]}


@router.put("/me/notifications")
def set_prefs(body: PrefsIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    ch = messaging.channels()
    if body.sms and not ch["sms"] or body.whatsapp and not ch["whatsapp"]:
        raise HTTPException(422, "Text messages and WhatsApp aren't set up on this StockPilot server yet")
    phone = re.sub(r"[\s\-()]", "", body.phone or "") or None
    if (body.sms or body.whatsapp) and not (phone and re.fullmatch(r"\+[1-9]\d{6,14}", phone)):
        raise HTTPException(422, "Enter your mobile number with the country code, e.g. +2348012345678")
    types = [t for t in (body.types if body.types is not None else list(messaging.ALERT_TYPES)) if t in messaging.ALERT_TYPES]
    p.user.notification_prefs = {"email": body.email, "sms": body.sms, "whatsapp": body.whatsapp, "types": types}
    p.user.phone = phone
    p.audit(db, "user.notification_prefs", entity_type="user", entity_id=p.user.id, summary="Changed alert settings",
            after={"email": body.email, "sms": body.sms, "whatsapp": body.whatsapp})
    db.commit()
    return get_prefs(p)
