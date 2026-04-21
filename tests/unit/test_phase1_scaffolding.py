"""
Phase 1 scaffolding regression tests.

Bu dosya Phase 1 hexagonal refactor sırasında eklenen port/adapter katmanının
beklenen kontratları sağladığını doğrular. Asıl amacı:

1. Port interface'leri changes sırasında kırılmasın (her port ABC'nin
   abstract method setleri korunsun).
2. 9 adapter'ın her biri doğru port'u implement etsin (inheritance).
3. Dataclass constructor'ları plan edildiği alan setiyle çalışsın.
4. Mevcut `app.infrastructure.*` modülleri hâlâ erişilebilir (import edilebilir)
   — adapter wrapper pattern'ının temel varsayımı.

Bu testler fast (I/O yok, mock yok) — unit seviyesinde. Integration path'i için
`tests/integration/test_phase1_integration.py` (next — Phase 1.G devamı).

References:
    - docs/decisions/001-hexagonal-refactor.md
    - docs/decisions/002-tenant-model-and-rls.md
    - docs/implementation-log/2026-04-21-phase-1-progress.md
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest


# ============================================================================
# 1. Port interfaces — abstract class contract
# ============================================================================


class TestPortContracts:
    """Port ABC'lerin abstract method setleri korunsun (refactor breaker check)."""

    def test_tenant_port_has_expected_methods(self):
        from app.ports.tenant import TenantPort

        expected = {
            "resolve_by_webhook_token",
            "resolve_by_api_key",
            "resolve_by_id",
            "resolve_by_source_ref",
            "get_config",
            "record_usage",
        }
        assert expected.issubset(TenantPort.__abstractmethods__)

    def test_ingest_port_has_expected_methods(self):
        from app.ports.ingest import IngestPort

        assert "accept" in IngestPort.__abstractmethods__

    def test_handoff_port_has_expected_methods(self):
        from app.ports.handoff import HandoffPort

        expected = {"send", "name"}
        assert expected.issubset(HandoffPort.__abstractmethods__)

    def test_knowledge_port_has_expected_methods(self):
        from app.ports.knowledge import KnowledgePort

        expected = {"search", "list_documents", "upsert_document", "delete_document"}
        assert expected.issubset(KnowledgePort.__abstractmethods__)

    def test_llm_port_has_expected_methods(self):
        from app.ports.llm import LLMPort

        expected = {"stream_chat", "structured_extract", "name"}
        assert expected.issubset(LLMPort.__abstractmethods__)

    def test_event_port_has_expected_methods(self):
        from app.ports.event import EventPort

        expected = {"publish", "subscribe"}
        assert expected.issubset(EventPort.__abstractmethods__)


# ============================================================================
# 2. Dataclasses — construction smoke tests
# ============================================================================


