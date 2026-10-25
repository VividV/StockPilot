"""Subscription and payments: /api/billing/*."""
import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, current_principal, require
from app.services import billing as svc
from app.services.inventory import run_with_retry

router = APIRouter(prefix="/api/billing", tags=["billing"])
log = logging.getLogger("stockpilot.billing")


class CheckoutIn(BaseModel):
    plan: Literal["starter", "business", "pro"]
    interval: Literal["monthly", "yearly"] = "monthly"


class VerifyIn(BaseModel):
    reference: str = Field(min_length=4, max_length=64)


class EmailIn(BaseModel):
    billing_email: EmailStr


@router.get("")
def status_(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Everyone sees whether the account is active; only people who manage billing see the details."""
    if not svc.enabled():
        return {"enabled": False}
    state = svc.state_out(db, p.org)
    db.commit()  # the trial starts the first time anyone looks
    if p.can("billing.manage"):
        return state
    return {k: state[k] for k in ("enabled", "plan", "plan_name", "status", "days_left", "locked", "trial_ends_at", "current_period_end")}


@router.post("/checkout")
def checkout(body: CheckoutIn, p: Principal = Depends(require("billing.manage")), db: Session = Depends(get_db)):
    if not svc.enabled():
        raise HTTPException(409, "Billing is switched off on this server")

    def op():
        out = svc.checkout(db, p.org, p.user, body.plan, body.interval)
        p.audit(db, "billing.checkout", entity_type="subscription", entity_id=p.org_id,
                summary=f"Started paying for {body.plan} ({body.interval}): {out['amount']} {out['currency']}")
        return out
    return run_with_retry(db, op)


@router.post("/verify")
def verify(body: VerifyIn, p: Principal = Depends(require("billing.manage")), db: Session = Depends(get_db)):
    def op():
        r = svc.verify(db, p.org_id, body.reference)
        if r["status"] == "success":
            p.audit(db, "billing.paid", entity_type="subscription", entity_id=p.org_id, summary=f"Payment {body.reference} confirmed")
        return {**r, "billing": svc.state_out(db, p.org)}
    return run_with_retry(db, op)


@router.post("/cancel")
def cancel(p: Principal = Depends(require("billing.manage")), db: Session = Depends(get_db)):
    svc.cancel(db, p.org_id)
    p.audit(db, "billing.cancel", entity_type="subscription", entity_id=p.org_id, summary="Turned off automatic renewal")
    db.commit()
    return svc.state_out(db, p.org)


@router.post("/resume")
def resume(p: Principal = Depends(require("billing.manage")), db: Session = Depends(get_db)):
    sub = svc.get_sub(db, p.org_id)
    sub.auto_renew = True
    p.audit(db, "billing.resume", entity_type="subscription", entity_id=p.org_id, summary="Turned automatic renewal back on")
    db.commit()
    return svc.state_out(db, p.org)


@router.put("/email")
def set_email(body: EmailIn, p: Principal = Depends(require("billing.manage")), db: Session = Depends(get_db)):
    sub = svc.get_sub(db, p.org_id)
    sub.billing_email = str(body.billing_email).lower()
    p.audit(db, "billing.email", entity_type="subscription", entity_id=p.org_id, summary=f"Billing email set to {sub.billing_email}")
    db.commit()
    return svc.state_out(db, p.org)


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, db: Session = Depends(get_db)):
    """Paystack calls this. Set the URL in the Paystack dashboard: <API address>/api/billing/webhook."""
    raw = await request.body()
    if not svc.verify_signature(raw, request.headers.get("x-paystack-signature")):
        raise HTTPException(401, "Bad signature")
    try:
        event = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Bad JSON")
    result = run_with_retry(db, lambda: svc.handle_webhook(db, event))
    log.info("paystack webhook %s: %s", event.get("event"), result)
    return {"status": result}
