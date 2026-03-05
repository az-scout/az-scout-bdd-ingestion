#!/usr/bin/env python3
"""
ADX → PostgreSQL Migration Script
==================================

Migrates ALL historical pricing data from Azure Data Explorer (pricing_metrics)
into PostgreSQL (retail_prices_vm), job by job.

Each ADX jobId maps to one job_runs entry in PG (dataset='adx_migration').
Supports --resume to skip already-migrated jobs on restart.

Usage:
    python migrate_adx_to_pg.py
    python migrate_adx_to_pg.py --resume --batch-size 2000
    python migrate_adx_to_pg.py --dry-run
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2  # type: ignore[import-untyped]
from psycopg2.extras import execute_values  # type: ignore[import-untyped]
from azure.identity import DefaultAzureCredential
from azure.kusto.data import ClientRequestProperties, KustoClient, KustoConnectionStringBuilder

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_ADX_CLUSTER = (
    "https://az-pricing-tool-adx.germanywestcentral.kusto.windows.net"
)
DEFAULT_ADX_DATABASE = "pricing-metrics"
DEFAULT_BATCH_SIZE = 5000
MIGRATION_DATASET = "adx_migration"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("migrate_adx_to_pg")

# ---------------------------------------------------------------------------
# INSERT SQL — mirrors ingestion/app/collectors/azure_pricing_collector.py
# ---------------------------------------------------------------------------
INSERT_SQL = """
INSERT INTO retail_prices_vm (
    job_id, job_datetime, job_type,
    currency_code, tier_minimum_units,
    retail_price, unit_price,
    arm_region_name, location,
    effective_start_date,
    meter_id, meter_name,
    product_id, sku_id, product_name, sku_name,
    service_name, service_id, service_family,
    unit_of_measure, pricing_type,
    is_primary_meter_region, arm_sku_name,
    reservation_term, savings_plan
) VALUES %s
ON CONFLICT (currency_code, arm_region_name, sku_id, pricing_type, reservation_term, job_id)
DO NOTHING
"""

# ADX column names (camelCase) — used to map KQL result rows
ADX_COLUMNS = [
    "jobId", "jobDateTime", "jobType",
    "currencyCode", "tierMinimumUnits",
    "retailPrice", "unitPrice",
    "armRegionName", "location",
    "effectiveStartDate",
    "meterId", "meterName",
    "productId", "skuId", "productName", "skuName",
    "serviceName", "serviceId", "serviceFamily",
    "unitOfMeasure", "type",
    "isPrimaryMeterRegion", "armSkuName",
    "reservationTerm", "savingsPlan",
]


# ---------------------------------------------------------------------------
# .env.local loader
# ---------------------------------------------------------------------------
def load_env_file() -> None:
    """Load env vars from .env.local if it exists (key=value lines)."""
    for candidate in [
        Path.cwd() / ".env.local",
        Path(__file__).parent / ".env.local",
    ]:
        if candidate.is_file():
            log.info("Loading env from %s", candidate)
            with open(candidate) as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key, _, value = line.partition("=")
                    key, value = key.strip(), value.strip()
                    if key and key not in os.environ:
                        os.environ[key] = value
            return


# ---------------------------------------------------------------------------
# ADX helpers
# ---------------------------------------------------------------------------
def create_adx_client(cluster_uri: str, tenant_id: str | None) -> KustoClient:
    """Authenticate to ADX using DefaultAzureCredential with auto-refresh."""
    kwargs: dict[str, object] = {"exclude_managed_identity_credential": True}
    if tenant_id:
        kwargs["additionally_allowed_tenants"] = [tenant_id]

    credential = DefaultAzureCredential(**kwargs)
    scope = "https://help.kusto.windows.net/.default"

    def token_provider() -> str:
        """Called by the Kusto SDK each time a token is needed — auto-refreshes."""
        return credential.get_token(scope).token

    kcsb = KustoConnectionStringBuilder.with_token_provider(
        cluster_uri, token_provider,
    )
    return KustoClient(kcsb)


def list_adx_jobs(
    client: KustoClient, database: str,
) -> list[dict[str, str]]:
    """Return distinct (jobId, jobDateTime) ordered by date ascending."""
    kql = (
        "pricing_metrics "
        "| distinct jobId, jobDateTime "
        "| order by jobDateTime asc"
    )
    response = client.execute(database, kql)
    jobs: list[dict[str, str]] = []
    for row in response.primary_results[0]:
        jobs.append({
            "jobId": str(row["jobId"]),
            "jobDateTime": str(row["jobDateTime"]),
        })
    return jobs


def query_adx_job(
    client: KustoClient, database: str, job_id: str,
) -> list[dict[str, object]]:
    """Fetch all rows for a single ADX job. Returns list of dicts."""
    kql = f"pricing_metrics | where jobId == '{job_id}'"
    properties = ClientRequestProperties()
    properties.set_option("notruncation", True)
    response = client.execute(database, kql, properties)
    primary = response.primary_results[0]

    # Build column-index map from the result set
    col_names = [col.column_name for col in primary.columns]

    rows: list[dict[str, object]] = []
    for row in primary:
        item: dict[str, object] = {}
        for col_name in col_names:
            item[col_name] = row[col_name]
        rows.append(item)
    return rows


# ---------------------------------------------------------------------------
# PG helpers
# ---------------------------------------------------------------------------
def create_pg_connection() -> "psycopg2.extensions.connection":
    """Create a psycopg2 connection from env vars."""
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "azscout"),
        user=os.environ.get("POSTGRES_USER", "azscout"),
        password=os.environ.get("POSTGRES_PASSWORD", "azscout"),
        sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
    )
    conn.autocommit = False
    return conn


def get_migrated_job_ids(conn: "psycopg2.extensions.connection") -> set[str]:
    """Return ADX jobIds already migrated (stored in job_runs.details->'adx_job_id')."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT details->>'adx_job_id' FROM job_runs "
            "WHERE dataset = %s AND status = 'ok'",
            (MIGRATION_DATASET,),
        )
        return {row[0] for row in cur.fetchall() if row[0]}


