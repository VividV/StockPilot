"""Spreadsheet import: /api/imports/{kind}/template | preview | validate | commit."""
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, current_principal
from app.services import imports as svc
from app.services import inventory as inv
from app.services.export import CONTENT_TYPES

router = APIRouter(prefix="/api/imports", tags=["imports"])

PERMS = {
    "products": ("products.create",),
    "suppliers": ("suppliers.manage",),
    "customers": ("customers.manage",),
    "opening-stock": ("inventory.adjust",),
}
MAX_FILE_MB = 5


class RowsIn(BaseModel):
    rows: list[dict] = Field(min_length=1, max_length=svc.MAX_ROWS)


def _check(kind: str, p: Principal):
    if kind not in svc.KINDS:
        raise HTTPException(404, "Unknown import type")
    missing = [x for x in PERMS[kind] if not p.can(x)]
    if missing:
        raise HTTPException(403, "You don't have permission to import this")


def _report(kind: str, results) -> dict:
    cols = svc.KINDS[kind]["columns"]
    return {
        "kind": kind, "title": svc.KINDS[kind]["title"],
        "columns": [{"key": c.key, "label": c.label, "required": c.required, "help": c.help} for c in cols],
        "rows": [{"row": r.row, "data": {c.key: r.data.get(c.key, "") for c in cols}, "action": r.action,
                  "errors": r.errors, "notes": r.notes} for r in results],
        "summary": {"total": len(results), "create": sum(1 for r in results if r.action == "create"),
                    "update": sum(1 for r in results if r.action == "update"),
                    "with_errors": sum(1 for r in results if r.errors)},
    }


@router.get("")
def kinds(p: Principal = Depends(current_principal)):
    return [{"kind": k, "title": v["title"], "allowed": all(p.can(x) for x in PERMS[k]),
             "columns": [{"key": c.key, "label": c.label, "required": c.required, "help": c.help} for c in v["columns"]]}
            for k, v in svc.KINDS.items()]


@router.get("/{kind}/template")
def template(kind: str, format: str = Query("xlsx", pattern="^(xlsx|csv)$"), p: Principal = Depends(current_principal)):
    _check(kind, p)
    return Response(svc.template(kind, format), media_type=CONTENT_TYPES[format],
                    headers={"Content-Disposition": f'attachment; filename="stockpilot-{kind}-template.{format}"'})


@router.post("/{kind}/preview")
async def preview(kind: str, file: UploadFile = File(...), p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Read the uploaded file and check every row. Nothing is saved."""
    _check(kind, p)
    data = await file.read(MAX_FILE_MB * 1024 * 1024 + 1)
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"Files can be up to {MAX_FILE_MB} MB")
    try:
        rows = svc.parse_file(kind, file.filename or "", data)
        return _report(kind, svc.validate(db, p.ctx, kind, rows))
    except svc.ImportFileError as e:
        raise HTTPException(422, str(e))


@router.post("/{kind}/validate")
def validate(kind: str, body: RowsIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Check rows again after corrections on screen. Nothing is saved."""
    _check(kind, p)
    try:
        return _report(kind, svc.validate(db, p.ctx, kind, body.rows))
    except svc.ImportFileError as e:
        raise HTTPException(422, str(e))


@router.post("/{kind}/commit")
def commit(kind: str, body: RowsIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Import every row, or none: if any row has a problem nothing is saved."""
    _check(kind, p)

    def op():
        out = svc.commit(db, p.ctx, kind, body.rows)
        if not out["ok"]:
            db.rollback()
            return JSONResponse(status_code=422, content={"detail": "Some rows still have problems. Nothing was imported.",
                                                          **_jsonable(_report(kind, out["results"]))})
        # creating products needs products.create; changing existing ones also needs products.edit
        if kind == "products" and any(r.action == "update" for r in out["results"]) and not p.can("products.edit"):
            raise inv.InventoryError("Some SKUs already exist and you aren't allowed to edit products", code="forbidden")
        title = svc.KINDS[kind]["title"]
        p.audit(db, f"import.{kind}", entity_type="import", entity_id=kind,
                summary=f"Imported {title.lower()}: {out['created']} new, {out['updated']} updated")
        if kind == "opening-stock":
            from app.services.inventory import refresh_expiry_alerts
            refresh_expiry_alerts(db, p.org_id)
        return {"message": f"Imported {out['created'] + out['updated']} row(s): {out['created']} new, {out['updated']} updated",
                "created": out["created"], "updated": out["updated"]}
    return inv.run_with_retry(db, op)


def _jsonable(d):
    import json
    return json.loads(json.dumps(d, default=lambda o: o.isoformat() if isinstance(o, date) else str(o)))
