"""
Phase 2: CSV Ingestion, Validation, Normalization, and Idempotency

Handles loading and validating the 4 CSV sources:
- transactions.csv
- settlements.csv
- refunds.csv
- bank_credits.csv
"""

import hashlib
import json
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd  # type: ignore[import-untyped]

from backend.models import (
    Transaction,
    Settlement,
    Refund,
    BankCredit,
    TransactionStatus,
    PaymentMethod,
    SettlementStatus,
    RefundStatus,
)


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

TRANSACTION_COLUMNS = {
    "payment_id": str,
    "order_id": str,
    "amount": int,
    "status": str,
    "method": str,
    "fee": int,
    "tax": int,
    "customer_email": str,
    "created_at": str,
    "settlement_id": str,
}

TRANSACTION_REQUIRED = {"payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"}

SETTLEMENT_COLUMNS = {
    "settlement_id": str,
    "amount": int,
    "status": str,
    "utr": str,
    "created_at": str,
    "settled_at": str,
    "linked_payment_ids": str,
    "linked_refund_ids": str,
}

SETTLEMENT_REQUIRED = {"settlement_id", "amount", "status", "utr", "created_at", "settled_at", "linked_payment_ids", "linked_refund_ids"}

REFUND_COLUMNS = {
    "refund_id": str,
    "payment_id": str,
    "amount": int,
    "status": str,
    "created_at": str,
}

REFUND_REQUIRED = {"refund_id", "payment_id", "amount", "status", "created_at"}

BANK_CREDIT_COLUMNS = {
    "utr": str,
    "amount": int,
    "date": str,
    "description": str,
    "bank_account": str,
}

BANK_CREDIT_REQUIRED = {"amount", "date"}


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def strip_bom(text: str) -> str:
    """Strip BOM from file content."""
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def parse_currency_string(value: str) -> str:
    """Strip currency symbols and commas from amount strings."""
    result = value.strip()
    result = result.replace("₹", "").replace("Rs.", "").replace("Rs", "")
    result = result.replace(",", "")
    return result.strip()


def parse_date(value: str) -> datetime:
    """Parse date in DD-MM-YYYY or YYYY-MM-DD format, normalize to datetime."""
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Invalid date format: {value}. Expected DD-MM-YYYY or YYYY-MM-DD")


def parse_datetime(value: str) -> datetime:
    """Parse datetime in ISO 8601 format."""
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid datetime format: {value}")


def parse_json_list(value: str) -> list[str]:
    """Parse a JSON array string into a list of strings."""
    if not value or value.strip() == "":
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        return []
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON array: {value}")


def sanitize_csv_field(value: Optional[str]) -> Optional[str]:
    """Sanitize a string field against CSV formula injection.

    Strips leading characters that trigger formula execution in spreadsheet apps
    (=, +, -, @, |, \\t, \\r). Prefixes with a single quote if needed.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped and stripped[0] in ("=", "+", "-", "@", "|", "\t", "\r"):
        return "'" + value
    return value


# ---------------------------------------------------------------------------
# Validation error types
# ---------------------------------------------------------------------------

class ValidationError:
    """Represents a validation error with line number context."""
    def __init__(self, line: int, field: str, message: str, error_type: str = "VALIDATION_ERROR"):
        self.line = line
        self.field = field
        self.message = message
        self.error_type = error_type

    def __repr__(self) -> str:
        return f"Line {self.line}: [{self.error_type}] {self.field}: {self.message}"


class ValidationResult:
    """Result of CSV validation."""
    def __init__(self) -> None:
        self.errors: list[ValidationError] = []
        self.records: list[dict[str, Any]] = []

    def add_error(self, line: int, field: str, message: str, error_type: str = "VALIDATION_ERROR") -> None:
        self.errors.append(ValidationError(line, field, message, error_type))

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# Core ingestion
# ---------------------------------------------------------------------------

def load_csv(file_path: str, file_type: str) -> pd.DataFrame:
    """
    Load a CSV file with encoding resilience.

    - Strips BOM from file start
    - Strips currency symbols and commas from amount columns
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    content = strip_bom(content)

    from io import StringIO
    df = pd.read_csv(StringIO(content))

    # Strip BOM from column names
    df.columns = [col.strip().lstrip("\ufeff") for col in df.columns]

    # Clean amount columns based on file type
    amount_columns = _get_amount_columns(file_type)
    for col in amount_columns:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: parse_currency_string(str(x)) if pd.notna(x) else x)

    return df


