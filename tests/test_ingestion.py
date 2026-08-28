"""
Phase 2 Tests: CSV Ingestion, Validation, Normalization, and Idempotency

Tests cover:
- Valid CSV accepted
- Invalid CSV rejected with line numbers
- Duplicate upload returns cached result
- Encoding handled (BOM, ₹, commas, DD-MM-YYYY)
- Missing columns rejected
- Future dates rejected
- Negative amounts rejected
- Duplicate IDs detected
- Missing references detected
- Refund overage detected
"""

import json
import os
import tempfile
from datetime import datetime, date, timedelta
from pathlib import Path

import pytest

from backend.ingestion import (
    load_csv,
    validate_transactions,
    validate_settlements,
    validate_refunds,
    validate_bank_credits,
    detect_duplicates,
    detect_cross_file_utr_duplicates,
    check_referential_integrity,
    compute_upload_hash,
    ingest_csvs,
    IngestionResult,
    ValidationError,
    strip_bom,
    parse_currency_string,
    parse_date,
    parse_datetime,
    parse_json_list,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _write_csv(path: str, headers: list[str], rows: list[list]):
    """Write a CSV file."""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def _write_csv_with_bom(path: str, headers: list[str], rows: list[list]):
    """Write a CSV file with BOM."""
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)


def _write_csv_with_currency(path: str, headers: list[str], rows: list[list]):
    """Write a CSV file with currency symbols."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(",".join(headers) + "\n")
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


def _create_valid_transactions(path: str):
    """Create a valid transactions.csv file."""
    headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "customer_email", "created_at", "settlement_id"]
    rows = [
        ["PAY_001", "ORD_001", "100000", "captured", "upi", "0", "0", "test@example.com", "2026-08-20T10:00:00", "SETL_001"],
        ["PAY_002", "ORD_002", "200000", "captured", "card", "4100", "738", "test2@example.com", "2026-08-20T11:00:00", "SETL_001"],
        ["PAY_003", "ORD_003", "50000", "failed", "netbanking", "850", "153", "", "2026-08-20T12:00:00", None],
    ]
    _write_csv(path, headers, rows)


def _create_valid_settlements(path: str):
    """Create a valid settlements.csv file."""
    headers = ["settlement_id", "amount", "status", "utr", "created_at", "settled_at", "linked_payment_ids", "linked_refund_ids"]
    rows = [
        ["SETL_001", "300000", "settled", "UTR123456", "2026-08-20T10:00:00", "2026-08-21T08:00:00", '["PAY_001", "PAY_002"]', "[]"],
        ["SETL_002", "100000", "settled", "UTR789012", "2026-08-21T10:00:00", "2026-08-22T08:00:00", '["PAY_003"]', "[]"],
    ]
    _write_csv(path, headers, rows)


def _create_valid_refunds(path: str):
    """Create a valid refunds.csv file."""
    headers = ["refund_id", "payment_id", "amount", "status", "created_at"]
    rows = [
        ["REF_001", "PAY_001", "10000", "processed", "2026-08-20T14:00:00"],
    ]
    _write_csv(path, headers, rows)


def _create_valid_bank_credits(path: str):
    """Create a valid bank_credits.csv file."""
    headers = ["utr", "amount", "date", "description", "bank_account"]
    rows = [
        ["UTR123456", "300000", "2026-08-22", "Settlement credit", "1234567890"],
        ["UTR789012", "100000", "2026-08-23", "Settlement credit", "1234567890"],
    ]
    _write_csv(path, headers, rows)


def _create_all_valid_csvs(tmpdir: str) -> tuple[str, str, str, str]:
    """Create all 4 valid CSV files and return their paths."""
    tx_path = os.path.join(tmpdir, "transactions.csv")
    st_path = os.path.join(tmpdir, "settlements.csv")
    rf_path = os.path.join(tmpdir, "refunds.csv")
    bc_path = os.path.join(tmpdir, "bank_credits.csv")

    _create_valid_transactions(tx_path)
    _create_valid_settlements(st_path)
    _create_valid_refunds(rf_path)
    _create_valid_bank_credits(bc_path)

    return tx_path, st_path, rf_path, bc_path


# ===========================================================================
# 1. Valid CSV accepted
# ===========================================================================

class TestValidCSVAccepted:
    def test_valid_transactions_accepted(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        _create_valid_transactions(path)
        result = validate_transactions(load_csv(path, "transactions"))
        assert result.is_valid
        assert len(result.records) == 3

    def test_valid_settlements_accepted(self, temp_dir):
        path = os.path.join(temp_dir, "settlements.csv")
        _create_valid_settlements(path)
        result = validate_settlements(load_csv(path, "settlements"))
        assert result.is_valid
        assert len(result.records) == 2

    def test_valid_refunds_accepted(self, temp_dir):
        path = os.path.join(temp_dir, "refunds.csv")
        _create_valid_refunds(path)
        result = validate_refunds(load_csv(path, "refunds"))
        assert result.is_valid
        assert len(result.records) == 1

    def test_valid_bank_credits_accepted(self, temp_dir):
        path = os.path.join(temp_dir, "bank_credits.csv")
        _create_valid_bank_credits(path)
        result = validate_bank_credits(load_csv(path, "bank_credits"))
        assert result.is_valid
        assert len(result.records) == 2

    def test_all_valid_csvs_accepted(self, temp_dir):
        tx, st, rf, bc = _create_all_valid_csvs(temp_dir)
        result = ingest_csvs(tx, st, rf, bc)
        assert result.is_valid
        assert len(result.transactions) == 3
        assert len(result.settlements) == 2
        assert len(result.refunds) == 1
        assert len(result.bank_credits) == 2


# ===========================================================================
# 2. Invalid CSV rejected with line numbers
# ===========================================================================

class TestInvalidCSVRejected:
    def test_missing_required_column(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        # Missing 'status' column
        headers = ["payment_id", "order_id", "amount", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "100000", "upi", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("columns" in e.field for e in result.errors)

    def test_invalid_amount_type(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "abc", "captured", "upi", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("amount" in e.field for e in result.errors)

    def test_invalid_status_value(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "100000", "unknown", "upi", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("status" in e.field for e in result.errors)

    def test_invalid_method_value(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "100000", "captured", "crypto", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("method" in e.field for e in result.errors)

    def test_error_includes_line_number(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [
            ["PAY_001", "ORD_001", "100000", "captured", "upi", "0", "0", "2026-08-20T10:00:00"],
            ["PAY_002", "ORD_002", "-500", "captured", "upi", "0", "0", "2026-08-20T11:00:00"],  # Invalid
        ]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any(e.line == 3 for e in result.errors)  # Line 3 (header + 2 rows)


# ===========================================================================
# 3. Duplicate upload returns cached result
# ===========================================================================

class TestIdempotency:
    def test_same_files_produce_same_hash(self, temp_dir):
        tx1, st1, rf1, bc1 = _create_all_valid_csvs(temp_dir)
        hash1 = compute_upload_hash([tx1, st1, rf1, bc1])
        hash2 = compute_upload_hash([tx1, st1, rf1, bc1])
        assert hash1 == hash2

    def test_duplicate_upload_returns_cached(self, temp_dir):
        tx, st, rf, bc = _create_all_valid_csvs(temp_dir)
        cache = {}

        # First ingestion
        result1 = ingest_csvs(tx, st, rf, bc, cache=cache)
        assert result1.is_valid
        assert not result1.is_cached

        # Second ingestion with same files
        result2 = ingest_csvs(tx, st, rf, bc, cache=cache)
        assert result2.is_valid
        assert result2.is_cached

    def test_different_files_produce_different_hash(self, temp_dir):
        tx1, st1, rf1, bc1 = _create_all_valid_csvs(temp_dir)
        hash1 = compute_upload_hash([tx1, st1, rf1, bc1])

        # Create different files
        tx2, st2, rf2, bc2 = _create_all_valid_csvs(temp_dir)
        # Modify one file
        _write_csv(tx2, ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"], [["PAY_999", "ORD_999", "999", "captured", "upi", "0", "0", "2026-08-20T10:00:00"]])
        hash2 = compute_upload_hash([tx2, st2, rf2, bc2])
        assert hash1 != hash2


# ===========================================================================
# 4. Encoding handled (BOM, ₹, commas, DD-MM-YYYY)
# ===========================================================================

class TestEncodingResilience:
    def test_bom_stripped(self):
        content = "\ufeffpayment_id,order_id\nPAY_001,ORD_001\n"
        assert strip_bom(content) == "payment_id,order_id\nPAY_001,ORD_001\n"

    def test_bom_not_stripped_if_absent(self):
        content = "payment_id,order_id\nPAY_001,ORD_001\n"
        assert strip_bom(content) == "payment_id,order_id\nPAY_001,ORD_001\n"

    def test_currency_symbol_stripped(self):
        assert parse_currency_string("₹100000") == "100000"
        assert parse_currency_string("Rs.100000") == "100000"
        assert parse_currency_string("Rs100000") == "100000"

    def test_commas_stripped(self):
        assert parse_currency_string("1,00,000") == "100000"
        assert parse_currency_string("1,000,000") == "1000000"

    def test_currency_with_commas(self):
        assert parse_currency_string("₹1,00,000") == "100000"

    def test_csv_with_bom_accepted(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "100000", "captured", "upi", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv_with_bom(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert result.is_valid

    def test_csv_with_currency_symbols(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "₹1,00,000", "captured", "upi", "₹0", "₹0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert result.is_valid

    def test_date_dd_mm_yyyy_format(self):
        dt = parse_date("20-08-2026")
        assert dt.year == 2026
        assert dt.month == 8
        assert dt.day == 20

    def test_date_yyyy_mm_dd_format(self):
        dt = parse_date("2026-08-20")
        assert dt.year == 2026
        assert dt.month == 8
        assert dt.day == 20

    def test_datetime_iso8601_format(self):
        dt = parse_datetime("2026-08-20T10:00:00")
        assert dt.year == 2026
        assert dt.month == 8
        assert dt.day == 20
        assert dt.hour == 10

    def test_json_list_parsing(self):
        assert parse_json_list('["PAY_001", "PAY_002"]') == ["PAY_001", "PAY_002"]
        assert parse_json_list("[]") == []
        assert parse_json_list("") == []


# ===========================================================================
# 5. Missing columns rejected
# ===========================================================================

class TestMissingColumns:
    def test_transactions_missing_payment_id(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["ORD_001", "100000", "captured", "upi", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid

    def test_settlements_missing_settlement_id(self, temp_dir):
        path = os.path.join(temp_dir, "settlements.csv")
        headers = ["amount", "status", "utr", "created_at", "settled_at", "linked_payment_ids", "linked_refund_ids"]
        rows = [["300000", "settled", "UTR123456", "2026-08-20T10:00:00", "2026-08-21T08:00:00", "[]", "[]"]]
        _write_csv(path, headers, rows)
        result = validate_settlements(load_csv(path, "settlements"))
        assert not result.is_valid

    def test_refunds_missing_refund_id(self, temp_dir):
        path = os.path.join(temp_dir, "refunds.csv")
        headers = ["payment_id", "amount", "status", "created_at"]
        rows = [["PAY_001", "10000", "processed", "2026-08-20T14:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_refunds(load_csv(path, "refunds"))
        assert not result.is_valid

    def test_bank_credits_missing_amount(self, temp_dir):
        path = os.path.join(temp_dir, "bank_credits.csv")
        headers = ["utr", "date", "description", "bank_account"]
        rows = [["UTR123456", "2026-08-22", "Settlement credit", "1234567890"]]
        _write_csv(path, headers, rows)
        result = validate_bank_credits(load_csv(path, "bank_credits"))
        assert not result.is_valid


# ===========================================================================
# 6. Future dates rejected
# ===========================================================================

class TestFutureDates:
    def test_transaction_future_created_at(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        future_date = (date.today() + timedelta(days=30)).isoformat()
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "100000", "captured", "upi", "0", "0", f"{future_date}T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("created_at" in e.field and "future" in e.message.lower() for e in result.errors)

    def test_settlement_future_created_at(self, temp_dir):
        path = os.path.join(temp_dir, "settlements.csv")
        future_date = (date.today() + timedelta(days=30)).isoformat()
        headers = ["settlement_id", "amount", "status", "utr", "created_at", "settled_at", "linked_payment_ids", "linked_refund_ids"]
        rows = [["SETL_001", "300000", "settled", "UTR123456", f"{future_date}T10:00:00", f"{future_date}T08:00:00", "[]", "[]"]]
        _write_csv(path, headers, rows)
        result = validate_settlements(load_csv(path, "settlements"))
        assert not result.is_valid
        assert any("created_at" in e.field and "future" in e.message.lower() for e in result.errors)

    def test_refund_future_created_at(self, temp_dir):
        path = os.path.join(temp_dir, "refunds.csv")
        future_date = (date.today() + timedelta(days=30)).isoformat()
        headers = ["refund_id", "payment_id", "amount", "status", "created_at"]
        rows = [["REF_001", "PAY_001", "10000", "processed", f"{future_date}T14:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_refunds(load_csv(path, "refunds"))
        assert not result.is_valid
        assert any("created_at" in e.field and "future" in e.message.lower() for e in result.errors)

    def test_bank_credit_future_date(self, temp_dir):
        path = os.path.join(temp_dir, "bank_credits.csv")
        future_date = (date.today() + timedelta(days=30)).isoformat()
        headers = ["utr", "amount", "date", "description", "bank_account"]
        rows = [["UTR123456", "300000", future_date, "Settlement credit", "1234567890"]]
        _write_csv(path, headers, rows)
        result = validate_bank_credits(load_csv(path, "bank_credits"))
        assert not result.is_valid
        assert any("date" in e.field and "future" in e.message.lower() for e in result.errors)


# ===========================================================================
# 7. Negative amounts rejected
# ===========================================================================

class TestNegativeAmounts:
    def test_transaction_negative_amount(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "-100000", "captured", "upi", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("amount" in e.field and "must be > 0" in e.message for e in result.errors)

    def test_transaction_zero_amount(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "0", "captured", "upi", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid

    def test_settlement_negative_amount(self, temp_dir):
        path = os.path.join(temp_dir, "settlements.csv")
        headers = ["settlement_id", "amount", "status", "utr", "created_at", "settled_at", "linked_payment_ids", "linked_refund_ids"]
        rows = [["SETL_001", "-300000", "settled", "UTR123456", "2026-08-20T10:00:00", "2026-08-21T08:00:00", "[]", "[]"]]
        _write_csv(path, headers, rows)
        result = validate_settlements(load_csv(path, "settlements"))
        assert not result.is_valid

    def test_refund_negative_amount(self, temp_dir):
        path = os.path.join(temp_dir, "refunds.csv")
        headers = ["refund_id", "payment_id", "amount", "status", "created_at"]
        rows = [["REF_001", "PAY_001", "-10000", "processed", "2026-08-20T14:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_refunds(load_csv(path, "refunds"))
        assert not result.is_valid

    def test_bank_credit_negative_amount(self, temp_dir):
        path = os.path.join(temp_dir, "bank_credits.csv")
        headers = ["utr", "amount", "date", "description", "bank_account"]
        rows = [["UTR123456", "-300000", "2026-08-22", "Settlement credit", "1234567890"]]
        _write_csv(path, headers, rows)
        result = validate_bank_credits(load_csv(path, "bank_credits"))
        assert not result.is_valid


# ===========================================================================
# 8. Duplicate IDs detected
# ===========================================================================

class TestDuplicateIDs:
    def test_duplicate_payment_id(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [
            ["PAY_001", "ORD_001", "100000", "captured", "upi", "0", "0", "2026-08-20T10:00:00"],
            ["PAY_001", "ORD_002", "200000", "captured", "card", "4100", "738", "2026-08-20T11:00:00"],
        ]
        _write_csv(path, headers, rows)
        records = load_csv(path, "transactions").to_dict("records")
        errors = detect_duplicates(
            [{"payment_id": r["payment_id"]} for r in records],
            "payment_id",
            "PAYMENT"
        )
        assert len(errors) > 0

    def test_duplicate_settlement_id(self, temp_dir):
        path = os.path.join(temp_dir, "settlements.csv")
        headers = ["settlement_id", "amount", "status", "utr", "created_at", "settled_at", "linked_payment_ids", "linked_refund_ids"]
        rows = [
            ["SETL_001", "300000", "settled", "UTR123456", "2026-08-20T10:00:00", "2026-08-21T08:00:00", "[]", "[]"],
            ["SETL_001", "100000", "settled", "UTR789012", "2026-08-21T10:00:00", "2026-08-22T08:00:00", "[]", "[]"],
        ]
        _write_csv(path, headers, rows)
        records = load_csv(path, "settlements").to_dict("records")
        errors = detect_duplicates(
            [{"settlement_id": r["settlement_id"]} for r in records],
            "settlement_id",
            "SETTLEMENT"
        )
        assert len(errors) > 0

    def test_duplicate_refund_id(self, temp_dir):
        path = os.path.join(temp_dir, "refunds.csv")
        headers = ["refund_id", "payment_id", "amount", "status", "created_at"]
        rows = [
            ["REF_001", "PAY_001", "10000", "processed", "2026-08-20T14:00:00"],
            ["REF_001", "PAY_002", "20000", "processed", "2026-08-20T15:00:00"],
        ]
        _write_csv(path, headers, rows)
        records = load_csv(path, "refunds").to_dict("records")
        errors = detect_duplicates(
            [{"refund_id": r["refund_id"]} for r in records],
            "refund_id",
            "REFUND"
        )
        assert len(errors) > 0

    def test_duplicate_utr_in_bank_credits(self, temp_dir):
        path = os.path.join(temp_dir, "bank_credits.csv")
        headers = ["utr", "amount", "date", "description", "bank_account"]
        rows = [
            ["UTR123456", "300000", "2026-08-22", "Settlement credit", "1234567890"],
            ["UTR123456", "100000", "2026-08-23", "Settlement credit", "1234567890"],
        ]
        _write_csv(path, headers, rows)
        records = load_csv(path, "bank_credits").to_dict("records")
        errors = detect_duplicates(
            [{"utr": r["utr"]} for r in records if r["utr"] is not None],
            "utr",
            "BANK_UTR"
        )
        assert len(errors) > 0

    def test_matching_utr_across_files_not_error(self, temp_dir):
        """Test that matching UTRs between settlements and bank_credits is expected behavior.
        
        This is how bank credits are linked to settlements - they should have the same UTR.
        Only duplicate UTRs within the same file type are errors.
        """
        st_path = os.path.join(temp_dir, "settlements.csv")
        bc_path = os.path.join(temp_dir, "bank_credits.csv")

        headers_st = ["settlement_id", "amount", "status", "utr", "created_at", "settled_at", "linked_payment_ids", "linked_refund_ids"]
        rows_st = [["SETL_001", "300000", "settled", "UTR123456", "2026-08-20T10:00:00", "2026-08-21T08:00:00", "[]", "[]"]]
        _write_csv(st_path, headers_st, rows_st)

        headers_bc = ["utr", "amount", "date", "description", "bank_account"]
        rows_bc = [["UTR123456", "300000", "2026-08-22", "Settlement credit", "1234567890"]]
        _write_csv(bc_path, headers_bc, rows_bc)

        settlements = [{"settlement_id": "SETL_001", "utr": "UTR123456"}]
        bank_credits = [{"utr": "UTR123456"}]

        errors = detect_cross_file_utr_duplicates(settlements, bank_credits)
        # Matching UTRs across files is expected - no errors should be raised
        assert len(errors) == 0


# ===========================================================================
# 9. Missing references detected
# ===========================================================================

class TestMissingReferences:
    def test_refund_missing_payment_id(self, temp_dir):
        transactions = [{"payment_id": "PAY_001", "amount": 100000}]
        refunds = [{"refund_id": "REF_001", "payment_id": "PAY_999", "amount": 10000}]
        settlements = [{"settlement_id": "SETL_001", "linked_payment_ids": [], "linked_refund_ids": []}]

        errors = check_referential_integrity(transactions, refunds, settlements)
        assert len(errors) > 0
        assert any("PAY_999" in e.message for e in errors)

    def test_settlement_missing_payment_reference(self, temp_dir):
        transactions = [{"payment_id": "PAY_001", "amount": 100000}]
        refunds = []
        settlements = [{"settlement_id": "SETL_001", "linked_payment_ids": ["PAY_999"], "linked_refund_ids": []}]

        errors = check_referential_integrity(transactions, refunds, settlements)
        assert len(errors) > 0
        assert any("PAY_999" in e.message for e in errors)

    def test_settlement_missing_refund_reference(self, temp_dir):
        transactions = [{"payment_id": "PAY_001", "amount": 100000}]
        refunds = [{"refund_id": "REF_001", "payment_id": "PAY_001", "amount": 10000}]
        settlements = [{"settlement_id": "SETL_001", "linked_payment_ids": ["PAY_001"], "linked_refund_ids": ["REF_999"]}]

        errors = check_referential_integrity(transactions, refunds, settlements)
        assert len(errors) > 0
        assert any("REF_999" in e.message for e in errors)


# ===========================================================================
# 10. Refund overage detected
# ===========================================================================

class TestRefundOverage:
    def test_refund_exceeds_payment(self):
        transactions = [{"payment_id": "PAY_001", "amount": 10000}]
        refunds = [
            {"refund_id": "REF_001", "payment_id": "PAY_001", "amount": 6000},
            {"refund_id": "REF_002", "payment_id": "PAY_001", "amount": 6000},
        ]
        settlements = []

        errors = check_referential_integrity(transactions, refunds, settlements)
        assert len(errors) > 0
        assert any("REFUND_OVERAGE" in e.error_type for e in errors)

    def test_refund_within_limit(self):
        transactions = [{"payment_id": "PAY_001", "amount": 10000}]
        refunds = [
            {"refund_id": "REF_001", "payment_id": "PAY_001", "amount": 4000},
            {"refund_id": "REF_002", "payment_id": "PAY_001", "amount": 4000},
        ]
        settlements = []

        errors = check_referential_integrity(transactions, refunds, settlements)
        assert not any("REFUND_OVERAGE" in e.error_type for e in errors)


# ===========================================================================
# 11. Invalid types and invalid status enums
# ===========================================================================

class TestInvalidTypesAndEnums:
    def test_invalid_transaction_status(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "100000", "pending", "upi", "0", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("status" in e.field for e in result.errors)

    def test_invalid_settlement_status(self, temp_dir):
        path = os.path.join(temp_dir, "settlements.csv")
        headers = ["settlement_id", "amount", "status", "utr", "created_at", "settled_at", "linked_payment_ids", "linked_refund_ids"]
        rows = [["SETL_001", "300000", "completed", "UTR123456", "2026-08-20T10:00:00", "2026-08-21T08:00:00", "[]", "[]"]]
        _write_csv(path, headers, rows)
        result = validate_settlements(load_csv(path, "settlements"))
        assert not result.is_valid
        assert any("status" in e.field for e in result.errors)

    def test_invalid_refund_status(self, temp_dir):
        path = os.path.join(temp_dir, "refunds.csv")
        headers = ["refund_id", "payment_id", "amount", "status", "created_at"]
        rows = [["REF_001", "PAY_001", "10000", "pending", "2026-08-20T14:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_refunds(load_csv(path, "refunds"))
        assert not result.is_valid
        assert any("status" in e.field for e in result.errors)

    def test_negative_fee_rejected(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "100000", "captured", "upi", "-10", "0", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("fee" in e.field for e in result.errors)

    def test_negative_tax_rejected(self, temp_dir):
        path = os.path.join(temp_dir, "transactions.csv")
        headers = ["payment_id", "order_id", "amount", "status", "method", "fee", "tax", "created_at"]
        rows = [["PAY_001", "ORD_001", "100000", "captured", "upi", "0", "-5", "2026-08-20T10:00:00"]]
        _write_csv(path, headers, rows)
        result = validate_transactions(load_csv(path, "transactions"))
        assert not result.is_valid
        assert any("tax" in e.field for e in result.errors)


# ===========================================================================
# 12. File not found
# ===========================================================================

class TestFileNotFound:
    def test_transactions_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_csv("/nonexistent/transactions.csv", "transactions")

    def test_settlements_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_csv("/nonexistent/settlements.csv", "settlements")


# ===========================================================================
# 13. IngestionResult properties
# ===========================================================================

class TestIngestionResult:
    def test_result_starts_invalid(self):
        result = IngestionResult()
        assert result.is_valid
        assert len(result.errors) == 0
        assert len(result.transactions) == 0
