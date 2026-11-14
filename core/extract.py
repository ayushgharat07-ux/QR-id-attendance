"""Read a scanned registration form with an AI vision model (Google Gemini or Anthropic Claude)
and return structured, bilingual data."""
from __future__ import annotations

import base64
import re
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from core.config import secret

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You read paper registration forms for community events in Maharashtra, India. \
Forms are scanned or photographed; they may be handwritten \
or typed, in Marathi (Devanagari) or English, or a mix.

The form has these labelled rows (labels are printed in Marathi):
- श्रीसदस्याचे नाव  -> the person's full name (often starts with an honorific such as "श्री." / "Shri")
- भ्रमणध्वनी क्रमांक -> mobile number
- विभाग             -> division / area (e.g. "कोन, ता. पनवेल")
- राहण्याचा पत्ता    -> residential address
There is usually a passport-size photo near the top right. Ignore the printed event header, \
date, time and venue rows; only extract the person's own details.

For every text field give three versions:
- original: exactly what is written, in the script it was written in.
- marathi: the value in Marathi (Devanagari). If it was written in English, TRANSLITERATE it \
(keep the same sound, do not translate meaning) — e.g. "Hiranandani Fortune City" -> \
"हिरानंदानी फॉर्च्युन सिटी". Use Devanagari digits (०-९) in this version.
- english: the value in English (Latin script). If it was written in Marathi, transliterate \
names and places into normal Indian English spelling with Title Case (e.g. "श्री. मितेश मधुकर \
मुरुडकर" -> "Shri. Mitesh Madhukar Murudkar"). Common address abbreviations: मु. = "At", \
पो. = "Post", ता. = "Tal.", जि. = "Dist.". Use Western digits (0-9) in this version.
Keep the honorific if it is written. Fix obvious spacing, but never invent missing information; \
use an empty string for a blank field. Set confident=false when handwriting is unclear or \
you guessed any part of the value.

mobile: digits only, Western numerals, as written (convert Devanagari digits).

photo_box: the passport photo's bounding box as fractions of the full image width/height \
(x, y = top-left corner). Use zeros if there is no photo.

If the image is not this kind of registration form, set is_registration_form=false and \
explain in notes."""


class TextField(BaseModel):
    original: str
    marathi: str
    english: str
    confident: bool


class PhotoBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class FormExtraction(BaseModel):
    is_registration_form: bool
    source_language: Literal["marathi", "english", "mixed", "unknown"]
    name: TextField
    mobile: str
    mobile_confident: bool
    vibhag: TextField
    address: TextField
    photo_present: bool
    photo_box: PhotoBox
    notes: str = Field(description="Anything the reviewer should double-check; empty if nothing.")


_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_mobile(raw: str) -> str:
    digits = re.sub(r"\D", "", (raw or "").translate(_DEV_DIGITS))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def _client() -> anthropic.Anthropic:
    key = secret("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


class ExtractionError(Exception):
    pass


class NamePair(BaseModel):
    english: str
    marathi: str


class NameList(BaseModel):
    names: list[NamePair]


TRANSLIT_PROMPT = """Transliterate these Indian (mostly Maharashtrian) personal names from English into \
Marathi Devanagari, keeping the sound (do not translate meanings). Use the spelling a Marathi speaker \
would write, e.g. "Kalekar" -> "काळेकर", "Mhatre" -> "म्हात्रे". Titles: Mr./Shri -> "श्री.", \
Mrs./Smt -> "सौ.", Miss/Kumari/Kumar -> "कु.". Return every name, in the same order."""

FORM_INSTRUCTION = "Extract this person's details from the form."

# ---------------------------------------------------------------- provider choice
# AI_PROVIDER = "gemini" | "anthropic". If not set: Gemini when GEMINI_API_KEY exists, else Anthropic.
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"


def provider() -> str | None:
    p = (secret("AI_PROVIDER") or "").strip().lower()
    if p in ("gemini", "anthropic"):
        return p
    if secret("GEMINI_API_KEY"):
        return "gemini"
    if secret("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def ai_configured() -> bool:
    p = provider()
    return bool(p and secret("GEMINI_API_KEY" if p == "gemini" else "ANTHROPIC_API_KEY"))


def provider_label() -> str:
    p = provider()
    if p == "gemini":
        return f"Google Gemini ({secret('GEMINI_MODEL') or GEMINI_DEFAULT_MODEL})"
    if p == "anthropic":
        return f"Anthropic Claude ({MODEL})"
    return "not configured"


def max_parallel() -> int:
    """Forms read at the same time. Gemini's free tier has low per-minute limits."""
    return 2 if provider() == "gemini" else 4


def _require_ai() -> str:
    if not ai_configured():
        raise ExtractionError("AI reading is not configured (add GEMINI_API_KEY or ANTHROPIC_API_KEY). "
                              "Fill in the details by hand.")
    return provider()