def _get_amount_columns(file_type: str) -> list[str]:
    """Get amount columns for each file type."""
    mapping = {
        "transactions": ["amount", "fee", "tax"],
        "settlements": ["amount"],
        "refunds": ["amount"],
        "bank_credits": ["amount"],
    }
    return mapping.get(file_type, [])


def validate_transactions(df: pd.DataFrame, start_line: int = 2) -> ValidationResult:
    """Validate transactions DataFrame."""
    result = ValidationResult()

    # Check required columns
    missing = TRANSACTION_REQUIRED - set(df.columns)
    if missing:
        result.add_error(start_line, "columns", f"Missing required columns: {missing}", "MISSING_COLUMN")
        return result

    for i, (_, row) in enumerate(df.iterrows()):
        line = start_line + i
        try:
            # Validate payment_id
            if pd.isna(row["payment_id"]) or str(row["payment_id"]).strip() == "":
                result.add_error(line, "payment_id", "payment_id is required", "REQUIRED_FIELD")
                continue

            # Validate amount
            try:
                amount = int(float(row["amount"]))
                if amount <= 0:
                    result.add_error(line, "amount", f"amount must be > 0, got {amount}", "INVALID_AMOUNT")
                    continue
            except (ValueError, TypeError):
                result.add_error(line, "amount", f"Invalid amount value: {row['amount']}", "INVALID_TYPE")
                continue

            # Validate status
            try:
                TransactionStatus(str(row["status"]).lower())
            except ValueError:
                result.add_error(line, "status", f"Invalid status: {row['status']}. Must be 'captured' or 'failed'", "INVALID_STATUS")
                continue

            # Validate method
            try:
                PaymentMethod(str(row["method"]).lower())
            except ValueError:
                result.add_error(line, "method", f"Invalid method: {row['method']}. Must be 'upi', 'card', or 'netbanking'", "INVALID_METHOD")
                continue

            # Validate fee
            try:
                fee = int(float(row["fee"]))
                if fee < 0:
                    result.add_error(line, "fee", f"fee must be >= 0, got {fee}", "INVALID_FEE")
                    continue
            except (ValueError, TypeError):
                result.add_error(line, "fee", f"Invalid fee value: {row['fee']}", "INVALID_TYPE")
                continue

            # Validate tax
            try:
                tax = int(float(row["tax"]))
                if tax < 0:
                    result.add_error(line, "tax", f"tax must be >= 0, got {tax}", "INVALID_TAX")
                    continue
            except (ValueError, TypeError):
                result.add_error(line, "tax", f"Invalid tax value: {row['tax']}", "INVALID_TYPE")
                continue

            # Validate created_at
            try:
                created_at = parse_datetime(str(row["created_at"]))
                if created_at.date() > date.today():
                    result.add_error(line, "created_at", f"Date cannot be in the future: {row['created_at']}", "FUTURE_DATE")
                    continue
            except ValueError as e:
                result.add_error(line, "created_at", str(e), "INVALID_DATE")
                continue

            # Record is valid
            result.records.append({
                "payment_id": str(row["payment_id"]),
                "order_id": str(row["order_id"]),
                "amount": amount,
                "status": str(row["status"]).lower(),
                "method": str(row["method"]).lower(),
                "fee": fee,
                "tax": tax,
                "customer_email": sanitize_csv_field(str(row.get("customer_email", "")) if pd.notna(row.get("customer_email")) else None),
                "created_at": created_at,
                "settlement_id": str(row["settlement_id"]) if pd.notna(row.get("settlement_id")) else None,
            })

        except Exception as e:
            result.add_error(line, "general", f"Unexpected error: {str(e)}", "UNEXPECTED_ERROR")

    return result


