"""Report Studio: Report Type（report_kind）とPrompt/Templateの対応表、およびRun契約の検証（2026-10-05）。

背景: Public Voice Monitorの Prompt 2 は analysis_mode（public_voice_monitor）だけで選ばれていたため、
US中間選挙のRunに「消費税減税・食品値上げ」専用のPrompt 2（v4.3）が渡っていた。analysis_contract
（public_voice_monitor_v4_3）は「分析方式」の契約であり、Report Product（何のレポートか）を区別しない。
そこで以下を別々の概念として保持する。

  report_kind           : Report Product（例 us_midterm_elections）。Prompt/Templateの選択キー
  analysis_contract     : 分析方式の契約（例 public_voice_monitor_v4_3）
  observation_start/end : 今回Runの観測期間（Runに指定した期間。Preprocessの対象）
  historical_trend_start: 過去からトレンドを追う場合の基準開始日（観測期間とは別。実データが無ければグラフに使えない）

対応するPromptが無い場合は別案件へフォールバックせず、PROMPT_PROFILE_NOT_FOUNDで停止する。
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

PROFILE_NOT_FOUND = "PROMPT_PROFILE_NOT_FOUND"
SEVERITY_ERROR, SEVERITY_WARNING = "error", "warning"

# 各profileの意味:
#   prompt{1,2}_resource_version : resources/report_studio/{stage}/{version}/instruction.md のディレクトリ名
#   prompt_expected / prompt_forbidden : Prompt本文のsemantic lint（語の有無でprofile不一致を検出する）
#   required_template_sections : Runに固定されたTemplateが持っているべきsection_id
PROFILES = {
    "us_midterm_elections": {
        "report_kind": "us_midterm_elections",
        "report_title": "US Midterm Election Monitor",
        "analysis_mode": "public_voice_monitor",
        "analysis_contract": "public_voice_monitor_v4_3",
        "prompt1_profile": "public_voice_monitor_prompt1_v4_3",
        "prompt1_resource_version": "v4.3",
        "prompt2_profile": "us_midterm_elections_prompt2_v4_3",
        "prompt2_resource_version": "us_midterm_v4.3",
        "template_id": "us_midterm_state_races",
        "required_template_sections": ["sec_02_electoral_position", "sec_04_candidate_narratives"],
        "targeted_stance": True,
        "prompt_expected": {
            "prompt1": ["targeted_stance_classified"],
            "prompt2": ["targeted_stance_classified", "Electoral Position", "Candidate Favorability",
                        "observation_start_date"],
        },
        "prompt_forbidden": {
            "prompt1": ["消費税減税", "食品値上げ"],
            "prompt2": ["消費税", "食品値上げ", "2026-05-01", "S1 または S7", "S1・S7", "価格妥当性"],
        },
    },
    "consumption_tax_food_price": {
        "report_kind": "consumption_tax_food_price",
        "report_title": "消費税減税・食品値上げ 生活者反応レポート",
        "analysis_mode": "public_voice_monitor",
        "analysis_contract": "public_voice_monitor_v4_3",
        "prompt1_profile": "public_voice_monitor_prompt1_v4_3",
        "prompt1_resource_version": "v4.3",
        "prompt2_profile": "consumption_tax_food_price_prompt2_v4_3",
        "prompt2_resource_version": "v4.3",
        "template_id": "consumption_tax_food_price",
        "required_template_sections": [],
        "targeted_stance": False,
        "prompt_expected": {"prompt1": ["targeted_stance_classified"], "prompt2": ["消費税", "食品値上げ"]},
        "prompt_forbidden": {
            "prompt1": [],
            "prompt2": ["Candidate Favorability", "Electoral Position", "targeted_stance_classified"],
        },
    },
    "evidence_intelligence": {
        "report_kind": "evidence_intelligence",
        "report_title": "Evidence Intelligence Report",
        "analysis_mode": "evidence_intelligence",
        "analysis_contract": "evidence_intelligence_v1",
        "prompt1_profile": "evidence_intelligence_prompt1_v1",
        "prompt1_resource_version": "evidence_intelligence_v1",
        "prompt2_profile": "evidence_intelligence_prompt2_v1",
        "prompt2_resource_version": "evidence_intelligence_v1",
        "template_id": "evidence_intelligence",
        "required_template_sections": [],
        "targeted_stance": False,
        "prompt_expected": {"prompt1": [], "prompt2": []},
        "prompt_forbidden": {"prompt1": [], "prompt2": []},
    },
}

# analysis_modeだけで種別が決まるモード（そのモード専用のPromptしか無い）。report_kind未指定でも許可する。
IMPLIED_KIND_BY_ANALYSIS_MODE = {"evidence_intelligence": "evidence_intelligence"}


class ProfileNotFound(ValueError):
    pass


def resolve_profile(report_design: Optional[dict]) -> dict:
    """Report Designからprofileを決める。見つからない場合は別案件のPromptへ落とさず例外にする。"""
    design = report_design or {}
    analysis_mode = design.get("analysis_mode") or "public_voice_monitor"
    kind = (design.get("report_kind") or "").strip() or IMPLIED_KIND_BY_ANALYSIS_MODE.get(analysis_mode, "")
    if not kind:
        raise ProfileNotFound(
            f"{PROFILE_NOT_FOUND}: Report Designにreport_kindがありません（analysis_mode={analysis_mode}）。"
            f"report_kindを設定してください。登録済み: {', '.join(sorted(PROFILES))}")
    profile = PROFILES.get(kind)
    if profile is None:
        raise ProfileNotFound(f"{PROFILE_NOT_FOUND}: report_kind={kind!r} に対応するProfileがありません。"
                              f"登録済み: {', '.join(sorted(PROFILES))}")
    if profile["analysis_mode"] != analysis_mode:
        raise ProfileNotFound(
            f"{PROFILE_NOT_FOUND}: report_kind={kind} はanalysis_mode={profile['analysis_mode']} 用ですが、"
            f"Report Designのanalysis_modeは{analysis_mode}です。")
    return profile


def content_hash(content) -> str:
    """Template等のcontent(JSON)の内容ハッシュ。Runへ固定した版の同一性確認に使う。"""
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def template_section_ids(template_content) -> list:
    sections = template_content.get("sections", []) if isinstance(template_content, dict) else (template_content or [])
    return [s.get("section_id") for s in sections if isinstance(s, dict) and s.get("section_id")]


def _issue(severity: str, code: str, message: str) -> dict:
    return {"severity": severity, "code": code, "message": message}


def lint_prompt_text(profile: dict, stage: str, text: str) -> list:
    """Prompt本文がprofileと一致するかの検査。別案件固有の語が残っていれば error。"""
    issues = []
    lowered = (text or "").lower()
    for word in profile.get("prompt_forbidden", {}).get(stage, []):
        if word.lower() in lowered:
            issues.append(_issue(SEVERITY_ERROR, "prompt_profile_mismatch",
                                 f"{stage}の指示文に、report_kind={profile['report_kind']}では使えない記述『{word}』があります"
                                 "（別のReport Product向けのPromptが選ばれている可能性）。"))
    for word in profile.get("prompt_expected", {}).get(stage, []):
        if word.lower() not in lowered:
            issues.append(_issue(SEVERITY_ERROR, "prompt_profile_missing_expected",
                                 f"{stage}の指示文に、report_kind={profile['report_kind']}で必須の記述『{word}』がありません。"))
    return issues


def _parse_date(value) -> Optional[str]:
    text = str(value or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text
    return None


def check_template(profile: dict, template_content) -> list:
    """Runに固定されたTemplateが、report_kindのProfileに必要なsectionを持つか。"""
    present = set(template_section_ids(template_content))
    return [_issue(SEVERITY_ERROR, "template_profile_mismatch",
                   f"Runに固定されたTemplateにsection『{section}』がありません"
                   f"（report_kind={profile['report_kind']}用のTemplateではない可能性）。")
            for section in profile["required_template_sections"] if section not in present]


def validate_run_contract(config_json: Optional[dict], *, prompt1_text: Optional[str] = None,
                          prompt2_text: Optional[str] = None, template_content=None,
                          monitoring_scope_rows: Optional[list] = None, run_period: Optional[tuple] = None) -> list:
    """Prompt 1/2 ZIP生成前のReport Contract検証。issue（severity/code/message）のlistを返す。

    config_json: geo_report_run_config.config_json（Runに固定された値）。run_period: (period_start, period_end)。"""
    cfg = config_json or {}
    issues = []
    kind = cfg.get("report_kind")
    # report_kind導入前に作られたEvidence IntelligenceのRun: そのモード専用のPromptしか無く取り違えが起きないため、
    # 警告にとどめて通す（US/消費税などPublic Voice Runは、取り違えの恐れがあるためerrorのまま）。
    legacy_implied = not kind and IMPLIED_KIND_BY_ANALYSIS_MODE.get(cfg.get("analysis_mode") or "") is not None
    if legacy_implied:
        kind = IMPLIED_KIND_BY_ANALYSIS_MODE[cfg["analysis_mode"]]
        issues.append(_issue(SEVERITY_WARNING, "report_kind_not_recorded_legacy",
                             f"Runのconfigにreport_kindが記録されていません（analysis_mode={cfg['analysis_mode']}のため"
                             f"{kind}として扱います）。"))
    profile = PROFILES.get(kind) if kind else None
    if not kind:
        issues.append(_issue(SEVERITY_ERROR, "report_kind_missing",
                             "Runのconfigにreport_kindがありません（Report Type未固定のRun。新しいRunを作成してください）。"))
    elif profile is None:
        issues.append(_issue(SEVERITY_ERROR, "report_kind_unknown", f"report_kind={kind!r} に対応するProfileがありません。"))
    if not cfg.get("analysis_contract") and not legacy_implied:
        issues.append(_issue(SEVERITY_ERROR, "analysis_contract_missing", "Runのconfigにanalysis_contractがありません。"))
    if profile:
        for key in (() if legacy_implied else ("prompt1_profile", "prompt2_profile", "template_id")):
            if not cfg.get(key):
                issues.append(_issue(SEVERITY_ERROR, f"{key}_missing", f"Runのconfigに{key}がありません。"))
            elif cfg.get(key) != profile[key]:
                issues.append(_issue(SEVERITY_ERROR, f"{key}_mismatch",
                                     f"{key}={cfg.get(key)!r} がreport_kind={kind}のProfile（{profile[key]!r}）と一致しません。"))
        if prompt1_text is not None:
            issues += lint_prompt_text(profile, "prompt1", prompt1_text)
        if prompt2_text is not None:
            issues += lint_prompt_text(profile, "prompt2", prompt2_text)
        if template_content is not None:
            issues += check_template(profile, template_content)
    if not cfg.get("signals"):
        issues.append(_issue(SEVERITY_ERROR, "signals_missing", "Runのconfigにsignalsがありません。"))
    if (not legacy_implied and cfg.get("primary_graph_signals_source") in (None, "", "unresolved")
            and not cfg.get("primary_graph_signals")):
        issues.append(_issue(SEVERITY_ERROR, "primary_graph_signals_unresolved",
                             "primary_graph_signalsが空で、解決方法も記録されていません（『空=全Signal』のような暗黙仕様は使わない）。"))

    obs_start, obs_end = _parse_date(cfg.get("observation_start_date")), _parse_date(cfg.get("observation_end_date"))
    if run_period and (run_period[0] or run_period[1]):
        if not (obs_start or obs_end):
            # 観測期間を記録する前に作られたRun。期間の整合は確認できないため警告にとどめる。
            issues.append(_issue(SEVERITY_WARNING, "observation_window_not_recorded",
                                 f"Runに期間({run_period[0]}〜{run_period[1]})がありますが、configに観測期間が記録されていません。"))
        elif (run_period[0] and obs_start != _parse_date(run_period[0])) or (run_period[1] and obs_end != _parse_date(run_period[1])):
            issues.append(_issue(SEVERITY_ERROR, "observation_window_mismatch",
                                 f"Runの期間({run_period[0]}〜{run_period[1]})とconfigの観測期間({obs_start}〜{obs_end})が一致しません。"))
    if (obs_start or obs_end) and monitoring_scope_rows:
        for row in monitoring_scope_rows:
            amin, amax = _parse_date(row.get("actual_min_date")), _parse_date(row.get("actual_max_date"))
            if obs_start and amin and amin < obs_start:
                issues.append(_issue(SEVERITY_ERROR, "data_before_observation_window",
                                     f"{row.get('signal_source')}の実データ最古日({amin})が観測期間の開始日({obs_start})より前です。"))
            if obs_end and amax and amax > obs_end:
                issues.append(_issue(SEVERITY_ERROR, "data_after_observation_window",
                                     f"{row.get('signal_source')}の実データ最新日({amax})が観測期間の終了日({obs_end})より後です。"))
            eff = _parse_date(row.get("effective_start_date"))
            if obs_start and eff and eff < obs_start:
                issues.append(_issue(SEVERITY_ERROR, "effective_start_before_observation_window",
                                     f"{row.get('signal_source')}のeffective_start_date({eff})が観測期間の開始日({obs_start})より前です。"))
            chart = _parse_date(row.get("common_primary_graph_start"))
            if obs_start and chart and chart < obs_start:
                issues.append(_issue(SEVERITY_ERROR, "chart_start_before_observation_window",
                                     f"グラフ開始日({chart})が観測期間の開始日({obs_start})より前です（存在しない履歴を含めない）。"))
        scope_signals = {r.get("signal_source") for r in monitoring_scope_rows}
        configured = set(cfg.get("signals") or [])
        if configured and scope_signals - configured - {"UNMAPPED"}:
            issues.append(_issue(SEVERITY_ERROR, "signals_mismatch",
                                 f"monitoring_scopeに、Run configにないSignalがあります: {sorted(scope_signals - configured)}"))
    return issues


def format_issues(issues: list) -> str:
    return "\n".join(f"- [{i['severity']}] {i['code']}: {i['message']}" for i in issues)


def errors_only(issues: list) -> list:
    return [i for i in issues if i["severity"] == SEVERITY_ERROR]
