"""ID card rendering (standard design, built-in templates or an uploaded one) and A4 print PDFs."""
from __future__ import annotations

import copy
import io
import re
from functools import lru_cache
from html import escape

import pymupdf
from PIL import Image, ImageDraw, ImageOps

from core import qr as qrm
from core.config import ASSETS, CORAL, NAVY

DPI = 300
CARD_MM = (54.0, 85.6)  # CR80 portrait
MM = DPI / 25.4
CARD_PX = (round(CARD_MM[0] * MM), round(CARD_MM[1] * MM))  # 638 x 1011

TYPE_TEXT = {"attendee": ("श्रीसदस्य", "MEMBER"), "volunteer": ("स्वयंसेवक", "VOLUNTEER")}
TYPE_COLOR = {"attendee": NAVY, "volunteer": CORAL}

# Element positions are fractions of the card width/height. Text "size" is a fraction of card height.
TEXT_ELEMENTS = ("name_mr", "name_en", "pid", "vibhag_mr", "business", "group", "type_label")
ELEMENT_LABELS = {"photo": "Photo", "qr": "QR code", "name_mr": "Name (Marathi)", "name_en": "Name (English)",
                  "vibhag_mr": "विभाग", "business": "Business", "group": "Baithak / Group", "pid": "ID number",
                  "type_label": "Member / Volunteer label"}
_OFF = {"enabled": False, "x": 0.5, "y": 0.5, "size": 0.02, "max_w": 0.8, "bold": False, "color": "#000000", "align": "center"}
DEFAULT_TEMPLATE_LAYOUT = {
    "photo": {"enabled": True, "x": 0.30, "y": 0.22, "w": 0.40, "h": 0.30, "circle": False},
    "qr": {"enabled": True, "x": 0.34, "y": 0.70, "size": 0.32},
    "name_mr": {"enabled": True, "x": 0.5, "y": 0.555, "size": 0.040, "max_w": 0.9, "bold": True, "color": NAVY, "align": "center"},
    "name_en": {"enabled": True, "x": 0.5, "y": 0.605, "size": 0.026, "max_w": 0.9, "bold": False, "color": NAVY, "align": "center"},
    "vibhag_mr": {"enabled": True, "x": 0.5, "y": 0.645, "size": 0.024, "max_w": 0.9, "bold": False, "color": "#333333", "align": "center"},
    "business": dict(_OFF),
    "group": dict(_OFF),
    "pid": {"enabled": True, "x": 0.5, "y": 0.925, "size": 0.026, "max_w": 0.9, "bold": True, "color": "#000000", "align": "center"},
    "type_label": {"enabled": False, "x": 0.5, "y": 0.17, "size": 0.026, "max_w": 0.9, "bold": True, "color": CORAL, "align": "center"},
}

# Ready-made designs shipped with the app (measured on the 1294x2000 px artwork).
BROWN = "#3B2208"
BUILTIN_TEMPLATES = {
    "business_camp_badge": {
        "label": "Business Camp badge (orange)",
        "file": ASSETS / "templates" / "business_camp_badge.png",
        "layout": {
            "photo": {"enabled": True, "x": 0.3485, "y": 0.310, "w": 0.320, "h": 0.207, "circle": True},
            "qr": {"enabled": True, "x": 0.3539, "y": 0.721, "size": 0.309},
            "name_mr": {"enabled": True, "x": 0.379, "y": 0.575, "size": 0.026, "max_w": 0.371, "bold": True,
                        "color": BROWN, "align": "left"},
            "business": {"enabled": True, "x": 0.5162, "y": 0.633, "size": 0.019, "max_w": 0.328, "bold": True,
                         "color": BROWN, "align": "center"},
            "pid": {"enabled": True, "x": 0.5, "y": 0.946, "size": 0.013, "max_w": 0.6, "bold": True,
                    "color": BROWN, "align": "center"},
            "name_en": dict(_OFF), "vibhag_mr": dict(_OFF), "group": dict(_OFF), "type_label": dict(_OFF),
        },
    },
}
DESIGNS = {"ubg": "Standard design", **{k: v["label"] for k, v in BUILTIN_TEMPLATES.items()},
           "upload": "My uploaded template"}


def default_layout(design: str | None = None) -> dict:
    if design in BUILTIN_TEMPLATES:
        return copy.deepcopy(BUILTIN_TEMPLATES[design]["layout"])
    return copy.deepcopy(DEFAULT_TEMPLATE_LAYOUT)


def event_design(event: dict) -> str:
    d = event.get("card_design")
    if d in DESIGNS:
        return d
    return "upload" if event.get("card_template_id") else "ubg"


