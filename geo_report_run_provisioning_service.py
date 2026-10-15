"""Report Studio「Design」領域: Run作成時のSnapshot固定（Phase B、create_run契約）。

Runを「過去に何度再実行しても同じ入力で再現できる」ものにするため、Run作成時に
以下を一括で固定する（根拠: kind-napping-hare.md「Run作成時の完全固定」）:

  - Design Version（＝Narrative Master Asset・Report Template Asset）
  - Prompt1/Prompt2指示文の本文＋fingerprint（Windowsローカルパスに依存せず、
    リポジトリ内resources/report_studio/配下のVersioned Resourceから読む）
  - report_config.yaml相当（geo_report_run_config、Design Versionのreport_designから導出）
  - Processing Version（preprocess/aggregation/renderer/application_build_revision）
  - Previous Continuity（baseline_run_idがあれば、その時点のcanonical Prompt2実行が
    生んだcontinuity_context_next.jsonへのポインタを固定。baseline側のcanonicalが
    後から変わってもこのRunのポインタは変わらない）

固定処理そのものは`geo_report_pin_run_snapshot` RPC（1トランザクション、
sql/2026-09-11b_geo_report_pin_run_snapshot.sql）で行い、失敗時は自動ロールバックされる。
Run行自体の作成（前処理より前の空行）はこのモジュールが担うが、CSV前処理
（run_preprocess/persist_collection_run、Shell Parity）はPhase Dの別モジュールの責務であり
ここには含めない。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests

import geo_report_master_lint_service as master_lint
import geo_report_profile_registry as registry

_RESOURCE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "report_studio")

# Current Source of Truth（Plan「0. Current Source of Truth」章）のバージョン。
# Prompt1/2が更新されたら、新しいバージョンdirを追加してここを更新する
# （既存Runのfingerprint/本文には影響しない——Run作成時に固定されたSnapshotが
# そのまま残るため）。
CURRENT_PROMPT_CONTRACT_VERSIONS = {"prompt1": "v4.3", "prompt2": "v4.3"}

# analysis_mode（report_design.analysis_mode）別のPrompt1バージョンdispatch
# （2026-09-15追加、外部監査指摘: analysis_modeをRunへsnapshotし、
# public_voice_monitorとevidence_intelligenceでPrompt1契約を分離する）。
# 'public_voice_monitor'はCURRENT_PROMPT_CONTRACT_VERSIONS['prompt1']（既定v4.3）を
# そのまま使う（Noneを返す＝load_prompt_instructionのversion省略時デフォルトに委ねる）。
# Prompt2側もPhase H（2026-09-16）でevidence_intelligence_v1専用版を実装した
# （ANALYSIS_MODE_PROMPT2_VERSIONS、下記）。ChatGPT Fallback専用——Application AI
# Aggregation/Compositionはevidence_intelligenceモードでは無効化する（UI側で対応）。
ANALYSIS_MODE_PROMPT1_VERSIONS = {
    "public_voice_monitor": None,
    "evidence_intelligence": "evidence_intelligence_v1",
}

# analysis_mode別のPrompt2バージョンdispatch（2026-09-16追加、Phase H）。
# ANALYSIS_MODE_PROMPT1_VERSIONSと全く同じパターン。'public_voice_monitor'は
# CURRENT_PROMPT_CONTRACT_VERSIONS['prompt2']（既定v4.3）をそのまま使う。
ANALYSIS_MODE_PROMPT2_VERSIONS = {
    "public_voice_monitor": None,
    "evidence_intelligence": "evidence_intelligence_v1",
}

# Current Pipelineの処理ロジック自体のバージョン。P0は単一世代のみ登録するが、
# 将来Version Dispatch（get_preprocess_pipeline(version)等）を導入する際の
# キーとしてそのまま使える値にしておく。
CURRENT_PROCESSING_VERSIONS = {
    "preprocess_contract_version": "preprocess-v1",
    "aggregation_contract_version": "aggregation-v1",
    "renderer_version": "renderer-v1",
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_prompt_instruction(stage: str, *, version: Optional[str] = None) -> dict:
    """resources/report_studio/{stage}/{version}/instruction.md を読み、
    {contract_version, text, fingerprint} を返す。versionを省略した場合は
    CURRENT_PROMPT_CONTRACT_VERSIONSの現行値を使う。"""
    if stage not in ("prompt1", "prompt2"):
        raise ValueError(f"未対応のstageです: {stage}（prompt1/prompt2のみ）")
    resolved_version = version or CURRENT_PROMPT_CONTRACT_VERSIONS[stage]
    path = os.path.join(_RESOURCE_ROOT, stage, resolved_version, "instruction.md")
    if not os.path.isfile(path):
        raise ValueError(f"Prompt指示文リソースが見つかりません: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return {"contract_version": resolved_version, "text": text, "fingerprint": _sha256(text)}


def get_latest_published_design_version(client, project_id: str) -> dict:
    rows = client.select("geo_report_design_versions", {
        "select": "*", "project_id": f"eq.{project_id}", "order": "version.desc", "limit": "1",
    })
    if not rows:
        raise ValueError(
            f"project_id={project_id} にPublished Design Versionがありません。"
            "先にDesign Chatで設計しPublishしてください。"
        )
    return rows[0]


def _natural_signal_order(signal_id: str):
    digits = "".join(ch for ch in str(signal_id) if ch.isdigit())
    return (int(digits) if digits else 10**6, str(signal_id))


def resolve_primary_graph_signals(raw_design: dict, design_signals: list, master_rows: list) -> tuple:
    """primary_graph_signalsを明示的に解決する。(list or None, source)を返す。
    『空=全Signal』のような暗黙仕様は使わない。解決できない場合はNone/"unresolved"。
      design                : Report Designに明示された値
      design_explicit_empty : Report Designが空リスト（グラフ不使用という明示判断）
      design_core_autofill  : continuity_role=='Core'のSignal
      narrative_master_trend_role_primary : Narrative Masterのtrend_role=='primary'のSignal"""
    explicit = (raw_design or {}).get("primary_graph_signals")
    if explicit is not None:
        return list(explicit), ("design" if explicit else "design_explicit_empty")
    core_ids = [s.get("signal_id") for s in (design_signals or [])
                if s.get("continuity_role") == "Core" and s.get("signal_id")]
    if core_ids:
        return core_ids, "design_core_autofill"
    primary_ids = sorted({r.get("signal_id") for r in (master_rows or [])
                          if r.get("signal_id") and str(r.get("trend_role") or "").strip().lower() == "primary"},
                         key=_natural_signal_order)
    if primary_ids:
        return primary_ids, "narrative_master_trend_role_primary"
    return None, "unresolved"


def resolve_primary_chart_start(historical_start, observation_start) -> Optional[str]:
    """グラフ開始日 = max(設定された履歴の開始日, 観測期間の開始日)。観測期間より前の履歴は
    （そのデータがRunに含まれない限り）グラフに含めない。どちらも無ければNone。"""
    candidates = [str(v)[:10] for v in (historical_start, observation_start) if v]
    return max(candidates) if candidates else None


def build_report_config(design_version: dict, *, profile: Optional[dict] = None, master_rows: Optional[list] = None,
                        run_period: Optional[tuple] = None, template_asset: Optional[dict] = None) -> dict:
    """report_config.yaml相当のconfig_jsonをDesign Versionのreport_designから導出する。
    Design Chat（Phase C）がreport_design.primary_graph_signals /
    report_design.common_primary_graph_start / report_design.final_graph_rule を
    設定する想定。未設定の場合は`autofill_prompt2_report_config`が安全な既定値で
    補完する（本来Publish時点で既に補完済みのはずだが、それより前に作られた
    Design Versionに対するフォールバックとしてここでも適用する）。

    戻り値の2番目（primary_graph_signals）はvalidate_prompt2_report_config用に
    **正規化せずNoneのまま**返す（2026-09-15修正）。config_json["primary_graph_signals"]
    （DB保存用、array型カラムへは`[]`で書き込む）とは別に保持する必要がある——
    以前は両者を同じ`or []`正規化済みの値で共有していたため、「意図的な空リスト」と
    「Core Signalが無く自動補完もできなかった未設定」の区別がvalidate側で
    できなくなっていた（Run作成のたびに同じ警告が繰り返し表示される不具合の原因）。

    config_json["analysis_mode"]（2026-09-15追加、外部監査指摘: Report Designから
    analysis_modeをRunへsnapshotすること）: Design Versionのreport_design.analysis_mode
    をそのままRun Configへ固定する（既定値'public_voice_monitor'、既存プロジェクトとの
    後方互換）。Run作成後にDesignのanalysis_modeが変わっても、既存Runのconfig_jsonは
    不変のまま——Version Isolationの原則に従う。

    config_json["discovery_signal_ids"]（2026-09-16追加、#4）: Signal定義の
    discovery_mode=trueなsignal_idの一覧をRun Configへ固定する。Preprocess
    （run_and_persist_preprocess）はここから読み、当該Signalにkeyword matchingを
    Primary routingとして使わずdedup/coarse clustering/noise reductionのみ行う。"""
    raw_design = design_version.get("report_design")
    report_design = autofill_prompt2_report_config(raw_design, design_version.get("signals"))
    signals = design_version.get("signals") or []
    analysis_mode = report_design.get("analysis_mode") or "public_voice_monitor"
    # primary_graph_signals: 暗黙の「空=全Signal」を使わず、解決元を記録する（2026-10-05）。
    primary_graph_signals, primary_source = resolve_primary_graph_signals(raw_design, signals, master_rows or [])
    final_graph_rule = report_design.get("final_graph_rule")
    discovery_signal_ids = [s.get("signal_id") for s in signals if s.get("discovery_mode") and s.get("signal_id")]
    design_signal_ids = [s.get("signal_id") for s in signals if s.get("signal_id")]
    master_signal_ids = sorted({r.get("signal_id") for r in (master_rows or []) if r.get("signal_id")},
                               key=_natural_signal_order)
    signal_ids, signals_source = (design_signal_ids, "design") if design_signal_ids else (master_signal_ids, "narrative_master")
    # 日付の意味を分離する（2026-10-05）: 観測期間(今回Runの対象) / 履歴の基準開始日 / グラフ開始日
    historical_start = report_design.get("historical_trend_start_date") or report_design.get("common_primary_graph_start")
    observation_start, observation_end = (run_period or (None, None))
    chart_start = resolve_primary_chart_start(historical_start, observation_start)
    config_json = {
        "project_id": design_version["project_id"],
        "design_version": design_version["version"],
        "analysis_mode": analysis_mode,
        "primary_graph_signals": primary_graph_signals or [],
        "primary_graph_signals_source": primary_source,
        "common_primary_graph_start": chart_start,
        "primary_chart_start": chart_start,
        "historical_trend_start_date": str(historical_start)[:10] if historical_start else None,
        "observation_start_date": str(observation_start)[:10] if observation_start else None,
        "observation_end_date": str(observation_end)[:10] if observation_end else None,
        "final_graph_rule": final_graph_rule,
        "discovery_signal_ids": discovery_signal_ids,
        "signals": signal_ids,
        "signals_source": signals_source,
    }
    if profile:
        config_json.update({
            "report_kind": profile["report_kind"], "report_title": profile["report_title"],
            "analysis_contract": profile["analysis_contract"],
            "prompt1_profile": profile["prompt1_profile"], "prompt2_profile": profile["prompt2_profile"],
            "template_id": profile["template_id"],
        })
    if template_asset:
        config_json.update({"template_asset_id": template_asset.get("id"),
                            "template_hash": registry.content_hash(template_asset.get("content"))})
    return config_json, primary_graph_signals, chart_start


_DEFAULT_FINAL_GRAPH_RULE = {
    "source_class": "Public Voice", "attitude": ["positive", "neutral", "negative"],
    "is_duplicate": False, "theme_relevance": "relevant",
}


def autofill_prompt2_report_config(report_design: Optional[dict], signals: Optional[list]) -> dict:
    """primary_graph_signals/final_graph_ruleが未設定のままPublish/Runが進んでしまい、
    Aggregation実行で初めて「Calculating trendsが失敗します」と気づくという同種の
    ユーザー報告が繰り返し発生したため（2026-09-15）、警告を出すだけでなく実際に
    安全な既定値で自動補完する。ユーザー/Design ChatのAIが明示的に設定した値が
    あれば、それを一切変更せずそのまま使う——ここは「未設定のまま」を防ぐための
    最終防衛ラインであり、明示的な設計判断を上書きしない。

    「未設定」の判定は**キーが存在しない、またはNone**の場合のみとする（2026-09-15
    実機報告で修正）。当初`not report_design.get(...)`で判定していたため、Design側が
    意図的に`primary_graph_signals: []`（＝このプロジェクトはグラフ機能を使わない、
    という明示的な設計判断）を設定していても「未設定」と誤判定し、Core Signalを
    勝手に上書きしてしまっていた——「明示的な設計判断を上書きしない」という本関数の
    意図に反する重大な副作用だった。空リストは「グラフ不使用という明示的な判断」
    として尊重し、自動補完の対象にしない。

    primary_graph_signalsの既定値は`continuity_role=='Core'`のSignal全件
    （継続的な主要トピックのみをメイングラフの分母に使うという既存の設計方針、
    Phase A監査結果の`run.ps1`ハードコード例=S1,S7もCore相当の運用だったことと
    整合する）。Core Signalが1つも無い場合は自動補完できないため空のまま返し、
    `validate_prompt2_report_config`の警告で表面化させる。

    analysis_mode='evidence_intelligence'の場合、final_graph_rule
    （Public Voice 3-way分母ルール）は自動補完しない（2026-09-15追加、外部監査指摘:
    Evidence IntelligenceはOfficial/Government/Industry/Expert/Media/Social等を
    すべてFirst-class Evidenceとして扱い、Public Voice sentimentを分母にしない、
    という別の集計思想のため。以前はanalysis_modeに関わらず常にPublic Voice向けの
    既定値で補完しており、Evidence IntelligenceのRunでもcontract mismatchが
    残っていた）。"""
    report_design = dict(report_design or {})
    analysis_mode = report_design.get("analysis_mode") or "public_voice_monitor"
    if "primary_graph_signals" not in report_design or report_design.get("primary_graph_signals") is None:
        core_ids = [
            s.get("signal_id") for s in (signals or [])
            if s.get("continuity_role") == "Core" and s.get("signal_id")
        ]
        if core_ids:
            report_design["primary_graph_signals"] = core_ids
    if analysis_mode != "evidence_intelligence":
        if "final_graph_rule" not in report_design or report_design.get("final_graph_rule") is None:
            report_design["final_graph_rule"] = dict(_DEFAULT_FINAL_GRAPH_RULE)
    return report_design


def validate_prompt2_report_config(config_json: dict, primary_graph_signals: Optional[list],
                                     common_primary_graph_start, valid_signal_ids: set, *,
                                     analysis_mode: str = "public_voice_monitor") -> list:
    """Prompt2 Aggregation必須のReport Config項目を検証し、不足/不正フィールド名の
    一覧を返す（空リスト=問題なし）。2026-09-12、Design側でこれらが一度も設定されない
    まま Run が作られ、Prompt2 Aggregation実行中に初めて発覚するケースが見つかったため
    追加（kind-napping-hare.md「B-4」）。Run作成自体はブロックしない
    （Preprocess/Prompt1だけを試したい探索的Runも正当な利用のため）——
    呼び出し元がwarningとして表面化する。

    「未設定」の判定はprimary_graph_signalsが**None**の場合のみとする（2026-09-15
    実機報告で修正）。`autofill_prompt2_report_config`と同じ意味論——空リスト`[]`は
    「グラフ機能を使わないという明示的な設計判断」を表すため、警告対象にしない。
    以前は`not primary_graph_signals`で判定しており、意図的に空にしたプロジェクトで
    Run作成のたびに同じ警告が繰り返し表示されてしまっていた（呼び出し元は必ず
    report_design.get("primary_graph_signals")をそのまま渡すこと——
    `or []`のような正規化をすると、ここでNoneと空リストの区別ができなくなる）。

    analysis_mode='evidence_intelligence'の場合、final_graph_ruleの欠落は警告しない
    （2026-09-15追加。Public Voice 3-way分母ルールを使わないモードのため、
    未設定であること自体が正しい状態）。"""
    problems: list = []
    if primary_graph_signals is None:
        problems.append("primary_graph_signals")
    elif valid_signal_ids and any(s not in valid_signal_ids for s in primary_graph_signals):
        problems.append("primary_graph_signals(存在しないsignal_idを含む)")
    if not common_primary_graph_start:
        problems.append("common_primary_graph_start")
    else:
        try:
            datetime.strptime(str(common_primary_graph_start)[:10], "%Y-%m-%d")
        except ValueError:
            problems.append("common_primary_graph_start(不正な日付形式)")
    if analysis_mode != "evidence_intelligence" and not config_json.get("final_graph_rule"):
        problems.append("final_graph_rule")
    return problems


def _narrative_master_rows(client, narrative_master_asset_id: Optional[str]) -> list:
    if not narrative_master_asset_id:
        return []
    rows = client.select("geo_report_project_assets", {"select": "content", "id": f"eq.{narrative_master_asset_id}"})
    if not rows:
        return []
    return list((rows[0].get("content") or {}).get("rows") or [])


def _narrative_master_signal_ids(client, narrative_master_asset_id: Optional[str]) -> set:
    return {r.get("signal_id") for r in _narrative_master_rows(client, narrative_master_asset_id)
            if r.get("signal_id")}


def build_master_lint_warnings(master_rows: list) -> dict:
    """Narrative Masterの静的lint結果（2026-10-03、Preprocess潜在不具合監査 #5）。
    Run作成時のwarningとして返すだけで、Run作成自体は止めない（既存Runとの互換維持）。
    戻り値: {"summary": {...}, "warnings": [表示用の短い文字列...]}。問題が無ければ空dict。"""
    findings = master_lint.lint_narrative_master(master_rows)
    summary = master_lint.summarize_findings(findings)
    warnings = master_lint.format_findings_for_warning(findings)
    if not warnings:
        return {}
    return {"summary": {"by_severity": summary["by_severity"], "by_code": summary["by_code"]},
            "warnings": warnings}


def resolve_baseline_continuity(client, baseline_run_id: Optional[str]) -> tuple:
    """baseline_run_idのcanonical Prompt2実行が生んだcontinuity_context_next.json
    アーティファクトを解決する。baselineが無い/canonical Prompt2が未実行の場合は
    (None, None)を返す（新規Projectの初回Run等、継続すべきContinuityが無いのは
    正常な状態）。

    規約: Prompt2 Executionが生成するcontinuity_context_next.jsonは
    geo_report_run_artifacts（artifact_type='prompt2_output',
    file_name='continuity_context_next.json', execution_id=そのPrompt2実行のid）
    として保存される（Phase E Canonical Prompt2 Output 10ファイルの1つ）。"""
    if not baseline_run_id:
        return None, None
    baseline_rows = client.select("geo_report_collection_runs", {
        "select": "canonical_prompt2_execution_id", "run_id": f"eq.{baseline_run_id}",
    })
    if not baseline_rows:
        return None, None
    baseline_prompt2_execution_id = baseline_rows[0].get("canonical_prompt2_execution_id")
    if not baseline_prompt2_execution_id:
        return None, None
    artifact_rows = client.select("geo_report_run_artifacts", {
        "select": "id", "execution_id": f"eq.{baseline_prompt2_execution_id}",
        "artifact_type": "eq.prompt2_output", "file_name": "eq.continuity_context_next.json",
        "limit": "1",
    })
    if not artifact_rows:
        return baseline_prompt2_execution_id, None
    return baseline_prompt2_execution_id, artifact_rows[0]["id"]


def _default_run_name() -> str:
    """run_nameを指定しなかった場合の既定名。UUIDそのままだと画面上でどのRunか
    判別できないというユーザー報告（2026-09-11）への対応——最低限、作成日時が
    人間に読める形で表示されるようにする。"""
    return datetime.now(timezone.utc).strftime("Run %Y-%m-%d %H:%M UTC")


def create_run_row(client, project_id: str, *, baseline_run_id: Optional[str] = None,
                    period_start: Optional[str] = None, period_end: Optional[str] = None,
                    run_name: Optional[str] = None) -> str:
    """前処理より前の空のRun行を作る（design_version_id等の固定列はまだNULL、
    pin_run_snapshotで後段に埋める）。"""
    payload = {"run_id": _new_id(), "project_id": project_id, "run_name": run_name or _default_run_name()}
    if baseline_run_id:
        payload["baseline_run_id"] = baseline_run_id
        payload["run_mode"] = "incremental"
    if period_start:
        payload["period_start"] = period_start
    if period_end:
        payload["period_end"] = period_end
    inserted = client.insert("geo_report_collection_runs", [payload])
    return (inserted[0] if inserted else payload)["run_id"]


def pin_run_snapshot(client, run_id: str, project_id: str, *,
                      baseline_run_id: Optional[str] = None,
                      preprocess_contract_version: Optional[str] = None,
                      aggregation_contract_version: Optional[str] = None,
                      renderer_version: Optional[str] = None,
                      application_build_revision: Optional[str] = None) -> dict:
    """既存のRun行（run_id）に対し、Design/Narrative/Template/Prompt/Config/
    Processing Version/Previous Continuityを1トランザクションで固定する
    （geo_report_pin_run_snapshot RPC呼び出し）。"""
    design_version = get_latest_published_design_version(client, project_id)
    # Report Type(report_kind)からProfileを決める。無い/未登録なら別案件のPromptへ落とさず停止する（2026-10-05）。
    profile = registry.resolve_profile(design_version.get("report_design"))
    run_rows = client.select("geo_report_collection_runs", {"select": "period_start,period_end", "run_id": f"eq.{run_id}"})
    run_period = (run_rows[0].get("period_start"), run_rows[0].get("period_end")) if run_rows else (None, None)
    master_rows = _narrative_master_rows(client, design_version.get("narrative_master_asset_id"))
    template_asset = None
    if design_version.get("report_template_asset_id"):
        template_rows = client.select("geo_report_project_assets", {
            "select": "id,content", "id": f"eq.{design_version['report_template_asset_id']}"})
        template_asset = template_rows[0] if template_rows else None
    config_json, primary_graph_signals, common_primary_graph_start = build_report_config(
        design_version, profile=profile, master_rows=master_rows, run_period=run_period,
        template_asset=template_asset)
    analysis_mode = config_json.get("analysis_mode") or "public_voice_monitor"
    prompt1 = load_prompt_instruction("prompt1", version=profile["prompt1_resource_version"])
    prompt2 = load_prompt_instruction("prompt2", version=profile["prompt2_resource_version"])
    contract_issues = registry.errors_only(
        registry.lint_prompt_text(profile, "prompt1", prompt1["text"])
        + registry.lint_prompt_text(profile, "prompt2", prompt2["text"]))
    if template_asset is not None:
        contract_issues += registry.check_template(profile, template_asset.get("content"))
    if contract_issues:
        raise ValueError("Run作成を中止しました（Report Contract不一致）:\n" + registry.format_issues(contract_issues))
    baseline_prompt2_execution_id, previous_continuity_artifact_id = resolve_baseline_continuity(
        client, baseline_run_id)
    valid_signal_ids = {r.get("signal_id") for r in master_rows if r.get("signal_id")}
    master_lint_warnings = build_master_lint_warnings(master_rows)
    prompt2_config_warnings = validate_prompt2_report_config(
        config_json, primary_graph_signals, common_primary_graph_start, valid_signal_ids,
        analysis_mode=analysis_mode)

    versions = dict(CURRENT_PROCESSING_VERSIONS)
    if preprocess_contract_version:
        versions["preprocess_contract_version"] = preprocess_contract_version
    if aggregation_contract_version:
        versions["aggregation_contract_version"] = aggregation_contract_version
    if renderer_version:
        versions["renderer_version"] = renderer_version

    try:
        result = client.rpc("geo_report_pin_run_snapshot", {
            "p_run_id": run_id,
            "p_project_id": project_id,
            "p_design_version_id": design_version["id"],
            "p_prompt1_contract_version": prompt1["contract_version"],
            "p_prompt1_instruction_text": prompt1["text"],
            "p_prompt1_instruction_fingerprint": prompt1["fingerprint"],
            "p_prompt2_contract_version": prompt2["contract_version"],
            "p_prompt2_instruction_text": prompt2["text"],
            "p_prompt2_instruction_fingerprint": prompt2["fingerprint"],
            "p_report_config_json": config_json,
            # DB保存用は常に[]へ正規化済みのconfig_json側を使う（primary_graph_signals
            # 変数自体はvalidate_prompt2_report_config用にNoneを許容する値のため、
            # array型カラムへそのまま渡さない。2026-09-15修正）。
            "p_primary_graph_signals": config_json["primary_graph_signals"],
            "p_common_primary_graph_start": common_primary_graph_start,
            "p_preprocess_contract_version": versions["preprocess_contract_version"],
            "p_aggregation_contract_version": versions["aggregation_contract_version"],
            "p_renderer_version": versions["renderer_version"],
            "p_application_build_revision": application_build_revision,
            "p_baseline_prompt2_execution_id": baseline_prompt2_execution_id,
            "p_previous_continuity_artifact_id": previous_continuity_artifact_id,
        })
    except requests.exceptions.HTTPError as exc:
        # 2026-09-14発覚: RPCがDB制約違反(例: date型カラムへ不正な文字列)で拒否すると、
        # 生のrequests例外がそのままAPI層まで伝播し500 Internal Server Errorになって
        # いた。ブラウザには「Failed to fetch」としか見えず原因が分からなかったため、
        # Postgresのエラーメッセージを取り出してValueErrorに変換する
        # （呼び出し元API層は既にValueError→HTTPException 400の変換パターンを持つ）。
        detail_message = str(exc)
        try:
            body = exc.response.json()
            detail_message = body.get("message") or detail_message
        except Exception:  # noqa: BLE001 — レスポンスがJSONでない場合は元の例外文言を使う
            pass
        raise ValueError(f"Run作成のSnapshot固定に失敗しました: {detail_message}") from exc
    if isinstance(result, list):
        result = result[0] if result else {}
    if prompt2_config_warnings:
        result["prompt2_config_warnings"] = prompt2_config_warnings
    if master_lint_warnings:
        result["master_lint_warnings"] = master_lint_warnings
    return result


def create_run_with_pinned_snapshot(client, project_id: str, *,
                                     baseline_run_id: Optional[str] = None,
                                     period_start: Optional[str] = None,
                                     period_end: Optional[str] = None,
                                     application_build_revision: Optional[str] = None,
                                     run_name: Optional[str] = None) -> dict:
    """Run行の作成とSnapshot固定を1つの操作として行う。固定（pin_run_snapshot）が
    失敗した場合、直前に作ったRun行を削除してから例外を再送出する
    （「Runだけ残る」「Snapshotだけ残る」という部分状態を残さないため。
    Run行自体は本関数がこの呼び出しの中で新規作成したものに限る——既存の
    Runを巻き込んで削除することはない）。"""
    run_id = create_run_row(client, project_id, baseline_run_id=baseline_run_id,
                             period_start=period_start, period_end=period_end, run_name=run_name)
    try:
        return pin_run_snapshot(
            client, run_id, project_id, baseline_run_id=baseline_run_id,
            application_build_revision=application_build_revision,
        )
    except Exception:
        client.delete("geo_report_collection_runs", {"run_id": f"eq.{run_id}"})
        raise
