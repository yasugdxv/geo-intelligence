-- Phase 3A: SC-only Shadow Router基盤。
-- 詳細設計: docs/report-studio/2026-09-13_intelligence_platform_phase0-2/SUMMARY.md
-- Section 20/23。Production Canonicalには一切接続しない——
-- geo_report_prompt1_orchestration_service.py等の既存Production Pipelineは
-- 本ファイルのテーブルを一切参照しない。Additive only。
--
-- Shadow Router ModelはCross-Run Generalizationのため「凍結」して永続化する
-- （confidence cutoff/novelty閾値/学習済み分類器/Train centroidsを固定し、
-- 新しいRunへ再学習せずそのまま適用する。SUMMARY.md Section 23の
-- 「新Runでも閾値は最初は固定する」という方針をテーブル設計として担保する）。

create table public.geo_report_shadow_router_models (
    id                          uuid primary key default gen_random_uuid(),
    task                        text not null check (task in ('sc_classification')),
    cluster_type                text not null check (cluster_type in ('sentiment')),
    method                      text not null check (method in ('logistic_regression', 'hybrid')),
    role                        text not null check (role in ('primary', 'challenger')),
    trained_on_run_id           uuid not null references public.geo_report_collection_runs(run_id),
    trained_on_benchmark_run_id text not null,
    split_scheme                text not null check (split_scheme in ('temporal', 'narrative_holdout')),
    target_escalation_rate      numeric not null,
    feature_version             text not null,
    embedding_model             text,
    embedding_version           text,
    classifiers_blob            text not null,  -- base64(pickle(dict[field, FieldClassifier]))
    calibrators_blob            text not null,  -- base64(pickle(dict[field, IsotonicRegression|None]))
    confidence_cutoff           numeric not null,
    novelty_gate_threshold      numeric not null,
    train_centroids_json        jsonb not null,  -- novelty計算用、Train split centroidの配列(float配列のlist)
    evidence_min_representative_count integer not null default 1,
    evidence_min_total_chars    integer not null default 50,
    status                      text not null default 'active' check (status in ('active', 'retired')),
    created_at                  timestamptz not null default now()
);
create index geo_report_shadow_router_models_active_idx
    on public.geo_report_shadow_router_models(task, cluster_type, method, status);

comment on table public.geo_report_shadow_router_models is
    'Phase 3A Shadow Router用の凍結済み学習済みモデル。Cross-Run Generalization検証のため、'
    '新しいRunへ適用する際もここに保存された閾値・分類器を再学習せずそのまま使う。';

create table public.geo_report_shadow_router_predictions (
    id                            uuid primary key default gen_random_uuid(),
    model_id                      uuid not null references public.geo_report_shadow_router_models(id) on delete cascade,
    run_id                        uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    cluster_type                  text not null check (cluster_type in ('sentiment')),
    cluster_id                    text not null,
    field_predictions_json        jsonb not null,
    field_calibrated_confidence_json jsonb not null,
    overall_calibrated_confidence numeric,
    novelty_proxy_score           numeric,
    nearest_neighbor_distance     numeric,
    evidence_sufficiency_json     jsonb not null,  -- {representative_count, usable_text_count, total_chars, sufficient}
    router_decision               text not null check (router_decision in ('lightweight_accept', 'would_escalate')),
    router_reason                 jsonb not null default '[]'::jsonb,
    canonical_strong_result_json  jsonb,  -- Canonical Prompt1 SC分類結果(利用可能な場合のみ、無ければNULL)
    field_agreement_json          jsonb,  -- canonical_strong_result_jsonがある場合のみ
    critical_field_disagreement   boolean,
    adjudication_status           text not null default 'pending'
        check (adjudication_status in
            ('pending', 'reviewed_shadow_correct', 'reviewed_canonical_correct',
             'reviewed_both_plausible', 'reviewed_undecidable')),
    adjudication_notes            text,
    created_at                    timestamptz not null default now(),
    unique(model_id, run_id, cluster_id)
);
create index geo_report_shadow_router_predictions_run_idx
    on public.geo_report_shadow_router_predictions(run_id, model_id);

comment on column public.geo_report_shadow_router_predictions.router_decision is
    'Shadow Mode中はProductionの実LLM呼び出しには一切影響しない(記録のみ)。'
    'lightweight_accept/would_escalateの削減率はRealized SavingsではなくProjected Savings。';
