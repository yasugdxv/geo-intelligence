#!/usr/bin/env python3
"""Render an eval-report.json (produced by aggregate_results.py) as a single,
self-contained, offline-viewable HTML file -- no external CSS/JS, no CDN,
safe to open directly from disk or attach to a ticket/email.

Tier: R (read-only, free -- only reads a local JSON file and writes a local
HTML file; no network, no live model calls).

Usage:
    python render_html_report.py eval-report.json --out eval-report.html
"""
import argparse
import html
import json
from pathlib import Path

CSS = """
body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0;
       padding: 24px; background: #f7f7f8; color: #1a1a1a; }
h1 { font-size: 20px; margin-bottom: 4px; }
.meta { color: #666; font-size: 13px; margin-bottom: 20px; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }
.card { background: white; border: 1px solid #e0e0e0; border-radius: 8px;
        padding: 12px 16px; min-width: 140px; }
.card .label { font-size: 12px; color: #666; }
.card .value { font-size: 22px; font-weight: 600; }
.card.warn .value { color: #b45309; }
.card.bad .value { color: #b91c1c; }
table { border-collapse: collapse; width: 100%; background: white;
        border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;
        margin-bottom: 20px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #eee;
         font-size: 13px; }
th { background: #fafafa; color: #444; }
tr:last-child td { border-bottom: none; }
.section-title { font-size: 15px; font-weight: 600; margin: 24px 0 8px; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px;
         font-size: 12px; }
.badge.pass { background: #dcfce7; color: #166534; }
.badge.fail { background: #fee2e2; color: #991b1b; }
code { background: #f1f1f1; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
"""


def esc(x) -> str:
    return html.escape(str(x))


def render(report: dict) -> str:
    summary = report.get("summary", {})
    metrics = summary.get("metrics", {})
    failure_patterns = report.get("failure_patterns", [])
    by_tag = report.get("by_tag", {})

    cards = [
        ("cases", summary.get("cases", 0), ""),
        ("failed deterministic checks", summary.get("failed_deterministic_checks", 0),
         "bad" if summary.get("failed_deterministic_checks") else ""),
        ("near-miss count", summary.get("near_miss_count", 0),
         "warn" if summary.get("near_miss_count") else ""),
        ("near-miss rate", summary.get("near_miss_rate"), ""),
    ]
    cards_html = "".join(
        f'<div class="card {cls}"><div class="label">{esc(label)}</div>'
        f'<div class="value">{esc(value) if value is not None else "-"}</div></div>'
        for label, value, cls in cards
    )

    metrics_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in metrics.items()
    ) or '<tr><td colspan="2">no metrics recorded</td></tr>'

    pattern_rows = "".join(
        f'<tr><td>{esc(p["check"])}</td><td>{p["fail_count"]}</td>'
        f'<td>{esc(", ".join(p["case_ids"]))}</td></tr>'
        for p in failure_patterns
    ) or '<tr><td colspan="3">no failed deterministic checks</td></tr>'

    tag_rows = ""
    for tag, d in by_tag.items():
        nm_badge = "fail" if d.get("near_miss_count") else "pass"
        tag_rows += (
            f"<tr><td>{esc(tag)}</td><td>{d.get('cases')}</td>"
            f'<td><span class="badge {nm_badge}">{d.get("near_miss_count", 0)} / '
            f'{d.get("near_miss_rate")}</span></td></tr>'
        )
    tag_rows = tag_rows or '<tr><td colspan="3">no tags recorded</td></tr>'

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Eval Report</title>
<style>{CSS}</style></head>
<body>
<h1>Agent Evaluation Report</h1>
<div class="meta">status: <code>{esc(report.get('status'))}</code>
 &middot; source_revision: <code>{esc(report.get('source_revision', 'n/a'))}</code>
 &middot; generated_at: <code>{esc(report.get('generated_at', 'n/a'))}</code></div>

<div class="cards">{cards_html}</div>

<div class="section-title">Metrics</div>
<table><tr><th>metric</th><th>average (0-1)</th></tr>{metrics_rows}</table>

<div class="section-title">Failure patterns (which check fails, and how often)</div>
<table><tr><th>check</th><th>fail count</th><th>affected case ids</th></tr>{pattern_rows}</table>

<div class="section-title">Near-miss rate by tag/component</div>
<table><tr><th>tag</th><th>cases</th><th>near-miss count / rate</th></tr>{tag_rows}</table>

</body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report", help="eval-report.json path")
    ap.add_argument("--out", default=None, help="Output HTML path (default: <report>.html)")
    args = ap.parse_args()

    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    out_path = Path(args.out) if args.out else report_path.with_suffix(".html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(report), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
