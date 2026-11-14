import io

import pytest
from openpyxl import Workbook
from PIL import Image

from conftest import ROOT
from core import cards, importer, pipeline, qr, repo
from core.scanner import decode_qr_image
from test_core import fake_extraction, _form_png

REAL_XLSX = sorted((ROOT / "samples").glob("*.xlsx")) if (ROOT / "samples").exists() else []


def _workbook() -> bytes:
    """Mimics the Kon directory: title rows, English sheet, Marathi sheet in a DIFFERENT order,
    and a husband & wife sharing one phone."""
    wb = Workbook()
    en = wb.active
    en.title = "Master"
    head = ["Sr", "Name", "Attendance No.", "Age", "Phone No.", "Business", "Business Category", "Baithak / Group"]
    en.append(["|| श्री ||"])
    en.append(["विभाग- कोन , तालुका. पनवेल (वर्ष 2026)"])
    en.append([])
    en.append(head)
    rows = [
        [1, "Mr. Akshay Anil Manve", 64, 29, 7387979512, "Bike Spare Parts", "Retailer", "Apta - Men (Thu)"],
        [2, "Mr. Kaluram Jethoo Patil", 16, 50, 7745083593, "Farming", "Trader", "Kon"],
        [3, "Mrs. Ashwini Kaluram Patil", 16, 45, 7745083593, "Tailoring", "Service", "Kon"],
        [4, "Mr. Vivek Kore", None, 22, 9876543210, "Laundry", "Service", "Shivnagar"],
    ]
    for r in rows:
        en.append(r)
    mr = wb.create_sheet("Master (Marathi Names)")
    mr.append(head)
    for r in [rows[3], rows[2], rows[0], rows[1]]:  # shuffled on purpose
        names = {"Mr. Akshay Anil Manve": "श्री. अक्षय अनिल मानवे", "Mr. Kaluram Jethoo Patil": "श्री. काळूराम जेठू पाटील",
                 "Mrs. Ashwini Kaluram Patil": "सौ. अश्विनी काळूराम पाटील", "Mr. Vivek Kore": "कु. विवेक कोरे"}
        mr.append([r[0], names[r[1]], *r[2:]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _records(data: bytes):
    table = importer.read_table(data, "Master")
    lookup = importer.marathi_lookup(importer.read_table(data, "Master (Marathi Names)"))
    return table, importer.build_records(table, importer.guess_mapping(table.headers), lookup)


def test_mapping_and_marathi_matched_by_phone_not_row():
    table, recs = _records(_workbook())
    assert table.header_row == 4
    m = importer.guess_mapping(table.headers)
    assert m["name_en"] == 1 and m["mobile"] == 4 and m["business"] == 5 and m["category"] == 6 and m["group"] == 7
    by_en = {r.fields["name_en"]: r.fields for r in recs}
    assert by_en["Mr. Vivek Kore"]["name_mr"] == "कु. विवेक कोरे"
    assert by_en["Mr. Kaluram Jethoo Patil"]["name_mr"] == "श्री. काळूराम जेठू पाटील"     # shared phone,
    assert by_en["Mrs. Ashwini Kaluram Patil"]["name_mr"] == "सौ. अश्विनी काळूराम पाटील"  # split by श्री./सौ.
    assert by_en["Mr. Akshay Anil Manve"]["mobile"] == "7387979512"
    assert importer.guess_vibhag(table.title_lines) == "कोन, तालुका. पनवेल"


def test_import_creates_ids_then_reimport_updates(db, event):
    _, recs = _records(_workbook())
    s1 = importer.import_records(db, event, recs, "attendee", "कोन", "Kon", "admin")
    assert len(s1.created) == 4 and not s1.updated
    assert s1.created[0] == "TH26-A-0001"
    _, recs2 = _records(_workbook())
    s2 = importer.import_records(db, event, recs2, "attendee", "कोन", "Kon", "admin")
    assert not s2.created and len(s2.updated) == 4
    p = repo.get_person_by_pid(db, event["_id"], "TH26-A-0001")
    assert p["status"] == "verified" and p["business"] == "Bike Spare Parts" and p["vibhag_mr"] == "कोन"
    assert p["att_no"] == "64" and p["group"] == "Apta - Men (Thu)"


def test_form_photo_attaches_to_imported_member(db, event):
    # member from Excel, no photo; the form's mobile + name match it
    pid = repo.create_draft(db, event["_id"], "attendee", {"name_mr": "श्री. संदेश लक्ष्मण शिंदे",
                            "name_en": "Mr. Sandesh Laxman Shinde", "mobile": "8010706467", "business": "Shop"})
    code = repo.approve_person(db, pid, event, "admin")
    data, name = _form_png()
    r = pipeline.process_page(db, event, "attendee", pipeline.jobs_from_upload(data, name)[0], "admin",
                              extractor=fake_extraction)
    assert r.status == "matched"
    member = repo.get_person_by_pid(db, event["_id"], code)
    assert member["photo_id"] and member["form_file_id"]
    assert member["address_mr"]  # filled from the form because it was empty
    assert member["name_en"] == "Mr. Sandesh Laxman Shinde"  # Excel name kept
    assert db.people.count_documents({"status": "draft"}) == 0


def test_form_with_different_name_goes_to_review_with_suggestion(db, event):
    pid = repo.create_draft(db, event["_id"], "attendee", {"name_mr": "सौ. सुनंदा शेवाळे", "name_en": "Mrs. Sunanda Shewale",
                                                           "mobile": "8010706467"})
    repo.approve_person(db, pid, event, "admin")
    data, name = _form_png()
    r = pipeline.process_page(db, event, "attendee", pipeline.jobs_from_upload(data, name)[0], "admin",
                              extractor=fake_extraction)
    assert r.status == "ok"
    draft = repo.get_person(db, r.person_id)
    assert draft["extraction"]["suggest_member"] == str(pid)
    # reviewer decides it IS that member
    member = repo.get_person(db, pid)
    photo_id = draft["photo_id"]
    repo.merge_draft_into(db, draft, member)
    member = repo.get_person(db, pid)
    assert member["photo_id"] == photo_id and repo.get_person(db, r.person_id) is None
    from core import db as dbm

    assert dbm.get_file(photo_id, db)  # the photo file survived the draft's deletion


def test_business_camp_badge_design(event):
    ev = {**event, "card_design": "business_camp_badge"}
    person = {"event_id": ev["_id"], "pid": "TH26-A-0042", "type": "attendee",
              "name_mr": "श्री. जनार्दन रघुनाथ काळेकर", "business": "Balloons and Flowers Decoration"}
    photo = Image.new("RGB", (300, 360), "#3366aa")
    card = cards.render_card(person, ev, photo)
    assert card.size == (1294, 2000)
    assert decode_qr_image(cards.card_png(card)) == qr.make_payload(ev["_id"], "TH26-A-0042")
    # round photo: centre of the circle is the photo colour, the box corner is still the template
    assert card.getpixel((658, 827)) == (51, 102, 170)
    assert card.getpixel((455, 625)) != (51, 102, 170)
    # without a photo the template's own silhouette stays
    blank = cards.render_card({**person, "photo_id": None}, ev, None)
    assert blank.getpixel((658, 780)) == cards.builtin_template_image("business_camp_badge").getpixel((658, 780))
    import pymupdf

    pdf = pymupdf.open(stream=cards.card_pdf(card, 54), filetype="pdf")
    w_mm = pdf[0].rect.width / 72 * 25.4
    assert pdf.page_count == 1 and abs(w_mm - 54) < 0.1


@pytest.mark.skipif(not REAL_XLSX, reason="put the members Excel in ./samples to run")
def test_real_kon_directory():
    data = REAL_XLSX[0].read_bytes()
    table = importer.read_table(data, importer.sheet_names(data)[0])
    sheets = importer.sheet_names(data)
    mr_sheet = next(s for s in sheets if "marathi" in s.lower())
    recs = importer.build_records(table, importer.guess_mapping(table.headers),
                                  importer.marathi_lookup(importer.read_table(data, mr_sheet)))
    assert len(recs) == 219
    assert all(r.fields.get("name_mr") for r in recs)
    by_en = {r.fields["name_en"]: r.fields["name_mr"] for r in recs}
    assert by_en["Mr. Vivek Kore"] == "कु. विवेक कोरे"             # row-shifted area
    assert by_en["Mrs. Ashwini Kaluram Patil"].startswith("सौ.")    # shared phone
    assert by_en["Mr. Kaluram Jethoo Patil"].startswith("श्री.")


# ---------------------------------------------------------------- duplicate protection
def _member(db, event, name_mr, name_en, mobile, photo=False):
    from core import db as dbm

    pid = repo.create_draft(db, event["_id"], "attendee", {"name_mr": name_mr, "name_en": name_en, "mobile": mobile})
    if photo:
        db.people.update_one({"_id": pid}, {"$set": {"photo_id": dbm.put_file(b"x", "p.jpg", "image/jpeg", db)}})
    repo.approve_person(db, pid, event, "admin")
    return repo.get_person(db, pid)


def test_find_duplicates_kinds(db, event):
    _member(db, event, "श्री. काळूराम जेठू पाटील", "Mr. Kaluram Jethoo Patil", "7745083593")
    _member(db, event, "श्री. सुनील खंडू मात्रे", "Mr. Sunil Khandu Matre", "9702397000")
    # same person, written differently (as Gemini read it)
    d = repo.find_duplicates(db, event["_id"], {"name_mr": "श्री सुनील खंडू मात्रे", "name_en": "Shri Sunil Khandu Mhatre",
                                                "mobile": "9702397000"})
    assert [x["kind"] for x in d] == ["same"]
    # wife on the same phone: family, NOT the same person
    d = repo.find_duplicates(db, event["_id"], {"name_mr": "सौ. अश्विनी काळूराम पाटील",
                                                "name_en": "Mrs. Ashwini Kaluram Patil", "mobile": "7745083593"})
    assert [x["kind"] for x in d] == ["same_mobile"]
    # same name, new phone number
    d = repo.find_duplicates(db, event["_id"], {"name_mr": "श्री. सुनील खंडू मात्रे", "name_en": "", "mobile": "9999999999"})
    assert [x["kind"] for x in d] == ["similar_name"]
    # a different person with a merely similar name (0.81 in the real list) is not flagged
    _member(db, event, "सौ. मनीषा मोहन पाटील", "", "9000000001")
    assert repo.find_duplicates(db, event["_id"], {"name_mr": "सौ. मीनल मोहन पाटील", "mobile": "9000000002"}) == []


def test_wife_form_never_attaches_to_husband(db, event):
    _member(db, event, "श्री. काळूराम जेठू पाटील", "Mr. Kaluram Jethoo Patil", "7745083593")

    def wife(_):
        x = fake_extraction(b"")
        x.name.marathi, x.name.english, x.mobile = "सौ. अश्विनी काळूराम पाटील", "Mrs. Ashwini Kaluram Patil", "7745083593"
        return x

    data, name = _form_png()
    r = pipeline.process_page(db, event, "attendee", pipeline.jobs_from_upload(data, name)[0], "admin", extractor=wife)
    assert r.status == "ok"                      # new draft for review, not "matched"
    husband = repo.get_person_by_pid(db, event["_id"], "TH26-A-0001")
    assert not husband.get("photo_id")


def test_review_blocks_approving_a_duplicate(db, event):
    from streamlit.testing.v1 import AppTest

    _member(db, event, "श्री. संदेश लक्ष्मण शिंदे", "Mr. Sandesh Laxman Shinde", "8010706467", photo=True)
    repo.create_draft(db, event["_id"], "attendee", {"name_mr": "श्री.संदेश लक्ष्मण शिंदे",
                                                     "name_en": "Shri. Sandesh Laxman Shinde", "mobile": "8010706467"})
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.secrets["DEV_LOGIN_EMAIL"] = "owner@example.com"
    at.secrets["QR_SECRET"] = "x"
    at.run()
    at.switch_page("views/review.py").run()
    assert not at.exception
    assert any("Possible duplicate" in e.value for e in at.error)
    approve = next(b for b in at.button if "Approve" in b.label)
    assert approve.disabled
    next(cb for cb in at.checkbox if "different person" in cb.label).check().run()
    approve = next(b for b in at.button if "Approve" in b.label)
    assert not approve.disabled


# ---------------------------------------------------------------- several events side by side
def _second_event(db):
    repo.save_event(db, {"_id": "test-event", "name_en": "Practice", "id_prefix": "TEST", "date": "2026-11-20"})
    return repo.get_event(db, "test-event")


def test_events_are_separate_and_cards_do_not_cross(db, event):
    test_ev = _second_event(db)
    a = _member(db, event, "श्री. अ", "Mr. A", "9000000001")
    b = _member(db, test_ev, "श्री. अ", "Mr. A", "9000000001")       # same person, other event
    assert a["pid"] == "TH26-A-0001" and b["pid"] == "TEST-A-0001"    # each event numbers from 0001
    assert repo.find_duplicates(db, test_ev["_id"], a) and not repo.find_duplicates(db, "nope", a)
    # a card from one event is recognised as belonging to that event only
    assert qr.parse_payload(qr.make_payload(test_ev["_id"], b["pid"])) == ("test-event", "TEST-A-0001")
    assert repo.prefix_in_use(db, "TH26", except_event="test-event")["_id"] == "thane-2026"
    assert repo.prefix_in_use(db, "TH26", except_event="thane-2026") is None


def test_clear_checkins_and_delete_event_touch_only_that_event(db, event):
    from core import db as dbm

    test_ev = _second_event(db)
    real = _member(db, event, "श्री. खरा", "Mr. Real", "9000000001", photo=True)
    fake = _member(db, test_ev, "श्री. सराव", "Mr. Practice", "9000000002", photo=True)
    repo.mark_attendance(db, event["_id"], real, "v")
    repo.mark_attendance(db, test_ev["_id"], fake, "v")

    assert repo.clear_attendance(db, test_ev["_id"]) == 1
    assert db.attendance.count_documents({"event_id": event["_id"]}) == 1          # real event untouched
    assert repo.get_person(db, fake["_id"])["pid"] == "TEST-A-0001"                 # members kept

    res = repo.delete_event(db, test_ev["_id"])
    assert res["people"] == 1 and repo.get_event(db, "test-event") is None
    assert dbm.get_file(fake["photo_id"], db) is None                               # its photo removed
    assert dbm.get_file(real["photo_id"], db) is not None                           # other event's photo kept
    assert repo.get_person(db, real["_id"]) and db.attendance.count_documents({}) == 1
    assert not db.counters.find_one({"_id": {"$regex": "^test-event:"}})


# ---------------------------------------------------------------- downloads for the printing agency
def test_card_downloads_for_printing_agency(db, event):
    import zipfile

    import pymupdf

    from core import card_exports

    people = [_member(db, event, f"श्री. सदस्य {i}", f"Mr. Member {i}", f"90000000{i:02d}", photo=(i == 1))
              for i in range(1, 4)]
    ev = repo.get_event(db, event["_id"])  # Business Camp badge (1294x2000)
    pdf = pymupdf.open(stream=card_exports.one_per_page_pdf(db, ev, people, 54), filetype="pdf")
    assert pdf.page_count == 3
    w_mm, h_mm = (round(pdf[0].rect.width / 72 * 25.4, 1), round(pdf[0].rect.height / 72 * 25.4, 1))
    assert (w_mm, h_mm) == (54.0, 83.5)                       # one card, true size, design proportions
    z = zipfile.ZipFile(io.BytesIO(card_exports.png_zip(db, ev, people, 54)))
    names = sorted(z.namelist())
    assert names == ["TH26-A-0001_Member-1.png", "TH26-A-0002_Member-2.png", "TH26-A-0003_Member-3.png"]
    png = Image.open(io.BytesIO(z.read(names[0])))
    assert png.size == (1294, 2000) and round(png.info["dpi"][0]) == 609   # 1294 px over 54 mm
    a4 = pymupdf.open(stream=card_exports.a4_pdf(db, ev, people, 54), filetype="pdf")
    assert a4.page_count == 1 and round(a4[0].rect.width / 72 * 25.4) == 210
    assert len(zipfile.ZipFile(io.BytesIO(card_exports.qr_zip(ev, people))).namelist()) == 3
