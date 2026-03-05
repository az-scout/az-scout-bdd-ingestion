"""Price Aggregator Job — main entry point.

Pre-computes pricing statistics (avg, median, min, max, percentiles)
per (region × category) and (region × global) for both retail and spot
prices. Results are stored in the ``price_summary`` table for fast
API lookups.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from price_aggregator_job.config import JobConfig
from price_aggregator_job.db import (
    check_sku_catalog,
    complete_job_run,
    connect,
    create_job_run,
    ensure_schema,
    fail_job_run,
    run_aggregations,
)

log = logging.getLogger("price_aggregator_job")


# ---------------------------------------------------------------------------
# Structured JSON log formatter
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line on stdout."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "table",
            "query",
            "rows_inserted",
            "items_read",
            "items_written",
            "duration_ms",
            "errors_count",
            "config",
            "run_id",
            "dataset",
            "dry_run",
            "sku_catalog_count",
        ):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = str(record.exc_info[1])
        return json.dumps(payload, default=str)


def _setup_logging(level: str) -> None:
    """Configure structured JSON logging on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run() -> None:
    """Execute the price aggregator batch job end-to-end."""
    config = JobConfig.from_env()
    _setup_logging(config.log_level)

    log.info("job_started", extra={"config": config.safe_repr()})
    t0 = time.monotonic()

    conn = connect(config)
    run_id: str | None = None
    items_written = 0
    errors_count = 0

    try:
        ensure_schema(conn)

        # -- Prerequisite: vm_sku_catalog must be populated ------------------
        catalog_count = check_sku_catalog(conn)
        log.info("sku_catalog_check", extra={"sku_catalog_count": catalog_count})

        if catalog_count == 0:
            log.warning(
                "sku_catalog_empty — skipping aggregation. "
                "Ensure the sku-mapper-job has run at least once."
            )
            return

        run_id = create_job_run(conn, config.dataset_name)
        log.info("job_run_created", extra={"run_id": run_id, "dataset": config.dataset_name})

        # -- Dry-run mode: log intent and exit -------------------------------
        if config.dry_run:
            log.info(
                "dry_run_complete",
                extra={
                    "dry_run": True,
                    "items_read": 0,
                    "items_written": 0,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "errors_count": 0,
                },
            )
            complete_job_run(conn, run_id, 0, 0)
            return

        # -- Run all aggregation queries -------------------------------------
        items_written = run_aggregations(conn, run_id)
        complete_job_run(conn, run_id, catalog_count, items_written)

    except Exception:
        errors_count = 1
        if run_id:
            try:
                fail_job_run(conn, run_id, _exc_message())
            except Exception:
                log.exception("failed_to_update_job_run")
        log.exception("job_failed")
        raise
    finally:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "job_finished",
            extra={
                "items_read": catalog_count if "catalog_count" in dir() else 0,
                "items_written": items_written,
                "duration_ms": duration_ms,
                "errors_count": errors_count,
            },
        )
        conn.close()


def _exc_message() -> str:
    """Return the current exception message (or empty string)."""
    exc = sys.exc_info()[1]
    return str(exc) if exc else ""
