"""Permission catalogue and the built-in role templates.

Roles are stored per organisation, so an owner can later edit them or create
custom roles. These templates are only used to create the defaults."""

PERMISSIONS: dict[str, str] = {
    "products.view": "View products",
    "products.create": "Create products",
    "products.edit": "Edit products and prices",
    "products.delete": "Delete or deactivate products",
    "inventory.view": "View stock levels and movement history",
    "inventory.receive": "Receive stock",
    "inventory.issue": "Issue / sell stock",
    "inventory.adjust": "Adjust stock (counts, damage, loss, expiry)",
    "inventory.transfer": "Create and dispatch / receive transfers",
    "inventory.transfer_approve": "Approve transfers",
    "locations.view": "View warehouses and locations",
    "locations.manage": "Create and edit warehouses and locations",
    "suppliers.view": "View suppliers",
    "suppliers.manage": "Create and edit suppliers",
    "customers.view": "View customers",
    "customers.manage": "Create and edit customers",
    "purchases.view": "View purchase orders",
    "purchases.create": "Create purchase orders",
    "purchases.receive": "Receive against purchase orders",
    "sales.view": "View sales orders",
    "sales.create": "Create sales orders",
    "sales.fulfill": "Fulfil sales orders",
    "production.run": "Record production (use materials, make products)",
    "reports.view": "View reports and dashboard",
    "reports.export": "Export reports and data",
    "audit.view": "View the audit log",
    "users.manage": "Invite, edit and deactivate users",
    "roles.manage": "Edit roles and permissions",
    "settings.manage": "Change organisation settings",
    "pos.sell": "Sell at the counter (checkout) and run their own till shift",
    "pos.discount": "Give discounts at the counter (up to the shop's limit)",
    "pos.price_override": "Change prices at the counter and give any discount",
    "pos.refund": "Take back goods and give refunds or store credit",
    "pos.manage": "See and close everyone's till shifts; record cash in/out for any shift",
    "promotions.manage": "Create and change promotions",
    "billing.manage": "Manage the StockPilot subscription and payments",
}

ALL = set(PERMISSIONS)
VIEW = {p for p in PERMISSIONS if p.endswith(".view")}

ROLE_TEMPLATES: dict[str, tuple[str, set[str]]] = {
    "Owner": ("Full access, including billing and ownership", ALL),
    "Administrator": ("Full access to everything except the subscription", ALL - {"billing.manage"}),
    "Manager": ("Runs operations; cannot manage users or settings",
                ALL - {"users.manage", "roles.manage", "settings.manage", "billing.manage"}),
    "Warehouse Staff": ("Receives, issues and moves stock", {
        "products.view", "inventory.view", "inventory.receive", "inventory.issue",
        "inventory.transfer", "locations.view", "suppliers.view", "purchases.view", "purchases.receive",
        "production.run",
    }),
    "Sales Staff": ("Sells stock to customers", {
        "products.view", "inventory.view", "inventory.issue", "locations.view",
        "customers.view", "customers.manage", "sales.view", "sales.create", "sales.fulfill", "pos.sell",
    }),
    "Cashier": ("Sells at the counter and runs a till", {
        "pos.sell", "products.view", "inventory.view", "customers.view", "customers.manage",
    }),
    "Purchasing Staff": ("Buys and receives stock", {
        "products.view", "inventory.view", "inventory.receive", "locations.view",
        "suppliers.view", "suppliers.manage", "purchases.view", "purchases.create", "purchases.receive",
        "reports.view",
    }),
    "Viewer": ("Read-only access", VIEW | {"reports.view"}),
}
