"""Limited Live Intelligence Pilot の事前準備（Production Scheduler未配備の間の運用手順）。

  1. Direct Collection Manual Run   … UKMTO / RSS＋HTML list / Federal Register（--with-sanctions で制裁リストも）
  2. Freshness確認                  … Coverage別 READY / WARNING / BLOCKED と、Pilot Themeごとの Freshness・Coverage
  3. Pilot Case Sheet生成           … 実データから、評価者向けの事例シート（Markdown）を作る

使い方（projects/geopolitical_monitor_dashboard/ で）:
    python scripts/geo_pilot_preflight.py                 # 1〜3を実行
    python scripts/geo_pilot_preflight.py --skip-collect  # 収集せず、2〜3だけ
    python scripts/geo_pilot_preflight.py --with-sanctions
「手動実行されていないためstale」は正しい挙動。staleのまま「変化なし」と扱わない。GDELT停止中はBroad Mediaを評価しない。
DBへの書き込みは収集（1）のみ。2〜3は読み取り（Monitoringはpersist=Falseで評価）。
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import geo_connector_federal_register as fr_conn            # noqa: E402
import geo_connector_html_list as html_conn                 # noqa: E402
import geo_connector_rss as rss_conn                        # noqa: E402
import geo_connector_sanctions_xml as sanc_conn             # noqa: E402
import geo_connector_ukmto as ukmto_conn                    # noqa: E402
import geo_direct_collection_common as common               # noqa: E402
import geo_monitoring_theme_service as mon                  # noqa: E402
import geo_source_library_service as lib                    # noqa: E402
import geo_theme_coverage_service as cov                    # noqa: E402
from config_utils import load_config                        # noqa: E402
from geo_db_client import SupabaseClient                    # noqa: E402

STATUS_ICON = {"fresh": "●", "partial": "◐", "stale": "▲", "unknown": "?", "not_configured": "○"}

# Pilotの評価対象版。Pilot中はこの版（Frozen）を変更しない。評価結果と実装変更を混ぜないため、作業ツリーが対象版と一致するかを毎回確認する
TARGET_VERSION = "GEO_DIRECT_COLLECTION_V1_2"
FREEZE_DIR = ROOT / "freeze" / TARGET_VERSION


def verify_target_version() -> dict:
    """freeze/<TARGET_VERSION>/ のSHA256SUMS（src/配下）と、現行の作業ツリーのファイルを照合する。差分があれば評価は対象版のものではない。"""
    import hashlib
    sums = FREEZE_DIR / "SHA256SUMS.txt"
    if not sums.exists():
        return {"target": TARGET_VERSION, "frozen": False, "drift": [], "manifest_sha256": None, "files": 0}
    drift, n = [], 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        h, rel = line.split(" *", 1)
        rel = rel[2:] if rel.startswith("./") else rel
        if not rel.startswith("src/"):
            continue
        n += 1
        orig = ROOT / rel[4:]
        if not orig.exists() or hashlib.sha256(orig.read_bytes()).hexdigest() != h:
            drift.append(rel[4:])
    m = FREEZE_DIR / "MANIFEST.sha256"
    return {"target": TARGET_VERSION, "frozen": True, "drift": drift, "files": n,
            "manifest_sha256": m.read_text(encoding="utf-8").split()[0] if m.exists() else None}


def case_gates(client, live: dict, now) -> dict:
    """Caseごとの Preflight Gate（EVALUABLE / NOT EVALUABLE）。決定論。Sourceがstaleなら、無理にLive Caseとして評価しない。
    Case 3は、監視窓内のRelevant Articleが0件でも失敗扱いにしない（0件を『変化なし』と誤認しないかも評価対象）。"""
    src_status = {s["name"]: s["status"] for a in live["areas"] for s in a["sources"]}
    by_domain = {}
    for a in live["areas"]:
        for s in a["sources"]:
            by_domain[s.get("source_id")] = s

    def st(domain: str):
        try:
            row = common.resolve_source(client, domain)
        except common.SourceNotReadyError as e:
            return {"status": "unknown", "name": domain, "reason": str(e)}
        return by_domain.get(row["source_id"]) or {"status": "unknown", "name": row["name"], "reason": "鮮度情報なし"}
    gates = {}
    ukm, cmf, mae = st("sccd.royalnavy.mod.uk"), st("combinedmaritimeforces.com"), st("maersk.com")
    reasons, caveats = [], []
    if ukm["status"] != "fresh":
        reasons.append(f"Official / Maritime Advisory（UKMTO）が最新でない（{ukm['status']}: {ukm.get('reason')}）")
    for nm, x in (("Military / Security（CMF）", cmf), ("Shipping / Commercial（Maersk）", mae)):
        if x["status"] != "fresh":
            caveats.append(f"{nm}が最新でない（{x['status']}）→この種類は評価対象外として扱う")
    gates["case1_hormuz"] = {"gate": "EVALUABLE" if not reasons else "NOT EVALUABLE", "reasons": reasons, "caveats": caveats,
                             "sources": {"UKMTO": ukm["status"], "CMF": cmf["status"], "Maersk": mae["status"]}}
    fr = st("federalregister.gov")
    gates["case2_econ_security"] = {"gate": "EVALUABLE" if fr["status"] == "fresh" else "NOT EVALUABLE",
                                    "reasons": [] if fr["status"] == "fresh" else [f"Federal Registerが最新でない（{fr['status']}: {fr.get('reason')}）"], "caveats": [],
                                    "sources": {"Federal Register": fr["status"]}}
    mod = st("mod.go.jp")
    js = client.select("geo_external_signals", {"select": "last_observed_at", "provider": "eq.official", "source_domain": "eq.mod.go.jp",
                                                "raw_metadata->>origin_section": "eq.統合幕僚監部", "order": "last_observed_at.desc", "limit": "1"})
    js_ok = bool(js) and (now - lib._parse_ts(js[0]["last_observed_at"])) < timedelta(hours=24)
    r3 = []
    if mod["status"] != "fresh":
        r3.append(f"防衛省（RSS）が最新でない（{mod['status']}: {mod.get('reason')}）")
    if not js_ok:
        r3.append("統合幕僚監部（HTML list）が直近24時間に取得されていない")
    gates["case3_taiwan"] = {"gate": "EVALUABLE" if not r3 else "NOT EVALUABLE", "reasons": r3,
                             "caveats": ["監視窓内のRelevant Articleが0件でも失敗扱いにしない（0件を変化なしと誤認しないかも評価する）"],
                             "sources": {"防衛省(RSS)": mod["status"], "統合幕僚監部(HTML)": "fresh" if js_ok else "stale"}}
    return gates


def step_collect(client, with_sanctions: bool) -> list:
    results = []

    def run(name, fn):
        try:
            r = fn()
            results.append((name, r.get("status", "ok") if isinstance(r, dict) else "ok", r))
        except Exception as e:  # noqa: BLE001 — 1つの失敗で他を止めない。失敗は結果に残す
            results.append((name, f"FAILED [{getattr(e, 'kind', type(e).__name__)}] {str(e)[:100]}", None))
    run("UKMTO", lambda: ukmto_conn.run_ukmto_collection(client, requested_by="pilot-preflight"))
    run("RSS（防衛省/IAEA/WTO/CMF/UN News/EIA/USTR）", lambda: {"status": "completed", "sources": rss_conn.run_all_rss_sources(client, requested_by="pilot-preflight")})
    run("HTML list（統合幕僚監部/Maersk）", lambda: {"status": "completed", "sources": html_conn.run_all_html_list_sources(client, requested_by="pilot-preflight")})
    run("Federal Register", lambda: fr_conn.run_federal_register_collection(client, requested_by="pilot-preflight"))
    if with_sanctions:
        for key in ("un_consolidated", "uk_fcdo"):
            run(f"制裁リスト {key}", lambda key=key: sanc_conn.run_sanctions_collection(client, list_key=key, requested_by="pilot-preflight"))
    return results


def md_theme_block(client, theme_name_prefix: str, now) -> dict:
    t = next(x for x in mon.list_monitoring_themes(client)["themes"] if x["display_name"].startswith(theme_name_prefix))
    th = mon.get_monitoring_theme(client, t["monitoring_theme_id"])
    r = mon.assess(client, th, now=now, persist=False)
    return {"theme": th, "assessment": r["assessment"], "articles": r["articles"], "alerts": r["alerts_detected"]}


def coverage_lines(a: dict) -> list:
    lines = []
    for c in a["coverage"]["components"]:
        names = ", ".join(f"{s['name'].split('（')[0]}={s['status']}" for s in c["sources"]) or "（未設定）"
        lines.append(f"- {STATUS_ICON.get(c['status'], '?')} **{c['label']}**: {c['status']} — {names}")
    return lines


def write_case_sheets(client, now, out_path: Path, collect_results: list, live: dict) -> None:
    tv = verify_target_version()
    gates = case_gates(client, live, now)
    L = [f"# Limited Live Intelligence Pilot — Case Sheets（{now.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M JST')} 生成）", "",
         f"**Target Version: `{TARGET_VERSION}`**（Frozen。Pilot中は変更しない）／ Manifest SHA256: `{tv['manifest_sha256']}` ／ 対象ファイル {tv['files']}件 ／ "
         + ("**作業ツリーは対象版と一致**" if tv["frozen"] and not tv["drift"] else f"**⚠ 作業ツリーが対象版と一致しない: {tv['drift'] or 'Freeze未作成'}（この評価は対象版のものではない）**"), "",
         "> 実データから自動生成。**評価するのはDirect Sourceでカバーされる範囲のみ**。GDELT（Broad Media）停止中は、Broad Mediaの網羅性・速報性・Media由来のAlert・今日のニュース全体の網羅性を**評価しない**。",
         "> 「手動実行されていないためstale」は正しい挙動。Pilot前に Manual Run → Freshness確認 を済ませてから評価する。", ""]
    L += ["## 0. Pilot前の確認（Manual Run → Freshness）", ""]
    if collect_results:
        L += ["| 取得 | 結果 |", "|---|---|"] + [f"| {n} | {s} |" for n, s, _ in collect_results] + [""]
    L += ["### Preflight Gate（Caseごと。staleなら無理にLive Caseとして評価しない）", "", "| Case | Gate | 理由 / 注意 |", "|---|---|---|"]
    names = {"case1_hormuz": "Case 1 Hormuz / Middle East Maritime", "case2_econ_security": "Case 2 US Economic Security / Export Control", "case3_taiwan": "Case 3 Taiwan / Japan Defense"}
    for k, g in gates.items():
        L.append(f"| {names[k]} | **{g['gate']}** | {'; '.join(g['reasons'] + g['caveats']) or '-'}（{g['sources']}） |")
    L += ["", "### Coverage別 Live Intelligence Status", "", "| 領域 | Status | Pilot評価 | 備考 |", "|---|---|---|---|"]
    for a in live["areas"]:
        L.append(f"| {a['label']} | **{a['status']}** | {'評価する' if a['pilot_evaluable'] else '未評価（BLOCKED）'} | {a['reason'][:120]} |")
    L += ["", f"READY {live['counts']['READY']} / WARNING {live['counts']['WARNING']} / BLOCKED {live['counts']['BLOCKED']}。**全体を1つのREADY/BLOCKEDにしない**。", ""]

    # --- Case 1: Hormuz / UKMTO ---
    h = md_theme_block(client, "ホルムズ", now); ha = h["assessment"]
    ukm = common.resolve_source(client, "sccd.royalnavy.mod.uk")
    rows = client.select("geo_external_signals", {"select": "title,summary,published_at,url,raw_metadata", "source_id": f"eq.{ukm['source_id']}", "order": "published_at.desc", "limit": "400"})
    hz = [r for r in rows if mon._keyword_hit(h["theme"], r)]
    br = {}
    for r in hz:
        k = mon.classify_maritime_region(r) or "unclassified"; br[k] = br.get(k, 0) + 1
    L += ["## Pilot Case 1: Hormuz / UKMTO（Official / Maritime Advisory）", "",
          f"- Theme: {h['theme']['display_name']}（`/geo/monitoring-themes/{h['theme']['monitoring_theme_id']}`）",
          f"- **Freshness: {ha['freshness_state']}** ／ **State: {ha['state']}**（freshness ≠ state。stableとは判定されない）／ Alert候補 {ha['validation']['candidates']} ／ Alert {len(h['alerts'])}",
          f"- UKMTO: 取得 {len(rows)}件、Hormuz Theme関連 {len(hz)}件。海域別: {br}", "- Coverage panel:"] + ["  " + x for x in coverage_lines(ha)] + [
          f"- 注記: {ha['coverage_note']}", "", "**直近のUKMTO incident（Evidence）**", "", "| 発生(UTC) | Incident | 海域 | Baseline | URL |", "|---|---|---|---|---|"]
    for r in hz[:8]:
        m = r["raw_metadata"]
        L.append(f"| {r['published_at'][:16]} | {r['title']} | {mon.classify_maritime_region(r) or '-'} | {'baseline' if m.get('baseline') else 'new/updated'} | {r['url']} |")
    L += ["", "**評価の観点**: 最新incidentに気づけるか／Baseline（初回53件）でAlertが大量発生していないか／Coverage panelで「どこが最新でどこが欠けるか（Shipping=Maersk、Specialist Media=未設定、Broad Media=停止）」が分かるか／Theme DetailからEvidence（元ページ）へ行けるか／Expert AIへつなぐ価値（`Expert AIで読む`）。", ""]

    # --- Case 2: US Economic Security / Federal Register ---
    s = md_theme_block(client, "米中半導体", now); sa = s["assessment"]
    fr = common.resolve_source(client, "federalregister.gov")
    frows = client.select("geo_external_signals", {"select": "title,published_at,url,agencies:raw_metadata->agencies", "source_id": f"eq.{fr['source_id']}", "order": "published_at.desc", "limit": "100"})
    bis = [r for r in frows if "Industry and Security Bureau" in (r.get("agencies") or [])]
    L += ["## Pilot Case 2: US Economic Security / Federal Register（BIS）", "",
          f"- Theme: {s['theme']['display_name']}（`/geo/monitoring-themes/{s['theme']['monitoring_theme_id']}`）",
          f"- **Freshness: {sa['freshness_state']}** ／ **State: {sa['state']}** ／ 関連記事 {sa['articles_in_window']}（7日窓） ／ Alert候補 {sa['validation']['candidates']} ／ Alert {len(s['alerts'])}",
          f"- Federal Register 取得 {len(frows)}件、うちBIS機関（`agency:Industry and Security Bureau`、構造化メタデータで判定）{len(bis)}件", "- Coverage panel:"] + ["  " + x for x in coverage_lines(sa)] + [
          f"- 注記: {sa['coverage_note']}", "", "**BIS関連document（Evidence）**", "", "| 公開日 | Title | URL |", "|---|---|---|"]
    for r in bis[:8]:
        L.append(f"| {r['published_at'][:10]} | {r['title'][:90]} | {r['url']} |")
    L += ["", "**評価の観点**: BIS文書を本文に『BIS』が無くても機関で捕捉できているか／Theme relevance（FTZ等、弱い関連が混ざらないか）／EvidenceからResearch・Expert AIへ進めるか。", ""]

    # --- Case 3: Taiwan / Japan Defense ---
    tw = md_theme_block(client, "台湾海峡", now); ta = tw["assessment"]
    mod = common.resolve_source(client, "mod.go.jp")
    jrows = client.select("geo_external_signals", {"select": "title,published_at,url,raw_metadata", "source_id": f"eq.{mod['source_id']}", "order": "published_at.desc", "limit": "300"})
    js = [r for r in jrows if (r["raw_metadata"] or {}).get("origin_section") == "統合幕僚監部"]
    matched = [r for r in js if mon._keyword_hit(tw["theme"], r)]
    L += ["## Pilot Case 3: Taiwan / Japan Defense（防衛省＋統合幕僚監部）", "",
          f"- Theme: {tw['theme']['display_name']}（`/geo/monitoring-themes/{tw['theme']['monitoring_theme_id']}`）",
          f"- **Freshness: {ta['freshness_state']}** ／ **State: {ta['state']}**（**falseなStableになっていないこと**を確認）／ 関連記事 {ta['articles_in_window']}（7日窓） ／ Alert {len(tw['alerts'])}",
          f"- 統合幕僚監部（Canonical Source=防衛省、origin_section=統合幕僚監部）: {len(js)}件、Taiwan条件に一致 {len(matched)}件", "- Coverage panel:"] + ["  " + x for x in coverage_lines(ta)] + [
          f"- 注記: {ta['coverage_note']}", "", "**統合幕僚監部の直近の発表**", "", "| 公表日 | Title | Taiwan条件 |", "|---|---|---|"]
    for r in js[:8]:
        L.append(f"| {r['published_at'][:10]} | {r['title'][:70]} | {'一致' if mon._keyword_hit(tw['theme'], r) else '-'} |")
    L += ["", "**評価の観点**: 一次情報だけで『中国海軍艦艇の動向』のような変化に気づけるか／窓内に関連記事が無い場合に『変化なし』と誤読されないか（注記・Coverage panel）／Evidence（公表PDF）へ行けるか。", ""]

    L += ["## 評価フォーム（Case共通。Live Intelligence Quality＝B。UX / Workflow Quality＝Aとは別に記録）", "",
          f"- Target Version: `{TARGET_VERSION}` ／ Evaluator: ______ ／ Case: ______ ／ 日時: ______ ／ Preflight Gate: ______（上表）／ 最終取得時刻: ______",
          "- 各項目 5段階（1〜5）＋自由記述:", "", "| 項目 | 1〜5 | コメント | Issue tag |", "|---|---|---|---|"] + [
          f"| {x} | | | |" for x in ("1. Freshness trust", "2. Source usefulness", "3. Coverage transparency", "4. Signal relevance", "5. Missing coverage awareness", "6. Actionability")] + [
          "", "- **総合: このMonitoringを実務で継続して見たいか**（1〜5）: ____  理由: ____",
          "- 自由記述: 何に気づけたか／普通のニュース確認との違い／どこまで信頼できたか／次に何を確認したくなったか／実際の業務で使う場面",
          "- **Hormuzのみ**: Official / Maritime Advisory・Military / Security・Shipping / Commercial・Broad Media（欠落）のCoverage差を理解できたか。「3つfreshだから全部見えている」と誤認しなかったか（観察）",
          "- **Taiwanのみ**: 監視窓内の関連記事0件を『変化なし』と誤認しなかったか／Coverage不足を理解できたか（観察）",
          "- Issue tag — Feature: Monitoring / Coverage / Evidence / Direct Source / Daily Brief / Expert AI / Navigation ／ Issue: Missing / Wrong / Too Broad / Too Narrow / Stale / False Positive / False Negative / Hard to Understand / Low Value / Presentation",
          "- Broad Media関連（Broad Media coverage／Media detection speed／Media Alert recall／Media missing signals／today's news completeness）は **N/A（Not Evaluated）**。低スコアに換算しない。", ""]
    L += ["## Broad Media（別欄・未評価）", "", f"GDELT: {live['broad_media']['status']}（最終 {(live['broad_media'].get('last_data_at') or '記録なし')[:10]}）。**以下は評価しない**: Broad Media coverage／Media detection speed／Media alert recall／Media由来のmissing signal／今日のニュース全体の網羅性。GDELT回復後に追加評価する。", ""]
    out_path.write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--with-sanctions", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    client = SupabaseClient(load_config())
    now = datetime.now(timezone.utc)
    results = [] if args.skip_collect else step_collect(client, args.with_sanctions)
    print("== 1. Manual Run ==")
    for n, s, _ in results:
        print(f"  {n}: {s}")
    live = cov.build_live_intelligence_status(client)
    tv = verify_target_version()
    print(f"== Target Version: {TARGET_VERSION} | manifest {str(tv['manifest_sha256'])[:12]} | "
          + ("作業ツリーは対象版と一致" if tv["frozen"] and not tv["drift"] else f"警告: 作業ツリーが対象版と一致しない {tv['drift'] or 'Freeze未作成'}"))
    print("== 2. Freshness（Coverage別）==", live["counts"])
    for a in live["areas"]:
        print(f"  {a['status']:8} {'評価可' if a['pilot_evaluable'] else '未評価'}  {a['label']}")
    stale_direct = [s["name"] for a in live["areas"] for s in a["sources"] if s["status"] != "fresh" and s["role"] == "primary"]
    if stale_direct:
        print("  注意: 最新でない一次Source:", sorted(set(stale_direct)), "（手動実行されていない場合はstaleが正しい挙動）")
    for k, g in case_gates(client, live, now).items():
        print(f"  Gate {k}: {g['gate']} {g['reasons'] or ''}")
    out = Path(args.out) if args.out else ROOT / "evaluation" / "LIVE_INTELLIGENCE_PILOT_CASE_SHEETS.md"
    write_case_sheets(client, now, out, results, live)
    print("== 3. Case Sheet:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
