"""Disposable email domain blocklist.

Source: github.com/disposable-email-domains/disposable-email-domains —
community-curated, ~5k high-quality entries (low false-positive rate).
Snapshot bundled at build time so the pipeline has a sensible default
with zero network dependency. A daily refresher can overwrite the same
file in a future cron/init container.

Common freemail providers are tracked in a separate small set — they
are NOT disposable but also NOT corporate, so the domain classifier
treats them as a distinct bucket.
"""
from __future__ import annotations

import functools
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_DISPOSABLE_FILE = _DATA_DIR / "disposable_domains.txt"


# Broader freemail set than the original classifier; used by domain intel
# to short-circuit the corporate lookup for obvious consumer providers.
FREEMAIL_DOMAINS: frozenset[str] = frozenset({
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


def is_freemail(domain: str) -> bool:
    if not domain:
        return False
    return domain.lower().strip() in FREEMAIL_DOMAINS


def disposable_list_size() -> int:
    """Used by `/health` and observability — how many entries loaded."""
    return len(_load_disposable_set())
