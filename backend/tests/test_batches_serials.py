from datetime import date, timedelta
from decimal import Decimal

D = lambda n: (date.today() + timedelta(days=n)).isoformat()  # noqa: E731


def batch_product(org, **kw):
    return org.product(track_expiry=True, **kw)


def test_batch_product_needs_batch_and_expiry(org):
    p = batch_product(org)
    assert p["track_batches"] and p["track_expiry"]
    r = org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 5})
    assert r.status_code == 400 and r.json()["code"] == "batch_required"
    r = org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 5, "batch_number": "B1"})
    assert r.status_code == 400 and r.json()["code"] == "expiry_required"
    org.receive(p["id"], 5, batch_number="B1", expires_on=D(100), manufactured_on=D(-30))
    b = org.owner.ok("get", f"/api/products/{p['id']}/batches")
    assert b[0]["batch_number"] == "B1" and Decimal(b[0]["quantity"]) == 5


def test_fefo_issues_earliest_expiry_first(org):
    p = batch_product(org)
    org.receive(p["id"], 10, batch_number="LATE", expires_on=D(200))
    org.receive(p["id"], 4, batch_number="SOON", expires_on=D(10))
    org.receive(p["id"], 6, batch_number="MID", expires_on=D(60))
    r = org.owner.ok("post", "/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 7})
    picked = [(m["batch_number"], Decimal(m["quantity"])) for m in r["movements"]]
    assert picked == [("SOON", -4), ("MID", -3)]
    left = {b["batch_number"]: Decimal(b["quantity"]) for b in org.owner.ok("get", f"/api/products/{p['id']}/batches")}
    assert left == {"SOON": 0, "MID": 3, "LATE": 10}


def test_specific_batch_can_be_chosen(org):
    p = batch_product(org)
    org.receive(p["id"], 5, batch_number="A", expires_on=D(10))
    org.receive(p["id"], 5, batch_number="B", expires_on=D(90))
    bid = next(b["id"] for b in org.owner.ok("get", f"/api/products/{p['id']}/batches") if b["batch_number"] == "B")
    r = org.owner.ok("post", "/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 2, "batch_id": bid})
    assert r["movements"][0]["batch_number"] == "B"
    r = org.owner.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 4, "batch_id": bid})
    assert r.status_code == 409  # only 3 left in batch B even though 8 exist overall


def test_expired_batches_are_never_picked_for_sale(org):
    p = batch_product(org)
    org.receive(p["id"], 5, batch_number="OLD", expires_on=D(-1))
    org.receive(p["id"], 3, batch_number="NEW", expires_on=D(30))
    r = org.owner.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 4, "type": "sale"})
    assert r.status_code == 409  # only 3 sellable
    r = org.owner.ok("post", "/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 3, "type": "sale"})
    assert r["movements"][0]["batch_number"] == "NEW"
    old = next(b["id"] for b in org.owner.ok("get", f"/api/products/{p['id']}/batches") if b["batch_number"] == "OLD")
    r = org.owner.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 1,
                                                     "batch_id": old, "type": "sale"})
    assert r.status_code == 400 and r.json()["code"] == "batch_expired"
    # writing it off as expired is allowed
    r = org.owner.ok("post", "/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 5,
                                                           "batch_id": old, "type": "expired"})
    assert r["movements"][0]["movement_type"] == "expired"


def test_expiry_report_buckets_and_alerts(org):
    p = batch_product(org, name="Vaccine")
    for num, days in (("EXP", -2), ("W1", 5), ("M1", 25), ("Q1", 80), ("Y1", 300)):
        org.receive(p["id"], 1, batch_number=num, expires_on=D(days))
    rep = org.owner.ok("get", "/api/reports/expiry?days=90")
    assert [r["batch"] for r in rep["rows"]] == ["EXP", "W1", "M1", "Q1"]
    assert [r["bucket"] for r in rep["rows"]] == ["expired", "7_days", "30_days", "90_days"]
    assert rep["summary"] == {"expired": 1, "7_days": 1, "30_days": 1, "90_days": 1}
    batches = org.owner.ok("get", "/api/batches?expiring_within=7")["items"]
    assert {b["batch_number"] for b in batches} == {"EXP", "W1"}
    notes = org.owner.ok("get", "/api/notifications")["items"]
    types = sorted(n["type"] for n in notes if n["entity_type"] == "batch")
    assert types == ["expired", "expiring", "expiring"]  # EXP, W1, M1 (30-day window)
    dash = org.owner.ok("get", "/api/dashboard")
    assert dash["expired_count"] == 1 and dash["expiring_count"] == 2


def test_same_batch_number_different_expiry_rejected(org):
    p = batch_product(org)
    org.receive(p["id"], 1, batch_number="X", expires_on=D(30))
    r = org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 1,
                                                       "batch_number": "X", "expires_on": D(40)})
    assert r.status_code == 400


def test_batches_travel_with_transfers(org):
    p = batch_product(org)
    shop = org.warehouse("Shop")["default_location_id"]
    org.receive(p["id"], 3, batch_number="A", expires_on=D(10))
    org.receive(p["id"], 3, batch_number="B", expires_on=D(20))
    org.owner.ok("post", "/api/inventory/transfer", json={"from_location_id": org.main, "to_location_id": shop,
                                                          "product_id": p["id"], "quantity": 4})
    at_shop = {(s["batch_number"], Decimal(s["quantity"])) for s in org.owner.ok("get", f"/api/stock?location_id={shop}")["items"]}
    assert at_shop == {("A", 3), ("B", 1)}


# ------------------------------------------------------------------ serial numbers
def test_serial_numbers_full_lifecycle(org):
    p = org.product(name="Laptop", track_serials=True)
    shop = org.warehouse("Shop")["default_location_id"]
    r = org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 3,
                                                       "serials": ["SN1", "SN2"]})
    assert r.status_code == 400 and r.json()["code"] == "serial_count"
    org.receive(p["id"], 3, serials=["SN1", "SN2", "SN3"])
    r = org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 1, "serials": ["SN2"]})
    assert r.status_code == 400 and r.json()["code"] == "duplicate_serial"

    # issue requires naming which units
    assert org.owner.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 1}).status_code == 400
    org.owner.ok("post", "/api/inventory/transfer", json={"from_location_id": org.main, "to_location_id": shop,
                                                          "product_id": p["id"], "quantity": 1, "serials": ["SN2"]})
    r = org.owner.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 1, "serials": ["SN2"]})
    assert r.status_code == 400 and r.json()["code"] == "serial_unavailable"  # it's at the shop now
    org.owner.ok("post", "/api/inventory/issue", json={"product_id": p["id"], "location_id": shop, "quantity": 1,
                                                       "serials": ["SN2"], "type": "sale", "issued_to": "Jane"})
    serials = {s["serial"]: s for s in org.owner.ok("get", f"/api/serials?product_id={p['id']}")["items"]}
    assert serials["SN2"]["status"] == "sold" and serials["SN1"]["status"] == "in_stock"
    hist = org.owner.ok("get", f"/api/serials/{serials['SN2']['id']}/history")["history"]
    assert [h["movement_type"] for h in hist] == ["purchase_receipt", "transfer_out", "transfer_in", "sale"]
    # customer return brings the same serial back
    org.receive(p["id"], 1, serials=["SN2"], type="return")
    assert org.on_hand(p["id"]) == 3
    # search & lookup find serials
    assert org.owner.ok("get", "/api/products/lookup?code=SN3")["matched_by"] == "serial"
    assert any(x["type"] == "serial" for x in org.owner.ok("get", "/api/search?q=SN3")["results"])
