-- =====================================================================
-- Source Library / Monitoring 基盤 — Phase DB-2: Source Set
--
-- Source Setは特定Theme専用にしない共通オブジェクト。Theme Explorer / Monitoring /
-- Research Studio から再利用する。Themeとの接続はMonitoring等のContext側で行う。
--
-- 前提: DB-1 適用済み（geo_source_registry）。
-- 適用方法: Supabase SQL Editorで手動適用。destructive文なし。
-- =====================================================================

create table if not exists public.geo_source_sets (
    source_set_id uuid primary key default gen_random_uuid(),
    name          text not null,
    description   text,
    created_by    text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

drop trigger if exists geo_source_sets_set_updated_at on public.geo_source_sets;
create trigger geo_source_sets_set_updated_at
    before update on public.geo_source_sets
    for each row execute function public.set_updated_at();
alter table public.geo_source_sets enable row level security;

create table if not exists public.geo_source_set_members (
    source_set_id uuid not null references public.geo_source_sets(source_set_id) on delete cascade,
    source_id     uuid not null references public.geo_source_registry(source_id) on delete cascade,
    sort_order    integer not null default 0,
    added_by      text,
    added_at      timestamptz not null default now(),
    primary key (source_set_id, source_id)
);

create index if not exists geo_source_set_members_source_idx
    on public.geo_source_set_members(source_id);
alter table public.geo_source_set_members enable row level security;

comment on table public.geo_source_sets is
    'Sourceの束。Theme専用ではなくTheme Explorer/Monitoring/Research Studioで共有する。';

-- Rollback:
--   drop table if exists public.geo_source_set_members;
--   drop table if exists public.geo_source_sets;
