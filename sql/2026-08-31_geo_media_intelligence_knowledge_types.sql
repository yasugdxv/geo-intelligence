-- =====================================================================
-- Media Intelligence Build（M5）— geo_knowledge_items.knowledge_typeへ
-- "media_profile" / "media_episode" / "media_pattern" を追加
--
-- 既存の3回の同種変更（2026-08-05 foundation_update等追加、
-- 2026-08-19 actor_profile_update等追加、2026-08-29 report_finding追加）と
-- 完全に同型のCHECK制約差し替えのみ。列追加・データ移行・既存行への影響は一切ない。
-- 意味:
--   media_profile = 比較的安定した媒体単位のIntelligence（Stable Media Profile）
--   media_episode = 個別Case/Event単位のHistorical Media Observation
--   media_pattern = 複数episodeから導出したRepeated Pattern Candidate
-- =====================================================================

alter table public.geo_knowledge_items
    drop constraint if exists geo_knowledge_items_knowledge_type_check,
    add constraint geo_knowledge_items_knowledge_type_check
        check (knowledge_type in (
            'country_foundation', 'actor_profile', 'party_profile',
            'relationship_assessment', 'historical_path_dependency', 'precedent',
            'mechanism', 'reasoning_rule', 'indicator', 'foundation_update',
            'case_finding', 'leverage_card',
            'actor_profile_update', 'relationship_update', 'dependency_update', 'sanctions_update',
            'report_finding',
            'media_profile', 'media_episode', 'media_pattern'
        ));

-- =====================================================================
-- ロールバック手順（実行しない。記録のみ）
--
-- alter table public.geo_knowledge_items
--     drop constraint if exists geo_knowledge_items_knowledge_type_check,
--     add constraint geo_knowledge_items_knowledge_type_check
--         check (knowledge_type in (
--             'country_foundation', 'actor_profile', 'party_profile',
--             'relationship_assessment', 'historical_path_dependency', 'precedent',
--             'mechanism', 'reasoning_rule', 'indicator', 'foundation_update',
--             'case_finding', 'leverage_card',
--             'actor_profile_update', 'relationship_update', 'dependency_update', 'sanctions_update',
--             'report_finding'
--         ));
-- =====================================================================
