"""
Unit tests for pipeline/oneshot_path.py — run_oneshot_path().
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pipeline.oneshot_path import run_oneshot_path


def test_oneshot_path_calls_extract_submit(tmp_path):
    """run_oneshot_path must call extract_client.submit_extraction with job_id and file_path."""
    staged = tmp_path / "scan.png"
    staged.write_bytes(b"\x89PNG")

    mock_client = MagicMock()
    mock_client.submit_extraction = AsyncMock(return_value={"extract_job_id": "ex-1"})

    result = asyncio.run(
        run_oneshot_path(job_id="job-os-1", staged_path=staged, extract_client=mock_client)
    )

    mock_client.submit_extraction.assert_called_once_with(
        job_id="job-os-1",
        file_path=staged,
    )
    assert result == {"extract_job_id": "ex-1"}


def test_oneshot_path_logs_at_info(tmp_path, caplog):
    """run_oneshot_path must log the forwarding decision at INFO level."""
    import logging

    staged = tmp_path / "receipt.jpg"
    staged.write_bytes(b"\xFF\xD8\xFF")

    mock_client = MagicMock()
    mock_client.submit_extraction = AsyncMock(return_value={})

    with caplog.at_level(logging.INFO, logger="pipeline.oneshot_path"):
        asyncio.run(
            run_oneshot_path(job_id="job-log-1", staged_path=staged, extract_client=mock_client)
        )

    assert "One-Shot path" in caplog.text
    assert "extract VLM" in caplog.text


@pytest.mark.parametrize("image_name", ["scan.png", "photo.jpg", "doc.tiff"])
def test_oneshot_path_returns_extract_result(tmp_path, image_name):
    """Return value must be exactly what extract_client.submit_extraction returns."""
    staged = tmp_path / image_name
    staged.write_bytes(b"data")

    expected = {"status": "pending", "extract_job_id": "ex-99"}
    mock_client = MagicMock()
    mock_client.submit_extraction = AsyncMock(return_value=expected)

    result = asyncio.run(
        run_oneshot_path(job_id="job-param-1", staged_path=staged, extract_client=mock_client)
    )
    assert result == expected
