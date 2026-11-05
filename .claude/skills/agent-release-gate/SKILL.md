---
name: agent-release-gate
description: Produce an evidence-based PASS, WARN, or BLOCK release decision for an AI application or agent by combining security findings, agent evaluation results, regressions, and token/cost/latency evidence. This is the canonical skill for "can/should we release, merge, or deploy this", "is this ready for production", or "give me a go/no-go" — it reads the evidence files the other 4 governance skills produce (security-report.json, eval-report.json, token-report.json, integrity-report.json) rather than re-running any of those reviews itself; if an evidence file is missing or stale, direct the user to (or run) the specific skill that produces it first, don't substitute this skill's own judgment for that review.
---

# Agent Release Gate

Turn separate reviews into a reproducible release decision. Do not replace missing evidence with optimism.

## Workflow

1. **Read the policy.** Auto-discover a repository-specific policy at `.agent-governance/release-policy.json` first; if absent, fall back to `references/default-policy.json` and explicitly state that defaults are being used. `scripts/release_gate.py` does this discovery automatically when `--policy` is omitted.
2. **Collect evidence.** Prefer these files:
   - `.agent-governance/security-report.json`
   - `.agent-governance/eval-report.json`
   - `.agent-governance/token-report.json`
3. **Refresh stale evidence.** If code, prompts, tools, permissions, model routing, or dependencies materially changed after a report was generated, re-run the relevant review.
4. **Fail closed on required missing evidence — but distinguish *why* it's missing.** Security and evaluation are required by the default policy. Token/cost evidence is advisory unless the local policy makes it blocking. Treat these as different situations, not one generic "blocked":
   - **`not_run`/deferred by an explicit, declared decision** (e.g. an evaluation was designed but live execution deferred to avoid cost until approved) — this is informational, and should read as `WARN` with the specific deferral reason surfaced, not a generic "missing or incomplete" `BLOCK`. Declare this in the evidence report as `"deferred_by_user_decision": true` with a `reason`.
   - **executed and failed a threshold** — a real quality/security blocker, always `BLOCK`.
   - **`unknown`/never attempted, no declared reason** — treat as `BLOCK` (fail closed), since an undeclared gap gets no benefit of the doubt.
5. **Run `scripts/release_gate.py`.** Pass the selected policy and report paths (or let it auto-discover the policy per step 1).
6. **Explain the decision.** Separate blockers from warnings. Cite the specific metric/finding that caused each gate result.
7. **Do not deploy automatically.** A PASS means the evidence meets the configured gate; deployment still requires the user's normal authorization/process.
8. **Distinguish this Code/Merge Gate from a Production Readiness Gate.** This skill answers "does the evidence meet the configured bar for the code itself" — a separate question from "is the surrounding system/organization/platform actually ready to operate this" (e.g. an external identity provider isn't configured yet, an operational team hasn't been briefed on a behavior change). Track both explicitly rather than collapsing them into one status; a `PASS` here does not imply the second gate also passes. Before recommending a merge while a Production Readiness Gate is still failing, check whether the merge itself is the deploy trigger (e.g. CI/CD that auto-deploys on push to the target branch) — if so, do not recommend merging into that branch until both gates pass; if merge and deploy are genuinely decoupled in this repo, merging while deploy is deliberately withheld is reasonable. See `agent-development-integrity`'s Two-Gate Model section.

## Default Gate Philosophy

- Any `P0` or `P1` confirmed security finding blocks release.
- Evaluation must meet explicit metric floors.
- Failed deterministic safety/authorization checks block release.
- Near misses are tracked separately; a low task-failure rate does not hide them.
- Missing required evidence blocks release.
- Token/cost/latency optimization should not override quality or security.

## Output

Use:

```markdown
# Release Readiness
Decision: PASS | WARN | BLOCK

## Blocking reasons
## Warnings
## Evidence summary
## Required remediation
## Re-run instructions
```

Save the machine-readable result to `.agent-governance/release-gate.json`.

## Bundled Resources

- `scripts/release_gate.py`: deterministic gate evaluator. **Tier R** (reads local evidence JSON files only, no network, no live calls).
- `references/default-policy.json`: conservative starter thresholds.
- `references/report-contract.md`: report fields consumed by the gate.

## Version

```yaml
version: 0.2
frozen: true  # do not add new features until validated on a second, independent project
validated_on:
  - world_monitor_dashboard
key_lessons:
  - this skill should consume the other 4 skills' structured evidence, not re-run or re-interpret their prose
  - distinguish deferred (declared, informational) from unknown (undeclared gap, fail closed) from failed (real threshold miss)
  - a Code/Merge Gate PASS does not imply a Production Readiness Gate PASS -- keep them as separate, explicit decisions
```
