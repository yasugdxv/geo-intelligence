"""Report Studio: Prompt 2 base input package の自動検証（2026-10-03追加、Preprocess潜在不具合監査 #11）。

背景: US中間選挙(209,976件)・Evidence Intelligence(391,909件)の2026-10-02のbase ZIPは、
record_cluster_map_001.csvが1,000行しか無く（PostgREST db-max-rows(1000)での取得打ち切り）、
population/weekly/source_bucketの派生CSVも全て同じ1,000件分から再集計されていたが、
ZIP生成は「成功」として完了し、誰にも気付かれなかった。件数の食い違いを機械的に検出して、
検証に失敗したZIPを完成扱いにしない（呼び出し側でValueErrorにする）ための関数群。

`validate_prompt2_base_files()`は副作用の無い純粋関数で、{ファイル名: str|bytes} と期待値を受けて
問題のリスト（dict: severity/code/message）を返す。severity='error'が1件でもあれば
そのZIPは不完全（Prompt 2へ渡してはいけない）。'warning'はデータ品質上の注意で生成は止めない。

検証項目（ユーザー指示#11の対応）:
  required files存在 / ZIP integrity（validate_zip_integrity） / record map全件性 /
  manifest row counts / record map chunk count / monitoring_scope completeness /
  report_config mode consistency / discovery mode conditional files / unique IDs /
  date coverage / duplicate flags / AC・SC IDがinput cluster masterに存在
"""

from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from collections import Counter, defaultdict
from typing import Optional

from geo_report_processing_service import parse_bool, parse_date_safe

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

DISCOVERY_FILES = ("discovery_candidates.csv", "discovery_candidate_representatives.csv")
_RECORD_MAP_RE = re.compile(r"^record_cluster_map_(\d{3})\.csv$")
_MAX_EXAMPLES = 3


def _issue(severity: str, code: str, message: str) -> dict:
    return {"severity": severity, "code": code, "message": message}


def _text(content) -> str:
    if isinstance(content, (bytes, bytearray)):
        return bytes(content).decode("utf-8-sig")
    return content or ""


def _csv_rows(files: dict, name: str):
    """CSVを行dictのリストとして返す。ファイルが無ければNone。"""
    if name not in files:
        return None
    return list(csv.DictReader(io.StringIO(_text(files[name]))))


def _iter_csv(content):
    return csv.DictReader(io.StringIO(_text(content)))


def parse_simple_yaml(text: str) -> dict:
    """report_config.yaml（`key: value`の単純形式＋`signals:`のリスト）を最小限に解釈する。"""
    result: dict = {}
    current_list = None
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("- ") and current_list is not None:
            current_list.append(raw.strip()[2:].strip())
            continue
        if ":" in raw and not raw.startswith(" "):
            key, _, value = raw.partition(":")
            key, value = key.strip(), value.strip()
            if value == "":
                result[key] = []
                current_list = result[key]
            else:
                result[key] = value
                current_list = None
    return result


def validate_zip_integrity(zip_bytes: bytes) -> tuple:
    """(issues, files) を返す。ZIPが壊れている・同名ファイルが重複している場合はerror。"""
    issues: list = []
    files: dict = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            bad = zf.testzip()
            if bad:
                issues.append(_issue(SEVERITY_ERROR, "zip_corrupt", f"ZIP内のファイルが壊れています: {bad}"))
                return issues, files
            names = [i.filename for i in zf.infolist() if not i.filename.endswith("/")]
            duplicated = sorted({n for n in names if names.count(n) > 1})
            if duplicated:
                issues.append(_issue(SEVERITY_ERROR, "zip_duplicate_entries",
                                      f"ZIP内に同名ファイルが重複しています: {duplicated}"))
            for name in names:
                files[name] = zf.read(name)
    except zipfile.BadZipFile as exc:
        issues.append(_issue(SEVERITY_ERROR, "zip_corrupt", f"ZIPとして読み込めません: {exc}"))
    return issues, files


