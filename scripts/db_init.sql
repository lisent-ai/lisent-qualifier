-- AI Lead Qualifier — PostgreSQL şema başlatma
-- docker-entrypoint-initdb.d ile ilk başlatmada otomatik çalışır.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Lead kayıtları (webhook'tan gelen ham veri + skor)
CREATE TABLE IF NOT EXISTS qualifier_leads (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id   UUID NOT NULL,
  lead_id      TEXT NOT NULL,
  phone        TEXT NOT NULL,
  name         TEXT,
  email        TEXT,
  city         TEXT,
  source       TEXT,
  project_type TEXT,
  budget_range TEXT,
  score        INT  NOT NULL DEFAULT 0,
  path         TEXT,                        -- 'fast' | 'chat'
  status       TEXT NOT NULL DEFAULT 'new', -- new | qualifying | qualified | lost
  raw_payload  JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_qualifier_lead_id UNIQUE (company_id, lead_id)
);

CREATE INDEX IF NOT EXISTS idx_ql_company_id  ON qualifier_leads(company_id);
CREATE INDEX IF NOT EXISTS idx_ql_phone        ON qualifier_leads(company_id, phone);
CREATE INDEX IF NOT EXISTS idx_ql_status       ON qualifier_leads(status);

-- Konuşma sessionları
CREATE TABLE IF NOT EXISTS qualifier_sessions (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  lead_id    UUID NOT NULL REFERENCES qualifier_leads(id) ON DELETE CASCADE,
  company_id UUID NOT NULL,
  score      INT  NOT NULL DEFAULT 0,
  stage      TEXT NOT NULL DEFAULT 'chat',  -- chat | handoff
  bant_json  JSONB,
  messages   JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_qs_lead_id    ON qualifier_sessions(lead_id);
CREATE INDEX IF NOT EXISTS idx_qs_company_id ON qualifier_sessions(company_id);

-- Handoff geçmişi
CREATE TABLE IF NOT EXISTS qualifier_handoffs (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  session_id     UUID REFERENCES qualifier_sessions(id) ON DELETE SET NULL,
  lead_id        UUID NOT NULL REFERENCES qualifier_leads(id) ON DELETE CASCADE,
  company_id     UUID NOT NULL,
  final_score    INT,
  reasoning_json JSONB,
  bant_json      JSONB,
  crm_sent       BOOLEAN NOT NULL DEFAULT FALSE,
  sent_at        TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_qh_lead_id    ON qualifier_handoffs(lead_id);
CREATE INDEX IF NOT EXISTS idx_qh_company_id ON qualifier_handoffs(company_id);
