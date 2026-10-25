from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_db
from app.deps import Principal, client_ip, current_principal
from app.models import User, UserSession
from app.schemas import ChangePasswordIn, LoginIn, MeOut, Message, OrgOut, SignupIn, TokenOut
from pydantic import BaseModel, Field
from app.security import (
    DUMMY_HASH, create_access_token, create_challenge_token, decode_challenge_token, hash_password, hash_token, limiter, new_opaque_token,
    validate_password_strength, verify_password,
)
from app.services.industry import INDUSTRIES
from app.services.org import bootstrap_organization

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TwoFactorLoginIn(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=20)
settings = get_settings()
COOKIE = "sp_refresh"
CSRF_HEADER = "x-requested-with"


def _set_refresh_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, max_age=settings.REFRESH_TOKEN_DAYS * 86400, httponly=True,
                    secure=settings.COOKIE_SECURE, samesite="strict", path="/api/auth")


def _start_session(db: Session, user: User, request: Request, resp: Response) -> TokenOut:
    token, token_hash = new_opaque_token()
    db.add(UserSession(user_id=user.id, token_hash=token_hash,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_DAYS),
                       ip_address=client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:255]))
    _set_refresh_cookie(resp, token)
    return TokenOut(access_token=create_access_token(user.id, user.org_id), expires_in=settings.ACCESS_TOKEN_MINUTES * 60)


def _csrf(request: Request) -> None:
    # Cookie-authenticated endpoints need a custom header a cross-site form cannot send.
    if request.headers.get(CSRF_HEADER) != "StockPilot":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")


@router.get("/industries")
def industries() -> dict[str, str]:
    """Options for the 'What type of business do you run?' step."""
    return INDUSTRIES


@router.post("/signup", response_model=TokenOut, status_code=201)
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not limiter.hit(f"signup:{client_ip(request)}", 5):
        raise HTTPException(429, "Too many sign-ups from this address. Try again in a minute.")
    try:
        validate_password_strength(body.password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if db.execute(select(User.id).where(User.email == body.email.lower())).first():
        raise HTTPException(409, "An account with this email already exists")
    try:
        org, owner = bootstrap_organization(db, org_name=body.organization_name, industry=body.industry,
                                            currency=body.currency, owner_name=body.full_name,
                                            owner_email=body.email, owner_password=body.password,
                                            timezone=body.timezone)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with this email already exists")
    audit.record(db, org_id=org.id, user_id=owner.id, action="org.signup", entity_type="organization",
                 entity_id=org.id, summary=f"{org.name} created ({org.industry})",
                 ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    from app.services.account import send_verification
    send_verification(db, owner, welcome=True)
    out = _start_session(db, owner, request, response)
    db.commit()
    return out


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    email = body.email.lower()
    if not (limiter.hit(f"login-ip:{ip}", settings.LOGIN_RATE_LIMIT_PER_MINUTE * 3)
            and limiter.hit(f"login-email:{email}", settings.LOGIN_RATE_LIMIT_PER_MINUTE)):
        raise HTTPException(429, "Too many sign-in attempts. Wait a minute and try again.")
    user = db.execute(select(User).where(User.email == email)).unique().scalar_one_or_none()
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not ok or not user.is_active:
        if user:
            audit.record(db, org_id=user.org_id, user_id=user.id, action="auth.login_failed",
                         entity_type="user", entity_id=user.id, summary="Wrong password" if not ok else "Account disabled",
                         ip=ip, user_agent=request.headers.get("user-agent"))
            db.commit()
        raise HTTPException(401, "Email or password is incorrect")
    if user.totp_enabled:
        # password was right; the authenticator code finishes the sign-in at /login/2fa
        return TokenOut(two_factor_required=True, challenge_token=create_challenge_token(user.id))
    return _finish_login(db, user, request, response, "password")


def _finish_login(db: Session, user: User, request: Request, response: Response, method: str) -> TokenOut:
    user.last_login_at = datetime.now(timezone.utc)
    audit.record(db, org_id=user.org_id, user_id=user.id, action="auth.login", entity_type="user", entity_id=user.id,
                 summary=f"{user.full_name} signed in" + (" with two-step verification" if method != "password" else ""),
                 ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    out = _start_session(db, user, request, response)
    db.commit()
    return out


@router.post("/login/2fa", response_model=TokenOut)
def login_2fa(body: TwoFactorLoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    from app.services.account import verify_second_factor
    try:
        user_id = decode_challenge_token(body.challenge_token)
    except Exception:
        raise HTTPException(401, "That took too long. Sign in again.")
    if not limiter.hit(f"2fa:{user_id}", 6):
        raise HTTPException(429, "Too many codes tried. Wait a minute.")
    user = db.get(User, user_id)
    if not user or not user.is_active or not user.totp_enabled:
        raise HTTPException(401, "Sign in again")
    how = verify_second_factor(db, user, body.code)
    if not how:
        audit.record(db, org_id=user.org_id, user_id=user.id, action="auth.login_failed", entity_type="user", entity_id=user.id,
                     summary="Wrong two-step code", ip=client_ip(request), user_agent=request.headers.get("user-agent"))
        db.commit()
        raise HTTPException(401, "That code isn't right. Use the 6-digit code from your authenticator app, or a recovery code.")
    return _finish_login(db, user, request, response, how)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    _csrf(request)
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not signed in")
    s = db.execute(select(UserSession).where(UserSession.token_hash == hash_token(token)).with_for_update()).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not s or s.revoked_at or s.expires_at < now:
        response.delete_cookie(COOKIE, path="/api/auth")
        raise HTTPException(401, "Session expired, please sign in again")
    user = db.get(User, s.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "Account disabled")
    s.revoked_at = now  # rotate: each refresh token works once
    out = _start_session(db, user, request, response)
    db.commit()
    return out


@router.post("/logout", response_model=Message)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    _csrf(request)
    token = request.cookies.get(COOKIE)
    if token:
        s = db.execute(select(UserSession).where(UserSession.token_hash == hash_token(token))).scalar_one_or_none()
        if s and not s.revoked_at:
            s.revoked_at = datetime.now(timezone.utc)
            db.commit()
    response.delete_cookie(COOKIE, path="/api/auth")
    return Message(message="Signed out")


@router.get("/me", response_model=MeOut)
def me(p: Principal = Depends(current_principal)):
    return MeOut(id=p.user.id, email=p.user.email, full_name=p.user.full_name, role=p.user.role.name,
                 email_verified=p.user.email_verified, totp_enabled=p.user.totp_enabled, phone=p.user.phone,
                 notification_prefs=p.user.notification_prefs or {},
                 permissions=sorted(p.permissions), warehouse_ids=sorted(p.user.warehouse_ids, key=str),
                 organization=OrgOut.model_validate(p.org))


@router.post("/change-password", response_model=Message)
def change_password(body: ChangePasswordIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, p.user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    try:
        validate_password_strength(body.new_password)
    except ValueError as e:
        raise HTTPException(422, str(e))
    p.user.password_hash = hash_password(body.new_password)
    # sign out every other session
    for s in db.execute(select(UserSession).where(UserSession.user_id == p.user.id, UserSession.revoked_at.is_(None))).scalars():
        s.revoked_at = datetime.now(timezone.utc)
    p.audit(db, "user.password_change", entity_type="user", entity_id=p.user.id, summary="Password changed")
    db.commit()
    return Message(message="Password changed. Other devices have been signed out.")
