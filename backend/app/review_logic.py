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


def pick_next_review(db: Session, user_id: int) -> Optional[Vocabulary]:
    q = (
        select(Vocabulary)
        .where(Vocabulary.user_id == user_id, Vocabulary.priority > 0)
        .order_by(Vocabulary.priority.desc(), Vocabulary.last_reviewed_at.asc().nulls_first())
        .limit(1)
    )
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
