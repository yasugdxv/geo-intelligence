-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS) Phase B: 基盤スキーマ
--
-- Expert AI → Analysis Design → Report Studio → Expert Review → Approval →
-- Knowledge DB → Other Agents / Learning Loop というライフサイクル全体を
-- 成立させるための「業務・知識循環基盤」。既存Report Studio（geo_report_*系）・
-- 既存Expert AI（geo_expert_*系）の内部実装・スキーマは一切変更しない。
--
-- 新規テーブルは全て geo_ios_ prefixとする。Report Studio側に既に
-- 「Report Design」（geo_report_design_state 等、パイプライン内部のUI状態）が
-- 存在するため、Expert AI Chatが生成する分析設計（investigation_question/
-- hypotheses等の構造化データ）を指す本Phaseの概念とは明確に名前空間を分離する。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない。geo_db_clientにマイグレーションランナーは存在せず、これが本リポジトリの
-- 既存運用）。destructive文（drop table/drop column）は含まない。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Analysis Session（Expert AI Chatの分析セッション、§4）
-- ---------------------------------------------------------------------
create table public.geo_ios_analysis_sessions (
    analysis_session_id    uuid primary key default gen_random_uuid(),
    title                  text not null,
    theme                  text,
    user_id                text not null,
    status                 text not null default 'active'
                               check (status in ('active', 'scenario_selected', 'design_in_progress',
                                                  'handed_off', 'archived')),
    -- 選択されたScenario Candidateへの参照。geo_ios_scenario_candidates作成前に
    -- このテーブルを作る都合上、FK制約は本ファイル末尾でまとめて追加する。
    selected_scenario_id   uuid,
    -- Report StudioのReport Project（既存・安定テーブル、Phase 3A基盤）への引き継ぎ先。
    -- Report Studio側のテーブル定義・ロジックには一切触れない、参照のみの追加的リンク。
    linked_report_project_id uuid references public.geo_report_projects(project_id) on delete set null,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now()
);

create index geo_ios_analysis_sessions_user_idx on public.geo_ios_analysis_sessions(user_id);
create index geo_ios_analysis_sessions_status_idx on public.geo_ios_analysis_sessions(status);
create index geo_ios_analysis_sessions_report_project_idx
    on public.geo_ios_analysis_sessions(linked_report_project_id);

create trigger geo_ios_analysis_sessions_set_updated_at
    before update on public.geo_ios_analysis_sessions
    for each row execute function public.set_updated_at();

alter table public.geo_ios_analysis_sessions enable row level security;

comment on table public.geo_ios_analysis_sessions is
    'Expert AI Chatの「分析について専門家と壁打ちする」単位（§2, §4）。既存geo_expert_review_sessions'
    '（Draft-vs-Final学習セッション）とは別概念——本テーブルは対話そのものの単位を表す。';

-- ---------------------------------------------------------------------
-- 2. CQAS Scenario Candidate（§3）
--
-- 生成された全候補（例: 20件）を保持し、UI表示するのは上位5件のみ（displayed列で区別）。
-- 表示しなかった候補も削除しない（AIが何を候補にしたかを後から比較するため）。
-- ---------------------------------------------------------------------
create table public.geo_ios_scenario_candidates (
    scenario_candidate_id  uuid primary key default gen_random_uuid(),
    analysis_session_id    uuid not null references public.geo_ios_analysis_sessions(analysis_session_id)
                               on delete cascade,
    rank                   integer not null,
    title                  text not null,
    content                jsonb not null default '{}'::jsonb,
    displayed              boolean not null default false,
    selected               boolean not null default false,
    generated_at           timestamptz not null default now(),
    model                  text,
    prompt_version         text,
    cqas_knowledge_version text,
    created_at             timestamptz not null default now(),
    unique (analysis_session_id, rank)
);

create index geo_ios_scenario_candidates_session_idx
    on public.geo_ios_scenario_candidates(analysis_session_id);
create index geo_ios_scenario_candidates_selected_idx
    on public.geo_ios_scenario_candidates(analysis_session_id, selected);

alter table public.geo_ios_scenario_candidates enable row level security;

