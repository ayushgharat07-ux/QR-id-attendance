import io
import zipfile

import streamlit as st

from core import cards, exports, photo, qr, repo, ui
from core import db as dbm
from core.ctx import ctx, require_admin

c = ctx()
require_admin(c)
st.subheader("🪪 ID cards · ओळखपत्र")

event = c.event
people = repo.list_people(c.db, event["_id"], status="verified")
sample = people[0] if people else {
    "event_id": event["_id"], "pid": f"{event['id_prefix']}-A-0001", "type": "attendee",
    "name_mr": "श्री. संदेश लक्ष्मण शिंदे", "name_en": "Shri. Sandesh Laxman Shinde", "vibhag_mr": "कोन, ता. पनवेल",
    "business": "Bike Spare Parts Sales & Services", "group": "Apta - Men (Thu)"}
if people:
    with_photo = next((p for p in people if p.get("photo_id")), None)
    sample = with_photo or sample

design_tab, generate_tab = st.tabs(["🎨 Card design", "🖨️ Generate & print"])

with design_tab:
    current = cards.event_design(event)
    options = list(cards.DESIGNS)
    design = st.radio("Design", options, index=options.index(current), format_func=cards.DESIGNS.get, horizontal=True)
    if design != current:
        c.db.events.update_one({"_id": event["_id"]}, {"$set": {"card_design": design}})
        st.rerun()

    if design == "upload":
        up = st.file_uploader("Upload your card design (PNG/JPG/PDF, blank — no name/photo)",
                              type=["png", "jpg", "jpeg", "pdf"], key="tpl_up")
        if up and st.button("Use this design", type="primary"):
            img = photo.upload_to_pages(up.getvalue(), up.name, dpi=300)[0]
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            new_id = dbm.put_file(buf.getvalue(), up.name, "image/png", c.db, event_id=event["_id"], kind="template")
            old = event.get("card_template_id")
            c.db.events.update_one({"_id": event["_id"]}, {"$set": {"card_template_id": new_id}})
            dbm.delete_file(old, c.db)
            st.rerun()
        if not event.get("card_template_id"):
            st.info("Upload a design to continue; until then cards use the standard design.")

    if design == "ubg":
        st.image(ui.render_card(sample, event, design), width=320, caption="Preview")
    else:
        layout = cards.event_layout(event, design)
        edit, preview = st.columns([1.3, 1], gap="large")
        with edit:
            st.caption("Fine-tune where things go. Positions are % of the card width (x) and height (y); "
                       "text y is the middle of the line.")
            new = {}
            for name, label in cards.ELEMENT_LABELS.items():
                el = dict(layout.get(name) or cards.default_layout(design)[name])
                with st.expander(("✅ " if el.get("enabled", True) else "▫️ ") + label):
                    k = f"lay_{design}_{name}"
                    el["enabled"] = st.checkbox("Show", el.get("enabled", True), key=k + "_on")
                    a, b = st.columns(2)
                    el["x"] = a.slider("x %", 0.0, 100.0, el["x"] * 100, 0.1, key=k + "_x") / 100
                    el["y"] = b.slider("y %", 0.0, 100.0, el["y"] * 100, 0.1, key=k + "_y") / 100
                    if name == "photo":
                        el["w"] = a.slider("width %", 5.0, 100.0, el["w"] * 100, 0.1, key=k + "_w") / 100
                        el["h"] = b.slider("height %", 5.0, 100.0, el["h"] * 100, 0.1, key=k + "_h") / 100
                        el["circle"] = st.checkbox("Round photo", el.get("circle", False), key=k + "_circle")
                    elif name == "qr":
                        el["size"] = a.slider("size % of width", 10.0, 80.0, el["size"] * 100, 0.1, key=k + "_s") / 100
                    else:
                        el["size"] = a.slider("text size % of height", 0.5, 10.0, el["size"] * 100, 0.1, key=k + "_s") / 100
                        el["max_w"] = b.slider("max width %", 10.0, 100.0, el.get("max_w", 0.9) * 100, 0.5, key=k + "_mw") / 100
                        a2, b2, c2 = st.columns(3)
                        el["color"] = a2.color_picker("Colour", el.get("color", "#000000"), key=k + "_c")
                        el["bold"] = b2.checkbox("Bold", el.get("bold", False), key=k + "_b")
                        el["align"] = c2.selectbox("Align", ["center", "left", "right"],
                                                   ["center", "left", "right"].index(el.get("align", "center")),
                                                   key=k + "_a")
                new[name] = el
            s1, s2 = st.columns(2)
            if s1.button("💾 Save layout", type="primary"):
                c.db.events.update_one({"_id": event["_id"]}, {"$set": {f"card_layouts.{design}": new}})
                st.toast("Layout saved")
            if s2.button("↺ Reset to default positions"):
                c.db.events.update_one({"_id": event["_id"]}, {"$unset": {f"card_layouts.{design}": ""}})
                for key in [k for k in st.session_state if str(k).startswith(f"lay_{design}_")]:
                    del st.session_state[key]
                st.rerun()
        with preview:
            st.image(ui.render_card(sample, event, design, new), caption=f"Preview: {sample.get('name_mr', '')}",
                     use_container_width=True)

