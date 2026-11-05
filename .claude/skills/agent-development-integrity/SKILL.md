---
name: agent-development-integrity
description: Verify that an AI coding agent is working from the correct code base, stayed within its intended change scope, and preserved declared invariants, before trusting anything it reports. Use when asked "is this worktree/branch/diff scope correct", before dispatching any AI-driven implementation task on an isolated branch/worktree, right after that agent reports completion, and again before a merge/release decision. This is the FIRST governance skill in the pipeline (development-integrity -> security-review -> evaluator -> token-optimizer -> release-gate) whenever the work in question involved an AI agent writing code — run it before the other four have anything meaningful to review, since their evidence is only trustworthy if the code they're reviewing is what it's claimed to be.
---

# Agent Development Integrity

An implementation can be functionally correct and still be untrustworthy, if
nobody checked that the agent that wrote it was looking at the right code,
stayed in scope, and is being taken at its word rather than independently
re-checked. This skill exists because a real incident showed all three
failure modes at once: an isolated worktree was created from a stale commit
(not the intended base), the implementing agent's own summary confidently
asserted things ("this bug doesn't exist", "these test files don't exist")
that were false relative to the true baseline, and a 145-file diff had to be
manually untangled to find the ~15 files that were actually intentional.

Use this skill around any task where an AI agent (a subagent, a different
session, or a different tool) writes code on your behalf, especially on an
isolated branch or worktree. It has two entry points: **Base Revision Guard**
(run *before* dispatching implementation work) and **Post-Implementation
Verification** (run *after* the agent reports it is done, and again before
any merge/release decision).

## Base Revision Guard (run before implementation starts)

Before any agent writes a line of code on an isolated branch/worktree,
mechanically confirm it is rooted at the intended commit — do not trust that
a worktree/branch-creation tool picked the right base by default:

```
git rev-parse HEAD                              # must equal the intended base SHA
git merge-base --is-ancestor <intended-base> HEAD && echo OK   # must print OK
git status --short                               # must be empty
git diff --name-only <intended-base>              # must be empty
```

If any of these fail, **stop and do not implement** — report the mismatch
instead of proceeding on a guess. See
`scripts/base_revision_guard.py` for a script form of this check.
Re-run it again as the *first* thing inside the implementing agent's own
task too (a cheap, redundant check), since the dispatching session and the
implementing agent may not share the same trust boundary.

## Scope Guard (declare before, verify after)

**Before** implementation: write down the exact allowed-file list for the
task (which files/directories may change) and explicit non-goals (systems
that must not be touched). Give this to the implementing agent as part of
its brief, not as an afterthought.

**After** implementation: compare the actual diff against that list
mechanically, not by re-reading the agent's own claim of what it touched:

```
git diff <base> --name-only
```

