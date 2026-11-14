from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st

from core import extract, pipeline, repo
from core.ctx import ctx, require_admin

c = ctx()
require_admin(c)
st.subheader("📤 Upload filled forms · फॉर्म अपलोड")
st.caption("Upload scans or phone photos (JPG/PNG) or PDFs. Each PDF page is treated as one form. "
           "The AI reads the details in Marathi and English and cuts out the photo. If the mobile number and "
           "name match a member already imported from Excel, the photo is attached to that member "
           "automatically; everything else goes to **Review & Approve**.")

if not extract.ai_configured():
    st.warning("No AI key is set (GEMINI_API_KEY or ANTHROPIC_API_KEY), so the AI cannot read forms. Drafts will be created "
               "with empty fields for manual entry.")

ptype = st.radio("These forms are for", list(repo.TYPE_LABELS), format_func=repo.TYPE_LABELS.get, horizontal=True)
files = st.file_uploader("Forms", type=["jpg", "jpeg", "png", "webp", "pdf"], accept_multiple_files=True,
                         key=f"uploader_{st.session_state.get('upload_round', 0)}")

if files and st.button(f"🤖 Read {len(files)} file(s)", type="primary"):
    jobs = []
    with st.spinner("Preparing pages…"):
        for f in files:
            try:
                jobs.extend(pipeline.jobs_from_upload(f.getvalue(), f.name))
            except Exception as e:
                st.error(f"Could not open {f.name}: {e}")
    bar = st.progress(0.0, text=f"Reading 0 / {len(jobs)} forms…")
    results = []
    with ThreadPoolExecutor(max_workers=extract.max_parallel()) as pool:
        futures = {pool.submit(pipeline.process_page, c.db, c.event, ptype, j, c.email): j for j in jobs}
        for i, fut in enumerate(as_completed(futures), start=1):
            try:
                results.append(fut.result())
            except Exception as e:  # unexpected (e.g. database) error: report and continue
                results.append(pipeline.PageResult(futures[fut], None, "error", f"Not saved: {e}"))
            bar.progress(i / len(jobs), text=f"Reading {i} / {len(jobs)} forms…")
    st.session_state["upload_results"] = results
    st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
    st.rerun()

if results := st.session_state.get("upload_results"):
    ok = [r for r in results if r.status == "ok"]
    matched = [r for r in results if r.status == "matched"]
    dup = [r for r in results if r.status == "duplicate"]
    err = [r for r in results if r.status == "error"]
    if matched:
        st.success(f"📷 {len(matched)} form(s) matched to existing members — photo added automatically.")
    if ok:
        st.success(f"{len(ok)} form(s) read and waiting for review.")
    if dup:
        st.info(f"{len(dup)} page(s) skipped because they were uploaded before.")
    if err:
        saved = [r for r in err if r.person_id is not None]
        if saved:
            st.warning(f"{len(saved)} page(s) could not be read by the AI; they are in the review list for manual entry.")
        if len(saved) < len(err):
            st.error(f"{len(err) - len(saved)} page(s) could not be saved at all — see details below and upload them again.")
    for r in results:
        r.job.image = None  # don't keep page images in the session
    st.dataframe(
        [{"File": r.job.filename, "Page": r.job.page_no, "Result": r.status, "Details": r.message} for r in results],
        hide_index=True, use_container_width=True,
    )
    st.page_link("views/review.py", label="Go to Review & Approve →", icon="✅")

drafts = repo.list_people(c.db, c.event["_id"], status="draft")
if drafts and not results:
    st.info(f"{len(drafts)} form(s) are waiting for review.")
    st.page_link("views/review.py", label="Go to Review & Approve →", icon="✅")
