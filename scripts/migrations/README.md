# AI Lead Qualifier — Database Migrations

Sequential, numeric-prefixed `.sql` files. Applied in filename order by `scripts/migrate.sh`.

## Rules

- **Idempotent.** Every statement must be safe to run more than once (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, guarded `DO $$ ... $$` blocks for constraints).
- **Forward-only.** No down migrations. Rolling back a bad change is done by writing a new migration that reverses it (or by restoring from a backup for destructive changes).
- **One change per file.** Do not bundle unrelated schema changes.
- **Filenames** must match `NNN_short_description.sql`, where `NNN` is zero-padded 3 digits. The number is the version key tracked in `schema_migrations`.
- **Tracking.** `migrate.sh` creates a `schema_migrations (version TEXT PK, applied_at TIMESTAMPTZ)` table on first run. Applied filenames (without `.sql`) are stored there; already-applied migrations are skipped.

## Running

```bash
# Local dev — Postgres running on :5433 (docker-compose)
DATABASE_URL=postgresql://app:qualifierpass@localhost:5433/lead_qualifier \
  ./scripts/migrate.sh

# Staging / prod — via docker-compose migration runner
docker compose -f docker-compose.yml -f docker-compose.migrate.yml run --rm migrate
```

## Pre-change checklist (production)

1. **Backup.** `pg_dump` the target database.
2. **Dry-run on staging** with a copy of production data.
3. **Review diff.** What tables / columns / indexes / constraints change? Any lock concerns on large tables?
4. **Roll-out.** Canary deploy; monitor error rate for 30 min before promoting to 100%.
5. **Rollback plan.** Document what a "restore from backup" would entail if the migration misbehaves.

## Migration index

- `001_init.sql` — baseline (qualifier_leads, qualifier_sessions, qualifier_handoffs, activity_log, handoff_attempts). Mirrors `scripts/db_init.sql`; kept idempotent so re-running on an already-initialized DB is a no-op.
