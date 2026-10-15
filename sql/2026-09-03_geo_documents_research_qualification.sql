-- =====================================================================
-- Expert AI Phase R1 Chunk4 — External Research用の最小追加カラム
-- 作成日: 2026-09-03
-- 対象: Supabase（PostgreSQL）。既存テーブルへのALTER（列追加のみ、破壊的変更なし）。
--
-- 方針: External Researchで取得しEvidence Qualificationを通過した文書のみ、
-- 既存geo_documentsへ通常の文書として永続化する（新しいEvidenceストレージを作らない、
-- 既存build_source_packのキーワードマッチ検索にそのまま乗せる＝将来の別クエリでも
-- 再利用可能にする）。source_type（誰が発信したか）は既存geo_source_registry.
-- source_typeをそのまま流用する（get-or-create、新規カラム不要）。本migrationで
-- 追加するのは、geo_source_registryには無い「このEvidence自体の性質・時間的有効性」
-- の軸のみ。
--
-- evidence_class / temporal_statusは既存Expert AI Depth & Grounding Upgrade /
-- Phase R1 Chunk2で確立した値の集合と概念的に対応するが、チェック制約による重複管理を
-- 避けるため緩いtext型のままとする（Python側（geo_expert_research_service.py）で
-- 許容値を管理する）。
-- =====================================================================

alter table public.geo_documents
    add column if not exists evidence_class text,
    add column if not exists temporal_status text,
    add column if not exists superseded_by uuid references public.geo_documents(document_id) on delete set null,
    add column if not exists research_origin text;

comment on column public.geo_documents.evidence_class is
    'Expert AI Phase R1 Chunk4: このDocumentの性質（current_direct_evidence/
    current_indirect_evidence/historical_actor_pattern/historical_media_pattern/
    structural_reference/model_general_knowledge/unsupported_assumption）。External
    Research由来の新規取得分はcurrent_direct_evidenceを既定とする。既存crawler由来の
    文書はnullのままでよい（後方互換、既存挙動に影響しない）。';

comment on column public.geo_documents.temporal_status is
    'Expert AI Phase R1 Chunk4: 時間的有効性（current/recent/historical/superseded/
    unknown）。「DBに存在する＝Current Evidence」とは扱わない——Retrieval時に
    published_at等からtemporal applicabilityを再評価する。supersededと判定された
    Documentは削除せず保持するが、Research SufficiencyのCurrent Evidenceカウントには
    含めない。';

comment on column public.geo_documents.superseded_by is
    'Expert AI Phase R1 Chunk4: このDocumentが後続のより新しいDocumentに置き換えられた
    場合の参照先（削除はしない、Historical Evidenceとして保持する）。';

comment on column public.geo_documents.research_origin is
    'Expert AI Phase R1 Chunk4: この文書がどの経路で投入されたか
    （crawler/manual_upload/external_research）。既存文書はnullのままでよい
    （後方互換）。External Research由来の文書のみexternal_researchを設定する。';
