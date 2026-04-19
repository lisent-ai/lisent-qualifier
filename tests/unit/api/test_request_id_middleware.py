from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware.request_id import (
    REQUEST_ID_HEADER,
    RequestIDMiddleware,
    get_request_id,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/ping")
    async def ping(request):  # type: ignore[no-untyped-def]
        return {"request_id": get_request_id(request)}

    return app


def test_mints_fresh_id_when_missing():
    client = TestClient(_build_app())
    resp = client.get("/ping")
    assert resp.status_code == 200
    rid = resp.json()["request_id"]
    assert rid
    assert resp.headers[REQUEST_ID_HEADER] == rid


def test_honors_incoming_id():
    client = TestClient(_build_app())
    resp = client.get("/ping", headers={REQUEST_ID_HEADER: "abc-123"})
    assert resp.status_code == 200
    assert resp.json()["request_id"] == "abc-123"
    assert resp.headers[REQUEST_ID_HEADER] == "abc-123"


def test_rejects_too_long_id():
    client = TestClient(_build_app())
    hostile = "a" * 200
    resp = client.get("/ping", headers={REQUEST_ID_HEADER: hostile})
    assert resp.status_code == 200
    assert resp.json()["request_id"] != hostile
    assert resp.json()["request_id"]


def test_rejects_control_chars():
    client = TestClient(_build_app())
    resp = client.get(
        "/ping",
        headers={REQUEST_ID_HEADER: "abc\x00def"},
    )
    # httpx / starlette may reject control chars at the header layer before we
    # even see the request. That is an equally good outcome (hostile header
    # never reached us). If the request does go through, we should have minted
    # a fresh id.
    if resp.status_code == 200:
        assert resp.json()["request_id"] != "abc\x00def"