comment on table public.geo_ios_scenario_candidates is
    'CQAS（Cue-Question-Hypothesis-Scenario、既存Expert AI推論パイプラインの社内呼称）が生成した'
    '分析シナリオ候補の全件履歴。既存geo_expert_engine.pyのmain_scenario/alternative_scenariosを'
    '書き換えるものではなく、Chat経由の候補提示・選択を監査可能にするための別テーブル。';

-- selected_scenario_idのFK制約（対象テーブル作成後にまとめて追加）
alter table public.geo_ios_analysis_sessions
    add constraint geo_ios_analysis_sessions_selected_scenario_fkey
        foreign key (selected_scenario_id)
        references public.geo_ios_scenario_candidates(scenario_candidate_id)
        on delete set null;

-- ---------------------------------------------------------------------
-- 3. Analysis Design（§5、Report Design構造化データ。version追記のみ、上書きしない）
-- ---------------------------------------------------------------------
create table public.geo_ios_analysis_designs (
    analysis_design_id     uuid primary key default gen_random_uuid(),
    analysis_session_id    uuid not null references public.geo_ios_analysis_sessions(analysis_session_id)
                               on delete cascade,
    version                integer not null,
    investigation_question text,
    scope                  text,
    key_actors             jsonb not null default '[]'::jsonb,
    analytical_lenses      jsonb not null default '[]'::jsonb,
    hypotheses             jsonb not null default '[]'::jsonb,
    alternative_hypotheses jsonb not null default '[]'::jsonb,
    evidence_needed        jsonb not null default '[]'::jsonb,
    known_evidence         jsonb not null default '[]'::jsonb,
    knowledge_gaps         jsonb not null default '[]'::jsonb,
    watch_points           jsonb not null default '[]'::jsonb,
    report_structure       jsonb not null default '{}'::jsonb,
    confidence             text,
    assumptions            jsonb not null default '[]'::jsonb,
    change_note            text,
    created_by             text not null,
    created_at             timestamptz not null default now(),
    unique (analysis_session_id, version)
);

create index geo_ios_analysis_designs_session_idx
    on public.geo_ios_analysis_designs(analysis_session_id);

alter table public.geo_ios_analysis_designs enable row level security;

comment on table public.geo_ios_analysis_designs is
    'ユーザーがChatで修正するたびに新versionを追記するAnalysis Design（§5）。既存の'
    'geo_reports.version/supersedes_idと同じ「version整数+unique制約」パターンを踏襲し、'
    '「現在の版」はanalysis_session_id内でversion最大の行として導出する（上書きしない）。';

-- ---------------------------------------------------------------------
-- 4. Review Cycle（§8, §11: 無制限ラウンドのExpert Review Workflow）
--
-- 既存geo_expert_review_sessions（Draft-vs-Final Internal Learning Layer、単発ペアのみ）
-- とは別の仕組み。Report Studio内部のバージョニング列がまだ流動的なため、レビュー時点の
-- Reportの状態は version_snapshot(jsonb) として固定保存し、特定の内部カラムへ厳密にFK
-- 結合しない（呼び出し側=API層が既存の公開関数経由でスナップショットを組み立てる）。
-- ---------------------------------------------------------------------
create table public.geo_ios_review_cycles (
    review_cycle_id        uuid primary key default gen_random_uuid(),
    report_project_id      uuid not null references public.geo_report_projects(project_id) on delete cascade,
    analysis_session_id    uuid references public.geo_ios_analysis_sessions(analysis_session_id)
                               on delete set null,
    round_number           integer not null,
    reviewer               text not null,
    message                text,
    reviewed_version_label text,
    version_snapshot        jsonb,
    requested_by           text not null,
    requested_at           timestamptz not null default now(),
    due_date               timestamptz,
    status                 text not null default 'requested'
                               check (status in ('requested', 'in_review', 'completed', 'superseded')),
    findings               jsonb,
    author_response        text,
    result                 text check (result is null or result in ('approved', 'revision_requested', 'rejected')),
    created_at             timestamptz not null default now(),
    completed_at           timestamptz,
    unique (report_project_id, round_number)
);

