-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): REVIEW MODE（§17-22）
--
-- Report Studioに「既存レポートをレビューする」入口を追加するための最小追加。
-- 既存のAnalysis Session / Scenario Candidate / Analysis Design / Draft / Review /
-- Approvalテーブルはそのまま再利用し、新しい並行Workflowは作らない
-- （geo_ios_analysis_sessions.modeで既存フローと分岐するだけ）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このリポジトリに
-- マイグレーションランナーは無い、既存運用どおり）。destructive文は含まない。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. geo_ios_analysis_sessions.mode（CREATE / REVIEW の入口を区別）
-- ---------------------------------------------------------------------
alter table public.geo_ios_analysis_sessions
    add column mode text not null default 'create'
        check (mode in ('create', 'review'));

comment on column public.geo_ios_analysis_sessions.mode is
    'create=新規分析（既存CQASフロー）、review=既存レポートのレビュー（geo_ios_review_importsを参照）。';

-- ---------------------------------------------------------------------
-- 2. geo_ios_review_imports（REVIEW MODE Step1: 取込・既存読み再構成・修正方針）
-- ---------------------------------------------------------------------
create table public.geo_ios_review_imports (
    review_import_id       uuid primary key default gen_random_uuid(),
    analysis_session_id    uuid not null references public.geo_ios_analysis_sessions(analysis_session_id)
                               on delete cascade,
    original_filename      text,
    original_mime_type     text,
    -- geo_documents_store.extract_text()の抽出結果そのまま（要約・改変しない）。
    original_text          text not null,
    status                 text not null default 'pending'
                               check (status in ('pending', 'context_extracted', 'readings_ready', 'failed')),
    error_message          text,
    -- Analysis Theme / Current Situation / Question / Report Context（§18）。
    extracted_context      jsonb,
    -- Primary Reading / Key Actors / Actor Objectives / Relationships / Cue /
    -- Assumptions / Causal Logic / Direction / Scenario / Evidence（§19）。
    -- 明示されていない項目はnull（一般知識で補完しない、§19の絶対条件）。
    reconstructed_reading  jsonb,
    -- 現在の読み(Reading A)として materialize したscenario_candidate_idへの参照。
    current_reading_scenario_candidate_id
                           uuid references public.geo_ios_scenario_candidates(scenario_candidate_id)
                               on delete set null,
    -- Human Selectで選ばれたReading（現在の読みそのものの場合もある）。
    selected_scenario_candidate_id
                           uuid references public.geo_ios_scenario_candidates(scenario_candidate_id)
                               on delete set null,
    -- §21 Revision Strategy。選択後にのみ設定される。
    revision_strategy      text check (revision_strategy in ('keep_and_reinforce', 'replace', 'integrate')),
    created_by             text not null,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now(),
    unique (analysis_session_id)
);

create index geo_ios_review_imports_session_idx
    on public.geo_ios_review_imports(analysis_session_id);

create trigger geo_ios_review_imports_set_updated_at
    before update on public.geo_ios_review_imports
    for each row execute function public.set_updated_at();

alter table public.geo_ios_review_imports enable row level security;

comment on table public.geo_ios_review_imports is
    'REVIEW MODE（既存レポートのレビュー）専用の取込・再構成・修正方針の記録。1 analysis_session_id'
    'につき1行（Step1で作成、Step2の選択・修正方針決定で更新）。Learning Workbenchが後から'
    'Before(original_text/reconstructed_reading)/After(revised draft)を参照できるようにする。';
