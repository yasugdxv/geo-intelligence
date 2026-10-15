-- =====================================================================
-- Report Studio（外部Babel Street/PowerShellワークフロー内製化）Phase1用テーブル
-- 作成日: 2026-09-02
-- 対象: Supabase（PostgreSQL）。新規構築、ALTER/DROP/移行処理は含まない。
--
-- 設計方針（Phase0監査の結論）:
--   * geo_documents/geo_source_registry/geo_themes等の既存Expert AIテーブルは
--     Report Studioの生収集データ（Babel Street由来の大量ソーシャル/報道記事）
--     とは粒度・要件が異なるため流用しない（theme_id NOT NULL等の制約が合わない）。
--     ただし将来Expert AI連携時（Phase3）は、Report Studio側のClusterから
--     geo_documents/geo_knowledge_itemsを参照する形で橋渡しする想定。
--   * 内部ZIP（Prompt1/Prompt2互換入力）は監査・エクスポート専用とし、内部契約は
--     本スキーマのテーブル/サービスオブジェクトとする（ZIP常駐を正としない）。
--   * Narrative Master（signal_narrative_master.csv、WIDE v6.0形式、1行=Signal×
--     Narrativeペア）は唯一のプロジェクト固有設定源。個別スキーマテーブルへ分解せず
--     行全体をjsonbで保持する（列数47・今後の列追加に強くするため。既存の
--     geo_events.quantities/geo_indicator_rule_versions.rule_definition等、本
--     プロジェクトの「柔軟な設定はjsonb」という既存慣習に合わせる）。
--   * Evidence Classification（Prompt1相当、LLM判定結果）はPhase3で追加する
--     `classification`列（nullable jsonb、本ファイルで予約のみ）に格納する。
--     Phase1では一切書き込まない。
--
-- 作成順: geo_report_projects → geo_report_narrative_master_rows
--        → geo_report_collection_runs → geo_report_clusters
--        → geo_report_cluster_representatives → geo_report_records
-- =====================================================================


-- ---- 1. geo_report_projects（Report Studioプロジェクト） ----

