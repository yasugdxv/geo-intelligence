#!/usr/bin/env python3
import argparse, json, os, re
from pathlib import Path

SKIP = {".git", "node_modules", ".venv", "venv", "dist", "build", ".next", "coverage"}
TEXT_EXTS = {".py", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml", ".md", ".env", ".ini", ".cfg"}
PATTERNS = {
    "outbound_url": re.compile(r"https?://[^\s\"'<>]+", re.I),
    "dangerous_exec": re.compile(r"\b(eval|exec|os\.system|subprocess\.(run|Popen|call)|child_process\.(exec|spawn))\b"),
    "tls_verification_disabled": re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false", re.I),
    "cors_wildcard": re.compile(r"allow_origins\s*=\s*\[?['\"]\*['\"]|Access-Control-Allow-Origin[^\n]*\*", re.I),
    "mcp_reference": re.compile(r"\bmcp\b", re.I),
    "auth_reference": re.compile(r"\b(auth|oauth|jwt|bearer|api[_-]?key|service[_-]?account|role|permission)\b", re.I),
    "tool_reference": re.compile(r"\b(tool|function_call|functioncall|agent)\b", re.I),
}
SECRET_ASSIGNMENT = re.compile(r"\b(secret|password|passwd|token|api[_-]?key|private[_-]?key)\b\s*[:=]", re.I)

def redacted_line(line):
    if SECRET_ASSIGNMENT.search(line):
        return "[REDACTED: secret-like line]"
    return line.strip()[:240]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--out")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    env_files, config_files, hits = [], [], []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".agent-governance")]
        for fn in files:
            p = Path(base) / fn
            rel = str(p.relative_to(root))
            low = fn.lower()
            if low.startswith(".env"):
                env_files.append(rel)
            if any(k in low for k in ("mcp", "auth", "security", "permission", "policy", "role", "agent")):
                config_files.append(rel)
            if p.suffix.lower() not in TEXT_EXTS and not low.startswith(".env"):
                continue
            try:
                if p.stat().st_size > 2_000_000:
                    continue
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, 1):
                for category, rx in PATTERNS.items():
                    if category in {"dangerous_exec", "tls_verification_disabled", "cors_wildcard"} and p.suffix.lower() in {".md", ".txt"}:
                        continue
                    if rx.search(line):
                        hits.append({"category": category, "path": rel, "line": idx, "preview": redacted_line(line)})
    report = {
        "note": "Static inventory only; hits are not vulnerability findings.",
        "root": str(root),
        "env_files": sorted(set(env_files)),
        "security_relevant_files": sorted(set(config_files)),
        "surface_hits": hits,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)

if __name__ == "__main__":
    main()
