"""Upload pipeline: page image -> stored form + cropped photo + AI-extracted draft person."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from PIL import Image
from pymongo.database import Database

from core import db as dbm
from core import extract, photo, repo


@dataclass
class PageJob:
    filename: str
    page_no: int
    image: Image.Image
    sha: str


@dataclass
class PageResult:
    job: PageJob
    person_id: object | None = None
    status: str = "ok"          # ok (new draft) | matched (photo added to member) | duplicate | error
    message: str = ""


AUTO_ATTACH_SCORE = repo.SAME_PERSON_SCORE  # same mobile AND name this similar -> attach photo without review


def _best_by_name(db: Database, event_id: str, fields: dict) -> tuple[dict | None, float]:
    best, best_score = None, 0.0
    for m in db.people.find({"event_id": event_id, "status": "verified"},
                            {"name_mr": 1, "name_en": 1, "pid": 1, "photo_id": 1, "mobile": 1}):
        s = max(repo.name_similarity(fields.get("name_mr", ""), m.get("name_mr", "")),
                repo.name_similarity(fields.get("name_en", ""), m.get("name_en", "")))
        if s > best_score:
            best, best_score = m, s
    return best, best_score


def jobs_from_upload(data: bytes, filename: str) -> list[PageJob]:
    file_sha = hashlib.sha256(data).hexdigest()
    pages = photo.upload_to_pages(data, filename)
    return [PageJob(filename, i + 1, img, f"{file_sha}:{i}") for i, img in enumerate(pages)]


def already_uploaded(db: Database, event_id: str, sha: str) -> dict | None:
    return db.people.find_one({"event_id": event_id, "source_sha": sha}, {"name_mr": 1, "pid": 1})


def process_page(db: Database, event: dict, ptype: str, job: PageJob, created_by: str,
                 extractor: Callable[[bytes], extract.FormExtraction] = extract.extract_form) -> PageResult:
    if dup := already_uploaded(db, event["_id"], job.sha):
        return PageResult(job, dup["_id"], "duplicate",
                          f"Already uploaded ({dup.get('pid') or 'draft'} {dup.get('name_mr', '')})")

    form_img = photo.shrink(job.image, photo.FORM_MAX_SIDE)
    form_jpeg = photo.to_jpeg(form_img, quality=75)
    form_id = dbm.put_file(form_jpeg, f"form_{job.filename}_p{job.page_no}.jpg", "image/jpeg", db,
                           event_id=event["_id"], kind="form")

    fields, meta = {}, {"source_file": job.filename, "page": job.page_no}
    hint = None
    try:
        x = extractor(form_jpeg)
        fields = extract.to_person_fields(x)
        meta.update({
            "ok": True,
            "is_form": x.is_registration_form,
            "source_language": x.source_language,
            "original": {"name": x.name.original, "vibhag": x.vibhag.original, "address": x.address.original},
            "unsure": [k for k, f in (("name", x.name), ("vibhag", x.vibhag), ("address", x.address)) if not f.confident]
                      + ([] if x.mobile_confident else ["mobile"]),
            "notes": x.notes,
        })
        if x.photo_present and x.photo_box.w > 0:
            b = x.photo_box
            hint = photo.Box(b.x, b.y, b.w, b.h)
    except extract.ExtractionError as e:
        meta.update({"ok": False, "error": str(e)})

    ph, box, method = photo.extract_photo(form_img, hint)
    photo_id = None
    if ph is not None:
        photo_id = dbm.put_file(photo.to_jpeg(ph, 88), "photo.jpg", "image/jpeg", db,
                                event_id=event["_id"], kind="photo")
    meta["photo_method"] = method
    meta["photo_box"] = box.__dict__ if box else None

    # Does this form belong to a member who is already in the database (e.g. imported from Excel)?
    member, score = repo.best_member_match(db, event["_id"], fields.get("mobile", ""),
                                           fields.get("name_mr", ""), fields.get("name_en", ""))
    if member is None and (fields.get("name_mr") or fields.get("name_en")):
        member, score = _best_by_name(db, event["_id"], fields)
        score = score if score >= repo.SIMILAR_NAME_SCORE else 0.0
        member = member if score else None
    if (member is not None and score >= AUTO_ATTACH_SCORE and fields.get("mobile") == member.get("mobile")
            and photo_id is not None and not member.get("photo_id")):
        draft = {"_id": None, "photo_id": photo_id, "form_file_id": form_id, **fields}
        repo.merge_draft_into(db, draft, member)
        db.people.update_one({"_id": member["_id"]}, {"$set": {"source_sha": job.sha}})
        return PageResult(job, member["_id"], "matched",
                          f"Photo added to {member.get('pid')} {member.get('name_mr') or member.get('name_en', '')}")
    if member is not None:
        meta["suggest_member"] = str(member["_id"])
        meta["suggest_score"] = round(score, 2)
        if member.get("photo_id"):
            meta["suggest_reason"] = "This member already has a photo."

    person_id = repo.create_draft(db, event["_id"], ptype, fields, photo_id=photo_id, form_file_id=form_id,
                                  extraction=meta, created_by=created_by)
    db.people.update_one({"_id": person_id}, {"$set": {"source_sha": job.sha}})
    if not meta.get("ok"):
        return PageResult(job, person_id, "error", meta.get("error", "AI reading failed"))
    return PageResult(job, person_id, "ok", fields.get("name_mr") or fields.get("name_en") or "")


def rerun_extraction(db: Database, person: dict,
                     extractor: Callable[[bytes], extract.FormExtraction] = extract.extract_form) -> None:
    """Re-read the stored form image and overwrite the draft's fields."""
    form_jpeg = dbm.get_file(person.get("form_file_id"), db)
    if not form_jpeg:
        raise extract.ExtractionError("The original form image is missing.")
    x = extractor(form_jpeg)
    meta = dict(person.get("extraction") or {})
    meta.update({"ok": True, "error": None, "is_form": x.is_registration_form,
                 "source_language": x.source_language, "notes": x.notes,
                 "unsure": [k for k, f in (("name", x.name), ("vibhag", x.vibhag), ("address", x.address))
                            if not f.confident] + ([] if x.mobile_confident else ["mobile"])})
    repo.update_person(db, person["_id"], extract.to_person_fields(x), extraction=meta)


def replace_photo(db: Database, person: dict, img: Image.Image) -> None:
    ph = photo.shrink(img.convert("RGB"), photo.PHOTO_MAX_SIDE)
    new_id = dbm.put_file(photo.to_jpeg(ph, 88), "photo.jpg", "image/jpeg", db,
                          event_id=person["event_id"], kind="photo")
    old = person.get("photo_id")
    db.people.update_one({"_id": person["_id"]}, {"$set": {"photo_id": new_id}})
    dbm.delete_file(old, db)
