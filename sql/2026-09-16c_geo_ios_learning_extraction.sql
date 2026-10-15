-- =====================================================================
-- Geopolitical Intelligence OS Phase G: Learning Extraction
--
-- geo_ios_learning_candidates.candidate_typeへ"monitoring_indicator"を追加するのみ
-- （Phase G §7で新たに要求された分類。既存10種は変更しない）。新規テーブルは作らない
-- （trigger_context jsonbを既存どおり拡張利用し、不必要なmigrationを避ける方針）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは実行しない、
-- 既存運用を踏襲）。destructive文は含まない。
-- =====================================================================

alter table public.geo_ios_learning_candidates
    drop constraint if exists geo_ios_learning_candidates_candidate_type_check,
    add constraint geo_ios_learning_candidates_candidate_type_check
        check (candidate_type in (
            'cqas_question_pattern', 'scenario_pattern', 'reasoning_rule',
            'attention_rule', 'failure_pattern', 'evidence_rule',
            'source_preference', 'actor_intelligence', 'media_intelligence',
            'report_design_pattern', 'intelligence_need_pattern',
            'monitoring_indicator'
        ));
