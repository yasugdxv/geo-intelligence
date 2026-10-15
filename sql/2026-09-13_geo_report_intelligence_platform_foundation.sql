-- Report Studio → 汎用Intelligence Analysis Platform 再構築、Phase 0-2基盤。
-- 2026-09-13、詳細設計は docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md
-- （Section D）参照。すべてAdditive only（新規テーブル7本）、既存テーブルへの
-- ALTERは無し。Production Pipeline（geo_report_prompt1_orchestration_service.py等）
-- はこれらのテーブルを一切参照しない——Phase 2 Benchmark専用の別系統として追加する。
--
-- 用語注記: 当初「Gold Dataset」と呼んでいたが、これはCurrent Prompt1（Strong LLM）
-- 自身の出力であり、人手検証を経た真のGold Standardではないため、科学的に正確な
-- 呼称として「Reference Dataset / Reference Labels」を使う（"gold"という語は使わない）。

-- Reference Dataset。Current Prompt1のexecution結果をタスク別・クラスタ単位で
-- 保持する。immutableとして扱う——splitはここに持たせず、
-- geo_report_benchmark_split_assignments（下記）で別管理する。
create table public.geo_report_reference_examples (
    id                uuid primary key default gen_random_uuid(),
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    execution_id      uuid not null references public.geo_report_executions(id),
    task              text not null check (task in ('ac_classification','sc_classification','targeted_stance')),
    cluster_type      text not null check (cluster_type in ('analysis','sentiment')),
    cluster_id        text not null,  -- (run_id, cluster_id)複合、単体FKにしない(cluster_idはRun間globalに一意でないため)
    item_key          text not null default '',  -- Targeted Stance用(target_type:target_key)。AC/SCは''固定
    label_json        jsonb not null,
    source            text not null default 'current_prompt1_strong_llm',
    model             text,
    prompt_version    text,
    reference_quality text not null default 'reference_only'
        check (reference_quality in ('human_verified','qa_pass','reference_only','ambiguous')),
    human_reviewed    boolean not null default false,
    qa_status         text,
    created_at        timestamptz not null default now(),
    unique(execution_id, task, cluster_id, item_key)
);
create index geo_report_reference_examples_run_task_idx on public.geo_report_reference_examples(run_id, task);
create index geo_report_reference_examples_cluster_idx on public.geo_report_reference_examples(cluster_type, cluster_id);

comment on table public.geo_report_reference_examples is
    'Current Prompt1(Strong LLM)出力から作るReference Dataset。immutable——splitは持たせない。"Gold"ではなく"Reference"と呼ぶ(独立したground truthではないため)。';

-- Benchmark Split Assignment。splitはReference属性ではなくBenchmark実験属性として
-- 分離管理する——同じReference Labelを異なるbenchmark_run_id/split_schemeで何度も
-- 別々に評価しうるため。
create table public.geo_report_benchmark_split_assignments (
    id                uuid primary key default gen_random_uuid(),
    benchmark_run_id  text not null,
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    task              text not null check (task in ('ac_classification','sc_classification','targeted_stance')),
    cluster_type      text not null check (cluster_type in ('analysis','sentiment')),
    cluster_id        text not null,
    item_key          text not null default '',
    split_scheme      text not null check (split_scheme in ('temporal','narrative_holdout')),
    split             text not null check (split in ('train','validation','test')),
    group_key         text,  -- narrative_holdout: narrative_id。temporal: week_start。
                              -- 実際にはLeakage Group id(connected component)を優先的に使う
    excluded_due_to_temporal_leakage boolean not null default false,
    random_seed       integer,
    created_at        timestamptz not null default now(),
    unique(benchmark_run_id, split_scheme, task, cluster_type, cluster_id, item_key)
);
create index geo_report_benchmark_split_assignments_idx
    on public.geo_report_benchmark_split_assignments(benchmark_run_id, split_scheme, task);

-- Feature Dataset。split/method非依存のcluster factのみ持つ
-- (novelty/nearest_neighbor_distance/prediction_entropyはここに置かない、
-- geo_report_semantic_census_predictionsにのみ持たせる)。
create table public.geo_report_cluster_features (
    id                       uuid primary key default gen_random_uuid(),
    run_id                   uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    cluster_type             text not null check (cluster_type in ('analysis','sentiment')),
    cluster_id               text not null,
    feature_version          text not null,  -- 'v1-no-embeddings' / 'v2-with-embeddings' 等
    volume                   integer,
    preliminary_consistency_score numeric, -- Preprocessの自己申告一貫性(High/Medium/Low)を数値化したもの。
                                            -- embedding由来の意味的一貫性(cohesion列)とは別物
    cohesion                 numeric,  -- 予約列(v1はNULL)。将来representative embeddingから算出する
                                        -- 真の意味的cohesionのみここに入れる
    dispersion               numeric,
    source_entropy           numeric,
    language_entropy         numeric,
    fallback_share           numeric,
    narrative_similarity     numeric,
    complexity_score         numeric,
    representative_diversity_score numeric,
    narrative_priority_score numeric, -- Narrative Masterのpriority/trend_role由来の重要度特徴。
                                       -- Semantic Census分類器の入力特徴には使わない、Router専用
    metadata_json            jsonb,
    computed_at              timestamptz not null default now(),
    unique(run_id, cluster_type, cluster_id, feature_version)
);