class TestPortDataclasses:
    def test_tenant_construction(self):
        from app.ports.tenant import (
            QualificationFramework,
            Tenant,
            TenantPlan,
            TenantSourceType,
            TenantStatus,
        )

        t = Tenant(
            id=uuid4(),
            slug="acme",
            name="Acme",
            source_type=TenantSourceType.STANDALONE,
            source_ref=None,
            plan=TenantPlan.PRO,
            status=TenantStatus.ACTIVE,
        )
        assert t.is_active is True
        assert t.is_legacy_crm is False
        assert t.qualification_framework == QualificationFramework.CHAMP

    def test_legacy_crm_tenant(self):
        from app.ports.tenant import Tenant, TenantPlan, TenantSourceType, TenantStatus

        t = Tenant(
            id=uuid4(),
            slug="legacy-abc",
            name="Legacy Co",
            source_type=TenantSourceType.LISENT_CRM,
            source_ref="crm-company-uuid-string",
            plan=TenantPlan.LEGACY,
            status=TenantStatus.ACTIVE,
        )
        assert t.is_legacy_crm is True

    def test_ingested_lead_construction(self):
        from app.ports.ingest import IngestedLead, IngestSource

        lead = IngestedLead(
            tenant_id=uuid4(),
            source=IngestSource.WEBHOOK,
            external_ref="crm-lead-123",
            contact_name="Ali",
            contact_email="ali@example.com",
        )
        assert lead.notes == ""  # default
        assert lead.source == IngestSource.WEBHOOK

    def test_handoff_payload_construction(self):
        from app.ports.handoff import HandoffPayload

        p = HandoffPayload(
            lead_id=uuid4(),
            tenant_id=uuid4(),
            session_id=None,
            external_ref="ext-1",
            score=78,
            threshold=70,
            status="qualified",
            path="chat",
            framework="champ",
        )
        assert p.score == 78
        assert p.champ is None
        assert p.contact == {}

    def test_kb_document_construction(self):
        from app.ports.knowledge import KBDocument

        doc = KBDocument(id="doc1:0", tenant_id=uuid4(), title="T", content="C")
        assert doc.similarity is None
        assert doc.metadata == {}

    def test_chat_message_construction(self):
        from app.ports.llm import ChatMessage

        m = ChatMessage(role="user", content="hi")
        assert m.tool_call_id is None

    def test_score_event_construction(self):
        from app.ports.event import ScoreEvent

        e = ScoreEvent(
            tenant_id=uuid4(),
            lead_id=uuid4(),
            session_id=None,
            event_type="lead.scored",
            score=42,
            threshold=70,
            path="fast",
        )
        assert e.payload == {}


# ============================================================================
# 3. Adapter inheritance — her adapter doğru port'u implement ediyor
# ============================================================================


class TestAdapterInheritance:
    """Her adapter ilgili port ABC'sini concretely implement ediyor."""

    def test_tenant_adapters_implement_port(self):
        from app.adapters.tenant import LisentCRMTenantAdapter, StandaloneTenantAdapter
        from app.ports.tenant import TenantPort

        for cls in [LisentCRMTenantAdapter, StandaloneTenantAdapter]:
            assert issubclass(cls, TenantPort)
            assert cls.__abstractmethods__ == frozenset()

    def test_handoff_adapters_implement_port(self):
        from app.adapters.handoff import GenericWebhookAdapter, LisentCRMHandoffAdapter
        from app.ports.handoff import HandoffPort

        for cls in [GenericWebhookAdapter, LisentCRMHandoffAdapter]:
            assert issubclass(cls, HandoffPort)
            assert cls.__abstractmethods__ == frozenset()

    def test_llm_adapters_implement_port(self):
        from app.adapters.llm import GroqAdapter, LocalLlamaAdapter
        from app.ports.llm import LLMPort

        for cls in [GroqAdapter, LocalLlamaAdapter]:
            assert issubclass(cls, LLMPort)
            assert cls.__abstractmethods__ == frozenset()

    def test_knowledge_adapters_implement_port(self):
        from app.adapters.knowledge import LisentCRMKBAdapter, PostgresKBAdapter
        from app.ports.knowledge import KnowledgePort

        for cls in [LisentCRMKBAdapter, PostgresKBAdapter]:
            assert issubclass(cls, KnowledgePort)
            assert cls.__abstractmethods__ == frozenset()

    def test_event_adapters_implement_port(self):
        from app.adapters.event import RedisPubSubAdapter
        from app.ports.event import EventPort

        assert issubclass(RedisPubSubAdapter, EventPort)
        assert RedisPubSubAdapter.__abstractmethods__ == frozenset()


# ============================================================================
# 4. Wrapper pattern — adapter'lar mevcut infrastructure modüllerini import ediyor
# ============================================================================


