-- =====================================================================
-- 地政学インテリジェンス基盤 地政学専門家AI本格版: 新規テーブル一式
-- 作成日: 2026-08-03
-- 対象: Supabase（PostgreSQL）。新規構築、ALTER/DROP/移行処理は含まない。
--
-- 前提: sql/2026-08-02_geo_core_schema.sql・2026-08-03_geo_indicator_tagging.sql
-- が実行済みであること
--
-- 指示書14章の地政学専門家AI（7モード）用。15段階Reasoning Pipelineは実装上、
-- Source Pack作成(コード)→1回の構造化LLM呼び出し→Quality Guard(コード)→保存、という
-- 流れに集約している（geo_expert_engine.py参照）。Prompt version・Knowledge version・
-- Model・Source Packをすべて記録し再現可能にする（絶対原則10）。
--
-- 作成順: geo_expert_requests → geo_source_packs → geo_expert_responses
--        → geo_quality_evaluations → geo_expert_feedback
-- =====================================================================


-- ---- 1. geo_expert_requests（依頼） ----

create table public.geo_expert_requests (
    request_id  uuid primary key default gen_random_uuid(),
    theme_id    uuid references public.geo_themes(theme_id) on delete set null,
    mode        text not null
        check (mode in (
            'theme_design', 'research_design', 'case_analysis', 'outline_review',
            'draft_review', 'executive_brief', 'analyst_coaching'
        )),
    query_text  text not null,
    created_at  timestamptz not null default now()
);

create index geo_expert_requests_theme_idx on public.geo_expert_requests(theme_id);

alter table public.geo_expert_requests enable row level security;

comment on table public.geo_expert_requests is
    '地政学専門家AIへの依頼。指示書14.2章の7モードのいずれかを指定する';


-- ---- 2. geo_source_packs（依頼時点の参照情報スナップショット） ----

create table public.geo_source_packs (
    source_pack_id      uuid primary key default gen_random_uuid(),
    request_id          uuid not null references public.geo_expert_requests(request_id) on delete cascade,
    document_ids        jsonb not null default '[]'::jsonb,
    event_ids           jsonb not null default '[]'::jsonb,
    evidence_ids        jsonb not null default '[]'::jsonb,
    observation_snapshot jsonb not null default '[]'::jsonb,
    created_at          timestamptz not null default now()
);

create index geo_source_packs_request_idx on public.geo_source_packs(request_id);

alter table public.geo_source_packs enable row level security;

comment on table public.geo_source_packs is
    '依頼時点でSource Packに含めたdocument/event/evidenceのid一覧と指標観測値スナップショット。'
    '後から遡って「何を根拠に回答したか」を再現できるようにする';


-- ---- 3. geo_expert_responses（構造化レスポンス） ----

create table public.geo_expert_responses (
    response_id      uuid primary key default gen_random_uuid(),
    request_id       uuid not null references public.geo_expert_requests(request_id) on delete cascade,
    source_pack_id   uuid not null references public.geo_source_packs(source_pack_id) on delete restrict,
    mode             text not null,
    prompt_version   text not null,
    knowledge_version text not null,
    model            text not null,
    response_json    jsonb not null,
    created_at       timestamptz not null default now()
);

create index geo_expert_responses_request_idx on public.geo_expert_responses(request_id);

alter table public.geo_expert_responses enable row level security;

comment on table public.geo_expert_responses is
    '地政学専門家AIの構造化レスポンス本体(schemas/geo_expert_ai_response.schema.json準拠)。'
    'prompt_version/knowledge_version/modelを保存し再現可能にする（絶対原則10）';


-- ---- 4. geo_quality_evaluations（Quality Guard評価） ----

create table public.geo_quality_evaluations (
    evaluation_id uuid primary key default gen_random_uuid(),
    response_id   uuid not null references public.geo_expert_responses(response_id) on delete cascade,
    checks        jsonb not null,
    passed_count  integer not null,
    failed_count  integer not null,
    created_at    timestamptz not null default now()
);

create index geo_quality_evaluations_response_idx on public.geo_quality_evaluations(response_id);

alter table public.geo_quality_evaluations enable row level security;

comment on table public.geo_quality_evaluations is
    '指示書14.6章のQuality Guard評価結果。Guard自体は承認せず、human reviewの判定材料として保持する';


-- ---- 5. geo_expert_feedback（人によるフィードバック） ----

create table public.geo_expert_feedback (
    feedback_id uuid primary key default gen_random_uuid(),
    response_id uuid not null references public.geo_expert_responses(response_id) on delete cascade,
    rating      text
        check (rating is null or rating in ('good', 'needs_improvement', 'incorrect')),
    comment     text,
    created_at  timestamptz not null default now()
);

create index geo_expert_feedback_response_idx on public.geo_expert_feedback(response_id);

alter table public.geo_expert_feedback enable row level security;

comment on table public.geo_expert_feedback is
    '専門家・アナリストによる回答へのフィードバック（Prompt/Knowledge改善のための入力）';
