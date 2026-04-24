"""Email domain classification helpers — objective signals only.

Two sets are bundled:

1. Disposable blocklist (source: github.com/disposable-email-domains/
   disposable-email-domains) — ~5k community-curated entries, low
   false-positive rate. These are genuine throwaway/fraud signals.
2. Public email providers (gmail/outlook/yandex/etc.) — a neutral
   factual classification, NOT a negative tag. Most individual
   consumers and many SMB B2B buyers use a public-provider address
   legitimately; flagging it as a risk would bake bias into the score.

The classifier returns objective TLD categories (public_provider,
registry_tld, country_tld, generic_tld, disposable) and the LLM
layer + scorer interpret them without prejudice against any category.
"""
from __future__ import annotations

import functools
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_DISPOSABLE_FILE = _DATA_DIR / "disposable_domains.txt"


# Set of well-known public email providers (gmail, outlook, yahoo, …).
# Used by the classifier as a factual "this is a public service" flag
# — NOT a risk signal. Real construction-sector leads in TR very often
# use these.
PUBLIC_EMAIL_PROVIDERS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com",
    "hotmail.com", "hotmail.co.uk", "hotmail.fr", "hotmail.de", "hotmail.com.tr",
    "outlook.com", "outlook.com.tr", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.de", "yahoo.fr", "yahoo.com.tr",
    "yandex.com", "yandex.ru", "yandex.com.tr",
    "mail.ru",
    "protonmail.com", "proton.me", "pm.me",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "aim.com",
    "gmx.com", "gmx.de", "gmx.net",
    "zoho.com", "fastmail.com",
})


@functools.lru_cache(maxsize=1)
def _load_disposable_set() -> frozenset[str]:
    """Read the bundled blocklist once, lowercased + stripped."""
    try:
        text = _DISPOSABLE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    domains = (line.strip().lower() for line in text.splitlines())
    return frozenset(d for d in domains if d and not d.startswith("#"))


def is_disposable(domain: str) -> bool:
    """True if the domain is in the curated disposable-email blocklist."""
    if not domain:
        return False
    return domain.lower().strip() in _load_disposable_set()


def is_public_provider(domain: str) -> bool:
    """Objective check — is this a known public email service?

    Does NOT imply anything negative about the lead. Gmail is the
    single most common B2B contact email in SMB Turkey.
    """
    if not domain:
        return False
    return domain.lower().strip() in PUBLIC_EMAIL_PROVIDERS


# Back-compat alias so existing imports keep working during the
# rollout. Prefer `is_public_provider`.
def is_freemail(domain: str) -> bool:
    return is_public_provider(domain)


# Legacy name kept for anyone still importing FREEMAIL_DOMAINS.
FREEMAIL_DOMAINS = PUBLIC_EMAIL_PROVIDERS


def disposable_list_size() -> int:
    """Used by `/health` and observability — how many entries loaded."""
    return len(_load_disposable_set())
