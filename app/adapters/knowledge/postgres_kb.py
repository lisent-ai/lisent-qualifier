"""
PostgresKBAdapter — Qualifier'ın kendi `ai_kb_documents` tablosunu KnowledgePort'a
uydurur.

Mevcut `app.infrastructure.rag.repository` modülünü wrap eder:
    - search_chunks()    → search()
    - list_documents()   → list_documents()
    - upsert_document()  → upsert_document()
    - delete_document()  → delete_document()

Tablonun pgvector kolonu var (`embedding vector(1536)`) ama şu anda tsvector
full-text search kullanılıyor (Phase 2'de embeddings ile hybrid'e geçiş).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from app.infrastructure.rag import repository as rag_repo
from app.ports.knowledge import KBDocument, KnowledgePort

log = structlog.get_logger(__name__)


class PostgresKBAdapter(KnowledgePort):
    """KnowledgePort over qualifier's local ai_kb_documents table (PostgreSQL)."""

    async def search(
        self,
        tenant_id: UUID,
        query: str,
        max_results: int = 5,
    ) -> list[KBDocument]:
        """tsvector full-text search — Phase 2'de pgvector hybrid ile genişletilecek.

        NOT: Mevcut repository `company_id: str` kabul ediyor. Migration'da
        `tenant_id` kolonu eklendi ama repository kodu henüz tenant-aware değil.
        Phase 1.E'de repository.search_chunks() tenant_id'yi filter'layacak;
        şimdilik tenant_id'yi company_id string'ine çeviriyoruz (legacy uyumu).
        """
        chunks = await rag_repo.search_chunks(
            company_id=str(tenant_id),
            query=query,
            limit=max_results,
        )
        results: list[KBDocument] = []
        for chunk in chunks:
            results.append(self._chunk_to_doc(chunk, tenant_id))
        return results

    async def list_documents(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KBDocument]:
        """Tenant'ın tüm dokümanları — chunk_index=0'lı ilk chunk başına bir doc."""
        docs = await rag_repo.list_documents(company_id=str(tenant_id), limit=limit)
        # list_documents zaten tek row/doc döner (DISTINCT ON doc_ref). Slice offset.
        sliced = docs[offset : offset + limit]
        return [self._row_to_doc(row, tenant_id) for row in sliced]

    async def upsert_document(
        self,
        tenant_id: UUID,
        doc_ref: str,
        title: str,
        content: str,
        source_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Dokümanı chunkla ve upsert et. Phase 2'de embeddings hesaplama eklenecek."""
        await rag_repo.upsert_document(
            company_id=str(tenant_id),
            doc_ref=doc_ref,
            title=title,
            content=content,
            source_url=source_url,
            metadata=metadata or {},
        )

    async def delete_document(self, tenant_id: UUID, doc_ref: str) -> None:
        await rag_repo.delete_document(company_id=str(tenant_id), doc_ref=doc_ref)

    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_to_doc(chunk: Any, tenant_id: UUID) -> KBDocument:
        """KBChunk dataclass → KBDocument (port dataclass)."""
        return KBDocument(
            id=f"{chunk.doc_ref}:{chunk.chunk_index}",
            tenant_id=tenant_id,
            title=getattr(chunk, "title", "") or "",
            content=chunk.content,
            source_url=getattr(chunk, "source_url", None),
            metadata=getattr(chunk, "metadata", {}) or {},
            similarity=getattr(chunk, "similarity", None),
        )

    @staticmethod
    def _row_to_doc(row: dict[str, Any], tenant_id: UUID) -> KBDocument:
        """DB row → KBDocument."""
        return KBDocument(
            id=f"{row.get('doc_ref', '')}:{row.get('chunk_index', 0)}",
            tenant_id=tenant_id,
            title=row.get("title", ""),
            content=row.get("content", ""),
            source_url=row.get("source_url"),
            metadata=row.get("metadata") or {},
            similarity=None,
        )
