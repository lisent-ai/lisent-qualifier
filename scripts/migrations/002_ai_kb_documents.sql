-- Faz 6 — knowledge base documents for RAG-grounded chat.
--
-- Retrieval runs on a Postgres tsvector index (reliable and zero-dependency).
-- The `embedding` column is left in place for a future swap-in to pgvector
-- cosine similarity once an embedding provider is wired through.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

CREATE TABLE IF NOT EXISTS ai_kb_documents (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id   UUID NOT NULL,
  doc_ref      TEXT NOT NULL,
  chunk_index  INT  NOT NULL DEFAULT 0,
  title        TEXT NOT NULL DEFAULT '',
  content      TEXT NOT NULL,
  source_url   TEXT,
  metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding    vector(1536),
  content_tsv  tsvector GENERATED ALWAYS AS (
    to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(content,''))
  ) STORED,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_kb_company_doc_chunk UNIQUE (company_id, doc_ref, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_kb_company ON ai_kb_documents(company_id);
CREATE INDEX IF NOT EXISTS idx_kb_doc_ref ON ai_kb_documents(company_id, doc_ref);
CREATE INDEX IF NOT EXISTS idx_kb_content_tsv ON ai_kb_documents USING GIN (content_tsv);
-- embedding index is created later, only once a backfill has populated the
-- column; creating it on a table of NULLs is wasted work.
