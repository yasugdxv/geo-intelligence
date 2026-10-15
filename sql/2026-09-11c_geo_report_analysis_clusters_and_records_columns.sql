-- =====================================================================
-- geo_report_analysis_clusters / geo_report_analysis_cluster_representatives
-- （Current Pipeline専用、Sentiment側と対になるAnalysis Cluster正規化テーブル）
-- + geo_report_records列追加（Current Prompt1 Input record_cluster_map相当の
--   欠落列を追加。既存列は一切変更しない）
-- 作成日: 2026-09-11
-- 根拠: C:\Users\269811\.claude\plans\kind-napping-hare.md Phase D実装メモ
--
-- 設計方針: 既存geo_report_clusters/geo_report_cluster_representatives
-- （21/16列程度）は実際のCurrent契約（33/23列、Phase A監査結果1で実測確認済み）と
-- 形状が大きく異なる。既存テーブルは`classification`列の書き込み予約も含め
-- Iran/EUDR実運用（旧Prompt1 v6.2）専用のまま維持し、Current PipelineはSentiment
-- Cluster側と同様に専用テーブルを新設する（既存テーブルへの列追加や共存は
-- 「既存コードのSELECT/集計を壊さない」方針と矛盾するため採らない）。
-- =====================================================================

create table public.geo_report_analysis_clusters (
    id                       uuid primary key default gen_random_uuid(),
    run_id                   uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    analysis_cluster_id      text not null,
    signal_id                text not null,
    signal_name              text,
    narrative_id             text not null,
    narrative_name           text,
    report_topic_label       text,
    theme_id                 text,
    theme_name               text,
    trend_role                text,
    trend_start_date          text,
    main_narrative            text,
    week_start                text,
    week_end                  text,
    cluster_records           integer not null default 0,
    eligible_records          integer not null default 0,
    duplicate_records         integer not null default 0,
    source_class_preliminary  text,
    source_bucket             text,
    reaction_type_hint        text,
    attitude_hint             text,
    cluster_cue               text,
    match_signature           text,
    exception_candidate_count integer not null default 0,
    source_distribution       text,
    reaction_distribution     text,
    attitude_distribution     text,
    language_distribution     text,
    fallback_share            numeric,
    preliminary_consistency   text,
    requires_review           boolean not null default true,
    created_at                timestamptz not null default now()
);

create unique index geo_report_analysis_clusters_run_cluster_idx
    on public.geo_report_analysis_clusters(run_id, analysis_cluster_id);
create index geo_report_analysis_clusters_run_idx on public.geo_report_analysis_clusters(run_id);

alter table public.geo_report_analysis_clusters enable row level security;

comment on table public.geo_report_analysis_clusters is
    'cluster_labeling_input.csv相当（Prompt1 Current Input、Current Pipeline専用）。既存'
    'geo_report_clustersとは別テーブル（形状が実際のCurrent契約と大きく異なるため、Iran/EUDR'
    '実運用中の既存テーブルには一切触れない）。分類結果はgeo_report_analysis_cluster_'
    'classificationsへExecution-scopedで持つ';


create table public.geo_report_analysis_cluster_representatives (
    id                        uuid primary key default gen_random_uuid(),
    run_id                    uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    analysis_cluster_id       text not null,
    representative_no         integer not null,
    record_id                 text not null,
    title                     text,
    text_for_review           text,
    source_name               text,
    domain                    text,
    url                       text,
    published_at              text,
    language_group            text,
    author                    text,
    document_type             text,
    source_bucket             text,
    source_class_preliminary  text,
    reaction_type_hint        text,
    attitude_hint             text,
    matched_terms             text,
    match_status               text,
    source_file                text,
    created_at                 timestamptz not null default now()
);

create index geo_report_analysis_cluster_representatives_run_cluster_idx
    on public.geo_report_analysis_cluster_representatives(run_id, analysis_cluster_id);

alter table public.geo_report_analysis_cluster_representatives enable row level security;

comment on table public.geo_report_analysis_cluster_representatives is
    'cluster_representatives.csv相当（Current Pipeline専用）。既存
    geo_report_cluster_representativesとは別テーブル（同上の理由）';


-- geo_report_sentiment_clusters（既に適用済み、2026-09-11_...sql）にも同じ理由で
-- signal_name/narrative_nameが欠けていたため追加する（analysis側と揃える）。

alter table public.geo_report_sentiment_clusters
    add column if not exists signal_name text,
    add column if not exists narrative_name text;


-- geo_report_records: Current Prompt1 Input record_cluster_map相当に必要な列を追加
-- （既存列は一切変更しない、既存geo_report_incremental_service.pyのbase_key再構築ロジックへの
-- 影響なし）。

alter table public.geo_report_records
    add column if not exists week_start text,
    add column if not exists week_end text,
    add column if not exists theme_id text,
    add column if not exists theme_name text,
    add column if not exists trend_role text,
    add column if not exists trend_start_date text,
    add column if not exists narrative_name text,
    add column if not exists source_class_preliminary text,
    add column if not exists reaction_type_hint text,
    add column if not exists attitude_hint text,
    add column if not exists cluster_cue text,
    add column if not exists match_signature text;

comment on column public.geo_report_records.analysis_cluster_id is
    'Current Pipeline専用列（Phase B ALTER）。geo_report_analysis_clusters.analysis_cluster_idと'
    '(run_id, analysis_cluster_id)で対応する';
comment on column public.geo_report_records.sentiment_cluster_id is
    'Current Pipeline専用列（Phase B ALTER）。geo_report_sentiment_clusters.sentiment_cluster_idと'
    '(run_id, sentiment_cluster_id)で対応する';


-- geo_report_executions: 進捗可視化用（ユーザー要望、2026-09-11）。前処理・分類バッチとも
-- 実行に時間がかかるため、実行中のExecution行へ定期的に書き込み、UIが別リクエストで
-- ポーリングして状況を見られるようにする（本プロジェクトにバックグラウンドジョブ基盤が
-- 無いため、同期実行中でも別リクエストからの読み取りで進捗が見える設計にする）。

alter table public.geo_report_executions
    add column if not exists progress jsonb;

comment on column public.geo_report_executions.progress is
    '実行中の進捗（例: preprocessなら{"files_processed":3,"files_total":9,"current_file":"..."}、'
    'prompt1分類なら{"batches_completed":5,"batches_total":20}）。UIが/executions/{id}をポーリングして'
    '表示する。完了後もそのまま最終値を保持する（クリアしない）';