def validate_settlements(df: pd.DataFrame, start_line: int = 2) -> ValidationResult:
    """Validate settlements DataFrame."""
    result = ValidationResult()

    # Check required columns
    missing = SETTLEMENT_REQUIRED - set(df.columns)
    if missing:
        result.add_error(start_line, "columns", f"Missing required columns: {missing}", "MISSING_COLUMN")
        return result

    for i, (_, row) in enumerate(df.iterrows()):
        line = start_line + i
        try:
            # Validate settlement_id
            if pd.isna(row["settlement_id"]) or str(row["settlement_id"]).strip() == "":
                result.add_error(line, "settlement_id", "settlement_id is required", "REQUIRED_FIELD")
                continue

            # Validate amount
            try:
                amount = int(float(row["amount"]))
                if amount <= 0:
                    result.add_error(line, "amount", f"amount must be > 0, got {amount}", "INVALID_AMOUNT")
                    continue
            except (ValueError, TypeError):
                result.add_error(line, "amount", f"Invalid amount value: {row['amount']}", "INVALID_TYPE")
                continue

            # Validate status
            try:
                SettlementStatus(str(row["status"]).lower())
            except ValueError:
                result.add_error(line, "status", f"Invalid status: {row['status']}. Must be 'settled' or 'pending'", "INVALID_STATUS")
                continue

            # Validate utr
            if pd.isna(row["utr"]) or str(row["utr"]).strip() == "":
                result.add_error(line, "utr", "utr is required", "REQUIRED_FIELD")
                continue

            # Validate created_at
            try:
                created_at = parse_datetime(str(row["created_at"]))
                if created_at.date() > date.today():
                    result.add_error(line, "created_at", f"Date cannot be in the future: {row['created_at']}", "FUTURE_DATE")
                    continue
            except ValueError as e:
                result.add_error(line, "created_at", str(e), "INVALID_DATE")
                continue

            # Validate settled_at
            try:
                settled_at = parse_datetime(str(row["settled_at"]))
                if settled_at.date() > date.today():
                    result.add_error(line, "settled_at", f"Date cannot be in the future: {row['settled_at']}", "FUTURE_DATE")
                    continue
                if settled_at < created_at:
                    result.add_error(line, "settled_at", "settled_at must be >= created_at", "INVALID_DATE")
                    continue
            except ValueError as e:
                result.add_error(line, "settled_at", str(e), "INVALID_DATE")
                continue

            # Validate linked_payment_ids
            try:
                linked_payment_ids = parse_json_list(str(row["linked_payment_ids"]))
            except ValueError as e:
                result.add_error(line, "linked_payment_ids", str(e), "INVALID_JSON")
                continue

            # Validate linked_refund_ids
            try:
                linked_refund_ids = parse_json_list(str(row["linked_refund_ids"]))
            except ValueError as e:
                result.add_error(line, "linked_refund_ids", str(e), "INVALID_JSON")
                continue

            # Record is valid
            result.records.append({
                "settlement_id": str(row["settlement_id"]),
                "amount": amount,
                "status": str(row["status"]).lower(),
                "utr": str(row["utr"]),
                "created_at": created_at,
                "settled_at": settled_at,
                "linked_payment_ids": linked_payment_ids,
                "linked_refund_ids": linked_refund_ids,
            })

        except Exception as e:
            result.add_error(line, "general", f"Unexpected error: {str(e)}", "UNEXPECTED_ERROR")

    return result


