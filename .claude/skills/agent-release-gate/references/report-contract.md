# Release Gate Report Contract

## Security report

Expected fields:

- `status`
- `summary.severity_counts.P0/P1/P2/P3`
- `findings[]`

## Eval report

Expected fields:

- `status`
- `summary.cases`
- `summary.metrics.<metric>`
- `summary.failed_deterministic_checks`
- `summary.near_miss_rate`

## Token report

The default gate treats token evidence as advisory. If a local policy makes it required, the file must exist and have `status` plus a `summary` object. Add organization-specific thresholds to the policy and extend the gate script if a deterministic blocking rule is needed.

## Status

A report with `status: partial` does not satisfy a required evidence gate.
