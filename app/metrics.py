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

BANT_EXTRACTIONS = Counter(
    "bant_extractions_total",
    "BANT extraction task executions",
    ["success"],
)

ACTIVE_SESSIONS = Gauge(
    "active_chat_sessions",
    "Currently active chat sessions",
)
