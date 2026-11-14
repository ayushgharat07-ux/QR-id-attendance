"""Per-session context shared by all pages."""
from __future__ import annotations

from dataclasses import dataclass

import streamlit as st
from pymongo.database import Database

from core import repo
from core.db import get_db


@dataclass
class Ctx:
    db: Database
    event: dict
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def ctx() -> Ctx:
    db = get_db()
    event = repo.get_event(db, st.session_state["event_id"])
    if event is None:
        st.error("The selected event no longer exists.")
        st.stop()
    return Ctx(db, event, st.session_state["user_email"], st.session_state["role"])


def require_admin(c: Ctx) -> None:
    if not c.is_admin:
        st.error("Only admins can open this page.")
        st.stop()
