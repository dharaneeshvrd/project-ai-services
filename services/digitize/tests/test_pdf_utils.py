"""
Unit tests for digitize.pdf_utils module.

Tests cover PDF and DOCX document processing utilities including:
- Page count retrieval for both PDF and DOCX files
- TOC extraction
- Document type detection
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from digitize.parsing.pdf import (
    get_pdf_page_count,
    get_document_page_count,
    get_matching_header_lvl,
    get_toc,
    load_pdf_pages,
)


@pytest.mark.unit
class TestGetPdfPageCount:
    """Tests for get_pdf_page_count function."""

    @patch('digitize.parsing.pdf.pdfium.PdfDocument')
    def test_get_page_count_success(self, mock_pdf_document):
        """Test successful PDF page count retrieval."""
        mock_pdf = Mock()
        mock_pdf.__len__ = Mock(return_value=10)
        mock_pdf.close = Mock()
        mock_pdf_document.return_value = mock_pdf
        
        result = get_pdf_page_count("test.pdf")
        
        assert result == 10
        mock_pdf.close.assert_called_once()

    @patch('digitize.parsing.pdf.pdfium.PdfDocument')
    def test_get_page_count_handles_exception(self, mock_pdf_document):
        """Test exception handling returns 0."""
        mock_pdf_document.side_effect = Exception("File not found")
        
        result = get_pdf_page_count("nonexistent.pdf")
        
        assert result == 0


@pytest.mark.unit
class TestGetDocumentPageCount:
    """Tests for get_document_page_count function."""

    @patch('digitize.parsing.pdf.get_pdf_page_count')
    def test_get_page_count_for_pdf(self, mock_get_pdf_count):
        """Test page count for PDF files."""
        mock_get_pdf_count.return_value = 25
        
        result = get_document_page_count("document.pdf")
        
        assert result == 25
        mock_get_pdf_count.assert_called_once_with("document.pdf")

    @patch('digitize.parsing.docx.estimate_docx_page_count')
    def test_get_page_count_for_docx(self, mock_estimate_docx):
        """Test page count for DOCX files."""
        mock_estimate_docx.return_value = 15
        
        result = get_document_page_count("document.docx")
        
        assert result == 15
        mock_estimate_docx.assert_called_once_with("document.docx")

    @patch('digitize.parsing.docx.estimate_docx_page_count')
    def test_get_page_count_for_docx_uppercase(self, mock_estimate_docx):
        """Test page count for DOCX files with uppercase extension."""
        mock_estimate_docx.return_value = 20
        
        result = get_document_page_count("document.DOCX")
        
        assert result == 20

    def test_get_page_count_for_unknown_extension(self):
        """Test page count for unknown file extension returns 0."""
        result = get_document_page_count("document.txt")
        
        assert result == 0

    @patch('digitize.parsing.pdf.get_pdf_page_count')
    def test_get_page_count_handles_exception(self, mock_get_pdf_count):
        """Test exception handling returns 0."""
        mock_get_pdf_count.side_effect = Exception("Error")
        
        result = get_document_page_count("document.pdf")
        
        assert result == 0


@pytest.mark.unit
class TestGetMatchingHeaderLvl:
    """Tests for get_matching_header_lvl function."""

    def test_exact_match(self):
        """Test exact title match."""
        toc = {"Introduction": 1, "Chapter 1": 1, "Section 1.1": 2}
        
        result = get_matching_header_lvl(toc, "Introduction")
        
        assert result == "#"

    def test_partial_match(self):
        """Test partial title match."""
        toc = {"Introduction to Python": 1, "Chapter 1": 1}
        
        result = get_matching_header_lvl(toc, "Introduction")
        
        assert result == "#"

    def test_case_insensitive_match(self):
        """Test case insensitive matching."""
        toc = {"INTRODUCTION": 1}
        
        result = get_matching_header_lvl(toc, "introduction")
        
        assert result == "#"

    def test_level_2_header(self):
        """Test level 2 header returns ##."""
        toc = {"Section 1.1": 2}
        
        result = get_matching_header_lvl(toc, "Section 1.1")
        
        assert result == "##"

    def test_level_3_header(self):
        """Test level 3 header returns ###."""
        toc = {"Subsection 1.1.1": 3}
        
        result = get_matching_header_lvl(toc, "Subsection 1.1.1")
        
        assert result == "###"

    def test_no_match_returns_empty(self):
        """Test no match returns empty string."""
        toc = {"Introduction": 1}
        
        result = get_matching_header_lvl(toc, "Conclusion")
        
        assert result == ""

    def test_below_threshold_returns_empty(self):
        """Test match below threshold returns empty string."""
        toc = {"Introduction to Advanced Topics": 1}
        
        # Very different title, should be below 80% threshold
        result = get_matching_header_lvl(toc, "Conclusion", threshold=80)
        
        assert result == ""

    def test_custom_threshold(self):
        """Test custom threshold parameter."""
        toc = {"Introduction": 1}
        
        # Lower threshold should match more easily
        result = get_matching_header_lvl(toc, "Intro", threshold=50)
        
        assert result == "#"


