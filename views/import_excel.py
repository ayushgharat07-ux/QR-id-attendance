import streamlit as st

from core import extract, importer, repo
from core.ctx import ctx, require_admin

c = ctx()
require_admin(c)
st.subheader("📑 Import members from Excel · एक्सेल मधून सदस्य")
st.caption("Members from the Excel sheet are added as approved members with their own ID and QR code. "
           "Their photos come later from the paper forms: on **Upload Forms**, each form is matched to the "
           "member by mobile number and the photo is attached automatically. Importing the same file again "
           "updates people instead of adding them twice.")

up = st.file_uploader("Excel file (.xlsx)", type=["xlsx"])
if not up:
    st.stop()
data = up.getvalue()
sheets = importer.sheet_names(data)

a, b = st.columns(2)
main_sheet = a.selectbox("Sheet with the member list", sheets, index=0)
table = importer.read_table(data, main_sheet)
a.caption(f"Header found on row {table.header_row}: {', '.join(h for h in table.headers if h)}")

# ---- column mapping
guess = importer.guess_mapping(table.headers)
cols = ["—"] + [f"{i + 1}. {h}" for i, h in enumerate(table.headers)]
with st.expander("Columns (detected automatically — change if wrong)", expanded=False):
    mapping = {}
    grid = st.columns(4)
    for n, (f, label) in enumerate(importer.FIELD_LABELS.items()):
        idx = guess.get(f)
        choice = grid[n % 4].selectbox(label, cols, index=0 if idx is None else idx + 1, key=f"map_{f}")
        mapping[f] = None if choice == "—" else cols.index(choice) - 1

# ---- Marathi names
marathi_sheets = [s for s in sheets if s != main_sheet]
default_src = next((s for s in marathi_sheets if "marathi" in s.lower() or "मराठी" in s), None)
options = (["column"] if mapping.get("name_mr") is not None else []) + \
          (["sheet"] if marathi_sheets else []) + ["ai", "none"]
labels = {"column": "Marathi name column in this sheet", "sheet": "Another sheet (matched by phone number)",
          "ai": "Convert English → Marathi with AI", "none": "Leave empty (type later)"}
src = b.radio("Marathi names from", options, format_func=labels.get,
              index=options.index("column") if "column" in options else (
                  options.index("sheet") if default_src else options.index("ai")))
lookup = None
if src == "sheet":
    mr_sheet = b.selectbox("Sheet with Marathi names", marathi_sheets,
                           index=marathi_sheets.index(default_src) if default_src else 0)
    lookup = importer.marathi_lookup(importer.read_table(data, mr_sheet))
if src != "column":
    mapping["name_mr"] = None

records = importer.build_records(table, mapping, lookup)

# ---- other sheets (e.g. per-category lists): anyone missing from the main sheet?
skip = {main_sheet} | ({mr_sheet} if src == "sheet" else set())
others = importer.check_other_sheets(data, skip, records, lookup)
if others.checked:
    if others.extra:
        st.warning(f"Checked the other sheets ({', '.join(others.checked)}): **{len(others.extra)} people** are "
                   f"in those sheets but not in '{main_sheet}'.")
        st.dataframe([{"Name": r.fields.get("name_en", ""), "Phone": r.fields.get("mobile", ""),
                       "Found in": r.warnings[-1]} for r in others.extra], hide_index=True)
        if st.checkbox("Also import these people", value=True):
            records = records + others.extra
    else:
        st.success(f"Checked the other sheets ({', '.join(others.checked)}, {others.total_rows} rows): "
                   f"everyone in them is already in '{main_sheet}' — nothing extra to import.")

# ---- other settings
a, b, cc = st.columns(3)
ptype = a.radio("Import as", list(repo.TYPE_LABELS), format_func=repo.TYPE_LABELS.get)
vibhag_mr = b.text_input("विभाग (Marathi) for everyone", importer.guess_vibhag(table.title_lines))
vibhag_en = cc.text_input("Division (English) for everyone", "")

missing_mr = [r for r in records if not r.fields.get("name_mr")]
if src == "ai" and missing_mr:
    st.info(f"{len(missing_mr)} names need Marathi. The AI converts them in one go; "
            "you can still correct any name later on the People page.")
    if not extract.ai_configured():
        st.warning("No AI key is set (GEMINI_API_KEY or ANTHROPIC_API_KEY), so AI conversion is not available.")
    elif st.button("🤖 Convert names to Marathi"):
        try:
            with st.spinner("Converting…"):
                st.session_state["translit"] = dict(zip(
                    [r.fields.get("name_en", "") for r in missing_mr],
                    extract.transliterate_names([r.fields.get("name_en", "") for r in missing_mr])))
        except extract.ExtractionError as e:
            st.error(str(e))
    for r in records:
        if not r.fields.get("name_mr") and (mr := st.session_state.get("translit", {}).get(r.fields.get("name_en", ""))):
            r.fields["name_mr"] = mr
            r.warnings = [w for w in r.warnings if w != "no Marathi name"]

# ---- preview
existing = 0
for r in records:
    if importer.find_existing(c.db, c.event["_id"], r.fields):
        existing += 1          # same person already in the app -> will be updated, not added
        continue
    dups = repo.find_duplicates(c.db, c.event["_id"], r.fields)
    if dups and not any(w.startswith("possible duplicate") for w in r.warnings):
        d = dups[0]
        r.warnings.append(f"possible duplicate of {repo.describe(d['person'])} ({repo.DUP_LABELS[d['kind']]})")
warn = [r for r in records if r.warnings]
m = st.columns(4)
m[0].metric("Rows", len(records))
m[1].metric("New", len(records) - existing)
m[2].metric("Already in app (update)", existing)
m[3].metric("With warnings", len(warn))
st.dataframe(
    [{"Name": r.fields.get("name_en", ""), "नाव": r.fields.get("name_mr", ""), "Phone": r.fields.get("mobile", ""),
      "Business": r.fields.get("business", ""), "Group": r.fields.get("group", ""),
      "Old att. no.": r.fields.get("att_no", ""), "Warnings": "; ".join(r.warnings)} for r in records],
    hide_index=True, use_container_width=True, height=360,
)

possible = [r for r in records if any(w.startswith("possible duplicate") for w in r.warnings)]
if possible and st.checkbox(f"Skip the {len(possible)} row(s) marked 'possible duplicate'", value=False,
                            help="Same mobile with a different name is often family (keep them). A very similar name "
                                 "with a different mobile may be the same person with a new number."):
    records = [r for r in records if r not in possible]

if st.button(f"📥 Import {len(records)} members", type="primary", disabled=not records):
    with st.spinner("Importing…"):
        s = importer.import_records(c.db, c.event, records, ptype, vibhag_mr, vibhag_en, c.email)
    st.success(f"Done: {len(s.created)} new members, {len(s.updated)} updated. "
               "Next: upload the paper forms to add photos, then print ID cards.")
    st.page_link("views/upload.py", label="Upload forms (photos) →", icon="📤")
    st.page_link("views/id_cards.py", label="ID cards →", icon="🪪")
