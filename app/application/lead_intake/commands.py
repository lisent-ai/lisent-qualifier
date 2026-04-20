from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProcessWebhookLeadCommand:
    lead_data: dict[str, Any]  # validated dict from WebhookLeadPayload
    fallback_url: str | None = None
    company_id: str = ""
    # Pre-existing CRM lead this webhook is enriching. Set by the CRM
    # side when forwarding a lead that's already been created there
    # (e.g. intranet inbound, manual create): the qualifier skips its
    # own CRM lead create and PATCHes ai-metadata on the given id.
    external_lead_id: str | None = None
