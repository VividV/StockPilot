from conftest import PASSWORD, Api


def test_signup_creates_org_with_defaults(org):
    me = org.owner.ok("get", "/api/auth/me")
    assert me["role"] == "Owner"
    assert "users.manage" in me["permissions"]
    assert set(org.roles) == {"Owner", "Administrator", "Manager", "Warehouse Staff", "Sales Staff", "Purchasing Staff", "Viewer", "Cashier"}
    assert {"pcs", "ctn", "kg", "l", "m"} <= set(org.units)
    whs = org.owner.ok("get", "/api/warehouses")
    assert [w["name"] for w in whs] == ["Main Store"]  # simple businesses get one place, nothing more


def test_industry_presets_configure_modules(make_org):
    health = make_org(industry="healthcare")
    s = health.owner.ok("get", "/api/settings")["settings"]["modules"]
    assert s["batches"] and s["expiry"]
    retail = make_org(industry="retail")
    s = retail.owner.ok("get", "/api/settings")["settings"]["modules"]
    assert s["barcodes"] and s["variants"] and not s["expiry"]
    # healthcare products default to batch + expiry tracking
    p = health.product()
    assert p["track_batches"] and p["track_expiry"]


def test_login_and_wrong_password(client, org):
    r = client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD})
    assert r.status_code == 200 and r.json()["access_token"]
    assert "sp_refresh" in r.cookies or "sp_refresh" in client.cookies
    r = client.post("/api/auth/login", json={"email": org.email, "password": "nope-nope-nope"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever-123A"})
    assert r.status_code == 401 and r.json()["detail"] == "Email or password is incorrect"


def test_passwords_are_hashed(org, db):
    from sqlalchemy import select
    from app.models import User
    u = db.execute(select(User).where(User.email == org.email)).unique().scalar_one()
    assert u.password_hash.startswith("$argon2id$") and PASSWORD not in u.password_hash


def test_weak_password_rejected(client):
    r = client.post("/api/auth/signup", json={"organization_name": "X", "full_name": "X", "email": "weak@example.com",
                                              "password": "alllowercase1"})
    assert r.status_code == 422


def test_requests_without_token_are_rejected(client):
    assert client.get("/api/products").status_code == 401
    assert client.get("/api/products", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_refresh_rotates_and_requires_csrf_header(client, org):
    client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD})
    assert client.post("/api/auth/refresh").status_code == 403  # no CSRF header
    h = {"X-Requested-With": "StockPilot"}
    old_cookie = client.cookies.get("sp_refresh")
    r = client.post("/api/auth/refresh", headers=h)
    assert r.status_code == 200
    new_cookie = client.cookies.get("sp_refresh")
    assert new_cookie and new_cookie != old_cookie
    # the old refresh token is now spent
    client.cookies.set("sp_refresh", old_cookie, path="/api/auth")
    assert client.post("/api/auth/refresh", headers=h).status_code == 401


def test_logout_revokes_refresh(client, org):
    client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD})
    h = {"X-Requested-With": "StockPilot"}
    token = client.cookies.get("sp_refresh")
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    client.cookies.set("sp_refresh", token, path="/api/auth")
    assert client.post("/api/auth/refresh", headers=h).status_code == 401


def test_login_rate_limited(client, org):
    codes = [client.post("/api/auth/login", json={"email": org.email, "password": "Wrong-password-1"}).status_code for _ in range(12)]
    assert 429 in codes


def test_deactivated_user_cannot_use_token(client, org):
    staff = org.user("Viewer")
    me = staff.ok("get", "/api/auth/me")
    org.owner.ok("put", f"/api/users/{me['id']}", json={"is_active": False})
    assert staff.get("/api/auth/me").status_code == 401


def test_login_is_audited(client, org):
    client.post("/api/auth/login", json={"email": org.email, "password": PASSWORD})
    logs = org.owner.ok("get", "/api/audit-logs?action=auth.login")["items"]
    assert logs and logs[0]["action"] == "auth.login" and logs[0]["ip_address"]


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_health_and_ready(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/ready").json() == {"status": "ready"}
