"""Excel workbook and photo ZIP exports."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import timezone, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core.config import NAVY

IST = timezone(timedelta(hours=5, minutes=30))

COLUMNS = [
    ("ID", "pid"), ("Type", "type"), ("नाव (मराठी)", "name_mr"), ("Name (English)", "name_en"),
    ("Mobile", "mobile"), ("विभाग", "vibhag_mr"), ("Division", "vibhag_en"),
    ("पत्ता (मराठी)", "address_mr"), ("Address (English)", "address_en"),
    ("Business", "business"), ("Category", "category"), ("Baithak / Group", "group"), ("Age", "age"),
    ("Old attendance no.", "att_no"),
    ("Status", "_status"), ("Check-in time (IST)", "_time"), ("Marked by", "_by"),
]
STATUS_COL = [k for _, k in COLUMNS].index("_status")


def fmt_time(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%d-%m-%Y %I:%M %p")


def rows(people: list[dict], attendance: list[dict]) -> list[list[str]]:
    att = {a["pid"]: a for a in attendance}
    out = []
    for p in people:
        a = att.get(p.get("pid"))
        extra = {"_status": "Present" if a else "Absent",
                 "_time": fmt_time(a["scanned_at"]) if a else "",
                 "_by": a.get("scanned_by", "") if a else ""}
        out.append([str(extra.get(k) if k.startswith("_") else p.get(k) or "") for _, k in COLUMNS])
    return out


def excel_bytes(event: dict, people: list[dict], attendance: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append([f"{event.get('name_mr', '')} {event.get('subtitle_mr', '')}".strip()])
    ws.append([f"{event.get('name_en', '')} — {event.get('date', '')} — {event.get('venue_en', '')}"])
    ws.append([])
    ws.append([c for c, _ in COLUMNS])
    header_row = ws.max_row
    for r in rows(people, attendance):
        ws.append(r)

    ws["A1"].font = Font(size=14, bold=True, color=NAVY.lstrip("#"))
    for cell in ws[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY.lstrip("#"))
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    widths = [14, 11, 30, 30, 13, 20, 20, 45, 45, 30, 13, 24, 6, 10, 10, 20, 26]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(COLUMNS))}{ws.max_row}"

    s = wb.create_sheet("Summary")
    present = sum(1 for r in rows(people, attendance) if r[STATUS_COL] == "Present")
    s.append(["Registered", len(people)])
    s.append(["Present", present])
    s.append(["Absent", len(people) - present])
    s.append([])
    s.append(["Attendance app developed by UNITED BROTHERS GLOBAL"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def safe_name(text: str) -> str:
    text = re.sub(r"^(shri|shree|smt|sau|kum|kumari|kumar|mr|mrs|ms|miss|dr)(\.\s*|\s+)", "", (text or "").strip(), flags=re.I)
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text[:60] or "person"


def photo_filename(person: dict) -> str:
    return f"{person.get('pid') or 'DRAFT'}_{safe_name(person.get('name_en', ''))}.jpg"


def zip_bytes(files: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()
