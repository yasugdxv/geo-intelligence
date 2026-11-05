# Incident Log

Concrete, previously-observed failures this skill exists to catch. Read
these once to calibrate what the abstract checklist items in SKILL.md
actually look like in practice.

## Incident 1: worktree created from a stale base commit

An implementation task was dispatched via an "isolated worktree" tool
option, expected to branch from the current stable commit (`bd4dcd7`). It
was actually branched from a much older, unrelated commit (`bc8fb28`) that
was not even an ancestor of `bd4dcd7`. Nobody checked this before
implementation began.

Consequence: every file the implementing agent touched was edited on top of
a version missing 15-450+ lines of real, unrelated intervening work. A raw
`git diff <true-base> --stat` afterward showed 145 files changed — almost
all of it divergence noise, not real edits, because entire modules that
existed on the true baseline had simply never existed on the stale branch
and looked "deleted" in the diff.

**Lesson -> Base Revision Guard.** A single `git rev-parse HEAD` check
before implementation starts would have caught this in under a second.

## Incident 2: an implementing agent's self-report was confidently wrong, twice, for the same underlying reason

The same task's final report stated two things as established fact:
"the `send_email()` arity bug in `competitor_alert.py` doesn't actually
exist" and "`verification_status` is not wired into the production save
path." Both were false relative to the true baseline (independently
re-verified by reading the real code directly) — the first was wrong
because the agent's *own* refactor of `send_email()`'s signature had
incidentally made the old bug stop reproducing, and it mistook that for
evidence the bug never existed; the second was wrong because the agent was
reading a stale copy of the file (see Incident 1) that was missing the
relevant `save_change_event()` code.

Underneath, the agent also introduced a real regression that its own
summary did not surface as a regression: it silently dropped a
`to_addresses` parameter from `send_email()` and stopped threading the
already-fetched recipient list into it, meaning every email send would have
gone to zero recipients in production. This was found only by re-reading
the actual diff of that one function, not by trusting the summary.

**Lesson -> Diff Audit.** Both false claims were specific, checkable factual
assertions. Neither needed adversarial suspicion to catch — just re-reading
the same two files at the true baseline commit. A wrong claim from an
agent is often explained by exactly this kind of incidental self-reference
("I changed X, then observed X's new behavior, and reported that as if it
were the original state") — worth checking for specifically when a claim
seems surprising.

## Incident 3: the same class of bug recurred in an eval harness, independently, three times

Across a separate, correctly-based piece of work, three distinct harness
bugs were found over the course of building and running an evaluation
harness — a live-mode model-resolution bug that produced a confusing
Azure `404` error indistinguishable from a real config/infra problem; a
missing per-case exception boundary that caused one adversarial test case's
real content-filter rejection to crash an entire 10-case batch, losing all
results; and a deterministic check that silently compared a live-mode
result against a hardcoded dry-run fixture value instead of the real
captured output, making a passing case flicker between "pass" and "fail"
across runs for reasons that had nothing to do with the code under test.

**Lesson -> Harness Self-Check.** All three were caught by treating "the
harness reported X" as a claim to verify, not a fact — the same discipline
as Diff Audit, applied to test/eval infrastructure specifically. See
`agent-evaluator`'s own harness-self-check practice, which grew directly out
of this.

## Incident 4: a correctly-scoped implementation still had a hidden deploy risk

After a large but correctly-scoped and independently-verified set of
changes was merged locally, a later phase discovered that pushing the
local `main` branch to its remote automatically triggers a real production
deployment (`on: push: branches: [main]` in the repo's CI/CD workflows).
The next phase of work made the app *require* an external dependency
(Entra ID) that was not yet configured anywhere. Merging that work into
`main` too — the same way the previous, safe phase had been merged — would
have been fine for the *code*, but would have created a live landmine: any
later, unrelated `git push` of `main` by anyone would have deployed
auth-required code against an unconfigured environment, breaking the app
for all users.

**Lesson -> Two-Gate Model.** "Safe to merge" and "safe to push/deploy"
were, in this repository, two different real-world consequences hiding
behind what looked like one git operation. The team deliberately kept that
phase's work on a separate branch/tag until the external dependency was
actually configured, rather than treating "the code passed its own tests"
as sufficient grounds to integrate it into the branch that auto-deploys.
