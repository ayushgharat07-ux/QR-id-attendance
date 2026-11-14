"""Event Attendance (developed by United Brothers Global) — entry point.  Run:  streamlit run app.py"""
from __future__ import annotations

import streamlit as st
from PIL import Image

from core import auth, branding, gsheets, repo
from core.config import LOGO_MARK
from core.db import get_db

st.set_page_config(
    page_title="Event Attendance",
    page_icon=Image.open(LOGO_MARK),
    layout="wide",
    initial_sidebar_state="auto",
)
branding.inject_css()

# "Add to Home Screen" with the app icon and name. On Streamlit Community Cloud the app runs inside
# Streamlit's wrapper page (same origin), so the icon/manifest must be put on the TOP page, replacing
# Streamlit's own ones; when the app is the top page (local / other hosts) it goes there instead.
st.iframe(
    """<script>
    (function(){
      let app = window.parent, top = app;
      try { if (window.top !== app && window.top.document) top = window.top; } catch (e) {}
      // static files are served by the app server: /app/static/... (on Cloud below the /~/+/ prefix)
      const p = app.location.pathname, cut = p.indexOf('/~/+/');
      const base = app.location.origin + (cut >= 0 ? p.slice(0, cut + 5) : '/') + 'app/static/';
      const docs = [top.document]; if (top !== app) docs.push(app.document);
      for (const d of docs) {
        if (d.getElementById('ubg-manifest')) continue;
        d.querySelectorAll('link[rel="manifest"], link[rel="apple-touch-icon"], link[rel="icon"], link[rel="shortcut icon"], meta[name="theme-color"]')
          .forEach(el => el.remove());
        const link = (attrs) => { const el = d.createElement('link'); Object.assign(el, attrs); d.head.appendChild(el); };
        const meta = (name, content) => { const el = d.createElement('meta'); el.name = name; el.content = content; d.head.appendChild(el); };
        link({id: 'ubg-manifest', rel: 'manifest', href: base + 'manifest.json'});
        link({rel: 'icon', type: 'image/png', sizes: '192x192', href: base + 'icon-192.png'});
        link({rel: 'apple-touch-icon', sizes: '180x180', href: base + 'apple-touch-icon.png'});
        meta('theme-color', '#1B3A5C');
        meta('mobile-web-app-capable', 'yes');
        meta('apple-mobile-web-app-capable', 'yes');
        meta('apple-mobile-web-app-title', 'Attendance');
        meta('application-name', 'Event Attendance');
        d.title = 'Event Attendance';
      }
    })();
    </script>""",
    height=1,
)

db = get_db()
repo.ensure_default_event(db)

email, role = auth.require_user(db)
st.session_state["user_email"] = email
st.session_state["role"] = role

# Google Sheets OAuth comes back to the app root with ?code=...&state=...
qp = st.query_params
if "code" in qp and "state" in qp and role == "admin":
    try:
        st.session_state["event_id"] = gsheets.finish_connect(db, qp["state"], qp["code"])
        st.session_state["flash"] = ("success", "Google Sheets connected and synced ✅")
    except Exception as e:  # show the reason, keep the app usable
        st.session_state["flash"] = ("error", f"Google connection failed: {e}")
    st.query_params.clear()
    st.rerun()

# ---- event selection
events = repo.list_events(db)
ids = [e["_id"] for e in events]
if st.session_state.get("event_id") not in ids:
    active = [e["_id"] for e in events if e.get("active")]
    st.session_state["event_id"] = (active or ids)[0]

with st.sidebar:
    if logo := branding.event_logo_uri(repo.get_event(db, st.session_state["event_id"])):
        st.image(logo, width=110)  # organiser's logo, if uploaded
    labels = {e["_id"]: f"{e.get('name_mr', e['_id'])} {e.get('subtitle_mr', '')}".strip() for e in events}
    st.selectbox("Event / कार्यक्रम", ids, key="event_id", format_func=lambda i: labels[i])
    st.caption(f"Using as **{email}** · {role}")
    auth.sidebar_controls()
    with st.expander("📱 Put this app on your phone"):
        st.markdown(
            "**Android (Chrome):** open this link → tap **⋮** (top right) → **Add to Home screen** "
            "or **Install app** → **Add**.\n\n"
            "**iPhone (Safari):** open this link → tap **Share** (square with arrow) → "
            "**Add to Home Screen** → **Add**.\n\n"
            "मोबाईलवर: Chrome मध्ये **⋮ → Add to Home screen**, iPhone वर Safari मध्ये "
            "**Share → Add to Home Screen**. त्यानंतर होम स्क्रीनवरील ॲपच्या चिन्हावर टॅप करा."
        )

event = repo.get_event(db, st.session_state["event_id"])
branding.header(event)

if flash := st.session_state.pop("flash", None):
    getattr(st, flash[0])(flash[1])

scan = st.Page("views/scan.py", title="Scan QR", icon="📷")
dashboard = st.Page("views/dashboard.py", title="Live Dashboard", icon="📊")
if role == "admin":
    pages = {
        "Event day": [dashboard, scan, st.Page("views/attendance.py", title="Attendance & Export", icon="📥")],
        "Registration": [
            st.Page("views/import_excel.py", title="Import Excel", icon="📑"),
            st.Page("views/upload.py", title="Upload Forms", icon="📤"),
            st.Page("views/review.py", title="Review & Approve", icon="✅"),
            st.Page("views/people.py", title="People", icon="👥"),
            st.Page("views/id_cards.py", title="ID Cards", icon="🪪"),
        ],
        "Admin": [st.Page("views/settings.py", title="Event Settings", icon="⚙️")],
    }
else:
    pages = [scan, dashboard]

st.navigation(pages).run()
branding.footer()
