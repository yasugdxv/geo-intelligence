# Report Studio → 汎用Intelligence Analysis Platform 再構築（2026-09-12着手）

## Context

Current Prompt1/Prompt2パイプラインは、行数上限バグ修正・TS絞り込み・並列化を経て
E2E完走が安定した（AC 1,477/1,477・SC 1,773/1,773・TS 508件・33.8分・206 LLM calls・
cache hit 54.3%・missing/invalid 0）。しかしこの最適化の延長では、「データ量が10倍に
なればLLM callも10倍になる」という構造的な限界から抜け出せない。

ユーザーの狙いは、Current Prompt1個別最適化ではなく、**LLM処理量が生データ量ではなく
「意味的難易度×不確実性×重要性」に比例するArchitecture**（Python構造化→Lightweight
Semantic Census→Strong LLM Deep Review→Expert AI Insight→Evidence-based Report）へ
段階的に移行すること。既存資産（Prompt1 Strong LLM・Narrative Master・Expert AI
Adapter・Execution Model・Prompt1/2 file contract）は最大限再利用し、Current Production
Pipelineは一切壊さない（Current Prompt1はReference/Fallback Engineとして残す）。

今回のスコープは全体像（Phase 0〜8、Evidence/Fact/Claim/Insight Canonical含む）のうち
**Phase 0（Architecture Audit）〜Phase 2（Semantic Census Benchmark）のみ**。Production
Canonicalは変更しない。DBは破壊的変更禁止、Additive onlyとする。

## A. Phase 0: Architecture Audit結果（確定事実、2エージェントによる監査済み）

### A.1 Report Studio DBスキーマ
- **2系統のClusterテーブルが意図的に併存**: 旧`geo_report_clusters`/`geo_report_cluster_representatives`
  （21/16列、Iran/EUDR実運用専用、`classification`列は予約済みだが未使用のまま）と、
  新`geo_report_analysis_clusters`+`geo_report_sentiment_clusters`（33/23列、Current
  Pipeline専用）。新Platformは後者のみを対象にする。前者には触れない。
- **`geo_report_records`**: 41列。`language`列は既存（一部コード内コメントが「無い」と
  誤記載しているが実際は当初から存在）。`analysis_cluster_id`/`sentiment_cluster_id`も
  既存。`entity_mentions`・`embedding`・`embedding_ref`・`duplicate_status`列は**存在しない**
  （重複判定は`is_duplicate`+`duplicate_group_id`で代替）。
- **Embedding基盤は皆無**: コードベース全体を検索しても、Report Studio・Expert AI双方に
  実働のembedding生成/保存/検索コードは存在しない。`geo_knowledge_items.embedding
  vector(1536)`列のみ存在するが「Phase 1では一切参照しない」と明記された予約列で、
  読み書きするPythonコードはゼロ。Azure OpenAI embeddings deploymentも未設定
  （`config.json`の`model_map`はchatモデルのみ: gpt-4o/gpt-4o-mini/gpt-4.1/gpt-4.1-mini/
  gpt-5/gpt-5.1/o1/o3/o3-mini）。**gpt-4o-mini/gpt-4.1-miniは新規依存なしですぐ使える。**
- **Gold Dataset/ground truthテーブルは存在しない**（Report Studio/Expert AI双方）。
  `docs/session2-golden-case-baseline/`はコード変更前後比較用の一時スナップショットで
  queryable datasetではない。`geo_expert_review_service.py`のExpert Review機構は
  「専門家が言ったから真実ではない」と明言しており正誤判定用途ではない。
- **Representative selectionは決定的reservoir sampling**（`geo_report_current_pipeline_service.
  add_representative`、`run.ps1`の`Add-Representative`と同一アルゴリズム、
  `seed=hash(record_id|cluster_id)`で同一入力なら同一代表投稿を再現）。Top-Nでも
  純粋randomでもない。既にexception representatives（mixed/unknown/multi-reaction/
  unmatchedを最大4件）を別枠で追跡済み——新方式の"Boundary/Exception"選択の土台になる。
- **Execution Model**（`geo_report_executions`+`geo_report_execution_batches`）は
  stage/execution_method/status/progress/canonical昇格まで揃った汎用チェックポイント
  基盤で、新stage追加にそのまま耐えられる。`stage='report_edit'`はDB制約のみ存在し
  未実装（要注意、今回は触れない）。
- **Narrative Master 34列は二重語彙**: Design Chat側（`required_context`等13列相当）と
  Shell/Preprocess側（`required_context_keywords`等19列、`match_master()`が実際に読む）
  が同居し、`sync_shell_mirror_columns()`が3ペアだけ相互補完する。`required_context`
  （単数、Targeted Stance用）と`required_context_keywords`（複数、Preprocess matching用）
  は**別列・別用途**（要混同注意）。

### A.2 Expert AIシステム
- `geo_expert_engine.py`/`geo_expert_schema.py`/`geo_expert_quality_guard.py`が本体。
  confidenceは**数値ではなく4値enum**（高/中/低/判断不能）、Quality Guardが証拠クラス・
  出典等に基づき決定的に「格下げのみ」補正するパイプライン（11関数、確率的較正ではない）。
- **Expert AI Adapter Protocolが既存・再利用可能**: `geo_report_design_expert_adapter.py`の
  `ReportDesignExpertAdapter`（Protocol）+ `GeoExpertDesignAdapter`（唯一の実装）。
  Knowledge（`knowledge/geo_expert/`静的md 9本、バージョンハッシュ付き）とRetrieval
  （Actor/Media Intelligence、名前ベースの厳密突合、embeddingではない）を疎結合で
  提供する設計。Analysis Contract（本Plan B章）のExpert AI接続はこれをそのまま使う。
- Report StudioとExpert AI本体（`geo_expert_engine.py`）の直接結合は薄い
  （`geo_expert_common`のKnowledge読み込みと、`geo_report_synthesis_service.py`が
  `geo_expert_engine`の private formatter関数を2つ流用するのみ）。

### A.3 実データ規模（今回のFull Run実測、`run_id=d558df7e`, `execution_id=6e27804c`）
AC 1,477件・SC 1,773件・TS 508件（旧932件から45.5%削減）・LLM calls 206回・
input 2,933,096 / output 666,245 / cached 1,592,320 tokens（cache hit 54.3%）・
wall-clock 33.8分・missing/invalid 0。**この1 Runのみが現時点で「AC/SC/TS全件
Strong LLM分類済み」の完全なデータセットであり、Reference Datasetの初期母集団となる
（"Gold"ではなく"Reference"と呼ぶ理由はSection Cで詳述）。**
Iran/EUDR等の他プロジェクトは旧`classification`列が未使用のまま（予約列）のため、
Strong LLM分類済みデータは存在しない——**Run跨ぎのReference Datasetは現状構築不可能**
（Section Cで詳述、既知のリスクとして明記）。

## B. Keep / Change / Replace

| 区分 | 対象 |
|---|---|
| **Keep（無変更）** | `geo_report_executions`/`geo_report_execution_batches`（新stage追加のみ）、`geo_llm_common.call_llm_structured`/`reconcile_batch_llm_results`、Narrative Master 34列契約+`required_context`によるTargeted Stance target検出、reservoir sampling代表選定（拡張の土台として維持）、Prompt1 9ファイル/Prompt2 10ファイル契約（Compatibility Contract）、Prompt2 deterministic aggregation、`ReportDesignExpertAdapter`/`GeoExpertDesignAdapter`、旧`geo_report_clusters`系（Iran/EUDR専用、touchしない） |
| **Change（Additive）** | 新テーブル6本追加（Section D）。既存テーブルへのALTERは今回ゼロ件（Phase 0-2スコープでは不要と判断） |
| **Replace（今回はしない）** | Prompt1 Strong LLM Engine・Prompt2 Composition・Current Canonical確定ロジックはすべて無変更。Evidence/Fact/Claim/Insight Canonical（Section 15-20）はPhase 5-6スコープでありPhase 0-2では作らない（先に作ると「実験せずArchitectureだけ書く」禁止事項に抵触するため意図的に後回し） |

## C. Reference Dataset（"Gold"と呼ばない——Current Prompt1自身の出力であり独立ground truthではないため）

