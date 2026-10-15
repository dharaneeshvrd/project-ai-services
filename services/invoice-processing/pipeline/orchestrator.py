"""
Invoice pipeline orchestrator.

Drives the end-to-end processing flow for a single job:
  pdf -> digitize -> md -> extract -> assemble
  image -> extract -> assemble  (one-shot VLM path)
"""

from pathlib import Path

from common.misc_utils import get_logger

from clients.digitize_client import DigitizeClient
from clients.extract_client import ExtractClient
from db.manager import db_manager
from pipeline.assembler import assemble_invoice
from pipeline.oneshot_path import run_oneshot_path
from pipeline.pdf_path import run_pdf_path
from settings import settings

logger = get_logger("pipeline.orchestrator")


async def _run_pipeline(job_id: str, staged_path: Path, input_type: str) -> None:
    """Background pipeline task — dispatches to pdf_path or oneshot_path.

    The job moves ``accepted`` → ``extracting`` as soon as it is picked up and
    stays ``extracting`` through digitize, extract and ``assemble_invoice``;
    it then moves directly to ``review``.

    Routing decision:
    - ``"image"`` → One-Shot path: forward directly to extract VLM endpoint.
    - ``"pdf"``   → PDF path: forward to digitize as-is; digitize owns
                    internal type detection.
    """
    try:
        db_manager.update_job(job_id=job_id, status="extracting")

        if input_type == "image":
            logger.info(
                f"[{job_id}] Routing decision: image → One-Shot path (extract VLM)"
            )
            extract_client = ExtractClient(base_url=settings.invoice.extract_url)
            raw = await run_oneshot_path(
                job_id=job_id,
                staged_path=staged_path,
                extract_client=extract_client,
            )
        elif input_type == "pdf":
            logger.info(
                f"[{job_id}] Routing decision: pdf → PDF path (digitize)"
            )
            digitize_client = DigitizeClient(base_url=settings.invoice.digitize_url)
            raw = await run_pdf_path(
                job_id=job_id,
                staged_path=staged_path,
                digitize_client=digitize_client,
            )
        else:
            raise ValueError(f"Unknown input_type {input_type!r} for job {job_id}")

        header, lines = assemble_invoice(raw=raw, job_id=job_id, filename=staged_path.name)

        # Persist structured JSON exclusively to PostgreSQL DB (JSONB fields)
        db_manager.update_job(
            job_id=job_id,
            status="review",
            staged_header=header,
            staged_lines=lines,
        )
        logger.info(
            f"Job {job_id!r} completed assembly, stored in DB, waiting for human review"
        )

    except Exception as exc:
        logger.error(f"Pipeline failed for job {job_id!r}: {exc}", exc_info=True)
        db_manager.update_job(job_id=job_id, status="failed", error=str(exc))