class TestWrapperPattern:
    """
    Adapter'lar mevcut `app.infrastructure.*` modüllerini import edip wrap ediyor.
    Bu modüller Phase 1.G sonrası (silme öncesi) korunmalı — wrapper pattern'ın
    temel varsayımı.
    """

    def test_infrastructure_crm_rest_client_reachable(self):
        from app.infrastructure.crm import rest_client

        assert callable(rest_client.lookup_company_by_qualifier_token)
        assert callable(rest_client.fetch_company_ai_config)
        assert callable(rest_client.fetch_company_kb_documents)

    def test_infrastructure_crm_webhook_client_reachable(self):
        from app.infrastructure.crm import webhook_client

        assert callable(webhook_client.send_to_crm)
        assert callable(webhook_client.flush_outbox)

    def test_infrastructure_llm_clients_reachable(self):
        from app.infrastructure.llm import groq_client, local_llm_client

        assert callable(groq_client.stream_chat_with_tools)
        # _call_local_llm — private ama LocalLlamaAdapter kullanıyor
        assert callable(local_llm_client._call_local_llm)

    def test_infrastructure_rag_repository_reachable(self):
        from app.infrastructure.rag import repository

        assert callable(repository.search_chunks)
        assert callable(repository.upsert_document)
        assert callable(repository.delete_document)
        assert callable(repository.list_documents)


# ============================================================================
# 5. Adapter name() + simple behavior
# ============================================================================


class TestAdapterNames:
    """Her adapter tanınabilir bir name döndürmeli (telemetry/logging için)."""

    def test_handoff_adapter_names(self):
        from app.adapters.handoff import GenericWebhookAdapter, LisentCRMHandoffAdapter

        assert LisentCRMHandoffAdapter().name == "lisent_crm"
        assert GenericWebhookAdapter("https://example.com", "secret").name == "generic_webhook"

    def test_llm_adapter_names(self):
        from app.adapters.llm import GroqAdapter, LocalLlamaAdapter

        assert GroqAdapter().name == "groq"
        assert LocalLlamaAdapter().name == "local_llama"


# ============================================================================
# 6. HMAC signature — GenericWebhookAdapter sanity
# ============================================================================


class TestGenericWebhookSigning:
    def test_hmac_signature_format(self):
        """Stripe-style signature: t=<unix_ts>,v1=<hex>."""
        import hashlib
        import hmac
        import re

        from app.adapters.handoff.generic_webhook import GenericWebhookAdapter

        secret = "test_secret"
        adapter = GenericWebhookAdapter("https://example.com/hook", secret)
        body = b'{"hello":"world"}'

        sig_header = adapter._sign(body)

        # Format: t=<int>,v1=<hex>
        m = re.match(r"^t=(\d+),v1=([0-9a-f]+)$", sig_header)
        assert m is not None, f"signature format broken: {sig_header}"

        ts = m.group(1)
        signed_payload = f"{ts}.".encode() + body
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

        assert m.group(2) == expected


# ============================================================================
# 7. Payload serialization — deterministic for HMAC stability
# ============================================================================


class TestHandoffPayloadSerialization:
    def test_serialize_is_deterministic(self):
        """Keys sorted, separators compact — HMAC stability için önemli."""
        from app.adapters.handoff.generic_webhook import GenericWebhookAdapter
        from app.ports.handoff import HandoffPayload

        lead_id = UUID("00000000-0000-0000-0000-000000000001")
        tenant_id = UUID("00000000-0000-0000-0000-000000000002")
        payload = HandoffPayload(
            lead_id=lead_id,
            tenant_id=tenant_id,
            session_id=None,
            external_ref="ext-1",
            score=78,
            threshold=70,
            status="qualified",
            path="chat",
            framework="champ",
            contact={"name": "Ali"},
        )

        a = GenericWebhookAdapter._serialize_payload(payload)
        b = GenericWebhookAdapter._serialize_payload(payload)
        assert a == b, "serialization must be deterministic"
        # Keys sorted: 'champ' öğesi 'contact'tan önce gelmeli
        assert b.index(b'"champ"') < b.index(b'"contact"')
