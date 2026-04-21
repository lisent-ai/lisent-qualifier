"""
KnowledgePort adapters.

Tenant'ın knowledge base'ine erişim:
    - LisentCRMKBAdapter — CRM'de saklanan legacy KB (fetch_company_kb_documents)
    - PostgresKBAdapter — qualifier'ın kendi `ai_kb_documents` tablosu (pgvector + tsvector)

İleride:
    - HybridKBAdapter — ikisini merge eder (legacy + standalone)
"""

from app.adapters.knowledge.lisent_crm_kb import LisentCRMKBAdapter
from app.adapters.knowledge.postgres_kb import PostgresKBAdapter

__all__ = ["LisentCRMKBAdapter", "PostgresKBAdapter"]
