"""Purchase orders and sales orders."""
from datetime import date, timedelta
from decimal import Decimal

D = lambda n: (date.today() + timedelta(days=n)).isoformat()  # noqa: E731


def supplier(org, name="Acme Wholesale"):
    return org.owner.ok("post", "/api/suppliers", json={"name": name})


def customer(org, name="Lekki Mart"):
    return org.owner.ok("post", "/api/customers", json={"name": name})


# ------------------------------------------------------------------ purchase orders
def test_po_partial_receiving_backorder_and_close(org):
    sup = supplier(org)
    a, b = org.product(name="Rice", cost_price=5), org.product(name="Beans", cost_price=3)
    po = org.owner.ok("post", "/api/purchase-orders", json={
        "supplier_id": sup["id"], "destination_location_id": org.main, "expected_date": D(7),
        "lines": [{"product_id": a["id"], "quantity": 100, "unit_cost": 4.5, "tax_rate": 10},
                  {"product_id": b["id"], "quantity": 40}]})
    assert po["number"].startswith("PO-") and po["status"] == "draft"
    assert Decimal(po["subtotal"]) == Decimal("570.00")  # 100*4.5 + 40*3
    assert Decimal(po["tax"]) == Decimal("45.00") and Decimal(po["total"]) == Decimal("615.00")
    assert org.on_hand(a["id"]) == 0  # ordering does not change stock

    po = org.owner.ok("post", f"/api/purchase-orders/{po['id']}/confirm")
    assert po["status"] == "confirmed"
    ia, ib = po["items"]
    r = org.owner.ok("post", f"/api/purchase-orders/{po['id']}/receive",
                     json={"lines": [{"item_id": ia["id"], "quantity": 60}]})
    assert r["status"] == "partial" and "waiting for the rest" in r["message"]
    assert Decimal(r["items"][0]["backorder_qty"]) == 40 and Decimal(r["items"][1]["backorder_qty"]) == 40
    assert Decimal(r["outstanding_value"]) == Decimal("300.00")  # 40*4.5 + 40*3
    assert org.on_hand(a["id"]) == 60
    mv = r["movements"][0]
    assert mv["movement_type"] == "purchase_receipt" and mv["reference_no"] == po["number"] and mv["counterparty"] == "Acme Wholesale"
    assert Decimal(mv["unit_cost"]) == Decimal("4.5")
    # last purchase price becomes the product's cost
    assert Decimal(org.owner.ok("get", f"/api/products/{a['id']}")["product"]["cost_price"]) == Decimal("4.5")

    # cannot receive more than is still expected
    r = org.owner.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"item_id": ia["id"], "quantity": 41}]})
    assert r.status_code == 400 and r.json()["code"] == "over_receive"

    # supplier will not send the rest -> close short
    r = org.owner.ok("post", f"/api/purchase-orders/{po['id']}/close")
    assert r["status"] == "completed" and Decimal(r["outstanding_value"]) == 0
    assert org.owner.post(f"/api/purchase-orders/{po['id']}/receive").status_code == 400


def test_po_receive_everything_and_cancel(org):
    sup = supplier(org)
    a = org.product()
    po = org.owner.ok("post", "/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": org.main,
                                                            "lines": [{"product_id": a["id"], "quantity": 12}], "confirm": True})
    r = org.owner.ok("post", f"/api/purchase-orders/{po['id']}/receive")
    assert r["status"] == "completed" and org.on_hand(a["id"]) == 12
    po2 = org.owner.ok("post", "/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": org.main,
                                                             "lines": [{"product_id": a["id"], "quantity": 5}]})
    assert org.owner.ok("post", f"/api/purchase-orders/{po2['id']}/close")["status"] == "cancelled"


def test_po_in_cartons_with_batches(org):
    sup = supplier(org)
    p = org.product(track_expiry=True)
    org.owner.ok("post", f"/api/products/{p['id']}/conversions", json={"unit_id": org.units["ctn"], "factor": 24})
    po = org.owner.ok("post", "/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": org.main,
        "lines": [{"product_id": p["id"], "quantity": 2, "unit_id": org.units["ctn"], "unit_cost": 48}], "confirm": True})
    item = po["items"][0]
    assert Decimal(item["ordered_qty"]) == 48 and Decimal(item["unit_cost"]) == 2  # 48 per carton = 2 per piece
    r = org.owner.post(f"/api/purchase-orders/{po['id']}/receive", json={"lines": [{"item_id": item["id"], "quantity": 48}]})
    assert r.status_code == 400 and r.json()["code"] == "batch_required"
    r = org.owner.ok("post", f"/api/purchase-orders/{po['id']}/receive", json={"lines": [
        {"item_id": item["id"], "quantity": 48, "batch_number": "LOT-9", "expires_on": D(200)}]})
    assert r["movements"][0]["batch_number"] == "LOT-9"
    batch = org.owner.ok("get", "/api/batches")["items"][0]
    assert batch["purchase_reference"] == po["number"]