def validate_refunds(df: pd.DataFrame, start_line: int = 2) -> ValidationResult:
    """Validate refunds DataFrame."""
    result = ValidationResult()

    # Check required columns
    missing = REFUND_REQUIRED - set(df.columns)
    if missing:
        result.add_error(start_line, "columns", f"Missing required columns: {missing}", "MISSING_COLUMN")
        return result

    for i, (_, row) in enumerate(df.iterrows()):
        line = start_line + i
        try:
            # Validate refund_id
            if pd.isna(row["refund_id"]) or str(row["refund_id"]).strip() == "":
                result.add_error(line, "refund_id", "refund_id is required", "REQUIRED_FIELD")
                continue

            # Validate payment_id
            if pd.isna(row["payment_id"]) or str(row["payment_id"]).strip() == "":
                result.add_error(line, "payment_id", "payment_id is required", "REQUIRED_FIELD")
                continue

            # Validate amount
            try:
                amount = int(float(row["amount"]))
                if amount <= 0:
                    result.add_error(line, "amount", f"amount must be > 0, got {amount}", "INVALID_AMOUNT")
                    continue
            except (ValueError, TypeError):
                result.add_error(line, "amount", f"Invalid amount value: {row['amount']}", "INVALID_TYPE")
                continue

            # Validate status
            try:
                RefundStatus(str(row["status"]).lower())
            except ValueError:
                result.add_error(line, "status", f"Invalid status: {row['status']}. Must be 'processed'", "INVALID_STATUS")
                continue

            # Validate created_at
            try:
                created_at = parse_datetime(str(row["created_at"]))
                if created_at.date() > date.today():
                    result.add_error(line, "created_at", f"Date cannot be in the future: {row['created_at']}", "FUTURE_DATE")
                    continue
            except ValueError as e:
                result.add_error(line, "created_at", str(e), "INVALID_DATE")
                continue

            # Record is valid
            result.records.append({
                "refund_id": str(row["refund_id"]),
                "payment_id": str(row["payment_id"]),
                "amount": amount,
                "status": str(row["status"]).lower(),
                "created_at": created_at,
            })

        except Exception as e:
            result.add_error(line, "general", f"Unexpected error: {str(e)}", "UNEXPECTED_ERROR")

    return result


def validate_bank_credits(df: pd.DataFrame, start_line: int = 2) -> ValidationResult:
    """Validate bank_credits DataFrame."""
    result = ValidationResult()

    # Check required columns
    missing = BANK_CREDIT_REQUIRED - set(df.columns)
    if missing:
        result.add_error(start_line, "columns", f"Missing required columns: {missing}", "MISSING_COLUMN")
        return result

    for i, (_, row) in enumerate(df.iterrows()):
        line = start_line + i
        try:
            # Validate amount
            try:
                amount = int(float(row["amount"]))
                if amount <= 0:
                    result.add_error(line, "amount", f"amount must be > 0, got {amount}", "INVALID_AMOUNT")
                    continue
            except (ValueError, TypeError):
                result.add_error(line, "amount", f"Invalid amount value: {row['amount']}", "INVALID_TYPE")
                continue

            # Validate date
            try:
                bank_date = parse_date(str(row["date"]))
                if bank_date.date() > date.today():
                    result.add_error(line, "date", f"Date cannot be in the future: {row['date']}", "FUTURE_DATE")
                    continue
            except ValueError as e:
                result.add_error(line, "date", str(e), "INVALID_DATE")
                continue

            # Record is valid
            result.records.append({
                "utr": str(row["utr"]) if pd.notna(row.get("utr")) else None,
                "amount": amount,
                "date": bank_date.date(),
                "description": sanitize_csv_field(str(row.get("description", "")) if pd.notna(row.get("description")) else None),
                "bank_account": str(row.get("bank_account", "")) if pd.notna(row.get("bank_account")) else None,
            })

        except Exception as e:
            result.add_error(line, "general", f"Unexpected error: {str(e)}", "UNEXPECTED_ERROR")

    return result


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def detect_duplicates(records: list[dict[str, Any]], key_field: str, file_type: str, start_line: int = 2) -> list[ValidationError]:
    """Detect duplicate IDs within a file."""
    errors = []
    seen: dict[Any, int] = {}
    for i, record in enumerate(records):
        key = record.get(key_field)
        if key in seen:
            original_line = seen[key]
            errors.append(ValidationError(
                line=start_line + i,
                field=key_field,
                message=f"Duplicate {key_field}: {key} (first seen at line {original_line})",
                error_type=f"DUPLICATE_{file_type.upper()}"
            ))
        else:
            seen[key] = start_line + i
    return errors


