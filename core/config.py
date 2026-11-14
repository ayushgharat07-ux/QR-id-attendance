"""Settings lookup: Streamlit secrets first, then environment variables."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
LOGO_FULL = ASSETS / "logos" / "ubg_full_tagline.png"
LOGO_NAME = ASSETS / "logos" / "ubg_full.png"
LOGO_MARK = ASSETS / "logos" / "ubg_mark.png"
FONT_REGULAR = ASSETS / "fonts" / "NotoSansDevanagari-Regular.ttf"
FONT_BOLD = ASSETS / "fonts" / "NotoSansDevanagari-Bold.ttf"

# The app is a product DEVELOPED by United Brothers Global. Events belong to their organisers:
# show the event (and the organiser's logo) first; UBG only appears as the developer credit.
APP_NAME = "Event Attendance"
COMPANY = "UNITED BROTHERS GLOBAL"
COMPANY_TAGLINE = "Import • Export • Trade Solutions"
DEV_CREDIT = f"App developed by {COMPANY}"
NAVY = "#1B3A5C"
CORAL = "#E8735A"
SILVER = "#A7B1BC"


def secret(key: str, default: Any = None) -> Any:
    """Read a setting: environment variable first (overrides), then st.secrets."""
    if key in os.environ:
        return os.environ[key]
    try:
        import streamlit as st

        if key in st.secrets:
            return st.secrets[key]
    except Exception:  # no secrets.toml, or not running under Streamlit
        pass
    return default


def secret_section(name: str) -> dict:
    try:
        import streamlit as st

        if name in st.secrets:
            return dict(st.secrets[name])
    except Exception:
        pass
    return {}
