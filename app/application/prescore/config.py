"""Pre-score subsystem constants + Redis key naming.

Farklı olarak webhook'tan:
    - **Per-tenant queue**: `prescore:queue:{tenant_id}` — bir tenant burst'ü
      diğer tenant'ları ezmesin (head-of-line blocking önlenir).
    - **Active tenants SET**: `prescore:active_tenants` — fair scheduler
      (SRANDMEMBER) RR ile gezer.
    - **Global retry ZSET**: member içinde tenant_id taşır, promotion'da
      doğru tenant queue'suna geri konur.
    - **Per-tenant DLQ**: operator inceleme tenant-izole.

Retry schedule kısaldı (chat-real-time olmayan bir internal işlem):
    [30s, 2m, 10m] — 3 attempt sonrası DLQ.
    Rationale: pre-score latency-critical değil (async), ama 10dk'dan uzun
    bekletmek de mantıksız (lead'in durumu eskir).

Throttle: global OSINT ve Groq rate limitini ezmemek için her iş arasında
jittered sleep (500-1500ms). Tek worker default; `prescore_worker_concurrency>1`
olursa her worker kendi jitter'ıyla girer, kümülatif rate Redis rate limiter
ile kısıtlanır.
"""

from __future__ import annotations

# Retry: failed attempt sonrası bu gecikmelerle tekrar denenir.
# Index 0 = initial attempt (enqueue'da zaten yapılmış); [0]=30s, [1]=2m, [2]=10m.
RETRY_DELAYS_SECONDS: tuple[int, ...] = (30, 120, 600)

# 3 failed attempt sonrası DLQ'ya düşer.
MAX_ATTEMPTS: int = len(RETRY_DELAYS_SECONDS) + 1  # initial + 3 retries = 4 total

# Worker throttle (jittered sleep min/max in seconds)
THROTTLE_MIN_S: float = 0.5
THROTTLE_MAX_S: float = 1.5

# BLPOP alternatifi yerine RR kullanıyoruz; ama retry scheduler poll interval:
RETRY_POLL_SECONDS: float = 2.0

# No-work sleep — active_tenants boş olduğunda worker bekleme süresi.
IDLE_SLEEP_SECONDS: float = 2.0

# Per-tenant DLQ retention cap.
DLQ_MAX_ENTRIES: int = 200

# Per-persona LLM çağrısı hard timeout (tüm ensemble için 3× paralel).
PERSONA_CALL_TIMEOUT_S: float = 12.0


# ─── Redis key naming ────────────────────────────────────────────────────────

def queue_key(tenant_id: str) -> str:
    """Per-tenant FIFO of pending pre-score jobs. Member: JSON{lead_id, attempt, enqueued_at_ms}."""
    return f"prescore:queue:{tenant_id}"


def active_tenants_key() -> str:
    """SET of tenant_ids that currently have queued pre-score items."""
    return "prescore:active_tenants"


def retry_key() -> str:
    """Global ZSET — score=next_run_ms, member=JSON{tenant_id, lead_id, attempt, enqueued_at_ms}."""
    return "prescore:retry"


def dlq_key(tenant_id: str) -> str:
    """Per-tenant DLQ (LIST, newest at head)."""
    return f"prescore:dlq:{tenant_id}"