def validate_prompt2_base_files(files: dict, *, required_files: list,
                                 expected_total_records: Optional[int] = None,
                                 expected_nonduplicate_records: Optional[int] = None,
                                 manifest: Optional[dict] = None,
                                 record_map_chunk_size: int = 50000,
                                 discovery_signal_ids=(),
                                 expected_analysis_contract: Optional[str] = None) -> list:
    """Prompt 2 base inputの内容検証（上記モジュールdocstring参照）。

    expected_total_records/expected_nonduplicate_records: Preprocess時点の正本件数
    （cluster_manifest.csvのtotal_records/nonduplicate_records）。省略時はmanifestから取る。
    どちらも得られない場合、record map全件性は検証できないため警告にする
    （ZIP内の他ファイル同士の整合性検証は引き続き行う）。"""
    issues: list = []
    manifest = manifest or {}
    if expected_total_records is None and manifest.get("total_records") not in (None, ""):
        expected_total_records = int(manifest["total_records"])
    if expected_nonduplicate_records is None and manifest.get("nonduplicate_records") not in (None, ""):
        expected_nonduplicate_records = int(manifest["nonduplicate_records"])

    # --- 1. required files ---
    missing = [n for n in required_files if n not in files]
    if missing:
        issues.append(_issue(SEVERITY_ERROR, "missing_required_file", f"必須ファイルがありません: {missing}"))
    chunk_names = sorted(n for n in files if _RECORD_MAP_RE.match(n))
    if not chunk_names:
        issues.append(_issue(SEVERITY_ERROR, "missing_record_map", "record_cluster_map_*.csvが1つもありません。"))

    # --- 2. record map（ストリーミングで集計し、行dictを全件保持しない）---
    allowed_files = {f for f in (manifest.get("input_files") or "").split("|") if f}
    stats = _scan_record_map(files, chunk_names, allowed_files)
    issues.extend(stats["issues"])

    # chunk番号の連続性・件数
    numbers = [int(_RECORD_MAP_RE.match(n).group(1)) for n in chunk_names]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        issues.append(_issue(SEVERITY_ERROR, "record_map_chunk_gap",
                              f"record_cluster_map_*.csvの連番が欠けています: {numbers}"))
    if expected_total_records is not None:
        if stats["total_rows"] != expected_total_records:
            issues.append(_issue(
                SEVERITY_ERROR, "record_map_row_count_mismatch",
                f"record_cluster_mapの総行数({stats['total_rows']})がPreprocessの総レコード数"
                f"({expected_total_records})と一致しません（取得打ち切り等でrecord mapが欠落している可能性）。"))
        expected_chunks = max(1, math.ceil(expected_total_records / record_map_chunk_size))
        if len(chunk_names) != expected_chunks:
            issues.append(_issue(
                SEVERITY_ERROR, "record_map_chunk_count_mismatch",
                f"record_cluster_mapのchunk数({len(chunk_names)})が期待値({expected_chunks}、"
                f"{expected_total_records}件÷{record_map_chunk_size}件/chunk)と一致しません。"))
    else:
        issues.append(_issue(SEVERITY_WARNING, "expected_total_unknown",
                              "Preprocessの総レコード数が得られず、record mapの全件性は検証できませんでした。"))
    if expected_nonduplicate_records is not None and stats["nonduplicate_rows"] != expected_nonduplicate_records:
        issues.append(_issue(
            SEVERITY_ERROR, "record_map_nonduplicate_mismatch",
            f"record_cluster_mapの非重複行数({stats['nonduplicate_rows']})がPreprocessの非重複レコード数"
            f"({expected_nonduplicate_records})と一致しません。"))
    if manifest.get("record_map_chunks") not in (None, "") and chunk_names \
            and int(manifest["record_map_chunks"]) != len(chunk_names):
        issues.append(_issue(
            SEVERITY_WARNING, "manifest_record_map_chunks_stale",
            f"cluster_manifestのrecord_map_chunks({manifest['record_map_chunks']})が実際のchunk数"
            f"({len(chunk_names)})と異なります（Preprocess時点では常に1が書かれる既知の仕様）。"))

    # --- 3. 派生CSVの件数整合（record map由来の正本件数と一致するか）---
    _check_derived_counts(files, stats, issues)

    # --- 4. cluster master（all_clusters / sentiment reps）とのID整合・一意性 ---
    all_clusters = _csv_rows(files, "all_clusters.csv")
    sentiment_reps = _csv_rows(files, "sentiment_cluster_representatives.csv")
    if all_clusters is not None:
        ids = [r.get("cluster_id", "") for r in all_clusters]
        dup_ids = sorted(i for i, n in Counter(ids).items() if n > 1)
        if dup_ids:
            issues.append(_issue(SEVERITY_ERROR, "duplicate_cluster_id",
                                  f"all_clusters.csvでcluster_idが重複しています: {dup_ids[:_MAX_EXAMPLES]}"))
        ac_ids = set(ids)
        unknown = stats["analysis_ids"] - ac_ids
        if unknown:
            issues.append(_issue(
                SEVERITY_ERROR, "record_map_unknown_analysis_cluster",
                f"record mapのanalysis_cluster_id {len(unknown)}種類がall_clusters.csvに存在しません"
                f"（例: {sorted(unknown)[:_MAX_EXAMPLES]}）。"))
        orphan = ac_ids - stats["analysis_ids"]
        if orphan and stats["total_rows"] > 0:
            issues.append(_issue(
                SEVERITY_WARNING, "cluster_without_records",
                f"all_clusters.csvの{len(orphan)}件のClusterにrecord mapの非重複レコードが1件も無い"
                f"（例: {sorted(orphan)[:_MAX_EXAMPLES]}）。"))
        eligible = sum(int(r.get("eligible_records") or 0) for r in all_clusters)
        if expected_nonduplicate_records is not None and eligible != expected_nonduplicate_records:
            issues.append(_issue(
                SEVERITY_ERROR, "derived_count_mismatch:all_clusters.csv",
                f"all_clusters.csvのeligible_records合計({eligible})が非重複レコード数"
                f"({expected_nonduplicate_records})と一致しません。"))
    if sentiment_reps is not None:
        sc_ids = {r.get("cluster_id", "") for r in sentiment_reps}
        unknown = stats["sentiment_ids"] - sc_ids
        if unknown:
            issues.append(_issue(
                SEVERITY_ERROR, "record_map_unknown_sentiment_cluster",
                f"record mapのsentiment_cluster_id {len(unknown)}種類がsentiment_cluster_representatives.csvに"
                f"存在しません（例: {sorted(unknown)[:_MAX_EXAMPLES]}）。"))

    # --- 5. report_config / mode consistency ---
    config = parse_simple_yaml(_text(files.get("report_config.yaml", "")))
    _check_report_config(config, expected_analysis_contract, files, issues)

    # --- 6. monitoring_scope completeness / date coverage ---
    _check_monitoring_scope(files, config, stats, issues)

    # --- 7. discovery conditional files ---
    discovery_signal_ids = list(discovery_signal_ids or [])
    present = [n for n in DISCOVERY_FILES if n in files]
    if discovery_signal_ids:
        absent = [n for n in DISCOVERY_FILES if n not in files]
        if absent:
            issues.append(_issue(
                SEVERITY_ERROR, "missing_discovery_file",
                f"discovery_mode Signal({discovery_signal_ids})が設定されているのに{absent}がありません。"))
    elif present:
        issues.append(_issue(SEVERITY_WARNING, "unexpected_discovery_files",
                              f"discovery_mode Signalが無いRunなのにdiscovery系ファイルがあります: {present}"))
    return issues


