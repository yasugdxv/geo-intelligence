-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2A: Retrieval Trace + Full Text Search RPC
-- 作成日: 2026-08-05
-- 前提: sql/2026-08-04_geo_knowledge_search_fts.sql
--      （geo_knowledge_items/geo_claims/geo_entitiesのsearch_vector列）が実行済みであること
--
-- pgvectorは今回のPhase2Aでは利用しない（Embedding Deployment未確認のため。
-- docs/geopolitical-retrieval-design.md参照）。検索はPostgreSQL Full Text Search中心とし、
-- 全件取得してPython側でランキングするのではなく、DB側でTop-K + ts_rank_cdスコアを計算する。
--
-- 作成順: geo_retrieval_runs → geo_retrieval_results
--        → search_geo_knowledge_items / search_geo_claims / search_geo_entities
-- =====================================================================


-- ---- 1. geo_retrieval_runs（検索・Retrieval実行の記録） ----

create table public.geo_retrieval_runs (
    retrieval_run_id    uuid primary key default gen_random_uuid(),
    case_id             uuid references public.geo_cases(case_id) on delete set null,
    expert_request_id   uuid references public.geo_expert_requests(request_id) on delete set null,
    query_text          text,
    filters             jsonb,
    retrieval_mode      text not null default 'hybrid_fts'
        check (retrieval_mode in ('hybrid_fts', 'expert_context')),
    started_at          timestamptz not null default now(),
    completed_at        timestamptz,
    retrieved_count     integer not null default 0,
    model_configuration jsonb,
    is_demo             boolean not null default false
);

create index geo_retrieval_runs_case_idx on public.geo_retrieval_runs(case_id);
create index geo_retrieval_runs_expert_request_idx on public.geo_retrieval_runs(expert_request_id);

alter table public.geo_retrieval_runs enable row level security;

comment on table public.geo_retrieval_runs is
    '検索（Knowledge Search画面）またはgeo_expert_engineのContext構築1回分の実行記録';


-- ---- 2. geo_retrieval_results（各結果のスコア内訳・採否理由） ----

create table public.geo_retrieval_results (
    retrieval_result_id  uuid primary key default gen_random_uuid(),
    retrieval_run_id     uuid not null references public.geo_retrieval_runs(retrieval_run_id) on delete cascade,
    result_type          text not null
        check (result_type in ('entity', 'relationship', 'knowledge_item', 'claim', 'event', 'observation')),
    result_id            uuid not null,
    rank                 integer,
    entity_score         real,
    text_score           real,
    vector_score         real,
    source_score         real,
    recency_score        real,
    approval_score       real,
    final_score          real,
    inclusion_reason     text,
    exclusion_reason     text,
    used_in_final_context boolean not null default false,
    created_at           timestamptz not null default now()
);

create index geo_retrieval_results_run_idx on public.geo_retrieval_results(retrieval_run_id);

alter table public.geo_retrieval_results enable row level security;

comment on table public.geo_retrieval_results is
    '各Retrieval結果のスコア内訳と採否理由。vector_scoreはPhase2Aでは常にnull'
    '（Embedding Deployment未確認のため。docs/geopolitical-retrieval-design.md参照）。'
    'exclusion_reasonには token_budget_exceeded / no_supporting_evidence_chain 等を記録する';


-- ---- 3. Full Text Search RPC関数（PostgREST経由でclient.rpc()から呼び出す） ----

create or replace function public.search_geo_knowledge_items(
    p_query text,
    p_status text[] default null,
    p_knowledge_types text[] default null,
    p_include_demo boolean default false,
    p_limit int default 20
) returns table (
    knowledge_item_id uuid, title text, summary text, knowledge_type text, status text,
    is_demo boolean, text_score real
)
language sql stable
as $$
    select ki.knowledge_item_id, ki.title, ki.summary, ki.knowledge_type, ki.status, ki.is_demo,
           ts_rank_cd(ki.search_vector, plainto_tsquery('simple', p_query))::real as text_score
    from public.geo_knowledge_items ki
    where ki.search_vector @@ plainto_tsquery('simple', p_query)
      and (p_status is null or ki.status = any(p_status))
      and (p_knowledge_types is null or ki.knowledge_type = any(p_knowledge_types))
      and (p_include_demo or ki.is_demo = false)
    order by text_score desc
    limit p_limit;
$$;

create or replace function public.search_geo_claims(
    p_query text,
    p_include_demo boolean default false,
    p_limit int default 20
) returns table (
    claim_id uuid, claim_text text, information_class text, is_demo boolean, text_score real
)
language sql stable
as $$
    select c.claim_id, c.claim_text, c.information_class, c.is_demo,
           ts_rank_cd(c.search_vector, plainto_tsquery('simple', p_query))::real as text_score
    from public.geo_claims c
    where c.search_vector @@ plainto_tsquery('simple', p_query)
      and (p_include_demo or c.is_demo = false)
    order by text_score desc
    limit p_limit;
$$;

create or replace function public.search_geo_entities(
    p_query text,
    p_entity_types text[] default null,
    p_include_demo boolean default false,
    p_limit int default 20
) returns table (
    entity_id uuid, canonical_name text, entity_type text, is_demo boolean, text_score real
)
language sql stable
as $$
    select e.entity_id, e.canonical_name, e.entity_type, e.is_demo,
           ts_rank_cd(e.search_vector, plainto_tsquery('simple', p_query))::real as text_score
    from public.geo_entities e
    where e.search_vector @@ plainto_tsquery('simple', p_query)
      and (p_entity_types is null or e.entity_type = any(p_entity_types))
      and (p_include_demo or e.is_demo = false)
    order by text_score desc
    limit p_limit;
$$;

comment on function public.search_geo_knowledge_items is
    'Full Text SearchのTop-KをDB側で計算する（全件取得してPython側でランキングしない）。'
    'plainto_tsquery(''simple'', ...)は日本語の複合語に弱いため、呼び出し側'
    '(geo_search_service.py)でEntity完全一致・正規化文字列部分一致と合成してスコアリングする';


-- =====================================================================
-- ロールバック手順（Phase 2Aでは実行しない。記録のみ）
--
-- drop function if exists public.search_geo_entities(text, text[], boolean, int);
-- drop function if exists public.search_geo_claims(text, boolean, int);
-- drop function if exists public.search_geo_knowledge_items(text, text[], text[], boolean, int);
-- drop table if exists public.geo_retrieval_results;
-- drop table if exists public.geo_retrieval_runs;
-- =====================================================================
