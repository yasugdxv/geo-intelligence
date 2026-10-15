-- =====================================================================
-- Expert AI Phase R1 Chunk3 — geo_knowledge_items.knowledge_typeへ
-- "institution_profile" を追加
--
-- 既存の同種変更（2026-08-05 foundation_update等追加、2026-08-19
-- actor_profile_update等追加、2026-08-29 report_finding追加、2026-08-31
-- media_profile等追加）と完全に同型のCHECK制約差し替えのみ。列追加・データ移行・
-- 既存行への影響は一切ない。
--
-- 意味:
--   institution_profile = Person Actorでは説明できない制度的意思決定主体
--   （BIS/US Congress/NATO等）の最小構造。mandate/decision_authority/constraints/
--   internal_stakeholders/external_dependencies/historical_decision_patterns/
--   known_exceptions/evidence_idsのみを持ち、擬人化（「BISの思想」「NATOの性格」等）
--   はしない——Person Actor（geo_knowledge_items.knowledge_type='actor_profile'）と
--   同じgeo_knowledge_itemsテーブル・同じentity_id起点の取得パターンを再利用するが、
--   Personに固有のフィールド（persistent_preference/decision_preferences/red_lines等、
--   「志向」「性格」に相当するもの）は一切持たせない。
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
            'media_profile', 'media_episode', 'media_pattern',
            'institution_profile'
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
--             'report_finding',
--             'media_profile', 'media_episode', 'media_pattern'
--         ));
-- =====================================================================