**用語の訂正**: 当初「Gold Dataset」と呼んでいたが、これはCurrent Prompt1（Strong LLM）
自身の出力であり、人手による正誤検証を経た真のGold Standardではない。科学的に正確な
呼称として**Reference Dataset / Reference Labels**とする（テーブル名も
`geo_report_reference_examples`とし、"gold"という語は今回のPhase 0-2成果物からは
使わない）。

**制約（重要、正直に記録する）**: Run-level split（Section Iで詳述）が本来望ましいが、
現時点でStrong LLM分類済みの完全なRunは`d558df7e`／`6e27804c`の1件のみ。複数Run跨ぎの
Reference Datasetは物理的に構築不可能——これは今回のPhase 2ベンチマークの構造的限界であり、
Phase 3以降で新規Runを増やすことが前提条件になる。さらに、この1 Runには
`baseline_run_id`が無く（continuity/previous run無し）、**Spotモード相当のデータしか
存在しない**——今回の評価は「Spotモードでの妥当性」のみを対象とし、「Spot/Monitoring
両方を評価した」とは言わない（Monitoring特有のcontinuity/drift featureはPhase 2の
評価対象外、Phase 3以降で複数時点のRunが揃ってから検証する）。

新規サービス `geo_report_reference_dataset_service.py`:
- `build_reference_examples_from_execution(client, run_id, execution_id)`: 完了済み
  `geo_report_analysis_cluster_classifications`/`geo_report_sentiment_cluster_
  classifications`/`geo_report_targeted_stance`（execution_id=`6e27804c`）を
  クラスタ本体・代表投稿と結合し、`geo_report_reference_examples`（Section D）へ3 task
  （ac_classification/sc_classification/targeted_stance）分の行を書き込む。
  Targeted Stanceは1クラスタ複数targetを持つため、`item_key`
  （`target_type:target_normalized_id_or_name`）を付与し、AC/SC taskでは
  `item_key=''`固定とする（Section Dのunique制約参照）。
- `source='current_prompt1_strong_llm'`固定。**`confidence_tier`ではなく
  `reference_quality`**（enum: `human_verified`/`qa_pass`/`reference_only`/`ambiguous`）
  を持たせる。今回のRunはすべて`reference_quality='qa_pass'`
  （`compute_prompt1_quality_audit`のqa_status='PASS'相当の場合）または
  `'reference_only'`（QA未通過/REVIEW REQUIRED相当）とし、
  `preliminary_consistency`/`cluster_consistency`/`stance_confidence`
  （＝Prompt1が「このクラスタは一貫している」と自己申告した指標であり分類結果自体の
  正しさの指標ではない）とは明確に区別して混同しない。`human_reviewed=False`固定
  （正直な現状表示）。将来、人手レビューを経たものだけ`reference_quality=
  'human_verified'`へ格上げする運用とする。
- Current Prompt1の元テーブル（`geo_report_*_classifications`/`geo_report_targeted_
  stance`）自体は一切変更しない（読み取り専用）。
- **`geo_report_reference_examples`はimmutableとする（重要な訂正）**: split
  （train/validation/test）はReferenceの属性ではなく、あくまでBenchmark実験ごとに
  変わりうる実験属性である——同じReference Labelを、異なるbenchmark_run_id・
  異なるsplit_scheme（temporal/narrative_holdout）で何度も別々に評価しうるため。
  split割当は`geo_report_reference_examples`へ持たせず、独立した
  `geo_report_benchmark_split_assignments`（Section D）で管理する。

## D. 追加DB Migration（Additive only、新規7テーブル）

`sql/2026-09-13_geo_report_intelligence_platform_foundation.sql`:

