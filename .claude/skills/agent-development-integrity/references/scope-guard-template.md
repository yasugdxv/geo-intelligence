# Scope Guard Template

Fill this in before dispatching an implementation task, and paste the
"Allowed files" / "Non-goals" sections directly into the task brief. Check
the "After implementation" section once the agent reports completion.

## Before implementation

**Task:** _(one line — what is being implemented)_

**Base commit:** _(the exact SHA/ref this must be rooted at — see
Base Revision Guard)_

**Allowed files (categories, not just a flat list — an agent should be able
to reason about *why* a file is or isn't in scope, not just pattern-match):**
- _Category 1, e.g. "backend identity/config boundary": file, file, ..._
- _Category 2, e.g. "related tests": file, file, ..._
- _Category 3, e.g. "documentation, secret-free only": file, file, ..._

**Explicit non-goals (systems/files that must not change, even if touching
them would make the task easier):**
- _System/file 1 — why it's off-limits this round_
- _System/file 2 — why it's off-limits this round_

**Behavioral invariants (existing behavior that must survive unchanged —
be as literal as possible, e.g. an exact function signature or call chain,
not just "don't break X"):**
- _Invariant 1 — how to verify it after (e.g. "grep the exact signature",
  "trace call chain from A to B")_
- _Invariant 2 — how to verify it after_

## After implementation

Run, and record the actual output, not a paraphrase:

```
git diff <base-commit> --name-only
```

- [ ] Every changed file is inside the declared "Allowed files" list, OR is
      explicitly flagged by the agent as a scope exception with a stated
      reason (not silently included).
- [ ] Every declared behavioral invariant has been independently re-checked
      against the actual current code (not accepted on the agent's word,
      not accepted on "tests pass" alone).
- [ ] Any claim in the agent's report that a decision depends on has been
      independently re-verified against the true base commit (see Diff
      Audit in SKILL.md) — not just the parts that looked surprising.
- [ ] If this work is destined for a branch/commit that could be merged or
      pushed later: has it been checked whether that merge/push is also a
      deploy trigger for anything this work isn't ready for yet? (Two-Gate
      Model.)

Record the result (PASS / scope violation found / invariant broken) before
moving on to the next phase or a merge decision.