or use `scripts/scope_guard_check.py <base> --allow file1 file2 ...` to get
a structured PASS/violation result (also usable non-interactively by
`agent-release-gate`, see `references/evidence-schema.md`). Every file outside the declared allowlist is a **scope violation** — surface
it explicitly, even if the change looks reasonable in isolation. A scope
violation is not automatically wrong, but it must be a deliberate, reported
decision (see the security-review skill's "justified exclusion" pattern),
never something absorbed silently into "looks fine, ship it."

## Behavioral Invariants

Before implementation, declare the specific existing behaviors that must
survive unchanged (a function signature, a data flow such as "recipient list
still reaches the send call", a security control, a config/schema
boundary). After implementation, re-verify each one by reading the actual
current code/diff — do not accept "tests still pass" alone as proof, since a
test suite can be silently weakened alongside a change. Prefer a positive,
literal check (e.g. `grep` the exact signature, trace the exact call chain)
over an inference from indirect evidence.

## Harness Self-Check (when the work involves an eval/test harness)

Delegate to `agent-evaluator`'s harness-self-check practice when the task
involves building or running an evaluation harness: config resolution,
model resolution, mock/live switching, side-effect isolation, production
path parity, and fail-fast validation must all be verified before trusting
anything the harness reports. A harness bug can look exactly like a
production bug (or vice versa) — see `references/incident-log.md` for a
worked example where this exact confusion occurred three separate times in
one session, each traced back to the harness, not the application.

## Diff Audit (never trust the agent's own summary)

An implementing agent's final report describes what it *believes* it did,
not a verified fact. Before accepting any claim in it:

- Re-derive the actual changed-file list yourself (`git diff <base> --name-only`),
  not from the agent's file list.
- For any specific factual claim relevant to a decision ("this bug doesn't
  exist", "this file doesn't exist", "the recipient list is unchanged"),
  re-check it directly against the base commit and the current code — the
  agent may have been reasoning from a different, stale, or partial view of
  the codebase than you assume it has.
- Independently re-run whatever the agent claims it ran (tests, a smoke
  test, a specific reproduction) rather than accepting a reported pass/fail
  count at face value when the decision matters.
- If a claim and your own direct check disagree, the direct check wins —
  and the disagreement itself is often diagnostic (see
  `references/incident-log.md`): a wrong claim is frequently explained by
  the agent having edited something in a way that incidentally made its own
  false premise look true (e.g. a refactor that happened to make an old bug
  no longer reproduce, mistaken for "the bug never existed").

This is not about distrust for its own sake — an agent that is later shown
to reliably survive this kind of audit earns faster review cycles. The
point is that the audit itself must happen at least once before a
consequential decision, not that it must be adversarial forever.

## Two-Gate Model: Code/Merge Gate vs. Production Readiness Gate

Treat "is this code correct and mergeable" as a **separate decision** from
"is it safe to actually deploy/operate this." A change can legitimately pass
the first and fail the second (e.g. code is correct but an external
dependency like an identity provider isn't configured yet). Keep both gates
explicit and separately recorded rather than collapsing them into one
"done" status.

**Before treating a merge as low-risk, check whether merging is actually the
deploy trigger.** In a CI/CD setup where pushing a branch (often `main`)
auto-deploys, "merge to main" and "deploy to production" can be the same
event — or, if the merge is local-only and a separate `push` step is what
triggers CI, they can be safely decoupled. Determine which is true for the
actual repository (read the CI/CD workflow files, don't assume) before
deciding whether merging now is safe while a Production Readiness Gate is
still failing. If merge and deploy are the same event, do not merge until
both gates pass; if they are separable, merging while deploy is deliberately
withheld is a reasonable way to keep code integrated without exposing the
gap.

## Output Contract

Write `.agent-governance/integrity-report.json` using the shared envelope
in `references/evidence-schema.md`, plus:

```json
{
  "status": "complete",
  "scope_status": "complete",
  "source_revision": "<HEAD at verification time>",
  "base_revision_guard": {"pass": true, "head": "...", "expected_base": "..."},
  "scope_guard": {"allowed_files": [], "actual_changed_files": [], "violations": []},
  "invariants_checked": [{"invariant": "send_email signature unchanged", "verified": true, "evidence": "file:line"}],
  "evidence": [],
  "limitations": []
}
```

A non-empty `scope_guard.violations` or any `base_revision_guard.pass: false`
should be treated by `agent-release-gate` as a blocking issue, the same way
a P0/P1 security finding is — a scope violation or wrong-base implementation
means the other 4 skills' evidence may not even be about the code you think
it's about.

## Bundled resources

- `scripts/base_revision_guard.py`: runs the 4 base-revision checks and
  exits non-zero with a clear message if any fail. **Tier R** (local `git`
  commands only).
- `scripts/scope_guard_check.py`: compares actual changed files against a
  declared allowlist, exits non-zero on any violation. **Tier R.**
- `references/incident-log.md`: the worked incident this skill is built
  from — read it once to calibrate how these failure modes actually look
  in practice, not just in the abstract.
- `references/scope-guard-template.md`: a fill-in-the-blanks template for
  declaring an allowed-file list and non-goals before dispatching
  implementation work.
- `references/evidence-schema.md`: the shared report envelope all 5
  governance skills use, so `agent-release-gate` can consume structured
  fields instead of interpreting prose.

## Version

```yaml
version: 0.2
frozen: true  # do not add new features until validated on a second, independent project
validated_on:
  - world_monitor_dashboard
key_lessons:
  - base revision guard (worktree/branch rooted at the wrong commit went undetected)
  - scope conformance (a 145-file diff had to be manually untangled to find ~15 real edits)
  - diff audit (an implementing agent confidently reported two false facts about the codebase)
  - two-gate release model (merge and deploy turned out to be the same event via CI/CD)
```
