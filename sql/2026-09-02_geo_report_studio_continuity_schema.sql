-- =====================================================================
-- Report Studio Phase4: Continuous Intelligence用の最小追加カラム
-- 作成日: 2026-09-02（geo_report_studio_schema.sql / synthesis_schema.sqlの追補）
-- 対象: Supabase（PostgreSQL）。既存テーブルへのALTER（列追加のみ、破壊的変更なし）。
--
-- 前提: sql/2026-09-02_geo_report_studio_schema.sql および
-- sql/2026-09-02_geo_report_studio_synthesis_schema.sql が適用済みであること
--
-- 方針: base_key・record membershipは新規カラムを持たず、既存
-- geo_report_records（signal_id/narrative_id/time_bucket/source_bucket/
-- split_label/language/matched_terms/match_status）から決定的に再構築する
-- （geo_report_processing_service.get_match_signature等、既存の決定的関数を
-- そのまま再利用できるため、新規カラム・新規保存先を増やさない）。
-- 本ファイルで追加するのは「どのrunがどのrunを起点にした差分実行か」という
-- 実行系譜（lineage）のみ。
-- =====================================================================

alter table public.geo_report_collection_runs
    add column if not exists baseline_run_id uuid references public.geo_report_collection_runs(run_id) on delete set null,
    add column if not exists run_mode text not null default 'full'
        check (run_mode in ('full', 'incremental'));

comment on column public.geo_report_collection_runs.baseline_run_id is
    'run_mode=incrementalの場合、差分処理の起点にした前回run（T0）。fullの場合はnull';
comment on column public.geo_report_collection_runs.run_mode is
    'full=全期間を毎回処理／incremental=baseline_run_idとの差分のみ処理'
    '（未変更クラスタはclassificationを再利用する）';
