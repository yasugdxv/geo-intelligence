-- 地政学インテリジェンス基盤 Phase 2F-2: Intelligence AI Search Results UI
-- Relevant Snippet（「なぜこのReportがHITしたか」の可視化）
--
-- Retrieval logic（Permission Evaluation→Eligible Corpus→ranking順序）は一切変更しない。
-- search_geo_reports/search_geo_knowledge_itemsの出力へ、既にranking計算に使っている
-- 同じ列（search_text_source / title+summary）からts_headlineで抜粋したsnippetを追加する
-- だけ——新しいLLM呼び出しは無い（Phase 2F-2仕様「新しいLLM呼び出しは不要」）。
--
-- 安全な表示方法: ts_headlineの既定StartSel/StopSel（<b>/</b>）はHTMLタグをそのまま
-- 差し込むため、report/knowledge_itemのtitle/summary自体に'<'等が含まれていた場合に
-- フロントで意図しないHTML解釈が起きうる。ここではStartSel/StopSelを制御文字
-- chr(2)/chr(3)（STX/STOP、通常のテキストに出現しない）に変更し、フロント側は
-- dangerouslySetInnerHTMLを一切使わず、この制御文字で分割してプレーンテキストの
-- 前後半分＋<mark>要素として安全にレンダリングする。
--
-- 既存呼び出し元（RETURNS TABLEへsnippet列を追加、列集合の変更のためcreate or replaceが
-- 使えない）に対する後方互換: 既存呼び出し元はSELECT *ではなく個別列参照のみのため、
-- 新しい列が増えても壊れない。p_eligible_ids等の引数シグネチャ自体は変更しない。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは実行しない）。

drop function if exists public.search_geo_reports(text, uuid[], boolean, int);

create function public.search_geo_reports(
    p_query text,
    p_eligible_ids uuid[] default null,
    p_include_demo boolean default false,
    p_limit int default 20
) returns table (
    report_id uuid, text_score real, snippet text
)
language sql stable
as $$
    select r.report_id,
           ts_rank_cd(r.search_vector, plainto_tsquery('simple', p_query))::real as text_score,
           ts_headline(
               'simple', coalesce(r.search_text_source, ''), plainto_tsquery('simple', p_query),
               'MaxFragments=1,MaxWords=40,MinWords=15,ShortWord=3,HighlightAll=false,StartSel='
                   || chr(2) || ',StopSel=' || chr(3)
           ) as snippet
    from public.geo_reports r
    where r.search_vector @@ plainto_tsquery('simple', p_query)
      and (p_eligible_ids is null or r.report_id = any(p_eligible_ids))
      and (p_include_demo or r.is_demo = false)
    order by text_score desc
    limit p_limit;
$$;

comment on function public.search_geo_reports is
    'Phase 2F-1/2F-2: geo_reports向けFull Text Search RPC。p_eligible_idsは呼び出し側
    (geo_intelligence_ai_search_service.py)がPermission Evaluationで事前に絞り込んだIDのみを
    渡す。snippetはranking計算と同じsearch_text_sourceから抜粋（Phase 2F-2追加、ranking/
    permissionロジックへの変更は無い）。';

drop function if exists public.search_geo_knowledge_items(text, text[], text[], boolean, int, uuid[]);

create function public.search_geo_knowledge_items(
    p_query text,
    p_status text[] default null,
    p_knowledge_types text[] default null,
    p_include_demo boolean default false,
    p_limit int default 20,
    p_eligible_ids uuid[] default null
) returns table (
    knowledge_item_id uuid, title text, summary text, knowledge_type text, status text,
    is_demo boolean, text_score real, snippet text
)
language sql stable
as $$
    select ki.knowledge_item_id, ki.title, ki.summary, ki.knowledge_type, ki.status, ki.is_demo,
           ts_rank_cd(ki.search_vector, plainto_tsquery('simple', p_query))::real as text_score,
           ts_headline(
               'simple', coalesce(ki.title, '') || ' ' || coalesce(ki.summary, ''),
               plainto_tsquery('simple', p_query),
               'MaxFragments=1,MaxWords=40,MinWords=15,ShortWord=3,HighlightAll=false,StartSel='
                   || chr(2) || ',StopSel=' || chr(3)
           ) as snippet
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
-- drop function if exists public.search_geo_reports(text, uuid[], boolean, int);
-- create function public.search_geo_reports(...) returns table (report_id uuid, text_score real) ... -- snippet無し版へ戻す
-- drop function if exists public.search_geo_knowledge_items(text, text[], text[], boolean, int, uuid[]);
-- create function public.search_geo_knowledge_items(...) returns table (..., text_score real) ... -- snippet無し版へ戻す
-- =====================================================================
