-- Faz 7 Phase 1.C — Multi-tenant schema + PostgreSQL Row-Level Security (RLS).
--
-- Bu migration qualifier'ı multi-tenant ürüne dönüştürür. Core felsefesi:
--   - Mevcut Lisent CRM'den gelen lead'ler otomatik "legacy tenant" olur
--     (source_type='lisent_crm', plan='legacy', source_ref=CRM company_id)
--   - Yeni external müşteriler 'standalone' tenant olarak eklenir
--   - Partner/reseller modeli için 'partner_sub' tenant type'ı
--   - PostgreSQL RLS ile DB seviyesinde cross-tenant leak koruması
--
-- Davranış:
--   - `current_setting('app.tenant_id')` set edilmemişse tenant-scoped tablolar BOŞ döner
--     (güvenlik default'u — silent deny). Application middleware her request'te set eder.
--   - `current_setting('app.is_super_admin') = 'true'` set edilirse RLS bypass edilir
--     (support/billing/ops için).
--
-- Idempotent:
--   - CREATE ... IF NOT EXISTS her yerde.
--   - Policy'ler `DROP POLICY IF EXISTS ... CREATE POLICY ...` deseni (CREATE POLICY
--     native `IF NOT EXISTS` desteklemiyor, PG17 dahil).
--   - Backfill `ON CONFLICT DO NOTHING` + `WHERE ... IS NULL` guard'ları ile.
--
-- References:
--   - docs/decisions/001-hexagonal-refactor.md
--   - docs/decisions/002-tenant-model-and-rls.md
--   - docs/migrations/003-tenants.md (detaylı spec + verification queries)

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid() için (uuid-ossp'ye ek, modern)

-- ============================================================================
-- 1. YENİ TABLOLAR
-- ============================================================================

-- Partners (opsiyonel — agency/reseller üst-katmanı)
CREATE TABLE IF NOT EXISTS partners (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  default_branding JSONB NOT NULL DEFAULT '{}'::jsonb,
  custom_domains TEXT[] NOT NULL DEFAULT '{}',
  revenue_share_percent INT NOT NULL DEFAULT 30 CHECK (revenue_share_percent BETWEEN 0 AND 100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tenants — kimlik, plan, config, branding
CREATE TABLE IF NOT EXISTS tenants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  source_type TEXT NOT NULL
    CHECK (source_type IN ('lisent_crm', 'standalone', 'partner_sub')),
  source_ref TEXT,
  partner_id UUID REFERENCES partners(id) ON DELETE SET NULL,
  plan TEXT NOT NULL DEFAULT 'free'
    CHECK (plan IN ('free', 'starter', 'pro', 'enterprise', 'partner', 'legacy')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'suspended', 'trialing', 'churned')),
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  domain_claims TEXT[] NOT NULL DEFAULT '{}',
  outbound_webhook_url TEXT,
  outbound_webhook_secret TEXT,
  branding JSONB NOT NULL DEFAULT '{}'::jsonb,
  qualification_framework TEXT NOT NULL DEFAULT 'champ'
    CHECK (qualification_framework IN ('champ', 'bant', 'meddic')),
  trial_ends_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenants_source      ON tenants(source_type, source_ref);
CREATE INDEX IF NOT EXISTS idx_tenants_partner     ON tenants(partner_id) WHERE partner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tenants_plan_status ON tenants(plan, status);

-- Tenant users (SuperTokens user_id referansıyla)
CREATE TABLE IF NOT EXISTS tenant_users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  supertokens_user_id TEXT NOT NULL,
  email TEXT NOT NULL,
  full_name TEXT,
  role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
  scopes TEXT[] NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'invited', 'suspended')),
  invited_by UUID REFERENCES tenant_users(id) ON DELETE SET NULL,
  last_active_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, email),
  UNIQUE (tenant_id, supertokens_user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_users_tenant      ON tenant_users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_users_supertokens ON tenant_users(supertokens_user_id);

-- API keys (bcrypt hash'li)
CREATE TABLE IF NOT EXISTS tenant_api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  prefix TEXT NOT NULL CHECK (prefix IN ('sk_live_', 'sk_test_')),
  last_4 TEXT NOT NULL,
  hash TEXT NOT NULL,
  name TEXT NOT NULL,
  scopes TEXT[] NOT NULL,
  created_by UUID REFERENCES tenant_users(id) ON DELETE SET NULL,
  last_used_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_active
  ON tenant_api_keys(tenant_id)
  WHERE revoked_at IS NULL;

-- OAuth clients (widget/extension/3rd-party app'ler için)
CREATE TABLE IF NOT EXISTS tenant_oauth_clients (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  client_id TEXT UNIQUE NOT NULL,
  client_type TEXT NOT NULL CHECK (client_type IN ('public', 'confidential')),
  client_secret_hash TEXT,
  redirect_uris TEXT[] NOT NULL,
  allowed_scopes TEXT[] NOT NULL,
  allowed_origins TEXT[] NOT NULL,
  pre_approved BOOLEAN NOT NULL DEFAULT false,
  created_by UUID REFERENCES tenant_users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_oauth_clients_tenant ON tenant_oauth_clients(tenant_id);

-- Usage metering (daily aggregate, Redis hot counter'larından beslenir)
CREATE TABLE IF NOT EXISTS tenant_usage (
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  metric TEXT NOT NULL,
  value BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, date, metric)
);

CREATE INDEX IF NOT EXISTS idx_tenant_usage_date ON tenant_usage(date);

-- Audit log (tenant-scoped, append-only)
CREATE TABLE IF NOT EXISTS tenant_audit_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  actor_type TEXT NOT NULL
    CHECK (actor_type IN ('user', 'api_key', 'system', 'oauth_client')),
  actor_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  resource_type TEXT,
  resource_id TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  ip_address INET,
  user_agent TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_date ON tenant_audit_log(tenant_id, created_at DESC);

-- ============================================================================
-- 2. MEVCUT TABLOLARA tenant_id KOLONU (nullable başlar, backfill sonrası NOT NULL)
-- ============================================================================

ALTER TABLE qualifier_leads    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id);
ALTER TABLE qualifier_sessions ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id);
ALTER TABLE qualifier_handoffs ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id);
ALTER TABLE ai_kb_documents    ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id);

