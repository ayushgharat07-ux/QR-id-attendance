import io

import streamlit as st
from PIL import Image

from core import extract, pipeline, repo, ui
from core import db as dbm
from core.ctx import ctx, require_admin

c = ctx()
require_admin(c)
st.subheader("✅ Review & approve · तपासणी")

drafts = repo.list_people(c.db, c.event["_id"], status="draft")

with st.expander("➕ Add a person manually (no form)"):
    with st.form("manual_add", clear_on_submit=True):
        vals = ui.person_editor({"type": "attendee"}, key="manual")
        up = st.file_uploader("Photo", type=["jpg", "jpeg", "png"], key="manual_photo")
        if st.form_submit_button("Add to review list"):
            if errs := ui.validate(vals):
                st.error(" ".join(errs))
            else:
                pid = repo.create_draft(c.db, c.event["_id"], vals["type"], vals, created_by=c.email,
                                        extraction={"manual": True})
                if up:
                    pipeline.replace_photo(c.db, repo.get_person(c.db, pid), Image.open(up))
                st.success("Added. Review and approve it below.")
                st.rerun()

if not drafts:
    st.success("Nothing to review. 🎉 Upload more forms or open **People** to see approved members.")
    st.stop()

ids = [str(d["_id"]) for d in drafts]
if st.session_state.get("review_id") not in ids:
    st.session_state["review_id"] = ids[0]
labels = {str(d["_id"]): f"{i + 1}. {d.get('name_mr') or d.get('name_en') or '(no name)'} · {d.get('mobile', '')}"
          for i, d in enumerate(drafts)}
st.selectbox(f"{len(drafts)} waiting", ids, key="review_id", format_func=labels.get)
person = repo.get_person(c.db, st.session_state["review_id"])
key = str(person["_id"])
meta = person.get("extraction") or {}

left, right = st.columns([1, 1.2], gap="large")
with left:
    form_bytes = ui.file_bytes(str(person["form_file_id"])) if person.get("form_file_id") else None
    if form_bytes:
        st.image(form_bytes, caption=f"{meta.get('source_file', '')} · page {meta.get('page', '')}",
                 use_container_width=True)
    else:
        st.info("No form image (added manually).")

with right:
    if meta.get("error"):
        st.error(f"AI could not read this form: {meta['error']}")
    if meta.get("is_form") is False:
        st.warning("The AI thinks this may not be a registration form.")
    if meta.get("notes"):
        st.info(f"AI note: {meta['notes']}")
    if meta.get("duplicate_mobile"):
        st.warning(f"Same mobile number already exists: {', '.join(meta['duplicate_mobile'])}")
    if meta.get("unsure"):
        st.warning("Please double-check: " + ", ".join(meta["unsure"]))

    members = repo.list_people(c.db, c.event["_id"], status="verified")
    if members:
        with st.container(border=True):
            st.markdown("**Is this form for a member who is already in the list (e.g. from Excel)?**")
            ids_m = [str(m["_id"]) for m in members]
            sug = meta.get("suggest_member")
            if sug in ids_m:
                st.caption(f"Suggested match (name similarity {meta.get('suggest_score', 0):.0%}). "
                           + meta.get("suggest_reason", ""))
            lab = {str(m["_id"]): f"{m['pid']} · {m.get('name_mr') or m.get('name_en', '')} · {m.get('mobile', '')}"
                              + ("" if m.get("photo_id") else " · no photo") for m in members}
            target = st.selectbox("Member", ids_m, index=ids_m.index(sug) if sug in ids_m else None,
                                  format_func=lab.get, placeholder="Search name / mobile / ID…", key=f"merge_{key}",
                                  label_visibility="collapsed")
            if target and st.button("📎 Attach this photo & form to the member (no new ID)", key=f"domerge_{key}"):
                repo.merge_draft_into(c.db, person, repo.get_person(c.db, target))
                st.toast(f"Photo added to {lab[target].split(' · ')[0]}")
                st.session_state.pop("review_id", None)
                st.rerun()

    pc1, pc2 = st.columns([1, 2])
    with pc1:
        img = ui.photo_uri(person)
        if img:
            st.image(ui.file_bytes(str(person["photo_id"])), caption="Photo", width=140)
        else:
            st.error("No photo found")
    with pc2:
        with st.popover("✂️ Fix photo"):
            tab_crop, tab_up = st.tabs(["Crop from form", "Upload photo"])
            with tab_crop:
                if form_bytes:
                    from streamlit_cropper import st_cropper

                    form_img = Image.open(io.BytesIO(form_bytes))
                    cropped = st_cropper(form_img, realtime_update=True, box_color="#E8735A",
                                         aspect_ratio=None, key=f"crop_{key}")
                    st.image(cropped, width=120)
                    if st.button("Use this crop", key=f"usecrop_{key}"):
                        pipeline.replace_photo(c.db, person, cropped)
                        st.rerun()
            with tab_up:
                up = st.file_uploader("Photo", type=["jpg", "jpeg", "png"], key=f"up_{key}")
                if up and st.button("Use uploaded photo", key=f"useup_{key}"):
                    pipeline.replace_photo(c.db, person, Image.open(up))
                    st.rerun()

    vals = ui.person_editor(person, key)
    errs = ui.validate(vals)

    # Duplicate guard: is this person already in the list (approved, or another draft)?
    dups = repo.find_duplicates(c.db, c.event["_id"], vals, exclude_id=person["_id"])
    blocking = [d for d in dups if d["kind"] == "same"]
    if dups:
        lines = "\n".join(f"- **{repo.DUP_LABELS[d['kind']]}**: {repo.describe(d['person'])}"
                          + (" *(not approved yet)*" if d["person"].get("status") == "draft" else "")
                          for d in dups)
        (st.error if blocking else st.warning)(
            "Possible duplicate — already in the list:\n" + lines +
            ("\n\nIf it is the same person, use **Attach this photo & form to the member** above "
             "(or delete this draft) instead of approving." if blocking else ""))
    override = False
    if blocking:
        override = st.checkbox("This is a different person — approve anyway", key=f"dupok_{key}")

    b1, b2, b3, b4 = st.columns(4)
    if b1.button("✅ Approve", type="primary", use_container_width=True,
                 disabled=bool(errs) or (bool(blocking) and not override)):
        repo.update_person(c.db, person["_id"], vals)
        pid = repo.approve_person(c.db, person["_id"], c.event, c.email)
        st.toast(f"Approved · ID {pid}")
        st.session_state.pop("review_id", None)
        st.rerun()
    if b2.button("💾 Save", use_container_width=True):
        repo.update_person(c.db, person["_id"], vals)
        st.toast("Saved")
    if b3.button("🔁 Re-read", use_container_width=True, disabled=not person.get("form_file_id"),
                 help="Ask the AI to read this form again (overwrites the fields)"):
        try:
            with st.spinner("Reading…"):
                pipeline.rerun_extraction(c.db, person)
            for k in list(st.session_state):
                if k.endswith(f"_{key}"):
                    del st.session_state[k]
            st.rerun()
        except extract.ExtractionError as e:
            st.error(str(e))
    if b4.button("🗑️ Delete", use_container_width=True):
        repo.delete_person(c.db, person["_id"])
        st.session_state.pop("review_id", None)
        st.rerun()
    if errs:
        st.caption(" · ".join(errs))
