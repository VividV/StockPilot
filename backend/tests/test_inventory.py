"""Inventory engine: quantities, units, costs, ledger, adjustments, transfers."""
from decimal import Decimal

import pytest
from sqlalchemy import text


def issue(org, pid, qty, api=None, **kw):
    return (api or org.owner).post("/api/inventory/issue", json={"product_id": pid, "location_id": kw.pop("location", org.main),
                                                                 "quantity": qty, **kw})


def test_receive_issue_and_balance(org):
    p = org.product()
    r = org.receive(p["id"], 100, unit_cost=5)
    assert Decimal(r["on_hand"]) == 100
    assert r["movements"][0]["movement_type"] == "purchase_receipt"
    r = issue(org, p["id"], 30, type="sale")
    assert r.status_code == 201 and Decimal(r.json()["on_hand"]) == 70
    assert org.on_hand(p["id"]) == 70
    mv = org.owner.ok("get", f"/api/products/{p['id']}/movements")["items"]
    assert [m["movement_type"] for m in mv] == ["sale", "purchase_receipt"]
    assert [Decimal(m["balance_after"]) for m in mv] == [70, 100]
    assert mv[0]["user_name"] == "Olu Owner"


def test_cannot_issue_more_than_on_hand(org):
    p = org.product()
    org.receive(p["id"], 5)
    r = issue(org, p["id"], 6)
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "insufficient_stock" and Decimal(body["available"]) == 5
    assert "Not enough stock" in body["detail"]
    assert org.on_hand(p["id"]) == 5


def test_negative_stock_when_enabled(org):
    p = org.product()
    org.owner.ok("put", "/api/settings", json={"allow_negative_stock": True})
    assert issue(org, p["id"], 3).status_code == 201
    assert org.on_hand(p["id"]) == -3
    org.receive(p["id"], 10)
    assert org.on_hand(p["id"]) == 7


def test_zero_or_negative_quantity_rejected(org):
    p = org.product()
    assert org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 0}).status_code == 422
    assert org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": -4}).status_code == 422


def test_unit_conversion_carton_to_pieces(org):
    p = org.product()
    org.owner.ok("post", f"/api/products/{p['id']}/conversions", json={"unit_id": org.units["ctn"], "factor": 24})
    r = org.receive(p["id"], 3, unit_id=org.units["ctn"], unit_cost=240)  # 240 per carton = 10 per piece
    assert Decimal(r["on_hand"]) == 72
    m = r["movements"][0]
    assert Decimal(m["quantity"]) == 72 and m["entered_unit"] == "ctn" and Decimal(m["entered_quantity"]) == 3
    assert Decimal(m["unit_cost"]) == 10
    assert issue(org, p["id"], 1, unit_id=org.units["ctn"]).status_code == 201
    assert org.on_hand(p["id"]) == 48
    # no conversion set up for kg
    assert issue(org, p["id"], 1, unit_id=org.units["kg"]).status_code == 400


def test_whole_numbers_for_pieces_decimals_for_kg(org):
    pcs = org.product()
    assert org.owner.post("/api/inventory/receive", json={"product_id": pcs["id"], "location_id": org.main,
                                                          "quantity": 1.5}).status_code == 400
    kg = org.product(base_unit_id=org.units["kg"])
    org.receive(kg["id"], "12.75")
    assert issue(org, kg["id"], "0.25").status_code == 201
    assert org.on_hand(kg["id"]) == 12.5


def test_weighted_average_cost(org):
    p = org.product(cost_price=0)
    org.receive(p["id"], 10, unit_cost=10)
    org.receive(p["id"], 30, unit_cost=20)
    prod = org.owner.ok("get", f"/api/products/{p['id']}")["product"]
    assert Decimal(prod["average_cost"]) == Decimal("17.5")
    val = org.owner.ok("get", "/api/reports/valuation")
    assert Decimal(str(val["summary"]["total_value"])) == Decimal("700.00")


def test_movements_are_immutable(org, db):
    p = org.product()
    org.receive(p["id"], 5)
    mid = org.owner.ok("get", "/api/movements")["items"][0]["id"]
    with pytest.raises(Exception, match="immutable"):
        db.execute(text("UPDATE stock_movements SET quantity = 500 WHERE id = :id"), {"id": mid})
    db.rollback()
    with pytest.raises(Exception, match="immutable"):
        db.execute(text("DELETE FROM stock_movements WHERE id = :id"), {"id": mid})
    db.rollback()


