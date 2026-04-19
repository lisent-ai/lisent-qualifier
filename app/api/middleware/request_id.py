"""Request correlation ID middleware.

Every HTTP request is tagged with an X-Request-ID. An incoming header is honored
if it is a plausible, non-hostile value (printable, <= 128 chars). Otherwise a
fresh UUIDv4 is minted. The ID is:

- stored on request.state.request_id for handler access,
- bound into structlog contextvars so every log line emitted during the request
  carries request_id automatically,
- echoed on the response as X-Request-ID.
"""

from __future__ import annotations

import uuid
from typing import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_LEN = 128


def _is_plausible(value: str) -> bool:
    if not value or len(value) > _MAX_LEN:
        return False
    for ch in value:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return False
    return True


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _is_plausible(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id

        # Bind to structlog context so every log line inside this request
        # automatically carries the ID.
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")