# ---------------------------------------------------------------- public API
def extract_form(jpeg_bytes: bytes) -> FormExtraction:
    """Send one form page (JPEG) to the configured AI and return validated, bilingual fields."""
    p = _require_ai()
    result = _gemini_form(jpeg_bytes) if p == "gemini" else _anthropic_form(jpeg_bytes)
    result.mobile = normalize_mobile(result.mobile)
    return result


def transliterate_names(names: list[str], batch_size: int = 80) -> list[str]:
    """English names -> Marathi, in order. Raises ExtractionError if the AI is unavailable."""
    p = _require_ai()
    out: list[str] = []
    for i in range(0, len(names), batch_size):
        chunk = names[i:i + batch_size]
        pairs = (_gemini_names if p == "gemini" else _anthropic_names)(chunk).names
        got = {pr.english.strip(): pr.marathi.strip() for pr in pairs}
        for j, name in enumerate(chunk):  # match by text, fall back to position
            out.append(got.get(name.strip()) or (pairs[j].marathi.strip() if j < len(pairs) else ""))
    return out


# ---------------------------------------------------------------- Anthropic
def _anthropic_call(system: str, content, schema):
    try:
        response = _client().beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
    except anthropic.AuthenticationError as e:
        raise ExtractionError("Anthropic API key is invalid or missing.") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Rate limited by the AI service; wait a minute and retry.") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"AI service error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Could not reach the AI service (network).") from e
    except anthropic.AnthropicError as e:  # anything else from the SDK (config, parsing)
        raise ExtractionError(f"AI reading failed: {e}") from e
    if response.stop_reason == "refusal":
        raise ExtractionError("The AI declined to read this image.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise ExtractionError("The AI response was incomplete; please retry.")
    return response.parsed_output


def _anthropic_form(jpeg_bytes: bytes) -> FormExtraction:
    image_b64 = base64.standard_b64encode(jpeg_bytes).decode("utf-8")
    return _anthropic_call(SYSTEM_PROMPT, [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
        {"type": "text", "text": FORM_INSTRUCTION},
    ], FormExtraction)


def _anthropic_names(chunk: list[str]) -> NameList:
    return _anthropic_call(TRANSLIT_PROMPT, "\n".join(chunk), NameList)


# ---------------------------------------------------------------- Google Gemini
def _gemini_client():
    from google import genai

    return genai.Client(api_key=secret("GEMINI_API_KEY"))


def _gemini_call(system: str, contents: list, schema, retries: int = 4, sleep=None):
    """generate_content with JSON output constrained to `schema`. Retries on rate limits (429),
    which the free tier hits easily, and on temporary server errors."""
    import time

    from google.genai import errors, types

    sleep = sleep or time.sleep
    client = _gemini_client()
    config = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                         response_schema=schema)
    model = secret("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    for attempt in range(retries + 1):
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            break
        except errors.APIError as e:
            code = e.code or 0
            if (code == 429 or code >= 500) and attempt < retries:
                sleep(min(60, 8 * 2 ** attempt))  # 8, 16, 32, 60 s
                continue
            if code == 429:
                raise ExtractionError("Gemini free-tier limit reached. Wait a few minutes (or until tomorrow if "
                                      "the daily limit is used up) and press Re-read.") from e
            if code in (400, 401, 403) and "key" in str(e.message or "").lower():
                raise ExtractionError("Gemini API key is invalid.") from e
            if code == 404:
                raise ExtractionError(f"Gemini model '{model}' was not found; set GEMINI_MODEL in secrets.") from e
            raise ExtractionError(f"Gemini error ({code}): {e.message}") from e
        except Exception as e:  # network problems etc.
            if attempt < retries:
                sleep(5)
                continue
            raise ExtractionError(f"Could not reach Gemini: {e}") from e

    parsed = response.parsed
    if isinstance(parsed, schema):
        return parsed
    try:
        return schema.model_validate_json(response.text or "")
    except Exception as e:
        reason = ""
        if response.candidates and response.candidates[0].finish_reason:
            reason = f"({response.candidates[0].finish_reason})"
        raise ExtractionError(f"Gemini returned an unreadable answer {reason}; please retry.".replace("  ", " ")) from e


def _gemini_form(jpeg_bytes: bytes) -> FormExtraction:
    from google.genai import types

    return _gemini_call(SYSTEM_PROMPT, [types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                                        FORM_INSTRUCTION], FormExtraction)


def _gemini_names(chunk: list[str]) -> NameList:
    return _gemini_call(TRANSLIT_PROMPT, ["\n".join(chunk)], NameList)


def to_person_fields(x: FormExtraction) -> dict:
    return {
        "name_mr": x.name.marathi, "name_en": x.name.english,
        "vibhag_mr": x.vibhag.marathi, "vibhag_en": x.vibhag.english,
        "address_mr": x.address.marathi, "address_en": x.address.english,
        "mobile": x.mobile,
    }
