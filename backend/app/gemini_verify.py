import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from google import genai
from google.genai import errors as genai_errors
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import QuotaState, utc_today_str


def _quota_row(db: Session) -> QuotaState:
    row = db.get(QuotaState, 1)
    if row is None:
        row = QuotaState(id=1, verify_count=0)
        db.add(row)
        db.flush()
    return row


def can_use_gemini_verify(db: Session, settings: Settings) -> bool:
    if not settings.gemini_api_key:
        return False
    row = _quota_row(db)
    now = datetime.now(timezone.utc)
    if row.verify_cooldown_until and row.verify_cooldown_until > now:
        return False
    today = utc_today_str()
    if settings.gemini_daily_verify_limit > 0:
        if row.verify_count_date != today:
            return True
        if row.verify_count >= settings.gemini_daily_verify_limit:
            return False
    return True


def record_verify_success(db: Session, settings: Settings) -> None:
    row = _quota_row(db)
    today = utc_today_str()
    if row.verify_count_date != today:
        row.verify_count_date = today
        row.verify_count = 0
    row.verify_count += 1
    db.flush()


def set_verify_cooldown(db: Session, settings: Settings) -> None:
    row = _quota_row(db)
    row.verify_cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=settings.verify_cooldown_seconds)
    db.flush()


def _extract_json_object(text: str) -> Optional[Dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[^{}]*\"correct\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def verify_explanation_gemini(
    term: str,
    reference_zh: str,
    alt_translations: Optional[List[str]],
    explanation: str,
    settings: Settings,
) -> Optional[bool]:
    """
    Returns True/False if Gemini responded; None on failure to parse or API error
    (caller should fall back to offline).
    """
    if not settings.gemini_api_key:
        return None
    client = genai.Client(api_key=settings.gemini_api_key)
    alts = ", ".join(alt_translations or [])
    prompt = (
        "You grade whether a learner's explanation of an English word/phrase is correct.\n"
        "Reference Simplified Chinese gloss (canonical): "
        f"{reference_zh}\n"
    )
    if alts:
        prompt += f"Alternative acceptable glosses: {alts}\n"
    prompt += (
        f"English term: {term}\n"
        f"Learner explanation (may be Chinese or English): {explanation}\n\n"
        'Reply with JSON only, no markdown: {"correct": true or false, "reason": "short phrase"}\n'
        "Mark correct if the explanation captures the same meaning as the reference, even with different wording.\n"
    )
    models = settings.preferred_gemini_models()
    if not models:
        return None

    saw_429 = False
    for model in models:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"temperature": 0.1},
            )
            text = getattr(resp, "text", None) or ""
            data = _extract_json_object(text)
            if not data or "correct" not in data:
                continue
            return bool(data["correct"])
        except genai_errors.ClientError as e:
            if getattr(e, "status_code", None) == 429:
                saw_429 = True
            # Try the next model on all client errors.
            continue
        except Exception as e:
            msg = str(e).lower()
            if "429" in msg or "resource exhausted" in msg:
                saw_429 = True
            continue

    if saw_429:
        raise RuntimeError("verify_429")
    return None
