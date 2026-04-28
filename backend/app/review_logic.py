import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Vocabulary
from app.gemini_verify import (
    can_use_gemini_verify,
    record_verify_success,
    set_verify_cooldown,
    verify_explanation_gemini,
)

PRIORITY_START = 100
PRIORITY_MAX = 200
PRIORITY_DELTA = 35


def _normalize_explanation(s: str) -> str:
    s = s.strip().lower()
    return " ".join(s.split())


def _normalize_zh(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[\s\u3000]+", "", s)
    return s


def offline_verify(explanation: str, primary: str, alts: Optional[List[str]]) -> bool:
    ex = _normalize_explanation(explanation)
    if not ex:
        return False
    candidates = [primary] + (alts or [])
    for c in candidates:
        if not c:
            continue
        c_low = _normalize_explanation(c)
        c_zh = _normalize_zh(c)
        ex_zh = _normalize_zh(explanation)
        if ex == c_low or ex in c_low or c_low in ex:
            return True
        if c_zh and ex_zh and (ex_zh in c_zh or c_zh in ex_zh):
            return True
        if SequenceMatcher(None, ex, c_low).ratio() >= 0.88:
            return True
        if c_zh and ex_zh and SequenceMatcher(None, ex_zh, c_zh).ratio() >= 0.88:
            return True
    return False


def _contains_script(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text))


def looks_like_language(text: str, target_lang: str) -> bool:
    t = text.strip()
    if not t:
        return False

    # Script-based checks for languages with clear scripts.
    if target_lang == "zh":
        return _contains_script(t, r"[\u4e00-\u9fff]")
    if target_lang == "hi":
        return _contains_script(t, r"[\u0900-\u097F]")
    if target_lang == "bn":
        return _contains_script(t, r"[\u0980-\u09FF]")
    if target_lang == "ar":
        return _contains_script(t, r"[\u0600-\u06FF]")
    if target_lang == "ur":
        return _contains_script(t, r"[\u0600-\u06FF]")
    if target_lang == "ru":
        return _contains_script(t, r"[\u0400-\u04FF]")

    # For Latin-script languages (en/es/fr/pt), require at least one Latin letter.
    if target_lang in {"en", "es", "fr", "pt"}:
        return _contains_script(t, r"[A-Za-z]")

    return True


def pick_next_review(
    db: Session, user_id: int, source_lang: Optional[str] = None, target_lang: Optional[str] = None
) -> Optional[Vocabulary]:
    q = select(Vocabulary).where(Vocabulary.user_id == user_id, Vocabulary.priority > 0)
    if source_lang:
        q = q.where(Vocabulary.source_lang == source_lang)
    if target_lang:
        q = q.where(Vocabulary.target_lang == target_lang)
    q = q.order_by(Vocabulary.priority.desc(), Vocabulary.last_reviewed_at.asc().nulls_first()).limit(1)
    return db.execute(q).scalar_one_or_none()


def apply_review_result(
    db: Session,
    row: Vocabulary,
    correct: bool,
) -> int:
    if correct:
        row.priority = max(0, row.priority - PRIORITY_DELTA)
        row.times_correct += 1
    else:
        row.priority = min(PRIORITY_MAX, row.priority + PRIORITY_DELTA)
        row.times_wrong += 1

    row.last_reviewed_at = datetime.now(timezone.utc)
    db.flush()
    db.refresh(row)
    return row.priority


def grade_review_answer(
    db: Session,
    user_id: int,
    vocab_id: int,
    explanation: str,
    settings: Settings,
) -> Tuple[bool, str, str, int]:
    row = db.get(Vocabulary, vocab_id)
    if row is None or row.user_id != user_id:
        raise ValueError("not_found")

    primary = row.primary_translation or ""
    alts = row.alt_translations or []
    canonical = primary

    grading_mode = "offline"
    correct: bool

    if not looks_like_language(explanation, row.target_lang):
        correct = False
        grading_mode = "language_mismatch"
        new_priority = apply_review_result(db, row, correct)
        return correct, canonical, grading_mode, new_priority

    use_gemini = can_use_gemini_verify(db, settings)
    if use_gemini:
        try:
            g = verify_explanation_gemini(
                row.display_term or row.term,
                primary,
                alts if isinstance(alts, list) else [],
                explanation,
                settings,
            )
            if g is not None:
                correct = g
                grading_mode = "gemini"
                record_verify_success(db, settings)
            else:
                correct = offline_verify(explanation, primary, alts if isinstance(alts, list) else [])
        except RuntimeError as e:
            if str(e) == "verify_429":
                set_verify_cooldown(db, settings)
            correct = offline_verify(explanation, primary, alts if isinstance(alts, list) else [])
    else:
        correct = offline_verify(explanation, primary, alts if isinstance(alts, list) else [])

    new_priority = apply_review_result(db, row, correct)
    return correct, canonical, grading_mode, new_priority
