"""
Unit tests for pipeline/assembler.py.
"""

import pytest
from pipeline.assembler import (
    _safe_float,
    _safe_int,
    _safe_str,
    assemble_invoice,
)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestSafeStr:
    def test_none(self):
        assert _safe_str(None) is None

    def test_empty_string(self):
        assert _safe_str("") is None
        assert _safe_str("   ") is None

    def test_valid_string(self):
        assert _safe_str("INV-100") == "INV-100"
        assert _safe_str("  Acme Corp  ") == "Acme Corp"

    def test_numeric_to_string(self):
        assert _safe_str(123) == "123"


class TestSafeInt:
    def test_none_and_bool(self):
        assert _safe_int(None) is None
        assert _safe_int(True) is None
        assert _safe_int(False) is None

    def test_integer(self):
        assert _safe_int(42) == 42
        assert _safe_int(0) == 0
        assert _safe_int(-5) == -5

    def test_float(self):
        assert _safe_int(42.0) == 42
        assert _safe_int(42.9) == 42

    def test_string_integer(self):
        assert _safe_int("42") == 42
        assert _safe_int(" 100 ") == 100
        assert _safe_int("1,000") == 1000
        assert _safe_int("12.0") == 12

    def test_invalid_string(self):
        assert _safe_int("") is None
        assert _safe_int("invalid") is None


class TestSafeFloat:
    def test_none_and_bool(self):
        assert _safe_float(None) is None
        assert _safe_float(True) is None
        assert _safe_float(False) is None

    def test_numeric(self):
        assert _safe_float(42.5) == 42.5
        assert _safe_float(42) == 42.0
        assert _safe_float(0) == 0.0

    def test_currency_and_commas(self):
        assert _safe_float("USD 33,659.75") == 33659.75
        assert _safe_float("1,250.00") == 1250.0
        assert _safe_float("$1,234.56") == 1234.56
        assert _safe_float("EUR 99.99") == 99.99

    def test_negative_values(self):
        assert _safe_float("-42.50") == -42.5
        assert _safe_float("(100.50)") == -100.5
        assert _safe_float("-USD 50.00") == -50.0

    def test_invalid_strings(self):
        assert _safe_float("") is None
        assert _safe_float("N/A") is None
        assert _safe_float("abc") is None


# ---------------------------------------------------------------------------
# assemble_invoice tests
# ---------------------------------------------------------------------------

