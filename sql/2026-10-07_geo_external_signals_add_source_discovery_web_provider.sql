-- =====================================================================
-- Theme Discovery「本当に探索する」拡張（ユーザー指示、2026-10-07）
-- geo_external_signals.provider の許可値に 'source_discovery_web' を追加する。
--
-- 背景: テーマ探索のチャット要件すり合わせ→情報ソース選定の後、選んだSourceの
-- domainに限定したWeb検索（Responses APIのweb_search_preview、既存の
-- geo_source_research_service.run_source_researchと同じdomain限定パターン）で
-- DB未収録の最新記事を見つけ、既存の正規化・dedup・upsert経路
-- （geo_external_signal_service.normalize_url/compute_dedup_key/bulk_upsert_signals）で
-- geo_external_signalsへ取り込む（geo_theme_discovery_live_service.py）。
-- 既存providerのいずれにも該当しないため、既存のprovider CHECK制約
-- （gdelt/media_cloud/acled/rss/official/babelstreet）に新しい値を1つ追加するだけの
-- 変更で、既存データ・既存値への影響はない（追加のみ、drop→再add）。
--
-- 影響範囲: public.geo_external_signals.provider CHECK制約のみ。他テーブル
-- （geo_external_signal_queries/geo_external_signal_metrics）は対象外
-- （本機能はこれらのテーブルへ書き込まない）。
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select conname, pg_get_constraintdef(oid) from pg_constraint
--     where conrelid = 'public.geo_external_signals'::regclass
--       and conname = 'geo_external_signals_provider_check';
--   -- 期待値: CHECK (provider = ANY (ARRAY['gdelt','media_cloud','acled','rss','official','babelstreet']))
-- =====================================================================

alter table public.geo_external_signals
    drop constraint geo_external_signals_provider_check;

alter table public.geo_external_signals
    add constraint geo_external_signals_provider_check
    check (provider in ('gdelt', 'media_cloud', 'acled', 'rss', 'official', 'babelstreet', 'source_discovery_web'));

comment on constraint geo_external_signals_provider_check on public.geo_external_signals is
    'source_discovery_web: テーマ探索（Theme Discovery Live）のチャット要件すり合わせ後、'
    '選択Sourceのdomainに限定したWeb検索で見つけた記事（2026-10-07追加、geo_theme_discovery_live_service.py）';

-- 適用後確認（記録すること）:
--   select conname, pg_get_constraintdef(oid) from pg_constraint
--     where conrelid = 'public.geo_external_signals'::regclass
--       and conname = 'geo_external_signals_provider_check';
--   -- 期待値: 'source_discovery_web' が配列に追加されていること