def test_po_draft_editing_rules(org):
    sup = supplier(org)
    a, b = org.product(), org.product()
    po = org.owner.ok("post", "/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": org.main,
                                                            "lines": [{"product_id": a["id"], "quantity": 1}]})
    po = org.owner.ok("put", f"/api/purchase-orders/{po['id']}", json={"lines": [{"product_id": b["id"], "quantity": 3, "unit_cost": 2}]})
    assert [i["product_id"] for i in po["items"]] == [b["id"]]
    org.owner.ok("post", f"/api/purchase-orders/{po['id']}/confirm")
    r = org.owner.put(f"/api/purchase-orders/{po['id']}", json={"lines": [{"product_id": a["id"], "quantity": 3}]})
    assert r.status_code == 400
    assert org.owner.ok("put", f"/api/purchase-orders/{po['id']}", json={"expected_date": D(3)})["expected_date"] == D(3)
    r = org.owner.post("/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": org.main,
                                                     "lines": [{"product_id": a["id"], "quantity": 1}, {"product_id": a["id"], "quantity": 2}]})
    assert r.status_code == 400 and r.json()["code"] == "duplicate_line"


def test_reorder_suggestions_and_pos_from_low_stock(org):
    s1, s2 = supplier(org, "First"), supplier(org, "Second")
    low1 = org.product(name="Low one", reorder_level=10, reorder_qty=50, preferred_supplier_id=s1["id"], cost_price=2)
    low2 = org.product(name="Low two", reorder_level=5, preferred_supplier_id=s2["id"])
    orphan = org.product(name="No supplier", reorder_level=5)
    fine = org.product(name="Fine", reorder_level=5, preferred_supplier_id=s1["id"])
    org.receive(low1["id"], 4)
    org.receive(fine["id"], 100)
    sug = {x["name"]: x for x in org.owner.ok("get", "/api/purchase-orders/suggestions")}
    assert set(sug) == {"Low one", "Low two", "No supplier"}
    assert Decimal(sug["Low one"]["suggested_qty"]) == 50 and Decimal(sug["Low two"]["suggested_qty"]) == 10
    r = org.owner.ok("post", "/api/purchase-orders/from-low-stock", json={"destination_location_id": org.main})
    assert len(r["purchase_orders"]) == 2 and [x["name"] for x in r["skipped"]] == ["No supplier"]
    by_sup = {po["supplier_name"]: po for po in r["purchase_orders"]}
    assert by_sup["First"]["line_count"] == 1 and Decimal(by_sup["First"]["total"]) == Decimal("100.00")
    # stock already on order is counted: no duplicate suggestions
    names = {x["name"] for x in org.owner.ok("get", "/api/purchase-orders/suggestions")}
    assert names == {"No supplier"}


def test_overdue_purchase_orders(org):
    sup = supplier(org)
    a = org.product()
    po = org.owner.ok("post", "/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": org.main,
        "expected_date": D(-2), "lines": [{"product_id": a["id"], "quantity": 1}], "confirm": True})
    assert po["overdue"] is True
    assert [x["number"] for x in org.owner.ok("get", "/api/purchase-orders?overdue=true")["items"]] == [po["number"]]
    rep = org.owner.ok("get", "/api/reports/suppliers")["rows"][0]
    assert rep["late_orders"] == 1 and rep["open_orders"] == 1


def test_purchase_permissions(org):
    sup = supplier(org)
    a = org.product()
    body = {"supplier_id": sup["id"], "destination_location_id": org.main, "lines": [{"product_id": a["id"], "quantity": 1}]}
    assert org.user("Warehouse Staff").post("/api/purchase-orders", json=body).status_code == 403
    buyer = org.user("Purchasing Staff")
    po = buyer.ok("post", "/api/purchase-orders", json={**body, "confirm": True})
    assert org.user("Sales Staff").get(f"/api/purchase-orders/{po['id']}").status_code == 403
    staff = org.user("Warehouse Staff")
    assert staff.ok("post", f"/api/purchase-orders/{po['id']}/receive")["status"] == "completed"


