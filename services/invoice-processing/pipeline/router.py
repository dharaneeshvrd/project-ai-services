"""
Extension-based pipeline router for invoice processing.
"""

from pathlib import Path
from typing import Literal

from common.misc_utils import get_logger

logger = get_logger("pipeline.router")

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff"}


def detect_input_type(filename: str) -> Literal["pdf", "image"]:
    """Detect pipeline path from the file extension.

    Args:
        filename: Original upload filename (e.g. "invoice.pdf").

    Returns:
        ``"pdf"`` for PDF files, ``"image"`` for supported image formats.

    Raises:
        ValueError: If the extension is not supported.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in PDF_EXTENSIONS:
        logger.info(f"Routing {filename!r} → PDF path")
        return "pdf"
    if suffix in IMAGE_EXTENSIONS:
        logger.info(f"Routing {filename!r} → One-Shot (image) path")
        return "image"
    raise ValueError(
        f"Unsupported file type: {suffix!r}. Accepted: .pdf, .png, .jpg, .jpeg, .tiff"
    )