def test_balance_always_equals_sum_of_movements(org, db):
    p = org.product()
    shop = org.warehouse("Shop")["default_location_id"]
    org.receive(p["id"], 50)
    issue(org, p["id"], 7)
    org.owner.ok("post", "/api/inventory/transfer", json={"from_location_id": org.main, "to_location_id": shop,
                                                          "product_id": p["id"], "quantity": 20})
    issue(org, p["id"], 3, location=shop)
    org.owner.ok("post", "/api/inventory/adjust", json={"reason": "count_correction", "lines": [
        {"product_id": p["id"], "location_id": org.main, "counted_qty": 21}]})
    rows = db.execute(text("""
        SELECT b.location_id, b.quantity, COALESCE(SUM(m.quantity), 0) AS ledger
        FROM stock_balances b LEFT JOIN stock_movements m
          ON m.product_id = b.product_id AND m.location_id = b.location_id
         AND m.batch_id IS NOT DISTINCT FROM b.batch_id
        WHERE b.product_id = :p GROUP BY b.id"""), {"p": p["id"]}).all()
    assert rows and all(r.quantity == r.ledger for r in rows)
    assert org.on_hand(p["id"]) == 21 + 17


def test_stock_count_adjustment(org):
    p = org.product()
    org.receive(p["id"], 40)
    r = org.owner.ok("post", "/api/inventory/adjust", json={"reason": "count_correction", "notes": "Monthly count", "lines": [
        {"product_id": p["id"], "location_id": org.main, "counted_qty": 37}]})
    assert r["number"].startswith("ADJ-")
    assert r["movements"][0]["movement_type"] == "adjustment_out" and Decimal(r["movements"][0]["quantity"]) == -3
    assert org.on_hand(p["id"]) == 37
    rep = org.owner.ok("get", "/api/reports/adjustments")["rows"][0]
    assert Decimal(rep["before"]) == 40 and Decimal(rep["change"]) == -3 and Decimal(rep["after"]) == 37
    # nothing to change
    r = org.owner.post("/api/inventory/adjust", json={"reason": "count_correction", "lines": [
        {"product_id": p["id"], "location_id": org.main, "counted_qty": 37}]})
    assert r.status_code == 400


def test_damaged_must_remove_stock(org):
    p = org.product()
    org.receive(p["id"], 5)
    r = org.owner.post("/api/inventory/adjust", json={"reason": "damaged", "lines": [
        {"product_id": p["id"], "location_id": org.main, "change": 2}]})
    assert r.status_code == 400
    r = org.owner.ok("post", "/api/inventory/adjust", json={"reason": "damaged", "lines": [
        {"product_id": p["id"], "location_id": org.main, "change": -2}]})
    assert r["movements"][0]["movement_type"] == "damaged"


def test_quick_transfer_moves_stock(org):
    p = org.product()
    shop = org.warehouse("Shop")
    org.receive(p["id"], 10)
    r = org.owner.ok("post", "/api/inventory/transfer", json={"from_location_id": org.main,
                                                              "to_location_id": shop["default_location_id"],
                                                              "product_id": p["id"], "quantity": 4})
    assert r["transfer"]["status"] == "received"
    stock = {s["warehouse_name"]: Decimal(s["quantity"]) for s in org.owner.ok("get", f"/api/stock?product_id={p['id']}")["items"]}
    assert stock == {"Main Store": 6, "Shop": 4}
    assert org.on_hand(p["id"]) == 10  # transfers never change the company total
    r = org.owner.post("/api/inventory/transfer", json={"from_location_id": org.main, "to_location_id": shop["default_location_id"],
                                                        "product_id": p["id"], "quantity": 7})
    assert r.status_code == 409


def test_transfer_workflow_with_partial_receipt(org):
    p = org.product()
    shop = org.warehouse("Shop")["default_location_id"]
    org.receive(p["id"], 20)
    t = org.owner.ok("post", "/api/transfers", json={"from_location_id": org.main, "to_location_id": shop,
                                                     "items": [{"product_id": p["id"], "quantity": 12}]})
    assert t["status"] == "draft" and Decimal(t["items"][0]["available_qty"]) == 20
    t = org.owner.ok("post", f"/api/transfers/{t['id']}/submit")
    assert t["status"] == "requested"
    t = org.owner.ok("post", f"/api/transfers/{t['id']}/approve")
    assert t["status"] == "approved"
    t = org.owner.ok("post", f"/api/transfers/{t['id']}/dispatch")
    assert t["status"] == "in_transit" and Decimal(t["items"][0]["dispatched_qty"]) == 12
    assert org.on_hand(p["id"]) == 8  # in transit is not on hand anywhere
    val = org.owner.ok("get", "/api/reports/valuation")["summary"]
    assert Decimal(str(val["in_transit_value"])) == Decimal("120.00")
    item_id = t["items"][0]["id"]
    t = org.owner.ok("post", f"/api/transfers/{t['id']}/receive", json={"quantities": {item_id: 5}})
    assert t["status"] == "in_transit" and Decimal(t["items"][0]["received_qty"]) == 5 and Decimal(t["items"][0]["remaining_qty"]) == 7
    assert org.owner.post(f"/api/transfers/{t['id']}/receive", json={"quantities": {item_id: 8}}).status_code == 400
    t = org.owner.ok("post", f"/api/transfers/{t['id']}/receive")
    assert t["status"] == "received" and org.on_hand(p["id"]) == 20
    assert org.owner.post(f"/api/transfers/{t['id']}/cancel").status_code == 400


