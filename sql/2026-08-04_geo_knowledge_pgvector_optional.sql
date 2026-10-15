-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB: pgvector（任意、Phase 2以降で使用開始）
-- 作成日: 2026-08-04
-- 前提: sql/2026-08-04_geo_knowledge_core.sql が実行済みであること
--
-- Phase 1ではPython側からこの列を一切参照しない（検索は
-- geo_knowledge_search_fts.sqlのFull Text Searchのみで完結させる）。
-- Supabaseプロジェクトでvector拡張が許可されていない場合は、本ファイルの
-- 適用をスキップしてよい（Core Migrationの動作に影響しない）。
-- =====================================================================

create extension if not exists vector;

alter table public.geo_knowledge_items
    add column if not exists embedding vector(1536);

comment on column public.geo_knowledge_items.embedding is
    'Phase 2でEmbedding類似検索に使用する予定の列。Phase 1では未使用。'
    '将来のEmbeddingメタデータ（embedding_model, embedding_dimensions, '
    'embedding_version, embedded_at）はPhase 2で別途カラム追加する'
    '（docs/geopolitical-data-model.md 6章）';

-- ロールバック（Phase 1では実行しない）:
-- alter table public.geo_knowledge_items drop column if exists embedding;
-- drop extension if exists vector;
