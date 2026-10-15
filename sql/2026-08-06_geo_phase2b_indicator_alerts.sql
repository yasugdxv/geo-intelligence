-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2B: Indicator閾値アラート + Indicator Freshness
-- 作成日: 2026-08-06
-- 前提: sql/2026-08-02_geo_core_schema.sql（geo_indicator_definitions/
--      geo_indicator_rule_versions/geo_observations）が適用済みであること
-- =====================================================================


-- ---- 1. geo_indicator_alerts（閾値超過アラート、Observation単位で冪等） ----

create table public.geo_indicator_alerts (
    alert_id        uuid primary key default gen_random_uuid(),
    indicator_id    uuid not null references public.geo_indicator_definitions(indicator_id) on delete cascade,
    rule_version_id uuid not null references public.geo_indicator_rule_versions(rule_version_id) on delete restrict,
    observation_id  uuid not null unique references public.geo_observations(observation_id) on delete cascade,
    severity        text not null check (severity in ('warning', 'critical')),
    triggered_at    timestamptz not null,
    acknowledged_at timestamptz,
    acknowledged_by text,
    created_at      timestamptz not null default now()
);

create index geo_indicator_alerts_indicator_idx on public.geo_indicator_alerts(indicator_id);

alter table public.geo_indicator_alerts enable row level security;

comment on table public.geo_indicator_alerts is
    'Indicator閾値超過アラート。observation_idにunique制約があるため、同一Observationに対し'
    '複数回再計算を実行してもAlertは重複作成されない（geo_indicator_engine.create_alert_if_needed'
    'が事前にexistsチェックを行うが、DB制約でも二重に保証する）';


-- ---- 2. geo_indicator_definitions の拡張（Indicator Freshness用） ----

alter table public.geo_indicator_definitions
    add column if not exists expected_update_cadence text
        check (expected_update_cadence is null or
               expected_update_cadence in ('daily', 'weekly', 'monthly', 'quarterly', 'annual'));

comment on column public.geo_indicator_definitions.expected_update_cadence is
    'この指標が本来どの頻度で更新されるべきか。geo_freshness_service.classify_indicator_freshness'
    'が最新Observationのas_ofとの経過日数を判定する基準に使う'
    '（geo_connector_base.UPDATE_CADENCE_DAYSと同じ語彙。geo_source_registryの'
    'update_cadence/classify_source_freshnessとは独立した、Indicator固有の判定）';


-- =====================================================================
-- ロールバック手順（Phase 2Bでは実行しない。記録のみ）
--
-- alter table public.geo_indicator_definitions drop column if exists expected_update_cadence;
-- drop table if exists public.geo_indicator_alerts;
-- =====================================================================
