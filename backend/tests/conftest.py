"""Tests run against a real PostgreSQL database (row locks, partial indexes and
triggers are part of what we test). Set TEST_DATABASE_URL to point elsewhere."""
import os
import uuid

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/stockpilot_test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("ATTACHMENTS_DIR", __import__("tempfile").mkdtemp(prefix="sp-att-"))

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.security import limiter  # noqa: E402

PASSWORD = "Test-Password-123"
HERE = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="session", autouse=True)
def database():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(HERE, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _reset_limits():
    limiter.reset()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class Api:
    """Small helper: an authenticated client for one user."""

    def __init__(self, client: TestClient, token: str):
        self.c = client
        self.h = {"Authorization": f"Bearer {token}"}

    def get(self, url, **kw):
        return self.c.get(url, headers=self.h, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json, headers=self.h, **kw)

    def put(self, url, json=None, **kw):
        return self.c.put(url, json=json, headers=self.h, **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self.h, **kw)

    def ok(self, method, url, json=None, status=(200, 201), **kw):
        r = getattr(self, method)(url, json=json, **kw) if method != "get" and method != "delete" else getattr(self, method)(url, **kw)
        assert r.status_code in (status if isinstance(status, tuple) else (status,)), f"{method.upper()} {url} -> {r.status_code} {r.text}"
        return r.json()


class Org:
    def __init__(self, client: TestClient, industry: str = "other", **settings):
        self.client = client
        self.email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
        r = client.post("/api/auth/signup", json={"organization_name": "Test Co", "industry": industry, "currency": "USD",
                                                  "full_name": "Olu Owner", "email": self.email, "password": PASSWORD})
        assert r.status_code == 201, r.text
        self.owner = Api(client, r.json()["access_token"])
        me = self.owner.ok("get", "/api/auth/me")
        self.org_id = me["organization"]["id"]
        if settings:
            self.owner.ok("put", "/api/settings", json=settings)
        self.units = {u["abbreviation"]: u["id"] for u in self.owner.ok("get", "/api/units")}
        self.roles = {r["name"]: r["id"] for r in self.owner.ok("get", "/api/roles")}
        whs = self.owner.ok("get", "/api/warehouses")
        self.main = whs[0]["default_location_id"]
        self.main_wh = whs[0]["id"]

    def user(self, role: str, warehouse_ids=None) -> Api:
        email = f"{role.split()[0].lower()}-{uuid.uuid4().hex[:8]}@example.com"
        self.owner.ok("post", "/api/users", json={"email": email, "full_name": f"{role} person", "password": PASSWORD,
                                                  "role_id": self.roles[role], "warehouse_ids": warehouse_ids or []})
        r = self.client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return Api(self.client, r.json()["access_token"])

    def warehouse(self, name="Second Store", type_="store") -> dict:
        return self.owner.ok("post", "/api/warehouses", json={"name": name, "type": type_})

    def product(self, **kw) -> dict:
        body = {"sku": f"SKU-{uuid.uuid4().hex[:6]}", "name": kw.pop("name", "Widget"), "cost_price": 10, "selling_price": 15}
        body.update(kw)
        return self.owner.ok("post", "/api/products", json=body)

    def receive(self, product_id, qty, location=None, api=None, **kw):
        return (api or self.owner).ok("post", "/api/inventory/receive",
                                      json={"product_id": product_id, "location_id": location or self.main, "quantity": qty, **kw})

    def on_hand(self, product_id) -> float:
        return float(self.owner.ok("get", f"/api/products/{product_id}")["on_hand"])


@pytest.fixture
def org(client) -> Org:
    return Org(client)


@pytest.fixture
def make_org(client):
    return lambda **kw: Org(client, **kw)
