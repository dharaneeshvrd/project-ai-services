"""
Unit tests for api/v1/invoices.py endpoints.
"""
import io
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from fastapi.testclient import TestClient
from models import UpdateInvoiceJobRequest


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
    job.job_metadata = None
    return job


# ---------------------------------------------------------------------------
# UpdateInvoiceJobRequest model tests
# ---------------------------------------------------------------------------

def test_update_request_all_fields():
    """Both reviewer-editable fields are accepted."""
    body = UpdateInvoiceJobRequest(
        staged_header={"invoice_num": "INV-001"},
        staged_lines=[{"amount": 100.0}],
    )
    assert body.staged_header == {"invoice_num": "INV-001"}
    assert body.staged_lines == [{"amount": 100.0}]


def test_update_request_all_optional_defaults_none():
    """Omitting all fields yields None defaults — no fields are required."""
    body = UpdateInvoiceJobRequest()
    assert body.staged_header is None
    assert body.staged_lines is None


def test_update_request_partial_update():
    """Only the supplied field is set; the rest remain None."""
    body = UpdateInvoiceJobRequest(staged_header={"invoice_num": "INV-002"})
    assert body.staged_header == {"invoice_num": "INV-002"}
    assert body.staged_lines is None


# ---------------------------------------------------------------------------
# Schema registration failure
# ---------------------------------------------------------------------------

