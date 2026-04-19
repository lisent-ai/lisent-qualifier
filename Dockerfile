# ---------- Builder ----------
# Install dependencies + build the project wheel into a dedicated venv at
# /opt/venv. Non-editable install: hatchling produces a wheel that includes
# the `app` package, so the runtime image needs nothing but the venv.
FROM python:3.13-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /build

COPY pyproject.toml ./
COPY app/ ./app/

RUN uv venv /opt/venv --python=python3.13 \
    && uv pip install --python=/opt/venv/bin/python --no-cache .

# ---------- Runtime ----------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1001 app \
    && useradd  --system --uid 1001 --gid app --no-create-home app

COPY --from=builder --chown=app:app /opt/venv /opt/venv

WORKDIR /app

USER app:app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop"]
