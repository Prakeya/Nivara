# Nivara — Demo Video Script

Target length: **90–120 seconds**. Style: screen recording + voiceover +
minimal captions. The hook is the judge's rule of thumb: *"works, and the code
is clean."* The script ends where the product is undeniably working.

---

## Scene 1 — Cold open (0:00–0:08)

**Screen:** `git log --oneline -20` scrolling, then the terminal clears to a
pytest run.

> **VO:** "Every commit in this repo runs the full test suite. Eight hundred
> checks... let's actually run them."

**Action:** `npm run build` (green), then:

```
PYTHONPATH="." python3 -m pytest -q
691 passed in 4.25s
```

> **VO (over the pass):** "That's the first thing the code does. Now the
> product."

---

## Scene 2 — Upload (0:08–0:25)

**Screen:** browser at `http://localhost:8000`. Click **Generate Sample**, then
**Upload & Reconcile**. Four CSVs parse; the toast fires: *"Processed 80
settlements."*

> **VO:** "Razorpay settles thousands of merchants every day. Nivara takes the
> four CSVs — transactions, settlements, refunds, bank credits — and runs 12
> deterministic checks in integer paise. No floats, no LLM in the money math."

---

## Scene 3 — Dashboard + Trace (0:25–0:45)

**Screen:** hero metrics (87.5% match, exceptions, unresolved), then click a
MATH_DISCREPANCY row → **Reconciliation Trace**.

> **VO:** "Every row opens a full reconciliation trace: expected, actual,
> difference, then exactly which deterministic checks passed and which failed.
> And critically — if the engine can't explain it, only then does the AI
> investigator run. Groq's 70B model, with a hard safety rule..."

**Highlight the evidence block:**

> **VO:** "...it can only cite evidence that exists in this packet. It can't
> invent numbers, it can't approve anything. The schema literally forbids it."

---

## Scene 4 — Review Queue + Audit (0:45–1:05)

**Screen:** Review Queue tab → click **Approve** on one item → Audit Trail tab
→ **Verify Integrity**.

> **VO:** "Every decision lands in an append-only audit log, chained with
> SHA-256. Watch what happens when we tamper with a single record..."

**Action:** flip a byte, re-verify → *"Chain broken at record 0."*

> **VO:** "That's how a finance team can prove — to auditors, to the bank —
> that nobody quietly changed a settlement after the fact."

---

## Scene 5 — Metrics + Close (1:05–1:30)

**Screen:** Metrics tab — decision pie chart, Groq quota progress bar, latency,
cost. Then back to the terminal:

```
scripts/load_test.py → 2,260 req/s · p95 7.5 ms · 0 errors
```

> **VO:** "And because the whole thing is groq-free-tier cost for the LLM, the
> Metrics tab shows a live cost of ₹0.00. Nivara: money math is deterministic,
> AI is advisory, and everything is auditable. Clean code that works — from the
> first commit to the 691st test."

**Final card:** `github.com/Prakeya/Nivara`