def detect_cross_file_utr_duplicates(
    settlements: list[dict[str, Any]],
    bank_credits: list[dict[str, Any]],
    settlement_start_line: int = 2,
    bank_start_line: int = 2,
) -> list[ValidationError]:
    """
    Detect duplicate UTRs within settlements and within bank_credits.
    
    Note: Matching UTRs between settlements and bank_credits is expected behavior
    (that's how bank credits are linked to settlements). Only duplicates within
    the same file type are errors.
    """
    errors = []

    # Index UTRs from settlements - check for duplicates within settlements
    settlement_utrs = {}
    for i, s in enumerate(settlements):
        utr = s.get("utr")
        if utr:
            if utr in settlement_utrs:
                errors.append(ValidationError(
                    line=settlement_start_line + i,
                    field="utr",
                    message=f"Duplicate UTR in settlements: {utr}",
                    error_type="DUPLICATE_UTR"
                ))
            else:
                settlement_utrs[utr] = settlement_start_line + i

    # Index UTRs from bank_credits - check for duplicates within bank_credits
    bank_utrs = {}
    for i, b in enumerate(bank_credits):
        utr = b.get("utr")
        if utr:
            if utr in bank_utrs:
                errors.append(ValidationError(
                    line=bank_start_line + i,
                    field="utr",
                    message=f"Duplicate UTR in bank_credits: {utr}",
                    error_type="DUPLICATE_BANK_UTR"
                ))
            else:
                bank_utrs[utr] = bank_start_line + i

    return errors


# ---------------------------------------------------------------------------
# Referential integrity
# ---------------------------------------------------------------------------

def check_referential_integrity(
    transactions: list[dict[str, Any]],
    refunds: list[dict[str, Any]],
    settlements: list[dict[str, Any]],
    transaction_start_line: int = 2,
    refund_start_line: int = 2,
    settlement_start_line: int = 2,
) -> list[ValidationError]:
    """Check referential integrity across CSVs."""
    errors = []

    # Build transaction index
    payment_ids = {t["payment_id"] for t in transactions}
    settlement_ids = {s["settlement_id"] for s in settlements}
    refund_ids = {r["refund_id"] for r in refunds}

    # Check refunds reference valid transactions
    for i, r in enumerate(refunds):
        if r["payment_id"] not in payment_ids:
            errors.append(ValidationError(
                line=refund_start_line + i,
                field="payment_id",
                message=f"refund.payment_id '{r['payment_id']}' does not exist in transactions",
                error_type="MISSING_REFERENCE"
            ))

    # Check settlements linked_payment_ids reference valid transactions
    for i, s in enumerate(settlements):
        for pid in s["linked_payment_ids"]:
            if pid not in payment_ids:
                errors.append(ValidationError(
                    line=settlement_start_line + i,
                    field="linked_payment_ids",
                    message=f"settlement.linked_payment_ids '{pid}' does not exist in transactions",
                    error_type="MISSING_REFERENCE"
                ))

    # Check settlements linked_refund_ids reference valid refunds
    for i, s in enumerate(settlements):
        for rid in s["linked_refund_ids"]:
            if rid not in refund_ids:
                errors.append(ValidationError(
                    line=settlement_start_line + i,
                    field="linked_refund_ids",
                    message=f"settlement.linked_refund_ids '{rid}' does not exist in refunds",
                    error_type="MISSING_REFERENCE"
                ))

    # Check refund overage: sum of refunds for a payment > payment amount
    payment_amounts = {t["payment_id"]: t["amount"] for t in transactions}
    refund_totals: dict[str, int] = {}
    for r in refunds:
        pid = r["payment_id"]
        refund_totals[pid] = refund_totals.get(pid, 0) + r["amount"]

    for pid, total_refund in refund_totals.items():
        if pid in payment_amounts and total_refund > payment_amounts[pid]:
            errors.append(ValidationError(
                line=0,
                field="amount",
                message=f"REFUND_OVERAGE: Sum of refunds ({total_refund}) for payment {pid} exceeds payment amount ({payment_amounts[pid]})",
                error_type="REFUND_OVERAGE"
            ))

    return errors


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def compute_upload_hash(file_paths: list[str]) -> str:
    """
    Compute a hash for the uploaded files for idempotency.
    
    From architecture.md:
    - Sort file paths
    - Sort each CSV by its first column
    - Convert to CSV string
    - Hash with SHA-256
    """
    contents = []
    for path in sorted(file_paths):
        df = pd.read_csv(path)
        df = df.sort_values(by=df.columns[0])
        contents.append(df.to_csv(index=False))
    normalized = "".join(contents).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


# ---------------------------------------------------------------------------
# Main ingestion function
# ---------------------------------------------------------------------------

