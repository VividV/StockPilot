from decimal import Decimal

from app.services.barcodes import ean13_check_digit, is_valid_ean13


def test_create_product_with_variants(org):
    p = org.owner.ok("post", "/api/products", json={"sku": "TS", "name": "Adire T-shirt", "selling_price": 6000, "cost_price": 2500,
                                                    "reorder_level": 3, "variant_options": {"Size": ["S", "M"], "Colour": ["Indigo", "Rust"]}})
    assert p["has_variants"] and p["variant_count"] == 4
    d = org.owner.ok("get", f"/api/products/{p['id']}")
    skus = sorted(v["sku"] for v in d["variants"])
    assert skus == ["TS-M-INDIGO", "TS-M-RUST", "TS-S-INDIGO", "TS-S-RUST"]
    v = next(v for v in d["variants"] if v["sku"] == "TS-M-RUST")
    assert v["name"] == "Adire T-shirt — M / Rust" and v["variant_attributes"] == {"Size": "M", "Colour": "Rust"}
    assert Decimal(v["selling_price"]) == 6000 and Decimal(v["reorder_level"]) == 3 and v["parent_id"] == p["id"]
    child = org.owner.ok("get", f"/api/products/{v['id']}")
    assert child["parent"]["id"] == p["id"]


def test_variant_stock_and_parent_rules(org):
    p = org.owner.ok("post", "/api/products", json={"sku": "SH", "name": "Shoe", "variant_options": {"Size": ["40", "41"]}})
    kids = {v["sku"]: v for v in org.owner.ok("get", f"/api/products/{p['id']}")["variants"]}
    # the parent can't hold stock
    r = org.owner.post("/api/inventory/receive", json={"product_id": p["id"], "location_id": org.main, "quantity": 5})
    assert r.status_code == 400 and r.json()["code"] == "variant_required"
    org.receive(kids["SH-40"]["id"], 5)
    org.receive(kids["SH-41"]["id"], 2)
    d = org.owner.ok("get", f"/api/products/{p['id']}")
    assert Decimal(d["on_hand"]) == 7 and len(d["recent_movements"]) == 2
    # list: parent shows the total; sizes hidden unless searching
    lst = org.owner.ok("get", "/api/products")["items"]
    assert [(x["sku"], Decimal(x["on_hand"])) for x in lst] == [("SH", 7)]
    found = {x["sku"] for x in org.owner.ok("get", "/api/products?q=SH-4")["items"]}
    assert found == {"SH-40", "SH-41"}
    # parents are not counted as out of stock
    assert org.owner.ok("get", "/api/dashboard")["out_of_stock_count"] == 0
    # orders refuse the parent
    r = org.owner.post("/api/sales-orders", json={"source_location_id": org.main, "lines": [{"product_id": p["id"], "quantity": 1}]})
    assert r.status_code == 400 and r.json()["code"] == "variant_required"


def test_add_more_variant_values_and_cascade(org):
    p = org.owner.ok("post", "/api/products", json={"sku": "DR", "name": "Dress", "selling_price": 100,
                                                    "variant_options": {"Size": ["S", "M"]}})
    r = org.owner.ok("post", f"/api/products/{p['id']}/variants", json={"options": {"Size": ["M", "L", "XL"]}})
    assert sorted(x["sku"] for x in r["created"]) == ["DR-L", "DR-XL"]
    assert org.owner.ok("get", f"/api/products/{p['id']}")["product"]["variant_options"] == {"Size": ["S", "M", "L", "XL"]}
    r = org.owner.post(f"/api/products/{p['id']}/variants", json={"options": {"Colour": ["Red"]}})
    assert r.status_code == 400 and "can't be added or removed" in r.json()["detail"]
    org.owner.ok("put", f"/api/products/{p['id']}", json={"selling_price": 150, "apply_to_variants": True})
    prices = {Decimal(v["selling_price"]) for v in org.owner.ok("get", f"/api/products/{p['id']}")["variants"]}
    assert prices == {Decimal(150)}
    log = org.owner.ok("get", f"/api/audit-logs?entity_id={p['id']}")["items"][0]
    assert "and 4 variants" in log["summary"]


