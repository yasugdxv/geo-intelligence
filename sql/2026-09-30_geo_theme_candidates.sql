-- =====================================================================
-- 地政学インテリジェンス基盤 Phase 2G-1: Theme Discovery — Theme Candidate data model
-- 作成日: 2026-09-30
-- 前提: sql/2026-09-08_geo_episode_clusters.sql / geo_episode_cluster_links.sql /
--       geo_external_events.sql、sql/2026-09-16_geo_ios_foundation.sql（geo_ios_analysis_
--       sessions）実行済みであること
--
-- 既存External Signal → Actor Episode Candidate → Episode Cluster → Situation Link
-- パイプラインは一切変更しない。Theme Candidateはその上に追加する新しいLayer
-- （geo_theme_candidate_service.pyがaccepted Episode Cluster / accepted same_situation
-- Situation Linkのconnected componentを読み取り専用で参照し、本テーブルへ生成・mergeする）。
--
-- internal_coverageは永続化しない（Requester-dependentなためRuntime計算。表示のたびに
-- geo_intelligence_ai_theme_coverage_service.assess_internal_coverage()を都度呼ぶ、
-- Phase 2F-6のStable Coreは無変更のまま利用する）。
--
-- Evidence本文はここへ複製しない。source_episode_cluster_ids/source_situation_link_ids/
-- source_external_signal_ids/source_external_event_idsは既存テーブルへの参照のみで、
-- Source of Truthは常にgeo_episode_clusters/geo_episode_cluster_links/
-- geo_external_signals/geo_external_eventsのまま。
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select count(*) from information_schema.tables
--     where table_schema='public' and table_name = 'geo_theme_candidates';
--   -- 期待値: 0（初回適用時）
--   select column_name from information_schema.columns
--     where table_schema='public' and table_name='geo_ios_analysis_sessions'
--       and column_name='source_theme_candidate_id';
--   -- 期待値: 0行（初回適用時）
-- =====================================================================


-- ---- 1. geo_theme_candidates ----

create table public.geo_theme_candidates (
    candidate_id             uuid primary key default gen_random_uuid(),

    -- actors/regions/themes（本テーブルのjsonb列、LLM出力をgroundingフィルタ後に保存した値）
    -- + change_type + source cluster構造（cluster_ids/link_ids）から決定的に生成する
    -- （geo_theme_candidate_service._compute_fingerprint参照）。actors/regions/themesは
    -- 実際にはLLM出力ではなく、source Episode Cluster/Situation Link自体から決定的に導出した
    -- 値を使う（LLMのフレージング揺れでfingerprintが不安定にならないようにするため）。
    -- 同一fingerprintの場合は新規作成せずmergeする（signal_count/latest_signal_at/why_now/
    -- observed_change更新、§Duplicate Candidate Control）。Semantic dedupは行わない。
    candidate_fingerprint    text not null,

    -- Candidate Framing: 4層（signal_summary→observed_change→why_now→questionの順で生成）。
    title                    text not null,
    signal_summary           text not null,   -- 何が起きたか（External Evidenceの要約）
    observed_change          text not null,   -- 何が変わっている可能性があるか（観測、断定しない）
    why_now                  text not null,   -- なぜ今見るのか
    question                 text not null,   -- 何を分析すべきか（問いであり結論ではない）

    actors                   jsonb not null default '[]'::jsonb,
    regions                  jsonb not null default '[]'::jsonb,
    themes                   jsonb not null default '[]'::jsonb,
    business_relevance       jsonb not null default '[]'::jsonb,
    -- market/supply_chain/regulatory/reputation/public_affairs/sustainability/technology/
    -- competitive_environmentからLLMが1〜3個選ぶだけ（§14、複雑なBusiness Impact Scoreは作らない）。

    candidate_type           text not null
        check (candidate_type in ('emerging', 'changing', 're_emerging', 'structural', 'cross_market')),
    -- Themeの「性質」の分類（重要度ランキングではない）。
    change_type              text not null
        check (change_type in ('actor_movement', 'policy_shift', 'relationship_change',
                                'institutionalization', 'signal_spike', 'recurrence',
                                'cross_region_pattern')),
    -- 「何が変わったトリガーか」の分類。candidate_typeとは別軸（混同しない、§11）。

    -- Evidence Traceability（複製しない、参照のみ）。
    source_episode_cluster_ids   jsonb not null default '[]'::jsonb,
    source_situation_link_ids    jsonb not null default '[]'::jsonb,
    source_external_signal_ids   jsonb not null default '[]'::jsonb,
    source_external_event_ids    jsonb not null default '[]'::jsonb,

    first_seen_at            timestamptz not null,
    latest_signal_at         timestamptz not null,
    signal_count             integer not null default 0,

    status                   text not null default 'NEW'
        check (status in ('NEW', 'SHORTLISTED', 'DISMISSED', 'MONITORING', 'HANDED_OFF')),
    -- NEW=AI新規提案 / SHORTLISTED=人間が検討対象として残した / DISMISSED=現時点では分析しない
    -- / MONITORING=まだ分析せず継続観測 / HANDED_OFF=Expert AI相談へ移した。
    -- AIは自動でSHORTLISTED/HANDED_OFFへ遷移させない（§22 Human Decision、
    -- geo_theme_candidate_service.update_candidate_status以外の経路でstatusを書き換えない）。
    status_updated_at        timestamptz,
    status_updated_by        text,

    generation_model         text,  -- Candidate framing生成に使ったモデル名（telemetry用、任意）
    is_demo                  boolean not null default false,
    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now()
);

