"""
PDF-path pipeline: PDF files → digitize service → markdown → extract service → raw extraction.
"""

from pathlib import Path

from clients.digitize_client import DigitizeClient
from clients.extract_client import ExtractClient
from common.misc_utils import get_logger
from db.manager import db_manager
from settings import settings

logger = get_logger("pipeline.pdf_path")


async def run_pdf_path(
    job_id: str,
    staged_path: Path,
    digitize_client: DigitizeClient | None = None,
    extract_client: ExtractClient | None = None,
) -> dict:
    """Full PDF-path orchestration.

    The job status stays 'extracting' for the whole run (set by the caller);
    this function only records the downstream job ids.

    1. Submits staged PDF to digitize service
    2. Records digitize_job_id in DB
    3. Polls digitize service until complete, retrieving Markdown content
    4. Submits Markdown content to extract service
    5. Records extract_job_id in DB
    6. Polls extract service until complete, returning raw extraction result dict.

    Args:
        job_id: Unique identifier for this processing job.
        staged_path: Path to the staged PDF file on disk.
        digitize_client: Optional DigitizeClient instance.
        extract_client: Optional ExtractClient instance.

    Returns:
        Raw extraction result dict from the extract service.
    """
    if digitize_client is None:
        digitize_client = DigitizeClient(base_url=settings.invoice.digitize_url)
    if extract_client is None:
        extract_client = ExtractClient(base_url=settings.invoice.extract_url)

    logger.info(f"[{job_id}] PDF path: starting digitize for {staged_path.name!r}")

    digitize_job_id = await digitize_client.submit_document(job_id=job_id, file_path=staged_path)
    db_manager.update_job(job_id=job_id, digitize_job_id=digitize_job_id)

    markdown = await digitize_client.poll_job(digitize_job_id)

    logger.info(f"[{job_id}] PDF path: digitization complete, submitting to extract service")

    extract_job_id = await extract_client.submit_extraction(
        job_id=job_id,
        content=markdown,
        schema_name=settings.invoice.extract_schema_name,
    )
    db_manager.update_job(job_id=job_id, extract_job_id=extract_job_id)

    result = await extract_client.poll_job(extract_job_id)
    logger.info(f"[{job_id}] PDF path: extraction complete")
    return result
