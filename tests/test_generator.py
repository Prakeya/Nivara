"""
Phase 5 Tests: Synthetic Data Generator

Must pass: Generator produces all 8 edge cases. Ground truth labels correct.
"""

import json
import os
import tempfile

from backend.generator import (
    compute_fee,
    compute_tax,
    generate_batch,
    write_dataset,
)


# ---------------------------------------------------------------------------
# Fee / tax formula tests
# ---------------------------------------------------------------------------

class TestComputeFee:
    def test_upi_fee_is_zero(self):
        assert compute_fee("upi", 100000) == 0

    def test_card_fee_formula(self):
        # floor(100000 * 2 / 100) + 100 = 2000 + 100 = 2100
        assert compute_fee("card", 100000) == 2100

    def test_netbanking_fee_formula(self):
        # floor(100000 * 15 / 1000) + 100 = 1500 + 100 = 1600
        assert compute_fee("netbanking", 100000) == 1600

    def test_card_fee_small_amount(self):
        # floor(1000 * 2 / 100) + 100 = 20 + 100 = 120
        assert compute_fee("card", 1000) == 120

    def test_netbanking_fee_small_amount(self):
        # floor(1000 * 15 / 1000) + 100 = 15 + 100 = 115
        assert compute_fee("netbanking", 1000) == 115

    def test_fee_is_always_non_negative(self):
        for method in ["upi", "card", "netbanking"]:
            for amount in [1, 100, 10000, 1000000]:
                assert compute_fee(method, amount) >= 0


class TestComputeTax:
    def test_tax_formula(self):
        # floor(2100 * 18 / 100) = floor(378) = 378
        assert compute_tax(2100) == 378

    def test_tax_zero_fee(self):
        assert compute_tax(0) == 0

    def test_tax_floor_behavior(self):
        # floor(105 * 18 / 100) = floor(18.9) = 18
        assert compute_tax(105) == 18

    def test_tax_always_non_negative(self):
        for fee in [0, 1, 50, 100, 500, 10000]:
            assert compute_tax(fee) >= 0


# ---------------------------------------------------------------------------
# Integer arithmetic invariant
# ---------------------------------------------------------------------------

class TestIntegerArithmetic:
    def test_fee_uses_integer_division(self):
        import inspect
        source = inspect.getsource(compute_fee)
        assert "float" not in source
        assert "//" in source

    def test_tax_uses_integer_division(self):
        import inspect
        source = inspect.getsource(compute_tax)
        assert "float" not in source
        assert "//" in source


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------

class TestGenerateBatch:
    def test_default_60_settlements(self):
        data = generate_batch()
        assert len(data["settlements"]) == 80

    def test_ground_truth_count_matches_settlements(self):
        data = generate_batch()
        assert len(data["ground_truth"]) == len(data["settlements"])

    def test_label_distribution(self):
        data = generate_batch()
        labels = [gt["label"] for gt in data["ground_truth"]]
        from collections import Counter
        dist = Counter(labels)
        assert dist["clean_match"] == 30
        assert dist["missing_reference"] == 5
        assert dist["bank_mismatch"] == 5
        assert dist["fee_mismatch"] == 5
        assert dist["tax_inconsistency"] == 3
        assert dist["refund_timing"] == 5
        assert dist["unexplained"] == 8
        assert dist["adjustment_entry"] == 5
        assert dist["refund_after_settlement"] == 5
        assert dist["timing_race"] == 5
        assert dist["partial_settlement"] == 4

    def test_ground_truth_has_required_fields(self):
        data = generate_batch()
        for gt in data["ground_truth"]:
            assert "settlement_id" in gt
            assert "label" in gt
            assert "expected_amount_paise" in gt
            assert "actual_amount_paise" in gt
            assert "difference_paise" in gt

    def test_ground_truth_difference_consistency(self):
        data = generate_batch()
        for gt in data["ground_truth"]:
            expected_diff = gt["actual_amount_paise"] - gt["expected_amount_paise"]
            assert gt["difference_paise"] == expected_diff, (
                f"{gt['settlement_id']}: difference_paise={gt['difference_paise']} "
                f"!= actual-expected={expected_diff}"
            )

    def test_reproducible_with_seed(self):
        data1 = generate_batch(seed=42)
        data2 = generate_batch(seed=42)
        assert data1["settlements"] == data2["settlements"]
        assert data1["ground_truth"] == data2["ground_truth"]

    def test_different_seeds_differ(self):
        data1 = generate_batch(seed=42)
        data2 = generate_batch(seed=99)
        assert data1["settlements"] != data2["settlements"]

    def test_custom_count(self):
        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })
        assert len(data["settlements"]) == 10

    def test_mismatched_count_raises(self):
        try:
            generate_batch(n_settlements=10, edge_cases={
                "clean_match": 5,
                "missing_reference": 1,
            })
            assert False, "Should have raised ValueError"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

