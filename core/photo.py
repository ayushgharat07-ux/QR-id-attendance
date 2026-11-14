"""Turn uploads into page images, and find + crop the passport photo on a form."""
from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

FORM_MAX_SIDE = 1600      # stored form image size (keeps MongoDB small)
PHOTO_MAX_SIDE = 600      # stored passport photo size


def upload_to_pages(data: bytes, filename: str, dpi: int = 200) -> list[Image.Image]:
    """A PDF becomes one image per page; an image file becomes one image."""
    if filename.lower().endswith(".pdf") or data[:4] == b"%PDF":
        import pymupdf

        pages = []
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=dpi)
                pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        return pages
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # respect phone camera rotation
    return [img.convert("RGB")]


def shrink(img: Image.Image, max_side: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def to_jpeg(img: Image.Image, quality: int = 82) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


@dataclass
class Box:
    """Box in fractions of the page size (0..1)."""
    x: float
    y: float
    w: float
    h: float

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (int(self.x * width), int(self.y * height),
                int((self.x + self.w) * width), int((self.y + self.h) * height))


def find_photo_box(img: Image.Image) -> Box | None:
    """Locate the passport photo: a large, colourful/dark, roughly portrait rectangle
    in the upper part of the page. Works on scans and flat phone photos of the form."""
    rgb = np.array(shrink(img, 1200))
    H, W = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    # "ink-like" pixels: saturated colour or fairly dark
    mask = ((hsv[..., 1] > 50) | (gray < 140)).astype(np.uint8) * 255
    # remove thin text strokes and table lines, merge photo blobs
    k = max(3, W // 150)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k * 3, k * 3), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, best_score = None, 0.0
    page_area = W * H
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if not (0.008 * page_area <= area <= 0.2 * page_area):
            continue
        aspect = h / w
        if not (0.7 <= aspect <= 1.8):
            continue
        if y + h / 2 > 0.6 * H:        # photo sits in the upper part of the form
            continue
        fill = cv2.contourArea(c) / area  # solid blob, not a sparse text cluster
        if fill < 0.6:
            continue
        score = area * fill
        if score > best_score:
            best, best_score = (x, y, w, h), score
    if best is None:
        return None
    # The colourful/dark blob can stop at a white shirt; grow the box until plain paper.
    not_paper = ((gray < 228) | (hsv[..., 1] > 35)).astype(np.float32)
    dark = (gray < 200).astype(np.float32)  # paper lines have none; even a white shirt has some creases
    x, y, w, h = _grow_to_paper(not_paper, dark, *best)
    inset = 0.025  # trim frame / border
    return Box((x + w * inset) / W, (y + h * inset) / H, w * (1 - 2 * inset) / W, h * (1 - 2 * inset) / H)


def _grow_to_paper(not_paper: np.ndarray, dark: np.ndarray, x: int, y: int, w: int, h: int,
                   min_fill: float = 0.12, min_dark: float = 0.02, gap_rows: int = 3) -> tuple[int, int, int, int]:
    """Extend each side of the box while the next line just outside it is not blank paper.
    Stops after `gap_rows` consecutive blank lines."""
    H, W = not_paper.shape
    x0, y0, x1, y1 = x, y, x + w, y + h
    max_grow_v, max_grow_h = int(h * 0.8), int(w * 0.3)

    def is_photo(arr_np, arr_dark) -> bool:
        return arr_np.mean() >= min_fill or arr_dark.mean() >= min_dark

    def grow(line, start, step, limit, bound):
        pos, last_good, misses = start, start, 0
        while 0 <= pos + step < bound and abs(pos - start) < limit:
            pos += step
            if is_photo(*line(pos)):
                last_good, misses = pos, 0
            else:
                misses += 1
                if misses >= gap_rows:
                    break
        return last_good

    y1 = grow(lambda r: (not_paper[r, x0:x1], dark[r, x0:x1]), y1 - 1, 1, max_grow_v, H) + 1
    y0 = grow(lambda r: (not_paper[r, x0:x1], dark[r, x0:x1]), y0, -1, max_grow_v, H)
    x1 = grow(lambda c: (not_paper[y0:y1, c], dark[y0:y1, c]), x1 - 1, 1, max_grow_h, W) + 1
    x0 = grow(lambda c: (not_paper[y0:y1, c], dark[y0:y1, c]), x0, -1, max_grow_h, W)
    return x0, y0, x1 - x0, y1 - y0


def crop_box(img: Image.Image, box: Box) -> Image.Image:
    return img.crop(box.to_pixels(*img.size))


def extract_photo(img: Image.Image, hint: Box | None = None) -> tuple[Image.Image | None, Box | None, str]:
    """Returns (photo, box, method). method is 'auto', 'ai' or 'none'."""
    box = find_photo_box(img)
    method = "auto"
    if box is None and hint is not None and hint.w > 0.03 and hint.h > 0.03:
        box, method = hint, "ai"
    if box is None:
        return None, None, "none"
    photo = shrink(crop_box(img, box), PHOTO_MAX_SIDE)
    return photo, box, method