def _scan_record_map(files: dict, chunk_names: list, allowed_files: Optional[set] = None) -> dict:
    total = nonduplicate = unknown_date = bad_date = 0
    dup_with_cluster = nondup_without_cluster = dup_without_group = 0
    analysis_ids: set = set()
    sentiment_ids: set = set()
    per_signal_nondup: dict = defaultdict(int)
    nondup_with_known_week = 0
    pair_seen: set = set()
    pair_repeated = 0
    min_date = max_date = None
    signal_dates: dict = {}
    foreign_files: Counter = Counter()
    issues: list = []
    for name in chunk_names:
        for row in _iter_csv(files[name]):
            total += 1
            if allowed_files and row.get("source_file", "") not in allowed_files:
                foreign_files[row.get("source_file", "")] += 1
            is_dup = parse_bool(row.get("is_duplicate"), False)
            analysis_id = row.get("analysis_cluster_id", "")
            sentiment_id = row.get("sentiment_cluster_id", "")
            if is_dup:
                if analysis_id or sentiment_id:
                    dup_with_cluster += 1
                if not row.get("duplicate_group_id"):
                    dup_without_group += 1
                continue
            nonduplicate += 1
            if not analysis_id or not sentiment_id:
                nondup_without_cluster += 1
            analysis_ids.add(analysis_id)
            sentiment_ids.add(sentiment_id)
            signal = row.get("signal_source", "")
            per_signal_nondup[signal] += 1
            pair = (row.get("record_id", ""), signal)
            if pair in pair_seen:
                pair_repeated += 1
            else:
                pair_seen.add(pair)
            date_key = row.get("date_key", "")
            if date_key == "unknown" or not date_key:
                unknown_date += 1
                continue
            if row.get("week_start") != "unknown":
                nondup_with_known_week += 1
            parsed = parse_date_safe(date_key)
            if parsed is None:
                bad_date += 1
                continue
            day = parsed.strftime("%Y-%m-%d")
            min_date = day if min_date is None or day < min_date else min_date
            max_date = day if max_date is None or day > max_date else max_date
            lo, hi = signal_dates.get(signal, (day, day))
            signal_dates[signal] = (min(lo, day), max(hi, day))
    analysis_ids.discard("")
    sentiment_ids.discard("")
    if foreign_files:
        issues.append(_issue(
            SEVERITY_ERROR, "record_map_stale_source_file",
            f"record mapに、このRunの入力ファイル(cluster_manifestのinput_files)に無いsource_fileの行が"
            f"{sum(foreign_files.values())}件あります（{len(foreign_files)}ファイル、例: "
            f"{sorted(foreign_files)[:_MAX_EXAMPLES]}）。同じRunでPreprocessをやり直した際に、過去の実行の行が"
            "geo_report_recordsに残って混ざっている可能性があります（このテーブルはrun_idでしか区別されません）。"))
    if dup_with_cluster:
        issues.append(_issue(SEVERITY_ERROR, "duplicate_row_has_cluster_id",
                              f"is_duplicate=trueの行{dup_with_cluster}件にcluster IDが入っています"
                              "（README_prompt2.md: 重複行のcluster IDは空欄）。"))
    if dup_without_group:
        issues.append(_issue(SEVERITY_WARNING, "duplicate_row_without_group",
                              f"is_duplicate=trueの行{dup_without_group}件にduplicate_group_idがありません。"))
    if nondup_without_cluster:
        issues.append(_issue(SEVERITY_ERROR, "nonduplicate_row_missing_cluster_id",
                              f"非重複の行{nondup_without_cluster}件にcluster IDが入っていません。"))
    if pair_repeated:
        issues.append(_issue(
            SEVERITY_WARNING, "record_id_signal_not_unique",
            f"非重複行の(record_id, signal_source)が{pair_repeated}件重複しています"
            "（同一Signal内で同じdocumentIdが本文違いで複数回出現。record_id単独での結合は曖昧になります）。"))
    if bad_date:
        issues.append(_issue(SEVERITY_ERROR, "unparsable_date_key",
                              f"date_keyが解釈できない非重複行が{bad_date}件あります。"))
    if unknown_date:
        issues.append(_issue(SEVERITY_WARNING, "unknown_date_records",
                              f"date_keyがunknown（timestamp無し/解釈不能）の非重複行が{unknown_date}件あります。"))
    return {
        "issues": issues, "total_rows": total, "nonduplicate_rows": nonduplicate,
        "nonduplicate_known_week": nondup_with_known_week, "analysis_ids": analysis_ids,
        "sentiment_ids": sentiment_ids, "per_signal_nondup": dict(per_signal_nondup),
        "min_date": min_date, "max_date": max_date, "signal_dates": signal_dates,
    }


