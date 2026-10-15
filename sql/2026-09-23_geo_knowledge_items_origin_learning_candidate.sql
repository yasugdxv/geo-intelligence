-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): geo_knowledge_items.origin
-- CHECK制約へ 'geo_ios_learning_candidate' を追加する
-- （Application/Schema Contract Finding、Mechanical E2E Pilot、2026-09-23）
--
-- 背景: geo_ios_learning_candidate_service.promote_to_knowledge_item()は、
-- Learning Candidate経由で生成したKnowledge Itemへ常にorigin=
-- 'geo_ios_learning_candidate'を設定する（このモジュール自体は2026-09-16の
-- Phase G IOS Learning実装時に追加済み）。しかしgeo_knowledge_items_origin_check
-- （直近の定義: sql/2026-09-04_geo_async_research_link.sql）へこの値を追加する
-- migrationがどこにも存在せず、対象DBの実制約は
--   'manual', 'connector_derived', 'case_candidate_extraction',
--   'external_claim_extraction', 'internal_expert_review', 'async_expert_research'
-- の6値のみを許可していた（Application/Schema Contract不整合）。
-- Mechanical E2E Pilot実行時に、promote_to_knowledge_item()の実INSERTが
-- 23514 (check_violation) で必ず失敗することを発見した——つまりLearning
-- Candidate→Knowledge Itemの昇格経路は、この環境で一度も成功したことが
-- なかったと考えられる（既存geo_ios_learning_candidates 2件とも
-- approved_knowledge_item_id=NULLである事実と整合）。
--
-- geo_knowledge_service._validate_active_lineage()（Human Gate #2 P0 Closure、
-- 2026-09-23）はorigin='geo_ios_learning_candidate'を前提にLearning
-- Candidate lineageを検証するため、既存許可値（internal_expert_review等）への
-- 読み替え・流用はしない——provenanceの出自を正確に記録するという設計そのものを
-- 曖昧にしてしまうため。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する。destructive文は
-- 含まない。安全なconstraint置換パターン（NOT VALID追加→VALIDATE→旧DROP→
-- rename）を使い、origin列が一瞬でも無制約になる時間を作らない。
-- =====================================================================

begin;

alter table public.geo_knowledge_items
    add constraint geo_knowledge_items_origin_check_v2
        check (origin in (
            'manual', 'connector_derived', 'case_candidate_extraction',
            'external_claim_extraction', 'internal_expert_review',
            'async_expert_research', 'geo_ios_learning_candidate'
        )) not valid;

alter table public.geo_knowledge_items
    validate constraint geo_knowledge_items_origin_check_v2;

alter table public.geo_knowledge_items
    drop constraint geo_knowledge_items_origin_check;

alter table public.geo_knowledge_items
    rename constraint geo_knowledge_items_origin_check_v2 to geo_knowledge_items_origin_check;

commit;

comment on constraint geo_knowledge_items_origin_check on public.geo_knowledge_items is
    'geo_ios_learning_candidate: IOS Learning Candidate経由でpromote_to_knowledge_item()が'
    '生成したKnowledge Item。geo_knowledge_service._validate_active_lineage()がactive遷移時に'
    'このoriginを検出し、geo_ios_learning_candidates.approved_knowledge_item_idが対応する'
    'approved状態のCandidateを指しているかを検証する（Human Gate #2 P0 Closure）。'
    'それ以外の6値は2026-09-04_geo_async_research_link.sqlのコメント参照。';
