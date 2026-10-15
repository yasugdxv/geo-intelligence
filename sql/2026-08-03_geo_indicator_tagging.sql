-- =====================================================================
-- 地政学インテリジェンス基盤 Phase 2: 指標タグ付け・定性ラベル用カラム追加
-- 作成日: 2026-08-03
-- 対象: Supabase（PostgreSQL）。ALTER のみ、新規テーブルは含まない。
--
-- 前提: sql/2026-08-02_geo_core_schema.sql が実行済みであること
--
-- geo_events に indicator_code を追加し、LLMが「この事象がどの指標に最も近いか」を
-- 分類できるようにする（1事象＝1指標、多重付与しない。既存の競合目標カテゴリと同じ
-- 「1レコード＝1カテゴリ」の設計方針を踏襲）。geo_observations に qualitative_label を
-- 追加し、qualitative_llm_state 方式（POL-01/DIP-02等）の人間可読な状態ラベルを保持する。
-- =====================================================================

alter table public.geo_events
    add column indicator_code text references public.geo_indicator_definitions(indicator_code) on delete set null;

create index geo_events_indicator_code_idx on public.geo_events(indicator_code);

comment on column public.geo_events.indicator_code is
    'この事象が主に紐づく指標コード（例: MIL-01）。LLMによる分類結果、不明な場合はnull';

alter table public.geo_observations
    add column qualitative_label text;

comment on column public.geo_observations.qualitative_label is
    'qualitative_llm_state方式で分類された状態の人間可読ラベル（例: 強硬化）。'
    'raw_valueはこのラベルに対応する序数値（コード側でマッピング）';
