-- Migration: allow multiple snapshots per SKU by adding job_id to UNIQUE constraint
-- Run this ONCE on your existing PostgreSQL database before the ADX migration.
--
-- Before: 1 row per SKU (ingestion overwrites previous snapshot)
-- After:  1 row per SKU per job (ingestion accumulates daily snapshots)

BEGIN;

-- Drop the old constraint (auto-generated name truncated to 63 chars by PG)
ALTER TABLE retail_prices_vm
    DROP CONSTRAINT IF EXISTS retail_prices_vm_currency_code_arm_region_name_sku_id_prici_key;

-- Create the new constraint including job_id
ALTER TABLE retail_prices_vm
    ADD CONSTRAINT retail_prices_vm_unique_with_job
    UNIQUE (currency_code, arm_region_name, sku_id, pricing_type, reservation_term, job_id);

COMMIT;