def _sum_column(rows: Optional[list], column: str) -> Optional[int]:
    if rows is None:
        return None
    try:
        return sum(int(r.get(column) or 0) for r in rows)
    except ValueError:
        return None


def _check_derived_counts(files: dict, stats: dict, issues: list) -> None:
    """record mapを正本として、派生CSV（population/weekly/source_bucket/monitoring_scope）の合計を検証する。
    population_narrative_by_signal*.csv/source_bucket_summary.csvは非重複の全行、
    weekly_trend_raw_basis.csv/timeline_basis.csv/monitoring_scope.csvはweek_start/dateが解釈できる非重複行が
    対象（Preprocessの集計仕様）。"""
    all_nondup = stats["nonduplicate_rows"]
    known_week = stats["nonduplicate_known_week"]
    checks = (
        ("population_narrative_by_signal.csv", "records", all_nondup),
        ("population_narrative_by_signal_date.csv", "records", all_nondup),
        ("source_bucket_summary.csv", "nonduplicate_records", all_nondup),
        ("weekly_trend_raw_basis.csv", "records", known_week),
        ("timeline_basis.csv", "records", known_week),
        ("monitoring_scope.csv", "nonduplicate_records", known_week),
    )
    for name, column, expected in checks:
        total = _sum_column(_csv_rows(files, name), column)
        if total is not None and total != expected:
            issues.append(_issue(
                SEVERITY_ERROR, f"derived_count_mismatch:{name}",
                f"{name}の{column}合計({total})がrecord mapの該当行数({expected})と一致しません。"))


