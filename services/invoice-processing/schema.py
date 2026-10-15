"""
Invoice extraction schema registered with extract service.
"""

INVOICE_SCHEMA_PAYLOAD = {
    "name": "invoice-extraction-v1",
    "description": "AP_INVOICES_ALL + AP_INVOICE_LINES_ALL structured extraction",
    "json_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["AP_INVOICES_ALL", "AP_INVOICE_LINES_ALL"],
        "properties": {
            "AP_INVOICES_ALL": {
                "type": "object",
                # NOTE: required fields are flagged per-property ("required": True) rather
                # than via a "required" array here. The extract service's schema
                # normalizer strips any "required" key found on a property schema and
                # rebuilds the parent's "required" array from the True flags, so a
                # standard array on this nested object would be silently dropped.
                "properties": {
                    "INVOICE_NUM": {"type": "string", "required": True},
                    "INVOICE_TYPE_LOOKUP_CODE": {"type": ["string", "null"], "default": "STANDARD"},
                    "INVOICE_DATE": {
                        "type": "string",
                        "description": "DD-MON-YY preferred",
                        "required": True,
                    },
                    "VENDOR_ID": {"type": ["integer", "null"]},
                    "VENDOR_NUM": {"type": ["string", "null"]},
                    "VENDOR_NAME": {"type": "string", "required": True},
                    "VENDOR_SITE_ID": {"type": ["integer", "null"]},
                    "VENDOR_SITE_CODE": {"type": ["string", "null"]},
                    "INVOICE_AMOUNT": {"type": "number", "required": True},
                    "INVOICE_CURRENCY_CODE": {"type": "string", "required": True},
                    "EXCHANGE_RATE": {"type": ["number", "null"]},
                    "EXCHANGE_RATE_TYPE": {"type": ["string", "null"]},
                    "EXCHANGE_DATE": {"type": ["string", "null"]},
                    "TERMS_ID": {"type": ["integer", "null"]},
                    "TERMS_NAME": {"type": ["string", "null"]},
                    "DESCRIPTION": {"type": ["string", "null"]},
                    "SOURCE": {"type": ["string", "null"]},
                    "ORG_ID": {"type": ["integer", "null"]},
                    "OPERATING_UNIT": {"type": ["string", "null"]},
                    "PAY_GROUP_LOOKUP_CODE": {"type": ["string", "null"]},
                    "PAYMENT_METHOD_LOOKUP_CODE": {"type": ["string", "null"]},
                    "TAX_AMOUNT": {"type": ["number", "null"]},
                    "TOTAL_TAX_AMOUNT": {"type": ["number", "null"]},
                    "FREIGHT_AMOUNT": {"type": ["number", "null"]},
                    "SELLER": {"type": ["string", "null"]},
                    "SELLER_ADDRESS": {"type": ["string", "null"]},
                    "CLIENT": {"type": ["string", "null"]},
                    "CLIENT_ADDRESS": {"type": ["string", "null"]},
                },
            },
            "AP_INVOICE_LINES_ALL": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["LINE_NUMBER", "AMOUNT"],
                    "properties": {
                        "LINE_NUMBER": {"type": "integer"},
                        "LINE_TYPE_LOOKUP_CODE": {"type": ["string", "null"], "default": "ITEM"},
                        "AMOUNT": {"type": "number"},
                        "QUANTITY_INVOICED": {"type": ["number", "null"]},
                        "UNIT_PRICE": {"type": ["number", "null"]},
                        "UNIT_OF_MEAS_LOOKUP_CODE": {"type": ["string", "null"]},
                        "DESCRIPTION": {"type": ["string", "null"]},
                        "PO_NUMBER": {"type": ["string", "null"]},
                        "PO_LINE_NUMBER": {"type": ["integer", "null"]},
                        "PO_SHIPMENT_NUM": {"type": ["integer", "null"]},
                        "PO_DISTRIBUTION_NUM": {"type": ["integer", "null"]},
                        "ITEM_DESCRIPTION": {"type": ["string", "null"]},
                        "TAX_CODE": {"type": ["string", "null"]},
                        "DIST_CODE_COMBINATION_ID": {"type": ["integer", "null"]},
                        "ACCOUNTING_DATE": {"type": ["string", "null"]},
                        "ATTRIBUTE1": {"type": ["string", "null"]},
                        "ATTRIBUTE2": {"type": ["string", "null"]},
                        "ATTRIBUTE3": {"type": ["string", "null"]},
                        "GROSS_AMOUNT": {"type": ["number", "null"]},
                    },
                },
            },
        },
    },
}
