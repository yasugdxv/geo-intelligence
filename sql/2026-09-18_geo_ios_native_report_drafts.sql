-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): Native Report Drafts
--
-- IOS自身が「Analysis Session → Native Draft(AI自動生成) → AI Review →
-- Expert Review → Approval」という本文生成〜レビューフローをIOS内で完結
-- させるための基盤。既存Report Studio（geo_report_projects/geo_report_syntheses、
-- CSVアップロード→クラスタリング→ChatGPT Narrative Master合成ツール）は、
-- 追加調査が必要な場合だけ使う独立した補助ツールという位置づけに変わり、
-- Native Reportの必須の親概念ではなくなる。Report Studio自身のテーブル・
-- ロジックは一切変更しない。
--
-- 【設計原則】
-- 1. Draft本文はimmutable — 生成・人間による手直し・AI Review後の修正の
--    どれであっても、必ず新しいversionを作る（in-place上書きは提供しない）。
--    どのversionをレビュー・承認したかを後から一意に追跡できることを優先する。
-- 2. AI Reviewはupsertしない — 同一Draftへの再Reviewもreview_attemptとして
--    履歴に積む（1 Draft = 1件、ではない）。将来のAI Review→修正→専門家
--    レビューというLearning Dataとして使える構造にする。
-- 3. Expert Review申請時点のDraftをFreezeする — Review Cycle行に
--    source_report_draft_id/source_draft_version/source_content_hashを
--    明示的に保存し、申請後に新しいDraft versionが作られてもこのReview
--    Cycleが指す内容は変わらない。Approvalは常にこのFreeze結果のみを見る
--    （Current Draftを再構築しない）。
-- 4. Native/Legacyの切り替えはSession側の明示フラグ（report_generation_mode）
--    で管理する——「Native Draftが存在するかどうか」の暗黙判定はしない。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない。geo_db_clientにマイグレーションランナーは存在せず、これが本リポジトリの
-- 既存運用）。destructive文（drop table/drop column）は含まない。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Report Draft（version整数+unique制約、フルスナップショット。
--    geo_ios_analysis_designsと同じ「毎回新versionを追記する」パターン）
-- ---------------------------------------------------------------------
create table public.geo_ios_report_drafts (
    report_draft_id         uuid primary key default gen_random_uuid(),
    analysis_session_id     uuid not null
                                 references public.geo_ios_analysis_sessions(analysis_session_id)
                                 on delete cascade,
    version                  integer not null,
    -- 生成元Version（v1のみnull）。将来のdiff表示・Learningのための親子関係。
    parent_report_draft_id   uuid references public.geo_ios_report_drafts(report_draft_id)
                                 on delete set null,
    -- ai_generated=Expert AI(draft_generationモード)による自動生成
    -- human_edit=人間による手直し（LLM呼び出しなし、即completed）
    source                    text not null check (source in ('ai_generated', 'human_edit')),
    status                    text not null default 'pending'
                                 check (status in ('pending', 'completed', 'failed')),
    -- [{heading, body, supporting_evidence_ids: string[]}]。completedになるまでnull。
    sections                  jsonb,
    -- sectionsを正規化(heading/body/supporting_evidence_idsを含む)したJSONのsha256。
    -- completedになった時点で計算・保存し、以後は再計算せずコピーして使い回す
    -- （Review Cycle/Approvalの整合性チェックの基準値）。
    content_hash              text,
    change_note               text,
    created_by                 text not null,
    error_message              text,
    -- 完了後のみ設定。既存geo_expert_requests/geo_expert_responsesへの参照のみで、
    -- 応答本体はコピーしない（Single Source of Truthを保つ）。human_editの場合はnull。
    expert_request_id          uuid,
    expert_response_id         uuid,
    created_at                  timestamptz not null default now(),
    completed_at                timestamptz,
    unique (analysis_session_id, version)
);

create index geo_ios_report_drafts_session_idx
    on public.geo_ios_report_drafts(analysis_session_id);

create index geo_ios_report_drafts_pending_idx
    on public.geo_ios_report_drafts(status)
    where status = 'pending';

alter table public.geo_ios_report_drafts enable row level security;

comment on table public.geo_ios_report_drafts is
    'IOS自身のNative Draft本文（AI自動生成 or 人間の手直し）。in-place上書きは提供せず、'
    '変更は必ず新versionとして追記する——Expert Review/Approvalがどのversionを見たかを'
    '後から一意に追跡できることを優先する。';

