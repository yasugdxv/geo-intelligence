-- Phase 3A Shadow Router: provenance/監査ログの拡充。
-- ユーザー指摘(2026-09-13, Cross-Run Shadow開始前の必須事項): 「同じRunを掘らない」
-- Cross-Run Generalization検証に備え、モデルがどのReference Dataset/embedding
-- version/calibration方式で学習されたかを明示的に追跡できるようにする。
-- Additive only、既存列は変更しない。

alter table public.geo_report_shadow_router_models
    add column if not exists model_version text not null default 'v1',
    add column if not exists reference_dataset_execution_id uuid
        references public.geo_report_executions(id),
    add column if not exists calibration_json jsonb;

comment on column public.geo_report_shadow_router_models.reference_dataset_execution_id is
    'このモデルの学習に使ったReference Dataset(geo_report_reference_examples)の元となった'
    'Prompt1 execution_id。Cross-Run適用時に「どの版のReferenceで学習したモデルか」を'
    '追跡するためのfingerprint。';

comment on column public.geo_report_shadow_router_models.calibration_json is
    'field毎のcalibration方式("isotonic"|"identity")。geo_report_semantic_census_benchmarks.'
    'calibration_jsonと同じ形式。';
