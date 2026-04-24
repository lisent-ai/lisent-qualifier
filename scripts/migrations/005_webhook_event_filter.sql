-- Phase 7.B+ — Outbound webhook event filtering + payload mode.
--
-- Adds two tenant-scoped knobs so admins can:
--   1) pick which event types fan out to their configured webhook URL
--      (wildcard patterns supported, e.g. 'lead.*' or '*')
--   2) choose between a full lead snapshot body or an event-specific
--      minimal body (consumer pulls detail via REST if needed).
--
-- Backward compatibility:
--   - enabled_events default = ARRAY['*']  → existing tenants receive every
--     event type just like before this migration.
--   - payload_mode default = 'full'        → mirrors the current rich body.
--
-- Idempotent — column adds are guarded with IF NOT EXISTS.

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS outbound_webhook_enabled_events TEXT[] NOT NULL DEFAULT ARRAY['*'];

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS outbound_webhook_payload_mode TEXT NOT NULL DEFAULT 'full';

-- CHECK constraints are added separately so re-running the migration on a
-- DB that already has the columns doesn't fail on a duplicate constraint.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tenants_outbound_webhook_payload_mode_chk'
    ) THEN
        ALTER TABLE tenants
            ADD CONSTRAINT tenants_outbound_webhook_payload_mode_chk
            CHECK (outbound_webhook_payload_mode IN ('full', 'minimal'));
    END IF;
END $$;
