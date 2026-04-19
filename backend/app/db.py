from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional
from pathlib import Path

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    vocabulary: Mapped[list["Vocabulary"]] = relationship(back_populates="user")


class TranslationCache(Base):
    __tablename__ = "translation_cache"
    __table_args__ = (UniqueConstraint("term", "source_lang", "target_lang", name="uq_cache_term_langs"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    source_lang: Mapped[str] = mapped_column(String(16), nullable=False)
    target_lang: Mapped[str] = mapped_column(String(16), nullable=False)
    primary_translation: Mapped[str] = mapped_column(Text, nullable=False)
    alt_translations: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Vocabulary(Base):
    __tablename__ = "vocabulary"
    __table_args__ = (
        UniqueConstraint("user_id", "term", "source_lang", "target_lang", name="uq_vocab_user_term_langs"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    term: Mapped[str] = mapped_column(String(512), nullable=False)
    display_term: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_lang: Mapped[str] = mapped_column(String(16), nullable=False)
    target_lang: Mapped[str] = mapped_column(String(16), nullable=False)
    primary_translation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    alt_translations: Mapped[Optional[List[Any]]] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    times_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    times_wrong: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped["User"] = relationship(back_populates="vocabulary")


class QuotaState(Base):
    """Single-row table for Gemini verify quota (id=1)."""

    __tablename__ = "quota_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    verify_cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verify_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verify_count_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # YYYY-MM-DD UTC


def _default_sqlite_url() -> str:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'lexi.db'}"


def make_engine(database_url: Optional[str] = None):
    url = (database_url or "").strip() or _default_sqlite_url()
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args)


_engine = None
SessionLocal = None


def init_engine(database_url: Optional[str] = None):
    global _engine, SessionLocal
    _engine = make_engine(database_url)
    SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=_engine)
    _ensure_quota_row()


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def get_session_local():
    if SessionLocal is None:
        init_engine()
    return SessionLocal


def _ensure_quota_row():
    from sqlalchemy.orm import Session

    s = Session(bind=_engine)
    try:
        row = s.get(QuotaState, 1)
        if row is None:
            s.add(QuotaState(id=1, verify_count=0, verify_count_date=None, verify_cooldown_until=None))
            s.commit()
    finally:
        s.close()


def normalize_term(raw: str) -> str:
    return " ".join(raw.strip().lower().split())


def utc_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def dumps_alt(alt: Optional[List[str]]) -> Optional[List[str]]:
    if not alt:
        return None
    return list(alt)
