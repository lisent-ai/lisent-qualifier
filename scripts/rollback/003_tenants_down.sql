-- Faz 7 Phase 1.C ROLLBACK — 003_tenants.sql'i geri alır.
--
-- KRİTİK: Bu dosya `scripts/rollback/` altındadır — migrate.sh bunu OTOMATİK çalıştırmaz.
-- Manuel olarak çalıştır:
--   psql "$DATABASE_URL" -f scripts/rollback/003_tenants_down.sql
--   psql "$DATABASE_URL" -c "DELETE FROM schema_migrations WHERE version='003_tenants';"
--
-- UYARI: `tenant_id` kolonları silindiğinde VERİ KAYBI OLMAZ (kolonlar drop edilir ama
-- company_id korunur; eski sistem yeniden çalışır). Ama tenants/tenant_users/partners/...
-- tablolarındaki YENİ veriler (external müşteriler, API keys vs) **silinir**.

-- ============================================================================
-- 1. TRIGGER'LAR
-- ============================================================================
DROP TRIGGER IF EXISTS trg_partners_updated_at ON partners;
DROP TRIGGER IF EXISTS trg_tenants_updated_at ON tenants;
DROP FUNCTION IF EXISTS set_updated_at();

-- ============================================================================
-- 2. APP_RUNTIME ROLE GRANTS + ROLE
-- ============================================================================
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM app_runtime;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM app_runtime;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM app_runtime;
REVOKE CONNECT ON DATABASE lead_qualifier FROM app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM app_runtime;

DROP ROLE IF EXISTS app_runtime;

-- ============================================================================
-- 3. POLICIES
-- ============================================================================
DROP POLICY IF EXISTS super_admin_bypass_audit    ON tenant_audit_log;
DROP POLICY IF EXISTS super_admin_bypass_kb       ON ai_kb_documents;
DROP POLICY IF EXISTS super_admin_bypass_handoffs ON qualifier_handoffs;
DROP POLICY IF EXISTS super_admin_bypass_sessions ON qualifier_sessions;
DROP POLICY IF EXISTS super_admin_bypass_leads    ON qualifier_leads;

DROP POLICY IF EXISTS tenant_isolation_audit    ON tenant_audit_log;
DROP POLICY IF EXISTS tenant_isolation_kb       ON ai_kb_documents;
DROP POLICY IF EXISTS tenant_isolation_handoffs ON qualifier_handoffs;
DROP POLICY IF EXISTS tenant_isolation_sessions ON qualifier_sessions;
DROP POLICY IF EXISTS tenant_isolation_leads    ON qualifier_leads;

-- ============================================================================
-- 3. RLS DISABLE
-- ============================================================================
ALTER TABLE tenant_audit_log   DISABLE ROW LEVEL SECURITY;
ALTER TABLE ai_kb_documents    DISABLE ROW LEVEL SECURITY;
ALTER TABLE qualifier_handoffs DISABLE ROW LEVEL SECURITY;
ALTER TABLE qualifier_sessions DISABLE ROW LEVEL SECURITY;
ALTER TABLE qualifier_leads    DISABLE ROW LEVEL SECURITY;

-- ============================================================================
-- 4. TENANT_ID KOLON + INDEX KALDIR
-- ============================================================================
DROP INDEX IF EXISTS idx_qualifier_leads_tenant;
DROP INDEX IF EXISTS idx_qualifier_sessions_tenant;
DROP INDEX IF EXISTS idx_qualifier_handoffs_tenant;
DROP INDEX IF EXISTS idx_ai_kb_docs_tenant;

ALTER TABLE qualifier_leads    DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE qualifier_sessions DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE qualifier_handoffs DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE ai_kb_documents    DROP COLUMN IF EXISTS tenant_id;

-- ============================================================================
-- 5. YENİ TABLOLARI KALDIR (CASCADE FK'leri de düşer)
-- ============================================================================
DROP TABLE IF EXISTS tenant_audit_log     CASCADE;
DROP TABLE IF EXISTS tenant_usage         CASCADE;
DROP TABLE IF EXISTS tenant_oauth_clients CASCADE;
DROP TABLE IF EXISTS tenant_api_keys      CASCADE;
DROP TABLE IF EXISTS tenant_users         CASCADE;
DROP TABLE IF EXISTS tenants              CASCADE;
DROP TABLE IF EXISTS partners             CASCADE;

-- pgcrypto / uuid-ossp extension'lar bırakılır (başka migration'lar kullanıyor olabilir).

-- ============================================================================
-- 6. SCHEMA_MIGRATIONS TABLOSUNDAN KAYDI SİL (manuel adım)
-- ============================================================================
-- Yukarıdaki blok başarılıysa elle çalıştır:
--   DELETE FROM schema_migrations WHERE version = '003_tenants';
-- (migrate.sh bir sonraki run'ında 003_tenants.sql'i tekrar uygulanabilir görsün)
