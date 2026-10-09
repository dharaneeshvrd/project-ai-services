"""
Invoice Processing v1 API endpoints.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from common.misc_utils import get_logger

from clients.digitize_client import DigitizeClient
from clients.extract_client import ExtractClient
from db.manager import db_manager
from models import (
    InvoiceJobCreatedResponse,
    InvoiceJobDetailResponse,
    PaginatedResponse,
    PaginationInfo,
)
from pipeline.oneshot_path import run_oneshot_path
from pipeline.pdf_path import run_pdf_path
from pipeline.router import detect_input_type
from settings import settings

logger = get_logger("api.v1.invoices")

router = APIRouter()


def _stage_uploaded_file(job_id: str, file: UploadFile) -> Path:
    """Save the uploaded file under ``staging_dir/<job_id>/``.

    Returns the path to the saved file.
    """
    job_dir = settings.invoice.staging_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dest = job_dir / (file.filename or "upload")
    dest.write_bytes(file.file.read())
    return dest


async def _run_pipeline(job_id: str, staged_path: Path, input_type: str) -> None:
    """Background pipeline task — dispatches to pdf_path or oneshot_path.

    Routing decision:
    - ``"image"`` → One-Shot path: forward directly to extract VLM endpoint.
    - ``"pdf"``   → PDF path: forward to digitize as-is; digitize owns
                    internal type detection.
    """
    try:
        db_manager.update_job(job_id=job_id, status="routing")

        if input_type == "image":
            logger.info(
                f"[{job_id}] Routing decision: image → One-Shot path (extract VLM)"
            )
            extract_client = ExtractClient(base_url=settings.invoice.extract_url)
            await run_oneshot_path(
                job_id=job_id,
                staged_path=staged_path,
                extract_client=extract_client,
            )
        else:  # "pdf"
            logger.info(
                f"[{job_id}] Routing decision: pdf → PDF path (digitize)"
            )
            digitize_client = DigitizeClient(base_url=settings.invoice.digitize_url)
            await run_pdf_path(
                job_id=job_id,
                staged_path=staged_path,
                digitize_client=digitize_client,
            )

    except Exception as exc:
        logger.error(f"Pipeline failed for job {job_id!r}: {exc}", exc_info=True)
        db_manager.update_job(job_id=job_id, status="failed", error=str(exc))
    finally:
        staged_path.unlink(missing_ok=True)


def _job_to_response(job) -> InvoiceJobDetailResponse:
    return InvoiceJobDetailResponse.model_validate(job)


# ---------------------------------------------------------------------------
# POST /v1/invoices
# ---------------------------------------------------------------------------

@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=InvoiceJobCreatedResponse,
    summary="Submit invoice document for processing",
)
async def submit_invoice(
    file: UploadFile = File(..., description="Invoice file (.pdf / .png / .jpg / .jpeg / .tiff)"),
):
    """Accept an invoice file, detect its type, create a job record, and dispatch
    the pipeline in the background.

    Returns **202 Accepted** immediately with the new ``job_id``.
    """
    try:
        input_type = detect_input_type(file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))

    job_id = str(uuid.uuid4())
    staged_path = _stage_uploaded_file(job_id, file)

    db_manager.create_job(
        job_id=job_id,
        filename=file.filename or "",
        input_type=input_type,
        status="accepted",
        submitted_at=datetime.now(timezone.utc),
    )
    logger.info(f"Invoice job {job_id!r} accepted (input_type={input_type!r})")

    asyncio.create_task(_run_pipeline(job_id, staged_path, input_type))

    return InvoiceJobCreatedResponse(job_id=job_id, input_type=input_type)


# ---------------------------------------------------------------------------
# GET /v1/invoices/{job_id}
# ---------------------------------------------------------------------------

@router.get(
    "/{job_id}",
    response_model=InvoiceJobDetailResponse,
    summary="Get invoice processing job detail",
)
async def get_invoice_job(job_id: str):
    """Retrieve the current status and result for an invoice processing job."""
    job = db_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice job {job_id!r} not found",
        )
    return _job_to_response(job)


# ---------------------------------------------------------------------------
# GET /v1/invoices
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=PaginatedResponse[InvoiceJobDetailResponse],
    summary="List invoice processing jobs",
)
async def list_invoice_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: Optional[str] = Query(default=None, alias="status"),
):
    """Return a paginated list of invoice jobs, newest first."""
    jobs, total = db_manager.list_jobs(
        page=page,
        page_size=page_size,
        status_filter=status_filter,
    )
    total_pages = ceil(total / page_size) if page_size else 0
    return PaginatedResponse(
        data=[_job_to_response(j) for j in jobs],
        pagination=PaginationInfo(
            page=page,
            page_size=page_size,
            total_items=total,
            total_pages=total_pages,
        ),
    )


# ---------------------------------------------------------------------------
# POST /v1/invoices/{job_id}/approve   (stub — PR-7)
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/approve",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Approve invoice for database loading (stub — PR-7)",
)
async def approve_invoice_job(job_id: str):
    """Approve a staged invoice for DB load. Implemented in PR-7."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Invoice job approval is not yet implemented (PR-7).",
    )


# ---------------------------------------------------------------------------
# POST /v1/invoices/{job_id}/reject   (stub — PR-7)
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/reject",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Reject staged invoice (stub — PR-7)",
)
async def reject_invoice_job(job_id: str):
    """Reject a staged invoice. Implemented in PR-7."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Invoice job rejection is not yet implemented (PR-7).",
    )
