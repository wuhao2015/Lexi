from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import TranslationCache, Vocabulary, get_session_local, normalize_term
from app.languages import looks_like_language

logger = logging.getLogger(__name__)


def _normalized_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[\s\u3000]+", " ", text)
    text = re.sub(r"[.,;:!?()\\[\\]{}\"'`~_-]+", "", text)
    return text.strip()


def _normalized_primary(value: str) -> str:
    return _normalized_text(value)


def _distinct_alts(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = (raw or "").strip()
        if not item:
            continue
        key = _normalized_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _merged_alts(*alt_lists: object) -> list[str] | None:
    items: list[str] = []
    for alt in alt_lists:
        if isinstance(alt, list):
            items.extend(str(v) for v in alt)
    merged = _distinct_alts(items)
    return merged or None


def _is_bad_cache_row(row: TranslationCache) -> bool:
    if row.source_lang == row.target_lang:
        return False
    return _normalized_text(row.term) == _normalized_primary(row.primary_translation)


def _quality_tuple(row: TranslationCache) -> tuple[int, int, int, int, int, float]:
    primary = (row.primary_translation or "").strip()
    has_primary = 1 if primary else 0
    script_match = 1 if primary and looks_like_language(primary, row.target_lang) else 0
    alt_count = len(_merged_alts(row.alt_translations) or [])
    primary_len = len(primary)
    extras = sum(
        1
        for v in (
            row.translation_explanation,
            row.example_sentence,
            row.lemma,
            row.term_pronunciation,
            row.translation_pronunciation,
        )
        if v and str(v).strip()
    )
    updated_ts = (
        row.updated_at.replace(tzinfo=timezone.utc).timestamp()
        if isinstance(row.updated_at, datetime) and row.updated_at.tzinfo is None
        else row.updated_at.timestamp()
        if isinstance(row.updated_at, datetime)
        else 0.0
    )
    return (has_primary, script_match, alt_count, primary_len, extras, updated_ts)


def _pick_winner(rows: list[TranslationCache], settings: Settings) -> TranslationCache:
    if settings.cache_conflict_policy != "keep_highest_quality_score":
        logger.warning(
            "cache_conflict_policy '%s' unsupported, falling back to keep_highest_quality_score",
            settings.cache_conflict_policy,
        )
    return max(rows, key=_quality_tuple)


@dataclass
class MaintenanceSummary:
    bad_cache_deleted: int = 0
    bad_vocab_deleted: int = 0
    merged_group_count: int = 0
    merged_cache_deleted: int = 0
    vocab_repointed: int = 0
    vocab_merged: int = 0


def _repoint_vocabulary_rows(
    db: Session, loser: TranslationCache, winner: TranslationCache, summary: MaintenanceSummary
) -> None:
    loser_rows = db.execute(
        select(Vocabulary).where(
            Vocabulary.term == loser.term,
            Vocabulary.source_lang == loser.source_lang,
            Vocabulary.target_lang == loser.target_lang,
        )
    ).scalars().all()

    for row in loser_rows:
        target = db.execute(
            select(Vocabulary).where(
                Vocabulary.user_id == row.user_id,
                Vocabulary.term == winner.term,
                Vocabulary.source_lang == winner.source_lang,
                Vocabulary.target_lang == winner.target_lang,
            )
        ).scalar_one_or_none()

        if target and target.id != row.id:
            target.priority = max(target.priority, row.priority)
            target.times_correct += row.times_correct
            target.times_wrong += row.times_wrong
            if row.last_reviewed_at and (
                target.last_reviewed_at is None or row.last_reviewed_at > target.last_reviewed_at
            ):
                target.last_reviewed_at = row.last_reviewed_at
            if not target.display_term and row.display_term:
                target.display_term = row.display_term
            target.primary_translation = winner.primary_translation
            target.alt_translations = _merged_alts(target.alt_translations, winner.alt_translations)
            target.translation_explanation = winner.translation_explanation
            target.example_sentence = winner.example_sentence
            target.lemma = winner.lemma
            target.term_pronunciation = winner.term_pronunciation
            target.translation_pronunciation = winner.translation_pronunciation
            db.delete(row)
            summary.vocab_merged += 1
        else:
            row.term = winner.term
            row.source_lang = winner.source_lang
            row.target_lang = winner.target_lang
            row.primary_translation = winner.primary_translation
            row.alt_translations = _merged_alts(row.alt_translations, winner.alt_translations)
            row.translation_explanation = winner.translation_explanation
            row.example_sentence = winner.example_sentence
            row.lemma = winner.lemma
            row.term_pronunciation = winner.term_pronunciation
            row.translation_pronunciation = winner.translation_pronunciation
            summary.vocab_repointed += 1


def clean_bad_cache_entries(db: Session, summary: MaintenanceSummary) -> None:
    rows = db.execute(select(TranslationCache)).scalars().all()
    for row in rows:
        if not _is_bad_cache_row(row):
            continue
        vocab_rows = db.execute(
            select(Vocabulary).where(
                Vocabulary.term == row.term,
                Vocabulary.source_lang == row.source_lang,
                Vocabulary.target_lang == row.target_lang,
            )
        ).scalars().all()
        for vocab in vocab_rows:
            db.delete(vocab)
            summary.bad_vocab_deleted += 1
        db.delete(row)
        summary.bad_cache_deleted += 1


def _group_same_term(rows: list[TranslationCache]) -> dict[tuple[str, str, str], list[TranslationCache]]:
    grouped: dict[tuple[str, str, str], list[TranslationCache]] = {}
    for row in rows:
        key = (normalize_term(row.term), row.source_lang, row.target_lang)
        grouped.setdefault(key, []).append(row)
    return grouped


def _group_same_primary(
    rows: list[TranslationCache],
) -> dict[tuple[str, str, str], list[TranslationCache]]:
    grouped: dict[tuple[str, str, str], list[TranslationCache]] = {}
    for row in rows:
        primary = _normalized_primary(row.primary_translation)
        if not primary:
            continue
        key = (primary, row.source_lang, row.target_lang)
        grouped.setdefault(key, []).append(row)
    return grouped


def _merge_group(
    db: Session, rows: list[TranslationCache], settings: Settings, summary: MaintenanceSummary
) -> None:
    if len(rows) < 2:
        return
    winner = _pick_winner(rows, settings)
    all_alts: list[str] = []
    for row in rows:
        if isinstance(row.alt_translations, list):
            all_alts.extend(str(v) for v in row.alt_translations)
    winner.alt_translations = _distinct_alts(all_alts) or None

    for row in rows:
        if row.id == winner.id:
            continue
        _repoint_vocabulary_rows(db, row, winner, summary)
        db.delete(row)
        summary.merged_cache_deleted += 1
    summary.merged_group_count += 1


def merge_duplicate_candidates(db: Session, settings: Settings, summary: MaintenanceSummary) -> None:
    rows = db.execute(select(TranslationCache)).scalars().all()
    if settings.cache_merge_identical_enabled:
        for group in _group_same_term(rows).values():
            if len(group) > 1:
                _merge_group(db, group, settings, summary)

    rows = db.execute(select(TranslationCache)).scalars().all()
    for group in _group_same_primary(rows).values():
        # only merge if at least two distinct normalized terms are present
        norm_terms = {normalize_term(r.term) for r in group}
        if len(group) > 1 and len(norm_terms) > 1:
            _merge_group(db, group, settings, summary)


def run_cache_maintenance_once(settings: Settings) -> MaintenanceSummary:
    session_factory = get_session_local()
    db = session_factory()
    summary = MaintenanceSummary()
    try:
        clean_bad_cache_entries(db, summary)
        merge_duplicate_candidates(db, settings, summary)
        db.commit()
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class CacheMaintenanceWorker:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="cache-maintenance", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        interval = max(60, int(self._settings.cache_maintenance_interval_seconds))
        while not self._stop_event.is_set():
            try:
                summary = run_cache_maintenance_once(self._settings)
                logger.info(
                    "cache_maintenance_done bad_cache_deleted=%s bad_vocab_deleted=%s merged_groups=%s merged_cache_deleted=%s vocab_repointed=%s vocab_merged=%s",
                    summary.bad_cache_deleted,
                    summary.bad_vocab_deleted,
                    summary.merged_group_count,
                    summary.merged_cache_deleted,
                    summary.vocab_repointed,
                    summary.vocab_merged,
                )
            except Exception:
                logger.exception("cache_maintenance_failed")
            if self._stop_event.wait(interval):
                break
