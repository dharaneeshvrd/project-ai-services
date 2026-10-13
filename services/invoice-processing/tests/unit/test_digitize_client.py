"""
Unit tests for clients/digitize_client.py — DigitizeClient using httpx.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from clients.digitize_client import DigitizeClient


@pytest.fixture
def staged_pdf(tmp_path):
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.4 test bytes")
    return p


@pytest.fixture
def client():
    return DigitizeClient(base_url="http://digitize-service:4000")


@pytest.mark.asyncio
async def test_submit_document_success(client, staged_pdf):
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"job_id": "dg-job-001"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        job_id = await client.submit_document(file_path=staged_pdf)

    assert job_id == "dg-job-001"
    mock_client.post.assert_awaited_once()
    call_args, call_kwargs = mock_client.post.call_args
    assert call_args[0] == "http://digitize-service:4000/v1/jobs"
    assert call_kwargs["params"] == {"operation": "digitization", "output_format": "md"}


def _status_error_resp(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{status_code} Error", request=MagicMock(), response=resp
    )
    return resp


@pytest.mark.asyncio
async def test_submit_document_http_error_raises(client, staged_pdf):
    mock_resp = _status_error_resp(400)

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await client.submit_document(file_path=staged_pdf)

    # Non-transient error: no retry
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_document_retries_on_queue_full(client, staged_pdf):
    ok_resp = MagicMock()
    ok_resp.status_code = 202
    ok_resp.json.return_value = {"job_id": "dg-job-retry"}

    mock_client = AsyncMock()
    mock_client.post.side_effect = [_status_error_resp(429), _status_error_resp(503), ok_resp]
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("common.retry_utils.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        job_id = await client.submit_document(file_path=staged_pdf)

    assert job_id == "dg-job-retry"
    assert mock_client.post.await_count == 3
    assert mock_sleep.await_count == 2


@pytest.mark.asyncio
async def test_submit_document_queue_full_exhausts_retries(client, staged_pdf):
    mock_client = AsyncMock()
    mock_client.post.return_value = _status_error_resp(429)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("common.retry_utils.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(httpx.HTTPStatusError):
            await client.submit_document(file_path=staged_pdf)

    assert mock_client.post.await_count == 3


@pytest.mark.asyncio
async def test_submit_document_no_file_raises(client):
    with pytest.raises(ValueError, match="file_path must be provided"):
        await client.submit_document()


@pytest.mark.asyncio
async def test_poll_job_completed(client):
    job_resp = MagicMock()
    job_resp.status_code = 200
    job_resp.json.return_value = {
        "status": "completed",
        "documents": [{"id": "doc-999", "name": "sample.pdf", "status": "completed"}],
    }

    content_resp = MagicMock()
    content_resp.status_code = 200
    content_resp.json.return_value = {"result": "# Markdown Content", "output_format": "md"}

    def fake_get(url, **kwargs):
        if "/v1/jobs/" in url:
            return job_resp
        if "/v1/documents/" in url:
            return content_resp
        raise ValueError(f"Unexpected url {url}")

    mock_client = AsyncMock()
    mock_client.get.side_effect = fake_get
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await client.poll_job("dg-job-001", timeout=5.0, poll_interval=0.01)

    assert result == "# Markdown Content"


@pytest.mark.asyncio
async def test_poll_job_completed_plain_text(client):
    job_resp = MagicMock()
    job_resp.status_code = 200
    job_resp.json.return_value = {
        "status": "completed",
        "documents": [{"doc_id": "doc-999", "name": "sample.pdf"}],
    }

    content_resp = MagicMock()
    content_resp.status_code = 200
    content_resp.json.side_effect = ValueError("Not JSON")
    content_resp.text = "# Plain Markdown Content"

    def fake_get(url, **kwargs):
        if "/v1/jobs/" in url:
            return job_resp
        if "/v1/documents/" in url:
            return content_resp
        raise ValueError(f"Unexpected url {url}")

    mock_client = AsyncMock()
    mock_client.get.side_effect = fake_get
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await client.poll_job("dg-job-001", timeout=5.0, poll_interval=0.01)

    assert result == "# Plain Markdown Content"


@pytest.mark.asyncio
async def test_poll_job_failed_status_raises(client):
    job_resp = MagicMock()
    job_resp.status_code = 200
    job_resp.json.return_value = {
        "status": "failed",
        "error": "OCR engine crashed",
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = job_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="status='failed': OCR engine crashed"):
            await client.poll_job("dg-job-fail", timeout=5.0, poll_interval=0.01)


@pytest.mark.asyncio
async def test_poll_job_timeout_raises(client):
    job_resp = MagicMock()
    job_resp.status_code = 200
    job_resp.json.return_value = {
        "status": "in_progress",
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = job_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(TimeoutError, match="did not complete within"):
            await client.poll_job("dg-job-timeout", timeout=0.05, poll_interval=0.01)
