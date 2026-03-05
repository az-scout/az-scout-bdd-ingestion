"""Tests for the price-aggregator-job."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from price_aggregator_job.config import JobConfig
from price_aggregator_job.main import run

# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------


class TestJobConfig:
    """Test configuration from env vars."""

    def test_from_env_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            cfg = JobConfig.from_env()
        assert cfg.pg_host == "localhost"
        assert cfg.pg_port == 5432
        assert cfg.pg_database == "az_scout"
        assert cfg.pg_user == "postgres"
        assert cfg.pg_password == ""
        assert cfg.pg_sslmode == "disable"
        assert cfg.dataset_name == "price_aggregator"
        assert cfg.dry_run is False
        assert cfg.log_level == "INFO"

    def test_from_env_custom(self) -> None:
        env = {
            "PGHOST": "db.example.com",
            "PGPORT": "5433",
            "PGDATABASE": "testdb",
            "PGUSER": "testuser",
            "PGPASSWORD": "secret",
            "PGSSLMODE": "require",
            "DRY_RUN": "true",
            "LOG_LEVEL": "debug",
            "JOB_DATASET_NAME": "custom_ds",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = JobConfig.from_env()
        assert cfg.pg_host == "db.example.com"
        assert cfg.pg_port == 5433
        assert cfg.pg_database == "testdb"
        assert cfg.dry_run is True
        assert cfg.log_level == "DEBUG"
        assert cfg.dataset_name == "custom_ds"

    def test_safe_repr_masks_password(self) -> None:
        env = {"PGPASSWORD": "supersecret"}
        with patch.dict("os.environ", env, clear=True):
            cfg = JobConfig.from_env()
        safe = cfg.safe_repr()
        assert safe["pg_password"] == "***"
        assert "supersecret" not in str(safe)


# ---------------------------------------------------------------------------
# Dry-run tests
# ---------------------------------------------------------------------------


class TestDryRun:
    """Test the dry-run path (no real DB writes)."""

    @patch("price_aggregator_job.main.connect")
    @patch("price_aggregator_job.main.ensure_schema")
    @patch("price_aggregator_job.main.check_sku_catalog", return_value=100)
    @patch("price_aggregator_job.main.create_job_run", return_value="test-run-id")
    @patch("price_aggregator_job.main.run_aggregations")
    @patch("price_aggregator_job.main.complete_job_run")
    def test_dry_run_skips_aggregations(
        self,
        mock_complete: MagicMock,
        mock_run_agg: MagicMock,
        mock_create_run: MagicMock,
        mock_check: MagicMock,
        mock_ensure: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {"DRY_RUN": "true", "LOG_LEVEL": "warning"}
        with patch.dict("os.environ", env, clear=True):
            run()

        mock_run_agg.assert_not_called()
        mock_complete.assert_called_once()
        # complete_job_run(conn, run_id, items_read=0, items_written=0)
        call_args = mock_complete.call_args
        assert call_args[0][2] == 0  # items_read
        assert call_args[0][3] == 0  # items_written


# ---------------------------------------------------------------------------
# Normal run tests
# ---------------------------------------------------------------------------


class TestNormalRun:
    """Test the normal (non-dry-run) path."""

    @patch("price_aggregator_job.main.connect")
    @patch("price_aggregator_job.main.ensure_schema")
    @patch("price_aggregator_job.main.check_sku_catalog", return_value=250)
    @patch("price_aggregator_job.main.create_job_run", return_value="test-run-id")
    @patch("price_aggregator_job.main.run_aggregations", return_value=48)
    @patch("price_aggregator_job.main.complete_job_run")
    def test_normal_run_calls_aggregations(
        self,
        mock_complete: MagicMock,
        mock_run_agg: MagicMock,
        mock_create_run: MagicMock,
        mock_check: MagicMock,
        mock_ensure: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {"DRY_RUN": "false", "LOG_LEVEL": "warning"}
        with patch.dict("os.environ", env, clear=True):
            run()

        mock_run_agg.assert_called_once_with(mock_conn, "test-run-id")
        mock_complete.assert_called_once()
        call_args = mock_complete.call_args
        assert call_args[0][2] == 250  # items_read = catalog_count
        assert call_args[0][3] == 48  # items_written


# ---------------------------------------------------------------------------
# Empty catalog tests
# ---------------------------------------------------------------------------


class TestEmptyCatalog:
    """Test that the job exits gracefully when vm_sku_catalog is empty."""

    @patch("price_aggregator_job.main.connect")
    @patch("price_aggregator_job.main.ensure_schema")
    @patch("price_aggregator_job.main.check_sku_catalog", return_value=0)
    @patch("price_aggregator_job.main.create_job_run")
    @patch("price_aggregator_job.main.run_aggregations")
    def test_empty_catalog_skips_work(
        self,
        mock_run_agg: MagicMock,
        mock_create_run: MagicMock,
        mock_check: MagicMock,
        mock_ensure: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {"LOG_LEVEL": "warning"}
        with patch.dict("os.environ", env, clear=True):
            run()

        mock_create_run.assert_not_called()
        mock_run_agg.assert_not_called()


# ---------------------------------------------------------------------------
# Error path tests
# ---------------------------------------------------------------------------


class TestErrorPath:
    """Test error handling."""

    @patch("price_aggregator_job.main.connect")
    @patch("price_aggregator_job.main.ensure_schema")
    @patch("price_aggregator_job.main.check_sku_catalog", return_value=100)
    @patch("price_aggregator_job.main.create_job_run", return_value="test-run-id")
    @patch(
        "price_aggregator_job.main.run_aggregations",
        side_effect=RuntimeError("query failed"),
    )
    @patch("price_aggregator_job.main.fail_job_run")
    @patch("price_aggregator_job.main.complete_job_run")
    def test_error_calls_fail_job_run(
        self,
        mock_complete: MagicMock,
        mock_fail: MagicMock,
        mock_run_agg: MagicMock,
        mock_create_run: MagicMock,
        mock_check: MagicMock,
        mock_ensure: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {"LOG_LEVEL": "warning"}
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(RuntimeError, match="query failed"),
        ):
            run()

        mock_fail.assert_called_once()
        mock_complete.assert_not_called()

    @patch("price_aggregator_job.main.connect")
    @patch(
        "price_aggregator_job.main.ensure_schema",
        side_effect=RuntimeError("DB down"),
    )
    def test_error_before_job_run_created(
        self,
        mock_ensure: MagicMock,
        mock_connect: MagicMock,
    ) -> None:
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {"LOG_LEVEL": "warning"}
        with (
            patch.dict("os.environ", env, clear=True),
            pytest.raises(RuntimeError, match="DB down"),
        ):
            run()
