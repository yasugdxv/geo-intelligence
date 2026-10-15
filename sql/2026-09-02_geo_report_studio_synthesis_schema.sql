-- =====================================================================
-- Report Studio: Synthesis（Prompt2相当）永続化テーブル
-- 作成日: 2026-09-02（geo_report_studio_schema.sqlの追補、UIのAssessment/Report
-- タブが実データを表示するために必要になったため追加）
-- 対象: Supabase（PostgreSQL）。新規構築、ALTER/DROP/移行処理は含まない。
--
-- 前提: sql/2026-09-02_geo_report_studio_schema.sql が適用済みであること
-- =====================================================================

create table public.geo_report_syntheses (
    id                        uuid primary key default gen_random_uuid(),
    run_id                    uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    signal_id                 text not null,
    narrative_id              text not null,
    draft                     jsonb not null,
    draft_validity_checks     jsonb not null,
    draft_validity_status     text not null,
    review                    jsonb,
    final_draft               jsonb not null,
    final_validity_status     text not null,
    final_html                text,
    observed_record_ids       jsonb not null default '[]'::jsonb,
    observed_cluster_ids      jsonb not null default '[]'::jsonb,
    expert_context_ref_ids    jsonb not null default '[]'::jsonb,
    created_at                timestamptz not null default now()
);

create index geo_report_syntheses_run_narrative_idx
    on public.geo_report_syntheses(run_id, signal_id, narrative_id, created_at desc);

alter table public.geo_report_syntheses enable row level security;

comment on table public.geo_report_syntheses is
    'Prompt2相当のSynthesis結果（central_assessment/findings/drivers/counterforces/'
    'implications/watchpoints/continuity_assessment）。draft=Synthesis直後、'
    'final_draft=Expert Review後（revised_draftがあればそちら、無ければdraftと同一）。'
    'Report Studio UIのAssessment/Reportタブはこのテーブルを読む';
