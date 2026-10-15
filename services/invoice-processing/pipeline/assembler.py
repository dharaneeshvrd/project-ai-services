"""
Invoice assembler: normalizes raw extraction into canonical staging format.
"""

import re
from typing import Any, Optional

from common.misc_utils import get_logger

logger = get_logger("pipeline.assembler")


def _safe_str(val: Any) -> Optional[str]:
    """Coerce value to trimmed string or None."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _safe_int(val: Any) -> Optional[int]:
    """Coerce value to integer or None."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        try:
            return int(val)
        except (ValueError, OverflowError):
            return None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            pass
        cleaned = re.sub(r"[^\d.-]", "", s)
        try:
            return int(float(cleaned))
        except (ValueError, OverflowError):
            return None
    return None


def _safe_float(val: Any) -> Optional[float]:
    """Coerce string/numeric LLM output (e.g. 'USD 33,659.75', '1,250.00') to float or None."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        is_negative = False
        if s.startswith("(") and s.endswith(")"):
            is_negative = True
            s = s[1:-1].strip()
        elif s.startswith("-"):
            is_negative = True
            s = s[1:].strip()

        cleaned = re.sub(r"[^\d.]", "", s)
        if not cleaned:
            return None
        try:
            num = float(cleaned)
            return -num if is_negative else num
        except ValueError:
            return None
    return None


def assemble_invoice(
    raw: dict[str, Any] | None = None,
    job_id: str = "",
    filename: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Map extract service raw JSON → (header_dict, lines_list).
    Normalises keys (case-insensitive lookup), coerces types using _safe_* helpers.
    Returns (ap_invoices_all, ap_invoice_lines_all) ready for JSON persistence and review.
    """
    raw_dict = raw if isinstance(raw, dict) else {}
    raw_lower = {k.lower(): v for k, v in raw_dict.items() if isinstance(k, str)}

    if "ap_invoices_all" not in raw_lower and "invoice" not in raw_lower:
        nested = raw_lower.get("data") or raw_lower.get("extraction") or raw_lower.get("result")
        if isinstance(nested, dict):
            raw_lower = {k.lower(): v for k, v in nested.items() if isinstance(k, str)}

    invoice_raw = (
        raw_lower.get("ap_invoices_all")
        or raw_lower.get("invoice")
        or {}
    )
    if not isinstance(invoice_raw, dict):
        invoice_raw = {}
    invoice = {k.lower(): v for k, v in invoice_raw.items() if isinstance(k, str)}

    lines_raw = (
        raw_lower.get("ap_invoice_lines_all")
        or raw_lower.get("lines")
        or []
    )
    if not isinstance(lines_raw, list):
        lines_raw = []
    lines = [{k.lower(): v for k, v in ln.items() if isinstance(k, str)} for ln in lines_raw if isinstance(ln, dict)]

    header = {
        "INVOICE_NUM":               _safe_str(invoice.get("invoice_num")),
        "INVOICE_TYPE_LOOKUP_CODE":  _safe_str(invoice.get("invoice_type_lookup_code")) or "STANDARD",
        "INVOICE_DATE":              _safe_str(invoice.get("invoice_date")),
        "VENDOR_ID":                 _safe_int(invoice.get("vendor_id")),
        "VENDOR_NUM":                _safe_str(invoice.get("vendor_num")),
        "VENDOR_NAME":               _safe_str(invoice.get("vendor_name")),
        "INVOICE_AMOUNT":            _safe_float(invoice.get("invoice_amount")),
        "INVOICE_CURRENCY_CODE":     _safe_str(invoice.get("invoice_currency_code")),
        "SOURCE":                    _safe_str(invoice.get("source")) or "AI_IMPORT",
        "SELLER":                    _safe_str(invoice.get("seller")),
        "SELLER_ADDRESS":            _safe_str(invoice.get("seller_address")),
        "CLIENT":                    _safe_str(invoice.get("client")),
        "CLIENT_ADDRESS":            _safe_str(invoice.get("client_address")),
        "_job_id":                   job_id,
        "_source_file":              filename,
    }
    assembled_lines = []
    for i, ln in enumerate(lines, start=1):
        assembled_lines.append({
            "LINE_NUMBER":           _safe_int(ln.get("line_number")) or i,
            "LINE_TYPE_LOOKUP_CODE": _safe_str(ln.get("line_type_lookup_code")) or "ITEM",
            "AMOUNT":                _safe_float(ln.get("amount")),
            "QUANTITY_INVOICED":     _safe_float(ln.get("quantity_invoiced")),
            "UNIT_PRICE":            _safe_float(ln.get("unit_price")),
            "DESCRIPTION":           _safe_str(ln.get("description")),
        })
    return header, assembled_lines
