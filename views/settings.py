import io
import re
from datetime import date

import streamlit as st
from PIL import Image

from core import auth, branding, extract, gsheets, repo
from core import db as dbm
from core.config import secret, secret_section
from core.ctx import ctx, require_admin

c = ctx()
require_admin(c)
st.subheader("⚙️ Event settings · कार्यक्रम माहिती")

ev_tab, users_tab, sys_tab = st.tabs(["📅 Events", "👤 Users & volunteers", "🩺 System check"])

with ev_tab:
    mode = st.radio("", ["Edit current event", "Create a new event"], horizontal=True, label_visibility="collapsed")
    creating = mode.startswith("Create")
    ev = {} if creating else c.event
    has_ids = bool(not creating and c.db.people.count_documents({"event_id": ev["_id"], "pid": {"$ne": None}}))
    with st.form("event_form"):
        a, b = st.columns(2)
        name_mr = a.text_input("Event name (Marathi) · कार्यक्रमाचे नाव", ev.get("name_mr", ""))
        name_en = b.text_input("Event name (English)", ev.get("name_en", ""))
        subtitle_mr = a.text_input("Second line (Marathi), e.g. ठाणे २०२६", ev.get("subtitle_mr", ""))
        subtitle_en = b.text_input("Second line (English), e.g. Thane 2026", ev.get("subtitle_en", ""))
        d = ev.get("date")
        dt = a.date_input("Date", date.fromisoformat(d) if d else date.today(), format="DD/MM/YYYY")
        time_mr = b.text_input("Time (Marathi), e.g. सकाळी ७ ते २", ev.get("time_mr", ""))
        venue_mr = a.text_input("Venue (Marathi) · स्थळ", ev.get("venue_mr", ""))
        venue_en = b.text_input("Venue (English)", ev.get("venue_en", ""))
        time_en = b.text_input("Time (English)", ev.get("time_en", ""))
        prefix = a.text_input("ID prefix (letters/digits, e.g. TH26)", ev.get("id_prefix", ""), max_chars=8,
                              disabled=has_ids, help="Cannot change after IDs have been issued.")
        slug = a.text_input("Short code (lowercase, e.g. pune-2027)", "", disabled=not creating) if creating else ev["_id"]
        active = b.checkbox("Default event when people open the app", ev.get("active", True))
        if st.form_submit_button("💾 Save event", type="primary"):
            prefix = (prefix or "").strip().upper()
            errs = []
            if not name_mr and not name_en:
                errs.append("Give the event a name.")
            if not re.fullmatch(r"[A-Z0-9]{2,8}", prefix):
                errs.append("ID prefix must be 2-8 letters/digits.")
            elif not has_ids and (other := repo.prefix_in_use(c.db, prefix, None if creating else ev["_id"])):
                errs.append(f"ID prefix {prefix} is already used by event '{other['_id']}'. "
                            "Each event needs its own prefix so printed IDs never look the same.")
            if creating:
                slug = slug.strip().lower()
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", slug):
                    errs.append("Short code: lowercase letters, digits and dashes.")
                elif repo.get_event(c.db, slug):
                    errs.append("That short code is already used.")
            if errs:
                st.error(" ".join(errs))
            else:
                if active:
                    c.db.events.update_many({}, {"$set": {"active": False}})
                repo.save_event(c.db, {"_id": slug, "name_mr": name_mr, "name_en": name_en,
                                       "subtitle_mr": subtitle_mr, "subtitle_en": subtitle_en,
                                       "date": dt.isoformat(), "time_mr": time_mr, "time_en": time_en,
                                       "venue_mr": venue_mr, "venue_en": venue_en,
                                       "id_prefix": prefix if not has_ids else ev["id_prefix"], "active": active})
                st.session_state["event_id"] = slug
                st.toast("Event saved")
                st.rerun()

    if not creating:
        st.markdown("**Organiser's logo** (optional) — shown in the app header and on standard-design cards.")
        lc1, lc2 = st.columns([1, 3])
        if ev.get("logo_id") and (logo_uri := branding.event_logo_uri(ev)):
            lc1.image(logo_uri, width=110)
        logo_up = lc2.file_uploader("Logo (PNG/JPG)", type=["png", "jpg", "jpeg"], key="event_logo_up",
                                    label_visibility="collapsed")
        b1, b2 = lc2.columns(2)
        if logo_up and b1.button("Use this logo", type="primary"):
            img = Image.open(logo_up).convert("RGBA")
            img.thumbnail((600, 600), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            new_id = dbm.put_file(buf.getvalue(), "logo.png", "image/png", c.db, event_id=ev["_id"], kind="logo")
            c.db.events.update_one({"_id": ev["_id"]}, {"$set": {"logo_id": new_id}})
            dbm.delete_file(ev.get("logo_id"), c.db)
            st.rerun()
        if ev.get("logo_id") and b2.button("Remove logo"):
            c.db.events.update_one({"_id": ev["_id"]}, {"$unset": {"logo_id": ""}})
            dbm.delete_file(ev.get("logo_id"), c.db)
            st.rerun()

        st.divider()
        n_people = c.db.people.count_documents({"event_id": ev["_id"]})
        n_att = c.db.attendance.count_documents({"event_id": ev["_id"]})
        with st.expander(f"🧹 Clean up this event ({n_people} people, {n_att} check-ins)"):
            st.markdown("**Clear all check-ins** — e.g. after a volunteer practice run. Members, photos, IDs and "
                        "printed cards stay valid; only the attendance list goes back to 0.")
            typed1 = st.text_input(f"Type the event code `{ev['_id']}` to confirm", key="confirm_clear")
            if st.button("Clear all check-ins", disabled=typed1.strip() != ev["_id"] or not n_att):
                st.toast(f"Removed {repo.clear_attendance(c.db, ev['_id'])} check-ins")
                st.rerun()

            st.markdown("---\n**Delete this event completely** — removes the event with ALL its members, photos, "
                        "form scans and check-ins. Use it for test events or old events. Other events are not "
                        "touched. **This cannot be undone**; download the Excel first if you need a record.")
            others = [e for e in repo.list_events(c.db) if e["_id"] != ev["_id"]]
            if not others:
                st.caption("This is the only event — create another event before deleting this one.")
            else:
                typed2 = st.text_input(f"Type `delete {ev['_id']}` to confirm", key="confirm_delete")
                if st.button("🗑️ Delete event permanently", type="primary",
                             disabled=typed2.strip() != f"delete {ev['_id']}"):
                    res = repo.delete_event(c.db, ev["_id"])
                    st.session_state.pop("event_id", None)
                    st.session_state["flash"] = ("success", f"Event '{ev['_id']}' deleted: {res['people']} people, "
                                                            f"{res['attendance']} check-ins, {res['files']} files.")
                    st.rerun()

with users_tab:
    if auth.mode() == "pin":
        st.info("**Who can open the app** is controlled in Streamlit Community Cloud: open the app, "
                "**Share** → make it private → add the volunteers' emails. Inside the app, everyone can scan; "
                "admin pages unlock with the **ADMIN_PIN** from the app secrets. "
                "The list below is only used if Google sign-in ([auth]) is set up.")
    st.caption("Only these Google accounts can sign in. **Volunteers** see only Scan and Live Dashboard; "
               "**admins** see everything.")
    boot = secret("BOOTSTRAP_ADMIN_EMAIL")
    if boot:
        st.caption(f"Owner admin (from secrets): {boot}")
    with st.form("add_user", clear_on_submit=True):
        a, b, cc = st.columns([3, 2, 1.2])
        emails = a.text_area("Email(s) — one per line or comma separated", height=80)
        role = b.radio("Role", ["volunteer", "admin"], horizontal=True)
        name = b.text_input("Name (optional)")
        if cc.form_submit_button("Add", type="primary"):
            added = 0
            for e in re.split(r"[\s,;]+", emails):
                if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
                    repo.add_user(c.db, e, role, name)
                    added += 1
            st.toast(f"Added {added} user(s)")
            st.rerun()
    for u in repo.list_users(c.db):
        a, b, cc = st.columns([4, 1.2, 1])
        a.write(f"{u['_id']}" + (f" · {u['name']}" if u.get("name") else ""))
        b.write("🛡️ admin" if u["role"] == "admin" else "📷 volunteer")
        if u["_id"] != c.email and cc.button("Remove", key=f"rm_{u['_id']}"):
            repo.remove_user(c.db, u["_id"])
            st.rerun()

with sys_tab:
    def check(label, ok, hint=""):
        st.write(("✅ " if ok else "❌ ") + label + ("" if ok else f" — {hint}"))

    try:
        c.db.command("ping")
        check("Database connected", True)
    except Exception as e:
        check("Database connected", False, str(e))
    check(f"AI form reading: {extract.provider_label()}", extract.ai_configured(),
          "add GEMINI_API_KEY (or ANTHROPIC_API_KEY) to secrets")
    check("QR signing secret (QR_SECRET)", bool(secret("QR_SECRET")), "add a long random string to secrets")
    m = auth.mode()
    if m == "pin":
        check("Access: Streamlit Cloud viewer list + admin PIN", bool(secret("ADMIN_PIN")),
              "set ADMIN_PIN in secrets, otherwise nobody can open the admin pages")
    elif m == "google":
        check("Access: Google sign-in + allowed users list", True)
    else:
        check("Access: local test mode (DEV_LOGIN_EMAIL)", True)
    check("Google Sheets ([google_sheets] + APP_URL)", gsheets.configured(), "optional")
    st.caption("Card text is rendered with bundled Noto fonts through PyMuPDF (HarfBuzz), "
               "so Marathi conjuncts print correctly on any server.")
