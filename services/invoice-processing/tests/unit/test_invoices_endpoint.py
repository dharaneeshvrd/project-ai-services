"""
Unit tests for api/v1/invoices.py endpoints.
"""

import io
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from app import _register_invoice_schema
from settings import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_job(job_id="job-abc", filename="inv.pdf", input_type="pdf", status="accepted"):
    job = MagicMock()
    job.job_id = job_id
    job.filename = filename
    job.input_type = input_type
    job.pipeline_path = None
    job.status = status
    job.submitted_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    job.completed_at = None
    job.updated_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    job.error = None
    job.digitize_job_id = None
    job.extract_job_id = None
    job.staged_header = None
    job.staged_lines = None
    job.interface_ref = None
    job.job_metadata = None
    return job


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /v1/invoices — submit
# ---------------------------------------------------------------------------

class TestSubmitInvoice:
    def test_pdf_accepted(self, client):
        with patch("api.v1.invoices._stage_uploaded_file") as mock_stage, \
             patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices._run_pipeline"):
            mock_stage.return_value = MagicMock()
            mock_mgr.create_job.return_value = MagicMock()

            resp = client.post(
                "/v1/invoices",
                files={"file": ("invoice.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["input_type"] == "pdf"
        assert "job_id" in body

    def test_image_accepted(self, client):
        with patch("api.v1.invoices._stage_uploaded_file") as mock_stage, \
             patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices._run_pipeline"):
            mock_stage.return_value = MagicMock()
            mock_mgr.create_job.return_value = MagicMock()

            resp = client.post(
                "/v1/invoices",
                files={"file": ("scan.png", io.BytesIO(b"\x89PNG"), "image/png")},
            )

        assert resp.status_code == 202
        assert resp.json()["input_type"] == "image"

    def test_unsupported_extension_returns_422(self, client):
        resp = client.post(
            "/v1/invoices",
            files={"file": ("data.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code == 422

    def test_jpeg_accepted(self, client):
        with patch("api.v1.invoices._stage_uploaded_file") as mock_stage, \
             patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices.asyncio.create_task"):
            mock_stage.return_value = MagicMock()
            mock_mgr.create_job.return_value = MagicMock()

            resp = client.post(
                "/v1/invoices",
                files={"file": ("photo.jpeg", io.BytesIO(b"\xFF\xD8\xFF"), "image/jpeg")},
            )

        assert resp.status_code == 202

    def test_tiff_accepted(self, client):
        with patch("api.v1.invoices._stage_uploaded_file") as mock_stage, \
             patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices.asyncio.create_task"):
            mock_stage.return_value = MagicMock()
            mock_mgr.create_job.return_value = MagicMock()

            resp = client.post(
                "/v1/invoices",
                files={"file": ("scan.tiff", io.BytesIO(b"II*\x00"), "image/tiff")},
            )

        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# GET /v1/invoices/{job_id}
# ---------------------------------------------------------------------------

class TestGetInvoiceJob:
    def test_returns_200_for_known_job(self, client):
        job = _fake_job()
        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = job

            resp = client.get("/v1/invoices/job-abc")

        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == "job-abc"
        assert body["status"] == "accepted"

    def test_returns_404_for_unknown_job(self, client):
        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = None

            resp = client.get("/v1/invoices/no-such-job")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1/invoices
# ---------------------------------------------------------------------------

class TestListInvoiceJobs:
    def test_returns_paginated_list(self, client):
        jobs = [_fake_job("j1"), _fake_job("j2")]
        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.list_jobs.return_value = (jobs, 2)

            resp = client.get("/v1/invoices")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["total_items"] == 2

    def test_status_filter_forwarded(self, client):
        with patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices._run_pipeline"):
            mock_mgr.list_jobs.return_value = ([], 0)

            client.get("/v1/invoices?status=completed")

        mock_mgr.list_jobs.assert_called_once_with(
            page=1, page_size=20, status_filter="completed"
        )


# ---------------------------------------------------------------------------
# _run_pipeline routing — both branches (mocked downstream calls)
# ---------------------------------------------------------------------------

class TestRunPipelineRouting:
    """Verify _run_pipeline dispatches to the correct path function and logs at INFO."""

    def _make_staged_path(self, tmp_path: Path, name: str) -> Path:
        p = tmp_path / name
        p.write_bytes(b"dummy")
        return p

    def test_image_routes_to_oneshot_path(self, tmp_path, caplog):
        """Image input_type must call run_oneshot_path and never run_pdf_path."""
        import logging
        from api.v1.invoices import _run_pipeline

        staged = self._make_staged_path(tmp_path, "scan.png")

        with patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices.run_oneshot_path", new_callable=AsyncMock) as mock_oneshot, \
             patch("api.v1.invoices.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
             patch("api.v1.invoices.ExtractClient") as mock_extract_cls, \
             caplog.at_level(logging.INFO, logger="api.v1.invoices"):

            mock_oneshot.return_value = {}
            asyncio.run(_run_pipeline("job-img-1", staged, "image"))

        mock_oneshot.assert_called_once()
        call_kwargs = mock_oneshot.call_args
        assert call_kwargs.kwargs["job_id"] == "job-img-1"
        assert call_kwargs.kwargs["staged_path"] == staged
        # ExtractClient must be instantiated; DigitizeClient must NOT be touched
        mock_extract_cls.assert_called_once()
        mock_pdf.assert_not_called()
        assert "One-Shot path" in caplog.text

    def test_pdf_routes_to_pdf_path(self, tmp_path, caplog):
        """PDF input_type must call run_pdf_path and never run_oneshot_path."""
        import logging
        from api.v1.invoices import _run_pipeline

        staged = self._make_staged_path(tmp_path, "invoice.pdf")

        with patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
             patch("api.v1.invoices.run_oneshot_path", new_callable=AsyncMock) as mock_oneshot, \
             patch("api.v1.invoices.DigitizeClient") as mock_digitize_cls, \
             caplog.at_level(logging.INFO, logger="api.v1.invoices"):

            mock_pdf.return_value = {}
            asyncio.run(_run_pipeline("job-pdf-1", staged, "pdf"))

        mock_pdf.assert_called_once()
        call_kwargs = mock_pdf.call_args
        assert call_kwargs.kwargs["job_id"] == "job-pdf-1"
        assert call_kwargs.kwargs["staged_path"] == staged
        # DigitizeClient must be instantiated; ExtractClient must NOT be touched
        mock_digitize_cls.assert_called_once()
        mock_oneshot.assert_not_called()
        assert "PDF path" in caplog.text

    def test_routing_logs_at_info_level(self, tmp_path, caplog):
        """Routing decision must be logged at INFO (not DEBUG or WARNING)."""
        import logging
        from api.v1.invoices import _run_pipeline

        staged = self._make_staged_path(tmp_path, "doc.pdf")

        with patch("api.v1.invoices.db_manager"), \
             patch("api.v1.invoices.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
             patch("api.v1.invoices.DigitizeClient"), \
             caplog.at_level(logging.DEBUG, logger="api.v1.invoices"):

            mock_pdf.return_value = {}
            asyncio.run(_run_pipeline("job-log-1", staged, "pdf"))

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("PDF path" in m for m in info_messages), \
            f"Expected INFO log containing 'PDF path'; got: {info_messages}"

    def test_pipeline_marks_routing_status_first(self, tmp_path):
        """db_manager.update_job must be called with status='routing' before the path call."""
        from api.v1.invoices import _run_pipeline

        staged = self._make_staged_path(tmp_path, "scan.jpg")
        call_order = []

        async def fake_oneshot(**kwargs):
            call_order.append("oneshot")
            return {}

        with patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices.run_oneshot_path", side_effect=fake_oneshot), \
             patch("api.v1.invoices.ExtractClient"):

            mock_mgr.update_job.side_effect = lambda **kw: call_order.append(
                ("update_job", kw.get("status"))
            )
            asyncio.run(_run_pipeline("job-order-1", staged, "image"))

        assert ("update_job", "routing") in call_order
        routing_idx = call_order.index(("update_job", "routing"))
        oneshot_idx = call_order.index("oneshot")
        assert routing_idx < oneshot_idx

    def test_pipeline_marks_failed_on_path_error(self, tmp_path):
        """If the path function raises, job must be marked 'failed'."""
        from api.v1.invoices import _run_pipeline

        staged = self._make_staged_path(tmp_path, "bad.pdf")

        with patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch("api.v1.invoices.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
             patch("api.v1.invoices.DigitizeClient"):

            mock_pdf.side_effect = RuntimeError("digitize down")
            asyncio.run(_run_pipeline("job-fail-1", staged, "pdf"))

        failed_calls = [
            c for c in mock_mgr.update_job.call_args_list
            if c.kwargs.get("status") == "failed"
        ]
        assert failed_calls, "Expected update_job(status='failed') to be called"
        assert "digitize down" in failed_calls[0].kwargs.get("error", "")


# ---------------------------------------------------------------------------
# Stub endpoints (approve / reject) still return 501
# ---------------------------------------------------------------------------

def test_approve_stub_returns_501(client):
    resp = client.post("/v1/invoices/some-job/approve")
    assert resp.status_code == 501


def test_reject_stub_returns_501(client):
    resp = client.post("/v1/invoices/some-job/reject")
    assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Schema registration (kept from scaffold)
# ---------------------------------------------------------------------------

def test_register_schema_success():
    with patch.object(settings.invoice, "extract_url", "http://extract-test:6000"):
        mock_resp = MagicMock(status_code=201)
        with patch("requests.post", return_value=mock_resp) as mock_post:
            _register_invoice_schema()
            mock_post.assert_called_once()


def test_register_schema_idempotent_409():
    with patch.object(settings.invoice, "extract_url", "http://extract-test:6000"):
        mock_resp = MagicMock(status_code=409)
        with patch("requests.post", return_value=mock_resp) as mock_post:
            _register_invoice_schema()
            mock_post.assert_called_once()


def test_register_schema_failure_raises():
    with patch.object(settings.invoice, "extract_url", "http://extract-test:6000"):
        mock_resp = MagicMock(status_code=500, text="Internal Server Error")
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Failed to register invoice schema"):
                _register_invoice_schema()


def test_register_schema_network_error_raises():
    with patch.object(settings.invoice, "extract_url", "http://extract-test:6000"):
        with patch("requests.post", side_effect=requests.RequestException("Connection refused")):
            with pytest.raises(RuntimeError, match="Network error connecting to extract service"):
                _register_invoice_schema()
