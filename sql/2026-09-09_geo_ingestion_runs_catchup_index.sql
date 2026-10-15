-- =====================================================================
-- GDELT External Intelligence Layer Operationalization:
-- Historical Initial Load + Incremental Ingestion
-- 作成日: 2026-09-09
-- 前提: sql/2026-08-05_geo_phase2a_ingestion.sql が実行済みであること
--
-- 目的: 大量のbatch_timestamp/query_idを持つcatch-up系connector
-- （gdelt_stream, gdelt_doc_catchup, gdelt_stream_catchup）のmissing/completed
-- batch判定・watermark再計算クエリ（connector_type + status絞り込み）を高速化する。
-- 既存動作には影響しない、純粋な読み取り性能の最適化（非destructive、rollback容易）。
-- =====================================================================

create index if not exists geo_ingestion_runs_connector_type_status_idx
    on public.geo_ingestion_runs(connector_type, status, started_at desc);

comment on index public.geo_ingestion_runs_connector_type_status_idx is
    'GDELT Operationalization: connector_type単位のcatch-up判定（list_missing_batch_timestamps系、'
    'compute_completed_through系）が頻繁にconnector_type+statusで絞り込むためのindex。'
    'Historical Initial Loadでbatch/query run数が数千〜数万に増えても劣化しないようにする';

-- =====================================================================
-- ロールバック手順（実行しない。記録のみ）
--
-- drop index if exists public.geo_ingestion_runs_connector_type_status_idx;
-- =====================================================================
