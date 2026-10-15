-- =====================================================================
-- Source Intelligence Final Hardening — DB-7: geo_source_registry.theme_id の FK を
--   ON DELETE CASCADE → ON DELETE SET NULL へ変更
--
-- 背景: geo_source_registryは「Theme配下のSource」ではなく「組織共通Source Master」。
-- Themeを削除するとそのtheme_idを持つSourceが連鎖削除される現状のCASCADEは危険。
-- 変更後: Theme削除時はSourceを残し、legacyのtheme_idだけがNULLになる。
-- Source↔Themeの正式な関係は geo_source_theme_links（Theme削除時はリンク行のみ削除）。
--
-- theme_id列は削除しない（legacy compatibility）。データは変更しない（FK定義のみ）。
-- 前提: DB-1 適用済み（theme_idがNULL可）。
-- 適用手順:
--   1) 下の「[SNAPSHOT]」SELECTを適用前に実行して結果を控える
--   2) このファイルを実行（DOブロック1つ=1トランザクションで原子的。失敗時は元のFKのまま）
--   3) 再度「[SNAPSHOT]」を実行し、3つの件数が適用前と同じであることを確認
-- =====================================================================

-- [SNAPSHOT] 適用前後で同じになるべき件数（読み取りのみ。適用前と適用後の両方で実行）
-- select (select count(*) from public.geo_source_registry)                          as source_total,
--        (select count(*) from public.geo_source_registry where theme_id is not null) as theme_id_kept,
--        (select count(*) from public.geo_source_theme_links)                       as source_theme_links;

do $$
declare
    fk_name text;
begin
    -- geo_source_registry.theme_id → geo_themes の既存FK名を動的に取得（命名差異に備える）
    select c.conname into fk_name
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any (c.conkey)
     where c.conrelid = 'public.geo_source_registry'::regclass
       and c.contype = 'f'
       and c.confrelid = 'public.geo_themes'::regclass
       and a.attname = 'theme_id'
     limit 1;

    if fk_name is null then
        raise exception 'geo_source_registry.theme_id のFKが見つかりません（変更を中止）';
    end if;

    execute format('alter table public.geo_source_registry drop constraint %I', fk_name);
    alter table public.geo_source_registry
        add constraint geo_source_registry_theme_id_fkey
        foreign key (theme_id) references public.geo_themes(theme_id) on delete set null;
end $$;

comment on column public.geo_source_registry.theme_id is
    'LEGACY: 旧「テーマ専用Source」の関連。Theme削除時はNULLになる（Source自体は残る）。正式な関係はgeo_source_theme_links。';

-- Rollback（元のCASCADEへ戻す。theme_idにNULLの行があっても戻せる）:
--   alter table public.geo_source_registry drop constraint geo_source_registry_theme_id_fkey;
--   alter table public.geo_source_registry
--       add constraint geo_source_registry_theme_id_fkey
--       foreign key (theme_id) references public.geo_themes(theme_id) on delete cascade;