def _check_report_config(config: dict, expected_contract: Optional[str], files: dict, issues: list) -> None:
    if not config:
        issues.append(_issue(SEVERITY_ERROR, "report_config_unreadable", "report_config.yamlが空または解釈できません。"))
        return
    contract = config.get("analysis_contract", "")
    title = config.get("report_title", "")
    rule = config.get("final_graph_rule", "")
    if expected_contract:
        if not contract:
            issues.append(_issue(
                SEVERITY_WARNING, "analysis_contract_missing",
                f"report_config.yamlにanalysis_contractがありません（期待値: {expected_contract}）。"
                "モードの判別がreport_title/final_graph_rule文面頼みになります。"))
        elif contract != expected_contract:
            issues.append(_issue(
                SEVERITY_ERROR, "analysis_contract_mismatch",
                f"report_config.yamlのanalysis_contract({contract})がRunの契約({expected_contract})と異なります。"))
    if not config.get("keyword_matcher"):
        issues.append(_issue(
            SEVERITY_WARNING, "keyword_matcher_missing",
            "report_config.yamlにkeyword_matcherがありません。Narrative割当に使われたmatcherの版（keyword_matcher_v2等）が"
            "パッケージから分かりません（2026-10-04以前のRunでは出ない）。"))
    effective = contract or expected_contract or ""
    is_evidence = effective.startswith("evidence_intelligence") or (
        not effective and title == "evidence_intelligence_report")
    if is_evidence:
        if title == "public_opinion_report":
            issues.append(_issue(SEVERITY_ERROR, "mode_title_mismatch",
                                  "Evidence Intelligence契約なのにreport_titleがpublic_opinion_reportです。"))
        if rule and not rule.startswith("not_applicable"):
            issues.append(_issue(SEVERITY_ERROR, "mode_graph_rule_mismatch",
                                  "Evidence Intelligence契約なのにfinal_graph_ruleがPublic Voice向けの値です。"))
    elif effective or title:
        if title == "evidence_intelligence_report":
            issues.append(_issue(SEVERITY_ERROR, "mode_title_mismatch",
                                  "Public Voice契約なのにreport_titleがevidence_intelligence_reportです。"))
        if rule.startswith("not_applicable"):
            issues.append(_issue(SEVERITY_ERROR, "mode_graph_rule_mismatch",
                                  "Public Voice契約なのにfinal_graph_ruleがnot_applicableです。"))
        # primary_graph_signalsが空なのにmasterにtrend_role=primaryの行がある（Prompt 2の分母定義が曖昧になる）
        master = _csv_rows(files, "signal_narrative_master.csv") or []
        primary_signals = sorted({r.get("signal_source", "") for r in master
                                  if (r.get("trend_role") or "").lower() == "primary"})
        configured = [s for s in (config.get("primary_graph_signals") or "").split(",") if s.strip()]
        if primary_signals and not configured:
            issues.append(_issue(
                SEVERITY_WARNING, "primary_graph_signals_empty",
                f"report_config.yamlのprimary_graph_signalsが空ですが、Narrative Masterにtrend_role=primaryの"
                f"Signalが{len(primary_signals)}件あります（Prompt 2の最終グラフ分母が曖昧になります）。"))


