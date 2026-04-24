"""Webhook event filter — wildcard pattern matching.

Pure functions so the logic is trivially unit-testable and reusable by the
fanout adapter, the admin API validator, and any future replay tooling.

Supported pattern forms:
    "*"              → catch-all (matches anything)
    "lead.*"         → prefix match on the dotted namespace
    "score.updated"  → exact literal match

`matches(event_type, patterns)` returns True if ANY pattern matches. An
empty patterns list matches nothing (conservative: misconfigured tenants
should not silently spam consumers).
"""

from __future__ import annotations

from collections.abc import Iterable


def matches(event_type: str, patterns: Iterable[str]) -> bool:
    for raw in patterns:
        p = (raw or "").strip()
        if not p:
            continue
        if p == "*":
            return True
        if p.endswith(".*"):
            prefix = p[:-1]  # keep the trailing dot so 'lead.' matches 'lead.won' but not 'leadx'
            if event_type.startswith(prefix):
                return True
        elif p == event_type:
            return True
    return False


def validate_patterns(patterns: Iterable[str], known_events: Iterable[str]) -> list[str]:
    """Return the list of patterns that are NOT valid.

    A pattern is valid if it is:
        - "*"
        - "<prefix>.*" where at least one known event starts with "<prefix>."
        - an exact match in known_events
    """
    known = set(known_events)
    # Derive valid namespaces from known event types (substring before first dot).
    namespaces = {e.split(".", 1)[0] for e in known if "." in e}

    invalid: list[str] = []
    for raw in patterns:
        p = (raw or "").strip()
        if not p:
            invalid.append(raw)
            continue
        if p == "*":
            continue
        if p.endswith(".*"):
            prefix = p[:-2]
            if prefix in namespaces:
                continue
            invalid.append(p)
            continue
        if p in known:
            continue
        invalid.append(p)
    return invalid