create index geo_ios_review_cycles_project_idx on public.geo_ios_review_cycles(report_project_id);
create index geo_ios_review_cycles_status_idx on public.geo_ios_review_cycles(status);

alter table public.geo_ios_review_cycles enable row level security;

comment on column public.geo_ios_review_cycles.version_snapshot is
    'レビュー依頼時点のReport状態のスナップショット（呼び出し側がReport Studioの公開関数'
    '経由で取得した内容をそのまま複製）。依頼後にDraftが変更されても、Reviewerが見ていた'
    'Versionが分からなくならないようにするため（§8要件）。';
comment on column public.geo_ios_review_cycles.findings is
    'critical_findings/major_findings/minor_findings/suggested_revision/evidence_gap/'
    'alternative_interpretation等（§7）を持つ構造化Finding。形式はAPI層のPydanticモデルで検証する。';

-- ---------------------------------------------------------------------
-- 5. Review Comment（§10: section/paragraph/claim/evidenceへのAnchor付きコメント）
-- ---------------------------------------------------------------------
create table public.geo_ios_review_comments (
    review_comment_id      uuid primary key default gen_random_uuid(),
    review_cycle_id        uuid not null references public.geo_ios_review_cycles(review_cycle_id)
                               on delete cascade,
    report_project_id      uuid not null references public.geo_report_projects(project_id) on delete cascade,
    anchor_type            text check (anchor_type is null
                               or anchor_type in ('section', 'paragraph', 'claim', 'evidence', 'general')),
    anchor_id              text,
    comment                text not null,
    severity               text check (severity is null or severity in ('critical', 'major', 'minor')),
    created_by             text not null,
    created_at             timestamptz not null default now(),
    resolved_status        text not null default 'open'
                               check (resolved_status in ('open', 'resolved', 'partially_resolved',
                                                            'rejected', 'not_applicable')),
    author_response        text,
    resolved_in_version    text,
    resolved_at            timestamptz
);

create index geo_ios_review_comments_cycle_idx on public.geo_ios_review_comments(review_cycle_id);
create index geo_ios_review_comments_project_idx on public.geo_ios_review_comments(report_project_id);
create index geo_ios_review_comments_status_idx on public.geo_ios_review_comments(resolved_status);

alter table public.geo_ios_review_comments enable row level security;

comment on table public.geo_ios_review_comments is
    'コメント削除で対応履歴を消さない（§12）。resolved_statusをopen以外へ変更するのみで、'
    '行自体の削除APIは提供しない。';

-- ---------------------------------------------------------------------
-- 6. Approval Event（§13: Route A（アプリ内承認）/ Route B（Upload）の両方を記録）
-- ---------------------------------------------------------------------
create table public.geo_ios_approval_events (
    approval_event_id      uuid primary key default gen_random_uuid(),
    report_project_id      uuid not null references public.geo_report_projects(project_id) on delete cascade,
    version_label          text,
    version_snapshot        jsonb,
    approval_status        text not null check (approval_status in ('approved', 'rejected')),
    approver               text not null,
    approval_date          timestamptz not null default now(),
    approval_source        text not null check (approval_source in ('in_app', 'upload')),
    notes                  text,
    created_at             timestamptz not null default now()
);

create index geo_ios_approval_events_project_idx on public.geo_ios_approval_events(report_project_id);

alter table public.geo_ios_approval_events enable row level security;

comment on table public.geo_ios_approval_events is
    'Upload経由（approval_source=upload）であっても、approval_status/approver/approval_dateを'
    '明示的に記録した行が無ければApproved扱いにしない（§13: 単なるUploadを自動的にApproved扱い'
    'しないこと、の機械的強制）。';

