"""Demo data. Safe to run repeatedly: it does nothing if the demo company exists.

    python seed.py            # create demo company if missing
    python seed.py --reset    # (dev only) wipe all data first

Every demo user's password is in DEMO_PASSWORD below.
"""
import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import (
    Category, Customer, Location, LocationKind, MovementType, Product, ProductUnitConversion, Role, Supplier,
    UnitOfMeasure, User, UserWarehouse, Warehouse, WarehouseType,
)
from app.security import hash_password

from app.services import inventory as inv
from app.services import monitors
from app.services import orders as ord_svc
from app.services import production as prod_svc
from app.services import variants as var_svc
from app.services.org import bootstrap_organization, create_warehouse, ensure_permissions

DEMO_EMAIL_DOMAIN = "demo.stockpilot.app"
DEMO_PASSWORD = "Demo-Password-2026"
ROLES = {
    "Owner": "owner", "Administrator": "admin", "Manager": "manager", "Warehouse Staff": "warehouse",
    "Sales Staff": "sales", "Purchasing Staff": "purchasing", "Viewer": "viewer", "Cashier": "cashier",
}


def reset(db):
    tables = db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'")).scalars().all()
    db.execute(text("ALTER TABLE stock_movements DISABLE TRIGGER stock_movements_immutable"))
    db.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_immutable"))
    db.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    db.execute(text("ALTER TABLE stock_movements ENABLE TRIGGER stock_movements_immutable"))
    db.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_immutable"))
    db.commit()


