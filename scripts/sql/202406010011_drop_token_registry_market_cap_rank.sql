-- Drop CoinGecko market-cap rank (registry is LiFi-catalog driven).
ALTER TABLE token_registry DROP COLUMN IF EXISTS market_cap_rank;

-- If using Alembic and version was 202406010010:
-- UPDATE alembic_version SET version_num = '202406010011' WHERE version_num = '202406010010';
