-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2A: Ingestion Run / Raw Snapshot
-- 作成日: 2026-08-05
-- 前提: sql/2026-08-04_geo_knowledge_core.sql が実行済みであること
--
-- 目的: 外部Connector（World Bank, OFAC）の実行履歴・原本データを正式に追跡する。
-- Raw Snapshotは原則Immutable（アプリ側でUPDATEを実装しない）。
-- OFACレコードの継続追跡（版管理）もここで扱う（docs/geopolitical-connector-guide.md参照）。
--
-- 作成順: geo_raw_snapshots → geo_ingestion_runs → geo_ingestion_run_snapshots
--        → geo_entity_match_candidates → geo_sanctions_entries → geo_sanctions_entry_versions
--        → ALTER geo_source_registry
-- =====================================================================


-- ---- 1. geo_raw_snapshots（外部取得の原本、Immutable） ----

create table public.geo_raw_snapshots (
    raw_snapshot_id     uuid primary key default gen_random_uuid(),
    source_id           uuid references public.geo_source_registry(source_id) on delete set null,
    request_url         text,
    request_parameters  jsonb,
    response_headers    jsonb,
    response_status     integer,
    fetched_at          timestamptz not null default now(),
    content_type        text,
    content_hash        text not null,
    storage_path        text,
    source_vintage      text,
    connector_version   text,
    parser_version      text,
    terms_checked_at    timestamptz,
    is_demo             boolean not null default false,
    created_at          timestamptz not null default now()
);

create unique index geo_raw_snapshots_content_hash_idx on public.geo_raw_snapshots(content_hash);
create index geo_raw_snapshots_source_idx on public.geo_raw_snapshots(source_id);

alter table public.geo_raw_snapshots enable row level security;

comment on table public.geo_raw_snapshots is
    '外部Connectorが取得した生レスポンスの原本。content_hashで重複防止。Immutable運用'
    '（アプリ側でUPDATE操作を実装しない。再解析は新しいparser_versionで別途処理する）。'
    '引用・根拠として使う内容はgeo_documents/geo_document_chunksへ変換して保存し、'
    'geo_claim_evidenceはgeo_raw_snapshotsへ直接接続しない';


-- ---- 2. geo_ingestion_runs（Connector実行履歴） ----

create table public.geo_ingestion_runs (
    ingestion_run_id  uuid primary key default gen_random_uuid(),
    source_id         uuid references public.geo_source_registry(source_id) on delete set null,
    connector_type    text not null,
    run_type          text not null default 'manual'
        check (run_type in ('manual', 'cli', 'scheduled_external')),
    status            text not null default 'queued'
        check (status in ('queued', 'running', 'completed', 'completed_with_warnings', 'failed', 'cancelled')),
    started_at        timestamptz not null default now(),
    completed_at      timestamptz,
    requested_by      text,
    request_parameters jsonb,
    fetched_count     integer not null default 0,
    inserted_count    integer not null default 0,
    updated_count     integer not null default 0,
    skipped_count     integer not null default 0,
    rejected_count    integer not null default 0,
    duplicate_count   integer not null default 0,
    warning_count     integer not null default 0,
    error_count       integer not null default 0,
    error_summary     text,
    connector_version text,
    parser_version    text,
    source_vintage    text,
    next_scheduled_at timestamptz,
    is_demo           boolean not null default false
);

create index geo_ingestion_runs_source_idx on public.geo_ingestion_runs(source_id);
create index geo_ingestion_runs_status_idx on public.geo_ingestion_runs(status);
create index geo_ingestion_runs_started_idx on public.geo_ingestion_runs(started_at desc);

alter table public.geo_ingestion_runs enable row level security;

comment on table public.geo_ingestion_runs is
    'Connector実行1回分の履歴。raw_snapshot_idsの配列は持たず、'
    'geo_ingestion_run_snapshotsで個別に対応関係を保持する';


-- ---- 3. geo_ingestion_run_snapshots（Run↔Snapshotの個別Link Table） ----

create table public.geo_ingestion_run_snapshots (
    ingestion_run_id uuid not null references public.geo_ingestion_runs(ingestion_run_id) on delete cascade,
    raw_snapshot_id  uuid not null references public.geo_raw_snapshots(raw_snapshot_id) on delete cascade,
    role             text not null default 'primary_payload'
        check (role in ('primary_payload', 'pagination_page', 'error_response')),
    created_at       timestamptz not null default now(),
    primary key (ingestion_run_id, raw_snapshot_id)
);

alter table public.geo_ingestion_run_snapshots enable row level security;

comment on table public.geo_ingestion_run_snapshots is
    '1つのIngestion Runが生成した複数のRaw Snapshotとの対応関係。'
    'roleはそのsnapshotがRun内で果たした役割を表す';


-- ---- 4. geo_entity_match_candidates（外部Entityの自動Merge防止キュー） ----

create table public.geo_entity_match_candidates (
    match_candidate_id      uuid primary key default gen_random_uuid(),
    source_id               uuid references public.geo_source_registry(source_id) on delete set null,
    raw_snapshot_id         uuid references public.geo_raw_snapshots(raw_snapshot_id) on delete set null,
    external_entity_name    text not null,
    external_entity_aliases jsonb not null default '[]'::jsonb,
    match_status            text not null default 'possible_match'
        check (match_status in (
            'exact_match_candidate', 'alias_match_candidate', 'possible_match', 'no_match', 'conflict'
        )),
    matched_entity_id       uuid references public.geo_entities(entity_id) on delete set null,
    confidence              text not null default '判断不能'
        check (confidence in ('高', '中', '低', '判断不能')),
    notes                   text,
    created_at              timestamptz not null default now(),
    resolved_at             timestamptz,
    resolved_by             text
);

