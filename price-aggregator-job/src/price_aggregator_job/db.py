"""Database helpers using psycopg 3 (synchronous)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg

from price_aggregator_job.config import JobConfig
from price_aggregator_job.sql import (
    AGGREGATION_QUERIES,
    COUNT_SKU_CATALOG,
    CREATE_PRICE_SUMMARY,
    INSERT_JOB_RUN,
    UPDATE_JOB_RUN_ERROR,
    UPDATE_JOB_RUN_OK,
)

log = logging.getLogger(__name__)


def connect(config: JobConfig) -> psycopg.Connection[Any]:
    """Open a synchronous psycopg 3 connection using password auth."""
    conn: psycopg.Connection[Any] = psycopg.connect(
        host=config.pg_host,
        port=config.pg_port,
        dbname=config.pg_database,
        user=config.pg_user,
        password=config.pg_password,
        sslmode=config.pg_sslmode,
        autocommit=False,
    )
    return conn


def ensure_schema(conn: psycopg.Connection[Any]) -> None:
    """Create the ``price_summary`` table and indexes if they don't exist."""
    with conn.cursor() as cur:
        cur.execute(CREATE_PRICE_SUMMARY)
    conn.commit()
    log.info("schema_ensured", extra={"table": "price_summary"})


def check_sku_catalog(conn: psycopg.Connection[Any]) -> int:
    """Return the row count of ``vm_sku_catalog``. Returns 0 if table is empty or missing."""
    try:
        with conn.cursor() as cur:
            cur.execute(COUNT_SKU_CATALOG)
            row = cur.fetchone()
        return int(row[0]) if row else 0
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return 0


def run_aggregations(conn: psycopg.Connection[Any], run_id: str) -> int:
    """Execute all aggregation INSERT...SELECT queries in a single transaction.

    Returns the total number of rows inserted across all queries.
    """
    total = 0
    with conn.cursor() as cur:
        for label, query in AGGREGATION_QUERIES:
            cur.execute(query, {"run_id": run_id})
            rows_affected = cur.rowcount
            total += rows_affected
            log.info(
                "aggregation_done",
                extra={"query": label, "rows_inserted": rows_affected},
            )
    conn.commit()
    return total


# -- Job tracking helpers ------------------------------------------------------


def create_job_run(conn: psycopg.Connection[Any], dataset: str) -> str:
    """Insert a new ``job_runs`` record with status 'running'. Returns the run_id."""
    run_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(INSERT_JOB_RUN, {"run_id": run_id, "dataset": dataset})
    conn.commit()
    return run_id


def complete_job_run(
    conn: psycopg.Connection[Any],
    run_id: str,
    items_read: int,
    items_written: int,
) -> None:
    """Mark a job run as 'ok'."""
    with conn.cursor() as cur:
        cur.execute(
            UPDATE_JOB_RUN_OK,
            {"run_id": run_id, "items_read": items_read, "items_written": items_written},
        )
    conn.commit()


def fail_job_run(
    conn: psycopg.Connection[Any],
    run_id: str,
    error_message: str,
) -> None:
    """Mark a job run as 'error'."""
    with conn.cursor() as cur:
        cur.execute(
            UPDATE_JOB_RUN_ERROR,
            {"run_id": run_id, "error_message": error_message[:4000]},
        )
    conn.commit()
