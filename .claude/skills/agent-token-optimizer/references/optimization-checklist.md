# Token Optimization Checklist

## Highest-value checks

1. **Repeated static context** — large policy, schema, or reference text sent on every call without caching or retrieval.
2. **History growth** — full conversation replay when older turns can be summarized or selectively retained.
3. **Retrieval overfetch** — too many chunks, large chunks, weak filtering, duplicated passages, or full documents when a small evidence set is enough.
4. **Tool payload bloat** — raw HTML, logs, API responses, tables, or DB rows returned wholesale instead of normalized fields.
5. **Redundant calls** — repeated classification, translation, summarization, or validation of unchanged content.
6. **Multi-agent duplication** — the same context copied into several agents that could share a compact handoff object.
7. **Model over-provisioning** — high-cost model used for deterministic extraction, routing, tagging, formatting, or low-risk summarization.
8. **Retry amplification** — retries caused by weak schemas, invalid structured output, flaky tools, or overly aggressive timeouts.
9. **Missing cache boundaries** — stable long context not separated from volatile user/task content.
10. **Missing compaction** — long sessions grow until context is expensive or unstable.

## Safe optimization order

Prefer, in order:

1. Remove duplicate content.
2. Normalize/structure tool outputs.
3. Improve retrieval precision.
4. Cache stable context.
5. Compact old history with retained critical facts.
6. Collapse redundant LLM calls.
7. Route simple work to cheaper/smaller models.
8. Reduce output verbosity where the product does not need it.

Do not start by deleting domain instructions, citations, security policy, or evidence.

## Evidence requirements

A recommendation is **measured** when supported by observed traces or token counters. It is **estimated** when based on file size, prompt length, or architecture alone. Always label which one applies — **per finding, not only at the report's top level.** A report can be labeled `estimated` overall (no runtime trace data exists) while still containing individual facts that are directly `measured` from the code (e.g. "this function resends the full ~8000-char input on its second call" is a measured fact even inside an otherwise-estimated report); conflating the two under one blanket label understates the confidence of the parts that are directly verifiable.

## Waste dimensions

Tag each finding with every dimension it actually affects — `cost`, `reliability`, `quality` — rather than forcing a single `waste_category`. A retry loop triggered by an overly broad `except Exception` is a cost problem (extra tokens) and a reliability problem (real errors are being silently retried instead of surfaced) at the same time; recommendations that only address the cost framing can leave the reliability issue in place.
