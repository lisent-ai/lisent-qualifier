"""
KnowledgePort — "Company knowledge base nerede?"

RAG için company-specific dokumentleri abstract eder. Mevcut sistemde KB
dokumanı CRM'den fetch ediliyor; yeni sistemde qualifier kendi KB tablosunu
da kullanabilir (standalone tenant'lar için).

Adapters (Phase 1.D ve sonrası):
    - LisentCRMKBAdapter — mevcut `app/infrastructure/crm/rest_client.py`
      `fetch_company_kb_documents()` fonksiyonunu wrap eder
    - PostgresKBAdapter — qualifier'ın kendi `ai_kb_documents` tablosu
      (pgvector + tsvector full-text search)
    - HybridKBAdapter (opsiyonel) — her iki kaynağı merge eder
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class KBDocument:
    """RAG için canonical document chunk."""

    id: str  # doc_ref + chunk_index
    tenant_id: UUID
    title: str
    content: str  # chunked text
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Similarity score arama sırasında set edilir
    similarity: float | None = None


class KnowledgePort(ABC):
    """Tenant KB erişimi. Tüm sonuçlar tenant-scoped olmalı (RLS destekli)."""

    @abstractmethod
    async def search(
        self,
        tenant_id: UUID,
        query: str,
        max_results: int = 5,
    ) -> list[KBDocument]:
        """Semantic + full-text hybrid search.

        Implementation strategy (Phase 1 için simple, Phase 2'de embeddings):
            1. Tsvector full-text match (ranked by ts_rank)
            2. Phase 2'de pgvector cosine similarity (embeddings ön-compute)
            3. Sonuçları merge + re-rank
        """

    @abstractmethod
    async def list_documents(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KBDocument]:
        """Admin UI için paginated listing."""

    @abstractmethod
    async def upsert_document(
        self,
        tenant_id: UUID,
        doc_ref: str,
        title: str,
        content: str,
        source_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Doküman upload / update. Chunking + embedding burada yapılır (Phase 2)."""

    @abstractmethod
    async def delete_document(self, tenant_id: UUID, doc_ref: str) -> None:
        """Doküman sil (tüm chunk'larıyla)."""
