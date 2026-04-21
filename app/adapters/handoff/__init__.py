"""
HandoffPort adapters.

Qualified lead'i dışarı teslim etme stratejileri:
    - LisentCRMHandoffAdapter — mevcut CRM webhook akışı (token auth, outbox retry)
    - GenericWebhookAdapter — HMAC-signed POST to tenant-configured URL
    - SlackAdapter, EmailAdapter, MCPResponseAdapter — Phase 5+ deferred

Adapter'lar paralel çalışabilir (fan-out): handler bir lead için birden fazla
`HandoffPort` instance'ına teslim eder — örn. hem CRM hem Slack.
"""

from app.adapters.handoff.lisent_crm import LisentCRMHandoffAdapter
from app.adapters.handoff.generic_webhook import GenericWebhookAdapter

__all__ = ["LisentCRMHandoffAdapter", "GenericWebhookAdapter"]
