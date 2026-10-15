-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2A: Metric Model
-- 作成日: 2026-08-05
-- 前提: sql/2026-08-05_geo_phase2a_ingestion.sql が実行済みであること
--
-- World Bank等の時系列数値データの保存先。改訂値は削除せずrevision_idで並存させる。
-- geo_claim_evidenceにmetric_observation_idを追加し、Claimの根拠として直接引用できるようにする。
--
-- 作成順: geo_metric_definitions → geo_metric_observations
--        → ALTER geo_claim_evidence → geo_knowledge_item_claims
-- =====================================================================


-- ---- 1. geo_metric_definitions（指標定義） ----

create table public.geo_metric_definitions (
    metric_definition_id uuid primary key default gen_random_uuid(),
    source_id            uuid references public.geo_source_registry(source_id) on delete set null,
    metric_code          text not null,
    metric_name          text not null,
    description          text,
    unit                 text,
    frequency            text,
    methodology          text,
    geographic_scope     text,
    source_url           text,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

create unique index geo_metric_definitions_source_code_idx
    on public.geo_metric_definitions(source_id, metric_code);

create trigger geo_metric_definitions_set_updated_at
    before update on public.geo_metric_definitions
    for each row execute function public.set_updated_at();

alter table public.geo_metric_definitions enable row level security;

comment on table public.geo_metric_definitions is
    'World Bank等の外部ソースが提供する時系列指標の定義（例: Population, GDP）。'
    'コードへの大量ハードコードを避け、config/geo_world_bank_indicators_seed.yaml等から登録する';


-- ---- 2. geo_metric_observations（実測値、改訂はrevisionで並存） ----

create table public.geo_metric_observations (
    metric_observation_id uuid primary key default gen_random_uuid(),
    metric_definition_id  uuid not null references public.geo_metric_definitions(metric_definition_id) on delete cascade,
    entity_id             uuid not null references public.geo_entities(entity_id) on delete cascade,
    counterpart_entity_id uuid references public.geo_entities(entity_id) on delete set null,
    period                text not null,
    observed_value        numeric,
    estimate_or_actual     text not null default 'actual'
        check (estimate_or_actual in ('actual', 'estimate', 'forecast')),
    source_vintage        text,
    revision_id           integer not null default 1,
    retrieved_at          timestamptz not null default now(),
    raw_snapshot_id       uuid references public.geo_raw_snapshots(raw_snapshot_id) on delete set null,
    is_demo               boolean not null default false,
    created_at            timestamptz not null default now()
);

create index geo_metric_observations_definition_entity_idx
    on public.geo_metric_observations(metric_definition_id, entity_id, period);
create index geo_metric_observations_raw_snapshot_idx on public.geo_metric_observations(raw_snapshot_id);

alter table public.geo_metric_observations enable row level security;

comment on table public.geo_metric_observations is
    '指標の実測値。改訂値が取得された場合、旧値は削除せずrevision_idを増やして並存させる'
    '（同一metric_definition_id×entity_id×periodで複数revisionがあり得る）';


-- ---- 3. 既存 geo_claim_evidence の拡張（metric_observation_idを4択目に追加） ----

alter table public.geo_claim_evidence
    add column if not exists metric_observation_id uuid references public.geo_metric_observations(metric_observation_id) on delete cascade;

alter table public.geo_claim_evidence
    drop constraint if exists geo_claim_evidence_one_target;

alter table public.geo_claim_evidence
    add constraint geo_claim_evidence_one_target check (
        (case when document_chunk_id is not null then 1 else 0 end
       + case when event_id is not null then 1 else 0 end
       + case when observation_id is not null then 1 else 0 end
       + case when metric_observation_id is not null then 1 else 0 end) = 1
    );

comment on column public.geo_claim_evidence.metric_observation_id is
    'World Bank等のMetric Observationを直接Evidenceとして引用する場合に設定する。'
    'document_chunk_id/event_id/observation_id/metric_observation_idのうちちょうど1つを設定すること'
    '（geo_claim_evidence_one_target制約で強制）';


-- ---- 4. geo_knowledge_item_claims（Knowledge ItemとSupporting Claimの個別Link Table） ----

create table public.geo_knowledge_item_claims (
    knowledge_item_id uuid not null references public.geo_knowledge_items(knowledge_item_id) on delete cascade,
    claim_id          uuid not null references public.geo_claims(claim_id) on delete cascade,
    relationship      text not null default 'supporting'
        check (relationship in ('supporting', 'contradicting')),
    created_at        timestamptz not null default now(),
    primary key (knowledge_item_id, claim_id)
);

alter table public.geo_knowledge_item_claims enable row level security;

comment on table public.geo_knowledge_item_claims is
    'Knowledge Itemを裏付ける（またはそれに反する）Claimの個別Link Table。Active Knowledge Itemを'
    'geo_expert_engineへ渡す条件（Supporting Claim必須＋そのClaimがEvidenceまで遡れること）で使用する'
    '（docs/geopolitical-expert-engine-integration.md参照）';


-- =====================================================================
-- ロールバック手順（Phase 2Aでは実行しない。記録のみ）
--
-- drop table if exists public.geo_knowledge_item_claims;
-- alter table public.geo_claim_evidence drop constraint if exists geo_claim_evidence_one_target;
-- alter table public.geo_claim_evidence drop column if exists metric_observation_id;
-- alter table public.geo_claim_evidence add constraint geo_claim_evidence_one_target check (
--     (case when document_chunk_id is not null then 1 else 0 end
--    + case when event_id is not null then 1 else 0 end
--    + case when observation_id is not null then 1 else 0 end) = 1
-- );
-- drop table if exists public.geo_metric_observations;
-- drop table if exists public.geo_metric_definitions;
-- =====================================================================
