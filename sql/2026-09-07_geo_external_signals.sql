-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 1: External Intelligence Layer
-- 作成日: 2026-09-07
-- 前提: sql/2026-08-02_geo_core_schema.sql（geo_themes, geo_cases, geo_source_registry）
--       sql/2026-08-05_geo_phase2a_ingestion.sql（geo_ingestion_runs）実行済み
--
-- 目的: GDELT DOC 2.0のような高頻度・provider横断のニュースシグナルを、
-- geo_documents（Case/Theme紐付けのHuman Review済みEvidenceストア）とは独立に
-- 蓄積する「Geopolitical News DB / External Intelligence Layer」を新設する。
-- External Signal（未検証）とValidated Knowledge（Knowledge Item状態機械）は
-- 明確に分離し、本migrationは既存のKnowledge/Evidence関連テーブルを一切変更しない。
--
-- 設計（4テーブル）:
--   geo_external_signal_queries        … 監視Query定義（何を観測するか）
--   geo_external_signals                … 記事本体（provider単位でdedup）
--   geo_external_signal_query_matches   … 記事×QueryのN:M対応（なぜこの記事を拾ったか）
--   geo_external_signal_metrics         … coverage/tone/source_country の時系列metric
--
-- 作成順: geo_external_signal_queries → geo_external_signals
--        → geo_external_signal_query_matches → geo_external_signal_metrics
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select count(*) from information_schema.tables
--     where table_schema='public' and table_name like 'geo_external_signal%';
--   -- 期待値: 0（初回適用時）
-- =====================================================================


-- ---- 1. geo_external_signal_queries（監視Queryレジストリ） ----

