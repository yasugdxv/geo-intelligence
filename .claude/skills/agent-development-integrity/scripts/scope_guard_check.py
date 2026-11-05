#!/usr/bin/env python3
"""Scope Guard: mechanically compare the actual changed-file set against a
declared allowlist. Exits 0 (no violations) or 2 (violations found, printed
explicitly) -- never silently absorbs an out-of-scope change.

Usage:
    python scope_guard_check.py <base-commit-ish> --allow file1 file2 ...
    python scope_guard_check.py <base-commit-ish> --allow-file allowlist.txt
"""
import argparse
import json
import subprocess
import sys


def changed_files(base: str) -> list:
    result = subprocess.run(["git", "diff", "--name-only", base], capture_output=True, text=True)
    tracked = [l for l in result.stdout.splitlines() if l.strip()]
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    untracked = [l[3:] for l in status.stdout.splitlines() if l.startswith("??")]
    # dedupe, keep order
    seen, out = set(), []
    for f in tracked + untracked:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="Base commit-ish to diff against")
    ap.add_argument("--allow", nargs="*", default=[], help="Allowed file paths (exact match)")
    ap.add_argument("--allow-file", help="File containing one allowed path per line")
    ap.add_argument("--out")
    args = ap.parse_args()

    allowed = set(args.allow)
    if args.allow_file:
        with open(args.allow_file, encoding="utf-8") as f:
            allowed |= {line.strip() for line in f if line.strip() and not line.startswith("#")}

    actual = changed_files(args.base)
    violations = [f for f in actual if f not in allowed]

    report = {
        "base": args.base,
        "allowed_files": sorted(allowed),
        "actual_changed_files": actual,
        "violations": violations,
        "pass": not violations,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        from pathlib import Path
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)

    if violations:
        print(f"\nSCOPE VIOLATION: {len(violations)} file(s) outside the declared allowlist:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
