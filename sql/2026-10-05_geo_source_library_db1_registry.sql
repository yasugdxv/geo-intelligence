-- =====================================================================
-- Source Library / Monitoring 基盤 — Phase DB-1: Source Registry拡張
--
-- 目的: geo_source_registry を「ある1テーマ専用のSource」から
--       「組織共通のSource Master」へ拡張する（additive / backward-compatible）。
--
-- 内容:
--   1. geo_source_registry へ列追加（region / media_type / is_primary / description /
--      reliability / registered_by / archived_at）。既存列は削除・改名しない。
--   2. theme_id の NOT NULL 解除（列自体はlegacy relationとして残す）。
--   3. geo_normalize_domain() 関数（domain正規化: lowercase/scheme除去/www除去/hostname抽出）。
--   4. geo_source_tags            : Tag（AI提案とHuman確定を区別）
--   5. geo_source_theme_links     : Source ↔ Theme 多対多（Tagとは別物）
--   6. geo_source_domains         : Source Domain Alias
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用（マイグレーションランナーは無い）。
-- 前提: 先に 2026-10-05_geo_source_library_preflight.sql を実行して結果を確認すること。
-- destructive文（drop table / drop column / delete）は含まない。
--
-- Rollback: ファイル末尾のコメント参照。
-- =====================================================================

-- ---- 1. geo_source_registry: 追加列（既存と重複させない: if not exists） ----
-- source_tier（一次〜四次）/ source_type は既存。is_primary は一次情報かどうかの
-- 明示フラグ、media_type はより細かい媒体種別（government/think_tank等）。
alter table public.geo_source_registry
    add column if not exists region        text,
    add column if not exists media_type    text,
    add column if not exists is_primary    boolean not null default false,
    add column if not exists description   text,
    add column if not exists reliability   text,
    add column if not exists registered_by text,
    add column if not exists archived_at   timestamptz;

-- reliability は high/medium/low のみ（nullable）。
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'geo_source_registry_reliability_check'
    ) then
        alter table public.geo_source_registry
            add constraint geo_source_registry_reliability_check
            check (reliability is null or reliability in ('high', 'medium', 'low'));
    end if;
end $$;

-- 既存の「一次」ソースは is_primary=true として初期化（既存データの意味を保つだけ）。
update public.geo_source_registry
   set is_primary = true
 where source_tier = '一次' and is_primary = false;

-- created_at / updated_at は既存（NOT NULL default now()）。updated_at trigger も既存。

create index if not exists geo_source_registry_archived_idx
    on public.geo_source_registry(archived_at);
create index if not exists geo_source_registry_media_type_idx
    on public.geo_source_registry(media_type);

comment on column public.geo_source_registry.archived_at is
    'soft delete。NULL以外はアーカイブ済み（一覧から除外、Article/Monitoringの参照は維持）。';
comment on column public.geo_source_registry.registered_by is
    '登録した社内担当者（source_ownerは発行元組織名であり別物）。';

-- ---- 2. theme_id NOT NULL 解除（列・FKは削除しない: legacy relation） ----
-- 注意: FK は on delete cascade のまま（既存挙動を変えない）。Source Masterとして使う
-- 新規Sourceは theme_id=NULL で登録されるため、Theme削除でcascade削除される対象は
-- 従来どおり「theme_idが入っているlegacy Source」のみ。
alter table public.geo_source_registry
    alter column theme_id drop not null;

comment on column public.geo_source_registry.theme_id is
    'LEGACY: 旧「テーマ専用Source」の関連。新規はgeo_source_theme_linksを使う（本列は互換のため残置）。';

-- ---- 3. domain正規化関数 ----
-- lowercase → scheme除去 → userinfo除去 → hostname抽出(port/path/query除去)
-- → 先頭 www. 除去 → 末尾 . 除去。空/NULLはNULLを返す。
create or replace function public.geo_normalize_domain(raw text)
returns text
language sql
immutable
as $$
    select nullif(
        regexp_replace(
            regexp_replace(
                substring(
                    regexp_replace(
                        regexp_replace(lower(btrim(coalesce(raw, ''))), '^[a-z][a-z0-9+.-]*://', ''),
                        '^[^/?#@]*@', ''
                    )
                    from '^([^/:?#]+)'
                ),
                '^www\.', ''
            ),
            '\.$', ''
        ),
        ''
    );
$$;

comment on function public.geo_normalize_domain(text) is
    'URL/ドメイン文字列をhostnameへ正規化（lowercase, scheme/userinfo/port/path除去, www除去）。';

