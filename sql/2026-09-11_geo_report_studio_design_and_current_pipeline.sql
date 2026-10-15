-- =====================================================================
-- Report Studio「Design」領域 + Current Prompt1/Prompt2パイプライン移植（P0）
-- 作成日: 2026-09-11
-- 対象: Supabase（PostgreSQL）。新規構築、既存テーブルへのALTERを一部含む。
-- 根拠: C:\Users\269811\.claude\plans\kind-napping-hare.md（Plan Mode承認済み）
--
-- 設計方針の要点（Planより）:
--   * cluster_id（AC_*/SC_*）はrun.ps1実測によりRunを跨いでグローバル一意ではない
--     （ハッシュにrun_id相当の要素を含まないため）。既存geo_report_clusters/
--     geo_report_cluster_representativesもcluster_id単体FKを持たず(run_id, cluster_id)
--     の緩い組で扱っており、本ファイルの新規テーブルも同じ方針を踏襲する
--     （cluster_id単体へのFKは張らない）。
--   * Prompt1の分類結果はCluster本体（Preprocessの不変事実、既存
--     geo_report_clusters/新規geo_report_sentiment_clusters）から分離し、
--     Execution単位（geo_report_executions）で複数保持できるようにする。
--     既存geo_report_clusters.classification列は物理削除しない
--     （レガシー参照が残る可能性があるため。新規コードは読み書きしない）。
--   * Prompt1→Aggregation→Prompt2→Report Editの依存関係をgeo_report_executions
--     で一本のチェーンとして追跡する。ChatGPT Prompt2はPython Aggregationを
--     経由しない別内部経路（Prompt2 Base Input Artifactを直接消費）だが、
--     Canonical Prompt2 Output契約（10ファイル）で合流する。
--   * 大容量成果物（record_cluster_map, 代表投稿CSV, ZIP, final_report.html等）は
--     Supabase Storageへ置き、DBにはstorage_path/sha256のみを持たせる。
--
-- 作成順: geo_report_project_assets → geo_report_design_state
--        → geo_report_design_versions → geo_report_design_chat_turns
--        → geo_report_publish_design(RPC)
--        → geo_report_executions（Artifactsと相互FKのため後段でALTERも使う）
--        → geo_report_sentiment_clusters → geo_report_sentiment_cluster_representatives
--        → geo_report_analysis_cluster_classifications
--        → geo_report_sentiment_cluster_classifications → geo_report_targeted_stance
--        → geo_report_run_config → geo_report_run_artifacts
--        → geo_report_execution_batches → geo_report_execution_log
--        → geo_report_collection_runs ALTER → geo_report_records ALTER
-- =====================================================================


-- ---- 1. geo_report_project_assets（Narrative Master / Report Template、版管理あり） ----

