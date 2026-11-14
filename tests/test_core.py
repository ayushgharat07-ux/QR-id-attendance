import io

import pytest
from openpyxl import load_workbook
from PIL import Image

from conftest import SAMPLES
from core import cards, exports, extract, photo, pipeline, qr, repo


# ---------------------------------------------------------------- QR
def test_qr_roundtrip_and_tamper():
    payload = qr.make_payload("thane-2026", "TH26-A-0001")
    assert qr.parse_payload(payload) == ("thane-2026", "TH26-A-0001")
    assert qr.parse_payload(payload.replace("A-0001", "A-0002")) is None
    assert qr.parse_payload("hello") is None
    assert qr.parse_payload("") is None


def test_qr_image_decodes():
    from core.scanner import decode_qr_image

    payload = qr.make_payload("thane-2026", "TH26-V-0007")
    assert decode_qr_image(qr.qr_png(payload)) == payload


# ---------------------------------------------------------------- repo
def _draft(db, event, ptype="attendee", **fields):
    base = {"name_mr": "श्री. संदेश शिंदे", "name_en": "Shri. Sandesh Shinde", "mobile": "8010706467",
            "vibhag_mr": "कोन", "vibhag_en": "Kon", "address_mr": "", "address_en": ""}
    base.update(fields)
    return repo.create_draft(db, event["_id"], ptype, base)


def test_ids_are_sequential_per_type(db, event):
    a1 = repo.approve_person(db, _draft(db, event), event, "x")
    a2 = repo.approve_person(db, _draft(db, event), event, "x")
    v1 = repo.approve_person(db, _draft(db, event, "volunteer"), event, "x")
    assert (a1, a2, v1) == ("TH26-A-0001", "TH26-A-0002", "TH26-V-0001")
    # approving again keeps the same ID
    p = repo.get_person_by_pid(db, event["_id"], a1)
    assert repo.approve_person(db, p["_id"], event, "x") == a1


def test_attendance_once_only_and_stats(db, event):
    pid = repo.approve_person(db, _draft(db, event), event, "x")
    repo.approve_person(db, _draft(db, event, "volunteer"), event, "x")
    _draft(db, event)  # unapproved draft does not count
    person = repo.get_person_by_pid(db, event["_id"], pid)
    s1, rec = repo.mark_attendance(db, event["_id"], person, "vol@example.com")
    s2, rec2 = repo.mark_attendance(db, event["_id"], person, "other@example.com")
    assert s1 == "marked" and s2 == "already" and rec2["scanned_by"] == "vol@example.com"
    st = repo.stats(db, event["_id"])
    assert st["registered"] == 2 and st["present"] == 1 and st["drafts"] == 1
    assert st["by_type"]["attendee"] == {"registered": 1, "present": 1}
    repo.unmark_attendance(db, event["_id"], pid)
    assert repo.stats(db, event["_id"])["present"] == 0


def test_roles(db):
    assert repo.get_role(db, "OWNER@example.com") == "admin"
    assert repo.get_role(db, "nobody@example.com") is None
    repo.add_user(db, " Vol@Example.com ", "volunteer")
    assert repo.get_role(db, "vol@example.com") == "volunteer"


# ---------------------------------------------------------------- extraction helpers
@pytest.mark.parametrize("raw,expected", [
    ("९७०२३९७०००", "9702397000"), ("+91 80107 06467", "8010706467"),
    ("08010706467", "8010706467"), ("8097-153-667", "8097153667"), ("", ""),
])
def test_normalize_mobile(raw, expected):
    assert extract.normalize_mobile(raw) == expected


def test_missing_api_key_is_a_clean_extraction_error(monkeypatch):
    for k in ("AI_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "")  # env wins over a local secrets.toml, so no real API is called
    with pytest.raises(extract.ExtractionError):
        extract.extract_form(b"\xff\xd8fake")


def fake_extraction(_jpeg: bytes) -> extract.FormExtraction:
    tf = lambda o, m, e: extract.TextField(original=o, marathi=m, english=e, confident=True)  # noqa: E731
    return extract.FormExtraction(
        is_registration_form=True, source_language="marathi",
        name=tf("श्री.संदेश लक्ष्मण शिंदे", "श्री. संदेश लक्ष्मण शिंदे", "Shri. Sandesh Laxman Shinde"),
        mobile="8010706467", mobile_confident=True,
        vibhag=tf("कोन, ता. पनवेल.", "कोन, ता. पनवेल", "Kon, Tal. Panvel"),
        address=tf("मु.पो.गुळसुंदे", "मु.पो. गुळसुंदे", "At Post Gulsunde"),
        photo_present=True, photo_box=extract.PhotoBox(x=0.65, y=0.26, w=0.18, h=0.11), notes="")


# ---------------------------------------------------------------- photo crop on real sample forms
@pytest.mark.skipif(not SAMPLES, reason="put sample forms in ./samples to run")
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name[:25])
def test_photo_found_top_right(path):
    for page in photo.upload_to_pages(path.read_bytes(), path.name):
        img, box, method = photo.extract_photo(page)
        assert method == "auto" and img is not None
        assert 0.55 < box.x < 0.75 and 0.2 < box.y < 0.32      # top-right area of these forms
        assert 0.08 < box.h < 0.17 and 0.1 < box.w < 0.22


