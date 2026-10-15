-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): 前提整理（Intake）
--
-- 「1 設計」ページを、専門家AIへ分析を依頼する前に担当者とAIがAnalysis Contextを
-- 十分に揃えるIntake画面へ改修する（ユーザー指示、2026-09-24）。既存Analysis
-- Session（geo_ios_analysis_sessions）・Analysis Design（geo_ios_analysis_designs、
-- Scenario選択後のCQAS構造化データ専用）とは別概念——本ファイルの3テーブルは
-- 「Scenario選択より前に、担当者とAIの会話でAnalysis Contextを構築する」段階のみを
-- 扱う。既存Expert AI（geo_expert_engine.py）・CQASロジックには一切触れない
-- （前提整理チャットはgeo_llm_common.call_llm_structuredによる軽量な会話用LLM
-- 呼び出しであり、Expert AI本体を呼ばない）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない。geo_db_clientにマイグレーションランナーは存在せず、これが本リポジトリの
-- 既存運用）。destructive文（drop table/drop column）は含まない。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Analysis Context（AIが会話から整理した構造化前提。version追記のみ、上書きしない
--    ——既存geo_ios_analysis_designsと同じ「version整数+unique制約」パターン）
-- ---------------------------------------------------------------------
create table public.geo_ios_analysis_contexts (
    analysis_context_id    uuid primary key default gen_random_uuid(),
    analysis_session_id    uuid not null references public.geo_ios_analysis_sessions(analysis_session_id)
                               on delete cascade,
    version                integer not null,
    analysis_objective     text,
    core_question          text,
    current_situation      text,
    business_context       text,
    time_horizon           text,
    geographic_scope       text,
    relevant_actors        jsonb not null default '[]'::jsonb,
    known_events           jsonb not null default '[]'::jsonb,
    user_assumptions       jsonb not null default '[]'::jsonb,
    must_consider_topics   jsonb not null default '[]'::jsonb,
    internal_context       text,
    reference_materials    jsonb not null default '[]'::jsonb,
    constraints            jsonb not null default '[]'::jsonb,
    information_gaps       jsonb not null default '[]'::jsonb,
    ready_for_handoff      boolean not null default false,
    change_note            text,
    created_by             text not null,
    created_at             timestamptz not null default now(),
    unique (analysis_session_id, version)
);

create index geo_ios_analysis_contexts_session_idx
    on public.geo_ios_analysis_contexts(analysis_session_id);

alter table public.geo_ios_analysis_contexts enable row level security;

comment on table public.geo_ios_analysis_contexts is
    '前提整理チャットでAIが会話から整理した「現在の理解」（Analysis Objective/Core Question/'
    'Current Situation/Business Context/Time Horizon/Geographic Scope/Relevant Actors/'
    'Known Events/User Assumptions/Must-consider Topics/Internal Context/Reference Materials/'
    'Constraints/Information Gaps）。ユーザーが全項目を直接入力するフォームではなく、'
    'チャットのたびに新versionを追記するAI生成物——geo_ios_analysis_designsと同じ'
    '「上書きせず追記」方針。';

-- ---------------------------------------------------------------------
-- 2. Intake Turn（前提整理チャット1往復。Expert AIの1〜3分呼び出しと異なり軽量な
--    会話用LLM呼び出しのため同期実行——geo_ios_consultation_turnsのような
--    pending/completed/failedの非同期ポーリングは不要）
-- ---------------------------------------------------------------------
create table public.geo_ios_intake_turns (
    intake_turn_id         uuid primary key default gen_random_uuid(),
    analysis_session_id    uuid not null references public.geo_ios_analysis_sessions(analysis_session_id)
                               on delete cascade,
    -- user_message: 担当者が入力したメッセージ。attachment_addedの場合は
    -- 「[資料が追加されました: filename]」等のsynthetic messageを記録する
    -- （実際にユーザーが打った文言ではないことをtriggerで区別する）。
    trigger                text not null check (trigger in ('user_message', 'attachment_added')),
    user_message           text not null,
    assistant_message      text not null,
    attachment_ids         jsonb not null default '[]'::jsonb,
    resulting_context_version integer,
    model                  text,
    created_by             text not null,
    created_at             timestamptz not null default now()
);

create index geo_ios_intake_turns_session_idx
    on public.geo_ios_intake_turns(analysis_session_id, created_at);

alter table public.geo_ios_intake_turns enable row level security;

comment on table public.geo_ios_intake_turns is
    '前提整理チャットの対話ログ（1行=1往復）。resulting_context_versionは、この往復の'
    '結果として作られたgeo_ios_analysis_contexts.versionへの非FK参照（変化が無かった'
    '往復ではNULLもありうる）。';

-- ---------------------------------------------------------------------
-- 3. Session Attachment（前提整理チャットへの参考資料アップロード）
--
-- 既存geo_documents（記事クローラ由来、theme_id/source_id必須で本Intake概念とは
-- 結合不可）とは別テーブルとして新設するが、本文抽出ロジック（geo_documents_store.
-- extract_text、PDF/Word/HTML/text対応）はそのまま呼び出して再利用する
-- （抽出処理自体を複製しない）。pptx/xlsxは今回のスコープでは本文抽出非対応
-- （ユーザー判断、2026-09-24）——ファイルの保存・一覧表示のみ行い、
-- extraction_status='unsupported'として記録する。
-- ---------------------------------------------------------------------
create table public.geo_ios_session_attachments (
    attachment_id          uuid primary key default gen_random_uuid(),
    analysis_session_id    uuid not null references public.geo_ios_analysis_sessions(analysis_session_id)
                               on delete cascade,
    filename               text not null,
    mime_type              text,
    storage_path           text not null,
    content_hash           text not null,
    extracted_text         text,
    extraction_status      text not null check (extraction_status in ('ok', 'failed', 'unsupported')),
    extraction_error       text,
    uploaded_by            text not null,
    created_at             timestamptz not null default now()
);

create index geo_ios_session_attachments_session_idx
    on public.geo_ios_session_attachments(analysis_session_id);

alter table public.geo_ios_session_attachments enable row level security;

comment on table public.geo_ios_session_attachments is
    '前提整理チャット・最初のIntakeフォームの両方からアップロードされた参考資料は'
    '同一analysis_session_idへ紐づく（ユーザー要件: 「最初のフォームでアップロードした'
    '資料と、チャット途中で追加した資料は同じAnalysis Sessionへ紐付ける」）。';
