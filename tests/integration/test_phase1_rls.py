"""
Phase 1 — PostgreSQL RLS integration tests.

Migration 003_tenants.sql'in RLS enforcement'ini doğrulayan pytest suite.
`/tmp/rls_test_runtime.sh` script'ini pytest'e port eder (CI-friendly).

Gereksinimler:
    - qualifier-db container up + migration 003 applied
    - `app_runtime` role mevcut (migration 003'te yaratıldı)
    - Test DB'ye 2 farklı user ile bağlanabiliyor olmalı

Skip: asyncpg yüklü değilse veya DB erişilemezse.

Usage:
    pytest tests/integration/test_phase1_rls.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

try:
    import asyncpg
    _HAS_ASYNCPG = True
except ImportError:  # pragma: no cover
    _HAS_ASYNCPG = False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _HAS_ASYNCPG, reason="asyncpg not installed"),
]


APP_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app:qualifierpass@qualifier-db:5432/lead_qualifier",
)
# app_runtime user aynı DB'ye bağlanır ama NOBYPASSRLS (migration 003'te yaratıldı)
APP_RUNTIME_DB_URL = APP_DB_URL.replace(
    "app:qualifierpass", "app_runtime:app_runtime_dev_password"
)


@pytest.fixture
async def app_conn():
    """Superuser 'app' connection — RLS'i bypass eder. Setup/teardown için."""
    try:
        conn = await asyncpg.connect(APP_DB_URL)
    except Exception as exc:
        pytest.skip(f"DB unreachable: {exc}")
    try:
        # tenants + RLS var mı kontrol — migration 003 applied mi?
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='tenants')"
        )
        if not exists:
            pytest.skip("migration 003_tenants not applied; tenants table missing")
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def app_runtime_conn():
    """'app_runtime' connection — RLS enforce edilir."""
    try:
        conn = await asyncpg.connect(APP_RUNTIME_DB_URL)
    except Exception as exc:
        pytest.skip(f"app_runtime user unreachable: {exc}")
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def test_tenants(app_conn):
    """İki fake tenant + birer lead yaratır; testten sonra cleanup yapar."""
    await app_conn.execute(
        """
        INSERT INTO tenants (slug, name, source_type, plan, status)
        VALUES ('rls-test-a', 'RLS Test A', 'standalone', 'pro', 'active'),
               ('rls-test-b', 'RLS Test B', 'standalone', 'pro', 'active')
        ON CONFLICT (slug) DO NOTHING
        """
    )
    a_id = await app_conn.fetchval("SELECT id FROM tenants WHERE slug='rls-test-a'")
    b_id = await app_conn.fetchval("SELECT id FROM tenants WHERE slug='rls-test-b'")

    # Her tenant için 1 lead
    await app_conn.execute(
        """
        INSERT INTO qualifier_leads (company_id, lead_id, phone, name, tenant_id)
        VALUES (gen_random_uuid(), 'rls-A-1', '+900000000001', 'Ali A', $1),
               (gen_random_uuid(), 'rls-B-1', '+900000000002', 'Burcu B', $2)
        ON CONFLICT (company_id, lead_id) DO NOTHING
        """,
        a_id,
        b_id,
    )

    yield {"a": a_id, "b": b_id}

    # Cleanup
    await app_conn.execute("DELETE FROM qualifier_leads WHERE lead_id IN ('rls-A-1','rls-B-1')")
    await app_conn.execute("DELETE FROM tenants WHERE slug IN ('rls-test-a','rls-test-b')")


class TestRLSIsolation:
    """RLS doğru enforce ediyor, cross-tenant leak yok."""

    async def test_tenant_a_context_sees_only_a_leads(self, app_runtime_conn, test_tenants):
        async with app_runtime_conn.transaction():
            await app_runtime_conn.execute(
                f"SET LOCAL app.tenant_id = '{test_tenants['a']}'"
            )
            rows = await app_runtime_conn.fetch("SELECT name FROM qualifier_leads")
        names = [r["name"] for r in rows]
        assert "Ali A" in names
        assert "Burcu B" not in names

    async def test_tenant_b_context_sees_only_b_leads(self, app_runtime_conn, test_tenants):
        async with app_runtime_conn.transaction():
            await app_runtime_conn.execute(
                f"SET LOCAL app.tenant_id = '{test_tenants['b']}'"
            )
            rows = await app_runtime_conn.fetch("SELECT name FROM qualifier_leads")
        names = [r["name"] for r in rows]
        assert "Burcu B" in names
        assert "Ali A" not in names

    async def test_no_context_silent_deny(self, app_runtime_conn, test_tenants):
        """Tenant context set edilmemişse RLS hiçbir row döndürmez (silent deny)."""
        count = await app_runtime_conn.fetchval("SELECT COUNT(*) FROM qualifier_leads")
        # Not all leads may be 0 (other test data), but A+B spesifik leads gizli olmalı
        rows = await app_runtime_conn.fetch(
            "SELECT name FROM qualifier_leads WHERE lead_id IN ('rls-A-1','rls-B-1')"
        )
        assert len(rows) == 0

    async def test_cross_tenant_leak_attempt_blocked(self, app_runtime_conn, test_tenants):
        """A context'te B'nin tenant_id'siyle explicit sorgulamak — 0 row dönmeli."""
        async with app_runtime_conn.transaction():
            await app_runtime_conn.execute(
                f"SET LOCAL app.tenant_id = '{test_tenants['a']}'"
            )
            count = await app_runtime_conn.fetchval(
                "SELECT COUNT(*) FROM qualifier_leads WHERE tenant_id = $1",
                test_tenants["b"],
            )
        assert count == 0


