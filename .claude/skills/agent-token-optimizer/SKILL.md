---
name: agent-token-optimizer
description: Analyze and reduce token use, LLM cost, context size, redundant retrieval, tool payload size, model overuse, and latency in AI applications or agents without lowering task quality. Use when asked to optimize token usage, reduce AI cost or latency, inspect prompts/context/retrieval, choose cheaper models, add caching or compaction, or compare before/after efficiency. This produces one input (`token-report.json`, advisory by default) toward a release decision; it does not itself decide whether to release.
---

# Agent Token Optimizer

Optimize measurable efficiency while preserving agent quality. Never optimize token count in isolation.

## Workflow

1. **Discover the execution path.** Locate model calls, system prompts, conversation history handling, retrieval, tool results, sub-agent calls, retries, and model-routing logic. Read project instructions before editing.
2. **Establish a baseline.** Prefer runtime traces or provider usage data. If usage JSONL exists, run `scripts/analyze_usage.py`. If no runtime data exists, run `scripts/prompt_inventory.py` and label conclusions as static estimates.
3. **Build a token-flow map.** For each important agent action, identify: static prompt, user input, history, retrieved context, tool output, model output, retries, and downstream re-sends.
4. **Classify waste.** Use `references/optimization-checklist.md`. Prioritize repeated context, retrieval overfetch, verbose tool payloads, unnecessary multi-agent handoffs, duplicate model calls, oversized models, and missing cache/compaction boundaries.
5. **Propose the smallest safe changes.** Rank each change by expected saving, implementation effort, and quality risk. Do not claim exact cost savings without measured pricing or cost data.
6. **Implement only changes appropriate to the request.** Preserve interfaces and tests. Do not silently change model/provider, drop evidence, or truncate safety-critical context.
7. **Re-run the same eval set.** If `/agent-evaluator` is available, use the same baseline cases. A token reduction that causes quality regression is not an improvement.
8. **Write evidence.** Save a machine-readable report to `.agent-governance/token-report.json` and a concise human summary when the repository is writable.

## Measurement Rules

- Prefer actual provider token counters over character estimates.
- If exact tokenization is unavailable, label estimates as estimates.
- Separate input, cached input, output, and total tokens whenever possible.
- Report p50/p95 latency only from observed data.
- Sum `cost_usd` only when the trace already contains cost. Do not hard-code current provider prices.
- Compare before/after on the same scenarios and model settings unless the change being tested is model routing.

## Quality Guard

Before recommending a change, identify what could be lost: evidence, historical context, instructions, tool arguments, safety policy, or domain knowledge. Prefer structured compression over blind truncation.

For agent systems, check observable behavior rather than hidden chain-of-thought. Use final-response quality, citations/grounding, tool trajectory, task success, and policy compliance.

## Output

Use this structure:

```markdown
# Token & Latency Review
## Baseline
## Top waste sources
## Recommended changes
## Before / after
## Quality guard results
## Remaining uncertainties
```

Each finding must include: evidence (`file:line`, trace field, or measured run); one of `HIGH`, `MEDIUM`, `LOW` confidence; which `waste_dimensions` it affects — one or more of `cost`, `reliability`, `quality` (a single root cause is often more than one of these at once — e.g. a retry loop caused by a swallowed exception is simultaneously a cost issue and a reliability issue; tag both rather than picking one `waste_category`); and its own `evidence_type` of `measured` (a directly code- or trace-observed fact) vs. `estimated` (a projection, e.g. expected savings) — do not let one top-level `baseline.type` label stand in for every individual number in the report, since some facts within an "estimated" baseline can still be directly measured from the code itself.

`.agent-governance/token-report.json` should wrap findings in the shared
envelope from `agent-development-integrity`'s `references/evidence-schema.md`
(`status`, `scope_status`, `generated_at`, `source_revision`, `confidence`,
`limitations`) alongside `baseline.type` and the `findings[]` list described
above.

## Bundled Resources

- `scripts/analyze_usage.py`: summarize generic JSONL usage traces. **Tier R.**
- `scripts/prompt_inventory.py`: static inventory of prompt/context-heavy files when runtime data is unavailable. **Tier R.** This skill never makes a live model call itself; if a proposed change needs to be validated by re-running `agent-evaluator`'s eval set, that re-run is Tier M in that skill and needs its own approval.
- `references/optimization-checklist.md`: framework-neutral optimization patterns and anti-patterns.
- `references/trace-contract.md`: accepted trace fields and reporting contract.

## Version

```yaml
version: 0.2
frozen: true  # do not add new features until validated on a second, independent project
validated_on:
  - world_monitor_dashboard
key_lessons:
  - one root cause can be a cost issue and a reliability issue at once; tag both
  - measured vs estimated belongs on each finding, not only on the report as a whole
```