def _check_monitoring_scope(files: dict, config: dict, stats: dict, issues: list) -> None:
    rows = _csv_rows(files, "monitoring_scope.csv")
    if rows is None:
        return
    if not rows:
        issues.append(_issue(SEVERITY_ERROR, "monitoring_scope_empty", "monitoring_scope.csvが空です。"))
        return
    config_signals = config.get("signals") if isinstance(config.get("signals"), list) else []
    scope_signals = {r.get("signal_source", "") for r in rows}
    missing = sorted(set(config_signals) - scope_signals)
    if missing:
        issues.append(_issue(SEVERITY_ERROR, "monitoring_scope_missing_signal",
                              f"report_config.yamlのsignalsのうちmonitoring_scope.csvに無いもの: {missing}"))
    bad = [r.get("signal_source", "") for r in rows
           if parse_date_safe(r.get("actual_min_date", "")) is None or parse_date_safe(r.get("actual_max_date", "")) is None]
    if bad:
        issues.append(_issue(SEVERITY_ERROR, "monitoring_scope_bad_actual_dates",
                              f"actual_min_date/actual_max_dateが空/解釈不能のSignal: {sorted(set(bad))}"))
    no_start = [r.get("signal_source", "") for r in rows
                if not (r.get("configured_start_date") or r.get("effective_start_date"))]
    if no_start:
        issues.append(_issue(
            SEVERITY_ERROR, "monitoring_scope_start_missing",
            f"configured_start_dateもeffective_start_dateも空のSignal: {sorted(set(no_start))}"
            "（Prompt 2が分析開始日を決められません）。"))
    elif any(not r.get("configured_start_date") for r in rows):
        issues.append(_issue(
            SEVERITY_WARNING, "configured_start_date_empty",
            "configured_start_dateが空のSignalがあります（effective_start_date/actual_min_dateで補完されています）。"))
    # date coverage: monitoring_scopeの実日付範囲 vs record mapの実日付範囲
    scope_min = min((r["actual_min_date"] for r in rows if r.get("actual_min_date")), default=None)
    scope_max = max((r["actual_max_date"] for r in rows if r.get("actual_max_date")), default=None)
    if stats["min_date"] and scope_min and stats["min_date"] != scope_min:
        issues.append(_issue(SEVERITY_ERROR, "date_coverage_mismatch",
                              f"record mapの最古日({stats['min_date']})がmonitoring_scopeの最古日({scope_min})と異なります。"))
    if stats["max_date"] and scope_max and stats["max_date"] != scope_max:
        issues.append(_issue(SEVERITY_ERROR, "date_coverage_mismatch",
                              f"record mapの最新日({stats['max_date']})がmonitoring_scopeの最新日({scope_max})と異なります。"))


# ---------------------------------------------------------------------------
# Run isolation（2026-10-04）: 今回のPreprocess executionが作ったrecord集合と、export対象のrecord集合の比較。
# geo_report_recordsはexecution_id列を持たずrun_idでしか区別されないため、同じRunでPreprocessをやり直すと
# 旧実行の行が残ってexportに混ざる（Evidence run a1cb0baf: record mapの9.7%が旧9/14ファイル由来だった）。
# ---------------------------------------------------------------------------