def event_layout(event: dict, design: str | None = None) -> dict:
    design = design or event_design(event)
    saved = (event.get("card_layouts") or {}).get(design)
    if saved is None and design == "upload":
        saved = event.get("card_layout")  # older single-layout field
    layout = default_layout(design)
    for k, v in (saved or {}).items():
        layout[k] = {**layout.get(k, {}), **v}
    return layout


@lru_cache(maxsize=4)
def builtin_template_image(design: str) -> Image.Image:
    return Image.open(BUILTIN_TEMPLATES[design]["file"]).convert("RGB")


# ------------------------------------------------------------------ text (HarfBuzz shaping via PyMuPDF)
# Pillow needs libraqm+fribidi to shape Devanagari (श्री, क्ष ...) and silently breaks text
# without them; MuPDF's HTML engine ships HarfBuzz, so text renders correctly on any OS.
_FONT_CSS = """
@font-face { font-family: dev; src: url(NotoSansDevanagari-Regular.ttf); }
@font-face { font-family: dev; font-weight: bold; src: url(NotoSansDevanagari-Bold.ttf); }
@font-face { font-family: lat; src: url(NotoSans-Regular.ttf); }
@font-face { font-family: lat; font-weight: bold; src: url(NotoSans-Bold.ttf); }
p { margin: 0; line-height: 1.25; }
.d { font-family: dev; } .l { font-family: lat; }
"""
_ARCHIVE = pymupdf.Archive(str(ASSETS / "fonts"))
_DEV_RUN = re.compile(r"([ऀ-ॿ᳐-᳿꣠-ꣿ]+(?:[\s.,()\-/:]*[ऀ-ॿ᳐-᳿꣠-ꣿ]+)*)")


def _runs_html(text: str) -> str:
    """Wrap Devanagari runs and Latin runs in spans with the matching font."""
    out = []
    for i, part in enumerate(_DEV_RUN.split(text)):
        if part:
            out.append(f'<span class="{"d" if i % 2 else "l"}">{escape(part)}</span>')
    return "".join(out)


def _render_html(text: str, size: float, bold: bool, color: str, width: float, wrap: bool, align: str) -> Image.Image:
    style = (f"font-size:{size}px; color:{color}; text-align:{align}; "
             f"font-weight:{'bold' if bold else 'normal'}; white-space:{'normal' if wrap else 'nowrap'};")
    page_w = width if wrap else max(width * 4, 2000)
    page_h = size * (12 if wrap else 3)
    doc = pymupdf.open()
    page = doc.new_page(width=page_w, height=page_h)
    page.insert_htmlbox(pymupdf.Rect(0, 0, page_w, page_h), f'<p style="{style}">{_runs_html(text)}</p>',
                        css=_FONT_CSS, archive=_ARCHIVE, scale_low=1)
    pix = page.get_pixmap(alpha=True)
    img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    bbox = img.getchannel("A").getbbox()
    if not bbox:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    if wrap:  # keep block width so alignment holds; trim height only
        return img.crop((0, bbox[1], int(width), bbox[3]))
    return img.crop(bbox)


@lru_cache(maxsize=512)
def render_text(text: str, size: int, bold: bool = False, color: str = "#000000",
                max_w: int = 600, wrap: bool = False, align: str = "center") -> Image.Image:
    """Tightly cropped RGBA image of the text. Single-line text shrinks to fit max_w."""
    img = _render_html(text, size, bold, color, max_w, wrap, align)
    if not wrap and img.width > max_w:
        img = _render_html(text, size * max_w / img.width * 0.98, bold, color, max_w, wrap, align)
    return img


def draw_text(img: Image.Image, text: str, x: float, y: float, size_px: int, *, max_w: int,
              bold=False, color="#000000", align="center") -> None:
    """Draw single-line text with its vertical centre at y; x is the anchor for `align`."""
    if not text:
        return
    t = render_text(text, int(size_px), bold, color, int(max_w))
    left = {"center": x - t.width / 2, "left": x, "right": x - t.width}[align]
    img.paste(t, (int(left), int(y - t.height / 2)), t)


def draw_block(img: Image.Image, text: str, x: float, y: float, size_px: int, *, width: int,
               bold=False, color="#000000", align="center") -> int:
    """Draw wrapped text with its top at y, centred on x. Returns the block height."""
    if not text:
        return 0
    t = render_text(text, int(size_px), bold, color, int(width), wrap=True, align=align)
    img.paste(t, (int(x - width / 2), int(y)), t)
    return t.height