def test_cancel_before_dispatch_changes_nothing(org):
    p = org.product()
    shop = org.warehouse("Shop")["default_location_id"]
    org.receive(p["id"], 3)
    t = org.owner.ok("post", "/api/transfers", json={"from_location_id": org.main, "to_location_id": shop,
                                                     "items": [{"product_id": p["id"], "quantity": 2}], "submit": True})
    t = org.owner.ok("post", f"/api/transfers/{t['id']}/cancel")
    assert t["status"] == "cancelled" and org.on_hand(p["id"]) == 3
    assert org.owner.post(f"/api/transfers/{t['id']}/dispatch").status_code == 400


def test_transfer_approval_setting(org):
    org.owner.ok("put", "/api/settings", json={"modules": {"transfer_approval": True}})
    p = org.product()
    shop = org.warehouse("Shop")["default_location_id"]
    org.receive(p["id"], 5)
    staff = org.user("Warehouse Staff")
    r = staff.ok("post", "/api/inventory/transfer", json={"from_location_id": org.main, "to_location_id": shop,
                                                          "product_id": p["id"], "quantity": 2})
    tid = r["transfer"]["id"]
    assert r["transfer"]["status"] == "requested"
    assert staff.post(f"/api/transfers/{tid}/dispatch").status_code == 400  # needs approval
    assert staff.post(f"/api/transfers/{tid}/approve").status_code == 403   # staff cannot approve
    org.owner.ok("post", f"/api/transfers/{tid}/approve")
    staff.ok("post", f"/api/transfers/{tid}/dispatch")
    staff.ok("post", f"/api/transfers/{tid}/receive")
    stock = {s["location_id"]: Decimal(s["quantity"]) for s in org.owner.ok("get", f"/api/stock?product_id={p['id']}")["items"]}
    assert stock == {org.main: 3, shop: 2}


def test_inactive_product_cannot_be_received(org):
    p = org.product()
    org.owner.ok("put", f"/api/products/{p['id']}", json={"is_active": False})
    assert org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 1}).status_code == 400


def test_delete_product_with_history_deactivates(org):
    p = org.product()
    org.receive(p["id"], 1)
    r = org.owner.ok("delete", f"/api/products/{p['id']}")
    assert "deactivated" in r["message"]
    assert org.owner.ok("get", f"/api/products/{p['id']}")["product"]["is_active"] is False
    q = org.product()
    org.owner.ok("delete", f"/api/products/{q['id']}")
    assert org.owner.get(f"/api/products/{q['id']}").status_code == 404


def test_price_change_is_audited(org):
    p = org.product(selling_price=15)
    org.owner.ok("put", f"/api/products/{p['id']}", json={"selling_price": 18})
    log = org.owner.ok("get", f"/api/audit-logs?entity_id={p['id']}")["items"][0]
    assert log["action"] == "product.price_change"
    assert log["before"] == {"selling_price": "15"} and log["after"] == {"selling_price": "18"}


def test_opening_stock_wizard(org):
    a, b = org.product(), org.product(base_unit_id=org.units["kg"])
    r = org.owner.ok("post", "/api/inventory/opening-stock", json={"lines": [
        {"product_id": a["id"], "location_id": org.main, "quantity": 12, "unit_cost": 3},
        {"product_id": b["id"], "location_id": org.main, "quantity": "4.5", "unit_cost": 8}]})
    assert r["lines"] == 2
    mv = org.owner.ok("get", "/api/movements?type=opening_balance")["items"]
    assert len(mv) == 2


def test_product_created_with_opening_quantity(org):
    p = org.owner.ok("post", "/api/products", json={"sku": "OPEN-1", "name": "Opened", "cost_price": 2,
                                                    "opening_quantity": 9, "opening_location_id": org.main})
    assert Decimal(p["on_hand"]) == 9
    assert org.owner.ok("get", "/api/movements")["items"][0]["movement_type"] == "opening_balance"


def test_barcode_lookup(org):
    p = org.product(barcode="5012345678900")
    org.receive(p["id"], 8)
    r = org.owner.ok("get", "/api/products/lookup?code=5012345678900")
    assert r["product"]["id"] == p["id"] and Decimal(r["on_hand"]) == 8
    assert r["locations"][0]["location_name"] == "Main Store"
    assert r["recent_movements"][0]["movement_type"] == "purchase_receipt"
    assert org.owner.ok("get", f"/api/products/lookup?code={p['sku']}")["product"]["id"] == p["id"]
    assert org.owner.get("/api/products/lookup?code=nothing").status_code == 404
