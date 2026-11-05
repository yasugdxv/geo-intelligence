#!/usr/bin/env python3
"""Governance Skills v0.2 regression suite.

Builds a small, throwaway fixture repo in a temp directory and deliberately
injects the six failure modes this project's own incidents were built from,
then confirms each governance skill script detects the intended one
correctly. This is a regression suite for the SKILLS, not for any
application -- it must never be run against a real project repo.

Usage: python run_regression_suite.py
Exits 0 if all 6 cases behave as expected, 1 otherwise.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parents[2]  # .../.claude/skills
DEV_INTEGRITY = SKILLS_ROOT / "agent-development-integrity" / "scripts"
SECURITY = SKILLS_ROOT / "agent-security-review" / "scripts"
EVALUATOR = SKILLS_ROOT / "agent-evaluator" / "scripts"
RELEASE_GATE = SKILLS_ROOT / "agent-release-gate" / "scripts"

results = []


def record(case, ok, detail):
    results.append((case, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {case}: {detail}")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def run_py(script, *args, cwd=None):
    return subprocess.run([sys.executable, str(script), *args], cwd=cwd, capture_output=True, text=True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="governance_fixture_"))
    repo = tmp / "fixture-repo"
    repo.mkdir()
    try:
        git("init", "-q", cwd=repo)
        git("config", "user.email", "fixture@example.com", cwd=repo)
        git("config", "user.name", "Fixture", cwd=repo)

        (repo / "app.py").write_text("def handler():\n    return 'v1'\n", encoding="utf-8")
        (repo / "unrelated.py").write_text("X = 1\n", encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", "commit0: initial", cwd=repo)
        commit0 = git("rev-parse", "HEAD", cwd=repo).stdout.strip()

        (repo / "app.py").write_text("def handler():\n    return 'v2 (intended base)'\n", encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "-q", "-m", "commit1: intended base", cwd=repo)
        commit1 = git("rev-parse", "HEAD", cwd=repo).stdout.strip()

        # ---- Case A: wrong base commit -> Base Revision Guard must BLOCK ----
        git("checkout", "-q", commit0, cwd=repo)  # simulates a worktree stuck on a stale base
        r = run_py(DEV_INTEGRITY / "base_revision_guard.py", commit1, cwd=repo)
        record("A: wrong base commit", r.returncode == 2 and "FAIL" in r.stdout,
               f"exit={r.returncode}, correctly rejected HEAD={commit0[:8]} != expected={commit1[:8]}")

        # Also confirm the correct-base case PASSes (not just that wrong-base fails)
        git("checkout", "-q", commit1, cwd=repo)
        r = run_py(DEV_INTEGRITY / "base_revision_guard.py", commit1, cwd=repo)
        record("A2: correct base commit (control)", r.returncode == 0 and "PASS" in r.stdout,
               f"exit={r.returncode}, correctly accepted matching base")

        # ---- Case B: change touches a file outside the allowlist ----
        (repo / "app.py").write_text("def handler():\n    return 'v3'\n", encoding="utf-8")
        (repo / "unrelated.py").write_text("X = 2  # should not have been touched\n", encoding="utf-8")
        r = run_py(DEV_INTEGRITY / "scope_guard_check.py", commit1, "--allow", "app.py", cwd=repo)
        report = json.loads(r.stdout)
        record("B: scope violation (unrelated.py touched, only app.py allowed)",
               r.returncode == 2 and report["violations"] == ["unrelated.py"],
               f"exit={r.returncode}, violations={report['violations']}")

        # ---- Case C: production-reachable pattern surfaces via the security scanner ----
        (repo / "handler_no_auth.py").write_text(
            "# no auth check here on purpose (fixture)\n"
            "def route_handler(request):\n"
            "    return read_sensitive_data()\n",
            encoding="utf-8",
        )
        out_path = tmp / "surface.json"
        r = run_py(SECURITY / "scan_security_surface.py", str(repo), "--out", str(out_path))
        surface = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
        hit_files = {h["path"] for h in surface.get("surface_hits", [])}
        record("C: security-surface scanner flags the fixture file",
               any("handler_no_auth.py" in p for p in hit_files),
               f"surface_hits touched handler_no_auth.py: {'handler_no_auth.py' in str(hit_files)}")

        # ---- Case D: deterministic PASS but near_miss=true must still surface ----
        eval_jsonl = tmp / "eval_results.jsonl"
        eval_jsonl.write_text(
            json.dumps({"id": "case-1", "tags": ["adversarial"],
                        "deterministic_checks": {"no_unauthorized_write": True},
                        "scores": {"task_success": 1.0}, "near_miss": True}) + "\n",
            encoding="utf-8",
        )
        agg_out = tmp / "eval-report.json"
        r = run_py(EVALUATOR / "aggregate_results.py", str(eval_jsonl), "--out", str(agg_out))
        agg = json.loads(agg_out.read_text(encoding="utf-8"))
        record("D: all deterministic checks pass, but near_miss=true is not hidden",
               agg["summary"]["failed_deterministic_checks"] == 0 and agg["summary"]["near_miss_count"] == 1,
               f"failed_checks=0, near_miss_count={agg['summary']['near_miss_count']} "
               f"(per-tag: {agg['by_tag'].get('adversarial', {}).get('near_miss_count')})")

        # ---- Case E: declared deferral -> WARN, undeclared gap -> BLOCK ----
        deferred_eval = tmp / "eval_deferred.json"
        deferred_eval.write_text(json.dumps({
            "status": "designed_not_run", "deferred_by_user_decision": True,
            "deferral_reason": "live LLM execution deferred pending cost approval",
            "summary": {"cases": 0, "metrics": {}},
        }), encoding="utf-8")
        clean_security = tmp / "sec_clean.json"
        clean_security.write_text(json.dumps({
            "status": "complete", "summary": {"severity_counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0}},
        }), encoding="utf-8")
        r_deferred = run_py(RELEASE_GATE / "release_gate.py",
                             "--policy", str(SKILLS_ROOT / "agent-release-gate" / "references" / "default-policy.json"),
                             "--security", str(clean_security), "--eval", str(deferred_eval))
        deferred_report = json.loads(r_deferred.stdout)
        # A declared deferral must surface a specific WARN reason and must NOT be
        # reported via the old generic "is unknown/missing" phrasing -- it may still
        # legitimately BLOCK for OTHER reasons (e.g. this fixture's 0 cases genuinely
        # doesn't meet the policy's min_cases=3 floor; deferring evidence-gathering
        # doesn't exempt it from that separate, real threshold).
        deferred_ok = any("deferred" in w for w in deferred_report.get("warnings", [])) and \
            not any("is unknown (not complete)" in b for b in deferred_report.get("blocking_reasons", []))

        unknown_eval = tmp / "eval_unknown.json"
        unknown_eval.write_text(json.dumps({"status": "partial", "summary": {"cases": 0, "metrics": {}}}),
                                 encoding="utf-8")
        r_unknown = run_py(RELEASE_GATE / "release_gate.py",
                            "--policy", str(SKILLS_ROOT / "agent-release-gate" / "references" / "default-policy.json"),
                            "--security", str(clean_security), "--eval", str(unknown_eval))
        unknown_report = json.loads(r_unknown.stdout)
        unknown_ok = any("required evaluation evidence is unknown" in b for b in unknown_report.get("blocking_reasons", []))
        record("E: declared deferral -> specific reason surfaced (not generic 'unknown'); undeclared gap -> BLOCK with generic reason",
               deferred_ok and unknown_ok,
               f"deferred_decision={deferred_report['decision']} (still BLOCKs here on the separate, real "
               f"min_cases=3 threshold -- that's correct, deferral isn't a threshold exemption), "
               f"unknown_decision={unknown_report['decision']}")

        # ---- Case F: Code Gate PASS + Production Gate BLOCK are separate runs ----
        good_eval = tmp / "eval_good.json"
        good_eval.write_text(json.dumps({
            "status": "complete",
            "summary": {"cases": 5, "metrics": {"task_success": 0.95, "grounding": 0.95, "safety": 0.99},
                        "failed_deterministic_checks": 0, "near_miss_rate": 0.0},
        }), encoding="utf-8")
        code_gate_policy = tmp / "code_gate_policy.json"
        code_gate_policy.write_text(json.dumps({
            "security": {"required": True, "block_severities": ["P0", "P1"]},
            "evaluation": {"required": True, "min_cases": 3,
                           "metric_minimums": {"task_success": 0.9, "grounding": 0.9, "safety": 0.95},
                           "max_failed_deterministic_checks": 0, "max_near_miss_rate": 0.05},
            "token": {"required": False},
        }), encoding="utf-8")
        clean_token = tmp / "token_clean.json"
        clean_token.write_text(json.dumps({"status": "estimated", "baseline": {"type": "estimated"}}), encoding="utf-8")
        r_code = run_py(RELEASE_GATE / "release_gate.py", "--policy", str(code_gate_policy),
                         "--security", str(clean_security), "--eval", str(good_eval),
                         "--token", str(clean_token))
        code_gate_report = json.loads(r_code.stdout)

        prod_readiness = tmp / "prod_readiness.json"
        prod_readiness.write_text(json.dumps({
            "status": "partial",
            "summary": {"cases": 0, "metrics": {}},  # stands in for e.g. "Entra ID not configured"
        }), encoding="utf-8")
        prod_gate_policy = tmp / "prod_gate_policy.json"
        prod_gate_policy.write_text(json.dumps({
            "security": {"required": True, "block_severities": ["P0", "P1"]},
            "evaluation": {"required": True, "min_cases": 1, "metric_minimums": {}, "max_failed_deterministic_checks": 0},
            "token": {"required": False},
        }), encoding="utf-8")
        r_prod = run_py(RELEASE_GATE / "release_gate.py", "--policy", str(prod_gate_policy),
                         "--security", str(clean_security), "--eval", str(prod_readiness))
        prod_gate_report = json.loads(r_prod.stdout)
        record("F: Code Gate and Production Gate are independent decisions",
               code_gate_report["decision"] == "PASS" and prod_gate_report["decision"] == "BLOCK",
               f"code_gate={code_gate_report['decision']}, production_gate={prod_gate_report['decision']}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    n_pass = sum(1 for _, ok, _ in results if ok)
    print(f"{n_pass}/{len(results)} cases behaved as expected")
    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    main()
