"""Card downloads for printing. Built on demand (Streamlit runs these when a download button is
clicked, outside the page script), so they read files straight from the database."""
from __future__ import annotations

import io
import zipfile
from typing import Iterator

from PIL import Image
from pymongo.database import Database

from core import cards, exports, qr
from core import db as dbm


def _image(db: Database, file_id) -> Image.Image | None:
    """A stored image, or None if missing or unreadable (one bad file must not stop the whole export)."""
    data = dbm.get_file(file_id, db) if file_id else None
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception:
        return None


def render_all(db: Database, event: dict, people: list[dict]) -> Iterator[tuple[dict, Image.Image]]:
    template = _image(db, event.get("card_template_id"))
    template = template.convert("RGB") if template else None
    logo = _image(db, event.get("logo_id"))
    for p in people:
        yield p, cards.render_card(p, event, _image(db, p.get("photo_id")), template, event_logo=logo)


def one_per_page_pdf(db: Database, event: dict, people: list[dict], width_mm: float) -> bytes:
    pages = cards.SinglePages(width_mm)
    for _, card in render_all(db, event, people):
        pages.add(card)
    return pages.tobytes()


def a4_pdf(db: Database, event: dict, people: list[dict], width_mm: float) -> bytes:
    sheets = cards.PrintSheets(width_mm=width_mm)
    for _, card in render_all(db, event, people):
        sheets.add(card)
    return sheets.tobytes()


def png_zip(db: Database, event: dict, people: list[dict], width_mm: float) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:  # PNGs are already compressed
        for p, card in render_all(db, event, people):
            z.writestr(f"{exports.photo_filename(p)[:-4]}.png", cards.card_png(card, width_mm))
    return buf.getvalue()


def qr_zip(event: dict, people: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in people:
            z.writestr(f"{exports.photo_filename(p)[:-4]}_QR.png", qr.qr_png(qr.make_payload(event["_id"], p["pid"])))
    return buf.getvalue()
