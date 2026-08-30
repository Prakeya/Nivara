"""
CSV schema versioning: tracks expected columns and types per CSV file version.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"

CSV_SCHEMAS = {
    "transactions": {
        "version": "1.0",
        "required_columns": [
            {"name": "payment_id", "type": "string"},
            {"name": "order_id", "type": "string"},
            {"name": "amount", "type": "integer"},
            {"name": "status", "type": "string", "enum": ["captured", "failed", "pending", "refunded"]},
            {"name": "method", "type": "string", "enum": ["upi", "card", "netbanking", "wallet", "emi", "paylater"]},
            {"name": "fee", "type": "integer"},
            {"name": "tax", "type": "integer"},
            {"name": "created_at", "type": "datetime"},
        ],
    },
    "settlements": {
        "version": "1.0",
        "required_columns": [
            {"name": "settlement_id", "type": "string"},
            {"name": "amount", "type": "integer"},
            {"name": "utr", "type": "string"},
            {"name": "status", "type": "string", "enum": ["settled", "pending", "failed"]},
            {"name": "created_at", "type": "datetime"},
            {"name": "settled_at", "type": "datetime"},
        ],
    },
    "refunds": {
        "version": "1.0",
        "required_columns": [
            {"name": "refund_id", "type": "string"},
            {"name": "payment_id", "type": "string"},
            {"name": "amount", "type": "integer"},
            {"name": "status", "type": "string", "enum": ["processed", "pending", "failed"]},
            {"name": "created_at", "type": "datetime"},
        ],
    },
    "bank_credits": {
        "version": "1.0",
        "required_columns": [
            {"name": "utr", "type": "string"},
            {"name": "amount", "type": "integer"},
            {"name": "credited_at", "type": "datetime"},
            {"name": "bank", "type": "string"},
        ],
    },
}


def validate_schema(csv_name: str, columns: list[str]) -> dict[str, Any]:
    """Validate CSV columns against expected schema."""
    schema = CSV_SCHEMAS.get(csv_name)
    if not schema:
        return {"valid": False, "error": f"Unknown CSV type: {csv_name}"}

    required = {c["name"] for c in schema["required_columns"]}
    provided = set(columns)

    missing = required - provided
    extra = provided - required

    return {
        "valid": len(missing) == 0,
        "schema_version": schema["version"],
        "missing_columns": list(missing),
        "extra_columns": list(extra),
    }
