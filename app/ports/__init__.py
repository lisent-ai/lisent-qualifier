"""
app.ports — Abstract interfaces (ports) for the hexagonal architecture.

Core (`app.domain`, `app.application`) bu modüldeki Protocol/ABC'lere bağımlı olur.
Concrete implementations `app.adapters.*` altındadır.

Port'lar tenant-aware'dır: çoğu method ya bir `Tenant` entity'si alır ya da
middleware tarafından set edilmiş bir tenant context'in içinde çalışır.

Bkz:
    - docs/decisions/001-hexagonal-refactor.md
    - docs/decisions/002-tenant-model-and-rls.md
"""

from app.ports.tenant import Tenant, TenantPort
from app.ports.ingest import IngestedLead, IngestPort
from app.ports.handoff import HandoffPayload, HandoffPort, HandoffResult
from app.ports.knowledge import KBDocument, KnowledgePort
from app.ports.llm import ChatMessage, LLMPort
from app.ports.event import EventPort, ScoreEvent

__all__ = [
    # Tenant
    "Tenant",
    "TenantPort",
    # Ingest
    "IngestedLead",
    "IngestPort",
    # Handoff
    "HandoffPayload",
    "HandoffPort",
    "HandoffResult",
    # Knowledge
    "KBDocument",
    "KnowledgePort",
    # LLM
    "ChatMessage",
    "LLMPort",
    # Event
    "EventPort",
    "ScoreEvent",
]
