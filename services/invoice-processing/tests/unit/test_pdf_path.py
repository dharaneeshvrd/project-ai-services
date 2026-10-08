"""
Unit tests for pipeline/pdf_path.py — run_pdf_path().
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pipeline.pdf_path import run_pdf_path


def test_pdf_path_calls_digitize_submit(tmp_path):
    """run_pdf_path must call digitize_client.submit_document with job_id and file_path."""
    staged = tmp_path / "invoice.pdf"
    staged.write_bytes(b"%PDF-1.4")

    mock_client = MagicMock()
    mock_client.submit_document = AsyncMock(return_value={"digitize_job_id": "dg-1"})

    result = asyncio.run(
        run_pdf_path(job_id="job-pdf-1", staged_path=staged, digitize_client=mock_client)
    )

    mock_client.submit_document.assert_called_once_with(
        job_id="job-pdf-1",
        file_path=staged,
    )
    assert result == {"digitize_job_id": "dg-1"}


def test_pdf_path_does_not_inspect_content(tmp_path):
    """PDF path must not attempt any type detection — content is forwarded as-is."""
    # Use deliberately minimal/empty bytes to prove no parsing occurs
    staged = tmp_path / "unknown.pdf"
    staged.write_bytes(b"not a real pdf")

    mock_client = MagicMock()
    mock_client.submit_document = AsyncMock(return_value={})

    # Should complete without raising even with invalid PDF bytes
    asyncio.run(
        run_pdf_path(job_id="job-asis-1", staged_path=staged, digitize_client=mock_client)
    )

    mock_client.submit_document.assert_called_once_with(
        job_id="job-asis-1",
        file_path=staged,
    )


def test_pdf_path_logs_at_info(tmp_path, caplog):
    """run_pdf_path must log the forwarding decision at INFO level."""
    import logging

    staged = tmp_path / "bill.pdf"
    staged.write_bytes(b"%PDF-1.7")

    mock_client = MagicMock()
    mock_client.submit_document = AsyncMock(return_value={})

    with caplog.at_level(logging.INFO, logger="pipeline.pdf_path"):
        asyncio.run(
            run_pdf_path(job_id="job-log-1", staged_path=staged, digitize_client=mock_client)
        )

    assert "PDF path" in caplog.text
    assert "digitize" in caplog.text


def test_pdf_path_returns_digitize_result(tmp_path):
    """Return value must be exactly what digitize_client.submit_document returns."""
    staged = tmp_path / "invoice.pdf"
    staged.write_bytes(b"%PDF-1.5")

    expected = {"digitize_job_id": "dg-42", "status": "queued"}
    mock_client = MagicMock()
    mock_client.submit_document = AsyncMock(return_value=expected)

    result = asyncio.run(
        run_pdf_path(job_id="job-ret-1", staged_path=staged, digitize_client=mock_client)
    )
    assert result == expected
