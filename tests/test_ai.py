"""AI provider selection and the Gemini path (with a fake client — no network, no key needed)."""
import io
from types import SimpleNamespace

import pytest
from google.genai import errors
from openpyxl import Workbook

from conftest import ROOT
from core import extract, importer
from test_core import fake_extraction

REAL_XLSX = sorted((ROOT / "samples").glob("*.xlsx")) if (ROOT / "samples").exists() else []


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("AI_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_MODEL"):
        monkeypatch.setenv(k, "")  # env wins over any local secrets.toml
    return monkeypatch


def test_provider_selection(clean_env):
    assert extract.provider() is None and not extract.ai_configured()
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert extract.provider() == "anthropic"
    clean_env.setenv("GEMINI_API_KEY", "AIza-x")
    assert extract.provider() == "gemini" and extract.ai_configured()  # Gemini preferred when both exist
    clean_env.setenv("AI_PROVIDER", "anthropic")
    assert extract.provider() == "anthropic"
    clean_env.setenv("AI_PROVIDER", "gemini")
    clean_env.setenv("GEMINI_API_KEY", "")
    assert extract.provider() == "gemini" and not extract.ai_configured()  # chosen but no key


class FakeGemini:
    """Stands in for genai.Client: fails with the given HTTP codes first, then answers."""

    def __init__(self, fail_codes, answer):
        self.fail_codes, self.answer, self.calls = list(fail_codes), answer, []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.fail_codes:
            code = self.fail_codes.pop(0)
            raise errors.ClientError(code, {"error": {"code": code, "message": "quota" if code == 429 else "bad",
                                                      "status": "X"}})
        return self.answer


def _use_fake(monkeypatch, fake):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-x")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setattr(extract, "_gemini_client", lambda: fake)
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_gemini_reads_form_and_retries_rate_limit(clean_env):
    x = fake_extraction(b"")
    x.mobile = "+91 80107 06467"
    fake = FakeGemini([429, 503], SimpleNamespace(parsed=x, text=x.model_dump_json(), candidates=[]))
    _use_fake(clean_env, fake)
    out = extract.extract_form(b"\xff\xd8jpeg")
    assert out.name.english == "Shri. Sandesh Laxman Shinde"
    assert out.mobile == "8010706467"              # normalised after the AI answer
    assert len(fake.calls) == 3                    # 429, 503, then success
    call = fake.calls[-1]
    assert call["model"] == extract.GEMINI_DEFAULT_MODEL
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is extract.FormExtraction
    assert call["contents"][0].inline_data.mime_type == "image/jpeg"


def test_gemini_falls_back_to_text_when_parsed_missing(clean_env):
    x = fake_extraction(b"")
    _use_fake(clean_env, FakeGemini([], SimpleNamespace(parsed=None, text=x.model_dump_json(), candidates=[])))
    assert extract.extract_form(b"jpeg").vibhag.marathi == "कोन, ता. पनवेल"


def test_gemini_errors_become_clear_messages(clean_env):
    _use_fake(clean_env, FakeGemini([429] * 10, None))
    with pytest.raises(extract.ExtractionError, match="free-tier limit"):
        extract.extract_form(b"jpeg")
    _use_fake(clean_env, FakeGemini([404], None))
    with pytest.raises(extract.ExtractionError, match="not found"):
        extract.extract_form(b"jpeg")


def test_gemini_transliterate(clean_env):
    answer = extract.NameList(names=[extract.NamePair(english="Mr. Vivek Kore", marathi="श्री. विवेक कोरे")])
    _use_fake(clean_env, FakeGemini([], SimpleNamespace(parsed=answer, text="", candidates=[])))
    assert extract.transliterate_names(["Mr. Vivek Kore"]) == ["श्री. विवेक कोरे"]


# ---------------------------------------------------------------- other sheets in the Excel
def test_other_sheets_find_people_missing_from_main():
    wb = Workbook()
    head = ["Sr", "Name", "Phone No.", "Business"]
    main = wb.active
    main.title = "Master"
    main.append(head)
    main.append([1, "Mr. A One", 9000000001, "Shop"])
    cat = wb.create_sheet("Retailer")
    cat.append(head)
    cat.append([1, "Mr. A One", 9000000001, "Shop"])        # same person: not extra
    cat.append([2, "Mrs. B Two", 9000000002, "Tailor"])     # only here: extra
    wb.create_sheet("Notes").append(["just some notes"])    # no Name column: ignored
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    t = importer.read_table(data, "Master")
    recs = importer.build_records(t, importer.guess_mapping(t.headers))
    o = importer.check_other_sheets(data, {"Master"}, recs, None)
    assert o.checked == ["Retailer"] and o.total_rows == 2
    assert [r.fields["name_en"] for r in o.extra] == ["Mrs. B Two"]


@pytest.mark.skipif(not REAL_XLSX, reason="put the members Excel in ./samples to run")
def test_real_directory_category_sheets_add_nobody():
    data = REAL_XLSX[0].read_bytes()
    t = importer.read_table(data, "Master")
    recs = importer.build_records(t, importer.guess_mapping(t.headers))
    o = importer.check_other_sheets(data, {"Master", "Master (Marathi Names)"}, recs, None)
    assert set(o.checked) == {"Manufacturer", "Trader", "Retailer", "Service"}
    assert o.total_rows == 219 and o.extra == []
