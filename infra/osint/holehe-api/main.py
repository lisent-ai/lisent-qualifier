"""Holehe API — thin FastAPI wrapper around the holehe email-OSINT package.

Endpoints:
    POST /check   → run all discovered holehe modules against an email in parallel
    GET  /health  → discovered module count + names

Language/region-agnostic: works for any email (.com, .com.tr, .de, .uk, freemail, corporate).
holehe's upstream package is in inactive maintenance; broken modules are surfaced per-site
in the response instead of failing the request.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import pkgutil
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, EmailStr, Field

import holehe.modules  # type: ignore[import-untyped]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("holehe-api")

DEFAULT_TIMEOUT = float(os.getenv("HOLEHE_DEFAULT_TIMEOUT", "4.0"))
DEFAULT_CONCURRENCY = int(os.getenv("HOLEHE_MAX_CONCURRENCY", "20"))

app = FastAPI(
    title="Lisent OSINT — Holehe API",
    description="Email OSINT wrapper around the holehe package",
    version="0.1.0",
)


def _discover_modules() -> list[tuple[str, Callable]]:
    """Walk holehe.modules and collect (short_name, coroutine_func) pairs.

    Each holehe module file defines an async function with the same name as the file
    (e.g. holehe.modules.social_media.snapchat.snapchat).
    """
    collected: list[tuple[str, Callable]] = []
    for _finder, modname, ispkg in pkgutil.walk_packages(
        holehe.modules.__path__, prefix="holehe.modules."
    ):
        if ispkg:
            continue
        try:
            mod = importlib.import_module(modname)
        except Exception as exc:  # noqa: BLE001
            log.warning("holehe module import failed: %s (%s)", modname, exc)
            continue
        short = modname.rsplit(".", 1)[-1]
        func = getattr(mod, short, None)
        if callable(func):
            collected.append((short, func))
    return sorted(collected, key=lambda p: p[0])


MODULES: list[tuple[str, Callable]] = _discover_modules()
log.info("discovered %d holehe modules", len(MODULES))


class CheckRequest(BaseModel):
    email: EmailStr
    timeout_per_module: float = Field(default=DEFAULT_TIMEOUT, ge=1.0, le=15.0)
    max_concurrency: int = Field(default=DEFAULT_CONCURRENCY, ge=1, le=64)
    modules: list[str] | None = Field(
        default=None,
        description="If provided, only run this subset of modules by short name.",
    )


class SiteResult(BaseModel):
    name: str
    domain: str
    exists: bool | None  # True=found, False=not found, None=unknown (timeout/error/rate-limited)
    rate_limited: bool
    elapsed_ms: int
    error: str | None = None


class CheckResponse(BaseModel):
    email: str
    checked_at: str
    elapsed_ms: int
    module_count: int
    registered_count: int
    rate_limited_count: int
    error_count: int
    registered_sites: list[str]
    rate_limited_sites: list[str]
    results: list[SiteResult]
    any_rate_limited: bool


async def _run_module(
    name: str,
    func: Callable,
    email: str,
    client: httpx.AsyncClient,
    timeout: float,
) -> SiteResult:
    started = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    err: str | None = None
    try:
        await asyncio.wait_for(func(email, client, out), timeout=timeout)
    except asyncio.TimeoutError:
        err = "timeout"
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {str(exc)[:180]}"

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    if err is not None:
        return SiteResult(
            name=name, domain=name, exists=None,
            rate_limited=False, elapsed_ms=elapsed_ms, error=err,
        )
    if not out:
        return SiteResult(
            name=name, domain=name, exists=None,
            rate_limited=False, elapsed_ms=elapsed_ms, error="no_result",
        )
    r = out[0]
    return SiteResult(
        name=str(r.get("name", name)),
        domain=str(r.get("domain", name)),
        exists=r.get("exists"),
        rate_limited=bool(r.get("rateLimit", False)),
        elapsed_ms=elapsed_ms,
    )


@app.post("/check", response_model=CheckResponse)
async def check_email(req: CheckRequest) -> CheckResponse:
    started = datetime.now(timezone.utc)
    selected = (
        [(n, f) for n, f in MODULES if n in set(req.modules)]
        if req.modules is not None
        else MODULES
    )
    if not selected:
        return CheckResponse(
            email=str(req.email),
            checked_at=started.isoformat(),
            elapsed_ms=0,
            module_count=0,
            registered_count=0,
            rate_limited_count=0,
            error_count=0,
            registered_sites=[],
            rate_limited_sites=[],
            results=[],
            any_rate_limited=False,
        )

    sem = asyncio.Semaphore(req.max_concurrency)

    async with httpx.AsyncClient(
        timeout=req.timeout_per_module,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (holehe-api)"},
    ) as client:

        async def _wrapped(name: str, func: Callable) -> SiteResult:
            async with sem:
                return await _run_module(name, func, str(req.email), client, req.timeout_per_module)

        results = await asyncio.gather(*(_wrapped(n, f) for n, f in selected))

    registered = [r.name for r in results if r.exists is True]
    rate_limited = [r.name for r in results if r.rate_limited]
    errors = [r for r in results if r.error is not None]

    return CheckResponse(
        email=str(req.email),
        checked_at=started.isoformat(),
        elapsed_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        module_count=len(results),
        registered_count=len(registered),
        rate_limited_count=len(rate_limited),
        error_count=len(errors),
        registered_sites=sorted(registered),
        rate_limited_sites=sorted(rate_limited),
        results=results,
        any_rate_limited=bool(rate_limited),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "module_count": len(MODULES),
        "modules": [n for n, _ in MODULES],
        "default_timeout_s": DEFAULT_TIMEOUT,
        "default_concurrency": DEFAULT_CONCURRENCY,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
