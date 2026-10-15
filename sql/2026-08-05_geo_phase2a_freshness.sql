-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2A: Freshness（既存テーブルのALTERのみ）
-- 作成日: 2026-08-05
-- 前提: sql/2026-08-04_geo_knowledge_core.sql が実行済みであること
--
-- next_review_at/stale_after_daysはPhase1で追加済み。overdue/failure_count等は保存せず、
-- geo_freshness_service.pyがupdate_cadenceとlast_successful_ingestion_atから都度算出する
-- （古いKnowledgeを自動削除しない、という方針のため状態を固定値として持たせない）。
-- =====================================================================

alter table public.geo_knowledge_items
    add column if not exists last_evidence_at timestamptz;

comment on column public.geo_knowledge_items.last_evidence_at is
    'このKnowledge Itemに紐づくSupporting Claimのうち、最も新しいEvidence取得時点。'
    'geo_freshness_service.classify_knowledge_freshnessの判定材料の一つ';

-- Phase2A: 外部データ由来のKnowledge Candidate種別を追加する（11章拡張）。
-- 既存の12種は維持し、4種を追加する（列削除・データ上書きなし）。
alter table public.geo_knowledge_items
    drop constraint if exists geo_knowledge_items_knowledge_type_check;

alter table public.geo_knowledge_items
    add constraint geo_knowledge_items_knowledge_type_check check (
        knowledge_type in (
            'country_foundation', 'actor_profile', 'party_profile',
            'relationship_assessment', 'historical_path_dependency', 'precedent',
            'mechanism', 'reasoning_rule', 'indicator', 'foundation_update',
            'case_finding', 'leverage_card',
            'actor_profile_update', 'relationship_update', 'dependency_update', 'sanctions_update'
        )
    );

-- ロールバック（Phase 2Aでは実行しない）:
-- alter table public.geo_knowledge_items drop constraint if exists geo_knowledge_items_knowledge_type_check;
-- alter table public.geo_knowledge_items add constraint geo_knowledge_items_knowledge_type_check check (
--     knowledge_type in (
--         'country_foundation', 'actor_profile', 'party_profile', 'relationship_assessment',
--         'historical_path_dependency', 'precedent', 'mechanism', 'reasoning_rule', 'indicator',
--         'foundation_update', 'case_finding', 'leverage_card'
--     )
-- );
-- alter table public.geo_knowledge_items drop column if exists last_evidence_at;
