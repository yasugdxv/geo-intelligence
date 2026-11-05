#!/usr/bin/env python3
"""Base Revision Guard: confirm a worktree/branch is rooted at the intended
commit before any AI-driven implementation work begins on it.

Exits 0 and prints PASS with all four checks if everything matches. Exits 2
and prints the specific failing check(s) otherwise -- the caller should
treat that as "stop, do not implement" rather than a soft warning.

Usage:
    python base_revision_guard.py <expected-base-commit-ish>
"""
import argparse
import subprocess
import sys


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("expected_base", help="Commit-ish the current checkout must be rooted at")
    args = ap.parse_args()

    problems = []

    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    expected = run(["git", "rev-parse", args.expected_base]).stdout.strip()
    if not expected:
        problems.append(f"could not resolve expected_base {args.expected_base!r} to a commit")
    elif head != expected:
        problems.append(f"HEAD={head} does not equal expected_base {args.expected_base!r} ({expected})")

    ancestor = run(["git", "merge-base", "--is-ancestor", args.expected_base, "HEAD"])
    if ancestor.returncode != 0:
        problems.append(f"expected_base {args.expected_base!r} is NOT an ancestor of HEAD "
                         "(this worktree/branch was likely created from the wrong starting point)")

    status = run(["git", "status", "--short"]).stdout
    if status.strip():
        problems.append("git status --short is not empty:\n" + status)

    diff_names = run(["git", "diff", "--name-only", args.expected_base]).stdout
    if diff_names.strip():
        problems.append(f"git diff --name-only {args.expected_base} is not empty "
                         "(unexpected pre-existing changes before implementation has even started):\n"
                         + diff_names)

    if problems:
        print("BASE REVISION GUARD: FAIL -- do not proceed with implementation.\n")
        for p in problems:
            print("-", p)
        sys.exit(2)

    print("BASE REVISION GUARD: PASS")
    print(f"  HEAD == expected_base == {head}")
    print("  ancestor check OK, git status clean, zero pre-existing diff")


if __name__ == "__main__":
    main()