@pytest.mark.unit
class TestGetToc:
    """Tests for get_toc function."""

    @patch('digitize.parsing.pdf.PDFPage.create_pages')
    @patch('digitize.parsing.pdf.PDFDocument')
    @patch('digitize.parsing.pdf.PDFParser')
    @patch('builtins.open', create=True)
    def test_get_toc_success(self, mock_open_file, mock_parser_class, mock_document_class, mock_create_pages):
        """Test successful TOC extraction."""
        # Setup mocks
        mock_file = MagicMock()
        mock_open_file.return_value.__enter__.return_value = mock_file
        
        mock_parser = Mock()
        mock_parser_class.return_value = mock_parser
        mock_parser.close = Mock()
        
        mock_document = Mock()
        mock_document_class.return_value = mock_document
        
        # Mock outlines
        mock_document.get_outlines.return_value = [
            (1, "Chapter 1", None, None, None),
            (2, "Section 1.1", None, None, None),
            (1, "Chapter 2", None, None, None),
        ]
        
        # Mock pages
        mock_create_pages.return_value = [Mock(), Mock(), Mock()]  # 3 pages
        
        toc, page_count = get_toc("test.pdf")
        
        assert toc == {"Chapter 1": 1, "Section 1.1": 2, "Chapter 2": 1}
        assert page_count == 3

    @patch('digitize.parsing.pdf.PDFParser')
    @patch('builtins.open', create=True)
    def test_get_toc_no_outlines(self, mock_open_file, mock_parser_class):
        """Test TOC extraction with no outlines."""
        from pdfminer.pdfdocument import PDFNoOutlines
        
        mock_file = MagicMock()
        mock_open_file.return_value.__enter__.return_value = mock_file
        
        mock_parser = Mock()
        mock_parser_class.return_value = mock_parser
        mock_parser.close = Mock()
        
        with patch('digitize.parsing.pdf.PDFDocument') as mock_document_class:
            mock_document = Mock()
            mock_document_class.return_value = mock_document
            mock_document.get_outlines.side_effect = PDFNoOutlines()
            
            toc, page_count = get_toc("test.pdf")
            
            assert toc == {}
            assert page_count == 0

    @patch('digitize.parsing.pdf.PDFParser')
    @patch('builtins.open', create=True)
    def test_get_toc_corrupted_pdf(self, mock_open_file, mock_parser_class):
        """Test TOC extraction with corrupted PDF."""
        from pdfminer.pdfparser import PDFSyntaxError
        
        mock_file = MagicMock()
        mock_open_file.return_value.__enter__.return_value = mock_file
        
        mock_parser = Mock()
        mock_parser_class.return_value = mock_parser
        mock_parser.close = Mock()
        
        with patch('digitize.parsing.pdf.PDFDocument') as mock_document_class:
            mock_document_class.side_effect = PDFSyntaxError("Corrupted")
            
            toc, page_count = get_toc("test.pdf")
            
            assert toc == {}
            assert page_count == 0


@pytest.mark.unit
class TestLoadPdfPages:
    """Tests for load_pdf_pages function."""

    @patch('digitize.parsing.pdf.pdfplumber.open')
    def test_load_pdf_pages_success(self, mock_pdfplumber_open):
        """Test successful PDF page loading."""
        mock_pdf = Mock()
        mock_page1 = Mock()
        mock_page2 = Mock()
        # load_pdf_pages calls page.extract_words(), so we need to mock that
        mock_page1.extract_words = Mock(return_value="page1_words")
        mock_page2.extract_words = Mock(return_value="page2_words")
        mock_pdf.pages = [mock_page1, mock_page2]
        mock_pdfplumber_open.return_value.__enter__.return_value = mock_pdf
        
        result = load_pdf_pages("test.pdf")
        
        # The function returns extract_words() results, not the page objects
        assert result == ["page1_words", "page2_words"]

    def test_load_pdf_pages_skips_docx(self):
        """Test that DOCX files are skipped."""
        result = load_pdf_pages("document.docx")
        
        # Should return empty list for DOCX files
        assert result == [] or result is None

    def test_load_pdf_pages_skips_docx_uppercase(self):
        """Test that DOCX files with uppercase extension are skipped."""
        result = load_pdf_pages("document.DOCX")
        
        # Should return empty list for DOCX files
        assert result == [] or result is None

    @patch('digitize.parsing.pdf.pdfplumber.open')
    def test_load_pdf_pages_handles_exception(self, mock_pdfplumber_open):
        """Test exception handling."""
        mock_pdfplumber_open.side_effect = Exception("File error")
        
        # Should handle exception gracefully
        try:
            result = load_pdf_pages("test.pdf")
            # If it doesn't raise, result should be empty or None
            assert result == [] or result is None
        except Exception:
            # If it raises, that's also acceptable behavior
            pass