# ------------------------------------------------------------------ sales orders
def test_so_totals_discount_tax_partial_fulfilment_payment(org):
    cust = customer(org)
    a = org.product(name="Shoes", selling_price=100, cost_price=40)
    b = org.product(name="Socks", selling_price=10, cost_price=4)
    org.receive(a["id"], 5, unit_cost=40)
    org.receive(b["id"], 50, unit_cost=4)
    so = org.owner.ok("post", "/api/sales-orders", json={"customer_id": cust["id"], "source_location_id": org.main, "lines": [
        {"product_id": a["id"], "quantity": 3, "discount_percent": 10, "tax_rate": 7.5},
        {"product_id": b["id"], "quantity": 20}]})
    # shoes: 300 - 30 = 270 + 20.25 tax; socks: 200
    assert Decimal(so["subtotal"]) == Decimal("500.00") and Decimal(so["discount"]) == Decimal("30.00")
    assert Decimal(so["tax"]) == Decimal("20.25") and Decimal(so["total"]) == Decimal("490.25")
    assert so["status"] == "draft" and so["payment_status"] == "unpaid"
    ia, ib = so["items"]
    assert Decimal(ia["available_qty"]) == 5

    r = org.owner.ok("post", f"/api/sales-orders/{so['id']}/fulfil", json={"lines": [{"item_id": ia["id"], "quantity": 2}]})
    assert r["status"] == "partial" and org.on_hand(a["id"]) == 3
    assert r["movements"][0]["movement_type"] == "sale" and r["movements"][0]["counterparty"] == "Lekki Mart"
    assert Decimal(r["cost_of_goods"]) == Decimal("80.00")
    # gross profit only counts what was shipped: 2*100*0.9 - 80
    assert Decimal(r["gross_profit"]) == Decimal("100.00")

    r = org.owner.ok("post", f"/api/sales-orders/{so['id']}/payments", json={"amount": 200})
    assert r["payment_status"] == "partial" and Decimal(r["balance_due"]) == Decimal("290.25")
    r = org.owner.ok("post", f"/api/sales-orders/{so['id']}/payments", json={})
    assert r["payment_status"] == "paid" and Decimal(r["balance_due"]) == 0

    r = org.owner.ok("post", f"/api/sales-orders/{so['id']}/fulfil")
    assert r["status"] == "completed" and org.on_hand(b["id"]) == 30


def test_so_cannot_oversell_and_uses_fefo(org):
    p = org.product(track_expiry=True, selling_price=5)
    org.receive(p["id"], 4, batch_number="LATE", expires_on=D(100))
    org.receive(p["id"], 3, batch_number="SOON", expires_on=D(10))
    so = org.owner.ok("post", "/api/sales-orders", json={"source_location_id": org.main, "confirm": True,
                                                         "lines": [{"product_id": p["id"], "quantity": 9}]})
    r = org.owner.post(f"/api/sales-orders/{so['id']}/fulfil")
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"
    assert org.on_hand(p["id"]) == 7  # nothing half-shipped
    item = so["items"][0]
    r = org.owner.ok("post", f"/api/sales-orders/{so['id']}/fulfil", json={"lines": [{"item_id": item["id"], "quantity": 5}]})
    assert [(m["batch_number"], Decimal(m["quantity"])) for m in r["movements"]] == [("SOON", -3), ("LATE", -2)]
    r = org.owner.post(f"/api/sales-orders/{so['id']}/fulfil", json={"lines": [{"item_id": item["id"], "quantity": 5}]})
    assert r.status_code == 400 and r.json()["code"] == "over_fulfil"


def test_so_serials_and_refund(org):
    p = org.product(track_serials=True, selling_price=1000)
    org.receive(p["id"], 2, serials=["A1", "A2"])
    so = org.owner.ok("post", "/api/sales-orders", json={"source_location_id": org.main, "lines": [{"product_id": p["id"], "quantity": 1}]})
    item = so["items"][0]
    r = org.owner.ok("post", f"/api/sales-orders/{so['id']}/fulfil", json={"lines": [{"item_id": item["id"], "quantity": 1, "serials": ["A2"]}]})
    assert r["movements"][0]["serials"] == ["A2"] and r["status"] == "completed"
    org.owner.ok("post", f"/api/sales-orders/{so['id']}/payments", json={})
    r = org.owner.ok("post", f"/api/sales-orders/{so['id']}/payments", json={"amount": -400})
    assert r["payment_status"] == "partial" and Decimal(r["amount_paid"]) == 600
    assert org.owner.post(f"/api/sales-orders/{so['id']}/payments", json={"amount": -5000}).status_code == 400


