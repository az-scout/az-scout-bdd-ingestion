# ADX → PostgreSQL Migration

Migrates all historical pricing data from Azure Data Explorer (`pricing_metrics`, ~186M rows, 225 daily snapshots) into the PostgreSQL `retail_prices_vm` table.

## Prerequisites

1. **Azure CLI login** with access to the ADX cluster:
   ```bash
   az login --tenant <TENANT_ID>
   ```
2. **PostgreSQL** `retail_prices_vm` table with the updated UNIQUE constraint that includes `job_id` (see `alter_unique.sql`).
3. **Python 3.11+**

## Setup

```bash
cd migration/
pip install -r requirements.txt

# Copy and fill in credentials
cp .env.example .env.local
# Edit .env.local with your POSTGRES_PASSWORD
```

## Usage

### Dry run — list jobs without writing
```bash
python migrate_adx_to_pg.py --dry-run
```

### Full migration
```bash
python migrate_adx_to_pg.py
```

### Resume after interruption
```bash
python migrate_adx_to_pg.py --resume
```

### Custom batch size
```bash
python migrate_adx_to_pg.py --resume --batch-size 2000
```

## How it works

1. Connects to ADX and lists all distinct `(jobId, jobDateTime)` pairs, ordered by date.
2. Connects to PostgreSQL.
3. For each ADX job (in chronological order):
   - Creates a `job_runs` row (`dataset='adx_migration'`, `status='running'`).
   - Queries `pricing_metrics | where jobId == '<id>'` to fetch all rows for that job.
   - Batch-inserts into `retail_prices_vm` with `ON CONFLICT DO NOTHING`.
   - Updates the `job_runs` row with final status, `items_read`, and `items_written`.
4. Per-job failures are logged and recorded in `job_runs` (status `'error'`) — the script continues with the next job.

## Resume support

With `--resume`, the script queries `job_runs WHERE dataset = 'adx_migration' AND status = 'ok'` to find already-migrated ADX job IDs and skips them. This lets you safely restart after a failure or interruption.

## Verification

```sql
-- Total rows after migration
SELECT COUNT(*) FROM retail_prices_vm;

-- Migration job runs
SELECT run_id, status, items_read, items_written, started_at_utc, finished_at_utc,
       details->>'adx_job_id' AS adx_job_id
FROM job_runs
WHERE dataset = 'adx_migration'
ORDER BY started_at_utc;

-- Summary
SELECT status, COUNT(*), SUM(items_read), SUM(items_written)
FROM job_runs WHERE dataset = 'adx_migration'
GROUP BY status;
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ADX_CLUSTER_URI` | `https://az-pricing-tool-adx.germanywestcentral.kusto.windows.net` | ADX cluster endpoint |
| `ADX_DATABASE_NAME` | `pricing-metrics` | ADX database |
| `ADX_TENANT_ID` | *(none)* | Azure tenant ID (for guest accounts) |
| `POSTGRES_HOST` | `localhost` | PG host |
| `POSTGRES_PORT` | `5432` | PG port |
| `POSTGRES_DB` | `azscout` | PG database |
| `POSTGRES_USER` | `azscout` | PG user |
| `POSTGRES_PASSWORD` | `azscout` | PG password |
| `POSTGRES_SSLMODE` | `disable` | PG SSL mode (`require` for Azure) |
