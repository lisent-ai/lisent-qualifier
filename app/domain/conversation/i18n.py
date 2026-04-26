"""Language display-name mapping for the qualifier multilingual prompt path.

The pre-score judge (and any future judge call) injects `{language_name}` and
`{language_code}` into the system prompt so the LLM responds in the lead's
language. The single English prompt template is the source of truth for all
15 supported locales.
"""
from __future__ import annotations

from typing import Final

LANGUAGE_DISPLAY_NAMES: Final[dict[str, tuple[str, str]]] = {
    "tr": ("tr-TR", "Turkish"),
    "en": ("en-US", "English"),
    "de": ("de-DE", "German"),
    "fr": ("fr-FR", "French"),
    "es": ("es-ES", "Spanish"),
    "it": ("it-IT", "Italian"),
    "pt": ("pt-PT", "Portuguese"),
    "nl": ("nl-NL", "Dutch"),
    "pl": ("pl-PL", "Polish"),
    "ru": ("ru-RU", "Russian"),
    "ar": ("ar-SA", "Arabic"),
    "sv": ("sv-SE", "Swedish"),
    "da": ("da-DK", "Danish"),
    "no": ("nb-NO", "Norwegian"),
    "fi": ("fi-FI", "Finnish"),
}

DEFAULT_LANGUAGE: Final[str] = "en"
SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset(LANGUAGE_DISPLAY_NAMES.keys())


def resolve_language(code: str | None) -> tuple[str, str]:
    """Returns (BCP-47, English display name).

    Falls back to ``("en-US", "English")`` if ``code`` is unknown or missing.
    """
    if code is None:
        return LANGUAGE_DISPLAY_NAMES[DEFAULT_LANGUAGE]
    normalized = code.lower().strip()
    return LANGUAGE_DISPLAY_NAMES.get(normalized, LANGUAGE_DISPLAY_NAMES[DEFAULT_LANGUAGE])


def normalize_language(code: str | None) -> str:
    """Returns a supported locale code, falling back to DEFAULT_LANGUAGE."""
    if code is None:
        return DEFAULT_LANGUAGE
    normalized = code.lower().strip()
    return normalized if normalized in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
