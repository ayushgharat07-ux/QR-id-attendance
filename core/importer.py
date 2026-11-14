"""Import members from an Excel directory (e.g. Kon_Final_Directory_2026.xlsx)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from openpyxl import load_workbook
from pymongo.database import Database

from core import repo
from core.extract import normalize_mobile

# field -> header names that mean it (lower-case). Exact matches win over "contains".
FIELDS = {
    "name_en": ["name", "full name", "member name"],
    "name_mr": ["name (marathi)", "marathi name", "नाव", "मराठी नाव"],
    "mobile": ["phone no.", "phone no", "phone", "mobile", "mobile no.", "contact", "भ्रमणध्वनी", "मोबाईल"],
    "att_no": ["attendance no.", "attendance no", "attendance number", "att no"],
    "age": ["age", "वय"],
    "business": ["business", "व्यवसाय"],
    "category": ["business category", "category"],
    "group": ["baithak / group", "baithak/group", "baithak", "group"],
}
FIELD_LABELS = {"name_en": "Name (English)", "name_mr": "Name (Marathi)", "mobile": "Phone", "att_no": "Old attendance no.",
                "age": "Age", "business": "Business", "category": "Business category", "group": "Baithak / Group"}

# English title -> Marathi honorific
TITLE_MAP = [(re.compile(r"^(mrs|smt|sau)\b\.?", re.I), "सौ."),
             (re.compile(r"^(miss|ms|kumari|kum|kumar|ku)\b\.?", re.I), "कु."),
             (re.compile(r"^(mr|shri|shree)\b\.?", re.I), "श्री.")]


def _norm(h) -> str:
    return re.sub(r"\s+", " ", str(h or "")).strip().lower()


def sheet_names(data: bytes) -> list[str]:
    return load_workbook(io.BytesIO(data), read_only=True, data_only=True).sheetnames


@dataclass
class Table:
    sheet: str
    header_row: int                 # 1-based row number in Excel
    headers: list[str]
    rows: list[list]                # data rows (values), blank rows dropped
    title_lines: list[str] = field(default_factory=list)  # text above the header (event / vibhag lines)


def read_table(data: bytes, sheet: str) -> Table:
    ws = load_workbook(io.BytesIO(data), data_only=True)[sheet]
    all_rows = [list(r) for r in ws.iter_rows(values_only=True)]
    header_idx = 0
    for i, r in enumerate(all_rows[:30]):
        if any(_norm(c) in FIELDS["name_en"] + FIELDS["name_mr"] for c in r):
            header_idx = i
            break
    headers = [str(c).strip() if c is not None else "" for c in all_rows[header_idx]]
    rows = [r for r in all_rows[header_idx + 1:] if any(c not in (None, "") for c in r)]
    titles = [str(c).strip() for r in all_rows[:header_idx] for c in r if c not in (None, "")]
    return Table(sheet, header_idx + 1, headers, rows, titles)


def guess_mapping(headers: list[str]) -> dict[str, int | None]:
    norm = [_norm(h) for h in headers]
    mapping: dict[str, int | None] = {f: None for f in FIELDS}
    used: set[int] = set()
    for exact in (True, False):
        for f, names in FIELDS.items():
            if mapping[f] is not None:
                continue
            for i, h in enumerate(norm):
                if i in used or not h:
                    continue
                if (h in names) if exact else any(n in h for n in names if len(n) > 3):
                    mapping[f] = i
                    used.add(i)
                    break
    return mapping


def guess_vibhag(titles: list[str]) -> str:
    """'विभाग- कोन , तालुका. पनवेल (वर्ष 2026)' -> 'कोन, तालुका. पनवेल'"""
    for t in titles:
        m = re.search(r"विभाग\s*[-:–]\s*([^()]+)", t)
        if m:
            return re.sub(r"\s*,\s*", ", ", m.group(1)).strip(" ,")
    return ""


def marathi_title(name_en: str) -> str | None:
    for rx, mr in TITLE_MAP:
        if rx.match(name_en.strip()):
            return mr
    return None


def _cell(row: list, idx: int | None):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def marathi_lookup(table: Table) -> dict[str, list[str]]:
    """phone -> Marathi names, from a sheet that has the same members with Marathi names
    (rows may be in a different order, so we match on phone, never on row position)."""
    m = guess_mapping(table.headers)
    name_col = m["name_mr"] if m["name_mr"] is not None else m["name_en"]
    out: dict[str, list[str]] = {}
    for r in table.rows:
        name, phone = _text(_cell(r, name_col)), normalize_mobile(_text(_cell(r, m["mobile"])))
        if name and phone and re.search(r"[ऀ-ॿ]", name):
            out.setdefault(phone, []).append(name)
    return out


def _pick_marathi(name_en: str, candidates: list[str], used: set[str]) -> str:
    free = [c for c in candidates if c not in used] or candidates
    if len(free) > 1:  # e.g. husband & wife sharing one phone: use श्री./सौ.
        t = marathi_title(name_en)
        same_title = [c for c in free if t and c.startswith(t)]
        if len(same_title) == 1:
            return same_title[0]
    return free[0] if free else ""


@dataclass
class Record:
    fields: dict
    warnings: list[str]


def build_records(table: Table, mapping: dict[str, int | None],
                  lookup: dict[str, list[str]] | None = None) -> list[Record]:
    records, used = [], set()
    for r in table.rows:
        f = {k: _text(_cell(r, i)) for k, i in mapping.items() if i is not None}
        if not f.get("name_en") and not f.get("name_mr"):
            continue
        warnings = []
        raw_phone = f.get("mobile", "")
        f["mobile"] = normalize_mobile(raw_phone)
        if raw_phone and len(f["mobile"]) != 10:
            warnings.append(f"phone '{raw_phone}' is not 10 digits")
        if not raw_phone:
            warnings.append("no phone")
        if not f.get("name_mr") and lookup is not None and f["mobile"] in lookup:
            f["name_mr"] = _pick_marathi(f.get("name_en", ""), lookup[f["mobile"]], used)
            used.add(f["name_mr"])
        if not f.get("name_mr"):
            warnings.append("no Marathi name")
        records.append(Record(f, warnings))
    return records


@dataclass
class OtherSheets:
    checked: list[str]                   # sheets that look like member lists
    total_rows: int
    extra: list[Record]                  # people found ONLY in those sheets


def check_other_sheets(data: bytes, skip: set[str], main: list[Record],
                       lookup: dict[str, list[str]] | None) -> OtherSheets:
    """Many directories repeat the members in per-category sheets (Manufacturer, Trader, ...).
    Read every other sheet that has a Name column and report anyone missing from the main list."""
    known = [(r.fields.get("mobile", ""), r.fields.get("name_en", "")) for r in main]
    out = OtherSheets([], 0, [])
    seen: list[tuple[str, str]] = []
    for sheet in sheet_names(data):
        if sheet in skip:
            continue
        try:
            table = read_table(data, sheet)
        except Exception:
            continue
        mapping = guess_mapping(table.headers)
        if mapping["name_en"] is None and mapping["name_mr"] is None:
            continue
        out.checked.append(sheet)
        for rec in build_records(table, mapping, lookup):
            out.total_rows += 1
            key = (rec.fields.get("mobile", ""), rec.fields.get("name_en", ""))
            same = lambda k: k[0] == key[0] and repo.name_similarity(k[1], key[1]) >= repo.SAME_PERSON_SCORE  # noqa: E731
            if not any(same(k) for k in known) and not any(same(k) for k in seen):
                rec.warnings.append(f"only in sheet '{sheet}'")
                out.extra.append(rec)
                seen.append(key)
    return out


def find_existing(db: Database, event_id: str, f: dict) -> dict | None:
    """Same person already imported? Same phone and (nearly) the same name."""
    if not f.get("mobile"):
        return db.people.find_one({"event_id": event_id, "name_en": f.get("name_en"), "mobile": ""})
    for p in db.people.find({"event_id": event_id, "mobile": f["mobile"]}):
        if (repo.name_similarity(p.get("name_en", ""), f.get("name_en", "")) >= repo.SAME_PERSON_SCORE
                or repo.name_similarity(p.get("name_mr", ""), f.get("name_mr", "")) >= repo.SAME_PERSON_SCORE):
            return p
    return None


@dataclass
class ImportSummary:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)


def import_records(db: Database, event: dict, records: list[Record], ptype: str, vibhag_mr: str,
                   vibhag_en: str, by: str) -> ImportSummary:
    """Create approved members (with IDs) or update ones imported before. Photos are never touched."""
    summary = ImportSummary()
    for rec in records:
        f = dict(rec.fields)
        if vibhag_mr:
            f.setdefault("vibhag_mr", vibhag_mr)
        if vibhag_en:
            f.setdefault("vibhag_en", vibhag_en)
        existing = find_existing(db, event["_id"], f)
        if existing:
            repo.update_person(db, existing["_id"], f)
            if existing.get("status") != "verified":
                repo.approve_person(db, existing["_id"], event, by)
            summary.updated.append(existing.get("pid") or str(existing["_id"]))
        else:
            pid_ = repo.create_draft(db, event["_id"], ptype, f, created_by=by,
                                     extraction={"source": "excel", "warnings": rec.warnings})
            summary.created.append(repo.approve_person(db, pid_, event, by))
    return summary
