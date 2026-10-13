"""
Client for Extract service.
"""

import asyncio
import mimetypes
from pathlib import Path
import time
from typing import Any

import httpx

from common.misc_utils import get_logger
from common.retry_utils import async_retry_on_transient_error
from schema import INVOICE_SCHEMA_PAYLOAD
from settings import settings

logger = get_logger("clients.extract_client")


class ExtractClient:
    """Client for interacting with the Extract service via async HTTP."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.invoice.extract_url).rstrip("/")

    async def register_schema(self, schema_payload: dict | None = None) -> dict:
        """Register extraction schema with extract service."""
        payload = schema_payload or INVOICE_SCHEMA_PAYLOAD
        url = f"{self.base_url}/v1/schemas"
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (200, 201):
                return resp.json()
            if resp.status_code == 409:
                return {"status": "already_exists"}
            resp.raise_for_status()
            return resp.json()

    async def submit_extraction(
        self,
        content: str | bytes | None = None,
        schema_name: str | None = None,
        file_path: Path | str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Submit text or file for extraction. Returns extract job_id as string."""
        schema = schema_name or kwargs.get("schema") or settings.invoice.extract_schema_name
        fp = file_path or kwargs.get("staged_path")
        if fp is not None:
            if isinstance(fp, str):
                fp = Path(fp)
            return await self.submit_file(fp, schema_name=schema)

        cnt = content if content is not None else kwargs.get("text")
        if cnt is not None:
            if isinstance(cnt, bytes):
                cnt = cnt.decode("utf-8")
            return await self.submit_file(cnt, schema_name=schema)

        raise ValueError("Either content or file_path must be provided to submit_extraction")

    async def submit_file(
        self,
        source: Path | str,
        schema_name: str | None = None,
    ) -> str:
        """POST a file or Markdown string to extract /v1/extract/jobs.

        *source* can be a :class:`~pathlib.Path` (any file type) or a plain
        ``str`` (treated as Markdown content posted as ``invoice.md``).

        Returns extract job_id.
        """
        schema = schema_name or settings.invoice.extract_schema_name
        url = f"{self.base_url}/v1/extract/jobs"

        if isinstance(source, Path):
            mime = mimetypes.guess_type(str(source))[0] or "application/octet-stream"
            with open(source, "rb") as fh:
                file_bytes = fh.read()
            files = {"files": (source.name, file_bytes, mime)}
        else:
            files = {"files": ("invoice.md", source.encode("utf-8"), "text/markdown")}

        data = {"schema_name": schema}

        @async_retry_on_transient_error()
        async def _post() -> str:
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                resp = await client.post(url, files=files, data=data)
                resp.raise_for_status()
                return resp.json()["job_id"]

        return await _post()

    async def poll_job(
        self,
        job_id: str,
        timeout: float | None = None,
        poll_interval: float | None = None,
    ) -> dict:
        """Poll GET /v1/extract/jobs/{job_id} until status=completed.

        Returns the result dict (AP_INVOICES_ALL + AP_INVOICE_LINES_ALL).
        Raises TimeoutError or RuntimeError on failure.
        """
        timeout_val = timeout if timeout is not None else settings.invoice.job_timeout_seconds
        interval_val = (
            poll_interval if poll_interval is not None else settings.invoice.job_poll_interval_seconds
        )
        deadline = time.monotonic() + timeout_val

        while time.monotonic() < deadline:
            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                resp = await client.get(f"{self.base_url}/v1/extract/jobs/{job_id}")
                resp.raise_for_status()
                data = resp.json()

            status = data.get("status")
            if status == "completed":
                doc_id = None
                if data.get("documents") and len(data["documents"]) > 0:
                    doc = data["documents"][0]
                    doc_id = doc.get("doc_id") or doc.get("id")
                return await self._get_result(job_id, doc_id)

            if status in ("failed", "completed_with_errors"):
                error_msg = data.get("error") or ""
                raise RuntimeError(
                    f"Extract job {job_id} ended with status={status!r}: {error_msg}".strip()
                )

            await asyncio.sleep(interval_val)

        raise TimeoutError(
            f"Extract job {job_id} did not complete within {timeout_val}s"
        )

    async def _get_result(self, job_id: str, doc_id: str | None = None) -> dict:
        urls = []
        if doc_id:
            urls.append(f"{self.base_url}/v1/extract/jobs/{job_id}/results/{doc_id}")
        urls.append(f"{self.base_url}/v1/extract/jobs/{job_id}/result")

        last_resp = None
        async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
            for url in urls:
                resp = await client.get(url)
                if resp.status_code == 200:
                    body = resp.json()
                    if isinstance(body, dict) and "data" in body:
                        data_val = body["data"]
                        if isinstance(data_val, dict) and "extraction" in data_val:
                            return data_val["extraction"]
                        return data_val
                    return body
                last_resp = resp

        if last_resp is not None:
            last_resp.raise_for_status()
        raise RuntimeError(f"Failed to fetch extraction result for job {job_id}")
