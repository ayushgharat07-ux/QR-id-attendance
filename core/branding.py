"""Look & feel: CSS, event header (name in Marathi, organiser logo), developer-credit footer."""
from __future__ import annotations

import base64
from functools import lru_cache
from html import escape

import streamlit as st

from core.config import APP_NAME, COMPANY, COMPANY_TAGLINE, CORAL, LOGO_MARK, LOGO_NAME, NAVY


@lru_cache(maxsize=8)
def _b64(path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def logo_data_uri(path=LOGO_MARK) -> str:
    return f"data:image/png;base64,{_b64(path)}"


CSS = f"""
<style>
  .block-container {{ padding-top: 1.2rem; }}
  .ubg-header {{ display:flex; gap:16px; align-items:center; padding:14px 18px; border-radius:14px;
      background: linear-gradient(120deg, {NAVY} 0%, #24507d 70%, {CORAL} 140%); color:#fff; margin-bottom: 14px; }}
  .ubg-header img {{ height:58px; background:#fff; border-radius:10px; padding:4px 8px; }}
  .ubg-header .t1 {{ font-size: 1.45rem; font-weight: 700; line-height: 1.25; }}
  .ubg-header .t2 {{ font-size: 0.95rem; opacity: .9; }}
  .ubg-header .t3 {{ font-size: 0.8rem; opacity: .8; margin-top: 2px; }}
  .ubg-footer {{ margin-top: 40px; padding: 14px; text-align:center; color:#5b6b7c; font-size: .82rem;
      border-top: 1px solid #e3e8ee; }}
  .ubg-footer img {{ height: 34px; vertical-align: middle; margin-right: 6px; }}
  .ubg-footer b {{ color:{NAVY}; letter-spacing:.04em; }}
  .scan-result {{ border-radius: 16px; padding: 18px; color: #fff; display:flex; gap:16px; align-items:center; }}
  .scan-result img {{ width: 110px; height: 132px; object-fit: cover; border-radius: 10px; border: 3px solid #fff; }}
  .scan-result .big {{ font-size: 1.6rem; font-weight: 800; }}
  .scan-result .name {{ font-size: 1.3rem; font-weight: 700; }}
  .ok {{ background: #1f9d55; }} .warn {{ background: #d69e2e; }} .bad {{ background: #c53030; }}
  @media (max-width: 640px) {{
     .ubg-header .t1 {{ font-size: 1.1rem; }} .ubg-header img {{ height: 44px; }}
     .block-container {{ padding-left: 0.8rem; padding-right: 0.8rem; }}
  }}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def event_logo_uri(event: dict | None) -> str:
    """The organiser's logo (uploaded in Event Settings), if any."""
    if not event or not event.get("logo_id"):
        return ""
    from core.ui import file_bytes

    data = file_bytes(str(event["logo_id"]))
    return f"data:image/png;base64,{base64.b64encode(data).decode()}" if data else ""


def header(event: dict | None) -> None:
    """The event is the star of the page. Without an event (sign-in page) show the app name."""
    if not event:
        title, sub, meta = APP_NAME, "QR attendance · हजेरी", ""
    else:
        title = f"{event.get('name_mr', '')} {event.get('subtitle_mr', '')}".strip()
        sub = f"{event.get('name_en', '')} {event.get('subtitle_en', '')}".strip()
        meta = " · ".join(x for x in [event.get("date", ""), event.get("time_mr", ""), event.get("venue_mr", "")] if x)
    logo = event_logo_uri(event)
    logo_html = f'<img src="{logo}" alt="logo"/>' if logo else ""
    st.markdown(
        f"""<div class="ubg-header">{logo_html}
        <div><div class="t1">{escape(title)}</div><div class="t2">{escape(sub)}</div>
        <div class="t3">{escape(meta)}</div></div></div>""",
        unsafe_allow_html=True,
    )


def footer() -> None:
    """Developer credit only — the events are not organised by UBG."""
    st.markdown(
        f"""<div class="ubg-footer"><img src="{logo_data_uri(LOGO_NAME)}" alt="United Brothers Global"/>
        App developed by <b>{COMPANY}</b> · {COMPANY_TAGLINE}</div>""",
        unsafe_allow_html=True,
    )