def insert_job_run(
    conn: "psycopg2.extensions.connection",
    run_id: str,
    adx_job_id: str,
    adx_job_datetime: str,
) -> None:
    """Insert a job_runs row for this migration chunk."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO job_runs (run_id, dataset, status, started_at_utc, details) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                run_id,
                MIGRATION_DATASET,
                "running",
                datetime.now(timezone.utc),
                json.dumps({
                    "adx_job_id": adx_job_id,
                    "adx_job_datetime": adx_job_datetime,
                }),
            ),
        )
    conn.commit()


def update_job_run(
    conn: "psycopg2.extensions.connection",
    run_id: str,
    status: str,
    items_read: int,
    items_written: int,
    error_message: str | None = None,
) -> None:
    """Update a job_runs row after processing."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE job_runs SET status = %s, finished_at_utc = %s, "
            "items_read = %s, items_written = %s, error_message = %s "
            "WHERE run_id = %s",
            (
                status,
                datetime.now(timezone.utc),
                items_read,
                items_written,
                error_message,
                run_id,
            ),
        )
    conn.commit()


def _row_to_tuple(item: dict[str, object]) -> tuple[object, ...]:
    """Convert an ADX row dict to a VALUES tuple (25 columns)."""
    savings_plan = item.get("savingsPlan")
    return (
        item.get("jobId"),
        item.get("jobDateTime"),
        item.get("jobType"),
        item.get("currencyCode"),
        item.get("tierMinimumUnits"),
        item.get("retailPrice"),
        item.get("unitPrice"),
        item.get("armRegionName"),
        item.get("location"),
        item.get("effectiveStartDate"),
        item.get("meterId"),
        item.get("meterName"),
        item.get("productId"),
        item.get("skuId"),
        item.get("productName"),
        item.get("skuName"),
        item.get("serviceName"),
        item.get("serviceId"),
        item.get("serviceFamily"),
        item.get("unitOfMeasure"),
        item.get("type"),
        item.get("isPrimaryMeterRegion"),
        item.get("armSkuName"),
        item.get("reservationTerm"),
        json.dumps(savings_plan) if savings_plan is not None else None,
    )


