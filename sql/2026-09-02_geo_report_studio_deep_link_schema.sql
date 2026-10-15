-- =====================================================================
-- Report Studio Phase6: Closed-loop Reassessment用の最小追加カラム
-- 作成日: 2026-09-02
-- 対象: Supabase（PostgreSQL）。既存テーブルへのALTER（列追加のみ、破壊的変更なし）。
--
-- 方針: geo_events.unknown_fields（既存jsonb）は既にReport Studio由来のsynthesis_id/
-- item_keyを格納しているため変更不要。geo_knowledge_gapsには同種の汎用jsonbが無いため、
-- Deep Link専用の1列だけ追加する（origin_type/requested_by_appは既存の「発生源の種別」
-- 用途のまま変更せず、本列は「UI遷移に必要な構造化参照情報」専用にする）。
-- =====================================================================

alter table public.geo_knowledge_gaps
    add column if not exists source_context jsonb;

comment on column public.geo_knowledge_gaps.source_context is
    '外部発生源（Report Studio等）へのDeep Link用メタデータ。Report Studio起源の場合の例:
    {"app":"report_studio","project_id":...,"run_id":...,"signal_id":...,"narrative_id":...,
    "synthesis_id":...}。origin_type/requested_by_appは発生源の「種別」を表す既存カラムで
    あり、本列はUI遷移に必要な構造化参照情報専用（両者を混同しない）。';
