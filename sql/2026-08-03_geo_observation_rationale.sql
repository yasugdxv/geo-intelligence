-- =====================================================================
-- 地政学インテリジェンス基盤: Observationにrationale（LLM分類理由）を追加
-- 作成日: 2026-08-03
-- 対象: Supabase（PostgreSQL）。ALTER のみ。
--
-- 前提: sql/2026-08-02_geo_core_schema.sql が実行済みであること
--
-- qualitative_llm_state方式（POL-01/DIP-02等）で、LLMがどの本文記述を根拠に
-- 状態を分類したかを保存する。指標の詳細ページで「なぜこの値になったか」を
-- 遡って確認できるようにするため（絶対原則: 断定の強さは根拠の強さに一致させる）。
-- =====================================================================

alter table public.geo_observations
    add column rationale text;

comment on column public.geo_observations.rationale is
    'qualitative_llm_state方式での分類根拠（LLMが提示した本文記述の要約）。他の方式ではnull';
