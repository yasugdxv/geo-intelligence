// Source Intelligence Final Hardening（2026-10-05）で追加した文言。
export const ja: Record<string, string> = {
  "geo.sourceResearch.expansion.title": "検索に使った語",
  "geo.sourceResearch.expansion.original": "問い（原文・変更なし）",
  "geo.sourceResearch.expansion.translated": "英訳",
  "geo.sourceResearch.expansion.kind.original": "原文",
  "geo.sourceResearch.expansion.kind.translated": "英訳",
  "geo.sourceResearch.expansion.kind.expanded": "展開",
  "geo.sourceResearch.expansion.dropped":
    "問いを広げる恐れのある検索語{count}件はGuardにより破棄しました。",
  "geo.sourceResearch.noDirectAnswer":
    "質問に直接関連する記事が見つからなかったため、回答は生成していません（下の補完記事は参考表示のみです）。",
  "geo.sourceResearch.col.match": "関連性",
  "geo.sourceResearch.match.matched": "質問に関連",
  "geo.sourceResearch.match.supplementary": "補完（関連性未確認）",
  "geo.monitoringThemes.alertValidation": "文脈検証",
  "geo.monitoringThemes.alertRule": "検出ルール",
  "geo.monitoringThemes.validation.title": "Alert候補の文脈検証",
  "geo.monitoringThemes.validation.summary":
    "候補{candidates}件のうち、実際に起きた出来事と確認できたのは{valid}件です。否定・仮定・予測・過去・否認の記事はAlertの根拠にしていません。",
  "geo.monitoringThemes.validation.excluded": "除外した候補",
  "geo.monitoringThemes.validation.label.negated": "否定",
  "geo.monitoringThemes.validation.label.hypothetical": "仮定",
  "geo.monitoringThemes.validation.label.forecast": "予測・可能性",
  "geo.monitoringThemes.validation.label.historical": "過去の回想",
  "geo.monitoringThemes.validation.label.denial": "否認",
  "geo.monitoringThemes.validation.label.llm_rejected": "LLM判定: 発生とは言えない",
  "geo.monitoringThemes.validation.label.uncertain_excluded": "確認できず除外",
  "geo.monitoringThemes.sourceSetArchived":
    "このSource Setはアーカイブ済みですが、このモニタリングは引き続きそのSourceを監視しています。",
};
export const en: Record<string, string> = {
  "geo.sourceResearch.expansion.title": "Search terms used",
  "geo.sourceResearch.expansion.original": "Question (original, unchanged)",
  "geo.sourceResearch.expansion.translated": "Translation",
  "geo.sourceResearch.expansion.kind.original": "Original",
  "geo.sourceResearch.expansion.kind.translated": "Translated",
  "geo.sourceResearch.expansion.kind.expanded": "Expanded",
  "geo.sourceResearch.expansion.dropped":
    "{count} search terms that could broaden the question were discarded by the guard.",
  "geo.sourceResearch.noDirectAnswer":
    "No articles directly relevant to the question were found, so no answer was generated (supplementary articles below are for reference only).",
  "geo.sourceResearch.col.match": "Relevance",
  "geo.sourceResearch.match.matched": "Relevant",
  "geo.sourceResearch.match.supplementary": "Supplementary (relevance unverified)",
  "geo.monitoringThemes.alertValidation": "Context validation",
  "geo.monitoringThemes.alertRule": "Detection rule",
  "geo.monitoringThemes.validation.title": "Alert candidate validation",
  "geo.monitoringThemes.validation.summary":
    "{valid} of {candidates} candidates were confirmed as events that actually occurred. Negated, hypothetical, forecast, historical or denied articles are not used as Alert evidence.",
  "geo.monitoringThemes.validation.excluded": "Excluded candidates",
  "geo.monitoringThemes.validation.label.negated": "Negated",
  "geo.monitoringThemes.validation.label.hypothetical": "Hypothetical",
  "geo.monitoringThemes.validation.label.forecast": "Forecast / possibility",
  "geo.monitoringThemes.validation.label.historical": "Historical reference",
  "geo.monitoringThemes.validation.label.denial": "Denial",
  "geo.monitoringThemes.validation.label.llm_rejected": "LLM: did not occur",
  "geo.monitoringThemes.validation.label.uncertain_excluded": "Unverified (excluded)",
  "geo.monitoringThemes.sourceSetArchived":
    "This Source Set is archived, but this monitoring keeps watching its sources.",
};