```sql
-- Reference Dataset（Section C。"Gold"ではなくReferenceと呼ぶ）
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

-- Benchmark Split Assignment（Section I。splitはReference属性ではなくBenchmark実験属性
-- として分離管理する——同じReference Labelを異なるbenchmark_run_id/split_schemeで
-- 何度も別々に評価しうるため、Reference自体はimmutableに保つ）
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
                              -- 実際にはLeakage Group id(Section I)を優先的に使う
    excluded_due_to_temporal_leakage boolean not null default false,  -- Section I Leakage Guard
    random_seed       integer,
    created_at        timestamptz not null default now(),
    unique(benchmark_run_id, split_scheme, task, cluster_type, cluster_id, item_key)
);
create index geo_report_benchmark_split_assignments_idx
    on public.geo_report_benchmark_split_assignments(benchmark_run_id, split_scheme, task);

-- Feature Dataset（Section E）
create table public.geo_report_cluster_features (
    id                       uuid primary key default gen_random_uuid(),
    run_id                   uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    cluster_type             text not null check (cluster_type in ('analysis','sentiment')),
    cluster_id               text not null,
    feature_version          text not null,  -- 'v1-no-embeddings' / 'v2-with-embeddings' 等
    volume                   integer,
    preliminary_consistency_score numeric, -- 旧cohesion。Preprocessの自己申告一貫性(High/Medium/Low)を
                                            -- 数値化しただけの指標であり、embedding由来の意味的一貫性とは別物
    cohesion                 numeric,  -- 予約列(v1はNULL)。将来representative embeddingから算出する
                                        -- 真の意味的cohesionのみここに入れ、上のpreliminary_consistency_scoreと
                                        -- 混同しない
    dispersion               numeric,
    source_entropy           numeric,
    language_entropy         numeric,
    fallback_share           numeric,
    narrative_similarity     numeric,
    -- 注意: nearest_neighbor_distance/novelty_proxy_score/prediction_entropyは
    -- ここに置かない。これらはどのTrain set(=split_scheme)を基準にするか、
    -- どのmethodの予測分布を使うかに依存するsplit/method依存値であり、
    -- split/method非依存のcluster factであるこのテーブルの性質と矛盾する。
    -- 実体は`geo_report_semantic_census_predictions`（Section D、split_scheme/method
    -- 別に1行）にのみ持たせる。
    complexity_score         numeric,
    representative_diversity_score numeric,
    narrative_priority_score numeric, -- Narrative Masterのpriority/trend_role/continuity_hint由来の重要度特徴。
                                       -- **Semantic Census分類器(B〜E)の入力特徴には使わない**——Router
                                       -- (Section K)専用のfeatureとしてのみ参照する(Section H/K参照)
    metadata_json            jsonb,
    computed_at              timestamptz not null default now(),
    unique(run_id, cluster_type, cluster_id, feature_version)
);

-- Representative Selection候補（Section F、Production representativesは変更しない）
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

-- Embedding Cache（Benchmark専用、Production非接続。Section G/F参照）
-- Content Cache（membershipを持たない、重要な設計訂正）: 1 recordはAC/SC両方の
-- クラスタに同時に所属しうる（`geo_report_records`は`analysis_cluster_id`と
-- `sentiment_cluster_id`を両方持つ）ため、embedding row自体に単一の
-- cluster_type/cluster_idを持たせると実態と矛盾する。この表は
-- entity_type/entity_id/model/version/input_text_hashで一意に決まる
-- 「このテキストをこのモデルでembeddingした結果」というContent Cacheに徹し、
-- 「どのクラスタに属するか」はここでは持たない——利用側が
-- `geo_report_records`/representative tablesへJoinしてmembershipを都度導出する。
-- entity_type: 'representative'(representative行のid)/'record'(recordのid)/
-- 'cluster'(将来用、v1では未使用、cluster centroidはこの表に保存せず
-- representative/record embeddingの平均として都度計算する、再現性優先)。
create table public.geo_report_embedding_cache (
    id                uuid primary key default gen_random_uuid(),
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    entity_type       text not null check (entity_type in ('representative','record','cluster')),
    entity_id         text not null,  -- representative/record: 対象行のid。cluster: cluster_id
    embedding_model   text not null,  -- 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
    embedding_version text not null,  -- モデルのrevision/commit hash等
    input_text_hash   text not null,  -- raw textから再生成可能なことを保証(sha256、正規化後テキスト基準)
    embedding_vector  jsonb not null, -- float配列。pgvector非導入(要件2:Production schemaをEmbedding前提にしない)
    dimension         integer not null,
    created_at        timestamptz not null default now(),
    unique(run_id, entity_type, entity_id, embedding_model, embedding_version, input_text_hash)
);

-- Semantic Census Benchmark結果（Section H/J）
create table public.geo_report_semantic_census_benchmarks (
    id                uuid primary key default gen_random_uuid(),
    benchmark_run_id  text not null,  -- 1回のbenchmark実行を束ねるラベル
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    task              text not null check (task in ('ac_classification','sc_classification','targeted_stance')),
    method            text not null check (method in
        ('strong_llm_reference','small_llm','embedding_nn','logistic_regression','gradient_boosting','hybrid')),
    method_variant    text not null default 'default',  -- 'gpt-4o-mini'/'gpt-4.1-mini'等、method内の
                                         -- さらなる区別。NOT NULL DEFAULT必須(PostgreSQLのUNIQUEは
                                         -- NULL同士を別値とみなし重複を防げないため)
    model_version     text,                -- LLM deployment名/embeddingモデルversion等
    method_config_json jsonb,              -- hyperparameter/random_seed以外の設定一式(再現用)
    random_seed       integer,
    embedding_model    text,               -- embedding系methodのみ。'sentence-transformers/...'
    embedding_version  text,
    split_scheme      text not null check (split_scheme in ('temporal','narrative_holdout')),
    split             text not null check (split in ('train','validation','test')),
    field_metrics_json jsonb not null,  -- field毎のprecision/recall/f1/macro_f1/per_class/confusion +
                                        -- class_support(train/validation/test毎のクラス別件数、少数class可視化用)。
                                        -- Trainに1件も存在しないclassがそのfieldにある場合、
                                        -- そのfield×methodは"production_eligible: false"と明記する
    cluster_exact_match_rate numeric,   -- 全field一致したクラスタの割合(holistic指標)
    calibration_json  jsonb,            -- field毎のcalibration mapping(isotonic/platt/bin)。
                                         -- Validation splitで学習し、Test split評価時に適用する
    escalation_cutoffs_json jsonb,      -- {target_total_escalation_rate: {mandatory_rate, cutoff,
                                        --  actual_rate, infeasible}}。Validationで解いた値(Section K、
                                        -- mandatory escalation適用後の残りに対してcutoffを解く設計)
    latency_ms_avg    numeric,
    relative_cost     numeric,          -- Strong LLM callを1.0とした相対コスト
    created_at        timestamptz not null default now(),
    unique(benchmark_run_id, split_scheme, task, method, method_variant, split)
);
create table public.geo_report_semantic_census_predictions (
    id                uuid primary key default gen_random_uuid(),
    benchmark_run_id  text not null,
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,  -- cluster_idはRun間globalに一意でないため必須
    task              text not null,
    cluster_type      text not null,
    cluster_id        text not null,
    item_key          text not null default '',  -- Targeted Stance用(target_type:target_key)。
                                         -- AC/SC taskは''固定(Section Cのreference_examplesと同じ設計)
    method            text not null,
    method_variant    text not null default 'default',  -- 'gpt-4o-mini'/'gpt-4.1-mini'等
                                         -- (Section D benchmarksと同じ区別。NOT NULL DEFAULT必須)
    split_scheme      text not null check (split_scheme in ('temporal','narrative_holdout')),
    split             text not null check (split in ('train','validation','test')),
    field_predictions_json jsonb not null,      -- {"theme_relevance": "relevant", "source_class": "Public Voice", ...}
    field_raw_confidence_json  jsonb,           -- field毎の自己申告/predict_proba(未較正)。
                                                 -- 例: {"theme_relevance_confidence": 0.94, "source_class_confidence": 0.81, ...}
    field_calibrated_confidence_json jsonb,     -- calibration_jsonを適用した後の値。Routerが使うのはこちらのみ
    overall_raw_confidence numeric,
    overall_calibrated_confidence numeric,      -- min(field calibrated confidences)
    unseen_reference_label_fields jsonb not null default '[]'::jsonb,  -- **Benchmark診断専用**
                                                 -- (Section H/K参照)。このsplit_schemeのTrainに
                                                 -- 存在しなかったreference label値を持つfield名の配列。
                                                 -- 正解ラベルを参照するoracle情報のため、
                                                 -- Production Routerの意思決定には使わない——
                                                 -- 「このfield×methodはTrainのclass coverageが
                                                 -- 不十分でproduction-eligibleではない」という
                                                 -- Benchmarkレベルの所見を出すためだけに使う
    novelty_proxy_score numeric,
    nearest_neighbor_distance numeric,
    prediction_entropy numeric,
    latency_ms        integer,
    created_at        timestamptz not null default now(),
    unique(benchmark_run_id, split_scheme, task, method, method_variant, cluster_id, item_key)
);
create index geo_report_semantic_census_predictions_bench_idx
    on public.geo_report_semantic_census_predictions(benchmark_run_id, run_id, task, method);
```

`REQUIRED_SCHEMA_CHECKS`（`geo_report_studio_service.py`）へ上記7テーブルを追加登録する。
既存テーブルへのALTERは無し。Production Pipeline（`geo_report_prompt1_orchestration_
service.py`等）はこれらのテーブルを一切参照しない——完全に別系統として追加する。

## E. Feature Dataset（v1、Embeddingなし）＋ Importance Feature

`geo_report_cluster_feature_service.py`:
- `volume`=`cluster_records`、`fallback_share`=クラスタ行の既存列そのまま、
  `source_entropy`/`language_entropy`=`source_distribution`/`language_distribution`
  文字列（`"Public_Voice_Candidate:2"`形式）からShannon entropy計算、
  `preliminary_consistency_score`=`preliminary_consistency`（High/Medium/Low）を
  数値マッピングしたもの（Preprocessの自己申告一貫性であり、embedding由来の
  真の意味的cohesionとは別物——`cohesion`列自体はv1では使わずNULLのまま予約する、
  Section D参照）、`complexity_score`=`exception_candidate_count`/
  `requires_review`/`cluster_records`からのヒューリスティック合成スコア。
- **`narrative_priority_score`（新規、要件追加分）**: Narrative Masterの`priority`
  （High/Medium/Low）＋`trend_role`（primary重み増し）から合成する重要度スコア。
  「semantic difficulty × uncertainty × **importance**」の3軸目を明示的にFeatureへ
  組み込む——これが無いとRouterが「簡単か難しいか」だけで決まってしまい、
  「分析上重要か」という今回のArchitecture思想が抜けてしまうため。
- `dispersion`/`narrative_similarity`は**v1ではNULL**（representative-level
  embeddingが要る、Section G完了後に`feature_version='v2-with-embeddings'`の
  別行として追加、既存v1行は上書きしない）。
- **`novelty_proxy_score`/`nearest_neighbor_distance`/`prediction_entropy`は
  `geo_report_cluster_features`には一切持たせない（重要な訂正）**——これらは
  「どのsplit_scheme（＝どのTrain set）を基準にするか」「どのmethodの予測分布か」に
  依存するsplit/method依存値であり、split/method非依存のcluster factを持つ
  このテーブルの性質と矛盾する。実体は`geo_report_semantic_census_predictions`
  （Section D、split_scheme×method別に1行）にのみ持たせる。
  **ただし`novelty_proxy_score`/`nearest_neighbor_distance`自体は「そのsplit_scheme
  のTrain setに対するembedding距離」というmethod非依存の量なので、Small LLMを
  含む全candidate methodについて共通の値を使う**（1回計算してすべてのmethodの
  predictions行にコピーする、method毎に計算し直さない）。`prediction_entropy`のみ
  method固有（各methodの予測分布のエントロピー）。
- **命名の注意**: 複数Run・複数テーマが揃うまでは`novelty_proxy_score`/
  `nearest_neighbor_distance`を「OOD」と呼ばない（Section Gで詳述）。
- 不要なfeatureを増やさない（ユーザー指示）。実際にRouter Simulation（Section K）で
  予測力が確認できたfeatureのみ本採用候補とし、それ以外は記録専用に留める。

## F. Representative Selection候補実装（Representative単位Embedding前提、候補母集団はcluster所属の全record）

`geo_report_representative_strategy_service.py`:
- Strategy `current_reservoir`（既存のまま、変更なし）をbaseline行として
  `geo_report_representative_selection_variants`へそのままコピー保存。