-- ---- 4. geo_source_tags（AI提案とHuman確定を区別） ----
create table if not exists public.geo_source_tags (
    tag_id       uuid primary key default gen_random_uuid(),
    source_id    uuid not null references public.geo_source_registry(source_id) on delete cascade,
    tag_type     text not null check (tag_type in ('theme', 'region', 'actor', 'other')),
    tag_value    text not null,
    origin       text not null default 'human' check (origin in ('ai', 'human', 'system')),
    is_confirmed boolean not null default false,
    created_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create unique index if not exists geo_source_tags_unique_idx
    on public.geo_source_tags(source_id, tag_type, lower(tag_value));
create index if not exists geo_source_tags_value_idx
    on public.geo_source_tags(tag_type, lower(tag_value));

drop trigger if exists geo_source_tags_set_updated_at on public.geo_source_tags;
create trigger geo_source_tags_set_updated_at
    before update on public.geo_source_tags
    for each row execute function public.set_updated_at();
alter table public.geo_source_tags enable row level security;

comment on table public.geo_source_tags is
    'Sourceの分類Tag（Semiconductor/China/AI等）。origin=aiは提案、is_confirmed=trueで人間確定。';

-- ---- 5. geo_source_theme_links（Source ↔ Theme 多対多） ----
-- Tag（分類）とは別物: 実際のプラットフォーム上のThemeとの接続を表す。
create table if not exists public.geo_source_theme_links (
    link_id         uuid primary key default gen_random_uuid(),
    source_id       uuid not null references public.geo_source_registry(source_id) on delete cascade,
    theme_id        uuid not null references public.geo_themes(theme_id) on delete cascade,
    relation_type   text not null default 'manual'
        check (relation_type in ('manual', 'ai_recommended', 'usage_derived')),
    relevance_score numeric(5, 4) check (relevance_score is null or (relevance_score >= 0 and relevance_score <= 1)),
    created_at      timestamptz not null default now(),
    created_by      text
);

create unique index if not exists geo_source_theme_links_unique_idx
    on public.geo_source_theme_links(source_id, theme_id, relation_type);
create index if not exists geo_source_theme_links_theme_idx
    on public.geo_source_theme_links(theme_id);
alter table public.geo_source_theme_links enable row level security;

comment on table public.geo_source_theme_links is
    'Source↔Themeの多対多。relation_type: manual(人が設定)/ai_recommended/usage_derived(利用実績由来)。';

-- legacy theme_id を多対多へ複製（既存関連を新構造からも辿れるようにする。冪等）。
insert into public.geo_source_theme_links (source_id, theme_id, relation_type, created_by)
select source_id, theme_id, 'manual', 'migration:legacy_theme_id'
  from public.geo_source_registry
 where theme_id is not null
on conflict do nothing;

-- ---- 6. geo_source_domains（Source Domain Alias） ----
-- Registry URL だけに domain matching を依存させないための別表。
-- 同一domainが複数Sourceに存在し得る（=ambiguous）ため domain 単独のuniqueは付けない。
create table if not exists public.geo_source_domains (
    domain_id     uuid primary key default gen_random_uuid(),
    source_id     uuid not null references public.geo_source_registry(source_id) on delete cascade,
    domain        text not null,
    is_primary    boolean not null default false,
    match_enabled boolean not null default true,
    created_at    timestamptz not null default now()
);

create unique index if not exists geo_source_domains_unique_idx
    on public.geo_source_domains(source_id, domain);
create index if not exists geo_source_domains_domain_idx
    on public.geo_source_domains(domain);
alter table public.geo_source_domains enable row level security;

comment on table public.geo_source_domains is
    'Sourceが使うdomain（本体/subdomain/別ブランド等）。Article→Source照合に使う。正規化済みhostnameを格納。';

-- Registry URL から primary domain を初期投入（正規化済み、冪等）。
insert into public.geo_source_domains (source_id, domain, is_primary, match_enabled)
select source_id, public.geo_normalize_domain(url), true, true
  from public.geo_source_registry
 where url is not null
   and public.geo_normalize_domain(url) is not null
on conflict do nothing;

-- =====================================================================
-- Rollback（必要時のみ。データ消失を伴うため実行前に確認）:
--   drop table if exists public.geo_source_domains;
--   drop table if exists public.geo_source_theme_links;
--   drop table if exists public.geo_source_tags;
--   drop function if exists public.geo_normalize_domain(text);
--   alter table public.geo_source_registry
--       drop column if exists region, drop column if exists media_type,
--       drop column if exists is_primary, drop column if exists description,
--       drop column if exists reliability, drop column if exists registered_by,
--       drop column if exists archived_at;
--   -- theme_id の NOT NULL 復元は theme_id NULL の行が無いことを確認してから:
--   -- alter table public.geo_source_registry alter column theme_id set not null;
-- =====================================================================
