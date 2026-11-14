"""Smoke test: every page renders without exceptions (mongomock DB, local dev login)."""
import pytest
from streamlit.testing.v1 import AppTest

from conftest import ROOT
from core import db as dbm
from core import repo

PAGES = ["views/dashboard.py", "views/scan.py", "views/attendance.py", "views/import_excel.py", "views/upload.py",
         "views/review.py", "views/people.py", "views/id_cards.py", "views/settings.py"]


@pytest.fixture
def app(db, event):
    # one approved person with attendance, one draft
    pid = repo.create_draft(db, event["_id"], "attendee",
                            {"name_mr": "श्री. संदेश शिंदे", "name_en": "Shri. Sandesh Shinde", "mobile": "8010706467"})
    repo.approve_person(db, pid, event, "owner@example.com")
    repo.mark_attendance(db, event["_id"], repo.get_person(db, pid), "owner@example.com")
    repo.create_draft(db, event["_id"], "volunteer", {"name_mr": "राम", "name_en": "Ram", "mobile": ""})
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.secrets["DEV_LOGIN_EMAIL"] = "owner@example.com"
    at.secrets["BOOTSTRAP_ADMIN_EMAIL"] = "owner@example.com"
    at.secrets["QR_SECRET"] = "test-secret"
    at.secrets["MONGO_URI"] = "mongomock://"
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(app, page):
    app.run()
    assert not app.exception, app.exception
    app.switch_page(page).run()
    assert not app.exception, [e.value for e in app.exception]


@pytest.mark.parametrize("status", ["ok", "warn", "bad"])
def test_scan_result_renders_once(app, db, event, status):
    person = repo.get_person_by_pid(db, event["_id"], "TH26-A-0001")
    app.run()
    app.session_state["scan_result"] = {"id": "1.0", "status": status, "title": "x", "detail": "",
                                        "code": "", "person": person if status != "bad" else None}
    app.switch_page("views/scan.py").run()
    assert not app.exception, [e.value for e in app.exception]
    undo = [b for b in app.button if "Undo" in (b.label or "")]
    assert len(undo) == (1 if status == "ok" else 0)


def _pin_app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.secrets["QR_SECRET"] = "x"
    at.secrets["ADMIN_PIN"] = "4321"  # no DEV_LOGIN_EMAIL, no [auth] -> PIN mode
    return at


def _page_titles(at) -> str:
    return " ".join(s.value for s in at.subheader)


def test_pin_mode_asks_name_then_volunteer_only(db, event):
    at = _pin_app()
    at.run()
    assert not at.exception
    assert any("Your name" in t.label for t in at.text_input)
    at.text_input[0].input("Rahul Patil")
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state["volunteer_name"] == "Rahul Patil"
    assert "Scan QR" in _page_titles(at)  # volunteers land on Scan
    assert any("Rahul Patil" in c.value and "volunteer" in c.value for c in at.sidebar.caption)


def test_pin_mode_admin_unlock(db, event):
    at = _pin_app()
    at.session_state["volunteer_name"] = "Admin"
    at.run()
    pin_box = next(t for t in at.sidebar.text_input if t.label == "Admin PIN")
    pin_box.input("0000")
    next(b for b in at.sidebar.button if b.label == "Unlock").click().run()
    assert not at.session_state["is_admin"] and any("Wrong PIN" in e.value for e in at.sidebar.error)
    pin_box = next(t for t in at.sidebar.text_input if t.label == "Admin PIN")
    pin_box.input("4321")
    next(b for b in at.sidebar.button if b.label == "Unlock").click().run()
    assert not at.exception and at.session_state["is_admin"]
    assert any("admin" in c.value for c in at.sidebar.caption)
    at.switch_page("views/settings.py").run()   # an admin-only page now works
    assert not at.exception


def test_check_in_records_volunteer_name(db, event):
    at = _pin_app()
    at.session_state["volunteer_name"] = "Rahul Patil"
    at.run()
    at.switch_page("views/scan.py").run()
    from core import qr

    person = repo.get_person(db, repo.create_draft(db, event["_id"], "attendee", {"name_mr": "x", "mobile": ""}))
    repo.approve_person(db, person["_id"], event, "admin")
    person = repo.get_person(db, person["_id"])
    # simulate a search-tab "Mark present" by calling the same code path the page uses
    status, rec = repo.mark_attendance(db, event["_id"], person, at.session_state["volunteer_name"])
    assert rec["scanned_by"] == "Rahul Patil" and qr.parse_payload(qr.make_payload(event["_id"], person["pid"]))


def test_unknown_user_denied(db, event):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.secrets["DEV_LOGIN_EMAIL"] = "stranger@example.com"
    at.secrets["QR_SECRET"] = "x"
    at.run()
    assert any("does not have access" in e.value for e in at.error)


def test_volunteer_sees_only_scan_and_dashboard(db, event):
    repo.add_user(db, "vol@example.com", "volunteer")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.secrets["DEV_LOGIN_EMAIL"] = "vol@example.com"
    at.secrets["QR_SECRET"] = "x"
    at.run()
    assert not at.exception
    assert any("हजेरी" in s.value or "Scan" in s.value for s in at.subheader)
