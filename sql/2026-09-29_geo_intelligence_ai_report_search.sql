-- 地政学インテリジェンス基盤 Phase 2F-1: Permission-filtered Lexical Hybrid（FTS-based）Report Search
--
-- 既存geo_knowledge_items/geo_claims/geo_entitiesと同じFull Text Search方式
-- （sql/2026-08-04_geo_knowledge_search_fts.sql、sql/2026-08-05_geo_phase2a_retrieval_trace.sql）を
-- geo_reportsへ拡張する。pgvector/Embeddingは今回も使わない（Azure OpenAIに
-- embeddings用Deploymentが存在しないことをPhase 2F Auditで再確認済み、
-- geo_report_cluster_semantic_service.py等の既存コメントと同じ結論）。
--
-- geo_reportsのtitleは自テーブルに無く（geo_documents側、1:1関係）、generated column
-- は他テーブルを参照できないため、Python側（geo_report_service.ingest_report/
-- create_report_version）が書き込み時にtitle等を合成したsearch_text_sourceを
-- 明示的にセットし、search_vector自体はそのsearch_text_sourceからのgenerated column
-- とする（自テーブル内で完結、real-time・バッチ不要）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない、既存migrationと同じ運用）。

-- ---- 1. geo_reports: search_text_source（Python書き込み）+ search_vector（generated） ----
alter table public.geo_reports
    add column if not exists search_text_source text;

alter table public.geo_reports
    add column if not exists search_vector tsvector
    generated always as (to_tsvector('simple', coalesce(search_text_source, ''))) stored;

create index if not exists geo_reports_search_idx on public.geo_reports using gin(search_vector);

comment on column public.geo_reports.search_text_source is
    'Phase 2F-1: search_vector(generated column)の元になるテキスト（title/author/
    owner_department/report_type/summary/business_context/topics/country_region/
    key_actors/keywordsを結合したもの）。geo_report_service.ingest_report/
    create_report_versionが書き込み時に設定する（real-time、バッチ不要）。';

-- 既存行（Phase 2D以前含む）のbackfill。geo_documents.titleとJOINできるのはこの
-- 一度きりのUPDATE文の中だけ（generated columnの制約は自テーブル内参照のみのため、
-- ここで先にsearch_text_sourceへ実体化しておく）。
update public.geo_reports r
set search_text_source = concat_ws(' ',
    d.title, r.author, r.owner_department, r.report_type, r.summary, r.business_context,
    (select string_agg(v, ' ') from jsonb_array_elements_text(coalesce(r.topics, '[]'::jsonb)) v),
    (select string_agg(v, ' ') from jsonb_array_elements_text(coalesce(r.country_region, '[]'::jsonb)) v),
    (select string_agg(v, ' ') from jsonb_array_elements_text(coalesce(r.key_actors, '[]'::jsonb)) v),
    (select string_agg(v, ' ') from jsonb_array_elements_text(coalesce(r.keywords, '[]'::jsonb)) v)
)
from public.geo_documents d
where r.document_id = d.document_id
  and r.search_text_source is null;

-- ---- 2. RPC: search_geo_reports（既存search_geo_knowledge_itemsと同じ形） -------------
create or replace function public.search_geo_reports(
    p_query text,
    p_eligible_ids uuid[] default null,
    p_include_demo boolean default false,
    p_limit int default 20
) returns table (
    report_id uuid, text_score real
)
language sql stable
as $$
    select r.report_id,
           ts_rank_cd(r.search_vector, plainto_tsquery('simple', p_query))::real as text_score
    from public.geo_reports r
    where r.search_vector @@ plainto_tsquery('simple', p_query)
      and (p_eligible_ids is null or r.report_id = any(p_eligible_ids))
      and (p_include_demo or r.is_demo = false)
    order by text_score desc
    limit p_limit;
$$;

comment on function public.search_geo_reports is
    'Phase 2F-1: geo_reports向けFull Text Search RPC（既存search_geo_knowledge_itemsと
    同じ設計）。p_eligible_idsは呼び出し側(geo_intelligence_ai_search_service.py)が
    Permission Evaluation（geo_knowledge_access_policy）で事前に絞り込んだIDのみを渡す
    ——retrieveしてから権限で落とすのではなく、retrieval対象自体をeligible corpusへ
    先に限定する（Phase 2F §3絶対条件）。nullの場合は無フィルタ（既存呼び出し元の
    後方互換のためのdefaultであり、Intelligence AI検索は必ず値を渡す）。';

-- ---- 3. 既存search_geo_knowledge_itemsへ同じp_eligible_idsを追加（後方互換） -----------
-- 既存シグネチャに無かった引数をdefault付きで追加するだけなので、既存呼び出し元
-- （/geo/knowledge-searchのsearch_knowledge_items等、p_eligible_idsを渡さない）の
-- 挙動は一切変わらない。
create or replace function public.search_geo_knowledge_items(
    p_query text,
    p_status text[] default null,
    p_knowledge_types text[] default null,
    p_include_demo boolean default false,
    p_limit int default 20,
    p_eligible_ids uuid[] default null
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
      and (p_eligible_ids is null or ki.knowledge_item_id = any(p_eligible_ids))
    order by text_score desc
    limit p_limit;
$$;

-- =====================================================================
-- ロールバック手順（今回は実行しない。記録のみ）
--
-- create or replace function public.search_geo_knowledge_items(
--     p_query text, p_status text[] default null, p_knowledge_types text[] default null,
--     p_include_demo boolean default false, p_limit int default 20
-- ) returns table (...) ... -- 旧シグネチャへ戻す
-- drop function if exists public.search_geo_reports(text, uuid[], boolean, int);
-- drop index if exists geo_reports_search_idx;
-- alter table public.geo_reports drop column if exists search_vector;
-- alter table public.geo_reports drop column if exists search_text_source;
-- =====================================================================
