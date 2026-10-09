"""
One-shot vision pipeline: image files → extract VLM endpoint directly.
"""

from pathlib import Path

from clients.extract_client import ExtractClient
from common.misc_utils import get_logger

logger = get_logger("pipeline.oneshot_path")


async def run_oneshot_path(
    job_id: str,
    staged_path: Path,
    extract_client: ExtractClient,
) -> dict:
    """Route an image invoice directly to the extract VLM endpoint.

    Args:
        job_id: Job identifier (used for logging).
        staged_path: Path to the staged image file on disk.
        extract_client: Pre-configured :class:`ExtractClient` instance.

    Returns:
        Raw extraction result dict from the extract service.
    """
    logger.info(
        f"[{job_id}] One-Shot path: forwarding {staged_path.name!r} to extract VLM"
    )
    result = await extract_client.submit_extraction(
        job_id=job_id,
        file_path=staged_path,
    )
    logger.info(f"[{job_id}] One-Shot path: extract VLM submission complete")
    return result
