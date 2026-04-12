import re
from typing import List, Literal, Optional, Tuple

from google import genai
from google.genai import errors as genai_errors
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import TranslationCache, Vocabulary, dumps_alt, normalize_term


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_translation_response(raw: str) -> Tuple[str, Optional[List[str]]]:
    text = _strip_code_fences(raw)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "", None
    primary = lines[0]
    alts = None
    if len(lines) > 1:
        parts = [p.strip() for p in lines[1].split(",") if p.strip()]
        alts = parts or None
    return primary, alts


def call_gemini_translate(term: str, settings: Settings) -> Tuple[str, Optional[List[str]]]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = (
        "Translate the following English word or phrase to Simplified Chinese.\n"
        "Reply with:\n"
        "Line 1: the primary translation only.\n"
        "Line 2 (optional): comma-separated synonyms or alternative glosses.\n"
        "Do not add explanations, labels, or markdown.\n\n"
        f"English: {term}\n"
    )
    try:
        resp = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config={"temperature": 0.2},
        )
    except genai_errors.ClientError as e:
        if getattr(e, "status_code", None) == 429:
            raise RuntimeError("Gemini rate limited (429)") from e
        raise
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "resource exhausted" in msg:
            raise RuntimeError("Gemini rate limited") from e
        raise
    text = getattr(resp, "text", None) or ""
    if not text.strip():
        raise RuntimeError("Empty response from Gemini")
    return parse_translation_response(text)


def get_or_create_global_translation(
    db: Session,
    raw_term: str,
    source_lang: str,
    target_lang: str,
    settings: Settings,
) -> tuple[TranslationCache, Literal["global_cache", "gemini"]]:
    term = normalize_term(raw_term)
    if not term:
        raise ValueError("Empty term")

    row = db.execute(
        select(TranslationCache).where(
            TranslationCache.term == term,
            TranslationCache.source_lang == source_lang,
            TranslationCache.target_lang == target_lang,
        )
    ).scalar_one_or_none()

    if row is not None and row.primary_translation.strip():
        return row, "global_cache"

    primary, alts = call_gemini_translate(raw_term.strip(), settings)
    if not primary.strip():
        raise RuntimeError("Could not parse translation")

    if row is None:
        row = TranslationCache(
            term=term,
            source_lang=source_lang,
            target_lang=target_lang,
            primary_translation=primary.strip(),
            alt_translations=dumps_alt(alts),
        )
        db.add(row)
        db.flush()
    else:
        row.primary_translation = primary.strip()
        row.alt_translations = dumps_alt(alts)
        db.flush()

    return row, "gemini"


def upsert_user_vocabulary(
    db: Session,
    user_id: int,
    raw_term: str,
    cache: TranslationCache,
) -> Vocabulary:
    term = cache.term
    display = raw_term.strip() if raw_term.strip() else term

    row = db.execute(
        select(Vocabulary).where(
            Vocabulary.user_id == user_id,
            Vocabulary.term == term,
            Vocabulary.source_lang == cache.source_lang,
            Vocabulary.target_lang == cache.target_lang,
        )
    ).scalar_one_or_none()

    if row is None:
        row = Vocabulary(
            user_id=user_id,
            term=term,
            display_term=display,
            source_lang=cache.source_lang,
            target_lang=cache.target_lang,
            primary_translation=cache.primary_translation,
            alt_translations=cache.alt_translations,
            priority=100,
        )
        db.add(row)
    else:
        row.display_term = display
        row.primary_translation = cache.primary_translation
        row.alt_translations = cache.alt_translations
    db.flush()
    db.refresh(row)
    return row
