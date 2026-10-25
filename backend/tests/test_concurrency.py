"""Two employees must never be able to issue more stock than exists.

These tests run real parallel database transactions (and real parallel HTTP
requests against a live server) and check that the row locks hold."""
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest
import uvicorn

from app.db import SessionLocal
from app.main import app
from app.models import MovementType
from app.services import inventory as inv
from conftest import PASSWORD


@pytest.fixture(scope="module")
def live_server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", workers=1))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)


def _ctx(org):
    from sqlalchemy import select
    from app.models import Organization, User
    s = SessionLocal()
    o = s.get(Organization, org.org_id)
    owner = s.execute(select(User).where(User.email == org.email)).unique().scalar_one()
    ctx = inv.Ctx(org_id=o.id, user_id=owner.id, org_settings=o.settings)
    s.close()
    return ctx


def test_two_people_issue_at_the_same_moment(org):
    """Stock = 10. Two transactions each try to issue 7 at the same instant.
    Exactly one must succeed; the other must be refused; stock ends at 3."""
    p = org.product()
    org.receive(p["id"], 10)
    ctx = _ctx(org)
    barrier = threading.Barrier(2)
    results = []

    def worker():
        db = SessionLocal()
        try:
            barrier.wait()
            inv.issue(db, ctx, product_id=p["id"], location_id=org.main, quantity=7, mtype=MovementType.ISSUE)
            time.sleep(0.2)  # hold the lock so the other transaction really has to wait
            db.commit()
            results.append("ok")
        except inv.InsufficientStock:
            db.rollback()
            results.append("refused")
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok", "refused"]
    assert org.on_hand(p["id"]) == 3


def test_many_parallel_http_issues_never_oversell(org, live_server):
    p = org.product()
    org.receive(p["id"], 25)
    shop = org.warehouse("Shop")["default_location_id"]
    token = httpx.post(f"{live_server}/api/auth/login", json={"email": org.email, "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    def sell(_):
        with httpx.Client(base_url=live_server, headers=h, timeout=30) as c:
            return c.post("/api/inventory/issue", json={"product_id": p["id"], "location_id": org.main, "quantity": 2, "type": "sale"}).status_code

    def move(_):
        with httpx.Client(base_url=live_server, headers=h, timeout=30) as c:
            return c.post("/api/inventory/transfer", json={"from_location_id": org.main, "to_location_id": shop,
                                                           "product_id": p["id"], "quantity": 1}).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(lambda i: sell(i) if i % 3 else move(i), range(30)))
    assert set(codes) <= {201, 409}, codes
    sold = sum(1 for i, c in enumerate(codes) if c == 201 and i % 3) * 2
    moved = sum(1 for i, c in enumerate(codes) if c == 201 and not i % 3)
    stock = {s["location_id"]: Decimal(s["quantity"]) for s in org.owner.ok("get", f"/api/stock?product_id={p['id']}&include_zero=true")["items"]}
    assert stock.get(org.main, 0) >= 0
    assert stock.get(org.main, 0) == 25 - sold - moved
    assert stock.get(shop, 0) == moved
    assert 409 in codes  # demand (40 units) exceeded supply (25), so some were refused


def test_opposite_transfers_do_not_deadlock(org, live_server):
    """A→B and B→A at the same time used to be a classic deadlock; the engine
    retries automatically, so every request completes."""
    p = org.product()
    b = org.warehouse("B")["default_location_id"]
    org.receive(p["id"], 50)
    org.receive(p["id"], 50, location=b)
    token = httpx.post(f"{live_server}/api/auth/login", json={"email": org.email, "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    def go(i):
        src, dst = (org.main, b) if i % 2 else (b, org.main)
        with httpx.Client(base_url=live_server, headers=h, timeout=30) as c:
            return c.post("/api/inventory/transfer", json={"from_location_id": src, "to_location_id": dst,
                                                           "product_id": p["id"], "quantity": 1}).status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(pool.map(go, range(24)))
    assert codes.count(201) == 24, codes
    assert org.on_hand(p["id"]) == 100