class TestSuperAdminBypass:
    """`app.is_super_admin=true` context'inde tüm tenant'lar görünür."""

    async def test_super_admin_sees_both_tenants(self, app_runtime_conn, test_tenants):
        async with app_runtime_conn.transaction():
            await app_runtime_conn.execute("SET LOCAL app.is_super_admin = 'true'")
            rows = await app_runtime_conn.fetch(
                "SELECT name FROM qualifier_leads WHERE lead_id IN ('rls-A-1','rls-B-1') ORDER BY name"
            )
        names = [r["name"] for r in rows]
        assert names == ["Ali A", "Burcu B"]


class TestBackwardCompatibility:
    """Mevcut davranış: `app` superuser bypass → tenant_id=NULL INSERT başarılı."""

    async def test_app_superuser_can_insert_null_tenant_id(self, app_conn):
        """Mevcut uygulama henüz tenant-aware değil; NULL tenant_id INSERT çalışmalı."""
        lead_id = str(uuid.uuid4())
        row = await app_conn.fetchrow(
            """
            INSERT INTO qualifier_leads (company_id, lead_id, phone, name)
            VALUES (gen_random_uuid(), $1, '+900000000099', 'Legacy Test')
            RETURNING id, tenant_id
            """,
            lead_id,
        )
        try:
            assert row["id"] is not None
            assert row["tenant_id"] is None  # nullable confirmed
        finally:
            await app_conn.execute("DELETE FROM qualifier_leads WHERE lead_id = $1", lead_id)

    async def test_app_superuser_bypasses_rls(self, app_conn, test_tenants):
        """app superuser tenant context olmasa bile tüm row'ları görür (bypass)."""
        rows = await app_conn.fetch(
            "SELECT name FROM qualifier_leads WHERE lead_id IN ('rls-A-1','rls-B-1')"
        )
        # Migration app_runtime'a grant verdi ama app superuser bypass sağladığı için hem A hem B görünür
        assert len(rows) == 2


class TestSchemaIntegrity:
    """Migration 003 tablolar ve column'lar doğru kurulmuş."""

    async def test_all_tenant_tables_exist(self, app_conn):
        tables = await app_conn.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname='public'
              AND tablename IN (
                'tenants','tenant_users','tenant_api_keys','tenant_oauth_clients',
                'partners','tenant_usage','tenant_audit_log'
              )
            ORDER BY tablename
            """
        )
        names = {r["tablename"] for r in tables}
        expected = {
            "tenants",
            "tenant_users",
            "tenant_api_keys",
            "tenant_oauth_clients",
            "partners",
            "tenant_usage",
            "tenant_audit_log",
        }
        assert names == expected

    async def test_rls_enabled_on_tenant_scoped_tables(self, app_conn):
        rows = await app_conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public'
              AND c.relname IN (
                'qualifier_leads','qualifier_sessions','qualifier_handoffs',
                'ai_kb_documents','tenant_audit_log'
              )
            ORDER BY c.relname
            """
        )
        for row in rows:
            assert row["relrowsecurity"] is True, f"{row['relname']} RLS not enabled"
            assert row["relforcerowsecurity"] is True, f"{row['relname']} RLS not forced"

    async def test_tenant_id_columns_added(self, app_conn):
        rows = await app_conn.fetch(
            """
            SELECT table_name FROM information_schema.columns
            WHERE column_name='tenant_id' AND table_schema='public'
              AND table_name IN ('qualifier_leads','qualifier_sessions','qualifier_handoffs','ai_kb_documents')
            ORDER BY table_name
            """
        )
        names = {r["table_name"] for r in rows}
        assert names == {
            "qualifier_leads",
            "qualifier_sessions",
            "qualifier_handoffs",
            "ai_kb_documents",
        }

    async def test_app_runtime_role_exists(self, app_conn):
        row = await app_conn.fetchrow(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='app_runtime'"
        )
        assert row is not None
        assert row["rolsuper"] is False
        assert row["rolbypassrls"] is False

    async def test_policies_exist(self, app_conn):
        rows = await app_conn.fetch(
            """
            SELECT DISTINCT polname FROM pg_policy
            WHERE polrelid IN (
                'qualifier_leads'::regclass,
                'qualifier_sessions'::regclass,
                'qualifier_handoffs'::regclass,
                'ai_kb_documents'::regclass,
                'tenant_audit_log'::regclass
            )
            ORDER BY polname
            """
        )
        names = {r["polname"] for r in rows}
        expected = {
            "tenant_isolation_leads",
            "tenant_isolation_sessions",
            "tenant_isolation_handoffs",
            "tenant_isolation_kb",
            "tenant_isolation_audit",
            "super_admin_bypass_leads",
            "super_admin_bypass_sessions",
            "super_admin_bypass_handoffs",
            "super_admin_bypass_kb",
            "super_admin_bypass_audit",
        }
        assert expected.issubset(names)