class IngestionResult:
    """Result of ingesting all 4 CSV files."""
    def __init__(self) -> None:
        self.transactions: list[dict[str, Any]] = []
        self.settlements: list[dict[str, Any]] = []
        self.refunds: list[dict[str, Any]] = []
        self.bank_credits: list[dict[str, Any]] = []
        self.errors: list[ValidationError] = []
        self.upload_hash: Optional[str] = None
        self.is_cached: bool = False

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def ingest_csvs(
    transactions_path: str,
    settlements_path: str,
    refunds_path: str,
    bank_credits_path: str,
    cache: Optional[dict[str, IngestionResult]] = None,
) -> IngestionResult:
    """
    Ingest and validate all 4 CSV files.

    Returns an IngestionResult with parsed records or validation errors.
    If cache is provided and a matching hash exists, returns cached result.
    """
    result = IngestionResult()

    # Compute upload hash for idempotency
    file_paths = [transactions_path, settlements_path, refunds_path, bank_credits_path]
    result.upload_hash = compute_upload_hash(file_paths)

    # Check cache
    if cache and result.upload_hash in cache:
        cached = cache[result.upload_hash]
        result.is_cached = True
        result.transactions = cached.transactions
        result.settlements = cached.settlements
        result.refunds = cached.refunds
        result.bank_credits = cached.bank_credits
        return result

    # Load and validate transactions
    try:
        tx_df = load_csv(transactions_path, "transactions")
        tx_result = validate_transactions(tx_df)
        result.errors.extend(tx_result.errors)
        result.transactions = tx_result.records

        # Check for duplicate payment_ids
        dup_errors = detect_duplicates(result.transactions, "payment_id", "PAYMENT")
        result.errors.extend(dup_errors)

    except Exception as e:
        result.errors.append(ValidationError(0, "file", f"Error loading transactions: {str(e)}", "FILE_ERROR"))

    # Load and validate settlements
    try:
        st_df = load_csv(settlements_path, "settlements")
        st_result = validate_settlements(st_df)
        result.errors.extend(st_result.errors)
        result.settlements = st_result.records

        # Check for duplicate settlement_ids
        dup_errors = detect_duplicates(result.settlements, "settlement_id", "SETTLEMENT")
        result.errors.extend(dup_errors)

    except Exception as e:
        result.errors.append(ValidationError(0, "file", f"Error loading settlements: {str(e)}", "FILE_ERROR"))

    # Load and validate refunds
    try:
        rf_df = load_csv(refunds_path, "refunds")
        rf_result = validate_refunds(rf_df)
        result.errors.extend(rf_result.errors)
        result.refunds = rf_result.records

        # Check for duplicate refund_ids
        dup_errors = detect_duplicates(result.refunds, "refund_id", "REFUND")
        result.errors.extend(dup_errors)

    except Exception as e:
        result.errors.append(ValidationError(0, "file", f"Error loading refunds: {str(e)}", "FILE_ERROR"))

    # Load and validate bank credits
    try:
        bc_df = load_csv(bank_credits_path, "bank_credits")
        bc_result = validate_bank_credits(bc_df)
        result.errors.extend(bc_result.errors)
        result.bank_credits = bc_result.records

        # Check for duplicate UTRs in bank credits
        utr_errors = detect_duplicates(
            [r for r in result.bank_credits if r["utr"] is not None],
            "utr",
            "BANK_UTR"
        )
        result.errors.extend(utr_errors)

    except Exception as e:
        result.errors.append(ValidationError(0, "file", f"Error loading bank_credits: {str(e)}", "FILE_ERROR"))

    # Cross-file checks
    if not result.errors or any(e.error_type in ("MISSING_REFERENCE", "REFUND_OVERAGE") for e in result.errors):
        # Only run referential integrity if no critical errors
        ref_errors = check_referential_integrity(
            result.transactions,
            result.refunds,
            result.settlements,
        )
        result.errors.extend(ref_errors)

    # Cross-file UTR duplicates
    utr_errors = detect_cross_file_utr_duplicates(result.settlements, result.bank_credits)
    result.errors.extend(utr_errors)

    # Store in cache
    if cache is not None and result.is_valid:
        cache[result.upload_hash] = result

    return result
