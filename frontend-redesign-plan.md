# Nivara Frontend Redesign — Plan

Styling-only redesign. Zero JSX structure, props, state, or logic changes anywhere — every edit is a CSS token/value swap or a `style={{}}` color/font value swap. `git diff --stat` at the end should show no line-count growth in any component beyond token substitutions and the audit-chain visual (Task 3).

## Direction (approved)

**Ledger / clearing-house**, not generic SaaS-blue. Domain: settlement, UTR, bank credit, ledger, stamp, escrow, clearing, hash-chain, human review.

- **Colors:** ink-navy `#12172b` (header/primary text), parchment `#f6f1e7` (page bg), cream `#fffdf8` (card surface), brass `#b8925a` (single accent — primary buttons, active tab, focus ring), ledger-green `#2f6f52` (clean/success), stamp-red `#9c3b32` (exception/error). Replaces the current SaaS blue/pastel-badge palette entirely.
- **Type:** serif (Spectral or IBM Plex Serif, Google Fonts) for `h1`/card titles/section headers. Body stays system sans. Trace/audit boxes keep monospace (SF Mono/Fira Code stack) — already on-brand, untouched.
- **Badges:** ink-stamp treatment — tighter radius (4–6px, not pill), bordered not soft-bg-only, uppercase + tracked text. Reserve ledger-green/stamp-red strictly for decision-state semantics (clean/exception), not decoration.
- **Depth:** borders + a quiet 2–3-step shadow scale, held consistently — replaces the current flat single `--shadow-sm` used everywhere.
- **Signature element:** audit trail (`AuditTrace.jsx`) rendered as a visual hash-chain — connector marks between records — instead of a bare JSON dump.
- **Dark mode:** `prefers-color-scheme` media query only, same hue shifted lighter/darker. No toggle UI, no JS state — zero logic touched.

## Task 1 — Token & base system rewrite

**File:** `frontend/src/index.css` (full rewrite of `:root` tokens + shared classes: header, tab-bar, card, metric, btn, badge, table, upload, trace-box, toast, modal).

- Replace every color token in `:root` with the ledger palette above.
- Add `@media (prefers-color-scheme: dark)` block redefining the same token names (same hue, shifted lightness), per the interface-design skill's dark-mode guidance.
- Add Google Fonts `<link>` for Spectral (or IBM Plex Serif) in `frontend/index.html` — the one non-CSS file this plan touches, and only to add a `<link>` tag, nothing else.
- Rewrite `.badge` family to the ink-stamp treatment (tighter radius, border, tracked uppercase text).
- Add a 2–3 step `--shadow` scale (replacing the single flat shadow currently reused everywhere) and apply consistently to `.card`, `.modal-content`, `.toast`.
- Add `font-variant-numeric: tabular-nums` to `.metric .value`, table numeric cells, and trace-box figures.
- Verify: `npm run build` clean, then eyeball every existing screen in the dev server (upload, dashboard, trace, review queue, patterns, sources, audit, metrics) — nothing should look structurally broken, only re-colored/re-typed.

## Task 2 — Component inline-style sweep (heavy files)

Hardcoded hex values inside `style={{}}` props that must move to the new tokens, grouped by weight:

- `App.jsx` — 41 occurrences (modal, toast colors, trace section inline styles)
- `components/MetricsDashboard.jsx` — 39 occurrences (chart colors — Recharts `fill`/`stroke` props, stat tiles)
- `components/AgentReasoningTree.jsx` — 26 occurrences
- `components/CashFlowImpact.jsx` — 23 occurrences

For each: replace hardcoded hex with the matching new CSS custom property (`var(--brass)`, `var(--green)`, `var(--red)`, etc.) or, where a value is truly one-off (e.g. a Recharts series color), the closest palette-derived hex — never reintroduce the old blue/purple/pastel set. No JSX structure changes; only the string/hex values inside existing `style={{}}` objects and any hardcoded color props change.

Verify: `npm run build` clean after this task; visually check Dashboard and Trace views (the two screens these files render into).

## Task 3 — Component inline-style sweep (remaining files) + audit-chain signature

- `components/CrossSourceLinker.jsx` (17), `components/SettlementSimulator.jsx` (8), `components/SettlementRiskRadar.jsx` (7), `components/AuditTrace.jsx` (5), `components/ReviewQueue.jsx` (5), `components/ResultsTable.jsx` (4), `components/UploadPanel.jsx` (2) — same token-swap treatment as Task 2.
- `components/AuditTrace.jsx` additionally gets the signature hash-chain visual: a connector mark (small vertical line + dot, pure CSS `::before`/`::after` on each audit-record block) between consecutive records, styled with the new ink-navy/brass tokens. This is a new CSS class applied to existing markup, not new component logic — no new state, no new props, no data reshaping.

Verify: `npm run build` clean; visually check Sources, Trace (risk radar), Review Queue, Results table, Upload, and Audit Trail views.

## Task 4 — Full verification pass

- `npm run build` clean (final check across all changed files).
- Dev server walkthrough of all 8 tabs (Upload, Dashboard, Trace, Review Queue, Patterns, Sources, Audit Trail, Metrics) at desktop width; spot-check mobile breakpoints already defined in `index.css` (`@media max-width: 900px/600px/500px`).
- Squint test: hierarchy still reads, nothing jarring, one accent color doing the work.
- Confirm zero `git diff` hunks outside `frontend/src/index.css`, `frontend/index.html`, and `style={{}}`/className value changes in the 11 component files + `App.jsx` — no prop signatures, no state, no logic.

## Explicitly out of scope

- No component restructuring, no new components, no new npm dependencies (fonts load via Google Fonts `<link>`, not a package).
- No dark-mode toggle UI — system-preference-driven only.
- No changes to `backend/` or any `.py` file.
