#!/usr/bin/env python3
"""
Nivara — AI Finance Controller Demo

One-command end-to-end run:
  1. Generate 80 synthetic settlements with ground truth
  2. Run full test suite (465+ tests)
  3. Ingest → Link → Reconcile → AI Investigate → Evaluate
  4. Print evaluation report with match rate, per-class F1, throughput

Usage:
    python3 demo.py
"""

import json
import os
import subprocess
import sys
import time


def run(cmd: list[str], desc: str, check: bool = True) -> subprocess.CompletedProcess:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"  $ {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, capture_output=False, text=True)
    if check and result.returncode != 0:
        print(f"\nFAILED: {desc}")
        sys.exit(1)
    return result


def main() -> int:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print("=" * 60)
    print("  NIVARA — AI Finance Controller Demo")
    print("  Razorpay Buildathon 2026 — Track 04")
    print("=" * 60)

    # Step 1: Generate synthetic data
    run(
        [sys.executable, "-m", "backend.generator"],
        "Step 1/4: Generate 80 synthetic settlements with ground truth",
    )

    # Step 2: Run full test suite
    run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
        "Step 2/4: Run full test suite",
    )

    # Step 3: Run evaluation harness
    print(f"\n{'='*60}")
    print("  Step 3/4: Evaluation — Ingest → Reconcile → AI → Evaluate")
    print(f"{'='*60}")

    from backend.ingestion import ingest_csvs
    from backend.engine import run_engine
    from backend.evaluation import evaluate_batch, format_report, format_label_breakdown

    ing = ingest_csvs(
        transactions_path="data/evaluation/transactions.csv",
        settlements_path="data/evaluation/settlements.csv",
        refunds_path="data/evaluation/refunds.csv",
        bank_credits_path="data/evaluation/bank_credits.csv",
    )

    with open("data/evaluation/ground_truth.json") as f:
        ground_truth = json.load(f)

    start = time.time()
    results = run_engine(
        ing.transactions, ing.settlements, ing.refunds, ing.bank_credits,
    )
    elapsed = time.time() - start

    metrics = evaluate_batch(
        results, ground_truth,
        batch_time_seconds=elapsed,
        ai_client_available=False,
    )

    print(f"\n{format_report(metrics)}")
    print(f"\n{format_label_breakdown(metrics)}")

    # Step 4: Summary
    print(f"\n{'='*60}")
    print("  Step 4/4: Demo Complete")
    print(f"{'='*60}")
    print(f"  Match rate:      {metrics.match_rate:.1%}")
    print(f"  False accept:    {metrics.false_accept_rate:.1%}")
    print(f"  Throughput:      {metrics.throughput_per_second:,.0f} settlements/sec")
    print(f"  Tests:           465+ passed")
    print(f"  Audit:           Append-only SQLite")
    print(f"  Human review:    POST /api/review/{{id}}/decision")
    print(f"  Agent trace:     ReAct loop, 6 tools, max 3 iterations")
    print()
    print("  To start the API:")
    print("    uvicorn backend.main:app --reload")
    print()
    print("  To view the dashboard:")
    print("    open http://localhost:8000")
    print()
    print("  To upload data via API:")
    print('    curl -X POST http://localhost:8000/upload \\')
    print('      -F "transactions=@data/evaluation/transactions.csv" \\')
    print('      -F "settlements=@data/evaluation/settlements.csv" \\')
    print('      -F "refunds=@data/evaluation/refunds.csv" \\')
    print('      -F "bank_credits=@data/evaluation/bank_credits.csv"')
    print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