RECORD_MANIFEST_COLUMNS = ["record_id", "signal_source", "source_file"]
RECORD_MANIFEST_FILE = "record_id_manifest.csv"


def record_key(record_id, signal, source_file) -> tuple:
    """record集合の比較キー。record_idはSignalを跨いで一意ではない（dedupがSignal単位のため）ので、
    (record_id, signal, source_file)の多重集合(Counter)で比較する。"""
    return (record_id or "", signal or "", source_file or "")


def counter_from_manifest_csv(text) -> Counter:
    return Counter(record_key(r.get("record_id"), r.get("signal_source"), r.get("source_file"))
                   for r in _iter_csv(text))


def counter_from_db_records(records: list) -> Counter:
    return Counter(record_key(r.get("record_id"), r.get("signal_id"), r.get("source_file")) for r in records)


def compare_record_sets(expected: Counter, exported: Counter, *, max_examples: int = 5) -> dict:
    """{'stale_total', 'missing_total', 'stale_examples', 'missing_examples', 'ok'}。
    stale = exportにあるが今回executionの期待集合に無い（旧実行の残骸等）、missing = 期待集合にあるがexportに無い。"""
    stale = exported - expected
    missing = expected - exported
    return {
        "ok": not stale and not missing,
        "expected_total": sum(expected.values()), "exported_total": sum(exported.values()),
        "stale_total": sum(stale.values()), "missing_total": sum(missing.values()),
        "stale_examples": [list(k) for k in list(stale)[:max_examples]],
        "missing_examples": [list(k) for k in list(missing)[:max_examples]],
        "stale_files": dict(Counter({k[2]: v for k, v in stale.items()}).most_common(5)),
    }


def describe_record_set_mismatch(result: dict) -> str:
    return (f"export対象のrecord集合が今回のPreprocess executionの期待集合と一致しません: "
            f"期待{result['expected_total']}件 / export{result['exported_total']}件、"
            f"stale(今回の実行に無い行)={result['stale_total']}件、missing(欠落)={result['missing_total']}件。"
            f" stale例={result['stale_examples'][:3]} 主なstaleのsource_file={result['stale_files']}"
            f" missing例={result['missing_examples'][:3]}。"
            "同じRunでPreprocessをやり直すと、過去の実行の行がgeo_report_recordsに残って混ざります"
            "（このテーブルはrun_idでしか区別されません）。新しいRunでPreprocessからやり直してください。")


def validate_prompt1_input_counts(files: dict) -> list:
    """Prompt 1 inputの件数整合: manifestの非重複数 = Analysis/Sentiment両方のeligible_records合計。"""
    issues: list = []
    manifest_rows = _csv_rows(files, "cluster_manifest.csv")
    if not manifest_rows:
        return issues
    manifest = manifest_rows[0]
    expected = int(manifest.get("nonduplicate_records") or 0)
    for name in ("cluster_labeling_input.csv", "sentiment_cluster_labeling_input.csv"):
        rows = _csv_rows(files, name)
        if rows is None:
            continue
        total = sum(int(r.get("eligible_records") or 0) for r in rows)
        if total != expected:
            issues.append(_issue(
                SEVERITY_ERROR, f"prompt1_count_mismatch:{name}",
                f"{name}のeligible_records合計({total})がcluster_manifestの非重複数({expected})と一致しません"
                "（別のPreprocess executionのartifactが混ざっている可能性）。"))
    return issues


def has_errors(issues: list) -> bool:
    return any(i["severity"] == SEVERITY_ERROR for i in issues)


def format_issues(issues: list, *, max_items: int = 20) -> str:
    ordered = sorted(issues, key=lambda i: (i["severity"] != SEVERITY_ERROR, i["code"]))
    lines = [f"[{i['severity']}] {i['code']}: {i['message']}" for i in ordered[:max_items]]
    if len(ordered) > max_items:
        lines.append(f"...他{len(ordered) - max_items}件")
    return "\n".join(lines)
