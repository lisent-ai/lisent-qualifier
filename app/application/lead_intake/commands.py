from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProcessWebhookLeadCommand:
    lead_data: dict[str, Any]  # validated dict from WebhookLeadPayload
    fallback_url: str | None = None
    company_id: str = ""
