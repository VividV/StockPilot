"""Subscriptions: trial, Paystack checkout and webhook, plan limits, renewals,
the read-only lock and the operator command. Paystack itself is replaced by a fake."""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import BillingPayment, Subscription
from app.services import billing
from tests.conftest import Org

SECRET = "sk_test_fake_key_for_tests"


class FakePaystack:
    def __init__(self):
        self.calls = []
        self.charge_status = "success"
        self.paid_amount = None

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/transaction/initialize":
            self.last_init = body
            return {"status": True, "data": {"authorization_url": f"https://checkout.paystack.com/{body['reference']}",
                                             "reference": body["reference"]}}
        if path.startswith("/transaction/verify/"):
            ref = path.rsplit("/", 1)[1]
            amount = self.paid_amount if self.paid_amount is not None else self.last_init["amount"]
            return {"status": True, "data": {"status": "success", "reference": ref, "amount": amount, "currency": "NGN",
                                             "channel": "card", "customer": {"email": "owner@shop.ng"},
                                             "authorization": {"authorization_code": "AUTH_abc", "reusable": True, "last4": "4081",
                                                               "brand": "visa", "exp_month": "12", "exp_year": "2030"}}}
        if path == "/transaction/charge_authorization":
            if self.charge_status == "success":
                return {"status": True, "data": {"status": "success", "reference": body["reference"], "amount": body["amount"],
                                                 "currency": "NGN", "channel": "card", "authorization": {}}}
            return {"status": True, "data": {"status": "failed", "gateway_response": "Insufficient Funds"}}
        raise AssertionError(path)


@pytest.fixture
def on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "BILLING_ENABLED", True)
    monkeypatch.setattr(s, "PAYSTACK_SECRET_KEY", SECRET)
    monkeypatch.setattr(s, "PLAN_PRICES", "")
    fake = FakePaystack()
    monkeypatch.setattr(billing, "_paystack", fake)
    return fake


def _sub(org_id) -> Subscription:
    db = SessionLocal()
    try:
        return db.get(Subscription, uuid.UUID(org_id))
    finally:
        db.close()


def _set(org_id, **kw):
    db = SessionLocal()
    try:
        sub = db.get(Subscription, uuid.UUID(org_id))
        for k, v in kw.items():
            setattr(sub, k, v)
        db.commit()
    finally:
        db.close()


def _renew():
    db = SessionLocal()
    try:
        out = billing.run_renewals(db)
        db.commit()
        return out
    finally:
        db.close()


def test_switched_off_by_default(org):
    assert org.owner.ok("get", "/api/billing") == {"enabled": False}
    for i in range(5):
        org.user("Viewer")
    org.warehouse("Second")
    org.warehouse("Third")


def test_trial_checkout_and_limits(client, on):
    shop = Org(client, industry="retail")
    st = shop.owner.ok("get", "/api/billing")
    assert st["status"] == "trialing" and st["days_left"] == 14 and st["plan_name"] == "Free trial"
    assert [p["key"] for p in st["plans"]] == ["starter", "business", "pro"]
    co = shop.owner.ok("post", "/api/billing/checkout", json={"plan": "starter", "interval": "monthly"})
    assert co["authorization_url"].startswith("https://checkout.paystack.com/")
    assert on.last_init["amount"] == 15000 * 100 and on.last_init["callback_url"].endswith(f"/billing?reference={co['reference']}")
    v = shop.owner.ok("post", "/api/billing/verify", json={"reference": co["reference"]})
    assert v["status"] == "success"
    st = v["billing"]
    assert st["status"] == "active" and st["plan"] == "starter" and st["card"]["last4"] == "4081"
    assert 27 <= st["days_left"] <= 32
    # verifying again changes nothing
    end = st["current_period_end"]
    assert shop.owner.ok("post", "/api/billing/verify", json={"reference": co["reference"]})["billing"]["current_period_end"] == end
    # Starter: 3 people, 1 location
    shop.user("Cashier")
    shop.user("Cashier")
    r = shop.owner.post("/api/users", json={"email": "x@example.com", "full_name": "X", "password": "Test-Password-123",
                                          "role_id": shop.roles["Cashier"], "warehouse_ids": []})
    assert r.status_code == 402 and r.json()["code"] == "plan_limit"
    r = shop.owner.post("/api/warehouses", json={"name": "Second shop", "type": "store"})
    assert r.status_code == 402
    # a bigger plan can't be swapped for a smaller one you've outgrown
    r = shop.owner.post("/api/billing/checkout", json={"plan": "pro", "interval": "yearly"})
    assert r.status_code == 200 and on.last_init["amount"] == 75000 * 10 * 100


def test_underpaid_payment_is_not_accepted(client, on):
    shop = Org(client)
    co = shop.owner.ok("post", "/api/billing/checkout", json={"plan": "business", "interval": "monthly"})
    on.paid_amount = 100  # someone tampered with the amount
    v = shop.owner.ok("post", "/api/billing/verify", json={"reference": co["reference"]})
    assert v["status"] == "failed" and v["billing"]["status"] == "trialing"


