-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 1: Full Text Search
-- 作成日: 2026-08-04
-- 前提: sql/2026-08-04_geo_knowledge_core.sql が実行済みであること
--
-- Phase 1の検索はpgvectorを使わず、SQL filter + PostgreSQL Full Text Search
-- + entity/relationship/issue/status/temporal filterのみで完結させる
-- （docs/geopolitical-knowledge-architecture.md 12章）。日本語の形態素解析辞書が
-- 無い前提のため 'simple' configurationを使う（Phase2で pg_bigm 等の導入を検討）。
-- =====================================================================

alter table public.geo_knowledge_items
    add column if not exists search_vector tsvector
    generated always as (
        to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(summary, ''))
    ) stored;

create index if not exists geo_knowledge_items_search_idx
    on public.geo_knowledge_items using gin(search_vector);

alter table public.geo_claims
    add column if not exists search_vector tsvector
    generated always as (
        to_tsvector('simple', coalesce(claim_text, '') || ' ' || coalesce(normalized_claim, ''))
    ) stored;

create index if not exists geo_claims_search_idx
    on public.geo_claims using gin(search_vector);

alter table public.geo_entities
    add column if not exists search_vector tsvector
    generated always as (
        to_tsvector('simple',
            coalesce(canonical_name, '') || ' ' || coalesce(local_name, '') || ' ' ||
            coalesce(name_en, '') || ' ' || coalesce(name_ja, ''))
    ) stored;

create index if not exists geo_entities_search_idx
    on public.geo_entities using gin(search_vector);

-- ロールバック（Phase 1では実行しない）:
-- drop index if exists geo_entities_search_idx;
-- alter table public.geo_entities drop column if exists search_vector;
-- drop index if exists geo_claims_search_idx;
-- alter table public.geo_claims drop column if exists search_vector;
-- drop index if exists geo_knowledge_items_search_idx;
-- alter table public.geo_knowledge_items drop column if exists search_vector;
