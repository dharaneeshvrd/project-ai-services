"""
Database manager for invoice processing job CRUD operations.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from common.misc_utils import get_logger
from db.connection import get_db_session
from db.models import InvoiceJob

logger = get_logger("db.manager")


class DatabaseManager:
    """CRUD operations for InvoiceJob records."""

    @staticmethod
    def create_job(
        job_id: str,
        filename: str,
        input_type: str,
        status: str = "accepted",
        submitted_at: Optional[datetime] = None,
    ) -> Optional[InvoiceJob]:
        """Insert a new invoice job row.

        Returns the created ``InvoiceJob`` or ``None`` on integrity/DB error.
        """
        try:
            with get_db_session() as session:
                now = submitted_at or datetime.now(timezone.utc)
                job = InvoiceJob(
                    job_id=job_id,
                    filename=filename,
                    input_type=input_type,
                    status=status,
                    submitted_at=now,
                    updated_at=now,
                )
                session.add(job)
                session.flush()
                return job
        except IntegrityError:
            logger.error(f"Invoice job {job_id!r} already exists")
            return None
        except SQLAlchemyError as exc:
            logger.error(f"DB error creating job {job_id!r}: {exc}", exc_info=True)
            return None

    @staticmethod
    def get_job(job_id: str) -> Optional[InvoiceJob]:
        """Retrieve a single job by primary key.

        Returns ``None`` if not found.
        """
        try:
            with get_db_session() as session:
                return session.get(InvoiceJob, job_id)
        except SQLAlchemyError as exc:
            logger.error(f"DB error fetching job {job_id!r}: {exc}", exc_info=True)
            return None

    @staticmethod
    def update_job(job_id: str, **fields: Any) -> Optional[InvoiceJob]:
        """Update mutable fields on an existing job.

        Automatically sets ``updated_at`` to now.  Caller may pass any column
        name accepted by ``InvoiceJob`` as a keyword argument.

        Returns the updated row or ``None`` if not found or on DB error.
        """
        try:
            with get_db_session() as session:
                job = session.get(InvoiceJob, job_id)
                if job is None:
                    logger.warning(f"update_job: job {job_id!r} not found")
                    return None
                for key, value in fields.items():
                    setattr(job, key, value)
                job.updated_at = datetime.now(timezone.utc)
                # Mark completed_at when reaching a terminal state
                if fields.get("status") in ("completed", "rejected", "failed"):
                    if job.completed_at is None:
                        job.completed_at = datetime.now(timezone.utc)
                session.flush()
                return job
        except SQLAlchemyError as exc:
            logger.error(f"DB error updating job {job_id!r}: {exc}", exc_info=True)
            return None

    @staticmethod
    def list_jobs(
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
    ) -> tuple[list[InvoiceJob], int]:
        """Return a paginated list of jobs and the total count.

        Args:
            page: 1-based page index.
            page_size: Rows per page (1–100).
            status_filter: If set, only return jobs with this status value.

        Returns:
            ``(rows, total_count)`` tuple.
        """
        try:
            with get_db_session() as session:
                query = select(InvoiceJob)
                count_query = select(func.count()).select_from(InvoiceJob)

                if status_filter:
                    query = query.where(InvoiceJob.status == status_filter)
                    count_query = count_query.where(InvoiceJob.status == status_filter)

                total: int = session.execute(count_query).scalar_one()

                offset = (page - 1) * page_size
                rows = list(
                    session.execute(
                        query.order_by(InvoiceJob.submitted_at.desc())
                        .offset(offset)
                        .limit(page_size)
                    ).scalars()
                )
                return rows, total
        except SQLAlchemyError as exc:
            logger.error(f"DB error listing jobs: {exc}", exc_info=True)
            return [], 0


db_manager = DatabaseManager()
