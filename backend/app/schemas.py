from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.languages import is_supported_language


class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=128)
    password: str = Field(min_length=6, max_length=256)


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    username: str

    model_config = {"from_attributes": True}


class LookupIn(BaseModel):
    term: str = Field(min_length=1, max_length=512)
    source_lang: str = "en"
    target_lang: str = "zh"

    @field_validator("source_lang", "target_lang")
    @classmethod
    def normalize_lang_code(cls, v: str) -> str:
        code = v.strip().lower()
        if not is_supported_language(code):
            raise ValueError(f"Unsupported language code: {v}")
        return code


class LookupOut(BaseModel):
    term: str
    display_term: str
    primary_translation: str
    alt_translations: Optional[List[str]] = None
    translation_explanation: Optional[str] = None
    example_sentence: Optional[str] = None
    lemma: Optional[str] = None
    translation_source: str  # global_cache | gemini
    vocabulary_id: int


class ReviewItemOut(BaseModel):
    id: int
    term: str
    display_term: Optional[str]
    source_lang: str
    target_lang: str


class ReviewAnswerIn(BaseModel):
    id: int
    explanation: str = Field(min_length=1, max_length=2048)


class ReviewAnswerOut(BaseModel):
    correct: bool
    canonical_answer: str
    grading_mode: str  # gemini | offline
    new_priority: int
