"""File attachments on products, orders, transfers, adjustments and partners.

Files are stored on disk under ATTACHMENTS_DIR/<org id>/<attachment id>, never
under the uploaded name, and are only served to signed-in users of the same
organisation who may see the record they belong to."""
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import Principal, current_principal
from app.models import (
    Attachment, Customer, Product, PurchaseOrder, SalesOrder, StockAdjustment, Supplier, Transfer,
)

router = APIRouter(prefix="/api/attachments", tags=["attachments"])
settings = get_settings()

# entity type -> (model, permission to see, permission to add/remove)
ENTITIES = {
    "product": (Product, "products.view", "products.edit"),
    "purchase_order": (PurchaseOrder, "purchases.view", "purchases.create"),
    "sales_order": (SalesOrder, "sales.view", "sales.create"),
    "transfer": (Transfer, "inventory.view", "inventory.transfer"),
    "adjustment": (StockAdjustment, "inventory.view", "inventory.adjust"),
    "supplier": (Supplier, "suppliers.view", "suppliers.manage"),
    "customer": (Customer, "customers.view", "customers.manage"),
}

# extension -> (content type, shown inline in the browser?)
ALLOWED = {
    ".pdf": ("application/pdf", True), ".png": ("image/png", True), ".jpg": ("image/jpeg", True),
    ".jpeg": ("image/jpeg", True), ".webp": ("image/webp", True), ".gif": ("image/gif", True),
    ".csv": ("text/csv", False), ".txt": ("text/plain", False),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
    ".xls": ("application/vnd.ms-excel", False),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", False),
    ".doc": ("application/msword", False),
}
# first bytes of files that are shown inline, so a renamed HTML page can't pose as an image
MAGIC = {
    ".pdf": [b"%PDF"], ".png": [b"\x89PNG"], ".jpg": [b"\xff\xd8\xff"], ".jpeg": [b"\xff\xd8\xff"],
    ".gif": [b"GIF87a", b"GIF89a"], ".webp": [b"RIFF"],
}


def _root() -> Path:
    return Path(settings.ATTACHMENTS_DIR).resolve()


def _check_entity(db: Session, p: Principal, entity_type: str, entity_id: str, write: bool):
    if entity_type not in ENTITIES:
        raise HTTPException(422, "Files can't be attached to that")
    model, view_perm, edit_perm = ENTITIES[entity_type]
    if not p.can(edit_perm if write else view_perm):
        raise HTTPException(403, "You don't have permission to do this")
    try:
        eid = uuid.UUID(str(entity_id))
    except ValueError:
        raise HTTPException(404, "Record not found")
    if not db.execute(select(model.id).where(model.id == eid, model.org_id == p.org_id)).first():
        raise HTTPException(404, "Record not found")


def _out(a: Attachment) -> dict:
    ext = Path(a.filename).suffix.lower()
    return {"id": a.id, "filename": a.filename, "content_type": a.content_type, "size_bytes": a.size_bytes,
            "is_image": a.content_type.startswith("image/"), "previewable": ALLOWED.get(ext, ("", False))[1],
            "uploaded_by": a.uploaded_by, "created_at": a.created_at}


@router.get("")
def list_attachments(entity_type: str, entity_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _check_entity(db, p, entity_type, entity_id, write=False)
    rows = db.execute(select(Attachment).where(Attachment.org_id == p.org_id, Attachment.entity_type == entity_type,
                                               Attachment.entity_id == str(entity_id)).order_by(Attachment.created_at.desc())).scalars()
    return [_out(a) for a in rows]


@router.post("", status_code=201)
async def upload(entity_type: str = Form(...), entity_id: str = Form(...), file: UploadFile = File(...),
                 p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    _check_entity(db, p, entity_type, entity_id, write=True)
    name = os.path.basename((file.filename or "file").replace("\\", "/"))[:200] or "file"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(422, "Upload a PDF, image (PNG, JPG, WebP, GIF), Excel, Word, CSV or text file")
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Files can be up to {settings.MAX_UPLOAD_MB} MB")
    if not data:
        raise HTTPException(422, "The file is empty")
    if ext in MAGIC and not any(data.startswith(m) for m in MAGIC[ext]):
        raise HTTPException(422, f"This doesn't look like a real {ext[1:].upper()} file")
    att_id = uuid.uuid4()
    folder = _root() / str(p.org_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / str(att_id)
    path.write_bytes(data)
    a = Attachment(id=att_id, org_id=p.org_id, entity_type=entity_type, entity_id=str(entity_id), filename=name,
                   content_type=ALLOWED[ext][0], size_bytes=len(data), storage_key=f"{p.org_id}/{att_id}", uploaded_by=p.user.id)
    db.add(a)
    p.audit(db, "attachment.upload", entity_type=entity_type, entity_id=entity_id, summary=f"Attached {name}")
    try:
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return _out(a)


def _get(db: Session, p: Principal, attachment_id: uuid.UUID) -> Attachment:
    a = db.execute(select(Attachment).where(Attachment.id == attachment_id, Attachment.org_id == p.org_id)).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "File not found")
    return a


@router.get("/{attachment_id}/file")
def download(attachment_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = _get(db, p, attachment_id)
    _check_entity(db, p, a.entity_type, a.entity_id, write=False)
    path = (_root() / a.storage_key).resolve()
    if _root() not in path.parents or not path.is_file():
        raise HTTPException(404, "File is missing from storage")
    inline = ALLOWED.get(Path(a.filename).suffix.lower(), ("", False))[1]
    return FileResponse(path, media_type=a.content_type, filename=a.filename,
                        content_disposition_type="inline" if inline else "attachment",
                        headers={"Cache-Control": "private, max-age=300"})


@router.delete("/{attachment_id}")
def delete(attachment_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    a = _get(db, p, attachment_id)
    _check_entity(db, p, a.entity_type, a.entity_id, write=True)
    path = (_root() / a.storage_key).resolve()
    db.delete(a)
    p.audit(db, "attachment.delete", entity_type=a.entity_type, entity_id=a.entity_id, summary=f"Removed {a.filename}")
    db.commit()
    if _root() in path.parents:
        path.unlink(missing_ok=True)
    return {"message": "File removed"}
