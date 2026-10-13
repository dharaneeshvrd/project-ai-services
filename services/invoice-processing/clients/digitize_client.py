"""
Client for Digitize service.
"""

import asyncio
from pathlib import Path
import time
from typing import Any

import httpx

from common.misc_utils import get_logger
from common.retry_utils import async_retry_on_transient_error
from settings import settings

logger = get_logger("clients.digitize_client")


class DigitizeClient:
    """Client for interacting with the Digitize service via async HTTP."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.invoice.digitize_url).rstrip("/")

    async def submit_document(
        self,
        file_path: Path | str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """POST PDF to digitize /v1/jobs (operation=digitization, output_format=md).

        Returns digitize job_id as string.
        """
        fp = file_path or kwargs.get("staged_path")
        if isinstance(fp, str):
            fp = Path(fp)
        if fp is None and job_id and isinstance(job_id, Path):
            fp = job_id
            job_id = None
        if fp is None:
            raise ValueError("file_path must be provided to submit_document")

        url = f"{self.base_url}/v1/jobs"
        params = {"operation": "digitization", "output_format": "md"}
        data = {"operation": "digitization", "output_format": "md"}

        @async_retry_on_transient_error()
        async def _post() -> str:
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                with open(fp, "rb") as fh:
                    files = {"files": (fp.name, fh.read(), "application/pdf")}
                    resp = await client.post(
                        url,
                        params=params,
                        data=data,
                        files=files,
                    )
                resp.raise_for_status()
                return resp.json()["job_id"]

        return await _post()

    submit_pdf = submit_document

    async def poll_job(
        self,
        job_id: str,
        timeout: float | None = None,
        poll_interval: float | None = None,
    ) -> str:
        """Poll GET /v1/jobs/{job_id} until status=completed.

        Returns Markdown content fetched from /v1/documents/{doc_id}/content.
        Raises TimeoutError or RuntimeError on failure/cancellation.
        """
        timeout_val = timeout if timeout is not None else settings.invoice.job_timeout_seconds
        interval_val = (
            poll_interval if poll_interval is not None else settings.invoice.job_poll_interval_seconds
        )
        deadline = time.monotonic() + timeout_val

        while time.monotonic() < deadline:
            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                resp = await client.get(f"{self.base_url}/v1/jobs/{job_id}")
                resp.raise_for_status()
                data = resp.json()

            status = data.get("status")
            if status == "completed":
                docs = data.get("documents", [])
                if not docs:
                    raise RuntimeError(f"Digitize job {job_id} completed but returned no documents")
                doc_id = docs[0].get("id") or docs[0].get("doc_id")
                if not doc_id:
                    raise RuntimeError(f"Digitize job {job_id} completed but document has no id")

                async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                    content_resp = await client.get(f"{self.base_url}/v1/documents/{doc_id}/content")
                    content_resp.raise_for_status()
                    try:
                        content_json = content_resp.json()
                        if isinstance(content_json, dict) and "result" in content_json:
                            res = content_json["result"]
                            return res if isinstance(res, str) else str(res)
                        elif isinstance(content_json, str):
                            return content_json
                    except Exception:
                        pass
                    return content_resp.text

            if status in ("failed", "cancelled", "completed_with_errors"):
                error_msg = data.get("error") or ""
                raise RuntimeError(
                    f"Digitize job {job_id} ended with status={status!r}: {error_msg}".strip()
                )

            await asyncio.sleep(interval_val)

        raise TimeoutError(
            f"Digitize job {job_id} did not complete within {timeout_val}s"
        )
