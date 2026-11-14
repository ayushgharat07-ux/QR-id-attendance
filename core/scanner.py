"""Python side of the live QR scanner component + a still-photo fallback decoder."""
from __future__ import annotations

import cv2
import numpy as np
import streamlit.components.v1 as components

from core.config import ROOT

_component = components.declare_component("qr_scanner", path=str(ROOT / "components" / "qr_scanner"))


def qr_scanner(result: dict | None = None, key: str = "qr_scanner"):
    """Shows the live camera. Returns {"code": str, "ts": int} for the latest scan (or None).
    `result` = {"id": ..., "status": "ok"|"warn"|"bad"} makes the scanner flash/beep."""
    return _component(result=result, key=key, default=None)


def decode_qr_image(data: bytes) -> str | None:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return text or None
