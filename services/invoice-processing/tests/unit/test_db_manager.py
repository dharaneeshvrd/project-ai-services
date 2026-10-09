"""
Unit tests for db/manager.py DatabaseManager.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from db.manager import DatabaseManager, db_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(job_id="job-1", filename="inv.pdf", input_type="pdf", status="accepted"):
    """Return a MagicMock that quacks like an InvoiceJob."""
    job = MagicMock()
    job.job_id = job_id
    job.filename = filename
    job.input_type = input_type
    job.status = status
    job.submitted_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    job.completed_at = None
    job.error = None
    job.pipeline_path = None
    job.digitize_job_id = None
    job.extract_job_id = None
    job.staged_header = None
    job.staged_lines = None
    job.interface_ref = None
    job.job_metadata = None
    return job


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------

class TestCreateJob:
    def test_creates_row(self):
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("db.manager.get_db_session", return_value=mock_session):
            result = DatabaseManager.create_job(
                job_id="abc-123",
                filename="invoice.pdf",
                input_type="pdf",
            )

        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    def test_returns_none_on_integrity_error(self):
        from sqlalchemy.exc import IntegrityError

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.flush.side_effect = IntegrityError("dup", {}, None)

        with patch("db.manager.get_db_session", return_value=mock_session):
            result = DatabaseManager.create_job(
                job_id="dup-id",
                filename="dup.pdf",
                input_type="pdf",
            )

        assert result is None


# ---------------------------------------------------------------------------
# get_job
# ---------------------------------------------------------------------------

class TestGetJob:
    def test_returns_job(self):
        job = _make_job()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = job

        with patch("db.manager.get_db_session", return_value=mock_session):
            result = DatabaseManager.get_job("job-1")

        assert result is job

    def test_returns_none_when_missing(self):
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = None

        with patch("db.manager.get_db_session", return_value=mock_session):
            result = DatabaseManager.get_job("missing")

        assert result is None


# ---------------------------------------------------------------------------
# update_job
# ---------------------------------------------------------------------------

class TestUpdateJob:
    def test_updates_fields(self):
        job = _make_job()
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = job

        with patch("db.manager.get_db_session", return_value=mock_session):
            result = DatabaseManager.update_job("job-1", status="routing")

        assert job.status == "routing"
        mock_session.flush.assert_called_once()

    def test_sets_completed_at_on_terminal_status(self):
        job = _make_job()
        job.completed_at = None
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = job

        with patch("db.manager.get_db_session", return_value=mock_session):
            DatabaseManager.update_job("job-1", status="completed")

        assert job.completed_at is not None

    def test_returns_none_when_job_missing(self):
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = None

        with patch("db.manager.get_db_session", return_value=mock_session):
            result = DatabaseManager.update_job("ghost", status="failed")

        assert result is None


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------

class TestListJobs:
    def test_returns_rows_and_total(self):
        jobs = [_make_job("j1"), _make_job("j2")]
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        # first execute → count, second → rows
        mock_session.execute.side_effect = [
            MagicMock(scalar_one=MagicMock(return_value=2)),
            MagicMock(scalars=MagicMock(return_value=iter(jobs))),
        ]

        with patch("db.manager.get_db_session", return_value=mock_session):
            rows, total = DatabaseManager.list_jobs(page=1, page_size=20)

        assert total == 2
        assert len(rows) == 2

    def test_returns_empty_on_db_error(self):
        from sqlalchemy.exc import SQLAlchemyError

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.execute.side_effect = SQLAlchemyError("oops")

        with patch("db.manager.get_db_session", return_value=mock_session):
            rows, total = DatabaseManager.list_jobs()

        assert rows == []
        assert total == 0
