-- =====================================================================
-- Source Intelligence Final Hardening — DB-6: Source Setの論理削除
--
-- geo_source_setsへ archived_at（nullable）を追加する（additive）。updated_atは既存
-- （DB-2で作成済み、set_updated_at triggerあり）。
-- Source Setのarchiveは「通常一覧から外す」だけで、Source自体・member行・Monitoringの
-- 参照（source_set_id）は変更しない（物理削除しない）。
--
-- 前提: DB-2 適用済み。適用方法: Supabase SQL Editorで手動適用。destructive文なし。
-- =====================================================================

alter table public.geo_source_sets
    add column if not exists archived_at timestamptz;

create index if not exists geo_source_sets_active_idx
    on public.geo_source_sets(updated_at desc)
    where archived_at is null;

comment on column public.geo_source_sets.archived_at is
    'soft delete。NULL以外はアーカイブ済み（通常一覧から除外。member・Monitoring参照は維持）。';

-- Rollback:
--   drop index if exists public.geo_source_sets_active_idx;
--   alter table public.geo_source_sets drop column if exists archived_at;
