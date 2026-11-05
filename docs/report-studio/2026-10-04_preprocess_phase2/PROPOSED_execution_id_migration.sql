-- ============================================================================
-- PROPOSED / NOT EXECUTED（2026-10-04）: Run isolation の Option B（execution_id 列の追加）
--
-- このファイルは提案であり、sql/ には置いていない（誤って一括適用されないようにするため）。
-- 実行にはDB schema変更の承認が必要。適用前に REPORT_PHASE2.md の「F. Run Isolation」を確認すること。
--
-- 背景: geo_report_records / geo_report_analysis_clusters / geo_report_sentiment_clusters と
--       各 *_representatives は run_id でしか区別されず、保存処理は insert のみ。同じ Run で Preprocess を
--       やり直すと旧実行の行が残り、export に混ざる（Evidence run a1cb0baf: record map の 9.7% が旧ファイル由来）。
--       現在の防止策（スキーマ変更なし）は、execution 単位の artifact（record_id_manifest.csv）との厳密比較で
--       stale/missing があれば ZIP 生成を失敗させること。
-- ============================================================================

-- 1) 列追加（nullable。既存行は NULL のまま = 旧Runは従来どおり run_id で扱う）
alter table public.geo_report_records
    add column if not exists execution_id uuid references public.geo_report_executions(id) on delete cascade;
alter table public.geo_report_analysis_clusters
    add column if not exists execution_id uuid references public.geo_report_executions(id) on delete cascade;
alter table public.geo_report_sentiment_clusters
    add column if not exists execution_id uuid references public.geo_report_executions(id) on delete cascade;
alter table public.geo_report_analysis_cluster_representatives
    add column if not exists execution_id uuid references public.geo_report_executions(id) on delete cascade;
alter table public.geo_report_sentiment_cluster_representatives
    add column if not exists execution_id uuid references public.geo_report_executions(id) on delete cascade;

create index if not exists geo_report_records_execution_idx on public.geo_report_records(execution_id);
create index if not exists geo_report_analysis_clusters_execution_idx on public.geo_report_analysis_clusters(execution_id);
create index if not exists geo_report_sentiment_clusters_execution_idx on public.geo_report_sentiment_clusters(execution_id);

-- 2) アプリ側の変更（このSQLとセット）:
--    - _save_record_map / _save_*_clusters / _save_*_representatives の payload に execution_id を入れる
--    - _fetch_all_records を `execution_id=eq.<canonical_preprocess_execution_id>` で絞る
--      （execution_id が NULL の旧Runは従来どおり run_id のみ + record_id_manifest との比較）
--    - 集合比較(compare_record_sets)は安全網として残す

-- 3) 旧実行の行の調査（SELECTのみ。削除はしない）
--    同じ run_id に複数の preprocess execution がある Run:
-- select run_id, count(*) as preprocess_executions
--   from public.geo_report_executions where stage = 'preprocess' group by run_id having count(*) > 1;
--
--    manifest の input_files に無い source_file の行（旧実行の残骸の疑い）:
-- select source_file, count(*) from public.geo_report_records where run_id = '<run_id>' group by source_file;

-- 4) 旧行の削除（要承認・要バックアップ。このファイルでは実行しない）
--    execution_id 追加後は、canonical でない execution の行だけを対象にできる:
-- delete from public.geo_report_records r using public.geo_report_collection_runs c
--  where r.run_id = c.run_id and r.run_id = '<run_id>'
--    and r.execution_id is not null and r.execution_id <> c.canonical_preprocess_execution_id;