-- ---------------------------------------------------------------------
-- 2. Draft AI Review（append-only履歴。1 Draftにつき複数review_attemptを許容する
--    ——upsertしない。Report Draft本文とは別テーブルで、Findingのみを保持する）
-- ---------------------------------------------------------------------
create table public.geo_ios_draft_ai_reviews (
    draft_ai_review_id       uuid primary key default gen_random_uuid(),
    report_draft_id           uuid not null
                                   references public.geo_ios_report_drafts(report_draft_id)
                                   on delete cascade,
    review_attempt             integer not null,
    status                     text not null default 'pending'
                                   check (status in ('pending', 'completed', 'failed')),
    -- [{finding_id, category, severity(Critical/Major/Minor), finding, recommendation,
    --   evidence_ids, status("open"|"waived"), waiver_reason, waived_by, waived_at}]。
    -- statusはopen/waivedの2値のみ——本文を直さずに「解決済み」にする設計は提供しない
    -- （指摘を採用しない場合のみ明示的にwaive、理由を必須で残す）。
    findings                   jsonb,
    error_message               text,
    expert_request_id           uuid,
    expert_response_id          uuid,
    requested_by                 text not null,
    created_at                   timestamptz not null default now(),
    completed_at                 timestamptz,
    unique (report_draft_id, review_attempt)
);

create index geo_ios_draft_ai_reviews_draft_idx
    on public.geo_ios_draft_ai_reviews(report_draft_id);

create index geo_ios_draft_ai_reviews_pending_idx
    on public.geo_ios_draft_ai_reviews(status)
    where status = 'pending';

alter table public.geo_ios_draft_ai_reviews enable row level security;

comment on table public.geo_ios_draft_ai_reviews is
    'Draft本文に対するAI Review実行の履歴（append-only、upsertしない）。同一Draftへの'
    '再Reviewもreview_attemptとして別行に積み、将来のAI Review→修正→専門家レビューの'
    'Learning Dataとして使える構造にする。';

-- ---------------------------------------------------------------------
-- 3. geo_ios_analysis_sessions: report_generation_mode（Native/Legacyの
--    明示的なSource of Truth。4段階で追加し、既存行と新規行の意図を
--    Migration上も明確にする）
-- ---------------------------------------------------------------------
alter table public.geo_ios_analysis_sessions
    add column report_generation_mode text;

update public.geo_ios_analysis_sessions
    set report_generation_mode = 'report_studio';

alter table public.geo_ios_analysis_sessions
    alter column report_generation_mode set default 'native';

alter table public.geo_ios_analysis_sessions
    alter column report_generation_mode set not null,
    add constraint geo_ios_analysis_sessions_report_generation_mode_check
        check (report_generation_mode in ('native', 'report_studio'));

comment on column public.geo_ios_analysis_sessions.report_generation_mode is
    '本文生成の経路。native=IOS自身のReport Draft機構、report_studio=既存Report Studio'
    '（geo_report_projects/geo_report_syntheses）経由。本Migration適用時点の既存行は全て'
    'report_studioへbackfillし、以後の新規Sessionはdefaultのnativeになる。Native Draftが'
    '偶然存在するかどうかでは判定しない——この列が唯一のSource of Truth。';

-- ---------------------------------------------------------------------
-- 4. geo_ios_review_cycles: Expert Review申請時点のDraftをFreezeする列
--    （nullable、Report Studio経路(legacy)では常にnullのまま・既存version_snapshot
--    のみで完結する。既存行への影響なし）
-- ---------------------------------------------------------------------
alter table public.geo_ios_review_cycles
    add column source_report_draft_id uuid
        references public.geo_ios_report_drafts(report_draft_id) on delete set null,
    add column source_draft_version integer,
    add column source_content_hash text;

comment on column public.geo_ios_review_cycles.source_report_draft_id is
    'Native経路: Expert Review申請時点でFreezeしたReport Draftへの参照。'
    'Report Studio経路(legacy)ではnullのまま（既存version_snapshotのみで完結）。'
    '申請後に新しいDraft versionが作られても、この行が指す内容は変わらない。';
comment on column public.geo_ios_review_cycles.source_draft_version is
    'Freeze時点のgeo_ios_report_drafts.version（source_report_draft_idと同時に設定）。';
comment on column public.geo_ios_review_cycles.source_content_hash is
    'Freeze時点でgeo_ios_report_drafts.content_hashからコピーした値（再計算しない）。'
    'Approval時にCurrent Draftと同一のReview Cycleかどうかを、versionだけでなく'
    'content_hashも合わせて確認するための整合性チェック用。';