def main():
    db = SessionLocal()
    if "--reset" in sys.argv:
        reset(db)
    ensure_permissions(db)
    if db.execute(select(User).where(User.email == f"owner@{DEMO_EMAIL_DOMAIN}")).first():
        print("Demo data already present. Use --reset to recreate.")
        db.commit()
        return

    org, owner = bootstrap_organization(db, org_name="Acme Supplies Ltd", industry="wholesale", currency="NGN",
                                        owner_name="Ada Owner", owner_email=f"owner@{DEMO_EMAIL_DOMAIN}",
                                        owner_password=DEMO_PASSWORD)
    org.settings = {**org.settings, "modules": {**org.settings["modules"], "batches": True, "expiry": True, "serials": True,
                                                "variants": True, "production": True, "pos": True},
                    "pos": {"receipt_header": "12 Allen Avenue, Ikeja · 0803 000 0000",
                            "receipt_footer": "Thank you for shopping with us! Exchanges within 7 days with this receipt."}}
    owner.email_verified = True
    db.flush()

    roles = {r.name: r for r in db.execute(select(Role).where(Role.org_id == org.id)).scalars()}
    units = {u.abbreviation: u for u in db.execute(select(UnitOfMeasure).where(UnitOfMeasure.org_id == org.id)).scalars()}

    main_wh = db.execute(select(Warehouse).where(Warehouse.org_id == org.id, Warehouse.code == "MAIN")).scalar_one()
    main_wh.name, main_wh.type = "Main Warehouse", WarehouseType.WAREHOUSE
    shop = create_warehouse(db, org.id, "Ikeja Shop", "IKJ", WarehouseType.STORE, "12 Allen Avenue, Ikeja")
    van = create_warehouse(db, org.id, "Delivery Van 1", "VAN1", WarehouseType.VEHICLE)
    rack = Location(org_id=org.id, warehouse_id=main_wh.id, name="Aisle A · Rack 1", code="A-R1", kind=LocationKind.RACK)
    db.add(rack)
    db.flush()
    loc = {w.id: db.execute(select(Location).where(Location.warehouse_id == w.id, Location.kind == LocationKind.DEFAULT)).scalar_one()
           for w in (main_wh, shop, van)}
    L_MAIN, L_SHOP, L_VAN = loc[main_wh.id], loc[shop.id], loc[van.id]

    users = {"Owner": owner}
    for role_name, handle in ROLES.items():
        if role_name == "Owner":
            continue
        u = User(org_id=org.id, email=f"{handle}@{DEMO_EMAIL_DOMAIN}", full_name=f"{handle.title()} Demo",
                 password_hash=hash_password(DEMO_PASSWORD), role_id=roles[role_name].id, email_verified=True)
        db.add(u)
        users[role_name] = u
    db.flush()
    # Sales staff and the cashier only work in the shop.
    db.add(UserWarehouse(user_id=users["Sales Staff"].id, warehouse_id=shop.id))
    db.add(UserWarehouse(user_id=users["Cashier"].id, warehouse_id=shop.id))

    cats = {n: Category(org_id=org.id, name=n) for n in ("Pharmacy", "Electronics", "Groceries")}
    db.add_all(cats.values())
    sup = {
        "medi": Supplier(org_id=org.id, name="MediPlus Distributors", contact_name="Tunde Bello", email="orders@mediplus.example",
                         phone="+234 801 000 0001"),
        "tech": Supplier(org_id=org.id, name="TechHub Wholesale", contact_name="Grace Okafor", email="sales@techhub.example",
                         phone="+234 802 000 0002"),
    }
    db.add_all(sup.values())
    db.add_all([
        Customer(org_id=org.id, name="Sunrise Clinic", contact_name="Dr. Amaka Eze", email="procurement@sunrise.example", phone="+234 803 000 0003"),
        Customer(org_id=org.id, name="Lekki Mart", contact_name="Femi Ade", email="buying@lekkimart.example", phone="+234 804 000 0004"),
    ])
    db.flush()

    def product(sku, name, cat, unit, cost, price, reorder, reorder_qty, supplier, barcode=None, batches=False, serials=False):
        p = Product(org_id=org.id, sku=sku, name=name, category_id=cats[cat].id, base_unit_id=units[unit].id,
                    cost_price=Decimal(cost), selling_price=Decimal(price), average_cost=Decimal(cost),
                    reorder_level=Decimal(reorder), reorder_qty=Decimal(reorder_qty), preferred_supplier_id=sup[supplier].id,
                    barcode=barcode, track_batches=batches, track_expiry=batches, track_serials=serials, brand=None)
        db.add(p)
        db.flush()
        return p

    P = {
        "para": product("PH-001", "Paracetamol 500mg (strip of 10)", "Pharmacy", "pcs", 250, 400, 50, 200, "medi", "6151100000019", batches=True),
        "amox": product("PH-002", "Amoxicillin 250mg capsules", "Pharmacy", "pcs", 900, 1500, 40, 100, "medi", "6151100000026", batches=True),
        "ors": product("PH-003", "Oral Rehydration Salts sachet", "Pharmacy", "pcs", 120, 200, 100, 300, "medi", "6151100000033", batches=True),
        "gloves": product("PH-004", "Nitrile gloves (box of 100)", "Pharmacy", "box", 4500, 6500, 10, 20, "medi", "6151100000040"),
        "laptop": product("EL-001", "ProBook 14 laptop", "Electronics", "pcs", 450000, 560000, 2, 5, "tech", "6151100000057", serials=True),
        "phone": product("EL-002", "Smartphone X2 128GB", "Electronics", "pcs", 180000, 235000, 3, 10, "tech", "6151100000064", serials=True),
        "cable": product("EL-003", "USB-C charging cable 1m", "Electronics", "pcs", 1200, 2500, 30, 100, "tech", "6151100000071"),
        "rice": product("GR-001", "Parboiled rice", "Groceries", "kg", 1100, 1500, 100, 500, "medi", "6151100000088"),
        "oil": product("GR-002", "Vegetable oil", "Groceries", "l", 2200, 2900, 40, 120, "medi", "6151100000095"),
        "water": product("GR-003", "Table water 75cl", "Groceries", "pcs", 80, 150, 120, 480, "medi", "6151100000101"),
    }
    # 1 carton of water = 12 bottles; 1 pack of cables = 10 cables
    db.add(ProductUnitConversion(org_id=org.id, product_id=P["water"].id, unit_id=units["ctn"].id, factor=Decimal(12)))
    db.add(ProductUnitConversion(org_id=org.id, product_id=P["cable"].id, unit_id=units["pack"].id, factor=Decimal(10)))
    db.flush()

    ctx_owner = inv.Ctx(org_id=org.id, user_id=owner.id, org_settings=org.settings)
    ctx_wh = inv.Ctx(org_id=org.id, user_id=users["Warehouse Staff"].id, org_settings=org.settings)
    today = date.today()
    OB = MovementType.OPENING_BALANCE

    # Opening stock
    inv.receive(db, ctx_owner, product_id=P["para"].id, location_id=L_MAIN.id, quantity=300, unit_cost=240,
                batch_number="PARA-2401", expires_on=today + timedelta(days=20), mtype=OB, note="Opening stock")
    inv.receive(db, ctx_owner, product_id=P["para"].id, location_id=L_MAIN.id, quantity=400, unit_cost=250,
                batch_number="PARA-2407", expires_on=today + timedelta(days=300), mtype=OB, note="Opening stock")
    inv.receive(db, ctx_owner, product_id=P["amox"].id, location_id=L_MAIN.id, quantity=60, unit_cost=900,
                batch_number="AMX-0915", expires_on=today + timedelta(days=5), mtype=OB, note="Opening stock")  # expiring this week
    inv.receive(db, ctx_owner, product_id=P["ors"].id, location_id=L_MAIN.id, quantity=40, unit_cost=120,
                batch_number="ORS-0311", expires_on=today - timedelta(days=3), mtype=OB, note="Opening stock")  # already expired
    inv.receive(db, ctx_owner, product_id=P["gloves"].id, location_id=rack.id, quantity=25, unit_cost=4500, mtype=OB)
    inv.receive(db, ctx_owner, product_id=P["laptop"].id, location_id=L_MAIN.id, quantity=4, unit_cost=450000,
                serials=["PB14-A1001", "PB14-A1002", "PB14-A1003", "PB14-A1004"], mtype=OB)
    inv.receive(db, ctx_owner, product_id=P["phone"].id, location_id=L_MAIN.id, quantity=3, unit_cost=180000,
                serials=["X2-77001", "X2-77002", "X2-77003"], mtype=OB)
    inv.receive(db, ctx_owner, product_id=P["cable"].id, location_id=L_MAIN.id, quantity=15, unit_id=units["pack"].id,
                unit_cost=12000, mtype=OB)
    inv.receive(db, ctx_owner, product_id=P["rice"].id, location_id=L_MAIN.id, quantity=Decimal("750.5"), unit_cost=1100, mtype=OB)
    inv.receive(db, ctx_owner, product_id=P["oil"].id, location_id=L_MAIN.id, quantity=30, unit_cost=2200, mtype=OB)  # low stock
    # water: none → out of stock

    # Day-to-day activity
    inv.receive(db, ctx_wh, product_id=P["gloves"].id, location_id=L_MAIN.id, quantity=10, unit_cost=4700,
                supplier_id=sup["medi"].id, reference_no="INV-88213", counterparty="MediPlus Distributors")
    inv.quick_transfer(db, ctx_wh, from_location_id=L_MAIN.id, to_location_id=L_SHOP.id, notes="Weekly shop restock",
                       items=[{"product_id": P["para"].id, "quantity": 120}, {"product_id": P["cable"].id, "quantity": 60},
                              {"product_id": P["phone"].id, "quantity": 2, "serials": ["X2-77001", "X2-77002"]}])
    inv.quick_transfer(db, ctx_wh, from_location_id=L_MAIN.id, to_location_id=L_VAN.id,
                       items=[{"product_id": P["rice"].id, "quantity": 100}])
    ctx_sales = inv.Ctx(org_id=org.id, user_id=users["Sales Staff"].id, org_settings=org.settings,
                        allowed_warehouse_ids={shop.id})
    inv.issue(db, ctx_sales, product_id=P["para"].id, location_id=L_SHOP.id, quantity=35, mtype=MovementType.SALE,
              counterparty="Sunrise Clinic", reference_no="RCPT-1001")
    inv.issue(db, ctx_sales, product_id=P["phone"].id, location_id=L_SHOP.id, quantity=1, serials=["X2-77001"],
              mtype=MovementType.SALE, counterparty="Lekki Mart", reference_no="RCPT-1002")
    inv.issue(db, ctx_sales, product_id=P["cable"].id, location_id=L_SHOP.id, quantity=22, mtype=MovementType.SALE,
              counterparty="Walk-in customer")
    inv.issue(db, ctx_wh, product_id=P["laptop"].id, location_id=L_MAIN.id, quantity=1, serials=["PB14-A1004"],
              mtype=MovementType.ISSUE, counterparty="IT department", note="Staff laptop")
    inv.issue(db, ctx_wh, product_id=P["rice"].id, location_id=L_VAN.id, quantity=Decimal("62.5"), mtype=MovementType.SALE,
              counterparty="Lekki Mart")
    inv.adjust(db, ctx_owner, reason="damaged", notes="Crushed in storage",
               lines=[{"product_id": P["gloves"].id, "location_id": rack.id, "change": -2}])
    # --- Phase 2 demo: sizes/colours, production, purchase and sales orders
    clothing = Category(org_id=org.id, name="Clothing")
    db.add(clothing)
    db.flush()
    tee = Product(org_id=org.id, sku="CL-TEE", name="Adire T-shirt", category_id=clothing.id, base_unit_id=units["pcs"].id,
                  cost_price=Decimal(3500), selling_price=Decimal(9000), average_cost=Decimal(3500), reorder_level=Decimal(3),
                  reorder_qty=Decimal(12))
    db.add(tee)
    db.flush()
    sizes = var_svc.add_variants(db, ctx_owner, tee, {"Size": ["S", "M", "L"], "Colour": ["Indigo", "Rust"]})
    for i, v in enumerate(sizes):
        if i != 4:  # leave L / Indigo empty so it shows as out of stock
            inv.receive(db, ctx_owner, product_id=v.id, location_id=L_SHOP.id, quantity=[6, 2, 8, 5, 0, 1][i] or 1,
                        unit_cost=3500, mtype=OB, note="Opening stock")

    pack = Product(org_id=org.id, sku="GR-PACK", name="Family food pack", category_id=cats["Groceries"].id,
                   base_unit_id=units["pcs"].id, selling_price=Decimal(12000), cost_price=Decimal(0), average_cost=Decimal(0))
    db.add(pack)
    db.flush()
    prod_svc.set_components(db, ctx_owner, pack.id, [{"component_id": P["rice"].id, "quantity": 5},
                                                     {"component_id": P["oil"].id, "quantity": 1}])
    prod_svc.run(db, ctx_wh, product_id=pack.id, location_id=L_MAIN.id, quantity=4, notes="Weekend promotion packs")

    today = date.today()
    po_late = ord_svc.create_po(db, ctx_owner, supplier_id=sup["medi"].id, destination_location_id=L_MAIN.id,
                                expected_date=today - timedelta(days=3), supplier_reference="QUOTE-5521", confirm=True,
                                lines=[{"product_id": P["gloves"].id, "quantity": 20, "unit_cost": 4600},
                                       {"product_id": P["water"].id, "quantity": 96, "unit_cost": 75}])
    ord_svc.receive_po(db, ctx_wh, po_late, [{"item_id": po_late.items[0].id, "quantity": 12}])
    ord_svc.create_po(db, ctx_owner, supplier_id=sup["tech"].id, destination_location_id=L_MAIN.id,
                      expected_date=today + timedelta(days=10),
                      lines=[{"product_id": P["phone"].id, "quantity": 5, "unit_cost": 178000},
                             {"product_id": P["cable"].id, "quantity": 100, "unit_cost": 1150}])

    customers = {c.name: c for c in db.execute(select(Customer).where(Customer.org_id == org.id)).scalars()}
    so_paid = ord_svc.create_so(db, ctx_sales, customer_id=customers["Sunrise Clinic"].id, source_location_id=L_SHOP.id,
                                customer_reference="SC-PO-310", confirm=True,
                                lines=[{"product_id": P["para"].id, "quantity": 20, "discount_percent": 5},
                                       {"product_id": sizes[0].id, "quantity": 2}])
    ord_svc.fulfil_so(db, ctx_sales, so_paid, None)
    ord_svc.record_payment(db, ctx_sales, so_paid, None)
    so_open = ord_svc.create_so(db, ctx_sales, customer_id=customers["Lekki Mart"].id, source_location_id=L_SHOP.id, confirm=True,
                                lines=[{"product_id": P["cable"].id, "quantity": 30, "unit_price": 2300},
                                       {"product_id": sizes[2].id, "quantity": 4}])
    ord_svc.fulfil_so(db, ctx_sales, so_open, [{"item_id": so_open.items[0].id, "quantity": 20}])
    ord_svc.record_payment(db, ctx_sales, so_open, Decimal(20000))

    # --- Phase 3 demo: counter sales at the Ikeja shop, yesterday's cash-up, a return, a promotion
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo
    from app.models import Promotion
    from app.permissions import ROLE_TEMPLATES
    from app.services import pos as pos_svc
    db.add(Promotion(org_id=org.id, name="Adire week: 10% off clothing", kind="percent", value=Decimal(10), scope="category",
                     category_id=clothing.id, min_qty=Decimal(1), starts_on=today - timedelta(days=2), ends_on=today + timedelta(days=5),
                     is_active=True, product_ids=[]))
    db.flush()
    cashier = users["Cashier"]
    ctx_till = inv.Ctx(org_id=org.id, user_id=cashier.id, org_settings=org.settings, allowed_warehouse_ids={shop.id})
    perms = ROLE_TEMPLATES["Cashier"][1]
    lagos = ZoneInfo("Africa/Lagos")

    def at(day, hh, mm):
        return datetime.combine(day, time(hh, mm), tzinfo=lagos).astimezone(timezone.utc)

    def till_sale(lines, payments, when, **kw):
        sale, _ = pos_svc.create_sale(db, ctx_till, perms, org, {"shift_id": shift.id, "lines": lines, "payments": payments,
                                                                 "sold_at": when, **kw})
        return sale

    yday = today - timedelta(days=1)
    shift = pos_svc.open_shift(db, ctx_till, location_id=L_SHOP.id, opening_float=Decimal(10000))
    shift.opened_at = at(yday, 8, 30)
    till_sale([{"product_id": P["cable"].id, "quantity": 2}], [{"method": "cash", "amount": 5000}], at(yday, 9, 12))
    till_sale([{"product_id": sizes[0].id, "quantity": 1}, {"product_id": P["para"].id, "quantity": 2}],
              [{"method": "transfer", "amount": 8900, "reference": "GTB 2231"}], at(yday, 11, 40), customer_name="Chioma",
              customer_phone="0803 555 0101")
    till_sale([{"product_id": P["para"].id, "quantity": 3}], [{"method": "cash", "amount": 1500}], at(yday, 13, 5))
    till_sale([{"product_id": sizes[3].id, "quantity": 2}], [{"method": "card", "amount": 16200}], at(yday, 16, 48))
    pos_svc.cash_movement(db, ctx_till, perms, shift, kind="out", amount=2000, reason="Fuel for the generator")
    summary = pos_svc.shift_summary(db, shift)
    pos_svc.close_shift(db, ctx_till, perms, shift, counted_cash=summary["expected_cash"] - 200, note="Short by 200, checked twice")
    shift.closed_at = at(yday, 19, 2)

    shift = pos_svc.open_shift(db, ctx_till, location_id=L_SHOP.id, opening_float=Decimal(10000))
    now_lagos = datetime.now(lagos)
    first = now_lagos.replace(hour=max(8, min(now_lagos.hour, 20)), minute=0, second=0, microsecond=0)
    shift.opened_at = (first - timedelta(hours=1)).astimezone(timezone.utc)
    s1 = till_sale([{"product_id": sizes[2].id, "quantity": 2}, {"product_id": P["cable"].id, "quantity": 1}],
                   [{"method": "cash", "amount": 20000}], first.astimezone(timezone.utc), customer_name="Bayo")
    till_sale([{"product_id": P["para"].id, "quantity": 5}], [{"method": "transfer", "amount": 2000}],
              (first + timedelta(minutes=20)).astimezone(timezone.utc))
    owner_ctx_till = inv.Ctx(org_id=org.id, user_id=owner.id, org_settings=org.settings)
    owner_shift = pos_svc.open_shift(db, owner_ctx_till, location_id=L_SHOP.id, opening_float=Decimal(0))
    pos_svc.create_return(db, owner_ctx_till, ROLE_TEMPLATES["Owner"][1], org, {
        "shift_id": owner_shift.id, "original_sale_id": str(s1.id), "refund_method": "credit", "reason": "Wrong size, will exchange",
        "lines": [{"original_item_id": str(s1.items[0].id), "quantity": 1}]})
    pos_svc.close_shift(db, owner_ctx_till, ROLE_TEMPLATES["Owner"][1], owner_shift, counted_cash=0, note=None)

    monitors.check_org(db, org.id)
    db.commit()

    print("Demo company created: Acme Supplies Ltd")
    print(f"Password for every demo user: {DEMO_PASSWORD}")
    for role_name, handle in ROLES.items():
        print(f"  {role_name:<17} {handle}@{DEMO_EMAIL_DOMAIN}")


if __name__ == "__main__":
    main()
