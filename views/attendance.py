import streamlit as st

from core import exports, gsheets, repo, ui
from core.ctx import ctx, require_admin

c = ctx()
require_admin(c)
st.subheader("📥 Attendance & export · हजेरी अहवाल")

people = repo.list_people(c.db, c.event["_id"], status="verified")
attendance = repo.list_attendance(c.db, c.event["_id"])
present = len({a["pid"] for a in attendance})
m = st.columns(3)
m[0].metric("Registered", len(people))
m[1].metric("Present", present)
m[2].metric("Absent", len(people) - present)

# ---------------------------------------------------------------- downloads
st.markdown("#### Downloads")
d1, d2 = st.columns(2)
d1.download_button("⬇️ Excel (.xlsx)", exports.excel_bytes(c.event, people, attendance),
                   f"{c.event['_id']}_attendance.xlsx",
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   type="primary", use_container_width=True)
if d2.button("📦 Prepare photos ZIP", use_container_width=True):
    files = [(exports.photo_filename(p), ui.file_bytes(str(p["photo_id"]))) for p in people if p.get("photo_id")]
    st.session_state["photos_zip"] = exports.zip_bytes([(n, b) for n, b in files if b])
if "photos_zip" in st.session_state:
    d2.download_button("⬇️ Photos ZIP (ID_Name.jpg)", st.session_state["photos_zip"],
                       f"{c.event['_id']}_photos.zip", "application/zip", use_container_width=True)

# ---------------------------------------------------------------- google sheets
st.markdown("#### Google Sheets")
if not gsheets.configured():
    st.info("Google Sheets is not set up yet (needs [google_sheets] and APP_URL in secrets). "
            "Excel download works without it.")
elif gsheets.is_connected(c.event):
    g = c.event["gsheet"]
    st.success(f"Connected to **{g.get('owner_email', '')}**'s Google Drive. "
               "Every scan is added to the 'Check-ins' tab automatically.")
    s1, s2, s3 = st.columns(3)
    if g.get("url"):
        s1.link_button("📄 Open Google Sheet", g["url"], use_container_width=True)
    if s2.button("🔄 Sync now (rewrite all)", use_container_width=True):
        try:
            with st.spinner("Syncing…"):
                gsheets.full_sync(c.db, c.event)
            st.toast("Google Sheet updated")
        except Exception as e:
            st.error(f"Sync failed: {e}")
    if s3.button("Disconnect", use_container_width=True):
        gsheets.disconnect(c.db, c.event["_id"])
        st.rerun()
    if g.get("last_sync"):
        st.caption(f"Last full sync: {ui.fmt_time(g['last_sync'])}")
else:
    st.write("Connect a Google account; the app creates a spreadsheet in that account's Drive and keeps it updated.")
    if st.button("🔗 Connect Google Sheets"):
        st.session_state["gs_url"] = gsheets.start_connect(c.db, c.event["_id"], c.email)
    if url := st.session_state.get("gs_url"):
        st.link_button("Continue to Google sign-in →", url, type="primary")

# ---------------------------------------------------------------- manual corrections
st.markdown("#### Attendance list")
present_pids = {a["pid"]: a for a in attendance}
flt = st.radio("Show", ["all", "present", "absent"], horizontal=True, format_func=str.title)
rows = [p for p in people if flt == "all" or (flt == "present") == (p["pid"] in present_pids)]
st.dataframe(
    [{"ID": p["pid"], "नाव": p.get("name_mr", ""), "Name": p.get("name_en", ""), "Mobile": p.get("mobile", ""),
      "विभाग": p.get("vibhag_mr", ""),
      "Status": "✅ Present" if p["pid"] in present_pids else "—",
      "Time": ui.fmt_time(present_pids[p["pid"]]["scanned_at"]) if p["pid"] in present_pids else "",
      "Marked by": present_pids[p["pid"]].get("scanned_by", "") if p["pid"] in present_pids else ""}
     for p in rows],
    hide_index=True, use_container_width=True,
)
with st.expander("Correct a check-in by hand"):
    opts = {p["pid"]: f"{p['pid']} · {p.get('name_mr', '')}" for p in people}
    pid = st.selectbox("Person", list(opts), format_func=opts.get, index=None, placeholder="Choose…")
    if pid:
        if pid in present_pids:
            if st.button("Remove check-in"):
                repo.unmark_attendance(c.db, c.event["_id"], pid)
                st.rerun()
        elif st.button("Mark present"):
            status, rec = repo.mark_attendance(c.db, c.event["_id"], repo.get_person_by_pid(c.db, c.event["_id"], pid), c.email)
            gsheets.append_checkin_async(c.event, rec)
            st.rerun()
