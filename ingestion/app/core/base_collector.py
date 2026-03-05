"""Base Data Collector Interface.

Defines the abstract base class for all data collectors.
All collectors must implement this interface to ensure consistent behavior
and orchestration with the PostgreSQL backend.
"""

import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any


class BaseCollector(ABC):
    """Abstract base class for all data collectors."""

    def __init__(
        self,
        job_id: str,
        job_datetime: datetime,
        job_type: str,
        config: dict[str, Any],
    ) -> None:
        self.job_id = job_id
        self.job_datetime = job_datetime
        self.job_type = job_type
        self.config = config
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")

        self.is_local = self.job_type.startswith("local") or config.get("environment", "production") == "local"
        self.max_items = self._parse_max_items(config.get("max_items", "5000"))

        # Statistics
        self.total_collected = 0
        self.total_ingested = 0
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None

    def _parse_max_items(self, max_items_str: str | int | float) -> float:
        """Parse max items configuration, handling -1 as unlimited."""
        try:
            val = str(max_items_str)
            if val == "-1":
                return float("inf")
            return int(val)
        except (ValueError, TypeError):
            self.logger.warning("Invalid max_items value '%s', defaulting to 5000", max_items_str)
            return 5000

    # ------------------------------------------------------------------
    # Abstract properties / methods
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def collector_name(self) -> str:
        """Unique name of this collector (e.g. 'azure_pricing')."""

    @property
    @abstractmethod
    def table_name(self) -> str:
        """PostgreSQL table that receives the collected data."""

    @property
    @abstractmethod
    def table_schema(self) -> str:
        """SQL CREATE TABLE IF NOT EXISTS statement for the target table."""

    @abstractmethod
    def validate_config(self) -> None:
        """Validate collector-specific configuration.

        Raises:
            ValueError: If configuration is invalid.
        """

    @abstractmethod
    def collect_data(self, pg_conn: Any) -> int:
        """Collect data from the source and insert into PostgreSQL.

        Args:
            pg_conn: A *psycopg2* connection object.

        Returns:
            Number of items successfully collected and ingested.
        """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, pg_conn: Any) -> None:
        """Create the target table (if needed) and validate config."""
        self.logger.info("Initializing %s collector", self.collector_name)
        self.validate_config()
        self._create_pg_table(pg_conn)
        self.logger.info("%s collector initialized successfully", self.collector_name)

    def run(self, pg_conn: Any) -> dict[str, Any]:
        """Execute the full collection cycle.

        Args:
            pg_conn: A *psycopg2* connection object.

        Returns:
            Dictionary containing execution results and statistics.
        """
        self.start_time = datetime.now(UTC)

        try:
            self.logger.info("Starting %s data collection", self.collector_name)
            self.initialize(pg_conn)
            self.total_ingested = self.collect_data(pg_conn)
            self.end_time = datetime.now(UTC)
            duration = (self.end_time - self.start_time).total_seconds()

            result: dict[str, Any] = {
                "collector_name": self.collector_name,
                "status": "success",
                "job_id": self.job_id,
                "job_datetime": self.job_datetime.isoformat(),
                "job_type": self.job_type,
                "start_time": self.start_time.isoformat(),
                "end_time": self.end_time.isoformat(),
                "duration_seconds": duration,
                "total_collected": self.total_collected,
                "total_ingested": self.total_ingested,
                "table_name": self.table_name,
            }

            self.logger.info(
                "%s collection completed: %d items in %.1fs",
                self.collector_name,
                self.total_ingested,
                duration,
            )
            return result

        except Exception as exc:
            self.end_time = datetime.now(UTC)
            duration = (self.end_time - self.start_time).total_seconds() if self.start_time else 0

            self.logger.error(
                "%s collection failed after %.1fs: %s",
                self.collector_name,
                duration,
                exc,
            )
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_pg_table(self, pg_conn: Any) -> None:
        """Execute the CREATE TABLE IF NOT EXISTS DDL."""
        try:
            with pg_conn.cursor() as cur:
                cur.execute(self.table_schema)
            pg_conn.commit()
            self.logger.info("PostgreSQL table '%s' created or already exists", self.table_name)
        except Exception:
            pg_conn.rollback()
            self.logger.exception("Error creating PostgreSQL table '%s'", self.table_name)
            raise

    def enrich_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Add common job metadata to a data item."""
        return {
            **item,
            "jobId": self.job_id,
            "jobDateTime": self.job_datetime.isoformat(),
            "jobType": self.job_type,
        }

    def get_stats(self) -> dict[str, Any]:
        """Return current collection statistics."""
        duration = 0.0
        if self.start_time:
            end = self.end_time or datetime.now(UTC)
            duration = (end - self.start_time).total_seconds()

        return {
            "collector_name": self.collector_name,
            "total_collected": self.total_collected,
            "total_ingested": self.total_ingested,
            "duration_seconds": duration,
            "items_per_second": self.total_ingested / duration if duration > 0 else 0,
        }
