-- =====================================================================
-- Geo共通インフラ: updated_at自動更新トリガー関数
--
-- 背景: このrepoは元々Sustainability側(world_monitor_dashboard)のフォークで、
-- set_updated_at()はSustainability側のmigration(旧sql/2026-07-15_crawl_and_tag_
-- schema_v2.sql、DB分離に伴い本repoからは削除済み)で定義されたものをGeo側の
-- 複数テーブル(geo_themes/geo_source_registry/geo_knowledge_items等)のtriggerが
-- そのまま流用していた。Geo専用DBを新規構築する際にこの関数が存在しないと
-- 後続migration(2026-08-02_geo_core_schema.sql以降)が失敗するため、Geo側の
-- 独立したmigrationとして本ファイルへ切り出す(定義内容は変更しない)。
-- =====================================================================

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;
