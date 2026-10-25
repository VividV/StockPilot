from decimal import Decimal


def test_low_and_out_of_stock_reports_and_alerts(org):
    sup = org.owner.ok("post", "/api/suppliers", json={"name": "Acme Wholesale", "email": "a@acme.example"})
    low = org.product(name="Low thing", reorder_level=10, reorder_qty=50, preferred_supplier_id=sup["id"])
    ok = org.product(name="Fine thing", reorder_level=5)
    out = org.product(name="Gone thing", reorder_level=3)
    org.receive(low["id"], 20)
    org.receive(ok["id"], 100)
    org.receive(out["id"], 2)
    org.owner.ok("post", "/api/inventory/issue", json={"product_id": low["id"], "location_id": org.main, "quantity": 12})
    org.owner.ok("post", "/api/inventory/issue", json={"product_id": out["id"], "location_id": org.main, "quantity": 2})
    rep = org.owner.ok("get", "/api/reports/low-stock")
    assert [r["name"] for r in rep["rows"]] == ["Low thing"]
    assert Decimal(str(rep["rows"][0]["suggested_order"])) == 50 and rep["rows"][0]["supplier"] == "Acme Wholesale"
    assert [r["name"] for r in org.owner.ok("get", "/api/reports/out-of-stock")["rows"]] == ["Gone thing"]
    assert org.owner.ok("get", "/api/products?status=low")["items"][0]["name"] == "Low thing"
    notes = {n["title"] for n in org.owner.ok("get", "/api/notifications")["items"]}
    assert "Low thing is running low" in notes and "Gone thing is out of stock" in notes
    # restock resolves the alert
    org.receive(low["id"], 100)
    notes = {n["title"] for n in org.owner.ok("get", "/api/notifications")["items"]}
    assert "Low thing is running low" not in notes
    # changing the reorder level re-evaluates
    org.owner.ok("put", f"/api/products/{ok['id']}", json={"reorder_level": 200})
    assert "Fine thing is running low" in {n["title"] for n in org.owner.ok("get", "/api/notifications")["items"]}


def test_exports(org):
    p = org.product(name="=cmd|' /C calc'!A0")  # spreadsheet-injection attempt
    org.receive(p["id"], 5, unit_cost=2)
    for fmt, ctype, magic in (("csv", "text/csv", b"\xef\xbb\xbf"), ("xlsx", "spreadsheetml", b"PK"), ("pdf", "application/pdf", b"%PDF")):
        r = org.owner.get(f"/api/reports/stock-on-hand?format={fmt}")
        assert r.status_code == 200, r.text
        assert ctype in r.headers["content-type"] and r.content.startswith(magic)
        assert "attachment" in r.headers["content-disposition"]
    csv_text = org.owner.get("/api/reports/stock-on-hand?format=csv").content.decode("utf-8-sig")
    assert "'=cmd" in csv_text  # neutralised
    for name in ("valuation", "low-stock", "out-of-stock", "expiry", "batches", "serials", "movements", "locations", "adjustments"):
        assert org.owner.get(f"/api/reports/{name}?format=xlsx").status_code == 200, name
    assert org.owner.ok("get", "/api/audit-logs?action=report.export")["total"] >= 3


def test_dashboard_numbers(org):
    a = org.product(cost_price=10, reorder_level=5)
    b = org.product(cost_price=4)
    org.receive(a["id"], 4, unit_cost=10)
    org.receive(b["id"], 10, unit_cost=4)
    org.owner.ok("post", "/api/inventory/issue", json={"product_id": b["id"], "location_id": org.main, "quantity": 3, "type": "sale"})
    d = org.owner.ok("get", "/api/dashboard")
    assert Decimal(str(d["inventory_value"])) == Decimal("68.00")  # 4*10 + 7*4
    assert Decimal(str(d["total_units"])) == 11
    assert d["product_count"] == 2 and d["low_stock_count"] == 1 and d["out_of_stock_count"] == 0
    assert Decimal(str(d["received_units"])) == 14 and Decimal(str(d["issued_units"])) == 3
    assert d["top_products"][0]["product_id"] == b["id"]
    assert d["by_location"][0]["name"] == "Main Store"
    assert len(d["recent_movements"]) == 3