create unique index geo_theme_candidates_fingerprint_idx
    on public.geo_theme_candidates(candidate_fingerprint);
create index geo_theme_candidates_status_idx on public.geo_theme_candidates(status, is_demo);
create index geo_theme_candidates_latest_signal_idx on public.geo_theme_candidates(latest_signal_at desc);

create trigger geo_theme_candidates_set_updated_at
    before update on public.geo_theme_candidates
    for each row execute function public.set_updated_at();
alter table public.geo_theme_candidates enable row level security;

comment on table public.geo_theme_candidates is
    'Phase 2G-1: 既存External Signal→Actor Episode→Episode Cluster→Situation Linkパイプライン'
    'の上に追加するTheme Candidate層（パイプライン自体は無変更）。internal_coverageは持たない'
    '（Requester依存のためRuntime計算、geo_intelligence_ai_theme_coverage_service.'
    'assess_internal_coverage()を都度呼ぶ、INTELLIGENCE_AI_V1 Stable Coreは無変更のまま利用）。'
    'Evidence本文は複製せず、source_*_idsで既存テーブルを参照するのみ。AIは自動でSHORTLISTED/'
    'HANDED_OFFに遷移させない（Human Decision）。';


-- ---- 2. geo_ios_analysis_sessions: Theme Candidateからの起源traceability ----
-- geo_ios_learning_candidates.source_*_id群と同じ「生成時点で設定するorigin参照」パターン
-- （linked_report_project_idのような後付けlinkとは向きが逆）。Handoff機能自体（Phase 2G-5）
-- はまだ実装しない、列の追加のみ。

alter table public.geo_ios_analysis_sessions
    add column if not exists source_theme_candidate_id uuid
        references public.geo_theme_candidates(candidate_id) on delete set null;
create index if not exists geo_ios_analysis_sessions_theme_candidate_idx
    on public.geo_ios_analysis_sessions(source_theme_candidate_id);

comment on column public.geo_ios_analysis_sessions.source_theme_candidate_id is
    'Phase 2G-1: どのTheme Candidateから始まったAnalysis Sessionかの起源参照（省略可、Handoff'
    'は未実装のためPhase 2G-1時点では常にnull）。Expert AI側はここから独立してReadingを構築'
    'し、Theme Candidateの内容をReadingとして扱わない（§23）。';


-- =====================================================================
-- ロールバック手順（本migrationでは実行しない、記録のみ）
--
-- alter table public.geo_ios_analysis_sessions drop column if exists source_theme_candidate_id;
-- drop table if exists public.geo_theme_candidates;
-- =====================================================================
