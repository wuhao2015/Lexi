from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, get_db, hash_password, verify_password
from app.config import get_settings
from app.db import User, init_engine
from app.languages import is_supported_language, list_languages
from app.review_logic import grade_review_answer, pick_next_review
from app.schemas import (
    LoginIn,
    LookupIn,
    LookupOut,
    RegisterIn,
    ReviewAnswerIn,
    ReviewAnswerOut,
    ReviewItemOut,
    TokenOut,
    UserOut,
)
from app.translation import (
    TranslationProviderError,
    get_or_create_global_translation,
    upsert_user_vocabulary,
)


def _friendly_translation_error_detail(code: str) -> str:
    if code == "missing_api_key":
        return "Translation service is not configured yet. Please set GEMINI_API_KEY on the server."
    if code == "rate_limited":
        return "Translation is temporarily rate limited. Please wait a moment and try again."
    if code == "service_unavailable":
        return "Translation service is busy right now. Please retry in a few seconds."
    if code == "empty_response":
        return "Translation service returned an empty response. Please try again."
    if code == "parse_failed":
        return "Could not parse translation result. Please try again."
    if code == "no_available_model":
        return "No translation model is currently available. Please try again later or update GEMINI_MODELS."
    return "Translation failed. Please try again."


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_engine(settings.database_url)
    yield


# --- API sub-application (mounted at /api) ---
api = FastAPI(title="Lexi API", docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json")


@api.get("/health")
def health():
    return {"ok": True}


@api.get("/languages")
def languages():
    return {"items": list_languages()}


@api.post("/auth/register", response_model=TokenOut)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if db.execute(select(User).where(User.username == body.username)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")
    user = User(username=body.username.strip(), password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Username already taken")
    db.refresh(user)
    token = create_access_token(user.id, user.username)
    return TokenOut(access_token=token)


@api.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(user.id, user.username)
    return TokenOut(access_token=token)


@api.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, username=user.username)


@api.post("/lookup", response_model=LookupOut)
def lookup(
    body: LookupIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    raw = body.term.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty term")
    if body.source_lang == body.target_lang:
        raise HTTPException(status_code=400, detail="Source and target languages must be different")
    try:
        cache, source = get_or_create_global_translation(
            db, raw, body.source_lang, body.target_lang, settings
        )
        vocab = upsert_user_vocabulary(db, user.id, raw, cache)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TranslationProviderError as e:
        db.rollback()
        detail = _friendly_translation_error_detail(e.code)
        raise HTTPException(status_code=502, detail=detail) from e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Translation failed: {e}") from e

    alts = cache.alt_translations if isinstance(cache.alt_translations, list) else None
    return LookupOut(
        term=cache.term,
        display_term=vocab.display_term or raw,
        primary_translation=cache.primary_translation,
        alt_translations=alts,
        translation_source=source,
        vocabulary_id=vocab.id,
    )


@api.get("/review/next", response_model=Optional[ReviewItemOut])
def review_next(
    source_lang: Optional[str] = Query(default=None),
    target_lang: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    source = source_lang.strip().lower() if source_lang else None
    target = target_lang.strip().lower() if target_lang else None
    if source and not is_supported_language(source):
        raise HTTPException(status_code=400, detail=f"Unsupported language code: {source_lang}")
    if target and not is_supported_language(target):
        raise HTTPException(status_code=400, detail=f"Unsupported language code: {target_lang}")
    if source and target and source == target:
        raise HTTPException(status_code=400, detail="Source and target languages must be different")
    row = pick_next_review(db, user.id, source_lang=source, target_lang=target)
    if row is None:
        return None
    return ReviewItemOut(
        id=row.id,
        term=row.term,
        display_term=row.display_term,
        source_lang=row.source_lang,
        target_lang=row.target_lang,
    )


@api.post("/review/answer", response_model=ReviewAnswerOut)
def review_answer(
    body: ReviewAnswerIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    try:
        correct, canonical, grading_mode, new_priority = grade_review_answer(
            db, user.id, body.id, body.explanation, settings
        )
        db.commit()
    except ValueError as e:
        if str(e) == "not_found":
            raise HTTPException(status_code=404, detail="Vocabulary item not found") from e
        raise
    return ReviewAnswerOut(
        correct=correct,
        canonical_answer=canonical,
        grading_mode=grading_mode,
        new_priority=new_priority,
    )


# --- Root app ---
_STATIC = Path(__file__).resolve().parent.parent / "static"
_has_spa = _STATIC.is_dir() and (_STATIC / "index.html").is_file()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

_settings = get_settings()
_origins = [o.strip() for o in _settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/api", api)

if _has_spa:
    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="spa")
else:

    @app.get("/")
    def root_placeholder():
        return JSONResponse(
            {
                "message": "Lexi API",
                "api": "/api",
                "docs": "/api/docs",
                "hint": "Build the frontend (outputs to backend/static) or use Vite dev with proxy to /api.",
            }
        )
