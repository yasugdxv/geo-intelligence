"""Intelligence AI プリセットの英語表示用データ（定義の翻訳・システムメッセージ）。

lang="ja" のときは何もしない（定義・メッセージは日本語のまま）。lang="en" のとき、プリセットの名称・説明・入力項目・
見出し・システムメッセージを英語で返し、LLMにも英語で答えるよう指示する。
アダプターの技術的な注記（接続できない理由など）は、日本語のままの箇所がある（残課題）。
"""
from __future__ import annotations

LANGS = ("ja", "en")


def norm_lang(lang) -> str:
    return lang if lang in LANGS else "ja"


SOURCE_LABELS_EN = {
    "reports": "Reports / approved knowledge", "knowledge": "Shared knowledge base (Expert AI knowledge)", "articles": "Article DB",
    "monitoring": "Monitoring", "events": "GDELT events", "source_library": "Source Library",
    "external_network": "External Network (contact logs)", "web": "Web search",
}

# preset_id -> {title, description, intro, fields:{key:{label, placeholder?, options?{value:label}}}, optional:{key:label}, sections:{key:title}}
PRESETS_EN = {
    "past_cases": {
        "title": "Find related past cases", "description": "Search past reports and expert analyses for related cases",
        "intro": "I will look for related past analyses and cases. First, what topic would you like to research?",
        "fields": {"topic": {"label": "Research topic", "placeholder": "e.g. Tensions in the Taiwan Strait"},
                   "period": {"label": "Period", "options": {"3y": "Past 3 years", "5y": "Past 5 years", "all": "All time"}},
                   "date_from": {"label": "Since (optional)"},
                   "scope": {"label": "Search scope", "options": {"all": "All accumulated materials", "reports": "Past analysis reports only",
                                                                    "experts": "Expert analyses and views only"}}},
        "optional": {"web": "Also use web search (public information only)", "external_network": "Include External Network contact logs"},
        "sections": {"cases": "Related cases (summary)", "relevance": "Relevance to this topic", "past_insights": "Analysis and implications at the time"},
    },
    "find_experts": {
        "title": "Find experts", "description": "Find experts on a topic or region from registered info and track record",
        "intro": "I will look for experts on your topic or region. First, what topic or field of expertise?",
        "fields": {"topic": {"label": "Topic / field of expertise", "placeholder": "e.g. Taiwan's economic security"},
                   "region": {"label": "Region (optional)"},
                   "expert_attributes": {"label": "Expert attributes (optional)", "placeholder": "e.g. university researcher, former diplomat"}},
        "optional": {"external_network": "Search External Network (contacts and contact logs)", "web": "Also use web search (public information only)"},
        "sections": {"candidates": "Expert candidates and why", "track_record": "Related analyses, statements and track record",
                     "registry_basis": "Registered information used as basis"},
    },
    "changes_since_last": {
        "title": "Check changes since last time", "description": "Compare the last analysis with the current situation",
        "intro": "I will compare the previous analysis with the current situation. First, what topic should I analyze?",
        "fields": {"topic": {"label": "Topic", "placeholder": "e.g. US-China semiconductor controls"},
                   "baseline_date": {"label": "Comparison date (optional)"},
                   "baseline_report": {"label": "Report / material to compare (optional)", "placeholder": "e.g. a title"}},
        "optional": {"web": "Also use web search (public information only)", "external_network": "Include External Network contact logs"},
        "sections": {"previous_state": "Previous situation", "current_state": "Current situation", "changed": "What has changed",
                     "continued": "What continues", "reinterpretation": "Possible changes in interpretation"},
    },
    "compare_views": {
        "title": "Compare expert views", "description": "Compare how several experts see the same issue",
        "intro": "I will compare the views of several experts. First, what issue would you like to compare?",
        "fields": {"topic": {"label": "Issue to compare", "placeholder": "e.g. Will China's pressure on Taiwan grow?"},
                   "experts": {"label": "Target experts (optional)"},
                   "period": {"label": "Period", "options": {"3y": "Past 3 years", "5y": "Past 5 years", "all": "All time"}},
                   "date_from": {"label": "Since (optional)"}},
        "optional": {"external_network": "Include External Network contact logs", "web": "Also use web search (public information only)"},
        "sections": {"positions": "Positions by expert", "common": "Common ground", "disagreements": "Points of disagreement",
                     "premises": "Differences in premises"},
    },
    "test_hypothesis": {
        "title": "Test a hypothesis", "description": "Test a hypothesis with evidence for and against",
        "intro": "I will test a hypothesis. First, what hypothesis would you like to test?",
        "fields": {"topic": {"label": "Hypothesis to test", "placeholder": "e.g. China's economic pressure on Taiwan will grow"},
                   "period": {"label": "Period", "options": {"3y": "Past 3 years", "5y": "Past 5 years", "all": "All time"}},
                   "date_from": {"label": "Since (optional)"}, "focus": {"label": "Focus (optional)"}},
        "optional": {"web": "Also use web search (public information only)", "external_network": "Include External Network contact logs"},
        "sections": {"hypothesis": "Hypothesis under test", "supporting": "Supporting evidence", "weakening": "Weakening / contradicting evidence",
                     "alternatives": "Alternative interpretations", "assessment": "Current assessment",
                     "switch_conditions": "Conditions that would change the assessment", "next_checks": "Next points to check"},
    },
    "early_signals": {
        "title": "Look for early signals of change", "description": "Extract early signs and key signals on a topic",
        "intro": "I will look for early signs of change. First, what topic would you like to monitor?",
        "fields": {"topic": {"label": "Topic to monitor", "placeholder": "e.g. Military activity around Taiwan"},
                   "scenario": {"label": "Assumed scenario (optional)"},
                   "watch_period": {"label": "Watch period", "options": {"7d": "Last 7 days", "30d": "Last 30 days", "90d": "Last 90 days"}}},
        "optional": {"web": "Also use web search (public information only)", "external_network": "Include External Network contact logs"},
        "sections": {"early_signs": "Notable early signs", "observed_facts": "Observed facts", "possible_changes": "Possible changes",
                     "withheld": "Points to withhold judgment on", "watchpoints": "Watchpoints"},
    },
}