create table public.geo_external_signal_queries (
    query_id         uuid primary key default gen_random_uuid(),
    name             text not null,
    provider         text not null default 'gdelt'
        check (provider in ('gdelt', 'media_cloud', 'acled', 'rss', 'official', 'babelstreet')),
    query            text not null,
    theme_id         uuid references public.geo_themes(theme_id) on delete set null,
    case_id          uuid references public.geo_cases(case_id) on delete set null,
    enabled          boolean not null default true,
    article_lookback text not null default '7d',
    metric_lookback  text not null default '30d',
    max_records      integer not null default 250,
    last_run_at      timestamptz,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create unique index geo_external_signal_queries_name_idx on public.geo_external_signal_queries(name);
create index geo_external_signal_queries_theme_idx on public.geo_external_signal_queries(theme_id);
create index geo_external_signal_queries_provider_enabled_idx
    on public.geo_external_signal_queries(provider, enabled);

create trigger geo_external_signal_queries_set_updated_at
    before update on public.geo_external_signal_queries
    for each row execute function public.set_updated_at();

alter table public.geo_external_signal_queries enable row level security;

comment on table public.geo_external_signal_queries is
    '外部Provider（Phase1はGDELTのみ）へ継続的に投げる監視Query定義。'
    'article_lookbackは人が読む記事一覧の取得期間、metric_lookbackはcoverage/tone/'
    'source_country timelineの取得期間（Trending Topicsのcurrent/previous期間比較に'
    '必要な日数を確保するため、article_lookbackより長め・既定30日）。'
    'enabled=falseでQuery単位に一時停止できる（Connector自体の有効/無効はconfig側で別管理）';


-- ---- 2. geo_external_signals（外部ニュース記事。Knowledgeとは分離） ----

create table public.geo_external_signals (
    external_signal_id         uuid primary key default gen_random_uuid(),
    provider                   text not null default 'gdelt'
        check (provider in ('gdelt', 'media_cloud', 'acled', 'rss', 'official', 'babelstreet')),
    provider_record_id         text,
    signal_type                text not null default 'article'
        check (signal_type in ('article')),
    title                      text,
    summary                    text,
    url                        text,
    canonical_url              text,
    normalized_url             text,
    source_domain               text,
    source_name                text,
    source_country              text,
    source_language             text,
    published_at                timestamptz,
    timestamp_source            text not null default 'unknown'
        check (timestamp_source in ('publisher', 'gdelt_seen', 'unknown')),
    observed_at                  timestamptz not null default now(),
    last_observed_at             timestamptz not null default now(),
    entities                    jsonb not null default '[]'::jsonb,
    countries                   jsonb not null default '[]'::jsonb,
    tone                        numeric,
    coverage_value               numeric,
    source_role                 text not null default 'discovery'
        check (source_role in ('discovery', 'media_analysis', 'event_data', 'primary_evidence', 'official_statement')),
    evidence_status              text not null default 'unverified'
        check (evidence_status in ('unverified', 'corroborated', 'disputed')),
    raw_metadata                 jsonb,
    validation_status            text not null default 'unreviewed'
        check (validation_status in ('unreviewed', 'reviewed', 'rejected')),
    knowledge_promotion_status   text not null default 'not_promoted'
        check (knowledge_promotion_status in ('not_promoted', 'promoted', 'rejected')),
    dedup_key                   text not null,
    is_demo                     boolean not null default false,
    created_at                   timestamptz not null default now(),
    updated_at                   timestamptz not null default now()
);

create unique index geo_external_signals_provider_dedup_idx
    on public.geo_external_signals(provider, dedup_key);
create index geo_external_signals_published_idx on public.geo_external_signals(published_at desc);
create index geo_external_signals_source_domain_idx on public.geo_external_signals(source_domain);
create index geo_external_signals_source_country_idx on public.geo_external_signals(source_country);

create trigger geo_external_signals_set_updated_at
    before update on public.geo_external_signals
    for each row execute function public.set_updated_at();

alter table public.geo_external_signals enable row level security;

comment on table public.geo_external_signals is
    '外部Provider（GDELT等）から継続収集したニュースシグナル。source_role=discovery, '
    'evidence_status=unverifiedが既定であり、Human Reviewを経たgeo_documents（Evidence）や'
    'Knowledge Item状態機械（geo_knowledge_items）には接続しない。Knowledge Promotionは'
    'Phase2以降（knowledge_promotion_status列はその準備のみ、Phase1では常にnot_promoted）。'
    'theme/query関連は列を持たず、geo_external_signal_query_matches経由のjoinで導出する'
    '（正規化優先、非正規化キャッシュ列を持たない）。dedup_keyはcanonical_url→normalized_url→'
    'source_domain+normalized_titleの優先順で算出し、(provider, dedup_key)で一意にする'
    '（provider単位＝同一記事を将来Media Cloud/RSS等が別途観測した場合は別行として残し、'
    'Provider別の有効性比較を可能にする）。published_atはGDELTが真の公開日時を返せない'
    'ことがあるため、timestamp_sourceでpublisher/gdelt_seen/unknownを区別する';


-- ---- 3. geo_external_signal_query_matches（記事×QueryのN:M対応） ----

create table public.geo_external_signal_query_matches (
    match_id            uuid primary key default gen_random_uuid(),
    external_signal_id  uuid not null references public.geo_external_signals(external_signal_id) on delete cascade,
    query_id            uuid not null references public.geo_external_signal_queries(query_id) on delete cascade,
    first_matched_at    timestamptz not null default now(),
    last_matched_at      timestamptz not null default now(),
    ingestion_run_id     uuid references public.geo_ingestion_runs(ingestion_run_id) on delete set null,
    created_at            timestamptz not null default now()
);

create unique index geo_external_signal_query_matches_signal_query_idx
    on public.geo_external_signal_query_matches(external_signal_id, query_id);
create index geo_external_signal_query_matches_query_idx
    on public.geo_external_signal_query_matches(query_id);

alter table public.geo_external_signal_query_matches enable row level security;

comment on table public.geo_external_signal_query_matches is
    '1記事が複数QueryにヒットしうるためのN:M対応テーブル（例: 同じ記事がTaiwan Strait/'
    'US-China semiconductor export controlsの両方にヒットする場合、2行できる）。'
    '既存の組み合わせが再ヒットした場合はlast_matched_at/ingestion_run_idのみ更新する';


-- ---- 4. geo_external_signal_metrics（coverage/tone/source_country の時系列） ----

create table public.geo_external_signal_metrics (
    metric_id          uuid primary key default gen_random_uuid(),
    provider            text not null default 'gdelt'
        check (provider in ('gdelt', 'media_cloud', 'acled', 'rss', 'official', 'babelstreet')),
    query_id            uuid not null references public.geo_external_signal_queries(query_id) on delete cascade,
    metric_type         text not null
        check (metric_type in ('coverage_volume', 'tone', 'source_country_coverage')),
    series_key          text not null default 'default',
    bucket_at           timestamptz not null,
    value               numeric not null,
    norm_value          numeric,
    raw_metadata         jsonb,
    ingestion_run_id     uuid references public.geo_ingestion_runs(ingestion_run_id) on delete set null,
    observed_at          timestamptz not null default now(),
    created_at            timestamptz not null default now()
);

create unique index geo_external_signal_metrics_key_idx
    on public.geo_external_signal_metrics(provider, query_id, metric_type, series_key, bucket_at);
create index geo_external_signal_metrics_query_type_idx
    on public.geo_external_signal_metrics(query_id, metric_type, bucket_at desc);

alter table public.geo_external_signal_metrics enable row level security;

comment on table public.geo_external_signal_metrics is
    'GDELT Timeline系（TimelineVolRaw=coverage_volume, TimelineTone=tone, '
    'TimelineSourceCountry=source_country_coverage、series_keyに国コード）の時系列metric。'
    '記事(geo_external_signals)とは別テーブルにし、Signals/24h等の件数集計に混ざらないようにする。'
    'Trending Topicsはこのcoverage_volumeのcurrent期間/previous期間合計を比較して算出する'
    '（geo_external_signalsの保存記事数はArticleListの1リクエスト最大250件という制約により'
    '実際の報道量から乖離するため使わない）。再取得時は同一キーがあればvalue等をUPDATEし'
    '（GDELTが直近バケットの値を改訂することがあるため）、行は増殖させない';


-- =====================================================================
-- Seed: Phase1初期Query（4件、nameのunique制約で冪等）
-- =====================================================================

insert into public.geo_external_signal_queries (theme_id, name, provider, query)
select
    -- 修正（2026-09-07、geo_external_signals_fix_seed_theme.sql参照）: 当初は相関を
    -- 持たない定数サブクエリになっており、4件全部に同じtheme_idが入ってしまっていた。
    -- "Taiwan Strait"の行だけtheme_idを紐付ける
    case when v.name = 'Taiwan Strait'
         then (select theme_id from public.geo_themes where code = 'taiwan_strait' limit 1)
         else null end as theme_id,
    v.name, 'gdelt', v.query
from (values
    ('Iran sanctions', 'Iran sanctions'),
    ('Taiwan Strait', 'Taiwan Strait'),
    ('US China semiconductor export controls', 'US China semiconductor export controls'),
    ('Ukraine ceasefire', 'Ukraine ceasefire')
) as v(name, query)
where not exists (
    select 1 from public.geo_external_signal_queries q where q.name = v.name
);
-- "Taiwan Strait"以外の3件はtheme_idがnullのまま投入される（対応する既存geo_themesが
-- 無いため）。運用者が判断のうえ後日UPDATEで紐付けてよい。


-- =====================================================================
-- ロールバック手順（本migrationでは実行しない、記録のみ）
--
-- delete from public.geo_external_signal_queries where name in (
--     'Iran sanctions', 'Taiwan Strait',
--     'US China semiconductor export controls', 'Ukraine ceasefire'
-- );
-- drop table if exists public.geo_external_signal_metrics;
-- drop table if exists public.geo_external_signal_query_matches;
-- drop table if exists public.geo_external_signals;
-- drop table if exists public.geo_external_signal_queries;
-- =====================================================================
