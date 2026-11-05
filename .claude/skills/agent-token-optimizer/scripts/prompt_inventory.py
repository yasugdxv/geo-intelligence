#!/usr/bin/env python3
import argparse, json, os
from pathlib import Path

EXTS = {".md", ".txt", ".yaml", ".yml", ".json", ".py", ".js", ".ts", ".tsx"}
SKIP = {".git", "node_modules", ".venv", "venv", "dist", "build", ".next", "coverage"}
KEYWORDS = ("prompt", "system", "instruction", "context", "retriev", "message", "agent", "tool")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--min-chars", type=int, default=4000)
    ap.add_argument("--out")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    findings = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".agent-governance")]
        for fn in files:
            p = Path(base) / fn
            if p.suffix.lower() not in EXTS:
                continue
            try:
                if p.stat().st_size > 2_000_000:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            low = (fn + "\n" + text[:2000]).lower()
            if len(text) >= args.min_chars and any(k in low for k in KEYWORDS):
                findings.append({
                    "path": str(p.relative_to(root)),
                    "chars": len(text),
                    "approx_tokens": round(len(text) / 4),
                    "lines": text.count("\n") + 1,
                })
    findings.sort(key=lambda x: x["chars"], reverse=True)
    report = {"status": "estimated", "root": str(root), "large_prompt_context_candidates": findings}
    out = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    print(out)

if __name__ == "__main__":
    main()
