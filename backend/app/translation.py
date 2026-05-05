import re
from typing import List, Literal, Optional, Tuple

from google import genai
from google.genai import errors as genai_errors
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import TranslationCache, Vocabulary, dumps_alt, normalize_term
from app.languages import auto_direction_for_pair, language_name
from app.review_logic import PRIORITY_MAX, PRIORITY_DELTA


class TranslationProviderError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_translation_response(
    raw: str,
) -> Tuple[str, Optional[List[str]], str, str, str]:
    """
    Five lines (padded): primary, explanation, example, lemma, optional comma-separated alts.

    Glosses are last so a skipped optional line does not shift the fixed fields.
    """
    text = _strip_code_fences(raw)
    lines = [ln.strip() for ln in text.splitlines()][:5]
    while len(lines) < 5:
        lines.append("")
    primary, explanation, example, lemma, alts_line = lines
    alts: Optional[List[str]] = None
    if alts_line:
        parts = [p.strip() for p in alts_line.split(",") if p.strip()]
        alts = parts or None
    return primary, alts, explanation.strip(), example.strip(), lemma.strip()


def call_gemini_translate(
    term: str,
    translate_from: str,
    translate_to: str,
    settings: Settings,
    *,
    learning_lang: str,
    known_lang: str,
) -> Tuple[str, Optional[List[str]], str, str, str]:
    if not settings.gemini_api_key:
        raise TranslationProviderError("missing_api_key")
    client = genai.Client(api_key=settings.gemini_api_key)
    from_name = language_name(translate_from)
    to_name = language_name(translate_to)
    learning_name = language_name(learning_lang)
    known_name = language_name(known_lang)
    prompt = (
        f"Translate the following {from_name} word or phrase to {to_name}.\n"
        "Reply with exactly these lines in order:\n"
        f"Line 1: the primary translation only.\n"
        f"Line 2: explanation in {known_name} of the translation.\n"
        f"Line 3: one natural example sentence written in {learning_name} "
        f"Line 4: dictionary headword (lemma) of the expression in {learning_name}.\n"
        f"Line 5 (optional): comma-separated synonyms or alternative glosses in {to_name}, "
        "or leave this line blank if none.\n"
        "Do not add labels or markdown.\n\n"
        f"{from_name}: {term}\n"
    )
    models = settings.preferred_gemini_models()
    if not models:
        raise TranslationProviderError("no_available_model")

    saw_rate_limit = False
    saw_service_unavailable = False

    for model in models:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"temperature": 0.2},
            )
            text = getattr(resp, "text", None) or ""
            if not text.strip():
                continue
            primary, alts, explanation, example, lemma = parse_translation_response(text)
            if primary.strip():
                return primary, alts, explanation, example, lemma
        except genai_errors.ClientError as e:
            status_code = getattr(e, "status_code", None)
            if status_code == 429:
                saw_rate_limit = True
                continue
            if status_code == 503:
                saw_service_unavailable = True
                continue
            # Common for unavailable/unsupported model IDs.
            if status_code in (400, 404):
                continue
            continue
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "resource exhausted" in msg:
                saw_rate_limit = True
                continue
            if "503" in msg or "unavailable" in msg or "high demand" in msg:
                saw_service_unavailable = True
                continue
            continue

    if saw_rate_limit:
        raise TranslationProviderError("rate_limited")
    if saw_service_unavailable:
        raise TranslationProviderError("service_unavailable")
    raise TranslationProviderError("no_available_model")


def get_or_create_global_translation(
    db: Session,
    raw_term: str,
    user_speaks_lang: str,
    user_learning_lang: str,
    settings: Settings,
) -> tuple[TranslationCache, Literal["global_cache", "gemini"]]:
    """
    ``user_speaks_lang`` / ``user_learning_lang`` match the UI (with / learning) and are
    always how rows are stored. Auto-direction only affects the Gemini translate step.
    """
    term = normalize_term(raw_term)
    if not term:
        raise ValueError("Empty term")

    row = db.execute(
        select(TranslationCache).where(
            TranslationCache.term == term,
            TranslationCache.source_lang == user_speaks_lang,
            TranslationCache.target_lang == user_learning_lang,
        )
    ).scalar_one_or_none()

    if row is not None and row.primary_translation.strip():
        return row, "global_cache"

    translate_from, translate_to = auto_direction_for_pair(
        raw_term, user_speaks_lang, user_learning_lang
    )
    primary, alts, explanation, example, lemma = call_gemini_translate(
        raw_term.strip(),
        translate_from,
        translate_to,
        settings,
        learning_lang=user_learning_lang,
        known_lang=user_speaks_lang,
    )
    if not primary.strip():
        raise TranslationProviderError("parse_failed")

    if row is None:
        row = TranslationCache(
            term=term,
            source_lang=user_speaks_lang,
            target_lang=user_learning_lang,
            primary_translation=primary.strip(),
            alt_translations=dumps_alt(alts),
            translation_explanation=explanation or None,
            example_sentence=example or None,
            lemma=lemma or None,
        )
        db.add(row)
        db.flush()
    else:
        row.primary_translation = primary.strip()
        row.alt_translations = dumps_alt(alts)
        row.translation_explanation = explanation or None
        row.example_sentence = example or None
        row.lemma = lemma or None
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
            translation_explanation=cache.translation_explanation,
            example_sentence=cache.example_sentence,
            lemma=cache.lemma,
            priority=100,
        )
        db.add(row)
    else:
        row.display_term = display
        row.primary_translation = cache.primary_translation
        row.alt_translations = cache.alt_translations
        row.translation_explanation = cache.translation_explanation
        row.example_sentence = cache.example_sentence
        row.lemma = cache.lemma
        row.priority = min(PRIORITY_MAX, row.priority + PRIORITY_DELTA)
    db.flush()
    db.refresh(row)
    return row
