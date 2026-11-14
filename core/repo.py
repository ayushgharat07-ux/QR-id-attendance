"""Domain operations on events, users, people and attendance."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from core import db as dbm
from core.config import secret

TYPES = {"attendee": "A", "volunteer": "V"}
TYPE_LABELS = {"attendee": "Attendee / श्रीसदस्य", "volunteer": "Volunteer / स्वयंसेवक"}

# Text fields kept in two languages (<field>_mr, <field>_en)
BILINGUAL_FIELDS = ("name", "vibhag", "address")
# Single-language extra details (from Excel imports)
EXTRA_FIELDS = ("business", "category", "group", "age", "att_no")
EXTRA_LABELS = {"business": "Business · व्यवसाय", "category": "Business category", "group": "Baithak / Group",
                "age": "Age", "att_no": "Old attendance no."}

DEFAULT_EVENT = {
    "_id": "thane-2026",
    "name_mr": "व्यवसाय मार्गदर्शन शिबिर व उन्नतीकरण अभियान",
    "subtitle_mr": "ठाणे २०२६",
    "name_en": "Business Guidance Camp & Upliftment Campaign",
    "subtitle_en": "Thane 2026",
    "date": "2026-11-23",
    "time_mr": "सकाळी ७ ते २",
    "time_en": "7 AM to 2 PM",
    "venue_mr": "राम गणेश गडकरी रंगायतन, तलाव पाली जवळ, ठाणे-पश्चिम",
    "venue_en": "Ram Ganesh Gadkari Rangayatan, Near Talao Pali, Thane West",
    "id_prefix": "TH26",
    "card_design": "business_camp_badge",
    "active": True,
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(x) -> ObjectId:
    return x if isinstance(x, ObjectId) else ObjectId(str(x))


# ---------------------------------------------------------------- events
def list_events(db: Database) -> list[dict]:
    return list(db.events.find().sort("date", -1))


def get_event(db: Database, event_id: str) -> dict | None:
    return db.events.find_one({"_id": event_id})


def save_event(db: Database, event: dict) -> None:
    event = dict(event)
    event_id = event.pop("_id")
    event["updated_at"] = now()
    db.events.update_one({"_id": event_id}, {"$set": event, "$setOnInsert": {"created_at": now()}}, upsert=True)


def prefix_in_use(db: Database, prefix: str, except_event: str | None = None) -> dict | None:
    q: dict = {"id_prefix": prefix}
    if except_event:
        q["_id"] = {"$ne": except_event}
    return db.events.find_one(q)


def clear_attendance(db: Database, event_id: str) -> int:
    """Remove every check-in of an event (e.g. after a dry run). Members, photos and IDs stay."""
    return db.attendance.delete_many({"event_id": event_id}).deleted_count


def delete_event(db: Database, event_id: str) -> dict:
    """Delete an event and everything that belongs to it: members, photos, form scans, check-ins,
    ID counters, card template and logo. Other events are not touched."""
    event = get_event(db, event_id)
    if event is None:
        return {"people": 0, "files": 0, "attendance": 0}
    files = 0
    for p in db.people.find({"event_id": event_id}, {"photo_id": 1, "form_file_id": 1}):
        for f in (p.get("photo_id"), p.get("form_file_id")):
            if f:
                dbm.delete_file(f, db)
                files += 1
    for f in (event.get("card_template_id"), event.get("logo_id")):
        if f:
            dbm.delete_file(f, db)
            files += 1
    # any other stored file tagged with this event (e.g. old templates)
    for f in db.fs.files.find({"metadata.event_id": event_id}, {"_id": 1}):
        dbm.delete_file(f["_id"], db)
        files += 1
    out = {"people": db.people.delete_many({"event_id": event_id}).deleted_count,
           "attendance": db.attendance.delete_many({"event_id": event_id}).deleted_count,
           "files": files}
    db.counters.delete_many({"_id": {"$regex": f"^{re.escape(event_id)}:"}})
    db.oauth_states.delete_many({"event_id": event_id})
    db.events.delete_one({"_id": event_id})
    return out


def ensure_default_event(db: Database) -> None:
    if db.events.count_documents({}) == 0:
        save_event(db, DEFAULT_EVENT)


# ---------------------------------------------------------------- users
def get_role(db: Database, email: str | None) -> str | None:
    if not email:
        return None
    email = email.strip().lower()
    boot = (secret("BOOTSTRAP_ADMIN_EMAIL") or "").strip().lower()
    if boot and email == boot:
        return "admin"
    user = db.users.find_one({"_id": email})
    return user["role"] if user else None


def list_users(db: Database) -> list[dict]:
    return list(db.users.find().sort([("role", 1), ("_id", 1)]))


def add_user(db: Database, email: str, role: str, name: str = "") -> None:
    assert role in ("admin", "volunteer")
    db.users.update_one(
        {"_id": email.strip().lower()},
        {"$set": {"role": role, "name": name, "updated_at": now()}},
        upsert=True,
    )


def remove_user(db: Database, email: str) -> None:
    db.users.delete_one({"_id": email.strip().lower()})


# ---------------------------------------------------------------- people
def next_pid(db: Database, event: dict, ptype: str) -> str:
    letter = TYPES[ptype]
    doc = db.counters.find_one_and_update(
        {"_id": f"{event['_id']}:{letter}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"{event['id_prefix']}-{letter}-{doc['seq']:04d}"


def create_draft(db: Database, event_id: str, ptype: str, fields: dict, *, photo_id=None,
                 form_file_id=None, extraction: dict | None = None, created_by: str = "") -> ObjectId:
    doc = {
        "event_id": event_id,
        "type": ptype,
        "status": "draft",
        "pid": None,
        "photo_id": photo_id,
        "form_file_id": form_file_id,
        "extraction": extraction or {},
        "created_by": created_by,
        "created_at": now(),
        **_clean_fields(fields),
    }
    return db.people.insert_one(doc).inserted_id


def _clean_fields(fields: dict) -> dict:
    out = {}
    for f in BILINGUAL_FIELDS:
        for lang in ("mr", "en"):
            key = f"{f}_{lang}"
            if key in fields:
                out[key] = (fields[key] or "").strip()
    if "mobile" in fields:
        out["mobile"] = (fields["mobile"] or "").strip()
    for key in EXTRA_FIELDS:
        if key in fields:
            v = fields[key]
            out[key] = "" if v is None else str(v).strip()
    if "type" in fields:
        out["type"] = fields["type"]
    return out


def list_people(db: Database, event_id: str, status: str | None = None, ptype: str | None = None) -> list[dict]:
    q: dict = {"event_id": event_id}
    if status:
        q["status"] = status
    if ptype:
        q["type"] = ptype
    return list(db.people.find(q).sort([("pid", 1), ("created_at", 1)]))


def get_person(db: Database, person_id) -> dict | None:
    return db.people.find_one({"_id": _oid(person_id)})


def get_person_by_pid(db: Database, event_id: str, pid: str) -> dict | None:
    return db.people.find_one({"event_id": event_id, "pid": pid})


def update_person(db: Database, person_id, fields: dict, **extra) -> None:
    upd = _clean_fields(fields)
    upd.update(extra)
    upd["updated_at"] = now()
    db.people.update_one({"_id": _oid(person_id)}, {"$set": upd})


def approve_person(db: Database, person_id, event: dict, by: str) -> str:
    person = get_person(db, person_id)
    if person is None:
        raise ValueError("Person not found")
    pid = person.get("pid") or next_pid(db, event, person["type"])
    db.people.update_one(
        {"_id": person["_id"]},
        {"$set": {"pid": pid, "status": "verified", "verified_by": by, "verified_at": now()}},
    )
    return pid


def delete_person(db: Database, person_id) -> None:
    person = get_person(db, person_id)
    if not person:
        return
    if person.get("pid"):
        db.attendance.delete_one({"_id": f"{person['event_id']}:{person['pid']}"})
    dbm.delete_file(person.get("photo_id"), db)
    # The form image may be shared by several drafts only if a PDF page was reused; keep it simple:
    if not db.people.count_documents({"form_file_id": person.get("form_file_id"), "_id": {"$ne": person["_id"]}}):
        dbm.delete_file(person.get("form_file_id"), db)
    db.people.delete_one({"_id": person["_id"]})


def find_by_mobile(db: Database, event_id: str, mobile: str, exclude_id=None) -> list[dict]:
    if not mobile:
        return []
    q: dict = {"event_id": event_id, "mobile": mobile}
    if exclude_id is not None:
        q["_id"] = {"$ne": _oid(exclude_id)}
    return list(db.people.find(q))


_HONORIFICS = re.compile(r"^(श्रीमती|श्री|सौ|कु|कुमारी|मा|mr|mrs|ms|miss|shri|shree|smt|sau|kum|kumar|kumari|dr)\.?\s*",
                         re.IGNORECASE)


def name_key(name: str) -> str:
    """Name without honorific, punctuation or spaces, for fuzzy comparison."""
    n = (name or "").strip()
    n = _HONORIFICS.sub("", n)
    return re.sub(r"[\s.,\-_/()]+", "", n).lower()


def name_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    a, b = name_key(a), name_key(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def best_member_match(db: Database, event_id: str, mobile: str, name_mr: str, name_en: str,
                      exclude_id=None) -> tuple[dict | None, float]:
    """Find the approved member a form belongs to: same mobile, best-matching name.
    Returns (member, name_score); member is None when nobody has that mobile."""
    if not mobile:
        return None, 0.0
    q: dict = {"event_id": event_id, "status": "verified", "mobile": mobile}
    if exclude_id is not None:
        q["_id"] = {"$ne": _oid(exclude_id)}
    best, best_score = None, -1.0
    for m in db.people.find(q):
        score = max(name_similarity(name_mr, m.get("name_mr", "")), name_similarity(name_en, m.get("name_en", "")))
        if score > best_score:
            best, best_score = m, score
    return best, max(best_score, 0.0)


SAME_PERSON_SCORE = 0.85    # same mobile + names this similar = same person. Calibrated on the Kon list:
                            # husband & wife sharing a phone score 0.69, different members up to 0.81,
                            # the same person written differently 0.88-1.00.
SIMILAR_NAME_SCORE = 0.9    # different/missing mobile but names this similar = check by hand

DUP_LABELS = {"same": "same person (same mobile & name)",
              "same_mobile": "same mobile, different name (family?)",
              "similar_name": "very similar name, different mobile"}


def find_duplicates(db: Database, event_id: str, fields: dict, exclude_id=None) -> list[dict]:
    """Everyone already in this event (approved or draft) who may be the same person as `fields`.
    Returns [{"person", "kind": same|same_mobile|similar_name, "score"}], strongest first."""
    mobile = (fields.get("mobile") or "").strip()
    name_mr, name_en = fields.get("name_mr", ""), fields.get("name_en", "")
    q: dict = {"event_id": event_id}
    if exclude_id is not None:
        q["_id"] = {"$ne": _oid(exclude_id)}
    out = []
    for p in db.people.find(q, {"name_mr": 1, "name_en": 1, "mobile": 1, "pid": 1, "status": 1, "photo_id": 1}):
        score = max(name_similarity(name_mr, p.get("name_mr", "")), name_similarity(name_en, p.get("name_en", "")))
        same_mobile = bool(mobile) and p.get("mobile") == mobile
        if same_mobile and score >= SAME_PERSON_SCORE:
            kind = "same"
        elif same_mobile:
            kind = "same_mobile"
        elif score >= SIMILAR_NAME_SCORE:
            kind = "similar_name"
        else:
            continue
        out.append({"person": p, "kind": kind, "score": round(score, 2)})
    order = {"same": 0, "same_mobile": 1, "similar_name": 2}
    return sorted(out, key=lambda d: (order[d["kind"]], -d["score"]))


def describe(p: dict) -> str:
    who = p.get("pid") or "draft"
    return f"{who} · {p.get('name_mr') or p.get('name_en') or '?'} · {p.get('mobile') or 'no mobile'}"


def merge_draft_into(db: Database, draft: dict, member: dict) -> None:
    """Move a reviewed form's photo + form image (and any details the member is missing) onto an
    existing member, then delete the draft."""
    upd: dict = {"form_file_id": draft.get("form_file_id"), "updated_at": now()}
    for f in BILINGUAL_FIELDS:
        for lang in ("mr", "en"):
            k = f"{f}_{lang}"
            if not member.get(k) and draft.get(k):
                upd[k] = draft[k]
    if draft.get("photo_id"):
        upd["photo_id"] = draft["photo_id"]
        if member.get("photo_id") and member["photo_id"] != draft["photo_id"]:
            dbm.delete_file(member["photo_id"], db)
    db.people.update_one({"_id": member["_id"]}, {"$set": upd})
    if draft.get("_id") is not None:
        db.people.delete_one({"_id": draft["_id"]})  # its files now belong to the member


def search_people(db: Database, event_id: str, text: str, limit: int = 20) -> list[dict]:
    text = text.strip()
    if not text:
        return []
    import re

    rx = {"$regex": re.escape(text), "$options": "i"}
    q = {"event_id": event_id, "status": "verified",
         "$or": [{"name_mr": rx}, {"name_en": rx}, {"mobile": rx}, {"pid": rx}, {"att_no": rx}]}
    return list(db.people.find(q).limit(limit))


# ---------------------------------------------------------------- attendance
def mark_attendance(db: Database, event_id: str, person: dict, by: str) -> tuple[str, dict]:
    """Returns ("marked", record) for a new check-in or ("already", existing_record)."""
    rec = {
        "_id": f"{event_id}:{person['pid']}",
        "event_id": event_id,
        "pid": person["pid"],
        "person_id": person["_id"],
        "type": person["type"],
        "name_mr": person.get("name_mr", ""),
        "name_en": person.get("name_en", ""),
        "vibhag_mr": person.get("vibhag_mr", ""),
        "scanned_by": by,
        "scanned_at": now(),
    }
    try:
        db.attendance.insert_one(rec)
        return "marked", rec
    except DuplicateKeyError:
        return "already", db.attendance.find_one({"_id": rec["_id"]})


def unmark_attendance(db: Database, event_id: str, pid: str) -> None:
    db.attendance.delete_one({"_id": f"{event_id}:{pid}"})


def list_attendance(db: Database, event_id: str, limit: int | None = None) -> list[dict]:
    cur = db.attendance.find({"event_id": event_id}).sort("scanned_at", -1)
    if limit:
        cur = cur.limit(limit)
    return list(cur)


def stats(db: Database, event_id: str) -> dict:
    people = list(db.people.find({"event_id": event_id, "status": "verified"},
                                 {"type": 1, "vibhag_mr": 1, "group": 1, "pid": 1}))
    present = {a["pid"] for a in db.attendance.find({"event_id": event_id}, {"pid": 1})}
    out = {"registered": len(people), "present": 0,
           "by_type": {t: {"registered": 0, "present": 0} for t in TYPES},
           "by_vibhag": {},
           "drafts": db.people.count_documents({"event_id": event_id, "status": "draft"})}
    for p in people:
        is_present = p["pid"] in present
        out["present"] += is_present
        bt = out["by_type"].setdefault(p["type"], {"registered": 0, "present": 0})
        bt["registered"] += 1
        bt["present"] += is_present
        v = (p.get("group") or p.get("vibhag_mr") or "—").strip() or "—"  # Baithak group if known
        bv = out["by_vibhag"].setdefault(v, {"registered": 0, "present": 0})
        bv["registered"] += 1
        bv["present"] += is_present
    return out
