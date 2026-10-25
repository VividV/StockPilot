"""Barcode generation and printable label sheets."""
from __future__ import annotations

import io
from dataclasses import dataclass
from decimal import Decimal

from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentSequence, Product
from sqlalchemy.dialects.postgresql import insert as pg_insert


def ean13_check_digit(first12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def is_valid_ean13(code: str) -> bool:
    return len(code) == 13 and code.isdigit() and ean13_check_digit(code[:12]) == code[12]


def _next_seq(db: Session, org_id) -> int:
    stmt = (pg_insert(DocumentSequence).values(org_id=org_id, prefix="BC", next_value=2)
            .on_conflict_do_update(index_elements=["org_id", "prefix"], set_={"next_value": DocumentSequence.next_value + 1})
            .returning(DocumentSequence.next_value))
    return db.execute(stmt).scalar_one() - 1


def new_internal_barcode(db: Session, org_id) -> str:
    """EAN-13 in the 20–29 'in-store' range, which never clashes with
    manufacturer barcodes: 2 + 0 + 10-digit running number + check digit."""
    while True:
        n = _next_seq(db, org_id)
        first12 = f"20{n:010d}"
        code = first12 + ean13_check_digit(first12)
        if not db.execute(select(Product.id).where(Product.org_id == org_id, Product.barcode == code)).first():
            return code


def assign_barcodes(db: Session, org_id, products: list[Product]) -> list[Product]:
    done = []
    for p in products:
        if not p.barcode and not p.has_variants:
            p.barcode = new_internal_barcode(db, org_id)
            done.append(p)
    db.flush()
    return done


# ------------------------------------------------------------------ label sheets
@dataclass
class Layout:
    key: str
    title: str
    page: tuple[float, float]
    cols: int
    rows: int
    label_w: float
    label_h: float
    margin_x: float
    margin_y: float
    gap_x: float = 0
    gap_y: float = 0


LAYOUTS = {
    "a4-3x8": Layout("a4-3x8", "A4 sheet, 24 labels (70 × 37 mm)", A4, 3, 8, 70 * mm, 37 * mm, 0, 0.5 * mm),
    "a4-2x7": Layout("a4-2x7", "A4 sheet, 14 labels (99 × 38 mm)", A4, 2, 7, 99.1 * mm, 38.1 * mm, 4.65 * mm, 15.15 * mm, 2.5 * mm, 0),
    "a4-4x10": Layout("a4-4x10", "A4 sheet, 40 small labels (48 × 25 mm)", A4, 4, 10, 48.5 * mm, 25.4 * mm, 8 * mm, 21.5 * mm, 0, 0),
    "thermal-50x25": Layout("thermal-50x25", "Label printer roll (50 × 25 mm)", (50 * mm, 25 * mm), 1, 1, 50 * mm, 25 * mm, 0, 0),
}


def _barcode_drawing(value: str, width: float, height: float) -> Drawing:
    """EAN-13 when the code is a valid one (with its digits printed under it),
    otherwise Code 128, which can encode any SKU. Scaled to fit the label."""
    if is_valid_ean13(value):
        d = createBarcodeDrawing("EAN13", value=value, barHeight=height * 0.78, fontSize=7)
    else:
        d = createBarcodeDrawing("Code128", value=value, barHeight=height, humanReadable=False, quiet=False, barWidth=0.33 * mm)
    sx = width / d.width if d.width > width else 1.0
    sy = height / d.height if d.height > height else 1.0
    d.scale(sx, sy)
    d.width, d.height = d.width * sx, d.height * sy
    return d


def _fit(c: canvas.Canvas, text: str, font: str, size: float, max_w: float) -> str:
    if c.stringWidth(text, font, size) <= max_w:
        return text
    while text and c.stringWidth(text + "…", font, size) > max_w:
        text = text[:-1]
    return text + "…"


def labels_pdf(items: list[tuple[Product, int]], layout_key: str, *, show_price: bool, currency: str, start_at: int = 1) -> bytes:
    lay = LAYOUTS[layout_key]
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=lay.page)
    c.setTitle("StockPilot labels")
    per_page = lay.cols * lay.rows
    slot = max(0, start_at - 1) % per_page  # lets people reuse a half-used sheet
    queue = [p for p, n in items for _ in range(max(0, min(n, 1000)))]
    page_h = lay.page[1]
    pad = 2.5 * mm
    for p in queue:
        if slot and slot % per_page == 0:
            c.showPage()
            slot = 0
        col, row = slot % lay.cols, slot // lay.cols
        x = lay.margin_x + col * (lay.label_w + lay.gap_x)
        y = page_h - lay.margin_y - (row + 1) * lay.label_h - row * lay.gap_y
        inner_w = lay.label_w - 2 * pad
        small = lay.label_h < 30 * mm
        name_size = 7 if small else 8.5
        c.setFont("Helvetica-Bold", name_size)
        c.drawString(x + pad, y + lay.label_h - pad - name_size, _fit(c, p.name, "Helvetica-Bold", name_size, inner_w))
        line2 = p.sku
        if show_price:
            line2 += f"   {currency} {Decimal(p.selling_price):,.2f}"
        c.setFont("Helvetica", 6.5 if small else 7.5)
        c.drawString(x + pad, y + lay.label_h - pad - name_size - (8 if small else 10), _fit(c, line2, "Helvetica", 7, inner_w))
        code = p.barcode or p.sku
        ean = is_valid_ean13(code)
        text_h = name_size + (8 if small else 10) + 2 * mm          # name + SKU/price + gap
        caption_h = 0 if ean else 7                                # Code 128 gets its value printed under it
        bar_h = lay.label_h - 2 * pad - text_h - caption_h
        d = _barcode_drawing(code, inner_w, max(bar_h, 6 * mm))
        renderPDF.draw(d, c, x + pad + max(0, (inner_w - d.width) / 2), y + pad + caption_h)
        if not ean:
            c.setFont("Helvetica", 6)
            c.drawCentredString(x + lay.label_w / 2, y + pad, _fit(c, code, "Helvetica", 6, inner_w))
        slot += 1
    if not queue:
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, page_h - 30 * mm, "No labels selected.")
    c.showPage()
    c.save()
    return out.getvalue()
