#!/usr/bin/env python3
"""
Screenshot Guide for Nivara — Razorpay Buildathon 2026

Run this script after starting the server:
  python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000

Then open http://localhost:8000 in your browser and take the following 8 screenshots.
Save them to docs/screenshots/ with the filenames below.

Steps:
  1. Upload the 4 CSV files from data/demo/ (transactions, settlements, refunds, bank_credits)
  2. Wait for processing to complete
  3. Navigate to each tab/view and take screenshots

Screenshots to capture:
"""

SCREENSHOTS = [
    {
        "filename": "01-dashboard-hero.png",
        "view": "Dashboard tab",
        "description": "5-card HeroMetrics (Processed, Clean Match, Exceptions Caught, Blind Spots, Human Review Queue) with match rate footer"
    },
    {
        "filename": "02-cash-flow-impact.png",
        "view": "Dashboard tab (scroll down)",
        "description": "Cash Flow Impact Dashboard showing Expected/Actual/Discrepancy/Potential Recovery"
    },
    {
        "filename": "03-settlement-simulator.png",
        "view": "Dashboard tab → Click 'Simulate Live Feed'",
        "description": "Real-Time Settlement Simulator streaming settlements with progress bar"
    },
    {
        "filename": "04-agent-reasoning-tree.png",
        "view": "Click any exception row → Trace tab",
        "description": "Agent Reasoning Tree showing ReAct loop with expandable steps"
    },
    {
        "filename": "05-risk-radar.png",
        "view": "Same Trace view (scroll down)",
        "description": "Settlement Risk Radar with 5-axis SVG spider chart"
    },
    {
        "filename": "06-sources-linker.png",
        "view": "Sources tab",
        "description": "Cross-Source Visual Linker showing 4 CSV cards with linkage lines"
    },
    {
        "filename": "07-review-queue.png",
        "view": "Review Queue tab",
        "description": "Review Queue with escalation reasons, AI analysis, and Approve/Reject buttons"
    },
    {
        "filename": "08-audit-trail.png",
        "view": "Audit Trail tab",
        "description": "Audit Trail with hash chain, Verify Integrity button, and append-only records"
    },
]

if __name__ == "__main__":
    print("=" * 60)
    print("NIVARA — Screenshot Guide")
    print("=" * 60)
    print()
    print("To capture screenshots:")
    print("  1. Start the server:")
    print("     python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000")
    print()
    print("  2. Open http://localhost:8000 in your browser")
    print()
    print("  3. Upload the 4 CSV files from data/demo/:")
    print("     - transactions.csv")
    print("     - settlements.csv")
    print("     - refunds.csv")
    print("     - bank_credits.csv")
    print()
    print("  4. Take screenshots as described below:")
    print()
    for i, s in enumerate(SCREENSHOTS, 1):
        print(f"  Screenshot {i}: {s['filename']}")
        print(f"    View: {s['view']}")
        print(f"    Shows: {s['description']}")
        print()
    print("  5. Save screenshots to docs/screenshots/")
    print("=" * 60)