def _paste_fit(card: Image.Image, img: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    fitted = ImageOps.fit(img.convert("RGB"), (max(1, x1 - x0), max(1, y1 - y0)), Image.LANCZOS)
    card.paste(fitted, (x0, y0))


def _placeholder_photo() -> Image.Image:
    ph = Image.new("RGB", (300, 360), "#D9DEE5")
    d = ImageDraw.Draw(ph)
    d.ellipse((95, 60, 205, 170), fill="#AEB7C2")
    d.rounded_rectangle((55, 190, 245, 360), radius=80, fill="#AEB7C2")
    return ph


# ------------------------------------------------------------------ built-in design
def render_builtin(person: dict, event: dict, photo: Image.Image | None,
                   event_logo: Image.Image | None = None) -> Image.Image:
    """Standard design: the event's name (and the organiser's logo, if uploaded) on top."""
    W, H = CARD_PX
    ptype = person.get("type", "attendee")
    band = TYPE_COLOR.get(ptype, NAVY)
    card = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(card)

    # header band with organiser logo (optional) + event name
    d.rectangle((0, 0, W, 250), fill=NAVY)
    y = 34
    if event_logo is not None:
        logo = event_logo.convert("RGBA")
        bbox = logo.getbbox()
        if bbox:
            logo = logo.crop(bbox)
        logo.thumbnail((150, 70), Image.LANCZOS)
        white_bg = Image.new("RGBA", (logo.width + 24, logo.height + 12), (255, 255, 255, 255))
        white_bg.alpha_composite(logo, (12, 6))
        card.paste(white_bg.convert("RGB"), ((W - white_bg.width) // 2, 14))
        y = 14 + white_bg.height + 10
    y += draw_block(card, event.get("name_mr", ""), W / 2, y, 32, width=W - 50, bold=True, color="#FFFFFF")
    if event.get("subtitle_mr"):
        draw_text(card, event["subtitle_mr"], W / 2, y + 22, 26, max_w=W - 50, color="#F6C9BC")

    # type ribbon
    mr, en = TYPE_TEXT.get(ptype, ("", ""))
    d.rectangle((0, 250, W, 300), fill=band)
    draw_text(card, f"{mr} · {en}", W / 2, 275, 28, max_w=W - 40, bold=True, color="#FFFFFF")

    # photo
    pw, ph = 250, 300
    px, py = (W - pw) // 2, 325
    d.rectangle((px - 6, py - 6, px + pw + 6, py + ph + 6), fill=band)
    _paste_fit(card, photo or _placeholder_photo(), (px, py, px + pw, py + ph))

    # name + vibhag
    draw_text(card, person.get("name_mr", ""), W / 2, 670, 42, max_w=W - 40, bold=True, color=NAVY)
    draw_text(card, person.get("name_en", ""), W / 2, 715, 26, max_w=W - 40, color=NAVY)
    draw_text(card, person.get("vibhag_mr", ""), W / 2, 752, 25, max_w=W - 40, color="#444444")

    # QR + ID
    if person.get("pid"):
        q = qrm.qr_image(qrm.make_payload(person["event_id"], person["pid"]), box_size=8, border=1)
        q = q.resize((190, 190), Image.NEAREST)
        card.paste(q, ((W - 190) // 2, 778))
        draw_text(card, person["pid"], W / 2, 984 - 10, 24, max_w=W - 40, bold=True, color="black")

    d.rectangle((0, H - 22, W, H), fill=NAVY)  # bottom edge; no app/developer branding on the card
    return card


# ------------------------------------------------------------------ template design
def render_template(person: dict, event: dict, photo: Image.Image | None, template: Image.Image,
                    layout: dict) -> Image.Image:
    card = template.convert("RGB").copy()
    W, H = card.size
    ptype = person.get("type", "attendee")

    p = layout.get("photo", {})
    if p.get("enabled", True):
        box = (int(p["x"] * W), int(p["y"] * H), int((p["x"] + p["w"]) * W), int((p["y"] + p["h"]) * H))
        if p.get("circle"):
            if photo is not None:  # no photo: keep the template's own silhouette
                _paste_circle(card, photo, box)
        else:
            _paste_fit(card, photo or _placeholder_photo(), box)

    q = layout.get("qr", {})
    if q.get("enabled", True) and person.get("pid"):
        side = int(q["size"] * W)
        # 2-module white border = quiet zone, so it scans even on a busy/dark background
        img = qrm.qr_image(qrm.make_payload(person["event_id"], person["pid"]), box_size=10, border=2)
        card.paste(img.resize((side, side), Image.NEAREST), (int(q["x"] * W), int(q["y"] * H)))

    values = {
        "name_mr": person.get("name_mr", ""),
        "name_en": person.get("name_en", ""),
        "pid": person.get("pid") or "",
        "vibhag_mr": person.get("vibhag_mr", ""),
        "business": person.get("business", ""),
        "group": person.get("group", ""),
        "type_label": " · ".join(TYPE_TEXT.get(ptype, ("", ""))),
    }
    for key in TEXT_ELEMENTS:
        el = layout.get(key, {})
        if not el.get("enabled", False):
            continue
        draw_text(card, values[key], el["x"] * W, el["y"] * H, max(8, int(el["size"] * H)),
                  max_w=int(el.get("max_w", 0.9) * W), bold=el.get("bold", False),
                  color=el.get("color", "#000000"), align=el.get("align", "center"))
    return card


def render_card(person: dict, event: dict, photo: Image.Image | None,
                uploaded_template: Image.Image | None = None, design: str | None = None,
                layout: dict | None = None, event_logo: Image.Image | None = None) -> Image.Image:
    """Render with the event's chosen design ('ubg' = standard, a built-in template, or 'upload')."""
    design = design or event_design(event)
    if design in BUILTIN_TEMPLATES:
        template = builtin_template_image(design)
    elif design == "upload" and uploaded_template is not None:
        template = uploaded_template
    else:
        return render_builtin(person, event, photo, event_logo)
    return render_template(person, event, photo, template, layout or event_layout(event, design))


def _paste_circle(card: Image.Image, img: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    # bias the crop upwards a little so faces (top of passport photos) stay centred
    fitted = ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.4))
    big = Image.new("L", (w * 4, h * 4), 0)
    ImageDraw.Draw(big).ellipse((0, 0, w * 4 - 1, h * 4 - 1), fill=255)
    card.paste(fitted, (x0, y0), big.resize((w, h), Image.LANCZOS))  # anti-aliased edge


def card_pdf(card: Image.Image, width_mm: float = CARD_MM[0]) -> bytes:
    """One card as a single-page PDF at true size."""
    pt = 72 / 25.4
    w = width_mm * pt
    h = w * card.height / card.width
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="JPEG", quality=92, dpi=(DPI, DPI))
    page.insert_image(page.rect, stream=buf.getvalue())
    return doc.tobytes(deflate=True)


def card_png(card: Image.Image) -> bytes:
    buf = io.BytesIO()
    card.save(buf, format="PNG", dpi=(DPI, DPI))
    return buf.getvalue()


# ------------------------------------------------------------------ A4 print sheets
class PrintSheets:
    """Adds cards one at a time to A4 pages at true card size with cut lines
    (so hundreds of cards never sit in memory as images at once)."""

    PT = 72 / 25.4  # points per mm

    def __init__(self, card_mm: tuple[float, float] | None = None, width_mm: float | None = None):
        self.card_mm = card_mm
        self.width_mm = width_mm  # if given, height follows the card image's own proportions
        self.page_w, self.page_h = 210 * self.PT, 297 * self.PT
        self.margin, self.gap = 8 * self.PT, 3 * self.PT
        self.doc = pymupdf.open()
        self.page, self.slot, self.cols, self.rows = None, 0, 0, 0

    def add(self, card: Image.Image) -> None:
        if self.card_mm is None:
            w = self.width_mm or (CARD_MM[0] if card.height >= card.width else CARD_MM[1])
            self.card_mm = (w, w * card.height / card.width)
        cw, ch = self.card_mm[0] * self.PT, self.card_mm[1] * self.PT
        if not self.cols:
            self.cols = max(1, int((self.page_w - 2 * self.margin + self.gap) // (cw + self.gap)))
            self.rows = max(1, int((self.page_h - 2 * self.margin + self.gap) // (ch + self.gap)))
        if self.page is None or self.slot >= self.cols * self.rows:
            self.page, self.slot = self.doc.new_page(width=self.page_w, height=self.page_h), 0
        grid_w = self.cols * cw + (self.cols - 1) * self.gap
        grid_h = self.rows * ch + (self.rows - 1) * self.gap
        x0 = (self.page_w - grid_w) / 2 + (self.slot % self.cols) * (cw + self.gap)
        y0 = (self.page_h - grid_h) / 2 + (self.slot // self.cols) * (ch + self.gap)
        rect = pymupdf.Rect(x0, y0, x0 + cw, y0 + ch)
        buf = io.BytesIO()
        card.convert("RGB").save(buf, format="JPEG", quality=92, dpi=(DPI, DPI))
        self.page.insert_image(rect, stream=buf.getvalue())
        self.page.draw_rect(rect, color=(0.7, 0.7, 0.7), width=0.3)  # cut line
        self.slot += 1

    def tobytes(self) -> bytes:
        if self.page is None:
            self.doc.new_page(width=self.page_w, height=self.page_h)
        return self.doc.tobytes(deflate=True)


def print_pdf(cards, card_mm: tuple[float, float] | None = None, width_mm: float | None = None) -> bytes:
    """cards: iterable of PIL images."""
    sheets = PrintSheets(card_mm, width_mm)
    for card in cards:
        sheets.add(card)
    return sheets.tobytes()
