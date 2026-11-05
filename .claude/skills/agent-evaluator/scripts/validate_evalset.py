#!/usr/bin/env python3
import argparse, json
from pathlib import Path

REQUIRED = {"id", "input", "expected_outcome"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("evalset")
    args = ap.parse_args()
    ids, errors, count = set(), [], 0
    for n, line in enumerate(Path(args.evalset).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        count += 1
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {n}: invalid JSON: {e}")
            continue
        if not isinstance(obj, dict):
            errors.append(f"line {n}: must be an object")
            continue
        missing = REQUIRED - obj.keys()
        if missing:
            errors.append(f"line {n}: missing {sorted(missing)}")
        cid = obj.get("id")
        if cid in ids:
            errors.append(f"line {n}: duplicate id {cid}")
        ids.add(cid)
        if "tags" in obj and not isinstance(obj["tags"], list):
            errors.append(f"line {n}: tags must be a list")
        if "rubrics" in obj and not isinstance(obj["rubrics"], list):
            errors.append(f"line {n}: rubrics must be a list")
    print(json.dumps({"valid": not errors, "cases": count, "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)

if __name__ == "__main__":
    main()
