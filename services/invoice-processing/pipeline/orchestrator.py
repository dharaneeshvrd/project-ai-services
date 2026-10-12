"""
Invoice pipeline orchestrator.

Drives the end-to-end processing flow for a single job:
  pdf -> digitize -> md -> extract -> assemble
  image -> extract -> assemble  (one-shot VLM path)
"""

from pathlib import Path

from common.misc_utils import get_logger

from pipeline.assembler import assemble_invoice
from pipeline.oneshot_path import run_oneshot_path
from pipeline.pdf_path import run_pdf_path

logger = get_logger("pipeline.orchestrator")


async def _run_pipeline(job_id: str, staged_path: Path, input_type: str) -> dict:
    """
    Orchestrate the full invoice processing pipeline for *job_id*.

    Parameters
    ----------
    job_id:       Unique identifier for this processing job.
    staged_path:  Filesystem path to the uploaded invoice file.
    input_type:   ``"pdf"`` or ``"image"`` as returned by
                  :func:`pipeline.router.detect_input_type`.

    Returns
    -------
    dict
        Assembled invoice payload with ``staged_header`` and
        ``staged_lines`` ready for downstream DB load.
    """
    logger.info("Starting pipeline job_id=%s input_type=%s path=%s", job_id, input_type, staged_path)

    if input_type == "pdf":
        # pdf -> digitize -> md -> extract
        raw = await run_pdf_path(job_id=job_id, staged_path=staged_path)
    elif input_type == "image":
        # image -> one-shot VLM extract
        raw = await run_oneshot_path(job_id=job_id, staged_path=staged_path)
    else:
        raise ValueError(f"Unknown input_type '{input_type}' for job {job_id}")

    result = assemble_invoice(job_id=job_id, raw=raw)
    logger.info("Pipeline complete job_id=%s", job_id)
    return result