- Strategy `typical_diverse_boundary_v1`（新規、最大5件）の**候補母集団は
  既存reservoir代表5件ではなく、そのクラスタに属する全record**とする
  （`geo_report_records.analysis_cluster_id`/`sentiment_cluster_id`で絞り込み）。
  既存reservoir代表だけをembeddingして選び直しても「同じ5件の中から選び直す」
  だけになり新方式の比較にならないため、**Benchmark対象クラスタ（後述サンプル
  100〜200件）に限り、そのクラスタの全recordをrecord-level embedding**
  （`geo_report_embedding_cache.entity_type='record'`、Section D参照）した上で
  Typical/Diverse/Boundary/Exceptionを選ぶ。全Run全recordをembeddingする必要は
  ない——Benchmark対象クラスタの全recordのみで十分（コスト抑制）。Semantic Census
  本体（Section H）のcluster centroidは、引き続き既存reservoir代表のembedding平均
  （`entity_type='representative'`）で計算してよい（そちらは全クラスタ対象のため
  軽量な方を使う）。
  - 2 Typical（record-level centroidへの距離が近い上位2件）
  - 1 Diverse（MMR: 既選択集合とのembedding距離max-min）
  - **1 Boundary（定義修正、Section Cの指摘反映）**: Reference labelはクラスタ単位
    でありrecord単位のlabelは存在しないため、「Reference labelが割れている」という
    定義は使わない。代わりに次のいずれかから選ぶ:
    (a) centroidからの距離は遠いが当該クラスタのmatch対象ではある record、
    (b) 隣接クラスタ（別のnarrative/signalの）centroidにもembedding距離が近い record
    （＝クラスタ境界上にある可能性が高い）、
    (c) `source_class_preliminary`/`attitude_hint`/`reaction_type_hint`が
    クラスタ多数派と異なる record、
    (d) 既存の`is_exception`判定に該当する record。
  - 1 Important/Exception（既存の`exception_representatives`をそのまま採用、重複計算しない）。
- **評価方法（3-way比較、循環評価とLLM非決定性の混同を避ける設計）**: Reference
  Datasetからサンプル（100〜200クラスタ程度）を抽出し、**同じサンプルに対して
  以下3つを同条件（同じStrong LLMプロンプト・同じモデル・同じ実行タイミング）で
  比較する**（`typical_diverse_boundary_v1`の代表投稿だけを現在のStrong LLMへ
  再投入すると、Reference自体が過去の別実行の出力であるため「Representative戦略の
  違い」と「LLMの非決定性/deployment差分」が混同されてしまうため）:
  1. **historical reference**: Reference Dataset自体（過去の`current_reservoir`＋
     過去のStrong LLM実行結果、再実行しない）。
  2. **current-reservoir-rerun**: 既存`current_reservoir`の代表投稿を、**今の**
     Strong LLM（同一プロンプト・同一モデル）で再分類した結果。
  3. **new-representative-rerun**: `typical_diverse_boundary_v1`の代表投稿を、
     同じく**今の**Strong LLMで再分類した結果。
  3つの比較（historical vs current-rerun／historical vs new／current-rerun vs new）
  を出すことで、historical vs current-rerunの差分が「LLM自体の非決定性・
  deployment変化」の推定値になり、current-rerun vs newの差分だけが純粋な
  「Representative戦略の違い」を表すようにする。**一致したケースは自動PASSとする
  一方、current-rerunとnewが不一致となった20〜30件はサンプル抽出し、元のクラスタ
  全体のレコードを見てどちら側のRepresentative集合がクラスタの実態をより正しく
  代表しているかを個別にレビューする**（実装時はレビュー用の比較artifact/CSVを
  生成し、人手またはClaudeによる一次スクリーニングを行う——後者の場合は
  「アナリストによる正式レビューの代替ではない簡易チェック」であることを明記する）。
  全件（100〜200件）を人手レビュー
  するわけではない、あくまで不一致サブセットのみ。

## G. Embedding Benchmark環境（Phase 2専用、Production非接続——ユーザー指示9項目を設計原則化）

ユーザー指示（9項目）をそのまま設計原則として明文化する:

1. Production Pipeline（`geo_report_prompt1_orchestration_service.py`等）からは
   一切import/呼び出しされない。新規`geo_report_embedding_benchmark_service.py`は
   Benchmark実行スクリプト/APIからのみ呼ばれる。
2. Production DBスキーマ（`geo_report_records`等）へembedding列を追加しない
   （Section Dの通り、embeddingは独立した`geo_report_embedding_cache`テーブルのみに存在）。
3. モデル名・versionを`geo_report_embedding_cache.embedding_model`/`embedding_version`
   へ必ず記録する（下記モデル選定を参照）。
4. `embedding_vector`はBenchmark artifact/cacheであり、`input_text_hash`
   （sha256、正規化済みtext基準）で照合し、raw textから**いつでも再生成可能**にする
   （テキスト変更や別モデルへの切替時はcacheキーが変わるので自動的に再計算対象になる）。
5. 将来Azure Embeddingsを導入した場合は、Production採用前に必ず再Benchmarkする。
6. Azure Embeddings採用時は本Benchmarkと同じ`geo_report_semantic_census_benchmarks`
   形式で必ず再実行し比較する（再Benchmark必須という運用ルールをコードコメントとして
   `geo_report_embedding_benchmark_service.py`冒頭に明記する）。
7. local embeddingで学習したclassifier（Logistic Regression/Gradient Boosting）の
   モデルファイルには`embedding_model`/`embedding_version`をメタデータとして必ず埋め込み、
   異なるembedding_modelの特徴量を渡された場合は明示的にエラーを出す
   （`_require_matching_embedding_model()`のようなガード関数を実装）。
8. `geo_report_reference_examples`（Current Prompt1由来のReference Dataset）は本
   Benchmark work中、読み取り専用として扱う——書き込みは一切しない。
9. pip install/download中に明らかな長時間ハング（ネットワーク・プロキシ起因）が
   発生した場合は、粘らず中断してユーザーへ報告する。

### モデル選定: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`

日本語・英語混在データのため、multilingual対応モデルが必須。比較検討した結果:

| モデル | dim | サイズ目安 | 多言語対応 | 備考 |
|---|---|---|---|---|
| **paraphrase-multilingual-MiniLM-L12-v2（採用）** | 384 | ~470MB | 50+言語(ja/en含む) | sentence-transformers公式配布、Apache-2.0、追加のprefix指定等が不要でシンプル |
| paraphrase-multilingual-mpnet-base-v2 | 768 | ~970MB | 50+言語 | 精度はやや高いが2倍近く重い。今回は「軽量さ優先」の指示に反する |
| intfloat/multilingual-e5-small | 384 | ~470MB | 100言語 | 高性能だが"query:"/"passage:"prefix運用が必要でBenchmark実装がやや複雑 |
| distiluse-base-multilingual-cased-v2 | 512 | ~500MB | 15言語 | 対応言語数で見劣り |

「巨大で高性能なモデルは不要、Embedding方式そのものの有効性を見たい」という指示に
沿い、**paraphrase-multilingual-MiniLM-L12-v2**を採用する。CPU推論速度・
sentence-transformers完全互換・シンプルなAPI（prefix不要）を優先した。

`geo_report_embedding_benchmark_service.py`:
- **`sentence-transformers`/`torch`はBenchmark専用のoptional dependencyとして隔離する
  （重要）**: Production側のmain dependency（実行中のAPIサーバーが読み込む環境）には
  一切追加しない。新規`requirements-benchmark.txt`（このプロジェクトには現状
  requirements.txt自体が無く、venvへ都度pip installする運用のため、Benchmark実行者が
  参照する専用の依存リストファイルとして新設する）で管理し、
  `pip install -r requirements-benchmark.txt`（プロキシ経由、ユーザー承認済み）は
  Benchmarkスクリプト実行前にのみ行う。`geo_report_embedding_benchmark_service.py`
  自体は`import sentence_transformers`をモジュールトップレベルではなく関数内
  （`get_embedding_model()`内）で遅延importし、Production側のAPIサーバー起動時に
  このモジュールが誤ってimportされても即座に依存エラーにならないようにする。
