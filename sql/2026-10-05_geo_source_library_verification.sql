-- =====================================================================
-- Source Library / Monitoring 基盤 — 適用後 Verification（読み取り専用）
-- 各Phase適用後に該当ブロックを実行して確認する。
-- =====================================================================

-- [V-DB1] 追加列 7つ + theme_id が nullable（is_not_null = false）
select column_name, is_nullable from information_schema.columns
 where table_schema='public' and table_name='geo_source_registry'
   and column_name in ('region','media_type','is_primary','description','reliability',
                       'registered_by','archived_at','theme_id')
 order by column_name;

-- [V-DB1] 正規化関数の動作確認（期待: example.com / bis.gov / csis.org / NULL）
select public.geo_normalize_domain('https://www.Example.com/path?q=1') as a,
       public.geo_normalize_domain('http://user:pw@bis.gov:8080/x#y')   as b,
       public.geo_normalize_domain('CSIS.org.')                          as c,
       public.geo_normalize_domain('')                                   as d;

-- [V-DB1] 新テーブルの件数（theme_links は legacy theme_id 複製分、domains は URL由来）
select (select count(*) from public.geo_source_tags)        as tags,
       (select count(*) from public.geo_source_theme_links) as theme_links,
       (select count(*) from public.geo_source_domains)     as domains,
       (select count(*) from public.geo_source_registry where theme_id is not null) as legacy_theme_id_rows;

-- [V-DB1] 既存データが壊れていない（Preflight P1 と source_total が一致すること）
select count(*) as source_total from public.geo_source_registry;

-- [V-DB2]
select (select count(*) from public.geo_source_sets) as source_sets,
       (select count(*) from public.geo_source_set_members) as members;

-- [V-DB3] source_id列・index・FK(on delete set null)
select column_name, is_nullable from information_schema.columns
 where table_schema='public' and table_name='geo_external_signals' and column_name='source_id';
select conname, case confdeltype when 'n' then 'SET NULL' else confdeltype::text end as on_delete
  from pg_constraint
 where conrelid='public.geo_external_signals'::regclass and confrelid='public.geo_source_registry'::regclass;

-- [V-DB3b] backfill結果（Preflight P8 の exact_unique 件数と一致すること）
select count(*) filter (where source_id is not null) as linked,
       count(*) filter (where source_id is null)     as unlinked,
       count(*)                                       as total
  from public.geo_external_signals;
select match_method, count(*) from public.geo_external_signal_source_matches group by 1;
-- 監査表と source_id の整合（0 が期待値）
select count(*) as inconsistent
  from public.geo_external_signal_source_matches m
  join public.geo_external_signals s using (external_signal_id)
 where m.source_id is not null and s.source_id is distinct from m.source_id;

-- [V-DB4]
select (select count(*) from public.geo_monitoring_themes) as monitoring_themes,
       (select count(*) from public.geo_monitoring_alerts) as alerts;

-- [V-ALL] RLS有効（全て true が期待値）
select relname, relrowsecurity from pg_class
 where relname in ('geo_source_tags','geo_source_theme_links','geo_source_domains',
                   'geo_source_sets','geo_source_set_members',
                   'geo_external_signal_source_matches',
                   'geo_monitoring_themes','geo_monitoring_alerts');
