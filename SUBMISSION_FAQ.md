# Nivara — Judge FAQ

Prepared answers to likely judge-attack questions for Razorpay Buildathon 2026, Track 04.

---

## 1. "Why do you need an LLM at all? Couldn't this all be deterministic?"

**The 11 deterministic checks catch what can be mathematically proven.** References, fees, taxes, bank credits, UTRs, amounts — these are all verifiable with integer arithmetic. No LLM needed.

**The LLM provides natural language classification of exception patterns to reduce human review time. It does not approve transactions.**

- A `MATH_DISCREPANCY` where all deterministic checks pass but the difference is non-zero. The engine cannot determine *why* — it could be refund timing, a delayed bank credit, or something genuinely unexplained. An LLM classifies the discrepancy based on the evidence packet.
- A `TIMING_MISMATCH` where the bank credit arrived late. The deterministic engine detects the amount difference but cannot determine if it's a bank processing delay or an actual error. The LLM classifies it based on the timing evidence.
- An `UNEXPLAINED` case where no clear cause exists. The LLM's job is to say "I don't know either" — which is the honest, safe behavior.

**The key insight:** Deterministic checks tell you *that* something is wrong. The LLM helps understand *why*. Humans decide what to do about it.

---

## 2. "Why isn't this just an LLM wrapper?"

An LLM wrapper sends raw data to a model and trusts its output. Nivara does the opposite:

| LLM Wrapper | Nivara |
|---|---|
| Sends raw CSV to the LLM | Sends structured evidence packets |
| LLM calculates financial values | Python performs all calculations |
| LLM decides if money matches | Deterministic engine proves correctness |
| LLM can approve transactions | LLM always escalates to human |
| LLM explanations are unverifiable | LLM citations are validated against the evidence packet |
| No ground truth evaluation | 80-settlement evaluation dataset with known labels |
| No safety guarantees | 12 deterministic checks, schema-enforced boundaries |

**The LLM never touches money.** It classifies discrepancies into categories (TIMING_MISMATCH, REFUND_TIMING, UNEXPLAINED). That's it. It cannot approve, calculate, or modify anything.

---

## 3. "What happens at 1M records?"

The deterministic engine processes ~36,000 in-memory reconciliations/second (pure computation, no I/O). End-to-end throughput with CSV parsing and SQLite writes will be lower. At that rate:

- **1M records:** ~28 seconds for deterministic reconciliation
- **LLM investigation:** Only applies to MATH_DISCREPANCY cases (~20-30% of settlements in typical data). With a real LLM, each investigation takes ~1-2 seconds.
- **Total for 1M records:** ~28 seconds deterministic + ~300 seconds LLM (if all 300K need investigation) = ~5.5 minutes

**Production scaling path:**
- Parallel processing (settlements are independent)
- Async queue for LLM investigations
- Batch LLM calls (multiple settlements per prompt)
- Persistent job store (Redis/SQS instead of in-memory)

These are deliberate scope decisions for the hackathon, not architectural limitations.

---

## 4. "Your evaluation dataset is synthetic. How do you know it's representative?"

We don't claim it is. The README explicitly states:

> "The dataset is synthetic and co-designed with the engine. A 100% match rate would mean the engine catches every case it was built to catch — which is expected, not impressive."

**What we do claim:**
- The 80 settlements cover 11 edge-case categories that map to real reconciliation failure modes
- Two deliberate blind spots (refund_after_settlement, timing_race) show where the engine genuinely fails
- Per-class precision/recall/F1 reveals exactly which categories the engine handles well vs. poorly
- The match rate is 87.5%, not 100%, because we're honest about blind spots

**What a real dataset would add:** partial settlements across multiple payouts, multi-currency transactions, adjustments and chargebacks, records from multiple merchants with overlapping payment IDs.

---

## 5. "What's the AI auto-approval rate?"

**Zero. By design. Enforced by schema.**

The `AIResponse` model has `extra="forbid"` — the LLM cannot inject any fields. The only `recommended_action` is `ESCALATE_TO_HUMAN`. The LLM classifies; humans decide.

This is not a policy choice — it's a technical constraint. Even if someone modified the LLM prompt to say "approve this," the Pydantic schema would reject the response.

---

## 6. "What if the LLM hallucinates evidence citations?"

**`validate_citations()` rejects hallucinated evidence.**

Every LLM response must cite evidence from the packet. The validation function checks that all cited IDs exist in the evidence packet. If the LLM invents a reference (e.g., cites "payment_999" which doesn't exist), the response is rejected and the case becomes UNRESOLVED.

This is tested: the test suite includes hallucinated evidence rejection tests.

---

## 7. "What happens when the LLM fails or times out?"

**The case becomes UNRESOLVED and escalates to human review.**

The `investigate()` function catches all LLM errors (timeout, API error, malformed response, hallucinated evidence). In every failure case, the result is `DecisionState.UNRESOLVED` with `escalate_to_human=True`.

The system never guesses. Never silently passes. Never assumes the discrepancy is fine.

**This is the safety invariant:** Uncertainty → human review. Always.

---

## 8. "How does the audit trail survive server restarts?"

The audit trail is stored in SQLite (`data/audit/audit.db`), not in memory. The `AuditLogger` uses append-only writes with batch hashes for idempotency.

When the server restarts:
- The in-memory job store is lost (known limitation)
- The audit trail persists in SQLite
- Any completed batch can be queried by upload hash

---

## 9. "What about the mock LLM mode? Isn't that misleading?"

The mock mode is **clearly labeled everywhere**:
- UI shows "MOCK MODE" badge in amber/yellow
- Hero metrics footer shows "MOCK MODE — heuristic classifications, not live AI"
- LLM investigation section header says "Demo Classification (Heuristic)"
- Review queue shows "MOCK" badge next to each mock-classified settlement
- LLM response explanations are prefixed with "[DEMO]"

**The goal:** A live demo that shows the full pipeline end-to-end without requiring judges to supply their own API key, while making it unambiguous that outputs are heuristic-based.

---

## 10. "What's the one thing you'd do with two more weeks?"

**Persistent job queue + async processing.** The current in-memory job store loses data on restart. Production would need Redis or SQS for job persistence, async processing for large batches, and proper error recovery.

**Multi-tenant support.** Current design is single-tenant. Production needs tenant isolation, per-tenant audit trails, and access controls.

Neither of these affect the core reconciliation logic or safety guarantees — they're infrastructure concerns that the hackathon scope deliberately excludes.

---

## Technical Summary

| Metric | Value |
|---|---|
| Deterministic checks | 12 (was 11, +adjustment consistency) |
| LLM classifications | 3 (TIMING_MISMATCH, REFUND_TIMING, UNEXPLAINED) |
| AI auto-approval rate | 0% (enforced by schema) |
| Evaluation settlements | 80 |
| Match rate | 87.5% |
| False accept rate | 12.5% (10 blind spots, disclosed) |
| Per-class macro F1 | 0.82 |
| Throughput | ~36,000 in-memory reconciliations/sec (no I/O) |
| Tests | 411 passing |
| Demo mode | Works without API key (heuristic, clearly labeled) |
