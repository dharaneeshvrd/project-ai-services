"""
Unit tests for pipeline/pdf_path.py — run_pdf_path().
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.pdf_path import run_pdf_path


@pytest.fixture
def staged_pdf(tmp_path):
    p = tmp_path / "invoice.pdf"
    p.write_bytes(b"%PDF-1.4 test invoice content")
    return p


@pytest.mark.asyncio
async def test_pdf_path_happy_path(staged_pdf):
    """Full PDF path orchestration updates DB states and calls downstream clients in order."""
    mock_digitize = MagicMock()
    mock_digitize.submit_document = AsyncMock(return_value="dg-123")
    mock_digitize.poll_job = AsyncMock(return_value="# Invoice Content\nAmount: $100")

    mock_extract = MagicMock()
    mock_extract.submit_extraction = AsyncMock(return_value="ext-456")
    mock_extract.poll_job = AsyncMock(
        return_value={"AP_INVOICES_ALL": {"INVOICE_NUM": "INV-001"}, "AP_INVOICE_LINES_ALL": []}
    )

    with patch("pipeline.pdf_path.db_manager") as mock_db:
        result = await run_pdf_path(
            job_id="job-pdf-1",
            staged_path=staged_pdf,
            digitize_client=mock_digitize,
            extract_client=mock_extract,
        )

    # Check digitize calls
    mock_digitize.submit_document.assert_awaited_once_with(job_id="job-pdf-1", file_path=staged_pdf)
    mock_digitize.poll_job.assert_awaited_once_with("dg-123")

    # Check extract calls
    mock_extract.submit_extraction.assert_awaited_once_with(
        job_id="job-pdf-1",
        content="# Invoice Content\nAmount: $100",
        schema_name="invoice-extraction-v1",
    )
    mock_extract.poll_job.assert_awaited_once_with("ext-456")

    # Check DB updates — status is owned by the caller; only downstream job ids are recorded
    assert mock_db.update_job.call_count == 2
    mock_db.update_job.assert_any_call(job_id="job-pdf-1", digitize_job_id="dg-123")
    mock_db.update_job.assert_any_call(job_id="job-pdf-1", extract_job_id="ext-456")

    assert result == {"AP_INVOICES_ALL": {"INVOICE_NUM": "INV-001"}, "AP_INVOICE_LINES_ALL": []}


@pytest.mark.asyncio
async def test_pdf_path_digitize_timeout(staged_pdf):
    """Digitize polling timeout raises TimeoutError and stops pipeline."""
    mock_digitize = MagicMock()
    mock_digitize.submit_document = AsyncMock(return_value="dg-123")
    mock_digitize.poll_job = AsyncMock(side_effect=TimeoutError("Digitize timed out"))

    mock_extract = MagicMock()
    mock_extract.submit_extraction = AsyncMock()

    with patch("pipeline.pdf_path.db_manager"):
        with pytest.raises(TimeoutError, match="Digitize timed out"):
            await run_pdf_path(
                job_id="job-pdf-to",
                staged_path=staged_pdf,
                digitize_client=mock_digitize,
                extract_client=mock_extract,
            )

    mock_extract.submit_extraction.assert_not_awaited()


@pytest.mark.asyncio
async def test_pdf_path_digitize_failure(staged_pdf):
    """Digitize job failure raises RuntimeError and stops pipeline."""
    mock_digitize = MagicMock()
    mock_digitize.submit_document = AsyncMock(return_value="dg-123")
    mock_digitize.poll_job = AsyncMock(side_effect=RuntimeError("Digitize job failed: OCR corrupted"))

    mock_extract = MagicMock()
    mock_extract.submit_extraction = AsyncMock()

    with patch("pipeline.pdf_path.db_manager"):
        with pytest.raises(RuntimeError, match="Digitize job failed"):
            await run_pdf_path(
                job_id="job-pdf-fail",
                staged_path=staged_pdf,
                digitize_client=mock_digitize,
                extract_client=mock_extract,
            )

    mock_extract.submit_extraction.assert_not_awaited()


@pytest.mark.asyncio
async def test_pdf_path_extract_timeout(staged_pdf):
    """Extract polling timeout raises TimeoutError."""
    mock_digitize = MagicMock()
    mock_digitize.submit_document = AsyncMock(return_value="dg-123")
    mock_digitize.poll_job = AsyncMock(return_value="# Markdown")

    mock_extract = MagicMock()
    mock_extract.submit_extraction = AsyncMock(return_value="ext-456")
    mock_extract.poll_job = AsyncMock(side_effect=TimeoutError("Extract timed out"))

    with patch("pipeline.pdf_path.db_manager"):
        with pytest.raises(TimeoutError, match="Extract timed out"):
            await run_pdf_path(
                job_id="job-ext-to",
                staged_path=staged_pdf,
                digitize_client=mock_digitize,
                extract_client=mock_extract,
            )


@pytest.mark.asyncio
async def test_pdf_path_extract_failure(staged_pdf):
    """Extract job failure raises RuntimeError."""
    mock_digitize = MagicMock()
    mock_digitize.submit_document = AsyncMock(return_value="dg-123")
    mock_digitize.poll_job = AsyncMock(return_value="# Markdown")

    mock_extract = MagicMock()
    mock_extract.submit_extraction = AsyncMock(return_value="ext-456")
    mock_extract.poll_job = AsyncMock(side_effect=RuntimeError("Extract job failed"))

    with patch("pipeline.pdf_path.db_manager"):
        with pytest.raises(RuntimeError, match="Extract job failed"):
            await run_pdf_path(
                job_id="job-ext-fail",
                staged_path=staged_pdf,
                digitize_client=mock_digitize,
                extract_client=mock_extract,
            )


@pytest.mark.asyncio
async def test_pdf_path_does_not_inspect_content(tmp_path):
    """PDF path must not perform type detection on content — forwards as-is."""
    staged = tmp_path / "unknown.pdf"
    staged.write_bytes(b"not a valid pdf header")

    mock_digitize = MagicMock()
    mock_digitize.submit_document = AsyncMock(return_value="dg-999")
    mock_digitize.poll_job = AsyncMock(return_value="text")

    mock_extract = MagicMock()
    mock_extract.submit_extraction = AsyncMock(return_value="ext-999")
    mock_extract.poll_job = AsyncMock(return_value={})

    with patch("pipeline.pdf_path.db_manager"):
        result = await run_pdf_path(
            job_id="job-asis-1",
            staged_path=staged,
            digitize_client=mock_digitize,
            extract_client=mock_extract,
        )

    mock_digitize.submit_document.assert_awaited_once_with(
        job_id="job-asis-1",
        file_path=staged,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_pdf_path_default_clients(staged_pdf):
    """Default DigitizeClient and ExtractClient instances are created when None passed."""
    with patch("pipeline.pdf_path.DigitizeClient") as mock_dig_cls, \
         patch("pipeline.pdf_path.ExtractClient") as mock_ext_cls, \
         patch("pipeline.pdf_path.db_manager"):

        mock_dig_inst = MagicMock()
        mock_dig_inst.submit_document = AsyncMock(return_value="dg-def")
        mock_dig_inst.poll_job = AsyncMock(return_value="md")
        mock_dig_cls.return_value = mock_dig_inst

        mock_ext_inst = MagicMock()
        mock_ext_inst.submit_extraction = AsyncMock(return_value="ext-def")
        mock_ext_inst.poll_job = AsyncMock(return_value={"status": "ok"})
        mock_ext_cls.return_value = mock_ext_inst

        result = await run_pdf_path(job_id="job-def-1", staged_path=staged_pdf)

        mock_dig_cls.assert_called_once()
        mock_ext_cls.assert_called_once()
        assert result == {"status": "ok"}