def test_submit_invoice_schema_registration_failure_returns_503(client, tmp_path):
    """When the extract service is unreachable, schema registration raises HTTP 503."""
    from fastapi import HTTPException
    from settings import InvoiceProcessingConfig

    async def _raise_503():
        raise HTTPException(status_code=503, detail="Extract service unavailable")

    with patch("api.v1.invoices.ensure_invoice_schema_registered", side_effect=_raise_503), \
         patch.object(InvoiceProcessingConfig, "staging_dir", new_callable=PropertyMock, return_value=tmp_path / "staging"):

        resp = client.post(
            "/v1/invoices",
            files={"file": ("invoice.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
        )

    assert resp.status_code == 503


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

            client.get("/v1/invoices?status=approved")

        mock_mgr.list_jobs.assert_called_once_with(
            page=1, page_size=20, status_filter="approved"
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

        with patch("pipeline.orchestrator.db_manager") as mock_mgr, \
             patch("pipeline.orchestrator.run_oneshot_path", new_callable=AsyncMock) as mock_oneshot, \
             patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
             patch("pipeline.orchestrator.ExtractClient") as mock_extract_cls, \
             caplog.at_level(logging.INFO, logger="pipeline.orchestrator"):

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

        with patch("pipeline.orchestrator.db_manager") as mock_mgr, \
             patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
             patch("pipeline.orchestrator.run_oneshot_path", new_callable=AsyncMock) as mock_oneshot, \
             patch("pipeline.orchestrator.DigitizeClient") as mock_digitize_cls, \
             caplog.at_level(logging.INFO, logger="pipeline.orchestrator"):

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

        with patch("pipeline.orchestrator.db_manager"), \
             patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
             patch("pipeline.orchestrator.DigitizeClient"), \
             caplog.at_level(logging.DEBUG, logger="pipeline.orchestrator"):

            mock_pdf.return_value = {}
            asyncio.run(_run_pipeline("job-log-1", staged, "pdf"))

        info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("PDF path" in m for m in info_messages), \
            f"Expected INFO log containing 'PDF path'; got: {info_messages}"

    def test_pipeline_marks_extracting_status_first(self, tmp_path):
        """db_manager.update_job must be called with status='extracting' before the path call."""
        from api.v1.invoices import _run_pipeline

        staged = self._make_staged_path(tmp_path, "scan.jpg")
        call_order = []

        async def fake_oneshot(**kwargs):
            call_order.append("oneshot")
            return {}

        with patch("pipeline.orchestrator.db_manager") as mock_mgr, \
             patch("pipeline.orchestrator.run_oneshot_path", side_effect=fake_oneshot), \
             patch("pipeline.orchestrator.ExtractClient"):

            mock_mgr.update_job.side_effect = lambda **kw: call_order.append(
                ("update_job", kw.get("status"))
            )
            asyncio.run(_run_pipeline("job-order-1", staged, "image"))

        assert ("update_job", "extracting") in call_order
        extracting_idx = call_order.index(("update_job", "extracting"))
        oneshot_idx = call_order.index("oneshot")
        assert extracting_idx < oneshot_idx

    def test_pipeline_marks_failed_on_path_error(self, tmp_path):
        """If the path function raises, job must be marked 'failed'."""
        from api.v1.invoices import _run_pipeline

        staged = self._make_staged_path(tmp_path, "bad.pdf")

        with patch("pipeline.orchestrator.db_manager") as mock_mgr, \
             patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
             patch("pipeline.orchestrator.DigitizeClient"):

            mock_pdf.side_effect = RuntimeError("digitize down")
            asyncio.run(_run_pipeline("job-fail-1", staged, "pdf"))

        failed_calls = [
            c for c in mock_mgr.update_job.call_args_list
            if c.kwargs.get("status") == "failed"
        ]
        assert failed_calls, "Expected update_job(status='failed') to be called"
        assert "digitize down" in failed_calls[0].kwargs.get("error", "")


# ---------------------------------------------------------------------------
# GET /v1/invoices/{job_id}/file
# ---------------------------------------------------------------------------

class TestGetInvoiceFile:
    def test_file_download_success(self, client, tmp_path):
        from settings import InvoiceProcessingConfig

        job_dir = tmp_path / "staging" / "job-file-1"
        job_dir.mkdir(parents=True, exist_ok=True)
        sample_file = job_dir / "invoice.pdf"
        sample_file.write_bytes(b"%PDF-1.4 test invoice file")

        job = _fake_job(job_id="job-file-1", filename="invoice.pdf", status="review")

        with patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch.object(InvoiceProcessingConfig, "staging_dir", new_callable=PropertyMock, return_value=tmp_path / "staging"):
            mock_mgr.get_job.return_value = job

            resp = client.get("/v1/invoices/job-file-1/file")

        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4 test invoice file"

    def test_file_download_job_not_found(self, client):
        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = None

            resp = client.get("/v1/invoices/missing-job/file")

        assert resp.status_code == 404

    def test_file_download_file_missing_on_disk(self, client, tmp_path):
        from settings import InvoiceProcessingConfig

        job = _fake_job(job_id="job-file-2", filename="missing.pdf", status="review")

        with patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch.object(InvoiceProcessingConfig, "staging_dir", new_callable=PropertyMock, return_value=tmp_path / "staging"):
            mock_mgr.get_job.return_value = job

            resp = client.get("/v1/invoices/job-file-2/file")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /v1/invoices/{job_id}
# ---------------------------------------------------------------------------

class TestUpdateInvoiceJob:
    def test_update_during_review_updates_and_returns_200(self, client):
        job = _fake_job(job_id="job-patch-1", status="review")
        updated_job = _fake_job(job_id="job-patch-1", status="review")
        updated_job.staged_header = {"INVOICE_NUM": "INV-CORRECTED"}

        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = job
            mock_mgr.update_job.return_value = updated_job

            resp = client.patch(
                "/v1/invoices/job-patch-1",
                json={"staged_header": {"INVOICE_NUM": "INV-CORRECTED"}},
            )

        assert resp.status_code == 200
        assert resp.json()["staged_header"] == {"INVOICE_NUM": "INV-CORRECTED"}
        mock_mgr.update_job.assert_called_once_with(
            job_id="job-patch-1",
            staged_header={"INVOICE_NUM": "INV-CORRECTED"},
        )

    def test_update_when_job_not_found_returns_404(self, client):
        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = None

            resp = client.patch(
                "/v1/invoices/missing-job",
                json={"staged_header": {"INVOICE_NUM": "INV-001"}},
            )

        assert resp.status_code == 404

    @pytest.mark.parametrize("invalid_status", ["accepted", "extracting", "approved", "rejected", "failed"])
    def test_update_when_status_not_review_returns_409(self, client, invalid_status):
        job = _fake_job(job_id="job-not-review", status=invalid_status)

        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = job

            resp = client.patch(
                "/v1/invoices/job-not-review",
                json={"staged_header": {"INVOICE_NUM": "INV-001"}},
            )

        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /v1/invoices/{job_id}/approve
# ---------------------------------------------------------------------------

class TestApproveInvoiceJob:
    def test_approve_review_job_sets_approved_and_returns_200(self, client):
        job = _fake_job(job_id="job-app-1", status="review")
        approved_job = _fake_job(job_id="job-app-1", status="approved")

        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = job
            mock_mgr.update_job.return_value = approved_job

            resp = client.post("/v1/invoices/job-app-1/approve")

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        mock_mgr.update_job.assert_called_once_with(job_id="job-app-1", status="approved")

    def test_approve_unknown_job_returns_404(self, client):
        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = None

            resp = client.post("/v1/invoices/missing-job/approve")

        assert resp.status_code == 404

    @pytest.mark.parametrize("invalid_status", ["accepted", "extracting", "approved", "rejected", "failed"])
    def test_approve_non_review_job_returns_409(self, client, invalid_status):
        job = _fake_job(job_id="job-invalid", status=invalid_status)

        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = job

            resp = client.post("/v1/invoices/job-invalid/approve")

        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /v1/invoices/{job_id}/reject
# ---------------------------------------------------------------------------

class TestRejectInvoiceJob:
    def test_reject_review_job_sets_rejected_and_returns_200(self, client):
        job = _fake_job(job_id="job-rej-1", status="review")
        rejected_job = _fake_job(job_id="job-rej-1", status="rejected")

        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = job
            mock_mgr.update_job.return_value = rejected_job

            resp = client.post("/v1/invoices/job-rej-1/reject")

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        mock_mgr.update_job.assert_called_once_with(job_id="job-rej-1", status="rejected")

    def test_reject_unknown_job_returns_404(self, client):
        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = None

            resp = client.post("/v1/invoices/missing-job/reject")

        assert resp.status_code == 404

    @pytest.mark.parametrize("invalid_status", ["accepted", "extracting", "approved", "rejected", "failed"])
    def test_reject_non_review_job_returns_409(self, client, invalid_status):
        job = _fake_job(job_id="job-invalid", status=invalid_status)

        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = job

            resp = client.post("/v1/invoices/job-invalid/reject")

        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /v1/invoices/{job_id}
# ---------------------------------------------------------------------------

class TestDeleteInvoiceJob:
    @pytest.mark.parametrize("deletable_status", ["review", "approved", "rejected", "failed"])
    def test_delete_deletable_status_returns_204_and_cleans_dir(self, client, tmp_path, deletable_status):
        from settings import InvoiceProcessingConfig

        job_dir = tmp_path / "staging" / "job-del-1"
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "invoice.pdf").write_bytes(b"%PDF dummy")

        job = _fake_job(job_id="job-del-1", status=deletable_status)

        with patch("api.v1.invoices.db_manager") as mock_mgr, \
             patch.object(InvoiceProcessingConfig, "staging_dir", new_callable=PropertyMock, return_value=tmp_path / "staging"):
            mock_mgr.get_job.return_value = job
            mock_mgr.delete_job.return_value = True

            resp = client.delete("/v1/invoices/job-del-1")

        assert resp.status_code == 204
        mock_mgr.delete_job.assert_called_once_with("job-del-1")
        assert not job_dir.exists()

    @pytest.mark.parametrize("in_flight_status", ["accepted", "extracting"])
    def test_delete_in_flight_status_returns_409(self, client, in_flight_status):
        job = _fake_job(job_id="job-inflight", status=in_flight_status)

        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = job

            resp = client.delete("/v1/invoices/job-inflight")

        assert resp.status_code == 409
        mock_mgr.delete_job.assert_not_called()

    def test_delete_unknown_job_returns_404(self, client):
        with patch("api.v1.invoices.db_manager") as mock_mgr:
            mock_mgr.get_job.return_value = None

            resp = client.delete("/v1/invoices/missing-job")

        assert resp.status_code == 404
