-- Phase 3 (Pre-Scoring Redesign) — OSINT profile repository.
--
-- Per-tenant kalıcı OSINT cache. Aynı email/telefon 60 gün boyunca upstream'e
-- tekrar sorulmaz; 60 gün sonra otomatik refresh (refetch_count++). Yan fayda:
-- tenant bazında sorgulanabilir "lead intelligence" deposu (MVP'de sadece SQL,
-- ileride endpoint).
--
-- Davranış:
--   - Tenant-scoped: RLS + super_admin_bypass policy'leri (003'teki pattern).
--   - key_hash = sha256(lower(email).strip() + '|' + digits_only(phone))
--     application tarafında hesaplanır; DB sadece saklar.
--   - ON DELETE CASCADE: tenant silinirse tüm profilleri de silinir (KVKK
--     veri minimizasyonu).
--   - email/phone ham halde saklanır (lookup convenience) — hash zaten
--     benzersiz, ama human debug için faydalı.
--
-- Idempotent: CREATE ... IF NOT EXISTS her yerde; policy'ler drop+create.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid() (003'te zaten var, guard olarak)

-- ============================================================================
-- 1. TABLO
-- ============================================================================

CREATE TABLE IF NOT EXISTS qualifier_osint_profiles (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  key_hash        TEXT NOT NULL,
  email           TEXT,
  phone           TEXT,
  profile         JSONB NOT NULL,
  provider        TEXT NOT NULL,
  notes           TEXT[] NOT NULL DEFAULT '{}',
  refetch_count   INT  NOT NULL DEFAULT 0,
  last_used_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT qualifier_osint_profiles_tenant_key_uniq UNIQUE (tenant_id, key_hash)
);

-- ============================================================================
-- 2. INDEXES (query patterns: by email, by phone, by staleness, by last_used)
-- ============================================================================

CREATE INDEX IF NOT EXISTS qualifier_osint_profiles_email_idx
  ON qualifier_osint_profiles (tenant_id, lower(email)) WHERE email IS NOT NULL;

CREATE INDEX IF NOT EXISTS qualifier_osint_profiles_phone_idx
  ON qualifier_osint_profiles (tenant_id, phone) WHERE phone IS NOT NULL;

CREATE INDEX IF NOT EXISTS qualifier_osint_profiles_updated_at_idx
  ON qualifier_osint_profiles (tenant_id, updated_at);

CREATE INDEX IF NOT EXISTS qualifier_osint_profiles_last_used_idx
  ON qualifier_osint_profiles (tenant_id, last_used_at);

-- ============================================================================
-- 3. RLS — tenant isolation + super admin bypass (003 pattern)
-- ============================================================================

ALTER TABLE qualifier_osint_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE qualifier_osint_profiles FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_osint         ON qualifier_osint_profiles;
DROP POLICY IF EXISTS super_admin_bypass_osint       ON qualifier_osint_profiles;

CREATE POLICY tenant_isolation_osint ON qualifier_osint_profiles
  AS PERMISSIVE FOR ALL
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY super_admin_bypass_osint ON qualifier_osint_profiles
  AS PERMISSIVE FOR ALL
  USING (current_setting('app.is_super_admin', true) = 'true');

-- ============================================================================
-- 4. TRIGGER — updated_at auto-update (003'teki set_updated_at fonksiyonu)
-- ============================================================================

DROP TRIGGER IF EXISTS trg_osint_profiles_updated_at ON qualifier_osint_profiles;
CREATE TRIGGER trg_osint_profiles_updated_at
  BEFORE UPDATE ON qualifier_osint_profiles
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================================
-- 5. GRANTS (app_runtime role — 003'te kurulmuştu)
-- ============================================================================
-- 003'te ALTER DEFAULT PRIVILEGES uygulandı, yeni tablo için zaten GRANT edilir.
-- Guard olarak explicit GRANT'ı tekrar çalıştır (idempotent).

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON qualifier_osint_profiles TO app_runtime';
  END IF;
END $$;
