# Usage Trace Contract

`analyze_usage.py` accepts one JSON object per line. Fields may be top-level or under `usage`.

Recognized fields:

- `request_id` / `id`
- `operation` / `step` / `agent`
- `model`
- `input_tokens` / `prompt_tokens`
- `output_tokens` / `completion_tokens`
- `cached_input_tokens` / `cache_read_input_tokens`
- `latency_ms` / `duration_ms`
- `cost_usd`

Unknown fields are ignored. Missing numeric fields are treated as unavailable, not as zero for claims about provider billing.

Recommended report path: `.agent-governance/token-report.json`.
