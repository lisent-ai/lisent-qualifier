"""
LisentCRMKBAdapter — Lisent CRM'in KB dokümanlarını KnowledgePort'a uydurur.

CRM endpoint: `GET /internal/company/{company_id}/kb-documents?include_content=true`
Mevcut fonksiyon: `app.infrastructure.crm.rest_client.fetch_company_kb_documents()`

NOT: Bu adapter read-only. Legacy CRM tenant'larında KB CRM'de yönetilir;
yeni standalone tenant'lar `PostgresKBAdapter`'ı kullanır. Upsert/delete bu
adapter'da NotImplementedError — yazma işlemleri CRM admin UI'sında yapılıyor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.infrastructure.crm import rest_client as crm_rest_client
from app.ports.knowledge import KBDocument, KnowledgePort
from app.ports.tenant import Tenant, TenantSourceType

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)


class LisentCRMKBAdapter(KnowledgePort):
    """KnowledgePort over Lisent CRM KB endpoint. Read-only."""

    def __init__(self, tenant_resolver=None) -> None:
        """tenant_resolver: tenant_id → Tenant çevirimi için TenantPort instance.
        None ise her çağrıda tenant_id doğrudan `source_ref` sayılır (dev fallback)."""
        self._tenant_resolver = tenant_resolver

    async def search(
        self,
        tenant_id: UUID,
        query: str,
        max_results: int = 5,
    ) -> list[KBDocument]:
        """CRM KB'de arama yap.

        NOT: CRM endpoint'i arama yapmaz, tüm dokümanları döner. Bu adapter
        client-side filter yapar (basit substring match). Gerçek semantic search
        için PostgresKBAdapter (tsvector + pgvector) kullanılmalı.
        """
        company_id = await self._resolve_company_id(tenant_id)
        if not company_id:
            return []

        docs = await crm_rest_client.fetch_company_kb_documents(company_id) or []
        results: list[KBDocument] = []
        q_lower = query.lower()
        for doc in docs:
            content = (doc.get("content") or "").lower()
            title = (doc.get("title") or "").lower()
            if q_lower in content or q_lower in title:
                results.append(self._to_doc(doc, tenant_id))
                if len(results) >= max_results:
                    break
        return results

    async def list_documents(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KBDocument]:
        """CRM'deki tüm dokümanları listele (paginasyon client-side)."""
        company_id = await self._resolve_company_id(tenant_id)
        if not company_id:
            return []

        docs = await crm_rest_client.fetch_company_kb_documents(company_id) or []
        sliced = docs[offset : offset + limit]
        return [self._to_doc(d, tenant_id) for d in sliced]

    async def upsert_document(
        self,
        tenant_id: UUID,
        doc_ref: str,
        title: str,
        content: str,
        source_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """LisentCRMKBAdapter upsert yapmaz — KB CRM admin UI'sından yönetilir."""
        raise NotImplementedError(
            "LisentCRMKBAdapter is read-only. Upsert via CRM admin UI or use PostgresKBAdapter."
        )

    async def delete_document(self, tenant_id: UUID, doc_ref: str) -> None:
        raise NotImplementedError("LisentCRMKBAdapter is read-only.")

    # ------------------------------------------------------------------

    async def _resolve_company_id(self, tenant_id: UUID) -> str | None:
        """tenant_id → CRM company_id (source_ref). TenantPort varsa resolve et."""
        if self._tenant_resolver is None:
            # Dev fallback: assume tenant_id == company_id stringly
            return str(tenant_id)
        try:
            tenant: Tenant = await self._tenant_resolver.resolve_by_id(tenant_id)
        except Exception as exc:
            log.warning("lisent_crm_kb_tenant_resolve_failed", tenant_id=str(tenant_id), error=str(exc))
            return None
        if tenant.source_type != TenantSourceType.LISENT_CRM:
            return None
        return tenant.source_ref

    @staticmethod
    def _to_doc(doc: dict[str, Any], tenant_id: UUID) -> KBDocument:
        return KBDocument(
            id=f"{doc.get('doc_ref', '')}:{doc.get('chunk_index', 0)}",
            tenant_id=tenant_id,
            title=doc.get("title", ""),
            content=doc.get("content", ""),
            source_url=doc.get("source_url"),
            metadata=doc.get("metadata") or {},
            similarity=None,
        )
