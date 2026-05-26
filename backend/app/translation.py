import logging
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

logger = logging.getLogger(__name__)


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
) -> Tuple[str, Optional[List[str]], str, str, str, str, str]:
    """
    Seven lines (padded): primary, optional comma-separated alts, explanation, example,
    lemma, term pronunciation, translation pronunciation.
    """
    text = _strip_code_fences(raw)
    lines = [ln.strip() for ln in text.splitlines()][:7]
    while len(lines) < 7:
        lines.append("")
    primary, alts_line, explanation, example, lemma, term_pron, trans_pron = lines
    alts: Optional[List[str]] = None
    if alts_line:
        parts = [p.strip() for p in alts_line.split(",") if p.strip()]
        alts = parts or None
    return (
        primary,
        alts,
        explanation.strip(),
        example.strip(),
        lemma.strip(),
        term_pron.strip(),
        trans_pron.strip(),
    )


def call_gemini_translate(
    term: str,
    translate_from: str,
    translate_to: str,
    settings: Settings,
    *,
    learning_lang: str,
    known_lang: str,
) -> Tuple[str, Optional[List[str]], str, str, str, str, str]:
    if not settings.gemini_api_key:
        raise TranslationProviderError("missing_api_key")
    client = genai.Client(api_key=settings.gemini_api_key)
    from_name = language_name(translate_from)
    to_name = language_name(translate_to)
    learning_name = language_name(learning_lang)
    known_name = language_name(known_lang)
    prompt = (
        f"Translate the following {from_name} word or phrase to {to_name}.\n"
        "Reply with:\n"
        f"Line 1: the primary translation only.\n"
        f"Line 2 (optional): comma-separated synonyms or alternative glosses in {to_name}, "
        "or leave this line blank if none.\n"
        f"Line 3: explanation in {known_name} — do NOT use {learning_name} for this line.\n"
        f"Line 4: an example sentence in {learning_name}.\n"
        f"Line 5: lemma in {learning_name}.\n"
        f"Line 6: how to pronunce the term.\n"
        f"Line 7: how to pronunce the translation.\n"
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
            if settings.gemini_log_raw_response:
                logger.info(
                    "gemini_raw_response model=%s term=%r from=%s to=%s\n%s",
                    model,
                    term,
                    translate_from,
                    translate_to,
                    text,
                )
            (
                primary,
                alts,
                explanation,
                example,
                lemma,
                term_pronunciation,
                translation_pronunciation,
            ) = parse_translation_response(text)
            if primary.strip():
                return (
                    primary,
                    alts,
                    explanation,
                    example,
                    lemma,
                    term_pronunciation,
                    translation_pronunciation,
                )
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
    ``user_speaks_lang`` / ``user_learning_lang`` come from the UI study pair (with / learning).

    ``TranslationCache`` rows are keyed by the real languages of the term and translation
    (``source_lang`` = language of ``term``, ``target_lang`` = language of ``primary_translation``),
    not by the user's study-pair labels.
    """
    term = normalize_term(raw_term)
    if not term:
        raise ValueError("Empty term")

    translate_from, translate_to = auto_direction_for_pair(
        raw_term, user_speaks_lang, user_learning_lang
    )

    row = db.execute(
        select(TranslationCache).where(
            TranslationCache.term == term,
            TranslationCache.source_lang == translate_from,
            TranslationCache.target_lang == translate_to,
        )
    ).scalar_one_or_none()

    if row is not None and row.primary_translation.strip():
        return row, "global_cache"
    (
        primary,
        alts,
        explanation,
        example,
        lemma,
        term_pronunciation,
        translation_pronunciation,
    ) = call_gemini_translate(
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
            source_lang=translate_from,
            target_lang=translate_to,
            primary_translation=primary.strip(),
            alt_translations=dumps_alt(alts),
            translation_explanation=explanation or None,
            example_sentence=example or None,
            lemma=lemma or None,
            term_pronunciation=term_pronunciation or None,
            translation_pronunciation=translation_pronunciation or None,
        )
        db.add(row)
        db.flush()
    else:
        row.primary_translation = primary.strip()
        row.alt_translations = dumps_alt(alts)
        row.translation_explanation = explanation or None
        row.example_sentence = example or None
        row.lemma = lemma or None
        row.term_pronunciation = term_pronunciation or None
        row.translation_pronunciation = translation_pronunciation or None
        db.flush()

    return row, "gemini"


def ensure_vocabulary_translation(
    db: Session,
    vocab: Vocabulary,
    settings: Settings,
) -> TranslationCache:
    """Return translation cache for a vocabulary row, fetching via Gemini if missing."""
    if vocab.cache is not None and (vocab.cache.primary_translation or "").strip():
        return vocab.cache

    raw = (vocab.display_term or vocab.term).strip()
    cache, _source = get_or_create_global_translation(
        db,
        raw,
        vocab.source_lang,
        vocab.target_lang,
        settings,
    )
    vocab.cache_id = cache.id
    db.flush()
    db.refresh(vocab)
    return cache


def upsert_user_vocabulary(
    db: Session,
    user_id: int,
    raw_term: str,
    cache: TranslationCache,
    user_speaks_lang: str,
    user_learning_lang: str,
) -> Vocabulary:
    term = cache.term
    display = raw_term.strip() if raw_term.strip() else term

    row = db.execute(
        select(Vocabulary).where(
            Vocabulary.user_id == user_id,
            Vocabulary.term == term,
            Vocabulary.source_lang == user_speaks_lang,
            Vocabulary.target_lang == user_learning_lang,
        )
    ).scalar_one_or_none()

    if row is None:
        row = Vocabulary(
            user_id=user_id,
            term=term,
            display_term=display,
            source_lang=user_speaks_lang,
            target_lang=user_learning_lang,
            cache_id=cache.id,
            priority=100,
        )
        db.add(row)
    else:
        row.display_term = display
        row.cache_id = cache.id
        row.priority = min(PRIORITY_MAX, row.priority + PRIORITY_DELTA)
    db.flush()
    db.refresh(row)
    return row
