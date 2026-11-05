---
name: agent-evaluator
description: Design and run repeatable evaluations for AI agents and LLM applications using scenario sets, deterministic checks, LLM-as-judge rubrics, tool-trajectory review, grounding checks, adversarial cases, near-miss detection, regression comparison, and scorecards. Use when testing agent quality, creating eval datasets, comparing versions, or validating prompts/tools. This produces one input (`eval-report.json`) toward a release decision; for "can we release/merge/deploy this" use `agent-release-gate` instead, which consumes this report rather than re-running evaluation itself.
---

# Agent Evaluator

Evaluate behavior across many representative cases. One good answer is not evidence of reliability.

## Core Evaluation Loop

1. **Define the decision.** State what must be true for the agent to be acceptable.
2. **Create scenarios.** Start with 3-5 core cases, then add edge, adversarial, and regression cases. Use `examples/eval-cases.example.jsonl` as the schema guide.
3. **Choose metric types.** Combine:
   - deterministic rules for objective invariants,
   - fixed rubrics for stable quality dimensions,
   - adaptive case-specific rubrics when different scenarios require different success criteria.
4. **Harness Self-Check, before trusting any harness output.** A harness bug looks exactly like a production bug (or vice versa) — verify, don't assume:
   - **config resolution**: the harness loads config through the same code path production uses, no parallel/duplicated logic.
   - **model resolution**: the model/deployment string used for a real call is the same value the real app would use, not a placeholder that only exists on a fake/dry-run object — fail fast if it is empty/`None`/a sentinel like `"unknown"`.
   - **mock/live switch**: which dependencies are real vs. stubbed is asserted at runtime (a self-check that aborts if a stub isn't confirmed active), not just documented.
   - **side-effect isolation**: mocked side effects (email, DB writes, crawls) are structurally incapable of firing for real, not just "supposed to be" mocked.
   - **production path parity**: the harness calls the actual function(s) production uses today, not a superseded/dead-code path — check for live callers, don't assume the target function is still the real entry point.
   - **fail-fast validation**: invalid/placeholder inputs to a real external call raise a clear local error instead of producing a confusing remote error.
   See the `agent-development-integrity` skill's incident log for three concrete cases where skipping this step led to misdiagnosing a harness bug as a production bug.
5. **Run the target agent, gated by Tier.** Classify every harness action as **Tier R** (read-only, free, no side effects — dataset validation, aggregation, dry-run/fake-client smoke tests; runs freely, no approval needed) or **Tier M** (makes a real paid/slow call, or any real side effect — a live LLM run, sending a real email, a real DB write; **requires explicit user approval before running, and must never be auto-run in the same turn it was proposed**, even if Tier R checks all passed). Say the tier out loud when proposing a step, and stop for confirmation before any Tier M action. Prefer the project's existing eval harness. If none exists, use a simple, explicit runner rather than inventing hidden infrastructure.
6. **Grade outputs and observable traces.** Evaluate final task success, grounding, instruction following, tool choice/arguments, safety, and efficiency. Do not request or depend on hidden chain-of-thought. If grading uses an LLM judge, record `judge_model`/`judge_independence` (`same_model` if the judge shares a model with the generator — this repo may only have one configured deployment, which is common; say so explicitly rather than implying independent cross-validation) and `judge_input_type` (`live_model_output` vs. e.g. `fixture_scenario_not_live_generation` when grading a pre-authored fixture instead of a fresh generation) per graded case. Never let the judge overwrite a deterministic verdict — report both side by side.
7. **Detect near misses.** Mark `near_miss=true` when the final result appears acceptable but observable evidence shows a material defect: wrong tool path, unsupported claim, policy violation that happened not to cause damage, lucky guess, or omitted required validation. Track near-miss rate as its own axis, broken out per component/tag (e.g. per "adversarial", "boundary") — do not blend it into one aggregate number, since a rare-but-severe near-miss in a security-sensitive tag can be diluted by a large happy-path case count.
8. **Analyze failures by pattern.** Fix the failure mode, not only the single example.
9. **Re-run the same baseline.** Add new cases only after core failures are understood.
10. **Compare versions.** Confirm the target metric improves and previously passing metrics do not regress.
11. **Write `.agent-governance/eval-report.json`.** Run `scripts/aggregate_results.py` when result JSONL is available. Allow component-specific metrics in addition to the generic dimensions below (e.g. `authorization_integrity`, `deterministic_gate_correctness` for a permission/approval-style component) — do not force every component onto one identical global metric bar when its acceptance criteria are genuinely different.

## Metric Guidance

Recommended default dimensions for agents:

- `task_success`
- `grounding`
- `instruction_following`
- `tool_use_quality`
- `trajectory_quality` — based only on observable actions/events
- `safety`
- `final_response_quality`

Domain-specific agents should add explicit dimensions rather than hiding them in a generic score. For intelligence/research agents, useful additions include source quality, evidence discipline, uncertainty handling, coverage of required actors/stakeholders, and separation of fact from inference.

## Dataset Design

Cover at minimum:

- happy path
- ambiguous request
- missing evidence/data
- conflicting evidence
- tool failure/timeout
- prompt injection or hostile retrieved content when tools/retrieval are used
- boundary/permission case when actions are possible
- previously observed production failure

Avoid datasets dominated by synthetic variants of one case.

## Judge Discipline

- Record the rubric with the score.
- Prefer specific criteria over "is this good?".
- Separate factual/grounding checks from style.
- Use deterministic checks for exact invariants whenever possible.
- Do not fabricate scores. If a case was not run, mark it `not_run`.
- Keep baseline and candidate conditions comparable.

## Result Contract

Each result JSONL row should contain:

```json
{
  "id": "case-001",
  "status": "completed",
  "scores": {"task_success": 1.0, "grounding": 0.8},
  "deterministic_checks": {"no_unauthorized_write": true},
  "near_miss": false,
  "judge_model": null,
  "judge_independence": null,
  "judge_input_type": "live_model_output",
  "notes": "short evidence-based explanation"
}
```

Scores should use `0.0-1.0`. Keep the original detailed judge evidence elsewhere if needed. Leave `judge_model`/`judge_independence` `null` when no judge graded this case (deterministic-only). Same-model judge output has observed non-zero run-to-run variance on subjective dimensions even against an identical input — treat a single-dimension flip between two otherwise-identical runs as noise unless it touches a security/safety-relevant verdict.

## Report Contract (`.agent-governance/eval-report.json`, produced by `scripts/aggregate_results.py`)

Wrap the aggregated summary in the shared envelope from
`agent-development-integrity`'s `references/evidence-schema.md`:

```json
{
  "status": "complete",
  "scope_status": "complete",
  "generated_at": "2026-09-13T00:00:00Z",
  "source_revision": "<git SHA evaluated>",
  "confidence": "HIGH",
  "deferred_by_user_decision": false,
  "deferral_reason": null,
  "summary": {"cases": 0, "metrics": {}, "near_miss_count": 0, "near_miss_rate": null},
  "failure_patterns": [{"check": "no_unauthorized_write", "fail_count": 3, "case_ids": ["case-004", "case-011", "case-019"]}],
  "by_tag": {},
  "limitations": []
}
```

Render this as a shareable HTML page with `scripts/render_html_report.py` when a
human audience (not just `agent-release-gate`) needs to read it.

If live execution was intentionally deferred (e.g. to avoid cost until
approved), set `status: "designed_not_run"` and
`deferred_by_user_decision: true` with a `deferral_reason` — `agent-release-
gate` treats this as a `WARN` with the reason surfaced, not the same as an
undeclared gap.

## Bundled Resources

- `scripts/validate_evalset.py`: validate scenario JSONL structure. **Tier R.**
- `scripts/aggregate_results.py`: aggregate scores, failures, near misses, tag breakdowns, and `failure_patterns` (which deterministic check fails, how often, and in which cases — a recurring check name is one systemic failure mode, not N separate bugs). **Tier R.**
- `scripts/render_html_report.py`: render `eval-report.json` as a single self-contained, offline HTML file (no CDN/JS framework) — convenient for sharing outside a terminal. **Tier R.**
- `references/rubric-patterns.md`: rubric and near-miss patterns.
- `examples/eval-cases.example.jsonl`: starter scenario set.
- See also the `agent-development-integrity` skill's Harness Self-Check
  section and incident log before running any harness for real.

## Version

```yaml
version: 0.2
frozen: true  # do not add new features until validated on a second, independent project
validated_on:
  - world_monitor_dashboard
key_lessons:
  - the eval harness itself needs a self-check before its output is trusted (3 separate harness bugs found in one project)
  - near-miss rate must be tracked per component/tag, not only as one blended number
  - declare judge_independence and judge_input_type explicitly; do not imply cross-validation a same-model judge cannot provide
  - "2026-09-15: added Tier R/Tier M gating, failure-pattern clustering, and an HTML report renderer, after comparing against Google's agent-platform-eval-flywheel skill (see GOOGLE_COMPARISON.md one level up) -- adopted its risk-tiering idea and its dual JSON+HTML output, kept our own cloud-agnostic JSONL contract"
```
