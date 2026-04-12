from typing import List, Optional

from pydantic import BaseModel, Field


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


class LookupOut(BaseModel):
    term: str
    display_term: str
    primary_translation: str
    alt_translations: Optional[List[str]] = None
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
