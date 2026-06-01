-- Add token_registry.lifi_supported (Alembic revision 202406010010)
-- Run in Railway: Postgres service → Data / Query, or `railway connect postgres` + psql -f
--
-- Prerequisite: table token_registry exists (migration 202406010009).

ALTER TABLE token_registry
  ADD COLUMN IF NOT EXISTS lifi_supported BOOLEAN NOT NULL DEFAULT false;

-- Verify:
-- SELECT column_name, data_type, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'token_registry' AND column_name = 'lifi_supported';

-- If you use Alembic on this DB and current version is exactly 202406010009, also run:
-- UPDATE alembic_version SET version_num = '202406010010' WHERE version_num = '202406010009';
--
-- If you are unsure, check first:
-- SELECT * FROM alembic_version;
-- Prefer: `uv run alembic upgrade head` from CI/local with DATABASE_URL instead of hand-editing alembic_version.