create table public.geo_report_project_assets (
    id             uuid primary key default gen_random_uuid(),
    project_id     uuid not null references public.geo_report_projects(project_id) on delete cascade,
    asset_type     text not null check (asset_type in ('narrative_master', 'report_template')),
    status         text not null default 'draft' check (status in ('draft', 'published', 'superseded')),
    version        integer check (version >= 1),
    supersedes_id  uuid references public.geo_report_project_assets(id) on delete set null,
    content        jsonb not null,
    content_format text not null default 'v1',
    source         text not null default 'manual'
        check (source in ('chat_ai', 'csv_upload', 'html_upload', 'manual', 'seed', 'template_extraction')),
    row_count      integer,
    published_at   timestamptz,
    published_by   text,
    created_by     text not null default 'analyst',
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

-- Draft行は正式version番号を持たない（version列はNULL可）。Publishedのみversion必須。
create unique index geo_report_project_assets_project_type_version_idx
    on public.geo_report_project_assets(project_id, asset_type, version) where version is not null;
create unique index geo_report_project_assets_one_published_idx
    on public.geo_report_project_assets(project_id, asset_type) where status = 'published';
create unique index geo_report_project_assets_one_draft_idx
    on public.geo_report_project_assets(project_id, asset_type) where status = 'draft';
create index geo_report_project_assets_project_idx
    on public.geo_report_project_assets(project_id, asset_type, status);

alter table public.geo_report_project_assets enable row level security;

create trigger geo_report_project_assets_set_updated_at
    before update on public.geo_report_project_assets
    for each row execute function public.set_updated_at();

comment on table public.geo_report_project_assets is
    'Narrative Master / Report TemplateのDraft/Published/History。Publishedのみversion番号を'
    '持つ不変スナップショット（差分が無ければ選択的バージョニングでversionを増やさない、'
    'geo_report_publish_design参照）。Report Templateのcontentはraw HTMLではなくI.1の'
    '構造化Section定義（style_css/shell_html/sections[]）を持つjsonb';


-- ---- 2. geo_report_design_state（Report Design / Signals / Queries、Draft・可変） ----

create table public.geo_report_design_state (
    project_id     uuid primary key references public.geo_report_projects(project_id) on delete cascade,
    report_design  jsonb not null default '{}'::jsonb,
    signals        jsonb not null default '[]'::jsonb,
    queries        jsonb not null default '[]'::jsonb,
    updated_at     timestamptz not null default now(),
    updated_by     text
);

create trigger geo_report_design_state_set_updated_at
    before update on public.geo_report_design_state
    for each row execute function public.set_updated_at();

alter table public.geo_report_design_state enable row level security;

comment on table public.geo_report_design_state is
    'Design Chatが対話で埋める作業中の設計（1 project 1行、常に可変）。queriesは'
    'bs_query_plan.csv契約と完全一致するフィールド名で持つ（signal_id, report_topic_label,'
    'fixed_query_group_a, fixed_query_group_b, event_terms, exclude_terms, continuity_role,'
    'collection_notes）。geography/languageはreport_design側（収集スコープ）に持つ';


-- ---- 3. geo_report_design_versions（Design全体のPublished不変スナップショット） ----

create table public.geo_report_design_versions (
    id                          uuid primary key default gen_random_uuid(),
    project_id                  uuid not null references public.geo_report_projects(project_id) on delete cascade,
    version                     integer not null check (version >= 1),
    report_design               jsonb not null,
    signals                     jsonb not null,
    queries                     jsonb not null,
    narrative_master_asset_id   uuid not null references public.geo_report_project_assets(id),
    report_template_asset_id    uuid not null references public.geo_report_project_assets(id),
    expert_adapter_type         text not null default 'geo',
    expert_knowledge_version    text not null default 'geo_expert_knowledge_v1',
    published_at                timestamptz not null default now(),
    published_by                text not null default 'analyst'
);

create unique index geo_report_design_versions_project_version_idx
    on public.geo_report_design_versions(project_id, version);
create index geo_report_design_versions_project_idx
    on public.geo_report_design_versions(project_id, published_at desc);

alter table public.geo_report_design_versions enable row level security;

comment on table public.geo_report_design_versions is
    'Design PublishのAtomicスナップショット。narrative_master_asset_id/report_template_asset_idは'
    '内容が変わっていなければ既存Published行を再利用する（選択的バージョニング、'
    'geo_report_publish_design参照）';


-- ---- 4. geo_report_design_chat_turns（Design Chat会話の永続化） ----

create table public.geo_report_design_chat_turns (
    id            uuid primary key default gen_random_uuid(),
    project_id    uuid not null references public.geo_report_projects(project_id) on delete cascade,
    turn_no       integer not null,
    role          text not null check (role in ('user', 'assistant')),
    content       text not null,
    applied_diff  jsonb,
    llm_usage     jsonb,
    latency_ms    integer,
    created_at    timestamptz not null default now()
);

create unique index geo_report_design_chat_turns_project_turn_idx
    on public.geo_report_design_chat_turns(project_id, turn_no);
create index geo_report_design_chat_turns_project_idx
    on public.geo_report_design_chat_turns(project_id, created_at);

alter table public.geo_report_design_chat_turns enable row level security;


-- ---- 5. geo_report_publish_design（Atomic Publish RPC、選択的バージョニング） ----

create or replace function public.geo_report_publish_design(
    p_project_id uuid,
    p_actor_id text,
    p_narrative_master_content jsonb,
    p_report_template_content jsonb,
    p_report_design jsonb,
    p_signals jsonb,
    p_queries jsonb,
    p_expert_adapter_type text,
    p_expert_knowledge_version text
) returns public.geo_report_design_versions
language plpgsql
as $$
declare
    v_existing_published public.geo_report_project_assets;
    v_new_asset           public.geo_report_project_assets;
    v_narrative_asset_id   uuid;
    v_template_asset_id    uuid;
    v_next_version         integer;
    v_result                public.geo_report_design_versions;
begin
    -- Narrative Master: 内容が変わっていなければ既存publishedを再利用、変わっていれば新version
    select * into v_existing_published from public.geo_report_project_assets
        where project_id = p_project_id and asset_type = 'narrative_master' and status = 'published';
    if v_existing_published.id is not null and v_existing_published.content = p_narrative_master_content then
        v_narrative_asset_id := v_existing_published.id;
    else
        if v_existing_published.id is not null then
            update public.geo_report_project_assets set status = 'superseded' where id = v_existing_published.id;
        end if;
        select coalesce(max(version), 0) + 1 into v_next_version
            from public.geo_report_project_assets
            where project_id = p_project_id and asset_type = 'narrative_master' and version is not null;
        insert into public.geo_report_project_assets
            (project_id, asset_type, status, version, supersedes_id, content, source, published_at, published_by)
            values (p_project_id, 'narrative_master', 'published', v_next_version, v_existing_published.id,
                    p_narrative_master_content, 'chat_ai', now(), p_actor_id)
            returning * into v_new_asset;
        v_narrative_asset_id := v_new_asset.id;
    end if;

    -- Report Template: 同様のロジック
    select * into v_existing_published from public.geo_report_project_assets
        where project_id = p_project_id and asset_type = 'report_template' and status = 'published';
    if v_existing_published.id is not null and v_existing_published.content = p_report_template_content then
        v_template_asset_id := v_existing_published.id;
    else
        if v_existing_published.id is not null then
            update public.geo_report_project_assets set status = 'superseded' where id = v_existing_published.id;
        end if;
        select coalesce(max(version), 0) + 1 into v_next_version
            from public.geo_report_project_assets
            where project_id = p_project_id and asset_type = 'report_template' and version is not null;
        insert into public.geo_report_project_assets
            (project_id, asset_type, status, version, supersedes_id, content, source, published_at, published_by)
            values (p_project_id, 'report_template', 'published', v_next_version, v_existing_published.id,
                    p_report_template_content, 'chat_ai', now(), p_actor_id)
            returning * into v_new_asset;
        v_template_asset_id := v_new_asset.id;
    end if;

    -- Working Draft行を1件確保（version=NULL固定。無ければ作る、既存draftがあれば内容だけ更新）
    if exists (select 1 from public.geo_report_project_assets
               where project_id = p_project_id and asset_type = 'narrative_master' and status = 'draft') then
        update public.geo_report_project_assets set content = p_narrative_master_content, updated_at = now()
            where project_id = p_project_id and asset_type = 'narrative_master' and status = 'draft';
    else
        insert into public.geo_report_project_assets (project_id, asset_type, status, content, source)
            values (p_project_id, 'narrative_master', 'draft', p_narrative_master_content, 'chat_ai');
    end if;
    if exists (select 1 from public.geo_report_project_assets
               where project_id = p_project_id and asset_type = 'report_template' and status = 'draft') then
        update public.geo_report_project_assets set content = p_report_template_content, updated_at = now()
            where project_id = p_project_id and asset_type = 'report_template' and status = 'draft';
    else
        insert into public.geo_report_project_assets (project_id, asset_type, status, content, source)
            values (p_project_id, 'report_template', 'draft', p_report_template_content, 'chat_ai');
    end if;

    -- Design Version snapshot（Report Design/Signals/Queriesに変更が無くても、Assetが変わっていれば新version）
    select coalesce(max(version), 0) + 1 into v_next_version
        from public.geo_report_design_versions where project_id = p_project_id;
    insert into public.geo_report_design_versions (
        project_id, version, report_design, signals, queries,
        narrative_master_asset_id, report_template_asset_id,
        expert_adapter_type, expert_knowledge_version, published_by
    ) values (
        p_project_id, v_next_version, p_report_design, p_signals, p_queries,
        v_narrative_asset_id, v_template_asset_id,
        p_expert_adapter_type, p_expert_knowledge_version, p_actor_id
    ) returning * into v_result;

    return v_result;
end;
$$;

comment on function public.geo_report_publish_design is
    '無変更Publish拒否（Narrative/Template/Report Design/Signals/Queries全てが前回Publishedと同一の'
    '場合）はPython側で事前チェックする。関数内で例外が起きればPostgresの暗黙トランザクション境界で'
    '自動ロールバックされ、部分的なPublishは残らない';


-- ---- 6. geo_report_executions（Provenance Chainの中核） ----

create table public.geo_report_executions (
    id                                    uuid primary key default gen_random_uuid(),
    run_id                                uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    stage                                 text not null
        check (stage in ('preprocess', 'prompt1', 'aggregation', 'prompt2', 'report_edit')),
    execution_method                      text not null
        check (execution_method in ('application_ai', 'chatgpt_import', 'python')),
    status                                text not null default 'pending'
        check (status in ('pending', 'running', 'completed', 'failed', 'stale')),
    prompt_contract_version               text,
    input_collection_artifact_id          uuid,  -- FK追加は後段のALTER（run_artifactsとの相互FKのため）
    input_preprocess_execution_id         uuid references public.geo_report_executions(id),
    input_prompt1_execution_id            uuid references public.geo_report_executions(id),
    input_aggregation_execution_id        uuid references public.geo_report_executions(id),
    input_prompt2_base_input_artifact_id  uuid,  -- FK追加は後段のALTER
    input_continuity_artifact_id          uuid,  -- FK追加は後段のALTER
    parent_execution_id                   uuid references public.geo_report_executions(id),
    started_at                            timestamptz,
    completed_at                          timestamptz,
    created_at                            timestamptz not null default now()
);

create index geo_report_executions_run_stage_idx on public.geo_report_executions(run_id, stage, status);

alter table public.geo_report_executions enable row level security;

comment on table public.geo_report_executions is
    '依存チェーン: Collection Input Artifact → Preprocess(input_collection_artifact_id) → '
    'Prompt1(input_preprocess_execution_id) → [Application AI: Aggregation('
    'input_prompt1_execution_id) → Prompt2(input_aggregation_execution_id)] または '
    '[ChatGPT: Prompt2(input_prompt1_execution_id + input_prompt2_base_input_artifact_id、'
    'input_aggregation_execution_id=NULL)] → Report Edit(parent_execution_id)。'
    'stale伝播はアプリ層のpropagate_staleness()で行う（DB triggerにしない、P0はシンプルに保つ）。'
    'same-run/same-stage整合性もアプリ層のvalidate_execution_reference()で検証する（FKだけでは'
    '別Runのexecution_idや誤stageの参照を防げないため）';


-- ---- 7. geo_report_sentiment_clusters（Sentiment Cluster、Preprocessの不変事実のみ） ----

create table public.geo_report_sentiment_clusters (
    id                       uuid primary key default gen_random_uuid(),
    run_id                   uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    sentiment_cluster_id     text not null,
    signal_id                text not null,
    narrative_id             text not null,
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

create unique index geo_report_sentiment_clusters_run_cluster_idx
    on public.geo_report_sentiment_clusters(run_id, sentiment_cluster_id);
create index geo_report_sentiment_clusters_run_idx on public.geo_report_sentiment_clusters(run_id);

alter table public.geo_report_sentiment_clusters enable row level security;

comment on table public.geo_report_sentiment_clusters is
    'sentiment_cluster_labeling_input.csv相当（Prompt1 Current Input）。cluster_labeling_input.csvと'
    '同一33列スキーマだが別クラスタ体系（クラスタリングキーがsource_class_preliminary/'
    'reaction_type_hint/attitude_hintを追加で含む、run.ps1実測で確定）。Preprocessの不変事実のみ、'
    '分類結果はgeo_report_sentiment_cluster_classificationsへExecution-scopedで持つ。'
    'sentiment_cluster_idはrun_idと組でのみ一意（グローバル一意ではない、run.ps1実測で確定）';


-- ---- 8. geo_report_sentiment_cluster_representatives ----

create table public.geo_report_sentiment_cluster_representatives (
    id                        uuid primary key default gen_random_uuid(),
    run_id                    uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    sentiment_cluster_id      text not null,
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

create index geo_report_sentiment_cluster_representatives_run_cluster_idx
    on public.geo_report_sentiment_cluster_representatives(run_id, sentiment_cluster_id);

alter table public.geo_report_sentiment_cluster_representatives enable row level security;

comment on table public.geo_report_sentiment_cluster_representatives is
    'sentiment_cluster_representatives.csv相当。既存geo_report_cluster_representativesは'
    'cluster_id自体にFK制約が無く技術的には共用も可能だが、AC/SCの問い合わせを明確に分離する'
    'ための意図的な別テーブル（Phase A監査で確認済み、FK制約による強制ではない）';


-- ---- 9. geo_report_analysis_cluster_classifications（Execution-scoped、Prompt1 §3） ----

create table public.geo_report_analysis_cluster_classifications (
    id                    uuid primary key default gen_random_uuid(),
    execution_id          uuid not null references public.geo_report_executions(id) on delete cascade,
    run_id                uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    cluster_id            text not null,
    source_assessment     text,
    tone_gradient         text,
    voice_direction       text,
    report_use_status     text,
    continuity_relevance  text,
    matched_previous_topic_id text,
    change_signal_summary text,
    noise_flag            boolean,
    noise_reason          text,
    voice_summary         text,
    notable_phrases       text,
    representative_quality text,
    notes_for_prompt2     text,
    created_at            timestamptz not null default now()
);

create unique index geo_report_analysis_cluster_classifications_exec_cluster_idx
    on public.geo_report_analysis_cluster_classifications(execution_id, cluster_id);
create index geo_report_analysis_cluster_classifications_run_idx
    on public.geo_report_analysis_cluster_classifications(run_id, cluster_id);

alter table public.geo_report_analysis_cluster_classifications enable row level security;

comment on table public.geo_report_analysis_cluster_classifications is
    'Prompt1 §3の分類結果（classified_clusters.csv相当）。cluster_idはrun_idとの組でのみ意味を持つ'
    '（geo_report_clustersと同じくグローバル一意ではない、単体FKは張らない）。Idempotency: '
    'unique(execution_id, cluster_id)でBatch Retry時の二重書きを防ぐ（upsert前提）';


-- ---- 10. geo_report_sentiment_cluster_classifications（Execution-scoped、Prompt1 §4） ----

create table public.geo_report_sentiment_cluster_classifications (
    id                      uuid primary key default gen_random_uuid(),
    execution_id            uuid not null references public.geo_report_executions(id) on delete cascade,
    run_id                  uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    sentiment_cluster_id    text not null,
    theme_relevance         text check (theme_relevance in ('relevant', 'irrelevant', 'uncertain')),
    source_class            text,
    reaction_type           text,
    attitude                text check (attitude in ('positive', 'neutral', 'negative', 'mixed', 'not_applicable', 'unknown')),
    cluster_consistency     text check (cluster_consistency in ('High', 'Medium', 'Low')),
    apply_to_cluster        boolean,
    exception_pattern       text,
    classification_reason   text,
    targeted_stance_status  text check (targeted_stance_status in ('available', 'unavailable', 'ambiguous', 'not_applicable')),
    targeted_stance_count   integer not null default 0,
    created_at              timestamptz not null default now()
);

create unique index geo_report_sentiment_cluster_classifications_exec_cluster_idx
    on public.geo_report_sentiment_cluster_classifications(execution_id, sentiment_cluster_id);
create index geo_report_sentiment_cluster_classifications_run_idx
    on public.geo_report_sentiment_cluster_classifications(run_id, sentiment_cluster_id);

alter table public.geo_report_sentiment_cluster_classifications enable row level security;

comment on table public.geo_report_sentiment_cluster_classifications is
    'Prompt1 §4の11列分類結果（sentiment_cluster_classified.csv相当）。theme_relevance=relevant AND '
    'source_class=Public Voice AND apply_to_cluster=true AND attitude in (positive,neutral,negative) が '
    'G.1の3-way分母フィルタの核。Idempotency: unique(execution_id, sentiment_cluster_id)';


-- ---- 11. geo_report_targeted_stance（Execution-scoped、Prompt1 §4.5。Prompt2へは接続しない） ----

create table public.geo_report_targeted_stance (
    id                          uuid primary key default gen_random_uuid(),
    execution_id                uuid not null references public.geo_report_executions(id) on delete cascade,
    run_id                      uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    sentiment_cluster_id        text not null,
    signal_id                   text,
    target_type                 text,
    target_name                 text,
    target_normalized_id        text,
    target_key                  text generated always as (coalesce(target_normalized_id, target_name)) stored,
    stance_polarity              text check (stance_polarity in ('favorable', 'unfavorable', 'neutral', 'mixed', 'unclear')),
    stance_strength               text check (stance_strength in ('strong', 'moderate', 'weak', 'not_applicable')),
    stance_confidence              text check (stance_confidence in ('high', 'medium', 'low')),
    stance_evidence                 text,
    stance_basis                     text,
    stance_cluster_consistency        text check (stance_cluster_consistency in ('High', 'Medium', 'Low')),
    apply_stance_to_cluster            boolean,
    stance_exception_pattern            text,
    stance_reason                        text,
    created_at                             timestamptz not null default now(),
    constraint geo_report_targeted_stance_target_key_not_null check (target_key is not null)
);

create unique index geo_report_targeted_stance_exec_cluster_target_idx
    on public.geo_report_targeted_stance(execution_id, sentiment_cluster_id, target_type, target_key);
create index geo_report_targeted_stance_run_idx on public.geo_report_targeted_stance(run_id, sentiment_cluster_id);

alter table public.geo_report_targeted_stance enable row level security;

comment on table public.geo_report_targeted_stance is
    'Prompt1 §4.5 Targeted Stance Layer（targeted_stance_classified.csv相当）。Current Prompt2 v4.3の'
    '入力ファイル一覧にはまだ含まれない（G.3参照）——Prompt1側のCanonical Output契約としてはP0に'
    '含むが、Prompt2 Composition/denominatorへは接続しない（Candidate Favorability集計は将来の'
    'v4.4-targeted-stance-aware拡張）。target_normalized_idはNULLになり得るため、生成列target_key '
    '（coalesce(target_normalized_id, target_name)、NOT NULL）でIdempotency unique制約を張る';


-- ---- 12. geo_report_run_config（report_config.yaml相当、Snapshot方式） ----

create table public.geo_report_run_config (
    run_id                     uuid primary key references public.geo_report_collection_runs(run_id) on delete cascade,
    config_json                jsonb not null,
    primary_graph_signals      text[],
    common_primary_graph_start date,
    created_at                 timestamptz not null default now()
);

alter table public.geo_report_run_config enable row level security;

comment on table public.geo_report_run_config is
    'report_config.yaml相当。config_jsonがSource of Truth（Run作成時にPublished Designから1回だけ'
    '生成し以後不変）。primary_graph_signals/common_primary_graph_startは検索・高速処理用の派生列'
    '（config_jsonと同時に書き込む）。現行run.ps1ではprimary_graph_signals/final_graph_ruleを'
    'この案件専用にハードコードしているが（S1,S7固定）、Report Studioではproject毎に設定可能にする'
    '（Phase A監査結果1参照、矛盾ではなく妥当な一般化）';


-- ---- 13. geo_report_run_artifacts（大容量成果物のStorage化＋Runスナップショット） ----

create table public.geo_report_run_artifacts (
    id                uuid primary key default gen_random_uuid(),
    run_id            uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    execution_id      uuid references public.geo_report_executions(id),  -- system_snapshot系はNULL
    execution_method  text not null
        check (execution_method in ('application_ai', 'chatgpt_import', 'python', 'system_snapshot')),
    artifact_type     text not null check (artifact_type in (
        'collection_input', 'preprocess_output', 'prompt_snapshot', 'prompt1_input', 'prompt1_output',
        'aggregation_output', 'prompt2_base_input', 'composition_result', 'final_report', 'prompt2_output',
        'report_template_snapshot', 'continuity_snapshot', 'report_config_snapshot')),
    file_name         text not null,
    storage_bucket    text,      -- ZIP/CSV/HTML等の大容量ファイルはSupabase Storageへ
    storage_path      text,
    content_text      text,      -- 小さいJSON/Markdown/Prompt本文等はDB内に直接保存してよい（storage_pathと排他）
    sha256            text not null,
    size_bytes        bigint not null,
    media_type        text not null,
    created_at        timestamptz not null default now(),
    constraint geo_report_run_artifacts_storage_xor_text check (
        (storage_path is not null and content_text is null)
        or (storage_path is null and content_text is not null)
    )
);

create index geo_report_run_artifacts_run_idx on public.geo_report_run_artifacts(run_id, artifact_type);
create index geo_report_run_artifacts_execution_idx on public.geo_report_run_artifacts(execution_id);

alter table public.geo_report_run_artifacts enable row level security;

-- geo_report_executionsとの相互FK（geo_report_executions側の該当列は上のCREATE TABLEでFK無しにしてある）
alter table public.geo_report_executions
    add constraint geo_report_executions_input_collection_artifact_fk
        foreign key (input_collection_artifact_id) references public.geo_report_run_artifacts(id),
    add constraint geo_report_executions_input_prompt2_base_input_artifact_fk
        foreign key (input_prompt2_base_input_artifact_id) references public.geo_report_run_artifacts(id),
    add constraint geo_report_executions_input_continuity_artifact_fk
        foreign key (input_continuity_artifact_id) references public.geo_report_run_artifacts(id);

comment on table public.geo_report_run_artifacts is
    'record_cluster_map_*.csv・代表投稿CSV・ZIP・final_report.html等の大容量成果物はstorage_bucket/'
    'storage_pathでSupabase Storageを参照する（DBサイズ・Egress・REST取得の負荷を避ける）。'
    '小さいJSON（continuity_context_next.json等）やPrompt本文Snapshotはcontent_textでDB内へ直接'
    '保存してよい（storage_pathと排他）。sha256により同一入力の検証ができる。composition_result'
    '（構造化JSON）とfinal_report（レンダリング後HTML）はartifact_typeを明確に分離する'
    '（1つのreport_revision型で曖昧にしない）';


-- ---- 14. geo_report_execution_batches（Chunk実行・チェックポイント・再開） ----

create table public.geo_report_execution_batches (
    id              uuid primary key default gen_random_uuid(),
    run_id          uuid not null references public.geo_report_collection_runs(run_id) on delete cascade,
    execution_id    uuid not null references public.geo_report_executions(id) on delete cascade,
    stage           text not null check (stage in
        ('prompt1_analysis_cluster', 'prompt1_sentiment_cluster', 'prompt1_targeted_stance', 'prompt2_composition')),
    batch_no        integer not null,
    item_ids        jsonb not null,
    status          text not null default 'pending'
        check (status in ('pending', 'running', 'completed', 'failed', 'skipped')),
    model_used      text,
    escalated       boolean not null default false,
    retry_count     integer not null default 0,
    error_message   text,
    result_artifact_ref text,
    started_at      timestamptz,
    completed_at    timestamptz
);

create unique index geo_report_execution_batches_execution_stage_batch_idx
    on public.geo_report_execution_batches(execution_id, stage, batch_no);
create index geo_report_execution_batches_run_stage_idx
    on public.geo_report_execution_batches(run_id, stage, status);

alter table public.geo_report_execution_batches enable row level security;


-- ---- 15. geo_report_execution_log（Token/Costログ専用） ----

create table public.geo_report_execution_log (
    id               uuid primary key default gen_random_uuid(),
    execution_id     uuid not null references public.geo_report_executions(id) on delete cascade,
    model            text,
    input_tokens     integer,
    cached_tokens    integer,
    output_tokens    integer,
    reasoning_tokens integer,
    estimated_cost   numeric,
    actual_cost      numeric,
    recorded_at      timestamptz not null default now()
);

create index geo_report_execution_log_execution_idx on public.geo_report_execution_log(execution_id);

alter table public.geo_report_execution_log enable row level security;

comment on table public.geo_report_execution_log is
    'Token/Costログ専用。Provenance（何が何の入力か）はgeo_report_executions側の列が担う。'
    '同じExecutionに対して複数回のLLM呼び出し（バッチ実行の各コール）が積み上がるログテーブル';


-- ---- 16. geo_report_collection_runs ALTER（Run固定・Canonical・Provenance） ----

alter table public.geo_report_collection_runs
    add column if not exists design_version_id uuid references public.geo_report_design_versions(id),
    add column if not exists narrative_master_asset_id uuid references public.geo_report_project_assets(id),
    add column if not exists report_template_asset_id uuid references public.geo_report_project_assets(id),
    add column if not exists prompt1_contract_version text,
    add column if not exists prompt2_contract_version text,
    add column if not exists prompt1_instruction_fingerprint text,
    add column if not exists prompt2_instruction_fingerprint text,
    add column if not exists preprocess_contract_version text,
    add column if not exists aggregation_contract_version text,
    add column if not exists renderer_version text,
    add column if not exists application_build_revision text,
    add column if not exists baseline_prompt2_execution_id uuid,
    add column if not exists previous_continuity_artifact_id uuid references public.geo_report_run_artifacts(id),
    add column if not exists canonical_preprocess_execution_id uuid,
    add column if not exists canonical_prompt1_execution_id uuid,
    add column if not exists canonical_prompt2_execution_id uuid,
    add column if not exists canonical_report_execution_id uuid;

alter table public.geo_report_collection_runs
    add constraint geo_report_collection_runs_baseline_prompt2_execution_fk
        foreign key (baseline_prompt2_execution_id) references public.geo_report_executions(id),
    add constraint geo_report_collection_runs_canonical_preprocess_execution_fk
        foreign key (canonical_preprocess_execution_id) references public.geo_report_executions(id),
    add constraint geo_report_collection_runs_canonical_prompt1_execution_fk
        foreign key (canonical_prompt1_execution_id) references public.geo_report_executions(id),
    add constraint geo_report_collection_runs_canonical_prompt2_execution_fk
        foreign key (canonical_prompt2_execution_id) references public.geo_report_executions(id),
    add constraint geo_report_collection_runs_canonical_report_execution_fk
        foreign key (canonical_report_execution_id) references public.geo_report_executions(id);

comment on column public.geo_report_collection_runs.canonical_prompt1_execution_id is
    'Aggregation/Prompt2再生成の入力起点。UIの「この結果をCurrent Reportにする」で更新される';
comment on column public.geo_report_collection_runs.canonical_prompt2_execution_id is
    'Continuity引き継ぎの起点（Report Editで表現を変えてもここは変わらない）';
comment on column public.geo_report_collection_runs.canonical_report_execution_id is
    '画面表示するFinal Report／次回Template Extractionの元（Report Editの最新版を反映してよい）。'
    '通常はcanonical_prompt2_execution_idと同じだが、Report Edit実行後はそれを指す';


-- ---- 17. geo_report_records ALTER（analysis_cluster_id/sentiment_cluster_id追加、cluster_id維持） ----

alter table public.geo_report_records
    add column if not exists analysis_cluster_id text,
    add column if not exists sentiment_cluster_id text;

create index if not exists geo_report_records_run_analysis_cluster_idx
    on public.geo_report_records(run_id, analysis_cluster_id);
create index if not exists geo_report_records_run_sentiment_cluster_idx
    on public.geo_report_records(run_id, sentiment_cluster_id);

comment on column public.geo_report_records.cluster_id is
    'legacy列。Current Pipelineの新規コードはanalysis_cluster_idを読む。移行完了（Phase D監査で'
    '洗い出した全参照箇所の切替、既存24箇所@geo_report_studio_service.py等）まではdual-writeし、'
    '本列は物理削除しない（P1以降）';
