"""Who is using the app, and as what (admin / volunteer).

Three modes, chosen from the secrets:
- "google": [auth] section present -> everyone signs in with Google (st.login); roles come from the
            allowed-users list (Event Settings > Users) plus BOOTSTRAP_ADMIN_EMAIL.
- "dev":    DEV_LOGIN_EMAIL set and the app is opened on this computer (localhost) -> acts as that email.
            Ignored on any real server, so a copied secrets file can't open the app to everyone.
- "pin":    default. Who may open the app at all is controlled by Streamlit Community Cloud
            (private app -> invite viewer emails). Inside, everyone is a volunteer (Scan + Dashboard) and
            types their name once; admin pages unlock with ADMIN_PIN.
"""
from __future__ import annotations

import hmac
import time

import streamlit as st

from core import branding, repo
from core.config import secret, secret_section

MAX_PIN_TRIES = 5
LOCKOUT_SECONDS = 60


def _auth_configured() -> bool:
    return bool(secret_section("auth").get("client_id"))


def _is_local() -> bool:
    try:
        host = (st.context.headers.get("Host") or "").split(":")[0].lower()
    except Exception:  # no request (tests / bare mode)
        host = ""
    return host in ("", "localhost", "127.0.0.1", "[::1]")


def mode() -> str:
    if _auth_configured():
        return "google"
    if secret("DEV_LOGIN_EMAIL") and _is_local():
        return "dev"
    return "pin"


def require_user(db) -> tuple[str, str]:
    """Stop the page unless the person may use the app. Returns (who, role)."""
    m = mode()
    if m == "pin":
        return _pin_mode_user()
    if m == "dev":
        email = str(secret("DEV_LOGIN_EMAIL")).lower()
    else:
        if not st.user.is_logged_in:
            _login_page()
            st.stop()
        email = (st.user.get("email") or "").lower()

    role = repo.get_role(db, email)
    if role is None:
        branding.header(None)
        st.error(f"**{email}** does not have access to this app. Ask the event admin to add your email.")
        if m == "google":
            st.button("Sign out", on_click=st.logout)
        branding.footer()
        st.stop()
    return email, role


# ---------------------------------------------------------------- PIN mode
def _pin_mode_user() -> tuple[str, str]:
    name = st.session_state.get("volunteer_name") or st.query_params.get("by", "")
    if not name:
        branding.header(None)
        st.markdown("### 👋 Welcome · स्वागत")
        with st.form("who"):
            typed = st.text_input("Your name · आपले नाव", placeholder="e.g. Rahul Patil",
                                  help="Shown next to every check-in you scan.")
            if st.form_submit_button("Continue · पुढे", type="primary", use_container_width=True):
                if typed.strip():
                    st.session_state["volunteer_name"] = typed.strip()[:40]
                    st.query_params["by"] = st.session_state["volunteer_name"]  # survives refresh / bookmarks
                    st.rerun()
                st.error("Please type your name.")
        branding.footer()
        st.stop()
    st.session_state["volunteer_name"] = name
    st.session_state.setdefault("is_admin", False)
    return name, "admin" if st.session_state["is_admin"] else "volunteer"


def _check_pin(typed: str) -> None:
    pin = str(secret("ADMIN_PIN") or "")
    blocked_until = st.session_state.get("pin_blocked_until", 0)
    if time.time() < blocked_until:
        st.session_state["pin_msg"] = f"Too many wrong tries. Wait {int(blocked_until - time.time())} s."
        return
    if pin and hmac.compare_digest(typed.strip(), pin):
        st.session_state.update(is_admin=True, pin_tries=0, pin_msg="")
        return
    tries = st.session_state.get("pin_tries", 0) + 1
    st.session_state["pin_tries"] = tries
    if tries >= MAX_PIN_TRIES:
        st.session_state.update(pin_blocked_until=time.time() + LOCKOUT_SECONDS, pin_tries=0)
    st.session_state["pin_msg"] = "Wrong PIN."


def sidebar_controls() -> None:
    """Sign-out (Google mode) or name + admin unlock (PIN mode)."""
    m = mode()
    if m == "google":
        st.button("Sign out", on_click=st.logout, use_container_width=True)
        return
    if m != "pin":
        return
    if st.button("Change name", use_container_width=True):
        st.session_state.pop("volunteer_name", None)
        st.query_params.pop("by", None)
        st.session_state["is_admin"] = False
        st.rerun()
    if st.session_state.get("is_admin"):
        if st.button("🔒 Lock admin pages", use_container_width=True):
            st.session_state["is_admin"] = False
            st.rerun()
        return
    with st.expander("🔐 Admin"):
        if not secret("ADMIN_PIN"):
            st.caption("Set ADMIN_PIN in the app secrets to enable admin pages.")
            return
        with st.form("admin_pin", clear_on_submit=True):
            typed = st.text_input("Admin PIN", type="password")
            if st.form_submit_button("Unlock"):
                _check_pin(typed)
                if st.session_state.get("is_admin"):
                    st.rerun()
        if msg := st.session_state.get("pin_msg"):
            st.error(msg)


def _login_page() -> None:
    branding.header(None)
    st.markdown("### Event Attendance")
    st.write("Sign in with the Google account your event admin registered.")
    st.button("🔐  Sign in with Google", type="primary", on_click=st.login, use_container_width=True)
    branding.footer()


def sign_out_button() -> None:  # kept for compatibility
    sidebar_controls()