- `get_embedding_model()`: モデルをプロセス内で1度だけロードしてキャッシュ。
- `compute_embeddings_for_representatives(client, run_id, cluster_type, cluster_ids)`:
  **representative単位**（クラスタ単位ではない、Section F参照）でtitle+text_for_review
  を正規化結合したテキストをエンコードし、`geo_report_embedding_cache`
  （`entity_type='representative'`, `entity_id`=representative行のid）へ保存
  （既にhashが一致する行はスキップ、再計算しない）。**Content Cacheの設計上
  （Section D）、この表自体はどのクラスタに属するかを持たない**。
- `compute_cluster_centroid(client, run_id, cluster_type, cluster_id) -> list[float]`:
  まず`geo_report_analysis_cluster_representatives`/`geo_report_sentiment_cluster_
  representatives`（cluster_typeに応じて）を`cluster_id`でJoinしてそのクラスタの
  representative行id一覧を求め、その`entity_id`集合で`geo_report_embedding_cache`
  （`entity_type='representative'`）を引いてembeddingを取得、平均を都度計算して
  返す（DBには保存しない、常にrepresentative embeddingから再導出できることを
  優先する）。Semantic Census（Section H）のNN/Logistic Regression/Gradient
  Boosting用のクラスタ特徴量として使う。

## H. Semantic Census候補モデル（Phase 2、5方式すべて実装、field単位classifier）

**AC/SC分類は複数フィールドを同時に出すmulti-output classificationであり、
`label_json`全体を1つのclassとして扱うとclass数が組合せ爆発するため使わない。
Field単位で個別のclassifier/分類ロジックを持つ。** SC taskなら
`theme_relevance`/`source_class`/`reaction_type`/`attitude`/`cluster_consistency`/
`apply_to_cluster`それぞれに対して個別モデルを持ち、最終的に
(a) field毎のprecision/recall/F1と (b) 全field一致した「cluster-level exact match」
の両方を評価する（Section D `field_metrics_json`/`cluster_exact_match_rate`）。

**Semantic Census対象fieldとCurrent Prompt1の自由記述fieldを明示的に分離する registry
（新規、重要）**: `geo_report_semantic_census_service.py`冒頭に、以下4つの明示的な
定数リストを定義する:
```python
# 分類/カテゴリカル値のみ。Semantic Census(Phase 2)のBenchmark対象
SEMANTIC_CENSUS_FIELDS_AC = ["source_assessment", "tone_gradient", "voice_direction",
                              "report_use_status", "continuity_relevance", "noise_flag",
                              "representative_quality"]
SEMANTIC_CENSUS_FIELDS_SC = ["theme_relevance", "source_class", "reaction_type",
                              "attitude", "cluster_consistency", "apply_to_cluster"]
# 自由記述。Phase 2ではBenchmark対象外——Strong LLMのみが生成する
GENERATIVE_FIELDS_AC = ["final_topic_label", "change_signal_summary", "noise_reason",
                          "voice_summary", "notable_phrases", "notes_for_prompt2"]
GENERATIVE_FIELDS_SC = ["classification_reason"]
```
Phase 2のSemantic Census/Benchmark/Router SimulationはすべてSEMANTIC_CENSUS_FIELDS_*
だけを対象にする。**Lightweight（Small LLM/Embedding系）でAcceptedと判定された
クラスタについて、GENERATIVE_FIELDS_*（`classification_reason`/`voice_summary`/
`notable_phrases`/`notes_for_prompt2`等）をどう生成するかは今回未設計・未検証**
——Prompt1 9ファイル契約（Compatibility Contract）はこれら自由記述列を要求するため、
Phase 3でProduction Routingを実装する前に必ずこの設計・検証を行う。**それが解決する
までは、Lightweightの結果をCanonical Prompt1 Output（9ファイル）へ直接採用しない**
（Phase 2はあくまで「分類精度・削減余地」を測るBenchmarkであり、Production出力の
生成方式そのものはPhase 3の課題として明確に切り分ける）。

`geo_report_semantic_census_service.py`:
- **A. Smaller LLM（minimal schema）**: 既存`classify_analysis_clusters_batch`/
  `classify_sentiment_clusters_batch`の**フルスキーマをそのまま流用しない**——
  `classification_reason`/`voice_summary`/`notable_phrases`/`notes_for_prompt2`等の
  自由記述フィールドを含む重いスキーマだと「全件を軽く見る」という目的に反する。
  新規`_semantic_census_schema_from(full_schema)`（既存の`_batch_item_schema_from`
  パターンを踏襲）で、カテゴリカル/boolean値フィールドのみを残した最小スキーマを
  導出し、**各fieldごとに個別の`<field>_confidence`（0-1の数値、自己申告）を
  付加する**（単一の全体confidenceにはしない——field-wise calibration・
  min(field calibrated confidences)の計算にfield単位のconfidenceが必須のため）。
  例（SC）:
  ```json
  {"cluster_id": "...",
   "theme_relevance": "...", "theme_relevance_confidence": 0.94,
   "source_class": "...", "source_class_confidence": 0.88,
   "reaction_type": "...", "reaction_type_confidence": 0.81,
   "attitude": "...", "attitude_confidence": 0.90,
   "apply_to_cluster": true, "apply_to_cluster_confidence": 0.97,
   "cluster_consistency": "...", "cluster_consistency_confidence": 0.85}
  ```
  自由記述は一切生成しない。モデルは`gpt-4o-mini`または`gpt-4.1-mini`
  （新規依存なし、Azureに既存の安価deployment。`method_variant`列でどちらを
  使ったか区別する、Section D参照）。Strong LLMへescalateされたクラスタだけ、
  既存のフルスキーマ（`classification_reason`等含む）で再分類する。
- **B. Embedding NN**: Train split（Reference、Section I）のクラスタcentroid
  embedding（Section F/Gの`compute_cluster_centroid`）とfield毎のlabelを
  `scikit-learn.neighbors.KNeighborsClassifier`（cosine距離）でfield毎にindex化し、
  Validation/Test側で最近傍多数決。field毎confidenceは近傍のlabel一致率。
- **C. Embedding + Logistic Regression**: field毎に`scikit-learn.linear_model.
  LogisticRegression`（multinomial、必要ならOVR）。field毎confidenceは`predict_proba`。
- **D. Embedding + Gradient Boosting**: field毎に`scikit-learn.ensemble.
  GradientBoostingClassifier`（追加ライブラリ不要、xgboost/lightgbmは入れない
  ——sklearn内蔵で十分小規模データセットに対応できるため、依存を増やさない）。
- **E. Hybrid（定義を明確化、恣意的なweighted heuristicにしない）**: 「B/C/Dのうち
  良い方を人手のweightで混ぜる」という曖昧な設計はやめ、**Hybrid v1は単一の
  具体的なモデルとして定義する**: [cluster centroid embedding（Section F/Gの
  `compute_cluster_centroid`、384次元）] + [決定的な数値特徴量（Section Eの
  `fallback_share`/`source_entropy`/`language_entropy`/
  `preliminary_consistency_score`/`complexity_score`/
  `representative_diversity_score`の6個、**分類器入力に使ってよいと明示したもの
  限定**）] を**concatenateした1本の特徴ベクトル**を作り、それをfield毎の
  `LogisticRegression`または`GradientBoostingClassifier`（C/Dと同じsklearn実装、
  入力次元が違うだけ）へそのまま入力する。B/C/Dの予測結果を後から混ぜる
  Stacking/Votingは行わない。field毎confidenceはC/Dと同様`predict_proba`。
  **`narrative_priority_score`はHybridを含むいかなるSemantic Census分類器の入力
  特徴にも使わない**——「重要だからPublic Voiceになる」「重要だからnegativeになる」
  ということはなく、importanceは分類結果を変える情報ではないため。importanceは
  Section KのRouterでのみ、confidence/noveltyと組み合わせて「Strong LLMを使う
  価値があるか」の判断に使う（分類予測とimportance routingを分離する）。
