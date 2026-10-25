"""Subscriptions: plans, the free trial, Paystack payments and plan limits.

Everything here is switched off unless ``BILLING_ENABLED`` is true. When it is
off every company has every feature, with no trial and no payments, which is
right for a business running StockPilot for itself.

How money moves
---------------
* Checkout: we create a ``billing_payments`` row (pending) with our own
  reference, ask Paystack for a payment page and send the owner there.
* Paystack then calls our webhook (``charge.success``) and also sends the owner
  back to ``/billing?reference=…``, where we verify the reference with Paystack.
  Either path activates the plan; doing it twice is harmless.
* Renewal: if the customer paid by card, Paystack gives a reusable
  authorisation and the worker charges it when the period ends. Bank transfer
  and USSD payments are not reusable, so those customers get a reminder and pay
  again from the Billing page.
* Missed payment: ``past_due`` for GRACE_DAYS (still fully usable), then
  ``expired``: the account becomes read-only until someone pays. Nothing is
  ever deleted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BillingPayment, Organization, Subscription, User, Warehouse
from app.services.inventory import InventoryError, _resolve_alerts, _upsert_alert

log = logging.getLogger("stockpilot.billing")

PLANS: dict[str, dict] = {
    "starter": {"name": "Starter", "users": 3, "locations": 1, "price": 15000,
                "blurb": "One shop, up to 3 staff"},
    "business": {"name": "Business", "users": 10, "locations": 3, "price": 35000,
                 "blurb": "Up to 3 shops or stores, 10 staff"},
    "pro": {"name": "Pro", "users": None, "locations": None, "price": 75000,
            "blurb": "Unlimited shops and staff"},
}
TRIAL_LIMITS = {"users": None, "locations": None}
INTERVALS = {"monthly": 1, "yearly": 12}
LOCKED = ("expired", "cancelled")


class BillingError(InventoryError):
    status_code = 402


def now() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    return get_settings().BILLING_ENABLED


def plan_price(plan: str, interval: str) -> Decimal:
    prices = {k: Decimal(v["price"]) for k, v in PLANS.items()}
    for part in (get_settings().PLAN_PRICES or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() in prices:
                prices[k.strip()] = Decimal(v.strip())
    monthly = prices[plan]
    return monthly * 10 if interval == "yearly" else monthly


def plans_out() -> list[dict]:
    cur = get_settings().BILLING_CURRENCY
    return [{"key": k, "name": v["name"], "blurb": v["blurb"], "users": v["users"], "locations": v["locations"],
             "monthly": plan_price(k, "monthly"), "yearly": plan_price(k, "yearly"), "currency": cur}
            for k, v in PLANS.items()]


# ------------------------------------------------------------------ subscription state
def start_trial(db: Session, org_id, email: str | None = None) -> Subscription:
    sub = db.get(Subscription, org_id)
    if sub:
        return sub
    sub = Subscription(org_id=org_id, plan="trial", status="trialing", interval="monthly", billing_email=email,
                       trial_ends_at=now() + timedelta(days=get_settings().TRIAL_DAYS), auto_renew=True, authorization={})
    db.add(sub)
    db.flush()
    return sub


def get_sub(db: Session, org_id) -> Subscription:
    sub = db.get(Subscription, org_id)
    if sub is None:  # organisations created before billing existed
        sub = start_trial(db, org_id)
    return sub


def limits(sub: Subscription) -> dict:
    if not enabled():
        return {"users": None, "locations": None}
    if sub.plan in PLANS and sub.status not in ("trialing",):
        return {"users": PLANS[sub.plan]["users"], "locations": PLANS[sub.plan]["locations"]}
    return dict(TRIAL_LIMITS)


def is_locked(sub: Subscription | None) -> bool:
    return enabled() and sub is not None and sub.status in LOCKED


def usage(db: Session, org_id) -> dict:
    users = db.execute(select(func.count()).select_from(User).where(User.org_id == org_id, User.is_active.is_(True))).scalar_one()
    locs = db.execute(select(func.count()).select_from(Warehouse).where(Warehouse.org_id == org_id, Warehouse.is_active.is_(True))).scalar_one()
    return {"users": users, "locations": locs}


def check_limit(db: Session, org_id, what: str, adding: int = 1) -> None:
    """Raise if adding ``adding`` more users / locations would go over the plan."""
    if not enabled():
        return
    sub = get_sub(db, org_id)
    cap = limits(sub)[what]
    if cap is None:
        return
    used = usage(db, org_id)[what]
    if used + adding > cap:
        noun = "active team members" if what == "users" else "locations (shops, stores and warehouses)"
        raise BillingError(f"Your {PLANS[sub.plan]['name']} plan allows {cap} {noun}. Upgrade on the Billing page to add more.",
                           code="plan_limit", limit=cap, used=used)


def state_out(db: Session, org: Organization) -> dict:
    s = get_settings()
    if not enabled():
        return {"enabled": False}
    sub = get_sub(db, org.id)
    days_left = None
    end = sub.trial_ends_at if sub.status == "trialing" else sub.current_period_end
    if end:
        days_left = max(0, (end - now()).days + (1 if (end - now()).seconds > 0 else 0))
    payments = db.execute(select(BillingPayment).where(BillingPayment.org_id == org.id)
                          .order_by(BillingPayment.created_at.desc()).limit(24)).scalars().all()
    auth = sub.authorization or {}
    return {
        "enabled": True, "configured": bool(s.PAYSTACK_SECRET_KEY), "plan": sub.plan,
        "plan_name": PLANS.get(sub.plan, {}).get("name", "Free trial"), "status": sub.status, "interval": sub.interval,
        "trial_ends_at": sub.trial_ends_at, "current_period_end": sub.current_period_end, "days_left": days_left,
        "auto_renew": sub.auto_renew, "locked": is_locked(sub), "billing_email": sub.billing_email,
        "card": {"brand": auth.get("brand"), "last4": auth.get("last4"), "exp": f"{auth.get('exp_month')}/{auth.get('exp_year')}"}
        if auth.get("reusable") else None,
        "limits": limits(sub), "usage": usage(db, org.id), "plans": plans_out(),
        "grace_ends_at": (sub.current_period_end + timedelta(days=s.GRACE_DAYS)) if sub.status == "past_due" and sub.current_period_end else None,
        "payments": [{"reference": x.reference, "plan": x.plan, "interval": x.interval, "amount": x.amount, "currency": x.currency,
                      "status": x.status, "kind": x.kind, "channel": x.channel, "paid_at": x.paid_at, "created_at": x.created_at,
                      "failure_reason": x.failure_reason} for x in payments],
    }


# ------------------------------------------------------------------ Paystack
class PaystackError(Exception):
    pass


def _paystack(method: str, path: str, body: dict | None = None) -> dict:
    """Call the Paystack API. Tests replace this function."""
    key = get_settings().PAYSTACK_SECRET_KEY
    if not key:
        raise PaystackError("Payments are not set up yet (PAYSTACK_SECRET_KEY is empty)")
    req = urllib.request.Request(f"{get_settings().PAYSTACK_API_URL.rstrip('/')}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("message")
        except ValueError:
            msg = None
        raise PaystackError(msg or f"Paystack error {e.code}")
    except OSError as e:
        raise PaystackError(f"Could not reach Paystack: {e}")


def new_reference() -> str:
    return "SP-" + secrets.token_hex(10).upper()


def checkout(db: Session, org: Organization, user: User, plan: str, interval: str) -> dict:
    if plan not in PLANS or interval not in INTERVALS:
        raise InventoryError("Choose a plan and how often to pay", code="invalid_plan")
    sub = get_sub(db, org.id)
    use = usage(db, org.id)
    cap = PLANS[plan]
    if (cap["users"] is not None and use["users"] > cap["users"]) or (cap["locations"] is not None and use["locations"] > cap["locations"]):
        raise InventoryError(f"You have {use['users']} active team members and {use['locations']} shops/stores, more than the "
                             f"{cap['name']} plan allows. Choose a bigger plan or deactivate some first.", code="over_plan_limit")
    s = get_settings()
    amount = plan_price(plan, interval)
    ref = new_reference()
    email = sub.billing_email or user.email
    pay = BillingPayment(org_id=org.id, reference=ref, kind="checkout", plan=plan, interval=interval, amount=amount,
                         currency=s.BILLING_CURRENCY, status="pending", created_by=user.id)
    db.add(pay)
    db.flush()
    try:
        r = _paystack("POST", "/transaction/initialize", {
            "email": email, "amount": int(amount * 100), "currency": s.BILLING_CURRENCY, "reference": ref,
            "callback_url": f"{s.APP_URL.rstrip('/')}/billing?reference={ref}",
            "metadata": {"org_id": str(org.id), "plan": plan, "interval": interval,
                         "custom_fields": [{"display_name": "Business", "variable_name": "business", "value": org.name}]},
        })
    except PaystackError as e:
        raise InventoryError(str(e), code="payment_unavailable")
    if not r.get("status"):
        raise InventoryError(r.get("message") or "Paystack refused the payment", code="payment_unavailable")
    return {"reference": ref, "authorization_url": r["data"]["authorization_url"], "amount": amount, "currency": s.BILLING_CURRENCY}


def _apply_success(db: Session, pay: BillingPayment, data: dict) -> bool:
    """Mark a payment as paid and extend the subscription. Safe to call twice."""
    if pay.status == "success":
        return False
    expected = int(Decimal(pay.amount) * 100)
    if int(data.get("amount") or 0) < expected or (data.get("currency") or pay.currency) != pay.currency:
        pay.status = "failed"
        pay.failure_reason = "Amount or currency did not match"
        log.warning("billing: amount mismatch on %s: %s", pay.reference, data.get("amount"))
        return False
    pay.status = "success"
    pay.channel = data.get("channel")
    pay.paid_at = now()
    sub = get_sub(db, pay.org_id)
    months = INTERVALS[pay.interval]
    # Same plan and still paid up: add the time on. Otherwise the new period starts today.
    start = sub.current_period_end if (sub.plan == pay.plan and sub.status in ("active", "past_due")
                                       and sub.current_period_end and sub.current_period_end > now()) else now()
    if sub.status == "past_due" and sub.plan == pay.plan and sub.current_period_end:
        start = max(sub.current_period_end, now() - timedelta(days=get_settings().GRACE_DAYS))
    sub.plan = pay.plan
    sub.interval = pay.interval
    sub.status = "active"
    sub.current_period_end = _add_months(start, months)
    sub.auto_renew = True
    sub.reminded_at = None
    auth = data.get("authorization") or {}
    if auth.get("reusable") and auth.get("authorization_code"):
        sub.authorization = {k: auth.get(k) for k in ("authorization_code", "last4", "brand", "exp_month", "exp_year", "reusable", "signature")}
        sub.authorization["email"] = (data.get("customer") or {}).get("email") or sub.billing_email
    if not sub.billing_email:
        sub.billing_email = (data.get("customer") or {}).get("email")
    _resolve_alerts(db, pay.org_id, ["billing"], "subscription")
    db.flush()
    return True


def _add_months(d: datetime, months: int) -> datetime:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def verify(db: Session, org_id, reference: str) -> dict:
    pay = db.execute(select(BillingPayment).where(BillingPayment.reference == reference, BillingPayment.org_id == org_id)
                     .with_for_update()).scalar_one_or_none()
    if not pay:
        raise InventoryError("Payment not found", code="not_found")
    if pay.status != "success":
        try:
            r = _paystack("GET", f"/transaction/verify/{reference}")
        except PaystackError as e:
            raise InventoryError(str(e), code="payment_unavailable")
        data = r.get("data") or {}
        if data.get("status") == "success":
            _apply_success(db, pay, data)
        elif data.get("status") in ("failed", "abandoned", "reversed"):
            pay.status = "failed"
            pay.failure_reason = data.get("gateway_response") or data.get("status")
    return {"reference": pay.reference, "status": pay.status}


def verify_signature(raw: bytes, signature: str | None) -> bool:
    key = get_settings().PAYSTACK_SECRET_KEY
    if not key or not signature:
        return False
    digest = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


def handle_webhook(db: Session, event: dict) -> str:
    if event.get("event") != "charge.success":
        return "ignored"
    data = event.get("data") or {}
    ref = data.get("reference")
    pay = db.execute(select(BillingPayment).where(BillingPayment.reference == ref).with_for_update()).scalar_one_or_none() if ref else None
    if not pay:
        return "unknown_reference"
    if data.get("status") != "success":
        return "not_successful"
    return "applied" if _apply_success(db, pay, data) else "already_applied"


def cancel(db: Session, org_id) -> Subscription:
    sub = get_sub(db, org_id)
    sub.auto_renew = False
    db.flush()
    return sub


# ------------------------------------------------------------------ scheduled (worker)
def _alert(db: Session, org_id, severity: str, title: str, body: str) -> None:
    _resolve_alerts(db, org_id, ["billing"], "subscription")
    _upsert_alert(db, org_id, type_="billing", severity=severity, title=title, body=body,
                  entity_type="subscription", entity_id="subscription")


def run_renewals(db: Session) -> dict:
    """Trials ending, renewals due, grace periods running out. Called by the worker."""
    stats = {"renewed": 0, "past_due": 0, "expired": 0, "reminded": 0}
    if not enabled():
        return stats
    s = get_settings()
    t = now()
    for sub in db.execute(select(Subscription).with_for_update(skip_locked=True)).scalars().all():
        cur = s.BILLING_CURRENCY
        if sub.status == "trialing" and sub.trial_ends_at:
            if sub.trial_ends_at <= t:
                sub.status = "expired"
                stats["expired"] += 1
                _alert(db, sub.org_id, "critical", "Your free trial has ended",
                       "Your data is safe, but nothing can be changed until you choose a plan on the Billing page.")
            elif sub.trial_ends_at - t <= timedelta(days=3) and not sub.reminded_at:
                sub.reminded_at = t
                stats["reminded"] += 1
                days = max(1, (sub.trial_ends_at - t).days)
                _alert(db, sub.org_id, "warning", f"Your free trial ends in {days} day{'s' if days != 1 else ''}",
                       "Choose a plan on the Billing page to keep selling without a break.")
            continue
        if sub.status == "active" and sub.current_period_end and sub.current_period_end <= t:
            if not sub.auto_renew:
                sub.status = "expired"
                stats["expired"] += 1
                _alert(db, sub.org_id, "critical", "Your subscription has ended",
                       "Nothing can be changed until you choose a plan again on the Billing page. Your data is safe.")
                continue
            auth = sub.authorization or {}
            ok = False
            if auth.get("reusable") and auth.get("authorization_code") and sub.plan in PLANS:
                amount = plan_price(sub.plan, sub.interval)
                pay = BillingPayment(org_id=sub.org_id, reference=new_reference(), kind="renewal", plan=sub.plan,
                                     interval=sub.interval, amount=amount, currency=cur, status="pending")
                db.add(pay)
                db.flush()
                try:
                    r = _paystack("POST", "/transaction/charge_authorization", {
                        "email": auth.get("email") or sub.billing_email, "amount": int(amount * 100), "currency": cur,
                        "authorization_code": auth["authorization_code"], "reference": pay.reference})
                    data = r.get("data") or {}
                    if data.get("status") == "success":
                        ok = _apply_success(db, pay, data)
                    else:
                        pay.status = "failed"
                        pay.failure_reason = (data.get("gateway_response") or r.get("message") or "declined")[:255]
                except PaystackError as e:
                    pay.status = "failed"
                    pay.failure_reason = str(e)[:255]
            if ok:
                stats["renewed"] += 1
            else:
                sub.status = "past_due"
                stats["past_due"] += 1
                _alert(db, sub.org_id, "warning", "Your StockPilot payment is due",
                       f"Pay on the Billing page within {s.GRACE_DAYS} days to keep making changes.")
            continue
        if sub.status == "past_due" and sub.current_period_end and sub.current_period_end + timedelta(days=s.GRACE_DAYS) <= t:
            sub.status = "expired"
            stats["expired"] += 1
            _alert(db, sub.org_id, "critical", "Your subscription has ended",
                   "Nothing can be changed until you pay on the Billing page. Your data is safe.")
    db.flush()
    return stats


def set_plan(db: Session, org_id, plan: str, months: int, interval: str = "monthly") -> Subscription:
    """For the operator: give a company a plan without payment (pilots, friends, refunds)."""
    if plan not in PLANS:
        raise ValueError(f"plan must be one of {', '.join(PLANS)}")
    sub = get_sub(db, org_id)
    sub.plan = plan
    sub.interval = interval
    sub.status = "active"
    sub.current_period_end = _add_months(now(), months)
    sub.auto_renew = False
    _resolve_alerts(db, org_id, ["billing"], "subscription")
    db.flush()
    return sub