comment on column public.geo_report_cluster_features.narrative_priority_score is
    'Router専用のimportance feature。Semantic Census分類器(B〜E)の入力特徴には使わない。';

-- Representative Selection候補。Production representatives(reservoir sampling)は変更しない。
create table public.geo_report_representative_selection_variants (
    id                       uuid primary key default gen_random_uuid(),
    run_id                   uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    cluster_type             text not null check (cluster_type in ('analysis','sentiment')),
    cluster_id               text not null,
    strategy                 text not null,  -- 'current_reservoir' / 'typical_diverse_boundary_v1'
    representative_record_ids jsonb not null,
    selection_metadata       jsonb,
    created_at               timestamptz not null default now(),
    unique(run_id, cluster_type, cluster_id, strategy)
);

-- Embedding Cache。Content Cacheとして設計する(membershipを持たない)——1 recordは
-- AC/SC両方のクラスタに同時に所属しうるため、entity_type/entity_id/model/version/
-- input_text_hashで一意に決まる「このテキストをこのモデルでembeddingした結果」に徹し、
-- どのクラスタに属するかは持たない(利用側がgeo_report_records/representative tablesへ
-- Joinしてmembershipを都度導出する)。Benchmark専用、Production非接続。
create table public.geo_report_embedding_cache (
    id                uuid primary key default gen_random_uuid(),
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    entity_type       text not null check (entity_type in ('representative','record','cluster')),
    entity_id         text not null,  -- representative/record: 対象行のid。cluster: cluster_id(将来用、v1未使用)
    embedding_model   text not null,  -- 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
    embedding_version text not null,
    input_text_hash   text not null,  -- raw textから再生成可能なことを保証(sha256、正規化後テキスト基準)
    embedding_vector  jsonb not null, -- float配列。pgvector非導入(Production schemaをEmbedding前提にしない)
    dimension         integer not null,
    created_at        timestamptz not null default now(),
    unique(run_id, entity_type, entity_id, embedding_model, embedding_version, input_text_hash)
);

comment on table public.geo_report_embedding_cache is
    'Benchmark専用Content Cache。Production Pipelineからは参照されない。';

-- Semantic Census Benchmark結果。
create table public.geo_report_semantic_census_benchmarks (
    id                uuid primary key default gen_random_uuid(),
    benchmark_run_id  text not null,
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    task              text not null check (task in ('ac_classification','sc_classification','targeted_stance')),
    method            text not null check (method in
        ('strong_llm_reference','small_llm','embedding_nn','logistic_regression','gradient_boosting','hybrid')),
    method_variant    text not null default 'default',  -- 'gpt-4o-mini'/'gpt-4.1-mini'等。
                                         -- NOT NULL DEFAULT必須(PostgreSQLのUNIQUEはNULL同士を
                                         -- 別値とみなし重複を防げないため)
    model_version     text,
    method_config_json jsonb,
    random_seed       integer,
    embedding_model    text,
    embedding_version  text,
    split_scheme      text not null check (split_scheme in ('temporal','narrative_holdout')),
    split             text not null check (split in ('train','validation','test')),
    field_metrics_json jsonb not null,  -- field毎precision/recall/f1/macro_f1/per_class/confusion +
                                        -- class_support。Trainに1件も無いclassがある場合は
                                        -- そのfield×methodをproduction_eligible:falseと明記する
    cluster_exact_match_rate numeric,
    calibration_json  jsonb,
    escalation_cutoffs_json jsonb,      -- {target_total_escalation_rate: {mandatory_rate, cutoff,
                                        --  actual_rate, infeasible}}
    latency_ms_avg    numeric,
    relative_cost     numeric,
    created_at        timestamptz not null default now(),
    unique(benchmark_run_id, split_scheme, task, method, method_variant, split)
);

create table public.geo_report_semantic_census_predictions (
    id                uuid primary key default gen_random_uuid(),
    benchmark_run_id  text not null,
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    task              text not null,
    cluster_type      text not null,
    cluster_id        text not null,
    item_key          text not null default '',
    method            text not null,
    method_variant    text not null default 'default',
    split_scheme      text not null check (split_scheme in ('temporal','narrative_holdout')),
    split             text not null check (split in ('train','validation','test')),
    field_predictions_json jsonb not null,
    field_raw_confidence_json  jsonb,
    field_calibrated_confidence_json jsonb,
    overall_raw_confidence numeric,
    overall_calibrated_confidence numeric,
    unseen_reference_label_fields jsonb not null default '[]'::jsonb,
    novelty_proxy_score numeric,
    nearest_neighbor_distance numeric,
    prediction_entropy numeric,
    latency_ms        integer,
    created_at        timestamptz not null default now(),
    unique(benchmark_run_id, split_scheme, task, method, method_variant, cluster_id, item_key)
);
create index geo_report_semantic_census_predictions_bench_idx
    on public.geo_report_semantic_census_predictions(benchmark_run_id, run_id, task, method);

comment on column public.geo_report_semantic_census_predictions.unseen_reference_label_fields is
    'Benchmark診断専用。正解ラベルを参照するoracle情報のためProduction Routerの意思決定には使わない。';
