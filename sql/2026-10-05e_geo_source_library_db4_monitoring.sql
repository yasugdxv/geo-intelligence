-- =====================================================================
-- Source Library / Monitoring 基盤 — Phase DB-4: Monitoring Tables
--
-- ユーザー向けMonitoring（/geo/monitoring-themes）用。既存の開発者向け /geo/monitoring
-- （geo_themes + indicator）は無改修。
--
-- 前提: DB-2（geo_source_sets）適用済み。geo_theme_candidates / geo_themes は既存。
-- 適用方法: Supabase SQL Editorで手動適用。destructive文なし。
-- =====================================================================

create table if not exists public.geo_monitoring_themes (
    monitoring_theme_id  uuid primary key default gen_random_uuid(),
    display_name         text not null,
    -- Theme Explorer由来（候補）／旧geo_themes由来のどちらも任意で参照。
    theme_candidate_id   uuid references public.geo_theme_candidates(candidate_id) on delete set null,
    theme_id             uuid references public.geo_themes(theme_id) on delete set null,
    source_set_id        uuid references public.geo_source_sets(source_set_id) on delete set null,
    regions              jsonb not null default '[]'::jsonb,
    actors               jsonb not null default '[]'::jsonb,
    tags                 jsonb not null default '[]'::jsonb,
    keywords             jsonb not null default '[]'::jsonb,
    monitoring_criteria  text,
    sensitivity          text not null default 'medium'
        check (sensitivity in ('low', 'medium', 'high')),
    frequency            text not null default 'daily'
        check (frequency in ('hourly', 'daily', 'weekly')),
    status               text not null default 'active'
        check (status in ('active', 'paused', 'archived')),
    -- 最新の評価結果キャッシュ（current_state / latest_change / freshness等）。
    latest_assessment    jsonb,
    created_by           text,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now(),
    last_checked_at      timestamptz
);

create index if not exists geo_monitoring_themes_status_idx
    on public.geo_monitoring_themes(status);
create index if not exists geo_monitoring_themes_candidate_idx
    on public.geo_monitoring_themes(theme_candidate_id);

drop trigger if exists geo_monitoring_themes_set_updated_at on public.geo_monitoring_themes;
create trigger geo_monitoring_themes_set_updated_at
    before update on public.geo_monitoring_themes
    for each row execute function public.set_updated_at();
alter table public.geo_monitoring_themes enable row level security;

create table if not exists public.geo_monitoring_alerts (
    alert_id            uuid primary key default gen_random_uuid(),
    monitoring_theme_id uuid not null
        references public.geo_monitoring_themes(monitoring_theme_id) on delete cascade,
    severity            text not null default 'medium'
        check (severity in ('low', 'medium', 'high')),
    alert_type          text not null,
    title               text not null,
    -- 「なぜAlertになったか」の説明（必須）。
    reason              text not null,
    -- 同一変化の重複Alert防止用（monitoring_theme_idとの組でunique）。
    fingerprint         text,
    related_signal_ids  jsonb not null default '[]'::jsonb,
    detected_at         timestamptz not null default now(),
    acknowledged_at     timestamptz,
    acknowledged_by     text
);

create unique index if not exists geo_monitoring_alerts_fingerprint_idx
    on public.geo_monitoring_alerts(monitoring_theme_id, fingerprint)
    where fingerprint is not null;
create index if not exists geo_monitoring_alerts_unack_idx
    on public.geo_monitoring_alerts(monitoring_theme_id, detected_at desc)
    where acknowledged_at is null;
alter table public.geo_monitoring_alerts enable row level security;

comment on table public.geo_monitoring_themes is
    'ユーザー向けMonitoring対象。Theme+Source Set+Actors+Keywords+感度+頻度。';
comment on table public.geo_monitoring_alerts is
    '重要な変化のAlert。reasonに「なぜAlertか」を必ず保持する（単なるキーワードHitにしない）。';

-- Rollback:
--   drop table if exists public.geo_monitoring_alerts;
--   drop table if exists public.geo_monitoring_themes;
