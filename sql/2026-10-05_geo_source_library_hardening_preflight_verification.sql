-- =====================================================================
-- Final Hardening（DB-6/7/8）— Preflight と Verification（どちらも読み取り専用）
-- =====================================================================

-- ===== PREFLIGHT（適用前に実行して結果を控える） =====

-- [H1] 件数スナップショット（DB-7の前後で3つとも同じになること）
select (select count(*) from public.geo_source_registry)                            as source_total,
       (select count(*) from public.geo_source_registry where theme_id is not null) as theme_id_kept,
       (select count(*) from public.geo_source_theme_links)                         as source_theme_links;

-- [H2] 現在のtheme_id FK（delete rule。変更前は CASCADE のはず）
select conname,
       case confdeltype when 'c' then 'CASCADE' when 'n' then 'SET NULL' when 'a' then 'NO ACTION'
                        when 'r' then 'RESTRICT' else confdeltype::text end as on_delete
  from pg_constraint
 where conrelid = 'public.geo_source_registry'::regclass and contype = 'f'
   and confrelid = 'public.geo_themes'::regclass;

-- [H3] 追加予定の列が既に存在しないか（0行が期待値）
select table_name, column_name from information_schema.columns
 where table_schema = 'public'
   and ((table_name = 'geo_source_sets' and column_name = 'archived_at')
     or (table_name = 'geo_monitoring_alerts' and column_name in ('candidate_rule', 'validation_result', 'validation_reason')));

-- [H4] geo_source_setsにupdated_atがあること（1行が期待値）
select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'geo_source_sets' and column_name = 'updated_at';

-- [H5] 現在のSource Set（アーカイブ対象の把握）
select source_set_id, name, created_at from public.geo_source_sets order by created_at;

-- ===== VERIFICATION（適用後に実行） =====

-- [V-H1] DB-6: archived_at列（1行）
select column_name, data_type, is_nullable from information_schema.columns
 where table_schema = 'public' and table_name = 'geo_source_sets' and column_name = 'archived_at';

-- [V-H2] DB-7: FKがSET NULLになっていること、列が残っていること
select conname,
       case confdeltype when 'c' then 'CASCADE' when 'n' then 'SET NULL' else confdeltype::text end as on_delete
  from pg_constraint
 where conrelid = 'public.geo_source_registry'::regclass and contype = 'f'
   and confrelid = 'public.geo_themes'::regclass;
select column_name, is_nullable from information_schema.columns
 where table_schema = 'public' and table_name = 'geo_source_registry' and column_name = 'theme_id';

-- [V-H3] DB-7: 件数がH1と同じこと（[H1]と同じSELECT）
select (select count(*) from public.geo_source_registry)                            as source_total,
       (select count(*) from public.geo_source_registry where theme_id is not null) as theme_id_kept,
       (select count(*) from public.geo_source_theme_links)                         as source_theme_links;

-- [V-H4] DB-8: 監査列3つ（3行）
select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'geo_monitoring_alerts'
   and column_name in ('candidate_rule', 'validation_result', 'validation_reason');