with generate_tab:
    if not people:
        st.info("No approved members yet. Import the Excel list or approve forms first.")
        st.stop()
    g1, g2, g3 = st.columns(3)
    which = g1.selectbox("Cards for", ["all", *repo.TYPE_LABELS],
                         format_func=lambda x: "Everyone" if x == "all" else repo.TYPE_LABELS[x])
    only = g2.selectbox("Only", ["all", "with_photo", "without_photo"],
                        format_func={"all": "All", "with_photo": "With photo", "without_photo": "Without photo"}.get)
    width_mm = g3.number_input("Card width on paper (mm)", 40.0, 120.0,
                               float(event.get("card_width_mm") or cards.CARD_MM[0]), 1.0,
                               help="Standard ID card: 54 mm. Height follows the design's proportions.")
    if width_mm != event.get("card_width_mm"):
        c.db.events.update_one({"_id": event["_id"]}, {"$set": {"card_width_mm": width_mm}})
    pool = [p for p in people if (which == "all" or p["type"] == which)
            and (only == "all" or (only == "with_photo") == bool(p.get("photo_id")))]
    if not pool:
        st.info("Nobody matches these filters.")
        st.stop()
    pids = [p["pid"] for p in pool]
    rng = st.select_slider("ID range", options=pids, value=(pids[0], pids[-1])) if len(pids) > 1 else (pids[0], pids[0])
    chosen = pool[pids.index(rng[0]):pids.index(rng[1]) + 1]
    no_photo = [p for p in chosen if not p.get("photo_id")]
    st.caption(f"{len(chosen)} card(s) selected · {len(no_photo)} without photo (printed with the design's "
               "placeholder) · Print the PDF at 100% / Actual size.")

    if st.button("🖨️ Generate cards", type="primary"):
        bar = st.progress(0.0)
        sheets = cards.PrintSheets(width_mm=width_mm)
        singles, qrs = io.BytesIO(), io.BytesIO()
        with zipfile.ZipFile(singles, "w", zipfile.ZIP_STORED) as zs, zipfile.ZipFile(qrs, "w") as zq:
            for i, p in enumerate(chosen, start=1):
                card = ui.render_card(p, event)
                sheets.add(card)
                base = exports.photo_filename(p)[:-4]
                zs.writestr(f"{base}.pdf", cards.card_pdf(card, width_mm))
                zq.writestr(f"{base}_QR.png", qr.qr_png(qr.make_payload(event["_id"], p["pid"])))
                bar.progress(i / len(chosen), text=f"Rendering {i}/{len(chosen)}")
        st.session_state["cards_out"] = {
            "name": f"{event['_id']}_{rng[0]}_to_{rng[1]}",
            "pdf": sheets.tobytes(), "singles": singles.getvalue(), "qrs": qrs.getvalue()}
        bar.empty()
    if out := st.session_state.get("cards_out"):
        d1, d2, d3 = st.columns(3)
        d1.download_button("⬇️ Print sheets (A4 PDF)", out["pdf"], f"{out['name']}_A4.pdf", "application/pdf",
                           type="primary", use_container_width=True)
        d2.download_button("⬇️ One PDF per member (ZIP)", out["singles"], f"{out['name']}_cards.zip",
                           "application/zip", use_container_width=True)
        d3.download_button("⬇️ QR codes only (ZIP)", out["qrs"], f"{out['name']}_qr.zip", "application/zip",
                           use_container_width=True)
