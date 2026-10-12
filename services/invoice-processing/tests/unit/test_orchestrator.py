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
    assembled = {"staged_header": {}, "staged_lines": []}

    with patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock, return_value=raw) as mock_pdf, \
         patch("pipeline.orchestrator.run_oneshot_path", new_callable=AsyncMock) as mock_oneshot, \
         patch("pipeline.orchestrator.assemble_invoice", return_value=assembled) as mock_assemble:

        result = await _run_pipeline(job_id="job-1", staged_path=staged_path, input_type="pdf")

        mock_pdf.assert_awaited_once_with(job_id="job-1", staged_path=staged_path)
        mock_oneshot.assert_not_awaited()
        mock_assemble.assert_called_once_with(job_id="job-1", raw=raw)
        assert result == assembled


@pytest.mark.asyncio
async def test_run_pipeline_image_path(tmp_path):
    """Image input_type delegates to run_oneshot_path and then assembles."""
    staged_path = tmp_path / "receipt.png"
    staged_path.write_bytes(b"\x89PNG\r\n")
    raw = {"header": {}, "lines": []}
    assembled = {"staged_header": {}, "staged_lines": []}

    with patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
         patch("pipeline.orchestrator.run_oneshot_path", new_callable=AsyncMock, return_value=raw) as mock_oneshot, \
         patch("pipeline.orchestrator.assemble_invoice", return_value=assembled) as mock_assemble:

        result = await _run_pipeline(job_id="job-2", staged_path=staged_path, input_type="image")

        mock_oneshot.assert_awaited_once_with(job_id="job-2", staged_path=staged_path)
        mock_pdf.assert_not_awaited()
        mock_assemble.assert_called_once_with(job_id="job-2", raw=raw)
        assert result == assembled


@pytest.mark.asyncio
async def test_run_pipeline_unknown_input_type_raises(staged_path):
    """Unknown input_type raises ValueError without calling any path handler."""
    with patch("pipeline.orchestrator.run_pdf_path", new_callable=AsyncMock) as mock_pdf, \
         patch("pipeline.orchestrator.run_oneshot_path", new_callable=AsyncMock) as mock_oneshot, \
         patch("pipeline.orchestrator.assemble_invoice") as mock_assemble:

        with pytest.raises(ValueError, match="Unknown input_type 'video' for job job-3"):
            await _run_pipeline(job_id="job-3", staged_path=staged_path, input_type="video")

        mock_pdf.assert_not_awaited()
        mock_oneshot.assert_not_awaited()
        mock_assemble.assert_not_called()