def test_webhook(client, on):
    shop = Org(client)
    co = shop.owner.ok("post", "/api/billing/checkout", json={"plan": "business", "interval": "yearly"})
    event = {"event": "charge.success", "data": {"status": "success", "reference": co["reference"], "amount": 350000 * 100,
                                                 "currency": "NGN", "channel": "bank_transfer", "customer": {"email": "a@b.ng"},
                                                 "authorization": {"reusable": False}}}
    raw = json.dumps(event).encode()
    assert client.post("/api/billing/webhook", content=raw, headers={"x-paystack-signature": "bad"}).status_code == 401
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    r = client.post("/api/billing/webhook", content=raw, headers={"x-paystack-signature": sig, "content-type": "application/json"})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    r = client.post("/api/billing/webhook", content=raw, headers={"x-paystack-signature": sig, "content-type": "application/json"})
    assert r.json()["status"] == "already_applied"
    st = shop.owner.ok("get", "/api/billing")
    assert st["plan"] == "business" and st["interval"] == "yearly" and st["card"] is None and st["days_left"] > 360


def test_trial_end_makes_account_read_only(client, on):
    shop = Org(client, industry="retail")
    p = shop.product(selling_price=100)
    shop.receive(p["id"], 5)
    sh = shop.owner.ok("post", "/api/pos/shifts", json={"location_id": shop.main})
    shop.owner.ok("get", "/api/billing")
    _set(shop.org_id, trial_ends_at=datetime.now(timezone.utc) + timedelta(days=2))
    assert _renew()["reminded"] >= 1
    _set(shop.org_id, trial_ends_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert _renew()["expired"] >= 1
    assert shop.owner.ok("get", "/api/billing")["locked"] is True
    r = shop.owner.post("/api/products", json={"sku": "NEW", "name": "New", "selling_price": 1})
    assert r.status_code == 402 and "Billing page" in r.json()["detail"]
    assert shop.owner.get("/api/products").status_code == 200            # reading still works
    sale = {"shift_id": sh["id"], "lines": [{"product_id": p["id"], "quantity": 1}], "payments": [{"method": "cash", "amount": 100}]}
    assert shop.owner.post("/api/pos/sales", json=sale).status_code == 402
    # sales made offline before the lock still upload, for a week
    assert shop.owner.post("/api/pos/sales", json={**sale, "offline": True, "client_id": str(uuid.uuid4())}).status_code == 201
    _set(shop.org_id, updated_at=datetime.now(timezone.utc) - timedelta(days=8))
    assert shop.owner.post("/api/pos/sales", json={**sale, "offline": True, "client_id": str(uuid.uuid4())}).status_code == 402
    # and the owner can pay to unlock
    co = shop.owner.ok("post", "/api/billing/checkout", json={"plan": "pro", "interval": "monthly"})
    shop.owner.ok("post", "/api/billing/verify", json={"reference": co["reference"]})
    assert shop.owner.post("/api/products", json={"sku": "NEW", "name": "New", "selling_price": 1}).status_code == 201


def test_card_renewal_and_failed_renewal(client, on):
    shop = Org(client)
    co = shop.owner.ok("post", "/api/billing/checkout", json={"plan": "starter", "interval": "monthly"})
    shop.owner.ok("post", "/api/billing/verify", json={"reference": co["reference"]})
    _set(shop.org_id, current_period_end=datetime.now(timezone.utc) - timedelta(minutes=5))
    assert _renew()["renewed"] >= 1
    sub = _sub(shop.org_id)
    assert sub.status == "active" and sub.current_period_end > datetime.now(timezone.utc) + timedelta(days=25)
    assert any(c[1] == "/transaction/charge_authorization" and c[2]["authorization_code"] == "AUTH_abc" for c in on.calls)
    # the card is declined next time: grace period, then read-only
    on.charge_status = "failed"
    _set(shop.org_id, current_period_end=datetime.now(timezone.utc) - timedelta(minutes=5))
    _renew()
    assert _sub(shop.org_id).status == "past_due"
    assert shop.owner.post("/api/products", json={"sku": "A1", "name": "Still fine", "selling_price": 1}).status_code == 201
    st = shop.owner.ok("get", "/api/billing")
    assert st["payments"][0]["status"] == "failed" and "Insufficient" in st["payments"][0]["failure_reason"]
    _set(shop.org_id, current_period_end=datetime.now(timezone.utc) - timedelta(days=8))
    _renew()
    assert _sub(shop.org_id).status == "expired"


def test_cancelled_plan_ends_without_charging(client, on):
    shop = Org(client)
    co = shop.owner.ok("post", "/api/billing/checkout", json={"plan": "starter", "interval": "monthly"})
    shop.owner.ok("post", "/api/billing/verify", json={"reference": co["reference"]})
    assert shop.owner.ok("post", "/api/billing/cancel")["auto_renew"] is False
    _set(shop.org_id, current_period_end=datetime.now(timezone.utc) - timedelta(minutes=1))
    n = len(on.calls)
    _renew()
    assert _sub(shop.org_id).status == "expired" and len(on.calls) == n


def test_who_can_see_and_manage_billing(client, on):
    shop = Org(client)
    admin = shop.user("Administrator")
    assert admin.post("/api/billing/checkout", json={"plan": "starter"}).status_code == 403
    view = admin.ok("get", "/api/billing")
    assert "payments" not in view and view["status"] == "trialing"


def test_operator_can_give_a_plan(client, on, capsys):
    from app import cli
    shop = Org(client)
    cli.main(["set-plan", shop.email, "pro", "12"])
    assert "pro until" in capsys.readouterr().out
    sub = _sub(shop.org_id)
    assert sub.plan == "pro" and sub.status == "active" and sub.auto_renew is False
    for _ in range(5):
        shop.user("Viewer")  # Pro: no user limit
    cli.main(["companies"])
    assert shop.email in capsys.readouterr().out