-- ============================================================================
-- 3. BACKFILL — Mevcut CRM lead'leri "legacy tenant"'a bağla
-- ============================================================================

-- Her distinct company_id için bir legacy tenant yarat
-- Slug unique olduğu için idempotent (tekrar çalıştırsak ON CONFLICT DO NOTHING)
INSERT INTO tenants (slug, name, source_type, source_ref, plan, status, config)
SELECT
  'legacy-' || REPLACE(company_id::text, '-', ''),
  'Legacy Company ' || SUBSTR(company_id::text, 1, 8),
  'lisent_crm',
  company_id::text,
  'legacy',
  'active',
  '{}'::jsonb
FROM (
  SELECT DISTINCT company_id
  FROM qualifier_leads
  WHERE company_id IS NOT NULL
) AS distinct_companies
ON CONFLICT (slug) DO NOTHING;

-- qualifier_leads.tenant_id backfill
UPDATE qualifier_leads
SET tenant_id = t.id
FROM tenants t
WHERE t.source_type = 'lisent_crm'
  AND t.source_ref = qualifier_leads.company_id::text
  AND qualifier_leads.tenant_id IS NULL;

-- qualifier_sessions backfill (lead_id üstünden join)
UPDATE qualifier_sessions s
SET tenant_id = l.tenant_id
FROM qualifier_leads l
WHERE s.lead_id = l.id
  AND s.tenant_id IS NULL;

-- qualifier_handoffs backfill (lead_id üstünden join)
UPDATE qualifier_handoffs h
SET tenant_id = l.tenant_id
FROM qualifier_leads l
WHERE h.lead_id = l.id
  AND h.tenant_id IS NULL;

-- ai_kb_documents backfill (company_id direkt join)
UPDATE ai_kb_documents d
SET tenant_id = t.id
FROM tenants t
WHERE t.source_type = 'lisent_crm'
  AND t.source_ref = d.company_id::text
  AND d.tenant_id IS NULL;

-- ============================================================================
-- 4. TENANT_ID NULLABLE BIRAKILIYOR (Phase 1.C)
-- ============================================================================
-- tenant_id kolonu bu migration'da NULLABLE kalır. Sebep:
--   - Mevcut uygulama (qualifier-api) henüz tenant-aware değil (Phase 1.D'de yapılacak)
--   - NOT NULL set edilirse webhook INSERT fail eder → production bozulur
--   - Backfill tamamlandıktan sonra uygulama 1.D'de tenant kontrollü INSERT yapacak
--   - Ayrı bir migration (örn. 005_tenant_id_not_null.sql) Phase 1.D deploy'u sonrası
--     NOT NULL constraint'i ekleyecek
--
-- GÜVENLİK: RLS policy'si `tenant_id = current_setting('app.tenant_id')::uuid`
-- zaten NULL tenant_id'li row'ları hiçbir context'te göstermez (NULL = NULL = FALSE).
-- Yani nullable olması leak'e yol açmaz; sadece legacy row'ları "görünmez" yapar.

-- ============================================================================
-- 5. TENANT_ID INDEXES (query performance)
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_qualifier_leads_tenant    ON qualifier_leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_qualifier_sessions_tenant ON qualifier_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_qualifier_handoffs_tenant ON qualifier_handoffs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ai_kb_docs_tenant         ON ai_kb_documents(tenant_id);

-- ============================================================================
-- 6. ROW-LEVEL SECURITY
-- ============================================================================
-- KRİTİK: PostgreSQL superuser ve BYPASSRLS role'leri RLS'i otomatik bypass eder.
-- Migration 'app' user'ı superuser olduğu için (dev docker-compose default'u),
-- RLS'i ENFORCE edebilmek için aşağıda 'app_runtime' rolü yaratırız — uygulama
-- (qualifier-api, crm-service) bu role ile DB'ye bağlanacak. Migration/ops için
-- 'app' (superuser) kullanılmaya devam eder.
--
-- Referans: docs/decisions/002-tenant-model-and-rls.md "Super admin bypass"

