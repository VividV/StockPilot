"""Counter sales: checkout, payments, offline upload, discounts, promotions,
returns, store credit, till shifts and cash-up, and the owner's day summary."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tests.conftest import Org

def D(v):
    return Decimal(str(v))


@pytest.fixture
def shop(client) -> Org:
    return Org(client, industry="retail")


def open_shift(api, shop, float_=0, location=None):
    return api.ok("post", "/api/pos/shifts", json={"location_id": location or shop.main, "opening_float": float_})


def sell(api, shift, lines, payments=None, status=(200, 201), **kw):
    body = {"shift_id": shift["id"], "lines": lines, "payments": payments if payments is not None else [], **kw}
    r = api.post("/api/pos/sales", json=body)
    if status is None:
        return r
    assert r.status_code in (status if isinstance(status, tuple) else (status,)), r.text
    return r.json()


def test_counter_sale_takes_stock_and_gives_change(client, shop):
    p = shop.product(name="Body lotion", selling_price=1500, cost_price=900, tax_rate=7.5)
    shop.receive(p["id"], 10, unit_cost=900)
    sh = open_shift(shop.owner, shop, 5000)
    s = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 2}], [{"method": "cash", "amount": 5000}])
    assert s["number"].startswith("R-")
    assert D(s["total"]) == D("3000.00")
    assert D(s["tax"]) == D("209.30")           # VAT is inside the price: 3000 x 7.5 / 107.5
    assert D(s["change_given"]) == D("2000.00")
    assert [(x["method"], D(x["amount"])) for x in s["payments"]] == [("cash", D("3000.00"))]
    assert D(s["cost_of_goods"]) == D("1800.00")
    assert D(s["profit"]) == D("3000.00") - D("209.30") - D("1800.00")
    assert shop.on_hand(p["id"]) == 8
    mv = shop.owner.ok("get", f"/api/products/{p['id']}")["recent_movements"][0]
    assert mv["movement_type"] == "sale" and mv["reference_no"] == s["number"] and mv["counterparty"] == "Walk-in customer"
    # the customer's link works without signing in and shows no internal ids
    token = s["receipt_url"].rsplit("/", 1)[1]
    pub = client.get(f"/api/public/receipts/{token}")
    assert pub.status_code == 200 and pub.json()["number"] == s["number"]
    assert "cost_of_goods" not in pub.json() and "client_id" not in pub.json() and "customer_phone" not in pub.json()
    assert client.get("/api/public/receipts/not-a-real-token-123456").status_code == 404


def test_shift_rules(shop):
    p = shop.product()
    shop.receive(p["id"], 5)
    r = shop.owner.post("/api/pos/sales", json={"lines": [{"product_id": p["id"], "quantity": 1}],
                                               "payments": [{"method": "cash", "amount": 15}]})
    assert r.status_code == 400 and r.json()["code"] == "no_shift"
    open_shift(shop.owner, shop)
    r = shop.owner.post("/api/pos/shifts", json={"location_id": shop.main})
    assert r.status_code == 400 and r.json()["code"] == "shift_open"
    assert shop.owner.ok("get", "/api/pos/shifts/current")["status"] == "open"


def test_module_must_be_on(org):
    r = org.owner.post("/api/pos/shifts", json={"location_id": org.main})
    assert r.status_code == 409


def test_split_payment_underpay_and_overpay(shop):
    p = shop.product(selling_price=1000)
    shop.receive(p["id"], 10)
    sh = open_shift(shop.owner, shop)
    line = [{"product_id": p["id"], "quantity": 3}]
    r = sell(shop.owner, sh, line, [{"method": "transfer", "amount": 1000}, {"method": "cash", "amount": 1000}], status=None)
    assert r.status_code == 400 and r.json()["code"] == "underpaid" and "1000" in r.json()["detail"]
    r = sell(shop.owner, sh, line, [{"method": "card", "amount": 3500}], status=None)
    assert r.status_code == 400 and r.json()["code"] == "overpaid"
    r = sell(shop.owner, sh, line, [{"method": "other", "amount": 3000}], status=None)
    assert r.status_code == 400 and r.json()["code"] == "invalid_method"
    s = sell(shop.owner, sh, line, [{"method": "transfer", "amount": 1000, "reference": "GTB 8812"},
                                    {"method": "cash", "amount": 2500}])
    assert D(s["change_given"]) == 500
    assert sorted((x["method"], D(x["amount"])) for x in s["payments"]) == [("cash", D(2000)), ("transfer", D(1000))]
    assert shop.on_hand(p["id"]) == 7  # the refused attempts took nothing


def test_not_enough_stock_online_is_refused_offline_is_saved_and_flagged(shop):
    p = shop.product(name="Hair cream", selling_price=500)
    shop.receive(p["id"], 2)
    sh = open_shift(shop.owner, shop)
    r = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 5}], [{"method": "cash", "amount": 2500}], status=None)
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"
    assert shop.on_hand(p["id"]) == 2
    cid = str(uuid.uuid4())
    s = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 5}], [{"method": "cash", "amount": 2500}],
             offline=True, client_id=cid, sold_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    assert s["needs_review"] and "below zero" in s["review_note"]
    assert shop.on_hand(p["id"]) == -3
    alerts = shop.owner.ok("get", "/api/notifications")
    items = alerts["items"] if isinstance(alerts, dict) else alerts
    assert any(a["type"] == "pos_review" and s["number"] in a["title"] for a in items)
    # the manager checks it
    s2 = shop.owner.ok("post", f"/api/pos/sales/{s['number']}/reviewed")
    assert s2["needs_review"] is False


def test_offline_upload_is_safe_to_repeat(shop):
    p = shop.product(selling_price=200)
    shop.receive(p["id"], 10)
    sh = open_shift(shop.owner, shop)
    body = dict(offline=True, client_id=str(uuid.uuid4()))
    a = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 4}], [{"method": "cash", "amount": 800}], **body)
    b = shop.owner.post("/api/pos/sales", json={"shift_id": sh["id"], "lines": [{"product_id": p["id"], "quantity": 4}],
                                               "payments": [{"method": "cash", "amount": 800}], **body})
    assert b.status_code == 200 and b.json()["duplicate"] is True and b.json()["number"] == a["number"]
    assert shop.on_hand(p["id"]) == 6


def test_offline_batch_item_with_no_stock_is_saved_without_taking_stock(client):
    shop = Org(client, industry="healthcare")
    p = shop.product(name="Amoxicillin", selling_price=800, track_batches=True, track_expiry=True)
    sh = open_shift(shop.owner, shop)
    s = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 1}], [{"method": "cash", "amount": 800}],
             offline=True, client_id=str(uuid.uuid4()))
    assert s["needs_review"] and s["lines"][0]["stock_deducted"] is False
    assert "not taken off stock" in s["review_note"]
    assert shop.on_hand(p["id"]) == 0


def test_offline_sale_uploaded_after_shift_closed(shop):
    p = shop.product(selling_price=100)
    shop.receive(p["id"], 5)
    sh = open_shift(shop.owner, shop)
    shop.owner.ok("post", f"/api/pos/shifts/{sh['id']}/close", json={"counted_cash": 0})
    r = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 1}], [{"method": "cash", "amount": 100}], status=None)
    assert r.status_code == 400 and r.json()["code"] == "shift_closed"
    s = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 1}], [{"method": "cash", "amount": 100}],
             offline=True, client_id=str(uuid.uuid4()))
    assert s["needs_review"] and "after shift" in s["review_note"]
    detail = shop.owner.ok("get", f"/api/pos/shifts/{sh['id']}")
    assert detail["summary"]["synced_after_close"] == 1


def test_discount_permissions_and_limit(shop):
    p = shop.product(selling_price=10000)
    shop.receive(p["id"], 50)
    cashier = shop.user("Cashier")
    sh = open_shift(cashier, shop)
    line = {"product_id": p["id"], "quantity": 1}
    r = sell(cashier, sh, [{**line, "discount": {"type": "percent", "value": 10}}], [{"method": "cash", "amount": 9000}], status=None)
    assert r.status_code == 400 and r.json()["code"] == "discount_not_allowed"
    r = sell(cashier, sh, [{**line, "unit_price": 8000}], [{"method": "cash", "amount": 8000}], status=None)
    assert r.status_code == 400 and "price" in r.json()["detail"]
    # offline it is saved but flagged
    s = sell(cashier, sh, [{**line, "discount": {"type": "amount", "value": 1000}}], [{"method": "cash", "amount": 9000}],
             offline=True, client_id=str(uuid.uuid4()))
    assert s["needs_review"] and "permission" in s["review_note"]

    # a role that may discount, up to the shop limit (20% by default)
    role = shop.owner.ok("post", "/api/roles", json={"name": "Senior cashier", "permissions": ["pos.sell", "pos.discount", "products.view"]})
    shop.roles["Senior cashier"] = role["id"]
    senior = shop.user("Senior cashier")
    sh2 = open_shift(senior, shop)
    ok = sell(senior, sh2, [{**line, "discount": {"type": "percent", "value": 20}}], [{"method": "cash", "amount": 8000}])
    assert D(ok["total"]) == 8000 and D(ok["discount"]) == 2000
    r = sell(senior, sh2, [line], [{"method": "cash", "amount": 7000}], cart_discount={"type": "amount", "value": 3000}, status=None)
    assert r.status_code == 400 and "limit of 20" in r.json()["detail"]
    shop.owner.ok("put", "/api/settings", json={"pos": {"max_discount_percent": 30}})
    sell(senior, sh2, [line], [{"method": "cash", "amount": 7000}], cart_discount={"type": "amount", "value": 3000})
    # managers can change prices freely
    mgr = shop.user("Manager")
    sh3 = open_shift(mgr, shop)
    s = sell(mgr, sh3, [{**line, "unit_price": 6000}], [{"method": "card", "amount": 6000}])
    assert D(s["lines"][0]["list_price"]) == 10000 and D(s["total"]) == 6000


def test_promotions_apply_automatically(shop):
    cat = shop.owner.ok("post", "/api/categories", json={"name": "Skincare"})
    cream = shop.product(name="Cream", selling_price=2000, category_id=cat["id"])
    soap = shop.product(name="Soap", selling_price=500)
    for x in (cream, soap):
        shop.receive(x["id"], 50)
    today = date.today()
    shop.owner.ok("post", "/api/promotions", json={"name": "Skincare week", "kind": "percent", "value": 10, "scope": "category",
                                                  "category_id": cat["id"], "starts_on": str(today - timedelta(days=1)),
                                                  "ends_on": str(today + timedelta(days=5))})
    shop.owner.ok("post", "/api/promotions", json={"name": "Soap 3+", "kind": "amount", "value": 100, "scope": "products",
                                                  "product_ids": [soap["id"]], "min_qty": 3})
    shop.owner.ok("post", "/api/promotions", json={"name": "Expired", "kind": "percent", "value": 50, "scope": "all",
                                                  "ends_on": str(today - timedelta(days=2))})
    # the better of two running offers wins
    shop.owner.ok("post", "/api/promotions", json={"name": "Cream special", "kind": "amount", "value": 300, "scope": "products",
                                                  "product_ids": [cream["id"]]})
    sh = open_shift(shop.owner, shop)
    s = sell(shop.owner, sh, [{"product_id": cream["id"], "quantity": 1}, {"product_id": soap["id"], "quantity": 2}],
             [{"method": "cash", "amount": 2700}])
    assert s["lines"][0]["promotion_name"] == "Cream special" and D(s["lines"][0]["discount"]) == 300
    assert s["lines"][1]["promotion_name"] is None   # only 2 soaps: offer needs 3
    assert D(s["total"]) == 1700 + 1000
    s = sell(shop.owner, sh, [{"product_id": cream["id"], "quantity": 3}, {"product_id": soap["id"], "quantity": 3}],
             [{"method": "cash", "amount": 10000}])
    assert s["lines"][0]["promotion_name"] == "Cream special" and D(s["lines"][0]["discount"]) == 900  # 3 x 300 beats 10% of 6000
    assert s["lines"][1]["promotion_name"] == "Soap 3+" and D(s["lines"][1]["discount"]) == 300
    # a cashier gets promotions without needing discount permission
    promos = shop.owner.ok("get", "/api/promotions")
    assert {x["name"]: x["running"] for x in promos}["Expired"] is False
    boot = shop.owner.ok("get", "/api/pos/bootstrap")
    assert {x["name"] for x in boot["promotions"]} == {"Skincare week", "Soap 3+", "Cream special"}


def test_cart_discount_split_and_tax_on_top(shop):
    shop.owner.ok("put", "/api/settings", json={"pos": {"prices_include_tax": False}})
    a = shop.product(name="A", selling_price=1000, tax_rate=7.5)
    b = shop.product(name="B", selling_price=3000, tax_rate=0)
    for x in (a, b):
        shop.receive(x["id"], 10)
    sh = open_shift(shop.owner, shop)
    s = sell(shop.owner, sh, [{"product_id": a["id"], "quantity": 1}, {"product_id": b["id"], "quantity": 1}],
             [{"method": "cash", "amount": 5000}], cart_discount={"type": "amount", "value": 400})
    # 400 split by value: 100 on A, 300 on B. Tax on A only: 7.5% of 900 = 67.50
    assert [D(x["discount"]) for x in s["lines"]] == [100, 300]
    assert D(s["tax"]) == D("67.50") and D(s["total"]) == D("3667.50")
    assert D(s["change_given"]) == D("1332.50")


def test_return_with_receipt(shop):
    p = shop.product(name="Sandals", selling_price=4000, cost_price=2500)
    shop.receive(p["id"], 10, unit_cost=2500)
    sh = open_shift(shop.owner, shop, 10000)
    s = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 3, "discount": {"type": "amount", "value": 1000}}],
             [{"method": "cash", "amount": 11000}])
    assert D(s["total"]) == 11000
    item = s["lines"][0]
    r = shop.owner.ok("post", "/api/pos/returns", json={
        "shift_id": sh["id"], "original_sale_id": s["number"], "refund_method": "cash", "reason": "Too small",
        "lines": [{"original_item_id": item["id"], "quantity": 2}]})
    assert r["kind"] == "return" and r["number"].startswith("RET-")
    assert D(r["total"]) == D("7333.33")            # 2/3 of what was actually paid
    assert D(r["payments"][0]["amount"]) == D("-7333.33")
    assert shop.on_hand(p["id"]) == 9
    bad = shop.owner.post("/api/pos/returns", json={"shift_id": sh["id"], "original_sale_id": s["id"],
                                                   "lines": [{"original_item_id": item["id"], "quantity": 2}]})
    assert bad.status_code == 400 and bad.json()["code"] == "over_return"
    last = shop.owner.ok("post", "/api/pos/returns", json={"shift_id": sh["id"], "original_sale_id": s["id"], "refund_method": "transfer",
                                                          "lines": [{"original_item_id": item["id"], "quantity": 1, "restock": False}]})
    assert D(last["total"]) + D(r["total"]) == D(s["total"])  # never refunds more than was paid, to the kobo
    assert D(last["cost_of_goods"]) == 0
    assert shop.on_hand(p["id"]) == 9                 # damaged one did not go back on the shelf
    sale = shop.owner.ok("get", f"/api/pos/sales/{s['id']}")
    assert [x["number"] for x in sale["returns"]] == [r["number"], last["number"]]
    summ = shop.owner.ok("get", f"/api/pos/shifts/{sh['id']}")["summary"]
    assert D(summ["expected_cash"]) == D("10000") + D("11000") - D("7333.33")
    assert D(summ["returns_total"]) == D(s["total"]) and D(summ["net_sales"]) == 0


def test_return_needs_permission(shop):
    p = shop.product(selling_price=100)
    shop.receive(p["id"], 5)
    cashier = shop.user("Cashier")
    sh = open_shift(cashier, shop)
    s = sell(cashier, sh, [{"product_id": p["id"], "quantity": 1}], [{"method": "cash", "amount": 100}])
    r = cashier.post("/api/pos/returns", json={"shift_id": sh["id"], "original_sale_id": s["id"],
                                              "lines": [{"original_item_id": s["lines"][0]["id"], "quantity": 1}]})
    assert r.status_code == 403


def test_store_credit_and_exchange(shop):
    shirt = shop.product(name="Shirt", selling_price=5000)
    trousers = shop.product(name="Trousers", selling_price=7000)
    for x in (shirt, trousers):
        shop.receive(x["id"], 5)
    sh = open_shift(shop.owner, shop)
    s = sell(shop.owner, sh, [{"product_id": shirt["id"], "quantity": 1}], [{"method": "card", "amount": 5000}])
    credit = shop.owner.ok("post", "/api/pos/returns", json={"shift_id": sh["id"], "original_sale_id": s["id"], "refund_method": "credit",
                                                            "lines": [{"original_item_id": s["lines"][0]["id"], "quantity": 1}]})
    assert D(credit["credit_remaining"]) == 5000
    r = sell(shop.owner, sh, [{"product_id": trousers["id"], "quantity": 1}],
             [{"method": "credit", "amount": 6000, "credit_ref": credit["number"]}, {"method": "cash", "amount": 1000}], status=None)
    assert r.status_code == 400 and r.json()["code"] == "credit_insufficient"
    ex = sell(shop.owner, sh, [{"product_id": trousers["id"], "quantity": 1}],
              [{"method": "credit", "amount": 5000, "credit_ref": credit["number"]}, {"method": "transfer", "amount": 2000}])
    assert {x["method"] for x in ex["payments"]} == {"credit", "transfer"}
    assert D(shop.owner.ok("get", f"/api/pos/sales/{credit['id']}")["credit_remaining"]) == 0
    summ = shop.owner.ok("get", f"/api/pos/shifts/{sh['id']}")["summary"]
    methods = {m["method"]: m for m in summ["methods"]}
    assert D(methods["credit"]["net"]) == 0 and D(summ["expected_cash"]) == 0


def test_serial_sale_and_return(client):
    shop = Org(client, industry="retail", modules={"serials": True})
    phone = shop.product(name="Phone X", selling_price=150000, track_serials=True)
    shop.receive(phone["id"], 2, serials=["IMEI-1", "IMEI-2"])
    sh = open_shift(shop.owner, shop)
    cat = shop.owner.ok("get", f"/api/pos/catalog?location_id={shop.main}")
    assert next(x for x in cat["items"] if x["id"] == phone["id"])["serials"] == ["IMEI-1", "IMEI-2"]
    r = sell(shop.owner, sh, [{"product_id": phone["id"], "quantity": 1}], [{"method": "transfer", "amount": 150000}], status=None)
    assert r.status_code == 400 and r.json()["code"] == "serial_required"
    s = sell(shop.owner, sh, [{"product_id": phone["id"], "quantity": 1, "serials": ["IMEI-2"]}], [{"method": "transfer", "amount": 150000}])
    assert s["lines"][0]["serials"] == ["IMEI-2"]
    serials = {x["serial"]: x["status"] for x in shop.owner.ok("get", "/api/serials")["items"]}
    assert serials == {"IMEI-1": "in_stock", "IMEI-2": "sold"}
    bad = shop.owner.post("/api/pos/returns", json={"shift_id": sh["id"], "original_sale_id": s["id"], "refund_method": "transfer",
                                                   "lines": [{"original_item_id": s["lines"][0]["id"], "serials": ["IMEI-1"]}]})
    assert bad.status_code == 400 and bad.json()["code"] == "serial_not_on_receipt"
    shop.owner.ok("post", "/api/pos/returns", json={"shift_id": sh["id"], "original_sale_id": s["id"], "refund_method": "transfer",
                                                   "lines": [{"original_item_id": s["lines"][0]["id"], "serials": ["IMEI-2"]}]})
    serials = {x["serial"]: x["status"] for x in shop.owner.ok("get", "/api/serials")["items"]}
    assert serials == {"IMEI-1": "in_stock", "IMEI-2": "in_stock"}


def test_cash_up(shop):
    p = shop.product(selling_price=1000)
    shop.receive(p["id"], 20)
    cashier = shop.user("Cashier")
    sh = open_shift(cashier, shop, 10000)
    sell(cashier, sh, [{"product_id": p["id"], "quantity": 3}], [{"method": "cash", "amount": 5000}])
    sell(cashier, sh, [{"product_id": p["id"], "quantity": 2}], [{"method": "transfer", "amount": 2000}])
    shop.owner.ok("post", "/api/pos/returns", json={"shift_id": open_shift(shop.owner, shop)["id"],
                                                   "lines": [{"product_id": p["id"], "quantity": 1}], "refund_method": "cash"})
    cashier.ok("post", f"/api/pos/shifts/{sh['id']}/cash", json={"kind": "out", "amount": 500, "reason": "Diesel for generator"})
    cashier.ok("post", f"/api/pos/shifts/{sh['id']}/cash", json={"kind": "in", "amount": 200, "reason": "Change from bank"})
    summ = cashier.ok("get", f"/api/pos/shifts/{sh['id']}")["summary"]
    assert D(summ["expected_cash"]) == 10000 + 3000 - 500 + 200   # the owner's refund was on the owner's own shift
    assert D(summ["net_sales"]) == 5000 and summ["sales_count"] == 2
    # somebody else can't close it, a manager can
    other = shop.user("Cashier")
    r = other.post(f"/api/pos/shifts/{sh['id']}/close", json={"counted_cash": 1})
    assert r.status_code == 400 and r.json()["code"] == "not_your_shift"
    closed = cashier.ok("post", f"/api/pos/shifts/{sh['id']}/close", json={"counted_cash": 12500, "note": "200 missing"})
    assert closed["status"] == "closed" and D(closed["difference"]) == -200
    alerts = shop.owner.ok("get", "/api/notifications")
    items = alerts["items"] if isinstance(alerts, dict) else alerts
    assert any("short by 200" in a["title"] for a in items)
    rep = shop.owner.ok("get", "/api/reports/pos-shifts")
    assert any(r_["number"] == sh["number"] and D(r_["difference"]) == -200 for r_ in rep["rows"])
    # a cashier only sees their own shifts
    assert [x["id"] for x in other.ok("get", "/api/pos/shifts")["items"]] == []


def test_tenancy_and_branch_restriction(client, shop):
    p = shop.product(selling_price=100)
    shop.receive(p["id"], 5)
    sh = open_shift(shop.owner, shop)
    s = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 1}], [{"method": "cash", "amount": 100}])
    other = Org(client, industry="retail")
    assert other.owner.get(f"/api/pos/sales/{s['id']}").status_code == 404
    assert other.owner.get(f"/api/pos/sales/{s['number']}").status_code == 404
    assert other.owner.get(f"/api/pos/shifts/{sh['id']}").status_code == 404
    osh = open_shift(other.owner, other)
    r = other.owner.post("/api/pos/sales", json={"shift_id": osh["id"], "lines": [{"product_id": p["id"], "quantity": 1}],
                                                "payments": [{"method": "cash", "amount": 100}]})
    assert r.status_code == 404
    second = shop.warehouse("Lekki shop")
    staff = shop.user("Cashier", warehouse_ids=[second["id"]])
    r = staff.post("/api/pos/shifts", json={"location_id": shop.main})
    assert r.status_code == 400 and r.json()["code"] == "location_forbidden"
    boot = staff.ok("get", "/api/pos/bootstrap")
    assert [x["name"] for x in boot["locations"]] == ["Lekki shop"]


def test_catalog_for_offline_use(client):
    shop = Org(client, industry="retail")
    tee = shop.owner.ok("post", "/api/products", json={"sku": "TEE", "name": "Tee", "selling_price": 3000,
                                                       "variant_options": {"Size": ["S", "M"]}})
    kids = shop.owner.ok("get", f"/api/products/{tee['id']}")["variants"]
    shop.receive(kids[0]["id"], 4)
    cat = shop.owner.ok("get", f"/api/pos/catalog?location_id={shop.main}")
    ids = {x["id"] for x in cat["items"]}
    assert tee["id"] not in ids and {k["id"] for k in kids} <= ids
    row = next(x for x in cat["items"] if x["id"] == kids[0]["id"])
    assert row["parent_name"] == "Tee" and D(row["on_hand"]) == 4 and row["attributes"]
    r = shop.owner.post("/api/pos/sales", json={"shift_id": open_shift(shop.owner, shop)["id"],
                                               "lines": [{"product_id": tee["id"], "quantity": 1}], "payments": []})
    assert r.status_code == 400 and r.json()["code"] == "variant_required"


def test_today_summary_and_reports(shop):
    a = shop.product(name="Bread", selling_price=1200, cost_price=800)
    b = shop.product(name="Milk", selling_price=900, cost_price=600)
    idle = shop.product(name="Old stock", selling_price=100, cost_price=50)
    for x in (a, b):
        shop.receive(x["id"], 30, unit_cost=x["cost_price"])
    shop.receive(idle["id"], 10, unit_cost=50)
    sh = open_shift(shop.owner, shop, 1000)
    sell(shop.owner, sh, [{"product_id": a["id"], "quantity": 2}], [{"method": "cash", "amount": 2400}])
    sell(shop.owner, sh, [{"product_id": b["id"], "quantity": 1}], [{"method": "transfer", "amount": 900}])
    # an offline sale made yesterday (local time) is not counted today
    yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    sell(shop.owner, sh, [{"product_id": b["id"], "quantity": 5}], [{"method": "cash", "amount": 4500}], offline=True,
         client_id=str(uuid.uuid4()), sold_at=yesterday.isoformat())
    t = shop.owner.ok("get", "/api/pos/summary")
    assert t["totals"]["sales_count"] == 2 and D(t["totals"]["net_sales"]) == 3300
    assert D(t["totals"]["profit"]) == 3300 - 1600 - 600
    assert D(t["yesterday"]["net_sales"]) == 4500
    assert {m["method"]: D(m["amount"]) for m in t["by_method"]} == {"cash": 2400, "transfer": 900}
    assert t["top_products"][0]["name"] == "Bread"
    assert [x["name"] for x in t["slow_movers"]] == ["Old stock"]
    assert t["open_shifts"][0]["number"] == sh["number"] and D(t["open_shifts"][0]["expected_cash"]) == 1000 + 2400 + 4500
    assert t["by_branch"][0]["name"] == "Main Store"
    assert sum(D(h) for h in t["by_hour"]) == 3300
    rep = shop.owner.ok("get", "/api/reports/pos-sales")
    assert rep["summary"]["receipts"] == 3 and D(rep["summary"]["net_sales"]) == 7800
    prods = shop.owner.ok("get", "/api/reports/pos-products")
    assert {r_["name"]: D(r_["net_units"]) for r_ in prods["rows"]} == {"Bread": 2, "Milk": 6}
    x = shop.owner.get("/api/reports/pos-sales?format=xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    # cashiers don't get the owner's view
    assert shop.user("Cashier").get("/api/pos/summary").status_code == 403


def test_send_receipt(shop, db):
    from app.models import NotificationDelivery
    shop.owner.ok("put", "/api/settings", json={"currency": "NGN"})  # local numbers like 0803… are Nigerian
    p = shop.product(selling_price=700)
    shop.receive(p["id"], 3)
    sh = open_shift(shop.owner, shop)
    s = sell(shop.owner, sh, [{"product_id": p["id"], "quantity": 1}], [{"method": "cash", "amount": 1000}])
    shop.owner.ok("post", f"/api/pos/sales/{s['id']}/send", json={"channel": "sms", "to": "0803 123 4567"})
    shop.owner.ok("post", f"/api/pos/sales/{s['id']}/send", json={"channel": "email", "to": "ada@example.com"})
    rows = db.query(NotificationDelivery).filter(NotificationDelivery.org_id == uuid.UUID(shop.org_id)).all()
    sms = next(r_ for r_ in rows if r_.channel == "sms")
    assert sms.recipient == "+2348031234567"
    assert s["number"] in sms.body and "/r/" in sms.body
    assert any(r_.channel == "email" and r_.recipient == "ada@example.com" for r_ in rows)


def test_pos_settings_validation(shop):
    assert shop.owner.put("/api/settings", json={"pos": {"payment_methods": []}}).status_code == 422
    assert shop.owner.put("/api/settings", json={"pos": {"paper_width": 70}}).status_code == 422
    assert shop.owner.put("/api/settings", json={"pos": {"nonsense": 1}}).status_code == 422
    assert shop.owner.put("/api/settings", json={"timezone": "Mars/Base"}).status_code == 422
    s = shop.owner.ok("put", "/api/settings", json={"timezone": "Africa/Accra", "pos": {"payment_methods": ["cash", "card"],
                                                                                        "receipt_footer": "No refund after 7 days"}})
    assert s["settings"]["timezone"] == "Africa/Accra" and s["settings"]["pos"]["payment_methods"] == ["cash", "card"]
    boot = shop.owner.ok("get", "/api/pos/bootstrap")
    assert [m["key"] for m in boot["methods"]] == ["cash", "card"] and boot["receipt"]["footer"] == "No refund after 7 days"
