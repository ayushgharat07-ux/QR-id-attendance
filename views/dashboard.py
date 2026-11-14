from html import escape

import pandas as pd
import streamlit as st

from core import repo, ui
from core.ctx import ctx

c = ctx()
st.subheader("📊 Live attendance · थेट हजेरी")


@st.fragment(run_every="5s")
def live() -> None:
    s = repo.stats(c.db, c.event["_id"])
    reg, pres = s["registered"], s["present"]
    a, v = s["by_type"]["attendee"], s["by_type"]["volunteer"]
    m = st.columns(4)
    m[0].metric("Registered · नोंदणी", reg)
    m[1].metric("Present · हजर", pres, f"{(pres / reg * 100 if reg else 0):.0f}%")
    m[2].metric("Members present", f"{a['present']} / {a['registered']}")
    m[3].metric("Volunteers present", f"{v['present']} / {v['registered']}")
    st.progress(pres / reg if reg else 0.0)

    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Recent check-ins**")
        recent = repo.list_attendance(c.db, c.event["_id"], limit=12)
        if not recent:
            st.info("Waiting for the first scan…")
        cols = st.columns(4)
        for i, rec in enumerate(recent):
            person = repo.get_person_by_pid(c.db, c.event["_id"], rec["pid"])
            img = ui.photo_uri(person)
            img_html = (f'<img src="{img}" style="width:90px;height:108px;object-fit:cover;border-radius:10px"/>'
                        if img else "")
            with cols[i % 4]:
                st.markdown(
                    f'<div style="text-align:center;margin-bottom:12px">{img_html}'
                    f'<div style="font-weight:600;font-size:.9rem">{escape(rec.get("name_mr", ""))}</div>'
                    f'<div style="font-size:.75rem;color:#5b6b7c">{escape(ui.fmt_time(rec["scanned_at"])[-8:])}</div></div>',
                    unsafe_allow_html=True,
                )
    with right:
        st.markdown("**By group / विभाग**")
        if s["by_vibhag"]:
            df = pd.DataFrame(
                [{"विभाग": k, "Present": v_["present"], "Absent": v_["registered"] - v_["present"]}
                 for k, v_ in sorted(s["by_vibhag"].items(), key=lambda kv: -kv[1]["registered"])]
            ).set_index("विभाग")
            st.bar_chart(df, horizontal=True, color=["#1f9d55", "#cfd6de"], stack=True)
        else:
            st.write("No registrations yet.")
    st.caption(f"Updates every 5 seconds · last update {ui.fmt_time(repo.now())}")


live()