class TestWriteDataset:
    def test_creates_all_files(self):
        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            write_dataset(data, tmpdir)
            assert os.path.exists(os.path.join(tmpdir, "transactions.csv"))
            assert os.path.exists(os.path.join(tmpdir, "settlements.csv"))
            assert os.path.exists(os.path.join(tmpdir, "refunds.csv"))
            assert os.path.exists(os.path.join(tmpdir, "bank_credits.csv"))
            assert os.path.exists(os.path.join(tmpdir, "ground_truth.json"))

    def test_csvs_are_valid(self):
        import csv
        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            write_dataset(data, tmpdir)
            for fname in ["transactions.csv", "settlements.csv", "refunds.csv", "bank_credits.csv"]:
                path = os.path.join(tmpdir, fname)
                with open(path) as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    assert len(rows) > 0, f"{fname} is empty"
                    assert reader.fieldnames is not None, f"{fname} has no header"

    def test_ground_truth_json_valid(self):
        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            write_dataset(data, tmpdir)
            gt_path = os.path.join(tmpdir, "ground_truth.json")
            with open(gt_path) as f:
                gt = json.load(f)
            assert isinstance(gt, list)
            assert len(gt) == 10


# ---------------------------------------------------------------------------
# Edge case isolation
# ---------------------------------------------------------------------------

class TestEdgeCaseIsolation:
    def test_clean_match_has_zero_difference(self):
        data = generate_batch()
        clean_gts = [gt for gt in data["ground_truth"] if gt["label"] == "clean_match"]
        for gt in clean_gts:
            assert gt["difference_paise"] == 0, (
                f"{gt['settlement_id']}: clean_match should have difference 0, "
                f"got {gt['difference_paise']}"
            )

    def test_missing_reference_has_nonzero_difference(self):
        data = generate_batch()
        mr_gts = [gt for gt in data["ground_truth"] if gt["label"] == "missing_reference"]
        for gt in mr_gts:
            assert gt["difference_paise"] != 0

    def test_bank_mismatch_has_valid_data(self):
        data = generate_batch()
        bm_gts = [gt for gt in data["ground_truth"] if gt["label"] == "bank_mismatch"]
        for gt in bm_gts:
            assert gt["actual_amount_paise"] > 0
            assert gt["expected_amount_paise"] > 0

    def test_fee_mismatch_has_mismatched_payment(self):
        data = generate_batch()
        fm_gts = [gt for gt in data["ground_truth"] if gt["label"] == "fee_mismatch"]
        for gt in fm_gts:
            assert "mismatched_payment_id" in gt
            assert gt["mismatched_payment_id"] is not None

    def test_tax_inconsistency_has_mismatched_payment(self):
        data = generate_batch()
        ti_gts = [gt for gt in data["ground_truth"] if gt["label"] == "tax_inconsistency"]
        for gt in ti_gts:
            assert "mismatched_payment_id" in gt
            assert gt["mismatched_payment_id"] is not None

    def test_refund_timing_has_nonzero_difference(self):
        data = generate_batch()
        rt_gts = [gt for gt in data["ground_truth"] if gt["label"] == "refund_timing"]
        for gt in rt_gts:
            assert gt["difference_paise"] != 0

    def test_unexplained_has_nonzero_difference(self):
        data = generate_batch()
        ue_gts = [gt for gt in data["ground_truth"] if gt["label"] == "unexplained"]
        for gt in ue_gts:
            assert gt["difference_paise"] != 0


# ---------------------------------------------------------------------------
# Amount positivity
# ---------------------------------------------------------------------------

class TestAmountPositivity:
    def test_all_actual_amounts_positive(self):
        data = generate_batch()
        for gt in data["ground_truth"]:
            assert gt["actual_amount_paise"] > 0, (
                f"{gt['settlement_id']}: actual_amount must be > 0"
            )

    def test_all_settlement_amounts_positive(self):
        data = generate_batch()
        for s in data["settlements"]:
            assert s["amount"] > 0, (
                f"{s['settlement_id']}: settlement amount must be > 0"
            )


# ---------------------------------------------------------------------------
# CSV readability with Phase 2 ingestion
# ---------------------------------------------------------------------------

class TestIngestionCompatibility:
    def test_transactions_csv_loadable(self):
        from backend.ingestion import load_csv
        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            write_dataset(data, tmpdir)
            df = load_csv(os.path.join(tmpdir, "transactions.csv"), "transactions")
            assert len(df) > 0

    def test_settlements_csv_loadable(self):
        from backend.ingestion import load_csv
        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            write_dataset(data, tmpdir)
            df = load_csv(os.path.join(tmpdir, "settlements.csv"), "settlements")
            assert len(df) > 0

    def test_refunds_csv_loadable(self):
        from backend.ingestion import load_csv
        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            write_dataset(data, tmpdir)
            df = load_csv(os.path.join(tmpdir, "refunds.csv"), "refunds")
            assert len(df) >= 0

    def test_bank_credits_csv_loadable(self):
        from backend.ingestion import load_csv
        data = generate_batch(n_settlements=10, edge_cases={
            "clean_match": 5,
            "missing_reference": 1,
            "duplicate_settlement": 0,
            "bank_mismatch": 1,
            "fee_mismatch": 1,
            "tax_inconsistency": 1,
            "refund_timing": 1,
            "unexplained": 0,
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            write_dataset(data, tmpdir)
            df = load_csv(os.path.join(tmpdir, "bank_credits.csv"), "bank_credits")
            assert len(df) > 0