def test_pagination_sorting_filtering(org):
    for i in range(7):
        org.product(name=f"Item {i}", selling_price=i)
    r = org.owner.ok("get", "/api/products?page_size=3&page=2&sort=-selling_price")
    assert r["total"] == 7 and len(r["items"]) == 3
    assert [Decimal(x["selling_price"]) for x in r["items"]] == [3, 2, 1]
    assert org.owner.get("/api/products?sort=password").status_code == 400
    assert org.owner.ok("get", "/api/products?q=Item 4")["total"] == 1


def test_global_search(org):
    sup = org.owner.ok("post", "/api/suppliers", json={"name": "Zebra Supplies"})
    org.owner.ok("post", "/api/customers", json={"name": "Zebra Clinic"})
    p = org.product(name="Zebra pens", barcode="ZB-1")
    org.receive(p["id"], 1, reference_no="PO-ZEBRA-9")
    types = {r["type"] for r in org.owner.ok("get", "/api/search?q=zebra")["results"]}
    assert {"product", "supplier", "customer", "reference"} <= types
    assert org.owner.ok("get", "/api/search?q=ZB-1")["results"][0]["id"] == p["id"]


def test_the_fifteen_main_workflows(client, org):
    """The acceptance list from the brief, end to end through the API."""
    from conftest import PASSWORD
    # 1. login
    r = client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD})
    assert r.status_code == 200
    api = org.owner
    # 2. create product
    p = api.ok("post", "/api/products", json={"sku": "WF-1", "name": "Workflow widget", "barcode": "WF-BAR", "cost_price": 5,
                                              "selling_price": 9})
    # 3. create warehouse
    wh = api.ok("post", "/api/warehouses", json={"name": "North Depot", "type": "warehouse"})
    north = wh["default_location_id"]
    # 4. receive stock
    api.ok("post", "/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 40, "unit_cost": 5})
    # 5. view stock
    assert Decimal(api.ok("get", f"/api/stock?product_id={p['id']}")["items"][0]["quantity"]) == 40
    # 6. issue stock
    api.ok("post", "/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 15, "type": "sale"})
    # 7. transfer stock
    api.ok("post", "/api/inventory/transfer", json={"from_location_id": org.main, "to_location_id": north, "product_id": p["id"], "quantity": 10})
    # 8. create supplier
    sup = api.ok("post", "/api/suppliers", json={"name": "Workflow Supplier"})
    # 9. set reorder level
    api.ok("put", f"/api/products/{p['id']}", json={"reorder_level": 30, "preferred_supplier_id": sup["id"]})
    # 10. low-stock report
    low = api.ok("get", "/api/reports/low-stock")["rows"]
    assert low[0]["sku"] == "WF-1" and low[0]["supplier"] == "Workflow Supplier"
    # 11. create batch  12. track expiry
    med = api.ok("post", "/api/products", json={"sku": "WF-MED", "name": "Syrup", "track_expiry": True})
    from datetime import date, timedelta
    api.ok("post", "/api/inventory/receive", json={"product_id": med["id"], "location_id": org.main, "quantity": 6,
                                                   "batch_number": "LOT-1", "expires_on": (date.today() + timedelta(days=6)).isoformat()})
    exp = api.ok("get", "/api/reports/expiry")["rows"]
    assert exp[0]["batch"] == "LOT-1" and exp[0]["bucket"] == "7_days"
    # 13. movement history
    hist = api.ok("get", f"/api/products/{p['id']}/movements")["items"]
    assert [h["movement_type"] for h in hist] == ["transfer_in", "transfer_out", "sale", "purchase_receipt"]
    assert all(h["user_name"] == "Olu Owner" for h in hist)
    # 14. export
    assert api.get("/api/reports/movements?format=xlsx").status_code == 200
    # 15. unauthorised users are refused
    viewer = org.user("Viewer")
    assert viewer.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 1}).status_code == 403
    assert client.get("/api/stock").status_code == 401
