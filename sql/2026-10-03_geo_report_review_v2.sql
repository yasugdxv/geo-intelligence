-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): Report Review V2
--
-- 既存レポートが採用している地政学的な「読み方」をExpert AIが再検討する機能の
-- 最小追加。既存のAnalysis Session / Scenario Candidate / Report Draft / Review
-- Cycle / Approval / Knowledge Publicationテーブルはそのまま再利用し、新しい
-- 並行Workflowは作らない——本テーブルは「Report Reading Extraction→Expert AI
-- 独自Reading生成→比較」の実行結果を保持するだけで、Review Cycle以降は既存
-- geo_ios_review_cycles等にそのまま接続する。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このリポジトリに
-- マイグレーションランナーは無い、既存運用どおり）。destructive文は含まない。
-- =====================================================================

create table public.geo_report_review_v2_runs (
    run_id                  uuid primary key default gen_random_uuid(),
    analysis_session_id     uuid not null references public.geo_ios_analysis_sessions(analysis_session_id)
                               on delete cascade,
    report_draft_id         uuid references public.geo_ios_report_drafts(report_draft_id)
                               on delete set null,
    status                  text not null default 'pending'
                               check (status in ('pending', 'completed', 'failed')),
    error_message           text,
    -- geo_report_reading_extraction_service.extract_report_readingの出力そのまま。
    -- 明示されていない項目はnull/空配列（一般知識で補完しない、絶対条件）。
    report_reading          jsonb,
    -- Report Readingを既存Reading Presentation v2で表示するためmaterializeした
    -- scenario_candidateへの参照（reading_role=REPORT_OWN_READING）。
    report_reading_scenario_candidate_id
                            uuid references public.geo_ios_scenario_candidates(scenario_candidate_id)
                               on delete set null,
    -- geo_ios_expert_ai_adapter.generate_scenario_candidatesの出力そのまま
    -- （Expert AI Coreが同じ状況に対して独立に生成したReading、main+alternative）。
    expert_reading          jsonb,
    -- geo_ios_expert_ai_adapter.run_report_reading_comparison_reviewのreview_findings。
    comparison_findings     jsonb,
    -- 比較表上段の要約行（着眼点/Key Actor/Primary Reading/根拠/弱化条件）。
    -- geo_report_review_v2_service._build_comparison_summaryの出力そのまま、
    -- report_reading/expert_readingから機械的に導出するのみ（新しいLLM呼び出し無し）。
    comparison_summary      jsonb,
    created_by              text not null,
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),
    unique (analysis_session_id)
);

create index geo_report_review_v2_runs_session_idx
    on public.geo_report_review_v2_runs(analysis_session_id);

create trigger geo_report_review_v2_runs_set_updated_at
    before update on public.geo_report_review_v2_runs
    for each row execute function public.set_updated_at();

alter table public.geo_report_review_v2_runs enable row level security;

comment on table public.geo_report_review_v2_runs is
    'Report Review V2（既存レポートの読み方をExpert AIが再検討する機能）の実行結果。'
    '1 analysis_session_idにつき1行。Expert AI Core自体・既存Review Cycle/Approval/'
    'Knowledge Publicationテーブルは無改修——本テーブルはそれらの手前の中間結果のみを保持する。';