def test_so_cancel_and_close(org):
    p = org.product(selling_price=1)
    org.receive(p["id"], 10)
    so = org.owner.ok("post", "/api/sales-orders", json={"source_location_id": org.main, "lines": [{"product_id": p["id"], "quantity": 4}]})
    assert org.owner.ok("post", f"/api/sales-orders/{so['id']}/close")["status"] == "cancelled"
    assert org.on_hand(p["id"]) == 10
    so = org.owner.ok("post", "/api/sales-orders", json={"source_location_id": org.main, "lines": [{"product_id": p["id"], "quantity": 4}]})
    org.owner.ok("post", f"/api/sales-orders/{so['id']}/fulfil", json={"lines": [{"item_id": so["items"][0]["id"], "quantity": 1}]})
    r = org.owner.ok("post", f"/api/sales-orders/{so['id']}/close")
    assert r["status"] == "completed" and Decimal(r["items"][0]["remaining_qty"]) == 0


def test_sales_permissions_and_location_restriction(org):
    shop = org.warehouse("Shop")
    p = org.product(selling_price=2)
    org.receive(p["id"], 5)
    clerk = org.user("Sales Staff", warehouse_ids=[shop["id"]])
    r = clerk.post("/api/sales-orders", json={"source_location_id": org.main, "lines": [{"product_id": p["id"], "quantity": 1}]})
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]
    assert org.user("Viewer").post("/api/sales-orders", json={"source_location_id": org.main,
                                                               "lines": [{"product_id": p["id"], "quantity": 1}]}).status_code == 403


def test_orders_tenant_isolation(make_org):
    a, b = make_org(), make_org()
    sup = supplier(a)
    pa = a.product(selling_price=1)
    a.receive(pa["id"], 3)
    po = a.owner.ok("post", "/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": a.main,
                                                          "lines": [{"product_id": pa["id"], "quantity": 1}]})
    so = a.owner.ok("post", "/api/sales-orders", json={"source_location_id": a.main, "lines": [{"product_id": pa["id"], "quantity": 1}]})
    assert b.owner.get(f"/api/purchase-orders/{po['id']}").status_code == 404
    assert b.owner.post(f"/api/purchase-orders/{po['id']}/receive").status_code == 404
    assert b.owner.post(f"/api/sales-orders/{so['id']}/fulfil").status_code == 404
    assert b.owner.ok("get", "/api/purchase-orders")["total"] == 0
    # B cannot order A's supplier or A's products
    pb = b.product()
    assert b.owner.post("/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": b.main,
                                                      "lines": [{"product_id": pb["id"], "quantity": 1}]}).status_code == 404
    assert b.owner.post("/api/sales-orders", json={"source_location_id": b.main,
                                                   "lines": [{"product_id": pa["id"], "quantity": 1}]}).status_code == 404


def test_purchase_and_sales_reports(org):
    sup = supplier(org)
    cust = customer(org)
    p = org.product(name="Widget", selling_price=20, cost_price=8)
    po = org.owner.ok("post", "/api/purchase-orders", json={"supplier_id": sup["id"], "destination_location_id": org.main,
        "lines": [{"product_id": p["id"], "quantity": 10, "unit_cost": 8}], "confirm": True})
    org.owner.ok("post", f"/api/purchase-orders/{po['id']}/receive")
    so = org.owner.ok("post", "/api/sales-orders", json={"customer_id": cust["id"], "source_location_id": org.main,
                                                         "lines": [{"product_id": p["id"], "quantity": 4}]})
    org.owner.ok("post", f"/api/sales-orders/{so['id']}/fulfil")
    pr = org.owner.ok("get", "/api/reports/purchases")
    assert pr["rows"][0]["number"] == po["number"] and Decimal(str(pr["summary"]["received_value"])) == Decimal("80.00")
    sr = org.owner.ok("get", "/api/reports/sales")
    assert Decimal(str(sr["summary"]["sales_value"])) == Decimal("80.00")
    assert Decimal(str(sr["summary"]["balance_due"])) == Decimal("80.00")
    bp = org.owner.ok("get", "/api/reports/sales-by-product")["rows"][0]
    assert Decimal(str(bp["units"])) == 4 and Decimal(str(bp["profit"])) == Decimal("48.00") and bp["margin"] == 60.0
    for name in ("purchases", "suppliers", "sales", "sales-by-product"):
        for fmt in ("csv", "xlsx", "pdf"):
            assert org.owner.get(f"/api/reports/{name}?format={fmt}").status_code == 200
    # a warehouse user sees stock reports but not sales figures
    staff = org.user("Warehouse Staff")
    assert staff.get("/api/reports/sales").status_code == 403
    found = {r["type"] for r in org.owner.ok("get", f"/api/search?q={so['number']}")["results"]}
    assert "sales_order" in found
