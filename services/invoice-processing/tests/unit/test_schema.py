"""
Unit tests for the invoice extraction schema payload.

The extract service's schema normalizer strips any ``required`` key found on a
property schema (see ``extract.utils.schema.normalize_schema``), so nested
objects must flag required fields per-property with ``"required": True``
instead of a standard ``required`` array.
"""

from schema import INVOICE_SCHEMA_PAYLOAD

JSON_SCHEMA = INVOICE_SCHEMA_PAYLOAD["json_schema"]
INVOICE_PROPS = JSON_SCHEMA["properties"]["AP_INVOICES_ALL"]["properties"]

EXPECTED_REQUIRED = {
    "INVOICE_NUM",
    "INVOICE_DATE",
    "VENDOR_NAME",
    "INVOICE_AMOUNT",
    "INVOICE_CURRENCY_CODE",
    "SELLER",
    "SELLER_ADDRESS",
    "CLIENT",
    "CLIENT_ADDRESS",
}

LINE_SCHEMA = JSON_SCHEMA["properties"]["AP_INVOICE_LINES_ALL"]["items"]


def test_root_required_lists_both_sections():
    assert JSON_SCHEMA["required"] == ["AP_INVOICES_ALL", "AP_INVOICE_LINES_ALL"]


def test_header_required_fields_flagged_per_property():
    flagged = {name for name, spec in INVOICE_PROPS.items() if spec.get("required") is True}
    assert flagged == EXPECTED_REQUIRED


def test_header_has_no_nested_required_array():
    """A ``required`` array on a property-level object would be dropped by extract."""
    header = JSON_SCHEMA["properties"]["AP_INVOICES_ALL"]
    assert "required" not in header


def test_line_item_required_fields():
    assert set(LINE_SCHEMA["required"]) == {
        "LINE_NUMBER",
        "AMOUNT",
        "DESCRIPTION",
        "QUANTITY_INVOICED",
        "UNIT_PRICE",
    }


def test_required_line_fields_remain_nullable():
    for name in ("LINE_NUMBER", "AMOUNT", "DESCRIPTION", "QUANTITY_INVOICED", "UNIT_PRICE"):
        assert "null" in LINE_SCHEMA["properties"][name]["type"], name


def test_custom_prompt_present_and_within_limit():
    prompt = INVOICE_SCHEMA_PAYLOAD["custom_prompt"]
    assert prompt and len(prompt) <= 2000


def test_no_property_uses_non_boolean_required():
    for name, spec in INVOICE_PROPS.items():
        assert isinstance(spec.get("required", True), bool), name
