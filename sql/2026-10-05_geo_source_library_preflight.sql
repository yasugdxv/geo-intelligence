-- =====================================================================
-- Source Library / Monitoring 基盤 — Preflight（読み取り専用 / 適用前に実行）
--
-- SELECTのみ。何も書き込まない。Supabase SQL Editorで「ブロックごと」に実行し、
-- 結果を確認してからDB-1以降を適用する。
-- ※ geo_normalize_domain() はDB-1で作成されるため、ここではCTE内で同じ正規化を
--    インライン実装している（lowercase / scheme除去 / userinfo除去 / hostname抽出 / www除去）。
-- =====================================================================

-- [P1] Source Registry 件数・theme_id状態
select count(*)                                    as source_total,
       count(*) filter (where theme_id is null)    as theme_id_null,
       count(*) filter (where theme_id is not null) as theme_id_not_null,
       count(*) filter (where url is null)         as url_null
  from public.geo_source_registry;

-- [P2] theme_id 列の NOT NULL 状態と FK（delete rule: cascade か確認）
select a.attname as column_name, a.attnotnull as is_not_null
  from pg_attribute a
 where a.attrelid = 'public.geo_source_registry'::regclass and a.attname = 'theme_id';

select conname, confrelid::regclass as references_table,
       case confdeltype when 'c' then 'CASCADE' when 'n' then 'SET NULL'
                        when 'a' then 'NO ACTION' when 'r' then 'RESTRICT' else confdeltype::text end as on_delete
  from pg_constraint
 where conrelid = 'public.geo_source_registry'::regclass and contype = 'f';

-- [P3] Source削除に関わるFK（geo_source_registryを参照する全テーブルとdelete rule）
select conrelid::regclass as referencing_table, conname,
       case confdeltype when 'c' then 'CASCADE' when 'n' then 'SET NULL'
                        when 'a' then 'NO ACTION' when 'r' then 'RESTRICT' else confdeltype::text end as on_delete
  from pg_constraint
 where confrelid = 'public.geo_source_registry'::regclass and contype = 'f'
 order by 1;

-- [P4] geo_external_signals 件数・期間・source_domain状態
select count(*) as signals_total,
       count(*) filter (where source_domain is null or source_domain = '') as source_domain_empty,
       min(created_at) as oldest_created_at, max(created_at) as latest_created_at,
       max(published_at) as latest_published_at
  from public.geo_external_signals;

-- [P5] domain別件数 上位50
select source_domain, count(*) as n
  from public.geo_external_signals
 group by 1 order by n desc limit 50;

-- [P6] Source Registry の URL / 正規化domain 一覧
select source_id, name, url, archived_flag_exists.col as note,
       lower(regexp_replace(regexp_replace(substring(
           regexp_replace(regexp_replace(lower(btrim(coalesce(url,''))), '^[a-z][a-z0-9+.-]*://', ''), '^[^/?#@]*@', '')
           from '^([^/:?#]+)'), '^www\.', ''), '\.$', '')) as normalized_domain
  from public.geo_source_registry
  cross join (select 'preflight'::text as col) archived_flag_exists
 order by normalized_domain nulls last, name;

-- [P7] ★ Registry側 domain の重複（同一domainを持つSourceが複数 = ambiguous候補）
with reg as (
    select source_id, name,
           nullif(regexp_replace(regexp_replace(substring(
               regexp_replace(regexp_replace(lower(btrim(coalesce(url,''))), '^[a-z][a-z0-9+.-]*://', ''), '^[^/?#@]*@', '')
               from '^([^/:?#]+)'), '^www\.', ''), '\.$', ''), '') as dom
      from public.geo_source_registry
)
select dom, count(distinct source_id) as sources, array_agg(name order by name) as source_names
  from reg where dom is not null
 group by dom having count(distinct source_id) > 1
 order by sources desc;

-- [P8] ★ domain backfill 見込み: exact unique / ambiguous / unmatched の件数（signal件数ベース）
with reg as (
    select source_id,
           nullif(regexp_replace(regexp_replace(substring(
               regexp_replace(regexp_replace(lower(btrim(coalesce(url,''))), '^[a-z][a-z0-9+.-]*://', ''), '^[^/?#@]*@', '')
               from '^([^/:?#]+)'), '^www\.', ''), '\.$', ''), '') as dom
      from public.geo_source_registry
),
reg_count as (
    select dom, count(distinct source_id) as n_sources from reg where dom is not null group by dom
),
sig as (
    select external_signal_id,
           nullif(regexp_replace(regexp_replace(substring(
               regexp_replace(regexp_replace(lower(btrim(coalesce(nullif(source_domain,''), url, ''))), '^[a-z][a-z0-9+.-]*://', ''), '^[^/?#@]*@', '')
               from '^([^/:?#]+)'), '^www\.', ''), '\.$', ''), '') as dom
      from public.geo_external_signals
)
select case when sig.dom is null then 'no_domain'
            when rc.n_sources is null then 'unmatched'
            when rc.n_sources = 1 then 'exact_unique'
            else 'ambiguous' end as match_class,
       count(*) as signals
  from sig left join reg_count rc on rc.dom = sig.dom
 group by 1 order by 2 desc;

-- [P9] ★ 一意一致するdomain数（Source側）
with reg as (
    select source_id,
           nullif(regexp_replace(regexp_replace(substring(
               regexp_replace(regexp_replace(lower(btrim(coalesce(url,''))), '^[a-z][a-z0-9+.-]*://', ''), '^[^/?#@]*@', '')
               from '^([^/:?#]+)'), '^www\.', ''), '\.$', ''), '') as dom
      from public.geo_source_registry
)
select count(distinct dom) filter (where dom is not null) as distinct_domains,
       count(*) filter (where dom is null)                 as sources_without_domain
  from reg;

-- [P10] 追加予定のテーブル/列が既に存在しないか（全て 0 行が期待値）
select table_name from information_schema.tables
 where table_schema = 'public'
   and table_name in ('geo_source_tags','geo_source_theme_links','geo_source_domains',
                      'geo_source_sets','geo_source_set_members',
                      'geo_external_signal_source_matches',
                      'geo_monitoring_themes','geo_monitoring_alerts');

select table_name, column_name from information_schema.columns
 where table_schema = 'public'
   and ((table_name = 'geo_source_registry' and column_name in
          ('region','media_type','is_primary','description','reliability','registered_by','archived_at'))
     or (table_name = 'geo_external_signals' and column_name = 'source_id'));

select proname from pg_proc where proname = 'geo_normalize_domain';

-- [P11] 依存確認: set_updated_at 関数 / geo_theme_candidates / geo_themes の存在（各1行が期待値）
select proname from pg_proc where proname = 'set_updated_at';
select table_name from information_schema.tables
 where table_schema = 'public' and table_name in ('geo_theme_candidates','geo_themes');
