import streamlit as st
from PIL import Image

from core import cards, exports, pipeline, qr, repo, ui
from core.ctx import ctx, require_admin

c = ctx()
require_admin(c)
st.subheader("👥 People · सदस्य यादी")

people = repo.list_people(c.db, c.event["_id"], status="verified")
present = {a["pid"] for a in repo.list_attendance(c.db, c.event["_id"])}

f1, f2, f3 = st.columns([2, 1, 1])
q = f1.text_input("Search", placeholder="Name, mobile, ID, विभाग").strip().lower()
t = f2.selectbox("Type", ["all", *repo.TYPE_LABELS], format_func=lambda x: "All" if x == "all" else repo.TYPE_LABELS[x])
s = f3.selectbox("Status", ["all", "present", "absent", "no photo"], format_func=str.title)


def keep(p):
    if t != "all" and p["type"] != t:
        return False
    if (s == "present" and p["pid"] not in present or s == "absent" and p["pid"] in present
            or s == "no photo" and p.get("photo_id")):
        return False
    if q:
        hay = " ".join(str(p.get(k, "")) for k in ("pid", "name_mr", "name_en", "mobile", "vibhag_mr", "vibhag_en", "business", "group", "att_no")).lower()
        return q in hay
    return True


rows = [p for p in people if keep(p)]
st.caption(f"{len(rows)} of {len(people)} approved people")
sel = st.dataframe(
    [{"ID": p["pid"], "Type": p["type"], "नाव": p.get("name_mr", ""), "Name": p.get("name_en", ""),
      "Mobile": p.get("mobile", ""), "Business": p.get("business", ""),
      "Group": p.get("group") or p.get("vibhag_mr", ""), "Photo": "📷" if p.get("photo_id") else "—",
      "Present": "✅" if p["pid"] in present else ""} for p in rows],
    hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row", key="people_table",
)
picked = sel.selection.rows if sel and sel.selection else []
if not picked:
    st.info("Select a row to edit, see the ID card, or delete.")
    st.stop()

person = rows[picked[0]]
key = f"p_{person['_id']}"
st.divider()
left, right = st.columns([1.3, 1], gap="large")
with left:
    st.markdown(f"#### {person.get('name_mr', '')} · `{person['pid']}`")
    vals = ui.person_editor(person, key, allow_type_change=False)
    errs = ui.validate(vals)
    for d in repo.find_duplicates(c.db, c.event["_id"], vals, exclude_id=person["_id"]):
        if d["kind"] == "same":
            st.warning(f"Looks like the same person as **{repo.describe(d['person'])}**. "
                       "If so, keep one and delete the other.")
    b1, b2 = st.columns(2)
    if b1.button("💾 Save changes", type="primary", disabled=bool(errs), use_container_width=True):
        repo.update_person(c.db, person["_id"], vals)
        st.toast("Saved")
        st.rerun()
    with b2.popover("🗑️ Delete person", use_container_width=True):
        st.write("This removes the person, their photo and attendance. Their printed card will stop working.")
        if st.button("Yes, delete", type="primary", key=f"del_{key}"):
            repo.delete_person(c.db, person["_id"])
            st.rerun()
    up = st.file_uploader("Replace photo", type=["jpg", "jpeg", "png"], key=f"ph_{key}")
    if up and st.button("Use this photo", key=f"useph_{key}"):
        pipeline.replace_photo(c.db, person, Image.open(up))
        st.rerun()
    if errs:
        st.caption(" · ".join(errs))

with right:
    card = ui.render_card(person, c.event)
    st.image(card, caption="ID card preview", use_container_width=True)
    if not person.get("photo_id"):
        st.caption("No photo yet — upload this member's form or a photo.")
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Card (PDF)", cards.card_pdf(card, c.event.get("card_width_mm") or cards.CARD_MM[0]),
                       f"{exports.photo_filename(person)[:-4]}_card.pdf", "application/pdf", use_container_width=True)
    d2.download_button("⬇️ QR code", qr.qr_png(qr.make_payload(c.event["_id"], person["pid"])),
                       f"{person['pid']}_qr.png", "image/png", use_container_width=True)
