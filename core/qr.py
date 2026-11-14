"""Signed QR payloads:  UBG1|<event_id>|<pid>|<sig>"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from core.config import secret

PREFIX = "UBG1"


def _key() -> bytes:
    key = secret("QR_SECRET")
    if not key:
        raise RuntimeError("QR_SECRET is not configured.")
    return str(key).encode()


def _sig(event_id: str, pid: str) -> str:
    mac = hmac.new(_key(), f"{event_id}|{pid}".encode(), hashlib.sha256).digest()
    return base64.b32encode(mac).decode()[:10]


def make_payload(event_id: str, pid: str) -> str:
    return f"{PREFIX}|{event_id}|{pid}|{_sig(event_id, pid)}"


def parse_payload(text: str) -> tuple[str, str] | None:
    """Return (event_id, pid) if the payload is genuine, else None."""
    parts = (text or "").strip().split("|")
    if len(parts) != 4 or parts[0] != PREFIX:
        return None
    _, event_id, pid, sig = parts
    if not hmac.compare_digest(sig, _sig(event_id, pid)):
        return None
    return event_id, pid


def qr_image(payload: str, box_size: int = 10, border: int = 2):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=box_size, border=border)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image().convert("RGB")


def qr_png(payload: str, box_size: int = 10) -> bytes:
    buf = io.BytesIO()
    qr_image(payload, box_size).save(buf, format="PNG")
    return buf.getvalue()