- **Raw confidenceをそのままRouterへ渡さない（重要な修正）**: Small LLMの自己申告
  `confidence`もLogistic Regression/Gradient Boostingの`predict_proba`も、そのままでは
  「その確率で実際に正しい」ことを意味しない（未較正）。必ず
  ```text
  raw_confidence → Validation splitでCalibration → calibrated_confidence → Router
  ```
  という順に通す。Embedding系分類器は`sklearn.calibration`（isotonic、または
  `CalibratedClassifierCV(method='sigmoid')`＝Platt scaling）でfield毎に較正。
  Small LLMは自己申告confidenceをそのまま統計的較正の対象にはできないため、
  Validation split上でconfidenceを等頻度binに分け、bin毎の実際のReference Agreement率
  （Section J参照、Accuracyとは呼ばない）へのマッピングを作る「bin較正」で代替する。
  較正マッピング自体は`geo_report_semantic_census_benchmarks.calibration_json`
  （`split='validation'`の行）に保存し、Test split評価・Router Simulation（Section K）
  はこの較正済みマッピングをTest側のraw_confidenceに適用した
  `calibrated_confidence`だけを使う。生の`raw_confidence`はログ用に保存するのみで
  意思決定には使わない。
- **cluster全体のconfidence/uncertainty**は「全fieldの中で最も自信が無いもの」
  （min(field **calibrated** confidences)）をoverall confidenceとする（1つでも怪しい
  fieldがあればクラスタ全体をuncertain扱いにする、Routerの安全側判断）。
- **`unseen_reference_label`はBenchmark診断専用——Production Routerの条件には
  使わない（重要な訂正）**: 当初「Testのreference labelがTrainに無ければ強制
  escalate」という設計にしていたが、これは**推論時点では分からない正解ラベルを
  見て判定するoracle情報**であり、実運用のRouterには使えない（実運用では
  推論対象の正解が分かっていないため）。split_scheme毎・field毎にTrain内の
  reference label集合を記録し、Validation/Test exampleで`unseen_reference_label_fields`
  （Section D、列名変更）へ記録するのは**あくまでBenchmark側の診断**に限定する:
  「このfield×methodはTrainのclass coverageが不十分（Prompt1の閉じたenumの一部が
  Trainに1件も無い）ため、そのfieldに関してはclassifier単独ではproduction-eligible
  ではない」という所見を`field_metrics_json.production_eligible=false`として
  最終報告に出すために使う。**Production Router（実運用）が入力時点で観測できる
  情報だけを使う安全策は、代わりに明示的なnovelty gateとする**:
  `geo_report_semantic_census_predictions`（Section D）の`novelty_proxy_score`/
  `nearest_neighbor_distance`がValidation上で決めた閾値を超える場合、
  calibrated confidenceが高くても強制的にStrong LLMへescalateする
  （Section K）。これなら推論時点で観測可能な信号（embedding距離）だけで、
  「学習時に見たことのないパターンかもしれない」を検知できる。
- Targeted Stanceタスクはprecision最優先のためSection 14方針通りStrong LLM維持を
  基本とし、Phase 2ベンチマークはAC/SC taskを主対象とする（TSはA方式との比較のみ
  参考実施、B〜Eは今回スコープ外としてよい）。

新規pip依存: `scikit-learn`（軽量、xgboost/lightgboostは導入しない）。

## I. Train / Validation / Test Split（2種類、目的を分ける）

Section Cの制約により、真のRun-level splitは今回実施不可（Strong LLM分類済みRunが
1件のみ）。単一のグルーピング分割だけだと評価目的が混ざるため、**性質の異なる2種類の
Testを別々に構築し、両方の指標を報告する**。split割当は`geo_report_reference_examples`
にではなく、独立した`geo_report_benchmark_split_assignments`（Section D、
`benchmark_run_id`単位）へ保存する——Reference自体は不変のまま、同じReferenceに対して
複数のbenchmark_run_id・split_schemeで異なるsplit割当を何度でも作れるようにするため。

1. **Temporal Test**（「次の新しいデータに対応できるか」を見る）: `week_start`で
   古い週→Train、中間週→Validation、最新週→Test（`sklearn.model_selection`の
   時系列分割相当、シンプルにweek_startの昇順でカット）。
2. **Narrative Holdout Test**（「未知論点に対して誤って高confidenceを出さないか」
   ＝擬似OODを見る）: 一部の`narrative_id`をTrainから完全除外し、Testだけに出す
   （`sklearn.model_selection.GroupShuffleSplit`相当、narrative_id単位でグルーピング）。

比率はTrain 60% / Validation 20% / Test 20%を両方式共通の初期値とする。
**この1 Runには`baseline_run_id`が無くcontinuity/previous runが存在しないため、
Spotモード相当の評価に限定される——「Spot/Monitoring両方を評価した」とは言わない。**
Monitoring特有のcontinuity/drift featureはPhase 2の評価対象外とし、最終報告のRisksで
明記した上でPhase 3以降（複数時点のRunが揃ってから）の課題とする。

split確定と同時に、split_scheme毎・field毎のTrain側reference label集合
（`unseen_reference_label`診断の基礎、Section H）を計算・保存する。

**Train/Validation/Testの役割を厳密に分離する（重要）**:
- **Train**: モデルの重み・パラメータをfitするためだけに使う。
- **Validation**: (a) B〜Eのうちfield毎にどのmethodを採用するか、(b) 各分類器の
  hyperparameter、(c) confidence calibrationのfit（Section H）、(d) Router threshold
  cutoffの逆算（Section K）、(e) Hybridの加重、をすべてここで決定する。
- **Test**: 最終評価のみに使う。**Testの結果を見てmethod選択・threshold・重みを
  選び直してはいけない**——Validationで確定した設定をそのままTestへ適用し、
  出てきた数値をそのまま最終報告に使う。

**Leakage Guard（Leakage Group＝connected component単位で扱う、重要な訂正）**:
「record単位でsplit間の重複を検出したら寄せ直す」という素朴な方式は、あるクラスタが
複数レコードを含み、それぞれ別のクラスタとduplicate関係を持ちうる場合に矛盾する
（クラスタのReference Labelはクラスタ全体に対して1つ確定しているため、後から
レコード構成だけを操作できない）。代わりに:
1. `record_id`/`duplicate_group_id`/`url_hash`/`text_hash`（いずれも既存
   `geo_report_records`列、Phase 0監査で確認済み）のいずれかを共有するクラスタ同士を
   辺で結び、**Leakage Group（connected component）**を構築する（Union-Find等で
   計算）。
2. **Narrative Holdout split_scheme**: Leakage Group全体を必ず同一split
   （train/validation/testのいずれか）へ置く——1つのLeakage Groupが複数splitに
   跨ることを構造的に禁止する（グルーピング単位を`narrative_id`ではなく
   Leakage Group idにする）。
3. **Temporal split_scheme**: 時間境界（week_startのカット位置）を跨ぐLeakage Group
   （＝一部のクラスタがTrain側の週、一部がTest側の週に属してしまうGroup）は、
   その回のBenchmark評価対象から除外する（`excluded_due_to_temporal_leakage=true`、
   Section D）。**クラスタのReference Labelやレコード構成そのものは変更しない**
   ——「評価対象に含めるかどうか」だけを制御する。
4. 除外されたクラスタ数（`excluded_due_to_temporal_leakage`件数）を必ず最終報告へ
   明記する——多い場合はEmbedding系method（B〜E）の成績評価がその分小さいサンプルに
   基づくことになるため、件数の透明性を優先する。

## J. Benchmark実施計画

`geo_report_semantic_census_benchmark_service.py`:
- AC task・SC task別に、Method A〜E（Eはfield毎に最良の分類器を採用）を
  Temporal/Narrative Holdoutの両split_schemeそれぞれのValidation/Test splitで評価し、
  `geo_report_semantic_census_benchmarks`（`field_metrics_json`/
  `cluster_exact_match_rate`/`calibration_json`）/
  `geo_report_semantic_census_predictions`へ記録。
- 指標: field毎precision/recall/F1 (macro + per-class)、**cluster-level exact match
  rate**（全field一致したクラスタの割合）。Critical fields（`source_class`の
  Public Voice/Fact Only区別、`theme_relevance`、`attitude`、`apply_to_cluster`）は
  個別に精度を出す。confidence calibration（reliability diagram相当、bin別の
  raw confidence vs実際のReference Agreement率、較正前後の比較）。推論時間・
  相対コスト（Strong LLM callを1とした相対値）も記録。
