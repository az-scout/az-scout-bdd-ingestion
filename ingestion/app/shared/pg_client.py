"""PostgreSQL Client Manager.

Provides a singleton wrapper around a psycopg2 connection, analogous
to the ADXClientManager in az-pricing-history but targeting PostgreSQL.
"""

import logging
from typing import Any

import psycopg2  # type: ignore[import-untyped]


class PGClientManager:
    """Manages a synchronous psycopg2 connection to PostgreSQL."""

    def __init__(
        self,
        host: str,
        port: str | int,
        dbname: str,
        user: str,
        password: str,
        sslmode: str = "disable",
    ) -> None:
        self.host = host
        self.port = str(port)
        self.dbname = dbname
        self.user = user
        self.password = password
        self.sslmode = sslmode
        self.logger = logging.getLogger(__name__)
        self._conn: Any | None = None

    def get_connection(self) -> Any:
        """Return a connected psycopg2 connection (create if needed).

        Returns:
            psycopg2 connection object.

        Raises:
            Exception: If the connection cannot be established.
        """
        if self._conn is not None and not self._conn.closed:
            return self._conn

        self.logger.info(
            "Connecting to PostgreSQL %s@%s:%s/%s …",
            self.user,
            self.host,
            self.port,
            self.dbname,
        )

        self._conn = psycopg2.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
            sslmode=self.sslmode,
        )
        self._conn.autocommit = False  # explicit transaction control

        # Quick health check
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1")

        self.logger.info("PostgreSQL connection established successfully")
        return self._conn

    def close(self) -> None:
        """Close the underlying connection."""
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
            self.logger.info("PostgreSQL connection closed")
        self._conn = None