class TestAssembleInvoice:
    def test_assemble_invoice_standard_ap_structure(self):
        raw = {
            "AP_INVOICES_ALL": {
                "INVOICE_NUM": "INV-2024-001",
                "INVOICE_TYPE_LOOKUP_CODE": "STANDARD",
                "INVOICE_DATE": "01-JAN-24",
                "VENDOR_ID": 501,
                "VENDOR_NUM": "V-100",
                "VENDOR_NAME": "Acme Supplies",
                "INVOICE_AMOUNT": "USD 33,659.75",
                "INVOICE_CURRENCY_CODE": "USD",
                "SOURCE": "EDI",
                "SELLER": "Acme Supplies Inc.",
                "SELLER_ADDRESS": "123 Main St",
                "CLIENT": "Big Corp",
                "CLIENT_ADDRESS": "456 Market St",
            },
            "AP_INVOICE_LINES_ALL": [
                {
                    "LINE_NUMBER": 1,
                    "LINE_TYPE_LOOKUP_CODE": "ITEM",
                    "AMOUNT": "10,000.00",
                    "QUANTITY_INVOICED": "10",
                    "UNIT_PRICE": "1,000.00",
                    "DESCRIPTION": "Widget A",
                },
                {
                    "LINE_NUMBER": 2,
                    "LINE_TYPE_LOOKUP_CODE": "TAX",
                    "AMOUNT": "23,659.75",
                    "QUANTITY_INVOICED": None,
                    "UNIT_PRICE": None,
                    "DESCRIPTION": "Sales Tax",
                },
            ],
        }

        header, lines = assemble_invoice(raw, job_id="job-123", filename="invoice.pdf")

        assert header["INVOICE_NUM"] == "INV-2024-001"
        assert header["INVOICE_TYPE_LOOKUP_CODE"] == "STANDARD"
        assert header["INVOICE_DATE"] == "01-JAN-24"
        assert header["VENDOR_ID"] == 501
        assert header["VENDOR_NUM"] == "V-100"
        assert header["VENDOR_NAME"] == "Acme Supplies"
        assert header["INVOICE_AMOUNT"] == 33659.75
        assert header["INVOICE_CURRENCY_CODE"] == "USD"
        assert header["SOURCE"] == "EDI"
        assert header["SELLER"] == "Acme Supplies Inc."
        assert header["SELLER_ADDRESS"] == "123 Main St"
        assert header["CLIENT"] == "Big Corp"
        assert header["CLIENT_ADDRESS"] == "456 Market St"
        assert header["_job_id"] == "job-123"
        assert header["_source_file"] == "invoice.pdf"

        assert len(lines) == 2
        assert lines[0] == {
            "LINE_NUMBER": 1,
            "LINE_TYPE_LOOKUP_CODE": "ITEM",
            "AMOUNT": 10000.0,
            "QUANTITY_INVOICED": 10.0,
            "UNIT_PRICE": 1000.0,
            "DESCRIPTION": "Widget A",
        }
        assert lines[1] == {
            "LINE_NUMBER": 2,
            "LINE_TYPE_LOOKUP_CODE": "TAX",
            "AMOUNT": 23659.75,
            "QUANTITY_INVOICED": None,
            "UNIT_PRICE": None,
            "DESCRIPTION": "Sales Tax",
        }

    def test_assemble_invoice_lowercase_and_alternate_keys(self):
        raw = {
            "invoice": {
                "invoice_num": "INV-999",
                "vendor_name": "Globex Corp",
                "invoice_amount": "1,250.00",
                "invoice_currency_code": "USD",
            },
            "lines": [
                {
                    "amount": "1250.00",
                    "description": "Consulting Services",
                }
            ],
        }

        header, lines = assemble_invoice(raw, job_id="job-456", filename="scan.png")

        assert header["INVOICE_NUM"] == "INV-999"
        assert header["INVOICE_TYPE_LOOKUP_CODE"] == "STANDARD"
        assert header["VENDOR_NAME"] == "Globex Corp"
        assert header["INVOICE_AMOUNT"] == 1250.0
        assert header["INVOICE_CURRENCY_CODE"] == "USD"
        assert header["SOURCE"] == "AI_IMPORT"
        assert header["_job_id"] == "job-456"
        assert header["_source_file"] == "scan.png"

        assert len(lines) == 1
        assert lines[0]["LINE_NUMBER"] == 1
        assert lines[0]["LINE_TYPE_LOOKUP_CODE"] == "ITEM"
        assert lines[0]["AMOUNT"] == 1250.0
        assert lines[0]["DESCRIPTION"] == "Consulting Services"

    def test_assemble_invoice_nested_result_envelope(self):
        raw = {
            "result": {
                "ap_invoices_all": {
                    "invoice_num": "INV-NESTED",
                    "invoice_amount": 500.0,
                },
                "ap_invoice_lines_all": [
                    {"amount": 500.0}
                ],
            }
        }

        header, lines = assemble_invoice(raw, job_id="job-nested", filename="test.pdf")
        assert header["INVOICE_NUM"] == "INV-NESTED"
        assert header["INVOICE_AMOUNT"] == 500.0
        assert len(lines) == 1
        assert lines[0]["AMOUNT"] == 500.0

    def test_assemble_invoice_empty_input(self):
        header, lines = assemble_invoice(None, job_id="job-empty", filename="")
        assert header["_job_id"] == "job-empty"
        assert header["INVOICE_TYPE_LOOKUP_CODE"] == "STANDARD"
        assert header["SOURCE"] == "AI_IMPORT"
        assert lines == []