- **class supportを必ず併記する（新規、少数クラス可視化）**: `field_metrics_json`へ
  train/validation/test split毎・field毎のクラス別件数（例: SC `source_class`の
  Mixed/Unknown/Fact Only等の少数クラスがTrainに何件、Testに何件あるか）を含める。
  Trainに1件も存在しないクラスがValidation/Test側に出現するfield×methodは
  `unseen_reference_label`診断（Section H、Benchmark専用でRouter条件には使わない）
  により`production_eligible=false`と明記し、その発生件数・発生fieldも最終報告で
  明示する。
- **用語の訂正（重要）**: Referenceデータ自体がCurrent Prompt1（Strong LLM）の出力で
  あるため、ここで計測できるのは独立したground truthとの一致＝「Accuracy」ではなく、
  **「Expected Reference Agreement」（Current Prompt1との一致度）**である。本Plan・
  実装コード・最終報告のすべてで「accuracy」ではなく「reference agreement」という
  語を一貫して使う（`field_metrics_json`のキー名も`precision`/`recall`/`f1`はそのまま
  統計用語として使うが、それらが比較している対象がReference Labelであることを
  明記する）。Strong LLM自体の正しさを検証する人手レビューは今回のスコープ外
  （Phase 3以降の課題として明記）。

## K. Router Simulation（Method固定にしない、calibrated confidenceのみ使用）

`geo_report_router_simulation_service.py`:
- **20%/30%/40%/50%/70%は「confidence閾値」ではなく「目標Strong LLM escalation率」
  である（重要な訂正）**: `calibrated_confidence >= 閾値`という単純なcutoffだと
  method間で実際のescalation率がバラバラになり公平な比較にならない。正しくは
  **Validation split上で「Strong LLMへX%回すには、calibrated confidenceの
  cutoffをいくつにすればよいか」を逆算し、そのcutoff値を固定してTest splitへ
  適用する**（`escalation_cutoffs_json`、Section D）。同じ目標escalation率
  （＝同じAI予算）でmethod間を比較できるようにするため。
- **Method A固定ではなく、A〜E（B〜Dはfield毎最良のもの、Eはhybrid）の
  それぞれについて独立にシミュレーションする**。**Mandatory escalation
  （novelty gate/importance）を先に適用し、残った集団に対してだけconfidence
  cutoffを解く**（新規、重要な訂正——先にconfidence cutoffだけで目標率を解くと、
  mandatory escalation分が上乗せされて実際のescalation率が目標を超過してしまう）:
  ```text
  for each candidate method in [small_llm, embedding_nn, logistic_regression,
                                 gradient_boosting, hybrid]:
      for each target_total_escalation_rate in [20%, 30%, 40%, 50%, 70%]:
          # Step 1(Validationのみ): まずmandatory escalation(novelty gate/
          # importance routing、confidenceに関係なく強制Strong LLM行き)を適用し、
          # このmandatory escalation率を求める
          mandatory_rate = rate(
              novelty_proxy_score > novelty_gate_threshold(validationで決定) or
              nearest_neighbor_distance > distance_gate_threshold(validationで決定) or
              (narrative_priority_score is High and importance_routing_enabled))

          if mandatory_rate >= target_total_escalation_rate:
              # mandatory escalationだけで目標率を超えてしまう場合、
              # confidence cutoffで調整する余地が無い
              → この(method, target_total_escalation_rate)の組み合わせは
                target_rate_infeasibleとして記録し、実際のescalation率
                (=mandatory_rate)とその時のExpected Reference Agreementのみ報告する
          else:
              # Step 2(Validationのみ): mandatory escalationの"残り"の集団(母集団全体
              # ではなく、mandatory対象を除いた残りの母集団であることに注意)に対して、
              # 合計escalation率がtarget_total_escalation_rateになるよう
              # calibrated_confidenceのcutoffを逆算する(確定後は固定、Testで再選択しない)。
              # 残り母集団に対する目標率 remaining_rate は単純な(t - m)ではなく、
              # 残り母集団のサイズが(1 - m)倍に縮小していることを補正した
              # (t - m) / (1 - m) を使う(t=target_total_escalation_rate、m=mandatory_rate)
              remaining_rate = (target_total_escalation_rate - mandatory_rate) / (1 - mandatory_rate)
              cutoff = solve_confidence_cutoff_for_target_rate(
                  validation_predictions_excluding_mandatory, remaining_rate)

              # Step 3(Test): 固定したmandatory条件+cutoffを適用
              for each test example:
                  if mandatory_escalate_condition(example):
                      → Strong LLM相当を採用(escalated, mandatory)
                  elif calibrated_confidence < cutoff:
                      → Strong LLM相当を採用(escalated, confidence-based)
                  else:
                      → candidate methodの予測をそのまま採用(non-escalated)

          → 実際の合計escalation率(target_total_escalation_rateとの差、
            mandatory分/confidence-based分の内訳も報告)、
            Strong LLM呼び出し数、candidate method呼び出し数、削減token数、
            推定wall-clock、推定costを算出。Expected Reference Agreementは
            下記のOracle/Realistic両方を算出する。
  ```
  **Routerが使うconfidenceは常にSection Hで較正済みの`calibrated_confidence`のみ**
  （raw_confidence・`unseen_reference_label`は使わない——後者はBenchmark診断専用、
  Section H参照）。
- **escalated分の扱いはOracle/Upper-boundであり、実際の一致率とは限らない
  （重要な訂正）**: これまでの設計は「escalated分＝Strong LLM＝Reference結果と
  100%一致する」という前提だったが、Section Fの3-way比較（historical vs
  current-rerun）で示した通り、Strong LLMを**再実行**した場合はLLMの非決定性・
  deployment差分により historical reference と100%一致するとは限らない。
  したがって最終報告には次の2種類を両方出す:
  1. **Oracle / Upper-bound Expected Reference Agreement**（今の設計のまま、
     escalated分は100%一致と仮定した理論上限値。「Router自体の判断が完璧なら
     ここまで到達しうる」という上限を示す指標として明記する）。
  2. **Realistic Expected Reference Agreement**（可能な場合）: Section Fの
     Representative Benchmarkで実測した`historical reference vs
     current-reservoir-rerun`の一致率を「Strong LLM再実行時の期待一致率」として
     escalated分に掛け合わせた、より現実的な推定値。Section Fのサンプル規模
     （100〜200クラスタ）に基づく推定であることも明記する。
  両者を並べて示すことで、「Router判断の質」と「Strong LLM自体の再実行時の
  ブレ」を混同しない。可能ならこのRealistic Expected Reference Agreementは
  全クラスタ一律の一致率ではなく、escalated subset（低confidence/novelty高/
  importance高のクラスタ）または難易度別（例: `preliminary_consistency_score`の
  高低）に分けて推定する——「簡単なクラスタの再実行一致率」と「難しい/曖昧な
  クラスタの再実行一致率」は異なりうるため、全体平均を機械的に当てはめない。
- **LLM呼び出し数・token数・wall-clock推定はクラスタ数の単純比例計算をしない
  （重要な訂正）**: 「escalated cluster数×平均token数」のような素朴な比例計算は
  実際のバッチ処理条件（`MAX_CLUSTERS_PER_BATCH`=20、token上限、
  `MAX_CONCURRENT_BATCHES`並列数、Section G-Iの実データBenchmark実測値）を反映
  しない。代わりに、**Production Strong Engineが実際に使っているのと同じ
  token-aware batch packer**（`geo_report_prompt1_orchestration_service.
  _pack_token_aware_batches`と同じロジック）を、escalatedと判定されたクラスタ
  集合にそのまま適用し、実際に生成されるbatch数・API call数・推定input/output
  token数・推定wall-clock（concurrency条件込み）を算出する。Small LLM
  （method A）呼び出し分も同じbatch packerロジックで算出する（Small LLMは
  minimal schemaのため1 batch当たりのtoken数はStrong LLMより少ないことを反映
  する）。Embedding系method（B〜D、Hybrid含む）はAPI呼び出し自体が発生しない
  （`API calls = 0`、embedding計算はSection Gで既に完了済みのcacheを再利用する
  想定）。
- importance routingは「confidence＋novelty gate（mandatory）のみ」と
  「confidence＋novelty gate＋importance（mandatory）」の2パターンで比較し、
  `narrative_priority_score`を組み込むことでExpected Reference Agreement/
  削減率がどう変わるかを示す。
