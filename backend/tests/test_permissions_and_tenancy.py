"""Who may do what, and nobody ever sees another organisation's data."""


# ------------------------------------------------------------------ permissions
def test_viewer_is_read_only(org):
    p = org.product()
    viewer = org.user("Viewer")
    assert viewer.get("/api/products").status_code == 200
    assert viewer.get("/api/reports/valuation").status_code == 200
    assert viewer.post("/api/products", json={"sku": "V1", "name": "x"}).status_code == 403
    assert viewer.put(f"/api/products/{p['id']}", json={"selling_price": 1}).status_code == 403
    assert viewer.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 1}).status_code == 403
    assert viewer.get("/api/reports/valuation?format=csv").status_code == 403  # view yes, export no
    assert viewer.get("/api/users").status_code == 403


def test_warehouse_staff_can_move_stock_but_not_adjust_or_admin(org):
    p = org.product()
    staff = org.user("Warehouse Staff")
    org.receive(p["id"], 10, api=staff)
    assert staff.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 2}).status_code == 201
    r = staff.post("/api/inventory/adjust", json={"reason": "count_correction", "lines": [
        {"product_id": p["id"], "location_id": org.main, "counted_qty": 50}]})
    assert r.status_code == 403
    assert staff.put(f"/api/products/{p['id']}", json={"cost_price": 1}).status_code == 403
    assert staff.post("/api/warehouses", json={"name": "X"}).status_code == 403
    assert staff.get("/api/users").status_code == 403
    assert staff.put("/api/settings", json={"allow_negative_stock": True}).status_code == 403


def test_sales_staff_cannot_receive(org):
    p = org.product()
    sales = org.user("Sales Staff")
    assert sales.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 5}).status_code == 403


def test_location_restricted_user(org):
    shop = org.warehouse("Shop")
    p = org.product()
    org.receive(p["id"], 10)
    org.receive(p["id"], 10, location=shop["default_location_id"])
    clerk = org.user("Sales Staff", warehouse_ids=[shop["id"]])
    r = clerk.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 1})
    assert r.status_code == 400 and "not allowed" in r.json()["detail"]
    assert clerk.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": shop["default_location_id"],
                                                    "quantity": 1}).status_code == 201
    stock = clerk.ok("get", "/api/stock")["items"]
    assert {s["warehouse_id"] for s in stock} == {shop["id"]}
    assert [l["warehouse_id"] for l in clerk.ok("get", "/api/locations")] == [shop["id"]]


def test_no_privilege_escalation(org):
    manager_api = org.user("Manager")
    # managers cannot manage users at all
    assert manager_api.post("/api/users", json={"email": "x@example.com", "full_name": "x", "password": "Abcdefghij1",
                                                "role_id": org.roles["Owner"]}).status_code == 403
    admin = org.user("Administrator")
    # admin can add users, but a custom role cannot exceed the creator's permissions (admin has all, so ok);
    viewer = org.user("Viewer")
    assert viewer.post("/api/roles", json={"name": "Hacker", "permissions": ["users.manage"]}).status_code == 403
    r = admin.post("/api/roles", json={"name": "Counter", "permissions": ["inventory.view", "inventory.adjust", "bogus.perm"]})
    assert r.status_code == 422


def test_last_owner_cannot_be_demoted(org):
    me = org.owner.ok("get", "/api/auth/me")
    r = org.owner.put(f"/api/users/{me['id']}", json={"role_id": org.roles["Viewer"]})
    assert r.status_code == 422


def test_permission_change_is_audited(org):
    staff = org.user("Viewer")
    sid = staff.ok("get", "/api/auth/me")["id"]
    org.owner.ok("put", f"/api/users/{sid}", json={"role_id": org.roles["Manager"]})
    log = org.owner.ok("get", f"/api/audit-logs?entity_id={sid}")["items"][0]
    assert log["action"] == "user.permission_change"
    assert log["before"]["role"] == "Viewer" and log["after"]["role"] == "Manager"


# ------------------------------------------------------------------ tenant isolation
def test_other_org_cannot_see_or_touch_data(make_org):
    a, b = make_org(), make_org()
    pa = a.product(name="Secret A thing", barcode="A-BARCODE-1")
    a.receive(pa["id"], 10)
    sup = a.owner.ok("post", "/api/suppliers", json={"name": "A's supplier"})

    # read
    assert b.owner.get(f"/api/products/{pa['id']}").status_code == 404
    assert b.owner.get(f"/api/suppliers/{sup['id']}").status_code == 404
    assert b.owner.ok("get", "/api/products")["total"] == 0
    assert b.owner.ok("get", "/api/stock")["total"] == 0
    assert b.owner.ok("get", "/api/movements")["total"] == 0
    assert b.owner.ok("get", "/api/search?q=Secret")["results"] == []
    assert b.owner.get("/api/products/lookup?code=A-BARCODE-1").status_code == 404
    assert b.owner.ok("get", "/api/reports/valuation")["rows"] == []
    assert b.owner.ok("get", "/api/dashboard")["product_count"] == 0
    assert not any(l["action"].startswith("product") for l in b.owner.ok("get", "/api/audit-logs")["items"])

    # write
    assert b.owner.put(f"/api/products/{pa['id']}", json={"name": "pwned"}).status_code == 404
    assert b.owner.delete(f"/api/products/{pa['id']}").status_code == 404
    assert b.owner.post("/api/inventory/issue", json={"product_id": pa["id"], "location_id": b.main, "quantity": 1}).status_code == 404
    # B's product into A's location
    pb = b.product()
    assert b.owner.post("/api/inventory/receive", json={"product_id": pb["id"], "location_id": a.main, "quantity": 1}).status_code == 404
    # transfer into the other org's location
    b.receive(pb["id"], 5)
    r = b.owner.post("/api/inventory/transfer", json={"from_location_id": b.main, "to_location_id": a.main,
                                                       "product_id": pb["id"], "quantity": 1})
    assert r.status_code == 404
    assert a.on_hand(pa["id"]) == 10


def test_same_sku_allowed_in_different_orgs(make_org):
    a, b = make_org(), make_org()
    a.product(sku="SAME-1")
    b.product(sku="SAME-1")
    r = a.owner.post("/api/products", json={"sku": "SAME-1", "name": "dup"})
    assert r.status_code == 409


def test_cannot_assign_other_orgs_role_or_warehouse(make_org):
    a, b = make_org(), make_org()
    r = a.owner.post("/api/users", json={"email": "cross@example.com", "full_name": "x", "password": "Abcdefghij1",
                                         "role_id": b.roles["Owner"]})
    assert r.status_code == 404
    r = a.owner.post("/api/users", json={"email": "cross2@example.com", "full_name": "x", "password": "Abcdefghij1",
                                         "role_id": a.roles["Viewer"], "warehouse_ids": [b.main_wh]})
    assert r.status_code == 404
