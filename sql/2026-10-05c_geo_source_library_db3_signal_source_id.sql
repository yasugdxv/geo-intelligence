-- =====================================================================
-- Source Library / Monitoring 基盤 — Phase DB-3: External Signals → Source 逆参照（スキーマのみ）
--
-- Article(geo_external_signals) から source_id を逆引きできるようにする。
--   - geo_external_signals.source_id (nullable, FK on delete set null) + index
--   - geo_external_signal_source_matches: 「なぜこのArticleがこのSourceになったか」の監査表
-- Source削除でArticleが消えない設計（set null）。
--
-- このファイルはスキーマ追加のみ。データのbackfillは 2026-10-05d（DB-3b）で別途行う。
-- 前提: DB-1 適用済み。
-- 適用方法: Supabase SQL Editorで手動適用。destructive文なし。
-- =====================================================================

alter table public.geo_external_signals
    add column if not exists source_id uuid
        references public.geo_source_registry(source_id) on delete set null;

create index if not exists geo_external_signals_source_id_idx
    on public.geo_external_signals(source_id)
    where source_id is not null;

create table if not exists public.geo_external_signal_source_matches (
    external_signal_id uuid primary key
        references public.geo_external_signals(external_signal_id) on delete cascade,
    source_id          uuid references public.geo_source_registry(source_id) on delete set null,
    match_method       text not null
        check (match_method in ('exact_domain', 'alias_domain', 'manual', 'crawler')),
    matched_domain     text,
    matched_at         timestamptz not null default now(),
    matched_by         text
);

create index if not exists geo_external_signal_source_matches_source_idx
    on public.geo_external_signal_source_matches(source_id);
alter table public.geo_external_signal_source_matches enable row level security;

comment on column public.geo_external_signals.source_id is
    'Article→Source逆参照。domainが一意に1Sourceへ確定できた場合のみ設定（曖昧/不一致はNULL）。';
comment on table public.geo_external_signal_source_matches is
    'Article→Source紐付けの根拠（match_method/matched_domain）。後から理由を確認するための監査表。';

-- Rollback:
--   drop table if exists public.geo_external_signal_source_matches;
--   drop index if exists public.geo_external_signals_source_id_idx;
--   alter table public.geo_external_signals drop column if exists source_id;
