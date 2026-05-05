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


def auto_direction_for_pair(
    text: str, user_speaks_lang: str, user_learning_lang: str
) -> tuple[str, str]:
    """
    Choose (translate_from, translate_to) for a single lookup call to the translator.

    ``user_speaks_lang`` / ``user_learning_lang`` are fixed UI meanings (language you use
    vs language you study) and must **not** be swapped when persisting rows.

    If the typed text clearly matches the learning language and not the spoken one,
    translate learning → spoken; otherwise translate spoken → learning.
    """
    is_spoken = looks_like_language(text, user_speaks_lang)
    is_learning = looks_like_language(text, user_learning_lang)
    if is_learning and not is_spoken:
        return user_learning_lang, user_speaks_lang
    return user_speaks_lang, user_learning_lang