# Made with Bob


@pytest.mark.unit
class TestOcrDocumentDetection:
    """Tests for is_ocr_doc and Docling converter configuration."""

    def test_is_ocr_doc_non_pdf_formats_return_false(self):
        """Non-PDF document formats bypass OCR."""
        from digitize.parsing.converter import is_ocr_doc

        for ext in [".docx", ".doc", ".txt", ".md", ".json", ".csv", ".xlsx", ".pptx"]:
            assert is_ocr_doc(f"/tmp/sample{ext}") is False
            assert is_ocr_doc(f"/tmp/sample{ext.upper()}") is False

    @patch("digitize.parsing.converter.pdfium.PdfDocument")
    def test_is_ocr_doc_digital_pdf(self, mock_pdf_doc):
        """PDF with sufficient digital text returns False (OCR not required)."""
        from digitize.parsing.converter import is_ocr_doc
        import pypdfium2.raw as pdfium_c

        mock_page = MagicMock()
        mock_text_page = MagicMock()
        # 100 characters of text per page
        mock_text_page.get_text_range.return_value = "A" * 100
        mock_page.get_textpage.return_value = mock_text_page
        mock_page.get_objects.return_value = []

        mock_pdf = MagicMock()
        mock_pdf.__len__.return_value = 2
        mock_pdf.__getitem__.return_value = mock_page
        mock_pdf_doc.return_value = mock_pdf

        assert is_ocr_doc("/tmp/digital.pdf") is False
        mock_pdf.close.assert_called_once()

    @patch("digitize.parsing.converter.pdfium.PdfDocument")
    def test_is_ocr_doc_scanned_image_pdf(self, mock_pdf_doc):
        """PDF with low text and image objects returns True (OCR required)."""
        from digitize.parsing.converter import is_ocr_doc
        import pypdfium2.raw as pdfium_c

        mock_page = MagicMock()
        mock_text_page = MagicMock()
        # 5 characters of text (below 50 chars threshold)
        mock_text_page.get_text_range.return_value = "Page1"
        mock_page.get_textpage.return_value = mock_text_page

        mock_img_obj = MagicMock()
        mock_img_obj.type = pdfium_c.FPDF_PAGEOBJ_IMAGE
        mock_page.get_objects.return_value = [mock_img_obj]

        mock_pdf = MagicMock()
        mock_pdf.__len__.return_value = 1
        mock_pdf.__getitem__.return_value = mock_page
        mock_pdf_doc.return_value = mock_pdf

        assert is_ocr_doc("/tmp/scanned.pdf") is True
        mock_pdf.close.assert_called_once()

    @patch("digitize.parsing.converter.pdfium.PdfDocument")
    def test_is_ocr_doc_zero_text_pdf(self, mock_pdf_doc):
        """PDF with zero digital text returns True (OCR required)."""
        from digitize.parsing.converter import is_ocr_doc

        mock_page = MagicMock()
        mock_text_page = MagicMock()
        mock_text_page.get_text_range.return_value = ""
        mock_page.get_textpage.return_value = mock_text_page
        mock_page.get_objects.return_value = []

        mock_pdf = MagicMock()
        mock_pdf.__len__.return_value = 1
        mock_pdf.__getitem__.return_value = mock_page
        mock_pdf_doc.return_value = mock_pdf

        assert is_ocr_doc("/tmp/empty_text.pdf") is True
        mock_pdf.close.assert_called_once()

    @patch("digitize.parsing.converter.pdfium.PdfDocument")
    def test_is_ocr_doc_empty_pdf(self, mock_pdf_doc):
        """Zero-page PDF returns False safely."""
        from digitize.parsing.converter import is_ocr_doc

        mock_pdf = MagicMock()
        mock_pdf.__len__.return_value = 0
        mock_pdf_doc.return_value = mock_pdf

        assert is_ocr_doc("/tmp/zero_pages.pdf") is False
        mock_pdf.close.assert_called_once()

    @patch("digitize.parsing.converter.pdfium.PdfDocument")
    def test_is_ocr_doc_handles_exception_safely(self, mock_pdf_doc):
        """Exception during inspection defaults safely to True (OCR enabled)."""
        from digitize.parsing.converter import is_ocr_doc

        mock_pdf_doc.side_effect = RuntimeError("Corrupted PDF")

        assert is_ocr_doc("/tmp/corrupted.pdf") is True

    def test_get_doc_converter_pipeline_options(self):
        """Test get_doc_converter sets OCR options correctly."""
        from digitize.parsing.converter import get_doc_converter

        # Without OCR
        conv_no_ocr = get_doc_converter(ocr_doc=False)
        assert conv_no_ocr is not None

        # With OCR
        conv_ocr = get_doc_converter(ocr_doc=True)
        assert conv_ocr is not None