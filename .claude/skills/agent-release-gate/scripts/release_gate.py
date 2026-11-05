#!/usr/bin/env python3
import argparse, json
from pathlib import Path

DEFAULT_REPO_POLICY_PATH = ".agent-governance/release-policy.json"


def load(path):
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))

def get(d, path, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur

def evidence_status(evidence: dict) -> str:
    """Classify why required evidence is/isn't satisfied, instead of one
    generic missing-or-incomplete bucket:
      - "complete": status == "complete", real evidence.
      - "deferred": explicitly declared deferral with a reason -- informational,
        not a hard blocker (still surfaced as a WARN with the reason).
      - "failed": evidence exists, ran, but did not meet the bar (caller decides
        this from specific metrics/findings, not this function).
      - "unknown": missing with no declared reason -- fail closed.
    """
    if not evidence:
        return "unknown"
    if evidence.get("status") == "complete":
        return "complete"
    if evidence.get("deferred_by_user_decision"):
        return "deferred"
    return "unknown"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy",
                     help=f"Policy JSON path. If omitted, auto-discovers {DEFAULT_REPO_POLICY_PATH} "
                          "(repo-specific policy convention); falls back to the bundled "
                          "references/default-policy.json if neither is found.")
    ap.add_argument("--security")
    ap.add_argument("--eval")
    ap.add_argument("--token")
    ap.add_argument("--integrity", help="Path to integrity-report.json (agent-development-integrity)")
    ap.add_argument("--current-revision",
                     help="The commit/ref actually being gated. If given, any evidence file whose "
                          "source_revision differs is flagged as stale evidence (a blocker), instead "
                          "of silently trusting a report generated against a different commit.")
    ap.add_argument("--out")
    args = ap.parse_args()

    policy_path = args.policy
    policy_source = "explicit --policy"
    if not policy_path:
        if Path(DEFAULT_REPO_POLICY_PATH).exists():
            policy_path = DEFAULT_REPO_POLICY_PATH
            policy_source = f"auto-discovered repo policy at {DEFAULT_REPO_POLICY_PATH}"
        else:
            policy_path = str(Path(__file__).parent.parent / "references" / "default-policy.json")
            policy_source = "bundled references/default-policy.json (no repo-specific policy found)"

    policy = load(policy_path)
    sec, ev, tok = load(args.security), load(args.eval), load(args.token)
    integrity = load(args.integrity)
    blockers, warnings = [], []

    # Development Integrity gate: a scope violation or wrong-base
    # implementation means the other evidence may not even be about the
    # code it claims to be about -- treat it as a hard blocker, same as a
    # P0/P1 security finding, not a soft note.
    if integrity:
        brg = integrity.get("base_revision_guard") or {}
        if brg.get("pass") is False:
            blockers.append(f"development integrity: base revision guard FAILED "
                             f"(head={brg.get('head')!r} expected_base={brg.get('expected_base')!r})")
        violations = get(integrity, "scope_guard.violations", []) or []
        if violations:
            blockers.append(f"development integrity: scope guard violation(s): {violations}")
    elif policy.get("development_integrity", {}).get("required"):
        blockers.append("required development-integrity evidence is unknown (not supplied)")

    # Staleness: an evidence file generated against a different commit than
    # the one actually being gated should not be silently trusted.
    if args.current_revision:
        for label, evidence in (("security", sec), ("evaluation", ev), ("token", tok), ("integrity", integrity)):
            rev = (evidence or {}).get("source_revision")
            if rev and rev != args.current_revision:
                blockers.append(f"{label} evidence is stale: generated against {rev!r}, "
                                 f"gating {args.current_revision!r}")

    sp = policy.get("security", {})
    sec_status = evidence_status(sec)
    if sp.get("required") and sec_status != "complete":
        if sec_status == "deferred":
            warnings.append(f"security evidence deferred: {(sec or {}).get('deferral_reason', 'no reason given')}")
        else:
            blockers.append(f"required security evidence is {sec_status} (not complete)")
    if sec:
        counts = get(sec, "summary.severity_counts", {}) or {}
        for sev in sp.get("block_severities", []):
            if counts.get(sev, 0) > 0:
                blockers.append(f"security {sev} findings: {counts.get(sev)}")

    ep = policy.get("evaluation", {})
    ev_status = evidence_status(ev)
    if ep.get("required") and ev_status != "complete":
        if ev_status == "deferred":
            warnings.append(f"evaluation evidence deferred: {(ev or {}).get('deferral_reason', 'no reason given')}")
        else:
            blockers.append(f"required evaluation evidence is {ev_status} (not complete)")
    if ev:
        cases = get(ev, "summary.cases", 0) or 0
        if cases < ep.get("min_cases", 0):
            blockers.append(f"eval cases {cases} < required {ep.get('min_cases')}")
        metrics = get(ev, "summary.metrics", {}) or {}
        for name, minimum in ep.get("metric_minimums", {}).items():
            actual = metrics.get(name)
            if actual is None:
                blockers.append(f"required eval metric missing: {name}")
            elif actual < minimum:
                blockers.append(f"eval metric {name}={actual:.3f} < {minimum:.3f}")
        failed = get(ev, "summary.failed_deterministic_checks", 0) or 0
        if failed > ep.get("max_failed_deterministic_checks", 0):
            blockers.append(f"failed deterministic checks: {failed}")
        nmr = get(ev, "summary.near_miss_rate")
        if nmr is not None and nmr > ep.get("max_near_miss_rate", 1.0):
            blockers.append(f"near_miss_rate={nmr:.3f} exceeds policy")

    tp = policy.get("token", {})
    if tp.get("required") and (not tok or tok.get("status") not in {"complete", "measured", "estimated"}):
        blockers.append("required token/cost evidence is missing")
    elif not tok:
        warnings.append("token/cost evidence not supplied")

    decision = "BLOCK" if blockers else ("WARN" if warnings else "PASS")
    report = {
        "decision": decision,
        "policy_used": {"path": policy_path, "source": policy_source},
        "blocking_reasons": blockers,
        "warnings": warnings,
        "evidence": {
            "security": bool(sec),
            "evaluation": bool(ev),
            "token": bool(tok),
            "integrity": bool(integrity)
        }
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(2 if decision == "BLOCK" else 0)

if __name__ == "__main__":
    main()
