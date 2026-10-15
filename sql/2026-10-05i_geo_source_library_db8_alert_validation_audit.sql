-- =====================================================================
-- Source Intelligence Final Hardening — DB-8: Alert Validationの監査列
--
-- Alertを「Candidate Detection → Context Validation → Alert」の2段階にしたため、
-- 「どのRuleが候補を出し、検証でどう判定され、なぜ採用されたか」を後から確認できるよう、
-- geo_monitoring_alertsへ nullable列を3つ追加する（additive）。既存Alert行は変更しない。
--
--   candidate_rule     : 候補を出したRule（例: event_military_action:invasion）
--   validation_result  : actual_event / llm_confirmed_actual / n/a（event系以外）
--   validation_reason  : 検証の根拠（rule判定 or LLM判定の理由）
--
-- Codeはこの列が無くても動く（無い場合は監査情報をlatest_assessmentへ保存）。
-- 前提: DB-4 適用済み。適用方法: Supabase SQL Editorで手動適用。destructive文なし。
-- =====================================================================

alter table public.geo_monitoring_alerts
    add column if not exists candidate_rule    text,
    add column if not exists validation_result text,
    add column if not exists validation_reason text;

comment on column public.geo_monitoring_alerts.candidate_rule is
    'Alert候補を出したRule（Candidate Detection）。';
comment on column public.geo_monitoring_alerts.validation_result is
    'Context Validationの結果（actual_event / llm_confirmed_actual / n/a）。否定・仮定・予測・過去・否認と判定された候補はAlertにならない。';
comment on column public.geo_monitoring_alerts.validation_reason is
    'Context Validationの根拠（なぜ実際に起きた出来事と判断したか）。';

-- Rollback:
--   alter table public.geo_monitoring_alerts
--       drop column if exists candidate_rule, drop column if exists validation_result,
--       drop column if exists validation_reason;
