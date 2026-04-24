from prometheus_client import Counter, Gauge, Histogram

LEADS_RECEIVED = Counter(
    "leads_received_total",
    "Total webhook leads received",
)

LEADS_FAST_PATH = Counter(
    "leads_fast_path_total",
    "Leads routed to fast path (score >= threshold)",
)

LEADS_CHAT_PATH = Counter(
    "leads_chat_path_total",
    "Leads routed to chat path (score < threshold)",
)

LEAD_SCORE_HISTOGRAM = Histogram(
    "lead_score",
    "Distribution of initial rule-based lead scores",
    buckets=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
)

HANDOFF_COUNTER = Counter(
    "handoffs_total",
    "Total handoffs triggered",
    ["path"],  # "fast" or "chat"
)

CRM_SEND_COUNTER = Counter(
    "crm_sends_total",
    "CRM webhook send attempts",
    ["path", "success"],
)

CHAMP_EXTRACTIONS = Counter(
    "champ_extractions_total",
    "CHAMP extraction task executions",
    ["success"],
)

ACTIVE_SESSIONS = Gauge(
    "active_chat_sessions",
    "Currently active chat sessions",
)

# ── Qualification Judge metrics ──────────────────────────────────────────────

JUDGE_EXTRACTIONS = Counter(
    "qualification_judge_extractions_total",
    "Qualification judge executions",
    ["success", "mode"],
)

JUDGE_LATENCY = Histogram(
    "qualification_judge_latency_seconds",
    "Qualification judge call latency",
    buckets=[0.5, 1, 2, 3, 5, 10, 15, 30],
)

JUDGE_SELF_CONSISTENCY = Counter(
    "qualification_judge_self_consistency_total",
    "Self-consistency passes triggered (borderline scores)",
)

SCORING_MODE_COMPARISON = Histogram(
    "scoring_mode_score_delta",
    "CHAMP vs Judge score delta (hybrid mode)",
    buckets=[-30, -20, -10, -5, 0, 5, 10, 20, 30],
)

# ── Instant handoff metrics ─────────────────────────────────────────────────

INSTANT_HANDOFF_TRIGGERS = Counter(
    "instant_handoff_triggers_total",
    "Instant handoff triggers by reason",
    ["reason"],
)

# ── Outbound webhook metrics (Phase 2.M) ────────────────────────────────────

WEBHOOK_DELIVERY_ATTEMPTS = Counter(
    "webhook_delivery_attempts_total",
    "Outbound webhook delivery attempts",
    ["tenant_id", "status"],  # status ∈ {success, retry, dlq}
)

WEBHOOK_DELIVERY_LATENCY = Histogram(
    "webhook_delivery_latency_seconds",
    "Single-attempt webhook delivery latency (request→response or error)",
    ["tenant_id"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
)

WEBHOOK_QUEUE_DEPTH = Gauge(
    "webhook_queue_depth",
    "Current depth of the shared webhook delivery queue",
)

WEBHOOK_RETRY_DEPTH = Gauge(
    "webhook_retry_depth",
    "Current size of the webhook retry ZSET (jobs waiting for next attempt)",
)

WEBHOOK_DLQ_SIZE = Gauge(
    "webhook_dlq_size",
    "Per-tenant DLQ size (jobs exhausted all retries)",
    ["tenant_id"],
)

# ── Pre-Score pipeline metrics (Phase 3) ────────────────────────────────────

PRESCORE_ACTIVE_TENANTS = Gauge(
    "prescore_active_tenants",
    "Number of tenants currently having queued pre-score items",
)

PRESCORE_RETRY_DEPTH = Gauge(
    "prescore_retry_depth",
    "Current size of the pre-score retry ZSET",
)

PRESCORE_DLQ_SIZE = Gauge(
    "prescore_dlq_size",
    "Per-tenant pre-score DLQ size",
    ["tenant_id"],
)

PRESCORE_QUEUE_DEPTH = Gauge(
    "prescore_queue_depth",
    "Per-tenant pre-score queue depth",
    ["tenant_id"],
)

PRESCORE_LATENCY_SECONDS = Histogram(
    "prescore_latency_seconds",
    "Wall-clock latency of a full pre-score pipeline (OSINT + ensemble + DB write + event)",
    buckets=[0.5, 1, 2, 3, 5, 8, 12, 20],
)

PRESCORE_SCORE_HISTOGRAM = Histogram(
    "prescore_final_score",
    "Distribution of final pre-score values",
    buckets=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
)

PRESCORE_FALLBACK_TOTAL = Counter(
    "prescore_fallback_total",
    "Pre-score ensemble fell back to data_quality_fallback",
    ["reason"],
)

PRESCORE_DIVERGENCE_TOTAL = Counter(
    "prescore_divergence_total",
    "|LLM median - formula audit| > threshold",
)

PRESCORE_PERSONA_FAILURES = Counter(
    "prescore_persona_failures_total",
    "Per-persona failures inside the ensemble",
    ["persona"],
)

# ── OSINT subsystem metrics (Phase 3) ───────────────────────────────────────

OSINT_CACHE_HIT_TOTAL = Counter(
    "osint_cache_hit_total",
    "OSINT profile DB cache hits",
    ["provider"],
)

OSINT_CACHE_MISS_TOTAL = Counter(
    "osint_cache_miss_total",
    "OSINT profile DB cache misses (upstream fetched)",
    ["provider"],
)

OSINT_LATENCY_SECONDS = Histogram(
    "osint_latency_seconds",
    "Per-provider OSINT upstream fetch latency",
    ["provider"],
    buckets=[0.1, 0.5, 1, 2, 4, 8, 12],
)

OSINT_FAILURES_TOTAL = Counter(
    "osint_failures_total",
    "OSINT upstream failures",
    ["provider", "reason"],  # reason ∈ {timeout, http_error, circuit_open, parse}
)