# ---------------------------------------------------------------- pipeline
def _form_png() -> tuple[bytes, str]:
    if SAMPLES:
        return SAMPLES[0].read_bytes(), SAMPLES[0].name
    img = Image.new("RGB", (800, 1100), "white")
    img.paste(Image.new("RGB", (130, 160), "#cc3333"), (520, 280))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "form.png"


def test_upload_creates_draft_and_skips_duplicates(db, event):
    data, name = _form_png()
    jobs = pipeline.jobs_from_upload(data, name)
    r = pipeline.process_page(db, event, "attendee", jobs[0], "admin@x", extractor=fake_extraction)
    assert r.status == "ok"
    p = repo.get_person(db, r.person_id)
    assert p["status"] == "draft" and p["name_en"] == "Shri. Sandesh Laxman Shinde" and p["photo_id"]
    again = pipeline.process_page(db, event, "attendee", pipeline.jobs_from_upload(data, name)[0], "admin@x",
                                  extractor=fake_extraction)
    assert again.status == "duplicate"


def test_ai_failure_still_creates_draft(db, event):
    def broken(_):
        raise extract.ExtractionError("boom")

    data, name = _form_png()
    r = pipeline.process_page(db, event, "volunteer", pipeline.jobs_from_upload(data, name)[0], "a", extractor=broken)
    assert r.status == "error" and repo.get_person(db, r.person_id)["status"] == "draft"


# ---------------------------------------------------------------- cards & exports
def test_cards_and_print_pdf(event):
    person = {"event_id": event["_id"], "pid": "TH26-A-0001", "type": "attendee",
              "name_mr": "श्री. संदेश लक्ष्मण शिंदे", "name_en": "Shri. Sandesh Laxman Shinde", "vibhag_mr": "कोन"}
    card = cards.render_card(person, event, None, design="ubg")
    assert card.size == cards.CARD_PX
    tpl = Image.new("RGB", (1011, 638), "#eeeeee")  # landscape template
    tcard = cards.render_card(person, event, None, tpl, design="upload")
    assert tcard.size == (1011, 638)
    import pymupdf

    pdf = pymupdf.open(stream=cards.print_pdf([card] * 10), filetype="pdf")
    assert pdf.page_count == 2  # 9 portrait cards per A4 page
    # the QR on the card must be scannable
    from core.scanner import decode_qr_image

    assert decode_qr_image(cards.card_png(card)) == qr.make_payload(event["_id"], "TH26-A-0001")


def test_marathi_text_is_shaped():
    # 'श्री' with correct shaping is narrower than its unshaped letters श + ् + र + ी laid side by side
    shaped = cards.render_text("श्री", 60)
    parts = sum(cards.render_text(ch, 60).width for ch in "श्री")
    assert shaped.width < parts


def test_excel_export(db, event):
    pid = repo.approve_person(db, _draft(db, event), event, "x")
    repo.mark_attendance(db, event["_id"], repo.get_person_by_pid(db, event["_id"], pid), "v@x")
    wb = load_workbook(io.BytesIO(exports.excel_bytes(event, repo.list_people(db, event["_id"], "verified"),
                                                      repo.list_attendance(db, event["_id"]))))
    ws = wb["Attendance"]
    header = [c.value for c in ws[4]]
    row = [c.value for c in ws[5]]
    assert header[0] == "ID" and row[0] == pid and row[2] == "श्री. संदेश शिंदे" and row[exports.STATUS_COL] == "Present"
    assert exports.photo_filename({"pid": pid, "name_en": "Shri. Sandesh Shinde"}) == f"{pid}_Sandesh-Shinde.jpg"
