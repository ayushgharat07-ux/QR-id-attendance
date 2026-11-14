"""Google Sheets: per-event "Connect Google" OAuth (drive.file scope) and sync."""
from __future__ import annotations

import os
import threading
from datetime import timedelta

from pymongo.database import Database

from core import exports, repo
from core.config import secret, secret_section

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
ATTENDANCE_TAB = "Attendance"
LOG_TAB = "Check-ins"
LOG_HEADER = ["Time (IST)", "ID", "Type", "नाव", "Name", "विभाग", "Marked by"]


def configured() -> bool:
    c = secret_section("google_sheets")
    return bool(c.get("client_id") and c.get("client_secret") and secret("APP_URL"))


def _redirect_uri() -> str:
    return str(secret("APP_URL")).rstrip("/") + "/"


def _flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    app_url = str(secret("APP_URL"))
    if app_url.startswith("http://localhost") or app_url.startswith("http://127.0.0.1"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    c = secret_section("google_sheets")
    config = {"web": {
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": TOKEN_URI,
        "redirect_uris": [_redirect_uri()],
    }}
    return Flow.from_client_config(config, scopes=SCOPES, state=state, redirect_uri=_redirect_uri())


def start_connect(db: Database, event_id: str, email: str) -> str:
    """Return the Google consent URL. State + PKCE verifier are kept in Mongo because the
    browser comes back in a brand-new Streamlit session."""
    flow = _flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent",
                                        include_granted_scopes="true", login_hint=email)
    db.oauth_states.insert_one({"_id": state, "event_id": event_id, "email": email,
                                "code_verifier": flow.code_verifier, "created_at": repo.now()})
    return url


def finish_connect(db: Database, state: str, code: str) -> str:
    """Exchange the code, create the spreadsheet, store tokens. Returns the event id."""
    pending = db.oauth_states.find_one_and_delete({"_id": state})
    if not pending:
        raise ValueError("This Google connection link has expired. Please click Connect again.")
    created = pending["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=repo.now().tzinfo)
    if repo.now() - created > timedelta(minutes=30):
        raise ValueError("This Google connection link has expired. Please click Connect again.")
    flow = _flow(state)
    flow.code_verifier = pending["code_verifier"]
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise ValueError("Google did not return a refresh token. Remove the app's access in your "
                         "Google account settings and connect again.")
    event = repo.get_event(db, pending["event_id"])
    db.events.update_one({"_id": event["_id"]}, {"$set": {"gsheet": {
        "refresh_token": creds.refresh_token, "owner_email": pending["email"],
        "connected_at": repo.now(), "spreadsheet_id": None, "url": None}}})
    event = repo.get_event(db, event["_id"])
    ensure_spreadsheet(db, event)
    full_sync(db, repo.get_event(db, event["_id"]))
    return event["_id"]


def disconnect(db: Database, event_id: str) -> None:
    db.events.update_one({"_id": event_id}, {"$unset": {"gsheet": ""}})


def _gc(event: dict):
    import gspread
    from google.oauth2.credentials import Credentials

    c = secret_section("google_sheets")
    creds = Credentials(token=None, refresh_token=event["gsheet"]["refresh_token"], token_uri=TOKEN_URI,
                        client_id=c["client_id"], client_secret=c["client_secret"], scopes=SCOPES)
    return gspread.authorize(creds)


def is_connected(event: dict) -> bool:
    return bool((event.get("gsheet") or {}).get("refresh_token"))


def ensure_spreadsheet(db: Database, event: dict):
    gc = _gc(event)
    sid = event["gsheet"].get("spreadsheet_id")
    if sid:
        return gc.open_by_key(sid)
    title = f"{event.get('name_en') or event['_id']} {event.get('subtitle_en', '')} — Attendance".strip()
    sh = gc.create(title)
    sh.sheet1.update_title(ATTENDANCE_TAB)
    sh.add_worksheet(LOG_TAB, rows=2000, cols=len(LOG_HEADER))
    db.events.update_one({"_id": event["_id"]}, {"$set": {"gsheet.spreadsheet_id": sh.id, "gsheet.url": sh.url}})
    return sh


def _tab(sh, title: str, cols: int):
    import gspread

    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title, rows=2000, cols=cols)


def log_row(rec: dict) -> list[str]:
    return [exports.fmt_time(rec.get("scanned_at")), rec.get("pid", ""), rec.get("type", ""),
            rec.get("name_mr", ""), rec.get("name_en", ""), rec.get("vibhag_mr", ""), rec.get("scanned_by", "")]


def full_sync(db: Database, event: dict) -> str:
    sh = ensure_spreadsheet(db, event)
    people = repo.list_people(db, event["_id"], status="verified")
    attendance = repo.list_attendance(db, event["_id"])
    ws = _tab(sh, ATTENDANCE_TAB, len(exports.COLUMNS))
    ws.clear()
    ws.update(values=[[c for c, _ in exports.COLUMNS]] + exports.rows(people, attendance),
              range_name="A1", value_input_option="RAW")
    log = _tab(sh, LOG_TAB, len(LOG_HEADER))
    log.clear()
    log.update(values=[LOG_HEADER] + [log_row(a) for a in reversed(attendance)],
               range_name="A1", value_input_option="RAW")
    db.events.update_one({"_id": event["_id"]}, {"$set": {"gsheet.last_sync": repo.now()}})
    return sh.url


def append_checkin_async(event: dict, rec: dict) -> None:
    """Append one check-in to the sheet without slowing down scanning. Failures are ignored;
    'Sync now' rewrites everything from the database anyway."""
    if not is_connected(event) or not event["gsheet"].get("spreadsheet_id"):
        return

    def work():
        try:
            sh = _gc(event).open_by_key(event["gsheet"]["spreadsheet_id"])
            _tab(sh, LOG_TAB, len(LOG_HEADER)).append_row(log_row(rec), value_input_option="RAW")
        except Exception:
            pass

    threading.Thread(target=work, daemon=True).start()
