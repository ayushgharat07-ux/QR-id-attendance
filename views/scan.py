import time

import streamlit as st

from core import gsheets, qr, repo, ui
from core.ctx import ctx
from core.scanner import decode_qr_image, qr_scanner

c = ctx()
st.subheader("📷 Scan QR · हजेरी नोंदवा")


def process(code: str) -> dict:
    res = {"id": f"{time.time():.3f}", "code": code, "person": None, "detail": ""}
    parsed = qr.parse_payload(code)
    if not parsed:
        return {**res, "status": "bad", "title": "Invalid QR ✕", "detail": "This QR is not an ID card from this app."}
    event_id, pid = parsed
    if event_id != c.event["_id"]:
        return {**res, "status": "bad", "title": "Different event ✕",
                "detail": f"This card belongs to event '{event_id}'. Switch event in the sidebar."}
    person = repo.get_person_by_pid(c.db, event_id, pid)
    if not person or person.get("status") != "verified":
        return {**res, "status": "bad", "title": "Not registered ✕", "detail": f"{pid} was not found."}
    status, rec = repo.mark_attendance(c.db, event_id, person, c.email)
    if status == "marked":
        gsheets.append_checkin_async(c.event, rec)
        return {**res, "status": "ok", "title": "Present ✓ हजर", "person": person,
                "detail": f"Marked at {ui.fmt_time(rec['scanned_at'])}"}
    return {**res, "status": "warn", "title": "Already marked !", "person": person,
            "detail": f"Checked in at {ui.fmt_time(rec['scanned_at'])} by {rec.get('scanned_by', '')}"}


def handle(code: str) -> None:
    st.session_state["scan_result"] = process(code)


def show_result() -> None:
    r = st.session_state.get("scan_result")
    if not r:
        return
    ui.result_card(r["status"], r["title"], r["person"], r["detail"])
    if r["status"] == "ok" and r["person"]:
        if st.button("↩️ Undo this check-in (wrong person)", key=f"undo_{r['id']}"):
            repo.unmark_attendance(c.db, c.event["_id"], r["person"]["pid"])
            st.session_state["scan_result"] = None
            st.toast("Check-in removed")
            st.rerun()


live, photo, search = st.tabs(["🎥 Live camera", "📸 Photo (backup)", "🔎 Search name / mobile"])

with live:
    r = st.session_state.get("scan_result")
    val = qr_scanner(result={"id": r["id"], "status": r["status"]} if r else None, key="live_scanner")
    if val and val.get("ts") != st.session_state.get("last_scan_ts"):
        st.session_state["last_scan_ts"] = val["ts"]
        handle(val["code"])
        st.rerun()

with photo:
    st.caption("If the live camera does not work on this phone, take a photo of the QR code instead.")
    shot = st.camera_input("Take a photo of the QR code", key="qr_photo")
    if shot is not None and st.session_state.get("last_photo_id") != shot.file_id:
        st.session_state["last_photo_id"] = shot.file_id
        code = decode_qr_image(shot.getvalue())
        if code:
            handle(code)
        else:
            st.session_state["scan_result"] = {"id": shot.file_id, "status": "bad", "title": "No QR found ✕",
                                               "person": None, "detail": "Hold the card closer and retry."}

with search:
    q = st.text_input("Name, mobile number or ID", placeholder="e.g. 8010706467 or संदेश or TH26-A-0001")
    for p in repo.search_people(c.db, c.event["_id"], q):
        col1, col2 = st.columns([4, 1])
        col1.markdown(f"**{p.get('name_mr', '')}** · {p.get('name_en', '')}  \n"
                      f"{p['pid']} · {p.get('mobile', '')} · {p.get('vibhag_mr', '')}")
        if col2.button("Mark present", key=f"mark_{p['_id']}"):
            handle(qr.make_payload(c.event["_id"], p["pid"]))
            st.rerun()

show_result()  # once, below the tabs, so it is visible whichever way the person was found
st.divider()
st.caption("My recent check-ins")
mine = [a for a in repo.list_attendance(c.db, c.event["_id"], limit=200) if a.get("scanned_by") == c.email][:10]
if mine:
    st.dataframe(
        [{"Time": ui.fmt_time(a["scanned_at"]), "ID": a["pid"], "नाव": a.get("name_mr", ""), "Name": a.get("name_en", "")}
         for a in mine],
        hide_index=True, use_container_width=True,
    )
else:
    st.write("No check-ins yet.")