create index geo_entity_match_candidates_source_idx on public.geo_entity_match_candidates(source_id);
create index geo_entity_match_candidates_status_idx on public.geo_entity_match_candidates(match_status);

alter table public.geo_entity_match_candidates enable row level security;

comment on table public.geo_entity_match_candidates is
    '外部データ（OFAC等）のEntityを既存geo_entitiesへ自動Mergeせず、候補として保持する。'
    'resolved_by/resolved_atが埋まるまでは未解決のまま。matched_entity_idが確定してもgeo_entities側の'
    'Mergeはgeo_entity_service.merge_entities（人間のRole操作）を別途通す';


-- ---- 5. geo_sanctions_entries（OFACレコードの現在状態） ----

create table public.geo_sanctions_entries (
    sanctions_entry_id uuid primary key default gen_random_uuid(),
    source_id          uuid references public.geo_source_registry(source_id) on delete set null,
    external_record_id text not null,
    latest_version_id  uuid,
    first_seen_at      timestamptz not null default now(),
    last_seen_at       timestamptz not null default now(),
    current_status     text not null default 'active'
        check (current_status in ('active', 'delisted')),
    matched_entity_id  uuid references public.geo_entities(entity_id) on delete set null,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create unique index geo_sanctions_entries_source_record_idx
    on public.geo_sanctions_entries(source_id, external_record_id);

create trigger geo_sanctions_entries_set_updated_at
    before update on public.geo_sanctions_entries
    for each row execute function public.set_updated_at();

alter table public.geo_sanctions_entries enable row level security;

comment on table public.geo_sanctions_entries is
    'OFAC等リスト型ソースの、external_record_id単位の「現在の代表行」。current_statusは'
    '今どうなっているか（active/delisted）のみを表す。変更履歴はgeo_sanctions_entry_versionsに保持する';


-- ---- 6. geo_sanctions_entry_versions（変更履歴、上書きしない） ----

create table public.geo_sanctions_entry_versions (
    version_id         uuid primary key default gen_random_uuid(),
    sanctions_entry_id uuid not null references public.geo_sanctions_entries(sanctions_entry_id) on delete cascade,
    source_record_hash text not null,
    raw_snapshot_id    uuid references public.geo_raw_snapshots(raw_snapshot_id) on delete set null,
    document_chunk_id  uuid references public.geo_document_chunks(chunk_id) on delete set null,
    entity_name        text not null,
    aliases            jsonb not null default '[]'::jsonb,
    program            text,
    list_date          date,
    addresses          jsonb not null default '[]'::jsonb,
    nationality        text,
    identifiers        jsonb not null default '[]'::jsonb,
    change_type        text not null
        check (change_type in ('added', 'amended', 'delisted', 'relisted')),
    observed_at        timestamptz not null default now(),
    valid_from         date,
    valid_to           date,
    created_at         timestamptz not null default now()
);

create index geo_sanctions_entry_versions_entry_idx
    on public.geo_sanctions_entry_versions(sanctions_entry_id, observed_at desc);

alter table public.geo_sanctions_entry_versions enable row level security;

alter table public.geo_sanctions_entries
    add constraint geo_sanctions_entries_latest_version_fk
    foreign key (latest_version_id) references public.geo_sanctions_entry_versions(version_id) on delete set null;

comment on table public.geo_sanctions_entry_versions is
    'OFACレコードの変更履歴。同一行を上書きしない。新versionを作るのはadded/amended/delisted/'
    'relistedを検知した場合のみ（source_record_hashが前回と同一＝変化なしの場合はversionを作らず、'
    'geo_sanctions_entries.last_seen_atのみ更新する）。新version作成時は直前versionのvalid_toを'
    '今回のobserved_atで更新してから新versionをinsertする。delisted判定は、その回のIngestion Runが'
    'status=completed（全件正常取得）の場合のみ行う。valid_fromは正式な発効日が確認できる場合のみ'
    '設定し、確認できない場合は推定せずnullのままにする';


-- ---- 7. 既存 geo_source_registry の拡張（列削除・データ上書きなし） ----

alter table public.geo_source_registry
    add column if not exists last_ingestion_run_id uuid,
    add column if not exists last_successful_ingestion_at timestamptz,
    add column if not exists last_failed_ingestion_at timestamptz,
    add column if not exists failure_count integer not null default 0;

comment on column public.geo_source_registry.failure_count is
    '直近の連続失敗回数。Ingestion Run成功時に0へリセットする。'
    'geo_freshness_service.classify_source_freshnessのoverdue判定材料の一つ';

-- last_ingestion_run_idの外部キーはgeo_ingestion_runs作成後に追加する
alter table public.geo_source_registry
    add constraint geo_source_registry_last_ingestion_run_fk
    foreign key (last_ingestion_run_id) references public.geo_ingestion_runs(ingestion_run_id) on delete set null;


-- =====================================================================
-- ロールバック手順（Phase 2Aでは実行しない。記録のみ）
--
-- alter table public.geo_source_registry drop constraint if exists geo_source_registry_last_ingestion_run_fk;
-- alter table public.geo_source_registry drop column if exists last_ingestion_run_id;
-- drop table if exists public.geo_sanctions_entry_versions;
-- alter table public.geo_sanctions_entries drop constraint if exists geo_sanctions_entries_latest_version_fk;
-- drop table if exists public.geo_sanctions_entries;
-- drop table if exists public.geo_entity_match_candidates;
-- drop table if exists public.geo_ingestion_run_snapshots;
-- drop table if exists public.geo_ingestion_runs;
-- drop table if exists public.geo_raw_snapshots;
-- =====================================================================
