from __future__ import annotations

from dataclasses import asdict, dataclass


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


def is_supported_language(code: str) -> bool:
    return code in SUPPORTED_LANGUAGE_CODES


def language_name(code: str) -> str:
    return LANGUAGE_NAME_BY_CODE.get(code, code)


def list_languages() -> list[dict[str, str]]:
    return [asdict(l) for l in LANGUAGES]
