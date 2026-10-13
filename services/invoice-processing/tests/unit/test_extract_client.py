"""
Unit tests for clients/extract_client.py — ExtractClient using httpx.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from clients.extract_client import ExtractClient


@pytest.fixture
def staged_img(tmp_path):
    p = tmp_path / "invoice.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p


@pytest.fixture
def client():
    return ExtractClient(base_url="http://extract-service:6000")


@pytest.mark.asyncio
async def test_register_schema(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"schema_id": "schema-123"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        res = await client.register_schema({"name": "test-schema"})

    assert res == {"schema_id": "schema-123"}
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_schema_already_exists(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 409

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        res = await client.register_schema({"name": "test-schema"})

    assert res == {"status": "already_exists"}


@pytest.mark.asyncio
async def test_submit_text(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"job_id": "ext-job-001"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        job_id = await client.submit_text("# Invoice Markdown", schema_name="invoice-extraction-v1")

    assert job_id == "ext-job-001"
    mock_client.post.assert_awaited_once()
    call_args, call_kwargs = mock_client.post.call_args
    assert call_args[0] == "http://extract-service:6000/v1/extract/jobs"
    assert call_kwargs["data"] == {"schema_name": "invoice-extraction-v1"}


@pytest.mark.asyncio
async def test_submit_image(client, staged_img):
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"job_id": "ext-job-img-001"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        job_id = await client.submit_image(staged_img, schema_name="invoice-extraction-v1")

    assert job_id == "ext-job-img-001"
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_extraction_routing(client, staged_img):
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"job_id": "ext-routed-001"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        # text content
        res1 = await client.submit_extraction(content="# Markdown text")
        assert res1 == "ext-routed-001"

        # image file_path
        res2 = await client.submit_extraction(file_path=staged_img)
        assert res2 == "ext-routed-001"

    with pytest.raises(ValueError, match="Either content or file_path"):
        await client.submit_extraction()


@pytest.mark.asyncio
async def test_poll_job_completed(client):
    job_resp = MagicMock()
    job_resp.status_code = 200
    job_resp.json.return_value = {
        "status": "completed",
        "documents": [{"doc_id": "doc-001", "filename": "invoice.md", "status": "completed"}],
    }

    result_resp = MagicMock()
    result_resp.status_code = 200
    result_resp.json.return_value = {
        "data": {
            "extraction": {
                "AP_INVOICES_ALL": {"INVOICE_NUM": "INV-100"},
                "AP_INVOICE_LINES_ALL": [{"LINE_NUMBER": 1, "AMOUNT": 500.0}],
            }
        }
    }

    def fake_get(url, **kwargs):
        if url == "http://extract-service:6000/v1/extract/jobs/ext-job-001":
            return job_resp
        if "results/doc-001" in url or "result" in url:
            return result_resp
        raise ValueError(f"Unexpected url {url}")

    mock_client = AsyncMock()
    mock_client.get.side_effect = fake_get
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await client.poll_job("ext-job-001", timeout=5.0, poll_interval=0.01)

    assert result == {
        "AP_INVOICES_ALL": {"INVOICE_NUM": "INV-100"},
        "AP_INVOICE_LINES_ALL": [{"LINE_NUMBER": 1, "AMOUNT": 500.0}],
    }


@pytest.mark.asyncio
async def test_poll_job_failed_status_raises(client):
    job_resp = MagicMock()
    job_resp.status_code = 200
    job_resp.json.return_value = {
        "status": "failed",
        "error": "LLM context exceeded",
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = job_resp
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError, match="status='failed': LLM context exceeded"):
            await client.poll_job("ext-job-fail", timeout=5.0, poll_interval=0.01)


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
            await client.poll_job("ext-job-timeout", timeout=0.05, poll_interval=0.01)
