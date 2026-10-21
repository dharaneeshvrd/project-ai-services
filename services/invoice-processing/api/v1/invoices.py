"""
Invoice Processing v1 API endpoints.
"""

import asyncio
import mimetypes
import shutil
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from common.misc_utils import get_logger

from db.manager import db_manager
from models import (
    InvoiceJobCreatedResponse,
    InvoiceJobDetailResponse,
    PaginatedResponse,
    PaginationInfo,
    UpdateInvoiceJobRequest,
)
from pipeline.orchestrator import _run_pipeline
from pipeline.router import detect_input_type
from schema_registration import ensure_invoice_schema_registered
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
    file: UploadFile = File(..., description="Invoice file (.pdf / .png / .jpg / .jpeg)"),
):
    """Accept an invoice file, detect its type, create a job record, and dispatch
    the pipeline in the background.

    Returns **202 Accepted** immediately with the new ``job_id``.
    """
    # Lazily register the extraction schema with the extract service on first submission.
    await ensure_invoice_schema_registered()

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
# GET /v1/invoices/{job_id}/file
# ---------------------------------------------------------------------------

@router.get(
    "/{job_id}/file",
    summary="Download or preview original invoice file",
)
async def get_invoice_file(job_id: str):
    """Stream the original uploaded PDF or image file for human review and UI preview."""
    job = db_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice job {job_id!r} not found",
        )
    file_path = settings.invoice.staging_dir / job_id / (job.filename or "upload")
    if not file_path.exists() or not file_path.is_file():
        job_dir = settings.invoice.staging_dir / job_id
        staged_files = [p for p in job_dir.iterdir() if p.is_file()] if job_dir.exists() else []
        if staged_files:
            file_path = staged_files[0]
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invoice file for job {job_id!r} not found on disk",
            )
    media_type, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(
        path=file_path,
        filename=job.filename or file_path.name,
        media_type=media_type or "application/octet-stream",
        content_disposition_type="inline",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/invoices/{job_id}
# ---------------------------------------------------------------------------

@router.patch(
    "/{job_id}",
    response_model=InvoiceJobDetailResponse,
    summary="Update staged invoice fields for human review",
)
async def update_invoice_job(job_id: str, body: UpdateInvoiceJobRequest):
    """Correct extracted invoice data while the job is in REVIEW status.

    Only ``staged_header`` and ``staged_lines`` are
    accepted — these are the fields a human reviewer may need to fix before
    approving.  The job must be in ``review`` status; any other status
    returns HTTP 409.
    """
    job = db_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice job {job_id!r} not found",
        )
    if job.status != "review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id!r} is in '{job.status}' status; only jobs in 'review' status can be updated.",
        )

    update_kwargs = {}
    if body.staged_header is not None:
        update_kwargs["staged_header"] = body.staged_header
    if body.staged_lines is not None:
        update_kwargs["staged_lines"] = body.staged_lines

    if update_kwargs:
        job = db_manager.update_job(job_id=job_id, **update_kwargs)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update job {job_id!r}",
            )
    return _job_to_response(job)


# ---------------------------------------------------------------------------
# DELETE /v1/invoices/{job_id}
# ---------------------------------------------------------------------------

@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an invoice processing job",
)
async def delete_invoice_job(job_id: str):
    """Delete an invoice job record and any associated staged data.

    Terminal-status jobs (``approved``, ``rejected``, ``failed``) and jobs
    still in ``review`` may be deleted.  Jobs actively in-flight
    (``accepted``, ``extracting``) return HTTP 409 — cancel is not supported.
    """
    job = db_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice job {job_id!r} not found",
        )
    if job.status in ("accepted", "extracting"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id!r} is currently {job.status} and cannot be deleted.",
        )

    deleted = db_manager.delete_job(job_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete job {job_id!r}",
        )

    job_dir = settings.invoice.staging_dir / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /v1/invoices/{job_id}/approve
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/approve",
    response_model=InvoiceJobDetailResponse,
    summary="Approve reviewed invoice",
)
async def approve_invoice_job(job_id: str):
    """Approve a reviewed invoice (review → approved)."""
    job = db_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice job {job_id!r} not found",
        )
    if job.status != "review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id!r} is in '{job.status}' status; only jobs in 'review' status can be approved.",
        )
    job = db_manager.update_job(job_id=job_id, status="approved")
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve job {job_id!r}",
        )
    return _job_to_response(job)


# ---------------------------------------------------------------------------
# POST /v1/invoices/{job_id}/reject
# ---------------------------------------------------------------------------

@router.post(
    "/{job_id}/reject",
    response_model=InvoiceJobDetailResponse,
    summary="Reject staged invoice",
)
async def reject_invoice_job(job_id: str):
    """Reject a staged invoice (review → rejected)."""
    job = db_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice job {job_id!r} not found",
        )
    if job.status != "review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id!r} is in '{job.status}' status; only jobs in 'review' status can be rejected.",
        )
    job = db_manager.update_job(job_id=job_id, status="rejected")
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject job {job_id!r}",
        )
    return _job_to_response(job)
