-- =====================================================================
-- 地政学インテリジェンス基盤 MVP: Phase 1 縦切り用コアテーブル一式
-- 作成日: 2026-08-02
-- 対象: Supabase（PostgreSQL）。新規構築、ALTER/DROP/移行処理は含まない。
--
-- 前提: 以下が既に作成済みであること
--   public.set_updated_at()（共通トリガー関数、sql/2026-08-01_geo_shared_infra.sql）
--
-- 台湾有事を Theme 設定データの1件として実装する。新規テーブルはすべて
-- geo_ プレフィックスで完全に独立させ、Sustainability側のテーブルには
-- 一切手を加えない（Sustainability側は別Supabaseプロジェクトへ分離済み）。
--
-- Phase 1 縦切り（台湾テーマseed→文書アップロード→Event抽出→Evidence表示→
-- MIL-01のObservation→Executive Overview最小表示→audit log）に必要な範囲に絞り、
-- Document/Event の版管理（geo_document_versions/geo_event_versions）・
-- Scenario/Alert系テーブルは Phase 2 以降で追加する。
--
-- 作成順: geo_themes → geo_source_registry → geo_documents
--        → geo_events → geo_evidence
--        → geo_indicator_definitions → geo_indicator_rule_versions → geo_observations
--        → geo_audit_logs
-- =====================================================================


-- ---- 1. geo_themes（地政学テーマ設定） ----

