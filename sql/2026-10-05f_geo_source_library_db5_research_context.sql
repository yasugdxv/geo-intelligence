-- =====================================================================
-- Source Library / Monitoring 基盤 — Phase DB-5: Research Studio Project への調査コンテキスト
--
-- Theme / Query / selected source_ids を Research Studio の Project へ引き継ぐための
-- nullable jsonb列を1つ追加するのみ（additive）。既存Project・既存Pipelineは無改修。
-- 形式: {"theme": str, "query": str, "source_ids": [uuid], "source_names": [str],
--        "source_set_id": uuid|null, "origin": "theme_explorer|source_library|monitoring"}
--
-- 適用方法: Supabase SQL Editorで手動適用。destructive文なし。
-- =====================================================================

alter table public.geo_report_projects
    add column if not exists research_context jsonb;

comment on column public.geo_report_projects.research_context is
    'Theme Explorer/Source Library/Monitoringから引き継いだ調査コンテキスト（theme/query/source_ids）。';

-- Rollback:
--   alter table public.geo_report_projects drop column if exists research_context;
