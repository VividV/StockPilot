from decimal import Decimal


def setup(org):
    org.owner.ok("put", "/api/settings", json={"modules": {"production": True}})
    fabric = org.product(name="Cotton fabric", base_unit_id=org.units["m"], cost_price=0)
    dye = org.product(name="Indigo dye", base_unit_id=org.units["l"], cost_price=0)
    shirt = org.product(name="Adire shirt", cost_price=0)
    org.receive(fabric["id"], 20, unit_cost=1000)   # per metre
    org.receive(dye["id"], 5, unit_cost=2000)       # per litre
    org.owner.ok("put", f"/api/products/{shirt['id']}/components", json={"components": [
        {"component_id": fabric["id"], "quantity": "1.5"}, {"component_id": dye["id"], "quantity": "0.25"}]})
    return fabric, dye, shirt


def test_bill_of_materials_and_production_run(org):
    fabric, dye, shirt = setup(org)
    bom = org.owner.ok("get", f"/api/products/{shirt['id']}/components")
    assert [(c["name"], Decimal(c["quantity"])) for c in bom] == [("Cotton fabric", Decimal("1.5")), ("Indigo dye", Decimal("0.25"))]
    av = org.owner.ok("get", f"/api/production/availability?product_id={shirt['id']}&location_id={org.main}&quantity=10")
    assert Decimal(str(av["max_quantity"])) == 13  # 20/1.5 = 13.3, 5/0.25 = 20
    assert Decimal(str(av["estimated_unit_cost"])) == 2000  # 1.5*1000 + 0.25*2000
    assert [Decimal(str(x["needed"])) for x in av["lines"]] == [15, Decimal("2.5")]

    r = org.owner.ok("post", "/api/production-runs", json={"product_id": shirt["id"], "location_id": org.main, "quantity": 10})
    assert r["number"].startswith("MO-") and Decimal(r["unit_cost"]) == 2000 and Decimal(r["total_cost"]) == 20000
    types = sorted((m["movement_type"], m["product_name"], Decimal(m["quantity"])) for m in r["movements"])
    assert types == [("production_consumption", "Cotton fabric", -15), ("production_consumption", "Indigo dye", Decimal("-2.5")),
                     ("production_output", "Adire shirt", 10)]
    assert org.on_hand(fabric["id"]) == 5 and org.on_hand(dye["id"]) == 2.5 and org.on_hand(shirt["id"]) == 10
    shirt_now = org.owner.ok("get", f"/api/products/{shirt['id']}")["product"]
    assert Decimal(shirt_now["average_cost"]) == 2000
    # valuation is unchanged by production: materials became shirts
    val = Decimal(str(org.owner.ok("get", "/api/reports/valuation")["summary"]["total_value"]))
    assert val == Decimal("30000.00")


def test_production_refuses_when_materials_short(org):
    fabric, dye, shirt = setup(org)
    r = org.owner.post("/api/production-runs", json={"product_id": shirt["id"], "location_id": org.main, "quantity": 14})
    assert r.status_code == 409 and "Cotton fabric" in r.json()["detail"]
    assert org.on_hand(shirt["id"]) == 0 and org.on_hand(dye["id"]) == 5  # nothing half-done


def test_actual_usage_override_and_batches(org):
    fabric, dye, shirt = setup(org)
    org.owner.ok("put", f"/api/products/{shirt['id']}", json={"track_batches": True})
    r = org.owner.ok("post", "/api/production-runs", json={"product_id": shirt["id"], "location_id": org.main, "quantity": 2,
                                                          "actual": {fabric["id"]: 4}, "notes": "Cutting waste"})
    out = next(m for m in r["movements"] if m["movement_type"] == "production_output")
    assert out["batch_number"] == r["number"]  # batch defaults to the run number
    assert Decimal(r["unit_cost"]) == 2500  # (4 m x 1000 + 0.5 l x 2000) / 2 shirts


def test_bom_rules(org):
    org.owner.ok("put", "/api/settings", json={"modules": {"production": True}})
    a, b = org.product(name="A"), org.product(name="B")
    assert org.owner.put(f"/api/products/{a['id']}/components", json={"components": [{"component_id": a["id"], "quantity": 1}]}).status_code == 400
    org.owner.ok("put", f"/api/products/{a['id']}/components", json={"components": [{"component_id": b["id"], "quantity": 1}]})
    r = org.owner.put(f"/api/products/{b['id']}/components", json={"components": [{"component_id": a["id"], "quantity": 1}]})
    assert r.status_code == 400 and r.json()["code"] == "bom_cycle"


def test_production_permissions_and_module(org):
    fabric, dye, shirt = setup(org)
    assert org.user("Sales Staff").post("/api/production-runs", json={"product_id": shirt["id"], "location_id": org.main,
                                                                      "quantity": 1}).status_code == 403
    org.user("Warehouse Staff").ok("post", "/api/production-runs", json={"product_id": shirt["id"], "location_id": org.main, "quantity": 1})
    org.owner.ok("put", "/api/settings", json={"modules": {"production": False}})
    r = org.owner.post("/api/production-runs", json={"product_id": shirt["id"], "location_id": org.main, "quantity": 1})
    assert r.status_code == 400 and r.json()["code"] == "module_off"