def test_cannot_turn_stocked_product_into_variants(org):
    p = org.product()
    org.receive(p["id"], 1)
    r = org.owner.post(f"/api/products/{p['id']}/variants", json={"options": {"Size": ["S"]}})
    assert r.status_code == 400 and r.json()["code"] == "has_stock"


def test_delete_variant_group(org):
    p = org.owner.ok("post", "/api/products", json={"sku": "HAT", "name": "Hat", "variant_options": {"Colour": ["Red", "Blue"]}})
    kid = org.owner.ok("get", f"/api/products/{p['id']}")["variants"][0]
    org.receive(kid["id"], 1)
    r = org.owner.ok("delete", f"/api/products/{p['id']}")
    assert "deactivated" in r["message"]
    assert org.owner.ok("get", f"/api/products/{kid['id']}")["product"]["is_active"] is False
    q = org.owner.ok("post", "/api/products", json={"sku": "CAP", "name": "Cap", "variant_options": {"Colour": ["Red"]}})
    org.owner.ok("delete", f"/api/products/{q['id']}")
    assert org.owner.ok("get", "/api/products?q=CAP")["total"] == 0


# ------------------------------------------------------------------ barcodes & labels
def test_ean13_helpers():
    assert ean13_check_digit("400638133393") == "1"
    assert is_valid_ean13("4006381333931") and not is_valid_ean13("4006381333932")


def test_generate_barcodes(org):
    a, b = org.product(name="A"), org.product(name="B", barcode="5012345678900")
    r = org.owner.ok("post", "/api/products/barcodes/generate", json={"all_missing": True})
    assert [x["name"] for x in r["products"]] == ["A"]
    code = r["products"][0]["barcode"]
    assert code.startswith("20") and is_valid_ean13(code)
    assert org.owner.ok("get", f"/api/products/lookup?code={code}")["product"]["id"] == a["id"]
    r = org.owner.ok("post", "/api/products/barcodes/generate", json={"product_ids": [b["id"]]})
    assert r["products"] == []
    c = org.product(name="C")
    code2 = org.owner.ok("post", "/api/products/barcodes/generate", json={"product_ids": [c["id"]]})["products"][0]["barcode"]
    assert code2 != code


def test_label_pdf(org):
    a = org.product(name="Very long product name that will not fit on a small label at all", barcode="4006381333931", selling_price=12)
    b = org.product(name="No barcode yet")
    layouts = {l["key"] for l in org.owner.ok("get", "/api/labels/layouts")}
    assert {"a4-3x8", "thermal-50x25"} <= layouts
    for layout in layouts:
        r = org.owner.post("/api/labels", json={"items": [{"product_id": a["id"], "copies": 30}, {"product_id": b["id"]}], "layout": layout})
        assert r.status_code == 200 and r.content.startswith(b"%PDF"), layout
    # printing gave B a barcode
    assert org.owner.ok("get", f"/api/products/{b['id']}")["product"]["barcode"]
    # products without a standard barcode print their SKU as Code 128
    c = org.product(name="Odd code", barcode="ABC-123/x")
    d = org.product(name="SKU only")
    r = org.owner.post("/api/labels", json={"items": [{"product_id": c["id"]}, {"product_id": d["id"]}], "generate_missing": False})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert org.owner.ok("get", f"/api/products/{d['id']}")["product"]["barcode"] is None
    viewer = org.user("Viewer")
    assert viewer.post("/api/labels", json={"items": [{"product_id": a["id"]}]}).status_code == 200
    assert viewer.post("/api/products/barcodes/generate", json={"all_missing": True}).status_code == 403
