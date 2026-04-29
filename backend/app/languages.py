from __future__ import annotations

from dataclasses import asdict, dataclass
import re

import langid


@dataclass(frozen=True)
class Language:
    code: str
    name: str


# Top 10 most widely used languages (native + second-language speakers).
LANGUAGES: tuple[Language, ...] = (
    Language("en", "English"),
    Language("zh", "Chinese (Mandarin)"),
    Language("hi", "Hindi"),
    Language("es", "Spanish"),
    Language("fr", "French"),
    Language("ar", "Arabic"),
    Language("bn", "Bengali"),
    Language("pt", "Portuguese"),
    Language("ru", "Russian"),
    Language("ur", "Urdu"),
)

LANGUAGE_NAME_BY_CODE = {l.code: l.name for l in LANGUAGES}
SUPPORTED_LANGUAGE_CODES = set(LANGUAGE_NAME_BY_CODE.keys())
langid.set_languages(["en", "es", "fr", "pt", "ru", "zh", "ar", "hi", "bn", "ur"])


def is_supported_language(code: str) -> bool:
    return code in SUPPORTED_LANGUAGE_CODES


def language_name(code: str) -> str:
    return LANGUAGE_NAME_BY_CODE.get(code, code)


def list_languages() -> list[dict[str, str]]:
    return [asdict(l) for l in LANGUAGES]


def _contains_script(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text))


def looks_like_language(text: str, code: str) -> bool:
    t = text.strip()
    if not t:
        return False

    if code == "zh":
        return _contains_script(t, r"[\u4e00-\u9fff]")
    if code == "hi":
        return _contains_script(t, r"[\u0900-\u097F]")
    if code == "bn":
        return _contains_script(t, r"[\u0980-\u09FF]")
    if code in {"ar", "ur"}:
        return _contains_script(t, r"[\u0600-\u06FF]")
    if code == "ru":
        return _contains_script(t, r"[\u0400-\u04FF]")
    if code in {"en", "es", "fr", "pt"}:
        if not _contains_script(t, r"[A-Za-z]"):
            return False
        detected, _score = langid.classify(t)
        return detected == code
    return True


def auto_direction_for_pair(text: str, source_lang: str, target_lang: str) -> tuple[str, str]:
    """
    If input text clearly matches target_lang (and not source_lang), flip direction.
    Otherwise keep user-selected direction.
    """
    is_source = looks_like_language(text, source_lang)
    is_target = looks_like_language(text, target_lang)
    if is_target and not is_source:
        return target_lang, source_lang
    return source_lang, target_lang