create table public.geo_report_projects (
    project_id     uuid primary key default gen_random_uuid(),
    project_code   text not null,
    project_title  text not null,
    report_language text not null default 'ja',
    status         text not null default 'active'
        check (status in ('active', 'archived')),
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create unique index geo_report_projects_code_idx on public.geo_report_projects(project_code);

create trigger geo_report_projects_set_updated_at
    before update on public.geo_report_projects
    for each row execute function public.set_updated_at();

alter table public.geo_report_projects enable row level security;

comment on table public.geo_report_projects is
    'Report Studioプロジェクト（例: イラン監視）。外部run.ps1のMasterSet/project_idに対応';


-- ---- 2. geo_report_narrative_master_rows（Narrative Master行、WIDE v6.0そのまま保持） ----

create table public.geo_report_narrative_master_rows (
    id           uuid primary key default gen_random_uuid(),
    project_id   uuid not null references public.geo_report_projects(project_id) on delete cascade,
    signal_id    text not null,
    narrative_id text not null,
    is_fallback  boolean not null default false,
    active       boolean not null default true,
    row_data     jsonb not null,
    imported_at  timestamptz not null default now(),
    created_at   timestamptz not null default now()
);

create unique index geo_report_narrative_master_rows_unique_idx
    on public.geo_report_narrative_master_rows(project_id, signal_id, narrative_id);
create index geo_report_narrative_master_rows_project_idx
    on public.geo_report_narrative_master_rows(project_id);

alter table public.geo_report_narrative_master_rows enable row level security;

comment on table public.geo_report_narrative_master_rows is
    'signal_narrative_master.csv（WIDE v6.0、47列/行）の1 Signal×Narrative行をそのまま'
    'jsonbで保持する。列個別カラム化はしない（将来の列追加・複数フォーマット共存に対応するため）。'
    'row_dataの中身の解釈（include_keywords等）はgeo_report_processing_service.pyが担う';


-- ---- 3. geo_report_collection_runs（前処理ラン、run_manifest.json相当） ----

create table public.geo_report_collection_runs (
    run_id                 uuid primary key default gen_random_uuid(),
    project_id             uuid not null references public.geo_report_projects(project_id) on delete cascade,
    status                 text not null default 'running'
        check (status in ('running', 'completed', 'failed')),
    period_start           date,
    period_end             date,
    input_file_summary     jsonb,
    preprocess_audit       jsonb,
    population_summary     jsonb,
    total_records          integer,
    in_scope_records       integer,
    nonduplicate_records   integer,
    cluster_count          integer,
    error_text             text,
    started_at             timestamptz not null default now(),
    completed_at           timestamptz,
    created_at             timestamptz not null default now()
);

create index geo_report_collection_runs_project_idx
    on public.geo_report_collection_runs(project_id, started_at desc);

alter table public.geo_report_collection_runs enable row level security;

comment on table public.geo_report_collection_runs is
    '前処理（Signal/Narrative照合・重複排除・クラスタリング）1回の実行記録。'
    'population_by_signal_*.csv・preprocess_audit_summary.csv相当の集計はここへjsonbで格納する'
    '（record単位ではないため件数が小さく、正規化テーブル化しない）';


-- ---- 4. geo_report_clusters（cluster_labeling_input.csv相当、Prompt1/3の入力） ----

create table public.geo_report_clusters (
    id                       uuid primary key default gen_random_uuid(),
    run_id                   uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    cluster_id               text not null,
    signal_id                text not null,
    narrative_id             text not null,
    time_bucket              text,
    source_bucket            text,
    split_label              text,
    match_signature          text,
    record_count             integer not null default 0,
    matched_share            numeric,
    fallback_share           numeric,
    distributions            jsonb,
    report_priority          text,
    report_section           text,
    report_order             integer,
    continuity_role          text,
    preliminary_consistency  text,
    requires_review          boolean not null default true,
    classification           jsonb,
    classified_at            timestamptz,
    created_at               timestamptz not null default now()
);

create unique index geo_report_clusters_run_cluster_idx
    on public.geo_report_clusters(run_id, cluster_id);
create index geo_report_clusters_run_idx on public.geo_report_clusters(run_id);

alter table public.geo_report_clusters enable row level security;

comment on table public.geo_report_clusters is
    'cluster_labeling_input.csv相当。1行=1クラスタ（Signal×Narrative×期間×ソース種別等で'
    'シャーディングされた集約単位）。classification列はPhase3のEvidence Classification'
    'Service（Prompt1相当のLLM判定）が書き込む予約列で、Phase1では常にnull';


-- ---- 5. geo_report_cluster_representatives（cluster_representatives.csv相当） ----

create table public.geo_report_cluster_representatives (
    id                uuid primary key default gen_random_uuid(),
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    cluster_id        text not null,
    representative_no integer not null,
    record_id         text not null,
    title             text,
    text_for_review   text,
    source_name       text,
    source_url        text,
    published_at      text,
    language          text,
    source_country    text,
    author_or_account text,
    document_type     text,
    source_bucket     text,
    matched_terms     text,
    match_status      text,
    split_label       text,
    source_file       text,
    created_at        timestamptz not null default now()
);

create index geo_report_cluster_representatives_run_cluster_idx
    on public.geo_report_cluster_representatives(run_id, cluster_id);

alter table public.geo_report_cluster_representatives enable row level security;

comment on table public.geo_report_cluster_representatives is
    'cluster_representatives.csv相当。クラスタごとの代表サンプル記事（reservoir sampling）。'
    'Evidence Classification Service・Reportの引用元本文はここから取得する';


-- ---- 6. geo_report_records（record_cluster_map_*.csv相当、レコード単位の監査台帳） ----

create table public.geo_report_records (
    id                       uuid primary key default gen_random_uuid(),
    run_id                   uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    record_id                text not null,
    source_file              text not null,
    timestamp_raw            text,
    date_key                 text,
    signal_id                text not null,
    narrative_id             text,
    match_status             text,
    matched_terms            text,
    split_label              text,
    time_bucket              text,
    cluster_id               text,
    is_duplicate             boolean not null default false,
    duplicate_group_id       text,
    period_in_scope          boolean not null default true,
    source_bucket            text,
    source_name              text,
    author                   text,
    document_type            text,
    language                 text,
    source_country           text,
    url                      text,
    engagement_raw           text,
    url_hash                 text,
    text_hash                text,
    created_at               timestamptz not null default now()
);

create index geo_report_records_run_idx on public.geo_report_records(run_id);
create index geo_report_records_run_cluster_idx on public.geo_report_records(run_id, cluster_id);

alter table public.geo_report_records enable row level security;

comment on table public.geo_report_records is
    'record_cluster_map_*.csv相当。レコード単位（全件）の監査台帳。ZIPを内部契約にしないための'
    '正本データ。件数が多いためbatch insert前提（geo_report_processing_service.py側でchunk書き込み）';