-- ENABLE + FORCE: FORCE olmadan tablo sahibi de bypass eder, FORCE kapatır.
ALTER TABLE qualifier_leads    ENABLE  ROW LEVEL SECURITY;
ALTER TABLE qualifier_leads    FORCE   ROW LEVEL SECURITY;
ALTER TABLE qualifier_sessions ENABLE  ROW LEVEL SECURITY;
ALTER TABLE qualifier_sessions FORCE   ROW LEVEL SECURITY;
ALTER TABLE qualifier_handoffs ENABLE  ROW LEVEL SECURITY;
ALTER TABLE qualifier_handoffs FORCE   ROW LEVEL SECURITY;
ALTER TABLE ai_kb_documents    ENABLE  ROW LEVEL SECURITY;
ALTER TABLE ai_kb_documents    FORCE   ROW LEVEL SECURITY;
ALTER TABLE tenant_audit_log   ENABLE  ROW LEVEL SECURITY;
ALTER TABLE tenant_audit_log   FORCE   ROW LEVEL SECURITY;

-- Tenant isolation policies (idempotent via DROP IF EXISTS)
DROP POLICY IF EXISTS tenant_isolation_leads    ON qualifier_leads;
DROP POLICY IF EXISTS tenant_isolation_sessions ON qualifier_sessions;
DROP POLICY IF EXISTS tenant_isolation_handoffs ON qualifier_handoffs;
DROP POLICY IF EXISTS tenant_isolation_kb       ON ai_kb_documents;
DROP POLICY IF EXISTS tenant_isolation_audit    ON tenant_audit_log;

CREATE POLICY tenant_isolation_leads ON qualifier_leads
  AS PERMISSIVE FOR ALL
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY tenant_isolation_sessions ON qualifier_sessions
  AS PERMISSIVE FOR ALL
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY tenant_isolation_handoffs ON qualifier_handoffs
  AS PERMISSIVE FOR ALL
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY tenant_isolation_kb ON ai_kb_documents
  AS PERMISSIVE FOR ALL
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY tenant_isolation_audit ON tenant_audit_log
  AS PERMISSIVE FOR ALL
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

-- Super admin bypass policies
DROP POLICY IF EXISTS super_admin_bypass_leads    ON qualifier_leads;
DROP POLICY IF EXISTS super_admin_bypass_sessions ON qualifier_sessions;
DROP POLICY IF EXISTS super_admin_bypass_handoffs ON qualifier_handoffs;
DROP POLICY IF EXISTS super_admin_bypass_kb       ON ai_kb_documents;
DROP POLICY IF EXISTS super_admin_bypass_audit    ON tenant_audit_log;

CREATE POLICY super_admin_bypass_leads ON qualifier_leads
  AS PERMISSIVE FOR ALL
  USING (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY super_admin_bypass_sessions ON qualifier_sessions
  AS PERMISSIVE FOR ALL
  USING (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY super_admin_bypass_handoffs ON qualifier_handoffs
  AS PERMISSIVE FOR ALL
  USING (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY super_admin_bypass_kb ON ai_kb_documents
  AS PERMISSIVE FOR ALL
  USING (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY super_admin_bypass_audit ON tenant_audit_log
  AS PERMISSIVE FOR ALL
  USING (current_setting('app.is_super_admin', true) = 'true');

-- ============================================================================
-- 7. APP_RUNTIME ROLE — uygulama bu role ile DB'ye bağlanacak (NOT superuser)
-- ============================================================================
-- Phase 1.E'de DATABASE_URL'de `app_runtime` user'ına switch edilecek.
-- Şimdilik yaratılıyor, GRANT'lar veriliyor; `app` superuser migration için kalır.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    -- Password application .env'inden gelir (dev için deterministic, prod'da rotate)
    CREATE ROLE app_runtime LOGIN PASSWORD 'app_runtime_dev_password'
      NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE INHERIT;
  END IF;
END $$;

-- Schema erişim
GRANT CONNECT ON DATABASE lead_qualifier TO app_runtime;
GRANT USAGE ON SCHEMA public TO app_runtime;

-- Mevcut tablolar + gelecekte eklenecekler (DEFAULT PRIVILEGES)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_runtime;

-- set_config / current_setting app_runtime tarafından çağrılabilsin
-- (Built-in'ler public'e zaten GRANT'lı; açıklık için not).

-- ============================================================================
-- 8. UPDATED_AT AUTO-UPDATE TRIGGER (tenants, partners için)
-- ============================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_tenants_updated_at ON tenants;
CREATE TRIGGER trg_tenants_updated_at
  BEFORE UPDATE ON tenants
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_partners_updated_at ON partners;
CREATE TRIGGER trg_partners_updated_at
  BEFORE UPDATE ON partners
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