def ingest_batch(
    conn: "psycopg2.extensions.connection",
    items: list[dict[str, object]],
) -> int:
    """Insert a batch of rows using execute_values (multi-row INSERT). Returns rows written."""
    if not items:
        return 0
    values = [_row_to_tuple(item) for item in items]
    with conn.cursor() as cur:
        execute_values(cur, INSERT_SQL, values, page_size=len(values))
        written = cur.rowcount
    conn.commit()
    return written


# ---------------------------------------------------------------------------
# Main migration
# ---------------------------------------------------------------------------
def migrate(args: argparse.Namespace) -> None:
    load_env_file()

    cluster_uri = os.environ.get("ADX_CLUSTER_URI", DEFAULT_ADX_CLUSTER)
    database = os.environ.get("ADX_DATABASE_NAME", DEFAULT_ADX_DATABASE)
    tenant_id = os.environ.get("ADX_TENANT_ID")

    log.info("ADX cluster : %s", cluster_uri)
    log.info("ADX database: %s", database)
    log.info("Batch size  : %d", args.batch_size)
    log.info("Resume      : %s", args.resume)
    log.info("Dry-run     : %s", args.dry_run)

    # --- ADX connection ---
    log.info("Connecting to ADX …")
    adx = create_adx_client(cluster_uri, tenant_id)

    # --- PG connection ---
    log.info("Connecting to PostgreSQL …")
    pg_conn = create_pg_connection()

    try:
        # --- List ADX jobs ---
        log.info("Listing ADX jobs …")
        jobs = list_adx_jobs(adx, database)
        log.info("Found %d ADX jobs", len(jobs))

        # --- Resume support ---
        skip_ids: set[str] = set()
        if args.resume:
            skip_ids = get_migrated_job_ids(pg_conn)
            log.info("Resume: %d jobs already migrated, will skip", len(skip_ids))

        pending = [j for j in jobs if j["jobId"] not in skip_ids]
        log.info("Jobs to migrate: %d", len(pending))

        if args.dry_run:
            log.info("DRY-RUN — no data will be written")
            for j in pending:
                log.info("  would migrate jobId=%s  date=%s", j["jobId"], j["jobDateTime"])
            return

        # --- Process each job ---
        grand_read = 0
        grand_written = 0
        migration_start = time.monotonic()

        for idx, job in enumerate(pending, 1):
            job_id = job["jobId"]
            job_dt = job["jobDateTime"]
            run_id = str(uuid.uuid4())

            log.info(
                "[%d/%d] Migrating jobId=%s  date=%s",
                idx, len(pending), job_id, job_dt,
            )

            # Record start
            insert_job_run(pg_conn, run_id, job_id, job_dt)

            try:
                # Query ADX for this job
                rows = query_adx_job(adx, database, job_id)
                job_read = len(rows)
                grand_read += job_read
                log.info("  fetched %d rows from ADX", job_read)

                # Batch-insert into PG
                job_written = 0
                for batch_start in range(0, len(rows), args.batch_size):
                    batch = rows[batch_start : batch_start + args.batch_size]
                    job_written += ingest_batch(pg_conn, batch)

                grand_written += job_written
                log.info("  inserted %d rows into PG (%d skipped)",
                         job_written, job_read - job_written)

                update_job_run(pg_conn, run_id, "ok", job_read, job_written)

            except Exception as exc:
                log.error("  FAILED jobId=%s: %s", job_id, exc)
                try:
                    pg_conn.rollback()
                except Exception:
                    pass
                update_job_run(pg_conn, run_id, "error", 0, 0, str(exc)[:500])
                # Continue with next job
                continue

        elapsed = time.monotonic() - migration_start
        log.info("=" * 60)
        log.info("Migration complete")
        log.info("  Jobs processed : %d / %d", len(pending), len(jobs))
        log.info("  Total read     : %d", grand_read)
        log.info("  Total written  : %d", grand_written)
        log.info("  Total skipped  : %d", grand_read - grand_written)
        log.info("  Elapsed        : %.1f s (%.1f min)", elapsed, elapsed / 60)

    finally:
        pg_conn.close()
        log.info("PostgreSQL connection closed")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate pricing data from ADX to PostgreSQL",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per PG commit (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip ADX jobs already migrated (matched via job_runs)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List jobs without writing any data",
    )
    args = parser.parse_args()
    migrate(args)


if __name__ == "__main__":
    main()
