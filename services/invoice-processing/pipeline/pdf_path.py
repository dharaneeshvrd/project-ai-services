"""
PDF-path pipeline: PDF files → digitize service (as-is, no type detection here).
"""

from pathlib import Path

from clients.digitize_client import DigitizeClient
from common.misc_utils import get_logger

logger = get_logger("pipeline.pdf_path")


async def run_pdf_path(
    job_id: str,
    staged_path: Path,
    digitize_client: DigitizeClient,
) -> dict:
    """Forward a PDF invoice to the digitize service as-is.

    Type detection (scanned vs. digital-native) is owned by digitize — this
    function simply submits the document and returns the raw result.

    Args:
        job_id: Job identifier (used for logging).
        staged_path: Path to the staged PDF file on disk.
        digitize_client: Pre-configured :class:`DigitizeClient` instance.

    Returns:
        Raw submission result dict from the digitize service.
    """
    logger.info(
        f"[{job_id}] PDF path: forwarding {staged_path.name!r} to digitize (as-is)"
    )
    result = await digitize_client.submit_document(
        job_id=job_id,
        file_path=staged_path,
    )
    logger.info(f"[{job_id}] PDF path: digitize submission complete")
    return result
