#!/usr/bin/env python3
import argparse, json
from collections import defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", help="Result JSONL")
    ap.add_argument("--out")
    args = ap.parse_args()
    rows, errors = [], []
    for n, line in enumerate(Path(args.results).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {n}: {e}")
            continue
        rows.append(obj)
    metric_values = defaultdict(list)
    failed_checks, near_misses = [], []
    by_tag = defaultdict(lambda: {"cases": 0, "scores": defaultdict(list), "near_miss_ids": []})
    for r in rows:
        rid = r.get("id", "unknown")
        scores = r.get("scores") or {}
        for k, v in scores.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                metric_values[k].append(float(v))
        checks = r.get("deterministic_checks") or {}
        for k, v in checks.items():
            if v is False:
                failed_checks.append({"id": rid, "check": k})
        is_near_miss = r.get("near_miss") is True
        if is_near_miss:
            near_misses.append(rid)
        for tag in r.get("tags") or []:
            by_tag[tag]["cases"] += 1
            for k, v in scores.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    by_tag[tag]["scores"][k].append(float(v))
            if is_near_miss:
                by_tag[tag]["near_miss_ids"].append(rid)
    metrics = {k: round(sum(v)/len(v), 4) for k, v in metric_values.items() if v}

    # Failure-pattern clustering: group by *which check* failed, not just a flat
    # list of (id, check) pairs -- a check name that recurs across many cases is
    # a systemic failure mode worth fixing once, not N separate bugs to read
    # through one at a time.
    checks_by_name = defaultdict(list)
    for fc in failed_checks:
        checks_by_name[fc["check"]].append(fc["id"])
    failure_patterns = sorted(
        [{"check": name, "fail_count": len(ids), "case_ids": ids} for name, ids in checks_by_name.items()],
        key=lambda x: -x["fail_count"],
    )

    tag_summary = {
        tag: {
            "cases": d["cases"],
            "metrics": {k: round(sum(v)/len(v), 4) for k, v in d["scores"].items() if v},
            # Near-miss rate is tracked per tag/component, not just blended into one
            # global number -- a rare-but-severe near-miss in a security-sensitive tag
            # (e.g. "adversarial", "boundary") can otherwise be diluted by a large
            # happy-path case count elsewhere in the same suite.
            "near_miss_count": len(d["near_miss_ids"]),
            "near_miss_rate": round(len(d["near_miss_ids"]) / d["cases"], 4) if d["cases"] else None,
            "near_miss_ids": d["near_miss_ids"],
        }
        for tag, d in by_tag.items()
    }
    report = {
        "status": "complete" if not errors else "partial",
        "summary": {
            "cases": len(rows),
            "metrics": metrics,
            "failed_deterministic_checks": len(failed_checks),
            "near_miss_count": len(near_misses),
            "near_miss_rate": round(len(near_misses)/len(rows), 4) if rows else None,
        },
        "failed_checks": failed_checks,
        "failure_patterns": failure_patterns,
        "near_miss_ids": near_misses,
        "by_tag": tag_summary,
        "parse_errors": errors,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)

if __name__ == "__main__":
    main()