MESSAGES = {
    "ja": {
        "read_conditions": "次の条件を読み取りました。", "need_fields": "調査を始めるには、次の項目が必要です：{fields}。",
        "ready": "必要な条件が揃いました。内容を確認して「この条件で調査する」を押してください（初期値のままの項目は、フォームで変更できます）。",
        "no_evidence": "検索できた範囲では、該当する資料は見つかりませんでした。",
        "no_azure": "AI分析エンジンが設定されていないため、分析は実行できませんでした（検索結果のみ表示）。",
        "dropped": "根拠（参照ID）を確認できなかった記述を{n}件除外しました",
        "baseline_user": "比較の基準時点：{date}（ユーザー指定）",
        "baseline_auto": "比較の基準時点：{date}（見つかった最新の社内分析「{title}」を前回とみなしました。違う場合は基準時点を指定してください）",
        "baseline_none": "比較できる前回の社内分析が見つからなかったため、基準時点を決められませんでした。基準時点か比較するレポートを指定してください。",
        "ctx_missing": "調査の文脈が見つかりません", "no_azure_followup": "AI分析エンジンが設定されていないため、追加の質問には答えられません",
        "refs_unavailable": "直前の調査で保持している資料を、現在の権限で取得できませんでした。条件を指定して調査し直してください。",
        "dropped_prior": "前回の資料のうち{n}件は、現在の権限では参照できないため使っていません",
        "required_missing": "必須項目が未入力です：{fields}", "output_lang": "",
    },
    "en": {
        "read_conditions": "I read the following conditions.", "need_fields": "To start, I still need: {fields}.",
        "ready": "All required conditions are set. Please review them and press “Research with these conditions” (you can change any default in the form).",
        "no_evidence": "No matching materials were found within the sources searched.",
        "no_azure": "The AI analysis engine is not configured, so no analysis was run (search results only).",
        "dropped": "{n} statement(s) were removed because their supporting references could not be verified",
        "baseline_user": "Comparison baseline: {date} (specified by you)",
        "baseline_auto": "Comparison baseline: {date} (the latest internal analysis found, “{title}”, was treated as the previous one; specify a baseline date if that is wrong)",
        "baseline_none": "No previous internal analysis was found to compare against, so no baseline could be set. Specify a baseline date or a report to compare.",
        "ctx_missing": "The research context was not found", "no_azure_followup": "The AI analysis engine is not configured, so follow-up questions cannot be answered",
        "refs_unavailable": "The materials kept from the previous research could not be retrieved with your current permissions. Please specify the conditions and research again.",
        "dropped_prior": "{n} of the previous materials were not used because you can no longer access them",
        "required_missing": "Required fields are missing: {fields}",
        "output_lang": "\n# 出力言語\nEnglish（summary / warnings / text / answer は英語で書く。資料のタイトルや引用は原文のままでよい）",
    },
}


def m(lang: str, key: str, **kw) -> str:
    return MESSAGES[norm_lang(lang)][key].format(**kw)


def localize_fields(preset_id: str, fields: list, lang: str) -> list:
    """フォーム項目の名称・プレースホルダー・選択肢を英語にする（jaならそのまま）。"""
    if norm_lang(lang) == "ja" or preset_id not in PRESETS_EN:
        return fields
    tr = PRESETS_EN[preset_id]["fields"]
    out = []
    for f in fields:
        t = tr.get(f["key"], {})
        nf = {**f, "label": t.get("label", f["label"]), "placeholder": t.get("placeholder", f.get("placeholder"))}
        if t.get("options"):
            nf["options"] = [{"value": o["value"], "label": t["options"].get(o["value"], o["label"])} for o in f["options"]]
        out.append(nf)
    return out


def section_titles(preset_id: str, sections: list, lang: str) -> dict:
    tr = PRESETS_EN.get(preset_id, {}).get("sections", {}) if norm_lang(lang) == "en" else {}
    return {k: tr.get(k, t) for k, t in sections}
