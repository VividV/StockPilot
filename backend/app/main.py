import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import engine
from app.deps import client_ip
from app.routers import account, admin, attachments, auth, billing, imports, inventory, orders, overview, partners, places, pos, production, products, reports, transfers
from app.security import limiter
from app.services.inventory import InventoryError

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("stockpilot")

app = FastAPI(
    title="StockPilot API",
    version="1.0.0",
    description=(
        "Multi-tenant inventory management. Authenticate with `POST /api/auth/login`, then send "
        "`Authorization: Bearer <access_token>`. All data is scoped to the signed-in user's organisation."
    ),
    docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def security_and_limits(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/health", "/api/ready"):
        if not limiter.hit(f"global:{client_ip(request)}", settings.RATE_LIMIT_PER_MINUTE):
            return JSONResponse({"detail": "Too many requests, slow down a little."}, status_code=429,
                                headers={"Retry-After": "60"})
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), geolocation=()"
    if not path.startswith("/api/docs") and not path.startswith("/api/redoc"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if path.startswith("/api/") and request.method != "GET":
        response.headers["Cache-Control"] = "no-store"
    log.info("%s %s %s %.0fms rid=%s", request.method, path, response.status_code,
             (time.perf_counter() - started) * 1000, request_id)
    return response


@app.exception_handler(InventoryError)
async def inventory_error(request: Request, exc: InventoryError):
    return JSONResponse({"detail": exc.message, "code": exc.code, **exc.details}, status_code=exc.status_code)


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    log.warning("integrity error: %s", exc.orig)
    return JSONResponse({"detail": "This conflicts with an existing record (duplicate or still in use)."}, status_code=409)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = []
    for e in exc.errors():
        field = ".".join(str(x) for x in e.get("loc", [])[1:]) or "request"
        errors.append({"field": field, "message": e.get("msg", "invalid")})
    first = errors[0] if errors else {"field": "request", "message": "invalid"}
    return JSONResponse({"detail": f"{first['field']}: {first['message']}", "errors": errors}, status_code=422)


for r in (auth.router, account.router, products.router, places.router, partners.suppliers, partners.customers, inventory.router,
          transfers.router, orders.purchases, orders.sales, attachments.router, imports.router, production.router, reports.router, overview.router, admin.router,
          pos.router, pos.promotions, pos.public, billing.router):
    app.include_router(r)


@app.get("/api/health", tags=["ops"])
def health():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/api/ready", tags=["ops"])
def ready():
    """Readiness: the database is reachable and migrated."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM alembic_version"))
        return {"status": "ready"}
    except Exception as e:  # noqa: BLE001
        log.error("readiness failed: %s", e)
        return JSONResponse({"status": "not_ready"}, status_code=503)