create table public.geo_themes (
    theme_id    uuid primary key default gen_random_uuid(),
    code        text not null,
    name_ja     text not null,
    name_en     text,
    description text,
    status      text not null default '有効'
        check (status in ('有効', '無効')),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create unique index geo_themes_code_idx on public.geo_themes(code);

create trigger geo_themes_set_updated_at
    before update on public.geo_themes
    for each row execute function public.set_updated_at();

alter table public.geo_themes enable row level security;

comment on table public.geo_themes is
    '地政学インテリジェンス基盤が監視するテーマの設定データ（台湾有事、将来の米中関係・中東・'
    '関税制裁・南シナ海等）。特定テーマ専用ロジックをコードにハードコードせず、本テーブルの1行'
    'として追加できるようにする';


-- ---- 2. geo_source_registry（テーマごとの監視対象ソース） ----

create table public.geo_source_registry (
    source_id   uuid primary key default gen_random_uuid(),
    theme_id    uuid not null references public.geo_themes(theme_id) on delete cascade,
    name        text not null,
    url         text,
    source_tier text
        check (source_tier is null or source_tier in ('一次', '二次', '三次', '四次')),
    source_type text
        check (source_type is null or source_type in (
            'official_site', 'news', 'sns', 'commercial_data', 'other'
        )),
    notes       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index geo_source_registry_theme_idx on public.geo_source_registry(theme_id);

create trigger geo_source_registry_set_updated_at
    before update on public.geo_source_registry
    for each row execute function public.set_updated_at();

alter table public.geo_source_registry enable row level security;

comment on table public.geo_source_registry is
    'テーマごとの監視対象ソース登録（台湾国防部、台湾海巡署等）。Phase1は手動登録のみ、'
    'Phase2でconnector単位の自動取得を追加する';


-- ---- 3. geo_documents（取得・アップロードした文書） ----

create table public.geo_documents (
    document_id    uuid primary key default gen_random_uuid(),
    theme_id       uuid not null references public.geo_themes(theme_id) on delete restrict,
    source_id      uuid references public.geo_source_registry(source_id) on delete set null,
    title          text,
    url            text,
    canonical_url  text,
    published_at   timestamptz,
    fetched_at     timestamptz not null default now(),
    language       text,
    content_hash   text not null,
    mime_type      text,
    storage_path   text,
    extracted_text text,
    fetch_status   text not null default '成功'
        check (fetch_status in ('成功', '失敗')),
    http_status    integer,
    parser_version text,
    is_mock        boolean not null default false,
    created_at     timestamptz not null default now()
);

create unique index geo_documents_content_hash_idx on public.geo_documents(content_hash);
create index geo_documents_theme_idx on public.geo_documents(theme_id);

alter table public.geo_documents enable row level security;

comment on table public.geo_documents is
    '手動URL登録・ファイルアップロード（Phase1）または将来のconnector取得（Phase2）による文書。'
    'content_hashで同一文書の再取得・重複保存を防ぐ';


-- ---- 4. geo_events（Event Candidate） ----

create table public.geo_events (
    event_id       uuid primary key default gen_random_uuid(),
    theme_id       uuid not null references public.geo_themes(theme_id) on delete restrict,
    actor          text,
    action         text,
    target         text,
    location       text,
    start_at       timestamptz,
    end_at         timestamptz,
    quantities     jsonb,
    event_type     text,
    confidence     numeric
        check (confidence is null or (confidence >= 0 and confidence <= 1)),
    unknown_fields jsonb,
    verified       boolean not null default false,
    verified_by    text,
    verified_at    timestamptz,
    created_at     timestamptz not null default now()
);

create index geo_events_theme_idx on public.geo_events(theme_id);
create index geo_events_verified_idx on public.geo_events(verified);

alter table public.geo_events enable row level security;

comment on table public.geo_events is
    'LLM抽出によるEvent Candidate。不明な値は創作せずnullとする。人またはVerification処理を'
    '経るまで verified=false とする';


-- ---- 5. geo_evidence（主張単位の証拠） ----

create table public.geo_evidence (
    evidence_id         uuid primary key default gen_random_uuid(),
    event_id            uuid not null references public.geo_events(event_id) on delete cascade,
    document_id         uuid not null references public.geo_documents(document_id) on delete restrict,
    claim_text          text not null,
    quote               text,
    page_section        text,
    primary_flag        boolean not null default false,
    source_tier         text
        check (source_tier is null or source_tier in ('一次', '二次', '三次', '四次')),
    corroboration_count integer not null default 1,
    confidence          numeric
        check (confidence is null or (confidence >= 0 and confidence <= 1)),
    verification_status text not null default 'unverified'
        check (verification_status in ('unverified', 'verified', 'contradicted')),
    contradictions       jsonb,
    created_at           timestamptz not null default now()
);

create index geo_evidence_event_idx on public.geo_evidence(event_id);
create index geo_evidence_document_idx on public.geo_evidence(document_id);

alter table public.geo_evidence enable row level security;

comment on table public.geo_evidence is
    '主張単位の証拠。重大アラートに使うFact/Official Claim/ObservationはSource Ledgerから'
    'document_id経由で原文へ遡れること';


-- ---- 6. geo_indicator_definitions（指標定義） ----

create table public.geo_indicator_definitions (
    indicator_id   uuid primary key default gen_random_uuid(),
    theme_id       uuid not null references public.geo_themes(theme_id) on delete cascade,
    indicator_code text not null,
    name           text not null,
    description    text,
    unit           text,
    mvp_enabled    boolean not null default false,
    display_order  integer,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create unique index geo_indicator_definitions_code_idx on public.geo_indicator_definitions(indicator_code);
create index geo_indicator_definitions_theme_idx on public.geo_indicator_definitions(theme_id);

create trigger geo_indicator_definitions_set_updated_at
    before update on public.geo_indicator_definitions
    for each row execute function public.set_updated_at();

alter table public.geo_indicator_definitions enable row level security;

comment on table public.geo_indicator_definitions is
    '監視指標の定義。mvp_enabled=trueの指標のみ初期ダッシュボードへ表示する。指標の追加・停止は'
    'コード変更なしに本テーブルの更新のみで行える';


-- ---- 7. geo_indicator_rule_versions（指標計算ルールのversion管理） ----

create table public.geo_indicator_rule_versions (
    rule_version_id uuid primary key default gen_random_uuid(),
    indicator_id    uuid not null references public.geo_indicator_definitions(indicator_id) on delete cascade,
    version         integer not null,
    rule_definition jsonb not null,
    is_draft        boolean not null default true,
    effective_from  timestamptz not null default now(),
    created_at      timestamptz not null default now()
);

create unique index geo_indicator_rule_versions_unique_idx
    on public.geo_indicator_rule_versions(indicator_id, version);

alter table public.geo_indicator_rule_versions enable row level security;

comment on table public.geo_indicator_rule_versions is
    '指標の計算ルール・閾値のversion管理。is_draft=true は「案・校正前」を表し、UIに明示する。'
    '過去のObservationも使用したrule_version_idを保持するため、ルール変更後に再計算できる';


-- ---- 8. geo_observations（指標の実測値） ----

create table public.geo_observations (
    observation_id        uuid primary key default gen_random_uuid(),
    indicator_id          uuid not null references public.geo_indicator_definitions(indicator_id) on delete cascade,
    rule_version_id       uuid not null references public.geo_indicator_rule_versions(rule_version_id) on delete restrict,
    as_of                 timestamptz not null default now(),
    raw_value             numeric,
    insufficient_data     boolean not null default false,
    baseline_7d           numeric,
    baseline_30d          numeric,
    baseline_90d          numeric,
    deviation             numeric,
    persistence           numeric,
    novelty               numeric,
    confidence            numeric
        check (confidence is null or (confidence >= 0 and confidence <= 1)),
    data_quality          text,
    source_freshness_hours numeric,
    evidence_id           uuid references public.geo_evidence(evidence_id) on delete set null,
    is_mock               boolean not null default false,
    created_at            timestamptz not null default now()
);

create index geo_observations_indicator_idx on public.geo_observations(indicator_id, as_of desc);

alter table public.geo_observations enable row level security;

comment on table public.geo_observations is
    '指標の実測値。値が不足する場合は0として扱わず insufficient_data=true を返す。取得障害と'
    '実際の変化を混同しない';


-- ---- 9. geo_audit_logs（監査ログ） ----

create table public.geo_audit_logs (
    audit_id    uuid primary key default gen_random_uuid(),
    actor       text not null,
    action      text not null,
    target_table text,
    target_id   uuid,
    detail      jsonb,
    created_at  timestamptz not null default now()
);

create index geo_audit_logs_target_idx on public.geo_audit_logs(target_table, target_id);
create index geo_audit_logs_created_idx on public.geo_audit_logs(created_at desc);

alter table public.geo_audit_logs enable row level security;

comment on table public.geo_audit_logs is
    '誰がどの資料を参照・操作したかの監査ログ。Level3/4アラートの承認履歴はPhase2以降で'
    '改変不可の形で扱う（Phase1では通常の監査ログとして記録するのみ）';
