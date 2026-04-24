"""User-Scanner API — thin FastAPI wrapper around the maintained
Holehe-successor package `user-scanner` (kaifcodec/user-scanner).

Endpoints:
    POST /check   → run all email scan modules against an email in parallel
    GET  /health  → module count + build metadata

Compared to Holehe: actively maintained in 2026, broader module coverage
(95+ email-integrated sites), built-in proxy rotation hooks, clearer
Status enum (TAKEN / AVAILABLE / ERROR / SKIPPED).

Output shape mirrors holehe-api so the qualifier-side OSINT adapter can
switch between the two via the `OSINT_EMAIL_SCANNER` env flag without
changing its merge logic.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, EmailStr, Field
from user_scanner.core import engine  # type: ignore[import-untyped]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("user-scanner-api")

DEFAULT_TIMEOUT = float(os.getenv("USER_SCANNER_TIMEOUT_S", "30.0"))

app = FastAPI(
    title="Lisent OSINT — User-Scanner API",
    description=(
        "Email OSINT wrapper around the user-scanner package "
        "(maintained Holehe successor)."
    ),
    version="0.1.0",
)


class CheckRequest(BaseModel):
    email: EmailStr
    timeout_s: float = Field(default=DEFAULT_TIMEOUT, ge=1.0, le=120.0)


class SiteResult(BaseModel):
    name: str
    category: str
    # "Registered" when the email has an account on the site, else
    # "Not Registered" / "Error" / "Skipped".
    status: str
    url: str | None = None
    extra: str | None = None
    reason: str | None = None


class CheckResponse(BaseModel):
    email: str
    checked_at: str
    elapsed_ms: int
    module_count: int
    registered_count: int
    error_count: int
    skipped_count: int
    registered_sites: list[str]
    results: list[SiteResult]
    any_error: bool


def _to_site_result(raw: dict[str, Any]) -> SiteResult:
    return SiteResult(
        name=str(raw.get("site_name") or raw.get("name") or "?"),
        category=str(raw.get("category") or "unknown"),
        status=str(raw.get("status") or "Unknown"),
        url=raw.get("url") or None,
        extra=raw.get("extra") or None,
        reason=raw.get("reason") or None,
    )


@app.post("/check", response_model=CheckResponse)
async def check_email(req: CheckRequest) -> CheckResponse:
    started = datetime.now(timezone.utc)

    try:
        results_raw = await asyncio.wait_for(
            engine.check_all(str(req.email), is_email=True),
            timeout=req.timeout_s,
        )
    except asyncio.TimeoutError:
        log.warning("user-scanner run timed out after %ss", req.timeout_s)
        results_raw = []

    results: list[SiteResult] = []
    for r in results_raw:
        try:
            d = r.to_dict()
        except Exception as exc:  # noqa: BLE001 — Result types vary in errors
            log.warning("user-scanner result serialize failed: %s", exc)
            continue
        results.append(_to_site_result(d))

    registered = [r.name for r in results if r.status == "Registered"]
    errors = [r for r in results if r.status in ("Error", "error")]
    skipped = [r for r in results if r.status in ("Skipped", "skipped")]

    elapsed_ms = int(
        (datetime.now(timezone.utc) - started).total_seconds() * 1000,
    )

    return CheckResponse(
        email=str(req.email),
        checked_at=started.isoformat(),
        elapsed_ms=elapsed_ms,
        module_count=len(results),
        registered_count=len(registered),
        error_count=len(errors),
        skipped_count=len(skipped),
        registered_sites=sorted(registered),
        results=results,
        any_error=bool(errors),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    # Engine's load_categories discovers all registered email scanners.
    try:
        cats = engine.load_categories(is_email=True, no_nsfw=False)
        cat_names = sorted(cats.keys())
    except Exception as exc:  # noqa: BLE001
        log.warning("user-scanner load_categories failed: %s", exc)
        cat_names = []
    return {
        "status": "ok",
        "scanner": "user-scanner",
        "categories": cat_names,
        "default_timeout_s": DEFAULT_TIMEOUT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