-- ---------------------------------------------------------------------
-- 7. Learning Candidate（§20-25: Expert Review→Human Review→Active Knowledgeの学習パイプライン）
--
-- 既存geo_expert_review_changes（Internal Expert Learning Layer、case_specific_assessment/
-- reusable_reasoningの2値分類のみ）とは別テーブル。本テーブルはCQAS・Report Design・
-- Intelligence Need等、より広い種類のLearning Candidateを扱うため新設するが、Expert Review
-- 由来のCandidateについてはsource_expert_review_change_idで既存changeへ参照を持てるようにし、
-- 二重管理を避ける（既存change自体の分類・状態遷移は変更しない）。
-- ---------------------------------------------------------------------
create table public.geo_ios_learning_candidates (
    candidate_id            uuid primary key default gen_random_uuid(),
    candidate_type          text not null check (candidate_type in (
                                'cqas_question_pattern', 'scenario_pattern', 'reasoning_rule',
                                'attention_rule', 'failure_pattern', 'evidence_rule',
                                'source_preference', 'actor_intelligence', 'media_intelligence',
                                'report_design_pattern', 'intelligence_need_pattern')),
    trigger_context         jsonb,
    observation             text not null,
    generalized_rule        text not null,
    failure_pattern_example text,
    positive_example        text,
    scope                   text,
    confidence              text,
    source_report_id        uuid,
    source_review_cycle_id  uuid references public.geo_ios_review_cycles(review_cycle_id) on delete set null,
    source_review_comment_id uuid references public.geo_ios_review_comments(review_comment_id) on delete set null,
    source_expert_review_change_id uuid references public.geo_expert_review_changes(change_id) on delete set null,
    extraction_model        text,
    status                  text not null default 'candidate'
                               check (status in ('candidate', 'approved', 'rejected', 'superseded')),
    approved_knowledge_item_id uuid references public.geo_knowledge_items(knowledge_item_id) on delete set null,
    reviewed_by             text,
    reviewed_at             timestamptz,
    created_at              timestamptz not null default now()
);

create index geo_ios_learning_candidates_status_idx on public.geo_ios_learning_candidates(status);
create index geo_ios_learning_candidates_type_idx on public.geo_ios_learning_candidates(candidate_type);

alter table public.geo_ios_learning_candidates enable row level security;

comment on table public.geo_ios_learning_candidates is
    '§24: Expert commentから自動でActive Knowledge化しない。statusはHuman Reviewでのみ'
    'candidate→approved/rejectedへ遷移し、approved時のみ既存geo_knowledge_service.'
    'create_knowledge_item/review_knowledge_itemの状態機械を通してActive Knowledge化する'
    '（本テーブル自身は独自のActive化ロジックを持たない）。';

-- ---------------------------------------------------------------------
-- 8. geo_knowledge_items: Human Visibility / Agent Sharing Policy（§16、追加列のみ）
--
-- 既定値は「現状の実質的な挙動（visibility制御が一切無く、社内であれば誰でも/どのAgentからも
-- 参照可能）」を変えないよう選んでいる。実際のfiltering（retrieval時のvisibilityチェック）は
-- 本Phaseでは実装しない——geo_intelligence_service.py / geo_department_intelligence_service.py
-- は現在進行中の改修対象ではないため、列を追加するに留め、enforcement配線は別Phase（F）で
-- 既存Retrieval実装者と調整の上、慎重に行う。
-- ---------------------------------------------------------------------
alter table public.geo_knowledge_items
    add column if not exists human_visibility text not null default 'company'
        check (human_visibility in ('private', 'project', 'geopolitics_team', 'department',
                                     'company', 'custom')),
    add column if not exists agent_sharing_policy text not null default 'geopolitics_only'
        check (agent_sharing_policy in ('none', 'geopolitics_only', 'allowed_cross_domain', 'custom'));

comment on column public.geo_knowledge_items.human_visibility is
    '人間ユーザーに対する公開範囲（§16）。既定company: 現状の「社内であれば閲覧可能」という'
    '実質挙動を変えない値。現時点ではどのRetrieval経路もこの列でfilterしていない（Phase Fで対応）。';
comment on column public.geo_knowledge_items.agent_sharing_policy is
    '他Domain AgentへのKnowledge共有範囲（§16、human_visibilityとは独立した軸）。既定'
    'geopolitics_only: 現状geo_department_intelligence_serviceのCross-Domain同期呼び出しが'
    'デフォルトOFF（GEO_DEPARTMENT_SYNC_EXPERT_ENABLED）であることと整合させた保守的な既定値。'
    '現時点ではどのRetrieval経路もこの列でfilterしていない（Phase Fで対応）。';
