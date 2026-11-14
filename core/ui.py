"""Shared UI helpers."""
from __future__ import annotations

import base64
from html import escape

import streamlit as st

from core import db as dbm
from core import exports


@st.cache_data(max_entries=2000, show_spinner=False)
def file_bytes(file_id: str) -> bytes | None:
    return dbm.get_file(file_id)


def photo_uri(person: dict | None) -> str:
    data = file_bytes(str(person["photo_id"])) if person and person.get("photo_id") else None
    if not data:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def result_card(status: str, title: str, person: dict | None = None, detail: str = "") -> None:
    cls = {"ok": "ok", "warn": "warn", "bad": "bad"}[status]
    img = photo_uri(person)
    img_html = f'<img src="{img}"/>' if img else ""
    name_html = ""
    if person:
        name_html = (f'<div class="name">{escape(person.get("name_mr", ""))}</div>'
                     f'<div>{escape(person.get("name_en", ""))}</div>'
                     f'<div>{escape(person.get("pid") or "")} · {escape(person.get("vibhag_mr", ""))}</div>')
    st.markdown(
        f'<div class="scan-result {cls}">{img_html}<div><div class="big">{escape(title)}</div>'
        f'{name_html}<div>{escape(detail)}</div></div></div>',
        unsafe_allow_html=True,
    )


def fmt_time(dt) -> str:
    return exports.fmt_time(dt)


def load_photo(person: dict):
    import io

    from PIL import Image

    b = file_bytes(str(person["photo_id"])) if person.get("photo_id") else None
    return Image.open(io.BytesIO(b)) if b else None


def uploaded_template(event: dict):
    import io

    from PIL import Image

    if not event.get("card_template_id"):
        return None
    b = file_bytes(str(event["card_template_id"]))
    return Image.open(io.BytesIO(b)).convert("RGB") if b else None


def event_logo(event: dict):
    import io

    from PIL import Image

    b = file_bytes(str(event["logo_id"])) if event.get("logo_id") else None
    return Image.open(io.BytesIO(b)) if b else None


def render_card(person: dict, event: dict, design: str | None = None, layout: dict | None = None):
    from core import cards

    return cards.render_card(person, event, load_photo(person), uploaded_template(event), design, layout,
                             event_logo(event))


FIELD_LABELS = {"name": ("नाव (मराठी)", "Name (English)"),
                "vibhag": ("विभाग (मराठी)", "Division (English)"),
                "address": ("पत्ता (मराठी)", "Address (English)")}


def person_editor(person: dict, key: str, allow_type_change: bool = True) -> dict:
    """Bilingual edit form. Returns the edited values (not saved)."""
    from core.repo import TYPE_LABELS

    unsure = set((person.get("extraction") or {}).get("unsure") or [])
    flag = lambda f: " ⚠️ check" if f in unsure else ""  # noqa: E731
    vals: dict = {}
    types = list(TYPE_LABELS)
    vals["type"] = st.radio("Type", types, index=types.index(person.get("type", "attendee")),
                            format_func=TYPE_LABELS.get, horizontal=True, key=f"type_{key}",
                            disabled=not allow_type_change)
    for f, (lmr, len_) in FIELD_LABELS.items():
        widget = st.text_area if f == "address" else st.text_input
        c1, c2 = st.columns(2)
        vals[f"{f}_mr"] = widget(lmr + flag(f), person.get(f"{f}_mr", ""), key=f"{f}_mr_{key}")
        vals[f"{f}_en"] = widget(len_ + flag(f), person.get(f"{f}_en", ""), key=f"{f}_en_{key}")
    vals["mobile"] = st.text_input("Mobile · भ्रमणध्वनी" + flag("mobile"), person.get("mobile", ""),
                                   key=f"mobile_{key}", max_chars=10)
    from core.repo import EXTRA_FIELDS, EXTRA_LABELS

    c1, c2 = st.columns(2)
    for i, f in enumerate(EXTRA_FIELDS):
        vals[f] = (c1 if i % 2 == 0 else c2).text_input(EXTRA_LABELS[f], str(person.get(f, "") or ""),
                                                        key=f"{f}_{key}")
    return vals


def validate(vals: dict) -> list[str]:
    errs = []
    if not (vals.get("name_mr") or vals.get("name_en")):
        errs.append("Name is required.")
    m = vals.get("mobile", "")
    if m and (not m.isdigit() or len(m) != 10):
        errs.append("Mobile number must be 10 digits.")
    return errs
