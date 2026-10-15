"""
Unit tests for pipeline.orchestrator._run_pipeline.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.orchestrator import _run_pipeline


@pytest.fixture
def staged_path(tmp_path):
    p = tmp_path / "invoice.pdf"
    p.write_bytes(b"%PDF-1.4 test")
    return p


@pytest.mark.asyncio
async def test_run_pipeline_pdf_path(staged_path):
    """PDF input_type delegates to run_pdf_path and then assembles."""
    raw = {"header": {}, "lines": []}
    header = {"invoice_num": "123"}
    lines = [{"amount": 10.0}]

    with patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock, return_value=raw) as mock_pdf, \
         patch("pipeline.orchestrator.run_oneshot_path", new_callable=AsyncMock) as mock_oneshot, \
         patch("pipeline.orchestrator.assemble_invoice", return_value=(header, lines)) as mock_assemble, \
         patch("pipeline.orchestrator.db_manager") as mock_db, \
         patch("pipeline.orchestrator.DigitizeClient") as mock_digitize:

        await _run_pipeline(job_id="job-1", staged_path=staged_path, input_type="pdf")

        # Verify DigitizerClient instantiated and run_pdf_path called
        mock_digitize.assert_called_once()
        mock_pdf.assert_awaited_once_with(
            job_id="job-1",
            staged_path=staged_path,
            digitize_client=mock_digitize.return_value
        )
        mock_oneshot.assert_not_awaited()
        mock_assemble.assert_called_once_with(raw=raw, job_id="job-1", filename=staged_path.name)
        
        # Verify DB updates: accepted -> extracting -> review
        mock_db.update_job.assert_any_call(job_id="job-1", status="extracting")
        mock_db.update_job.assert_any_call(
            job_id="job-1",
            status="review",
            staged_header=header,
            staged_lines=lines
        )


@pytest.mark.asyncio
async def test_run_pipeline_image_path(tmp_path):
    """Image input_type delegates to run_oneshot_path and then assembles."""
    staged_path = tmp_path / "receipt.png"
    staged_path.write_bytes(b"\x89PNG\r\n")
    raw = {"header": {}, "lines": []}
    header = {"invoice_num": "456"}
    lines = [{"amount": 20.0}]

    with patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
         patch("pipeline.orchestrator.run_oneshot_path", new_callable=AsyncMock, return_value=raw) as mock_oneshot, \
         patch("pipeline.orchestrator.assemble_invoice", return_value=(header, lines)) as mock_assemble, \
         patch("pipeline.orchestrator.db_manager") as mock_db, \
         patch("pipeline.orchestrator.ExtractClient") as mock_extract:

        await _run_pipeline(job_id="job-2", staged_path=staged_path, input_type="image")

        mock_extract.assert_called_once()
        mock_oneshot.assert_awaited_once_with(
            job_id="job-2",
            staged_path=staged_path,
            extract_client=mock_extract.return_value
        )
        mock_pdf.assert_not_awaited()
        mock_assemble.assert_called_once_with(raw=raw, job_id="job-2", filename=staged_path.name)
        
        mock_db.update_job.assert_any_call(job_id="job-2", status="extracting")
        mock_db.update_job.assert_any_call(
            job_id="job-2",
            status="review",
            staged_header=header,
            staged_lines=lines
        )


@pytest.mark.asyncio
async def test_run_pipeline_unknown_input_type_raises(staged_path):
    """Unknown input_type records failed status in db without calling any path handler."""
    with patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
         patch("pipeline.orchestrator.run_oneshot_path", new_callable=AsyncMock) as mock_oneshot, \
         patch("pipeline.orchestrator.assemble_invoice") as mock_assemble, \
         patch("pipeline.orchestrator.db_manager") as mock_db:

        await _run_pipeline(job_id="job-3", staged_path=staged_path, input_type="video")

        mock_pdf.assert_not_awaited()
        mock_oneshot.assert_not_awaited()
        mock_assemble.assert_not_called()
        
        # Should set to extracting, then fail because of Unknown input_type
        mock_db.update_job.assert_any_call(job_id="job-3", status="extracting")
        # Find the call with status="failed"
        failed_calls = [
            c for c in mock_db.update_job.call_args_list
            if c.kwargs.get("status") == "failed"
        ]
        assert failed_calls, "Expected update_job to be called with status='failed'"
        assert "Unknown input_type 'video'" in failed_calls[0].kwargs.get("error", "")
