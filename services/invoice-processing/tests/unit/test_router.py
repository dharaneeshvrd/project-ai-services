"""
Unit tests for pipeline/router.py — detect_input_type().
"""

import pytest
from pipeline.router import detect_input_type


@pytest.mark.parametrize("filename", [
    "invoice.pdf",
    "INVOICE.PDF",
    "scan.Pdf",
])
def test_detect_input_type_pdf(filename):
    assert detect_input_type(filename) == "pdf"


@pytest.mark.parametrize("filename", [
    "receipt.png",
    "photo.jpg",
    "photo.JPG",
    "scan.jpeg",
    "doc.tiff",
    "SCAN.TIFF",
])
def test_detect_input_type_image(filename):
    assert detect_input_type(filename) == "image"


@pytest.mark.parametrize("filename,expected_suffix", [
    ("invoice.txt", ".txt"),
    ("data.csv", ".csv"),
    ("archive.zip", ".zip"),
    ("image.bmp", ".bmp"),   # not in spec — rejected
    ("image.webp", ".webp"),
])
def test_detect_input_type_unsupported_raises(filename, expected_suffix):
    with pytest.raises(ValueError, match="Unsupported file type"):
        detect_input_type(filename)


def test_detect_input_type_logs_pdf(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="pipeline.router"):
        detect_input_type("bill.pdf")
    assert "PDF path" in caplog.text


def test_detect_input_type_logs_image(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="pipeline.router"):
        detect_input_type("scan.png")
    assert "One-Shot" in caplog.text
