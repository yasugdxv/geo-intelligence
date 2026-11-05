# Governance Evidence Schema (shared across all 5 governance skills)

Every governance skill's machine-readable report lives under
`.agent-governance/` and shares this base envelope, so `agent-release-gate`
(or a human) can make a decision from **structured fields**, not by
re-reading and interpreting each report's prose. This is what most directly
prevents the "the written report said X, but the actual data said Y"
failure mode this project hit more than once (see
`references/incident-log.md`).

## Base fields every report must have

```json
{
  "status": "complete",
  "scope_status": "complete",
  "generated_at": "2026-09-13T00:00:00Z",
  "source_revision": "<git SHA the review/eval/optimization/gate ran against>",
  "confidence": "HIGH",
  "production_reachability": "not_applicable",
  "evidence": [],
  "limitations": []
}
```

- **`status`**: this skill's own run status — `complete`, `partial`,
  `designed_not_run`, `error`. Distinct from `scope_status`.
- **`scope_status`**: did this run cover what it set out to cover?
  `complete` / `partial`. A `partial` `scope_status` should always be
  explained by entries in `limitations`, not left bare.
- **`generated_at`**: ISO-8601 timestamp of report generation. Lets a
  consumer (especially `agent-release-gate`) detect stale evidence.
- **`source_revision`**: the exact commit/ref the review ran against. If this
  doesn't match the commit currently being gated, the evidence is stale —
  `agent-release-gate` should treat that as a reason to re-run, not ignore.
- **`confidence`**: `HIGH` / `MEDIUM` / `LOW` for the report as a whole
  (individual findings/metrics may also carry their own).
- **`production_reachability`**: for anything finding- or metric-shaped,
  whether what's being reported is reachable from a live/production caller
  today — `production_reachable` / `dead_code_only` / `not_applicable` (use
  `not_applicable` for reports that aren't about a specific code path, e.g.
  a pure token-usage summary).
- **`evidence`**: a list of concrete pointers (`file:line`, a trace field, a
  measured run, a test name) backing this report's claims — never leave a
  significant finding or metric without at least one entry here.
- **`limitations`**: justified, by-design exclusions or deferrals — each
  entry `{"item": "...", "reason": "...", "by_design": true}`. A limitation
  entry must never by itself force `scope_status` to `partial`; only a real,
  undeclared gap should.

## Per-skill report files and their skill-specific fields

| File | Skill | Skill-specific fields on top of the base envelope |
|---|---|---|
| `integrity-report.json` | `agent-development-integrity` | `base_revision_guard: {pass, head, expected_base}`, `scope_guard: {allowed_files[], actual_changed_files[], violations[]}`, `invariants_checked[]` |
| `security-report.json` | `agent-security-review` | `summary.severity_counts`, `findings[]` (each with `severity`, `severity_rationale`), `reliability_findings[]`, `amendments[]`, `needs_verification[]` |
| `eval-report.json` | `agent-evaluator` | `summary.cases`, `summary.metrics` (+ per-component metrics), `summary.near_miss_rate` (+ per-tag), `deferred_by_user_decision` + `deferral_reason` when applicable |
| `token-report.json` | `agent-token-optimizer` | `baseline.type` (`measured`/`estimated`), `findings[]` (each with `waste_dimensions[]`, its own `evidence_type`) |
| `release-gate.json` | `agent-release-gate` | `decision` (`PASS`/`WARN`/`BLOCK`), `policy_used`, `blocking_reasons[]`, `warnings[]`, `evidence` (which of the above were present) |

`agent-release-gate` should read `source_revision` from every evidence file
it consumes and flag a mismatch against the commit actually being gated as a
blocking staleness issue, not silently proceed as if the evidence still
applies.

## Tier R / Tier M (shared action-risk convention)

Every script bundled with any of these 5 skills is one of:

- **Tier R** — read-only and free: reads local files, runs `git` status/diff
  commands, does static analysis, aggregates already-collected results. Runs
  without asking. All scripts in all 5 skills are Tier R today.
- **Tier M** — makes a real paid/slow call or a real side effect: a live LLM
  call, sending a real email, a real DB write, applying a real Azure/GitHub
  configuration change. **Requires explicit user approval before running,
  and must never be auto-run in the same turn it was proposed**, even if
  every Tier R check already passed. State the tier out loud when proposing
  a next step.

This mirrors (and was adopted after comparing against) Google's
`agent-platform-eval-flywheel` skill, which formalizes the same idea for its
own compute-heavy evaluation runs — see `../../GOOGLE_COMPARISON.md`.

## Two-Gate Model and this schema

A Code/Merge Gate decision only needs `security-report.json` +
`eval-report.json` (+ `token-report.json` advisory) at the commit being
merged. A Production Readiness Gate decision additionally needs operational
evidence (platform configuration status, operational-readiness sign-off)
that doesn't fit neatly into any one existing report — record it as its own
evidence file (e.g. `production-readiness-report.json`) using the same base
envelope, with `production_reachability` describing whether the *code* is
ready vs. whether the *platform* is actually configured (mirroring the
`CODE_READY` / `PLATFORM_CONFIGURED` / `FIXED_IN_PRODUCTION` distinction
this project's own security finding used).
