-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2B: Network Graph（Relationship Explorer）
-- 作成日: 2026-08-06
-- 前提: sql/2026-08-04_geo_knowledge_core.sql（geo_entities/geo_relationships/
--      geo_relationship_issues）が適用済みであること
--
-- world_monitor_dashboardのfetch_engagement_mapで実際に発生した「巨大なin.()フィルタが
-- URL長超過で400エラーになり、それが握りつぶされて件数0件表示になっていた」バグと同じ
-- 落とし穴を避けるため、Entity一括取得はPostgRESTの素朴なin.()に頼らず、結合をDB側で
-- 完結させる単一のRPC関数として実装する（Phase2Aのsearch_geo_*と同じ設計方針）。
-- =====================================================================

create or replace function public.get_relationship_graph_for_issue(
    p_issue_id uuid,
    p_include_demo boolean default false
) returns jsonb
language sql stable
as $$
    with issue_relationships as (
        select r.* from public.geo_relationships r
        join public.geo_relationship_issues ri on ri.relationship_id = r.relationship_id
        where ri.issue_id = p_issue_id
          and (p_include_demo or r.is_demo = false)
    ),
    graph_entity_ids as (
        select from_entity_id as entity_id from issue_relationships
        union
        select to_entity_id as entity_id from issue_relationships
    )
    select jsonb_build_object(
        'nodes', coalesce((select jsonb_agg(to_jsonb(e)) from public.geo_entities e
                           where e.entity_id in (select entity_id from graph_entity_ids)), '[]'::jsonb),
        'edges', coalesce((select jsonb_agg(to_jsonb(r)) from issue_relationships r), '[]'::jsonb)
    );
$$;

comment on function public.get_relationship_graph_for_issue is
    'Issue単位のNetwork Graph（node=Entity, edge=Relationship）をDB側の結合1回で返す。'
    'PostgRESTの素朴なin.()フィルタに依存せず、Entity一括取得のURL長超過を構造的に回避する';


-- =====================================================================
-- ロールバック手順（Phase 2Bでは実行しない。記録のみ）
--
-- drop function if exists public.get_relationship_graph_for_issue(uuid, boolean);
-- =====================================================================
