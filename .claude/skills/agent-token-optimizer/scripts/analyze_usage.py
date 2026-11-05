#!/usr/bin/env python3
import argparse, json, math, statistics
from collections import defaultdict
from pathlib import Path

ALIASES = {
    "input_tokens": ["input_tokens", "prompt_tokens"],
    "output_tokens": ["output_tokens", "completion_tokens"],
    "cached_input_tokens": ["cached_input_tokens", "cache_read_input_tokens"],
    "latency_ms": ["latency_ms", "duration_ms"],
    "cost_usd": ["cost_usd"],
}

def get_nested(obj, key):
    if key in obj:
        return obj[key]
    usage = obj.get("usage")
    if isinstance(usage, dict) and key in usage:
        return usage[key]
    return None

def get_num(obj, name):
    for key in ALIASES[name]:
        v = get_nested(obj, key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None

def pct(values, p):
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    idx = (len(vals) - 1) * p
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (idx - lo)

def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        operation = r.get("operation") or r.get("step") or r.get("agent") or "unknown"
        model = r.get("model") or "unknown"
        groups[(operation, model)].append(r)

    def metrics(items):
        ins = [v for r in items if (v := get_num(r, "input_tokens")) is not None]
        outs = [v for r in items if (v := get_num(r, "output_tokens")) is not None]
        cached = [v for r in items if (v := get_num(r, "cached_input_tokens")) is not None]
        lat = [v for r in items if (v := get_num(r, "latency_ms")) is not None]
        costs = [v for r in items if (v := get_num(r, "cost_usd")) is not None]
        return {
            "calls": len(items),
            "input_tokens": sum(ins) if ins else None,
            "output_tokens": sum(outs) if outs else None,
            "cached_input_tokens": sum(cached) if cached else None,
            "total_tokens": (sum(ins) + sum(outs)) if ins or outs else None,
            "latency_ms_p50": pct(lat, 0.50),
            "latency_ms_p95": pct(lat, 0.95),
            "cost_usd": round(sum(costs), 8) if costs else None,
        }

    overall = metrics(rows)
    by_operation_model = []
    for (operation, model), items in sorted(groups.items()):
        by_operation_model.append({"operation": operation, "model": model, **metrics(items)})
    return {"status": "measured", "summary": overall, "by_operation_model": by_operation_model}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace", help="JSONL trace file")
    ap.add_argument("--out", help="Output JSON path")
    args = ap.parse_args()
    rows = []
    for i, line in enumerate(Path(args.trace).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"invalid JSON on line {i}: {e}")
        if not isinstance(obj, dict):
            raise SystemExit(f"line {i} is not a JSON object")
        rows.append(obj)
    report = summarize(rows)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)

if __name__ == "__main__":
    main()
