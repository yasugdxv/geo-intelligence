# Governance Skills v0.2 — Fault-Injection Regression Suite Results

Run via `python run_regression_suite.py` (rebuilds a throwaway fixture repo
each time; safe to re-run any time the skills change). Last run:
2026-09-13, **7/7 PASS**.

| Case | Injected fault | Expected detector | Result |
|---|---|---|---|
| A | Worktree/branch rooted at the wrong base commit | `agent-development-integrity`'s `base_revision_guard.py` exits 2 | PASS |
| A2 (control) | Correctly-based worktree | same script exits 0 | PASS |
| B | A change touches a file outside the declared allowlist | `scope_guard_check.py` reports the exact violating file, exits 2 | PASS |
| C | A fixture file with an obvious unauthenticated-handler pattern | `agent-security-review`'s `scan_security_surface.py` surfaces it as a hit | PASS |
| D | All deterministic checks pass, but `near_miss=true` | `agent-evaluator`'s `aggregate_results.py` still reports `near_miss_count=1` (overall and per-tag) — a clean pass rate does not hide it | PASS |
| E | (a) a genuinely deferred eval with a declared reason, (b) an undeclared `partial` eval | `agent-release-gate` surfaces the specific deferral reason as a WARN instead of a generic "missing" message for (a) — note it still correctly BLOCKs on the separate, real `min_cases` threshold not being met, since a declared deferral is not a threshold exemption; (b) BLOCKs with the generic reason since no reason was declared | PASS |
| F | Same code, two different evidence sets — one meeting a "Code Gate" policy, one representing a still-unconfigured production dependency under a "Production Gate" policy | `release_gate.py` returns `PASS` for the Code Gate run and `BLOCK` for the Production Gate run from the same script, proving the two are genuinely independent decisions, not one blended status | PASS |

## What this suite deliberately does NOT cover

- It does not test any of the 4 evidence-producing skills' actual LLM-driven
  judgment (e.g. "does `agent-security-review` correctly derive P1 vs P2
  severity from a real codebase") — that requires a real or much richer
  fixture app and a live model call, and is a different, larger validation
  exercise. This suite only tests the **mechanical, scriptable** parts of
  each skill: the parts that must never depend on an LLM getting it right,
  because they exist specifically to catch cases where a report's prose
  could be wrong.
- It does not exercise `agent-token-optimizer`'s scripts directly (no fault
  was designed for token/cost estimation in this pass) — a candidate
  addition for a future revision of this suite.

## Re-running this suite

Safe to run standalone any time; it only touches a `tempfile.mkdtemp()`
directory, never this repository. Re-run after any change to
`base_revision_guard.py`, `scope_guard_check.py`,
`scan_security_surface.py`, `aggregate_results.py`, or `release_gate.py` to
confirm no regression in the mechanical detectors themselves.
