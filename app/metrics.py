from prometheus_client import Counter, Histogram, Gauge

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