- 最終報告では、method×target_total_escalation_rate×routingパターン
  （importance有無）の全組み合わせを一覧表として提示し、実際のescalation率
  （mandatory/confidence-based内訳・infeasible判定含む）とOracle/Realistic両方の
  Expected Reference Agreementとともに「どのmethod＋Routerの組み合わせが、
  目標のStrong LLM呼び出し削減率を実現しつつExpected Reference Agreement
  （特にCritical fieldのRecall/Precision）を維持できるか」を結論として示す
  （Section 32/34/37の最終的な問いに直接答える形にする）。

## L. 実装順序（今回のスコープ、Phase 0-2）

1. SQL migration（Section D、7テーブル、additive）
2. `geo_report_reference_dataset_service.py`（Section C、Referenceはimmutable、
   splitは持たせない）
3. `geo_report_cluster_feature_service.py`（Section E、v1 no-embedding、
   `narrative_priority_score`含む。novelty/prediction_entropy系はここに置かない）
4. `requirements-benchmark.txt`作成＋`pip install -r requirements-benchmark.txt`
   （sentence-transformers/scikit-learn、ユーザー承認済み、プロキシ経由、
   Production主要依存とは隔離）
5. `geo_report_embedding_benchmark_service.py`（Section G、representative単位embedding、
   Content Cache設計＝entity_type/entity_id/model/version/hashのみ、
   遅延import、＋9原則を冒頭コメントに明記）
6. `geo_report_representative_strategy_service.py`（Section F、Benchmark対象クラスタの
   全recordをrecord-level embeddingし候補母集団とする＋修正後のBoundary定義＋
   historical/current-rerun/new-rerunの3-way比較）
7. `geo_report_semantic_census_service.py`（Section H、SEMANTIC_CENSUS_FIELDS_*/
   GENERATIVE_FIELDS_*のregistry定義、field単位classifier・field毎confidence、
   minimal schemaのA方式、Hybrid v1=centroid+数値特徴concatenate、
   raw→calibrated confidence較正、`unseen_reference_label`はBenchmark診断のみ、
   narrative_priority_scoreは分類器入力から除外）
8. `geo_report_benchmark_split_service.py`（Section I、新規——split割当を
   `geo_report_benchmark_split_assignments`へ書く専用サービス。Leakage Group
   （connected component）構築、Temporal/Narrative Holdoutそれぞれの割当ロジック、
   Train=fit/Validation=選定/Test=最終評価の厳密分離を徹底）
9. `geo_report_semantic_census_benchmark_service.py`（Section J、class support併記、
   field-wise + cluster exact match）
10. `geo_report_router_simulation_service.py`（Section K、mandatory escalation
    優先適用→残り母集団への(t-m)/(1-m)でのcutoff逆算、token-aware batch packerに
    よる現実的なcall/token/time推定、Oracle/Realistic双方のExpected Reference
    Agreement、A〜E各methodについて独立シミュレーション）
11. 各サービスのテスト（既存`FakeSupabaseClient`パターンを踏襲）
12. 最終報告書 `docs/report-studio/2026-09-13_intelligence_platform_phase0-2/SUMMARY.md`
    （既存の`2026-09-12_prompt1_runlevel_job_poc/SUMMARY.md`と同じ形式）——
    Section 35の20項目（Audit/Keep-Change-Replace/Architecture/DB/Reference Dataset/
    Feature Dataset/Representative Benchmark(3-way)/Models/Model Benchmark
    (field-wise + cluster exact match)/Critical-field Metrics/Novelty-proxy
    Performance/Calibration/Router Simulation(Expected Reference Agreement,
    target escalation rate方式)/推定Strong LLM削減率/token削減率/時間削減率/
    Risks/推奨Architecture/Phase 3計画/Go-No-Go）を網羅する。
13. 本節（「# Report Studio → 汎用Intelligence Analysis Platform 再構築」章全体）を
    独立ファイル`docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md`へ切り出し、
    以後の実装はそちらを一次参照とする（本Planファイル冒頭の注記の通り）。

Production Pipeline（`geo_report_prompt1_orchestration_service.py`他、既存ファイル）は
今回一切変更しない——全て新規ファイル・新規テーブルの追加のみ。

## 検証方法
- 新規サービス各々に`FakeSupabaseClient`ベースのユニットテストを追加し、
  既存`pytest tests/`全件（現在1168件）が引き続きpassすることを確認する。
- Reference Dataset構築・Feature計算・Embedding生成（representative単位）・
  Benchmark実行は、実データ（`run_id=d558df7e`, `execution_id=6e27804c`）に対して
  実際に実行し、`geo_report_semantic_census_benchmarks`に実測値が入ることを確認する
  （机上のArchitecture文書だけで終わらせない、ユーザー要件37）。
- Representative Benchmark（Section F）の不一致サブセット（20〜30件）レビュー結果も
  最終報告へ含める。
- 最終報告書をユーザーへ提示し、Phase 3着手のGo/No-Go判断を仰ぐ。

## 既知のリスク（今回時点で明記）
- Reference DatasetがRun 1件のみに基づく（Section C/I）——真のRun-level split未実施。
  Temporal/Narrative Holdoutの2種類のsplitで補完するが、根本解決には複数Run蓄積が必要。
- この1 RunはSpotモード相当（continuity無し）——Monitoringモード特有のfeatureは
  Phase 2で検証できない。
- Reference Label自体がStrong LLM出力であり独立ground truthではない——本Plan・
  実装・報告のすべてで「Accuracy」ではなく「Expected Reference Agreement」と呼ぶ
  （Section J/K）。
- Representative Benchmark（Section F）はReference自体がcurrent_reservoirベースで
  作られているため構造的にcurrent方式に有利——historical/current-rerun/new-rerunの
  3-way比較とcurrent-rerun対new-rerunの不一致サブセットの個別レビューで補う。
- Local embeddingでの結論はAzure Embeddings採用可否の直接的な保証にはならない
  （ユーザー指示6/7の通り、将来Azure導入時は必ず再Benchmark。local embeddingで
  学習したclassifierをAzure embeddingへそのまま流用しない）。
- Targeted StanceはB〜E方式の本格評価対象外（Precision最優先の既存方針を尊重）。
- `novelty_proxy_score`/`nearest_neighbor_distance`は複数Run・複数テーマが揃うまで
  「OOD」と呼ばない（Section E/G）。
- **Confidence Calibration自体も1 Runのみのデータで学習する**ため、較正マッピングの
  汎化性（別Run/別テーマでも同様に機能するか）はPhase 2単体では検証できない——
  Phase 3以降、複数Runが揃った時点でCalibrationも再検証が必要（Router Simulationの
  数値はあくまで「この1 Run内でのcalibrated confidenceに基づく試算」であることを
  最終報告で明記する）。
- Representative Selection Benchmark（Section F）のrecord-level embeddingは
  Benchmark対象クラスタ（100〜200件）のみが対象——全Run全recordのembeddingは
  行わない。この母集団のサンプリング方法自体も最終報告で明記する。
- `unseen_reference_label`はBenchmark診断専用（「このfield×methodはTrainの
  class coverageが不十分でproduction-eligibleではない」という所見を出すためだけに
  使う）であり、Production Routerの意思決定には使わない——実運用の安全策は
  novelty gate（`novelty_proxy_score`/`nearest_neighbor_distance`、推論時点で
  観測可能な情報のみ）で代替する（Section H/K）。novelty gateの閾値も今回の
  1 Run内のValidationでのみ決定されるため、実運用（新規Runでの真に未知の
  クラス値）に対する有効性はPhase 3以降の実データで再検証が必要。
  「Trainに1件もクラスが無い」ことが判明したfieldは、Semantic Census単体では
  Production非採用（Strong LLM必須）とし、その旨を推奨Architecture（最終報告）へ
  明記する。
- Train/Validation/Testの厳密分離（Section I）とLeakage Guardを導入したが、
  この1 Run内でのnear-duplicate検出（`duplicate_group_id`/`url_hash`/`text_hash`
  ベース）が実際にどの程度の件数に影響するかは実装・実行してみないと分からない
  ——検出件数が多い場合はEmbedding系methodの成績を割り引いて解釈する必要がある。
