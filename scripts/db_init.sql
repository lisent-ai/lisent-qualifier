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
  raw_payload       JSONB,
  score_breakdown   JSONB,
  extra_data        JSONB,
  duplicate_of      UUID REFERENCES qualifier_leads(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
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
  champ_json  JSONB,
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
  champ_json      JSONB,
  crm_sent       BOOLEAN NOT NULL DEFAULT FALSE,
  sent_at        TIMESTAMPTZ,
  cta_type       TEXT,                -- 'cyprus_visit' | 'calendly' | 'nurture'
  meeting_url    TEXT,                -- Calendly URL (only populated for cta_type='calendly')
  qualification_potential TEXT,       -- 'high' | 'medium' | 'low'
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotent migration for existing installations
ALTER TABLE qualifier_handoffs ADD COLUMN IF NOT EXISTS cta_type TEXT;
ALTER TABLE qualifier_handoffs ADD COLUMN IF NOT EXISTS meeting_url TEXT;
ALTER TABLE qualifier_handoffs ADD COLUMN IF NOT EXISTS qualification_potential TEXT;

CREATE INDEX IF NOT EXISTS idx_qh_lead_id    ON qualifier_handoffs(lead_id);
CREATE INDEX IF NOT EXISTS idx_qh_company_id ON qualifier_handoffs(company_id);
CREATE INDEX IF NOT EXISTS idx_qh_cta_type   ON qualifier_handoffs(cta_type);

-- Activity log
CREATE TABLE IF NOT EXISTS activity_log (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id TEXT NOT NULL,
  lead_db_id UUID NOT NULL REFERENCES qualifier_leads(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  actor      TEXT NOT NULL DEFAULT 'system',
  payload    JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activity_lead    ON activity_log(lead_db_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_company ON activity_log(company_id, created_at DESC);

-- Handoff retry attempts
CREATE TABLE IF NOT EXISTS handoff_attempts (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id      TEXT NOT NULL,
  lead_db_id      UUID,
  handoff_payload JSONB NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  error_message   TEXT,
  attempt_count   INT NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_attempted_at TIMESTAMPTZ
);
