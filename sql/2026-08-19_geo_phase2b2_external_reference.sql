-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase2B-2: External Reference Model &
-- Internal Expert Learning Layer
--
-- 承認済みPlan: C:\Users\269811\.claude\plans\pure-drifting-cosmos.md
-- 既存テーブル・列の削除やデータ上書きは一切行わない（すべてadditive）。
-- 適用順序: このファイル1本のみ（Phase1/2A/2Bのmigrationが適用済みであることが前提）。
-- =====================================================================


-- ---- 1. geo_source_registry: Source Reference Policy（1章）----
-- Claimを自動availableにするかどうかは、Claim個別のHuman Reviewではなく、Source側に
-- 一度だけ設定する参照方針で決める。既存のautomation_allowed/content_storage_allowedと
-- 同じ「Steward/管理者がSource単位で1回設定する」パターン。

alter table public.geo_source_registry
    add column if not exists reference_availability text not null default 'restricted'
        check (reference_availability in ('available', 'restricted')),
    add column if not exists reference_availability_set_by text,
    add column if not exists reference_availability_set_at timestamptz;

comment on column public.geo_source_registry.reference_availability is
    'このSource由来のClaimを、AIがHuman Reviewなしで参照してよいか（available/restricted）。'
    '内容の正誤判定ではなく、Sourceの信頼性・利用条件についての運用判断（Steward操作）。';

-- backfill: 既存trust_tierを初期値のヒントとしてのみ使う（以降の判断基準はこの列が正）
update public.geo_source_registry set reference_availability = 'available',
    reference_availability_set_by = 'system_backfill_2026-08-19',
    reference_availability_set_at = now()
    where trust_tier in ('A1', 'A2');


-- ---- 2. geo_knowledge_items: Retrieval Status（二軸）・Internal Expert Knowledge由来管理 ----

alter table public.geo_knowledge_items
    drop constraint if exists geo_knowledge_items_information_class_check,
    add constraint geo_knowledge_items_information_class_check
        check (information_class in (
            'observed_fact', 'actor_claim', 'external_assessment',
            'internal_hypothesis', 'internal_expert_assessment'
        ));

-- 既存の不整合の是正（Phase2Aでgeo_knowledge_item_schemas.pyには追加済みだがSQL側は未反映だった4種）
alter table public.geo_knowledge_items
    drop constraint if exists geo_knowledge_items_knowledge_type_check,
    add constraint geo_knowledge_items_knowledge_type_check
        check (knowledge_type in (
            'country_foundation', 'actor_profile', 'party_profile',
            'relationship_assessment', 'historical_path_dependency', 'precedent',
            'mechanism', 'reasoning_rule', 'indicator', 'foundation_update',
            'case_finding', 'leverage_card',
            'actor_profile_update', 'relationship_update', 'dependency_update', 'sanctions_update'
        ));

alter table public.geo_knowledge_items
    add column if not exists retrieval_status text not null default 'restricted'
        check (retrieval_status in ('available', 'restricted', 'deprecated', 'archived')),
    add column if not exists origin text
        check (origin in (
            'manual', 'connector_derived', 'case_candidate_extraction',
            'external_claim_extraction', 'internal_expert_review'
        )),
    add column if not exists derived_from_review_session_id uuid,
    add column if not exists derived_from_change_id uuid,
    add column if not exists usage_count integer not null default 0,
    add column if not exists support_count integer not null default 0,
    add column if not exists last_used_at timestamptz,
    add column if not exists last_confirmed_at timestamptz;

comment on column public.geo_knowledge_items.retrieval_status is
    'status（lifecycle: candidate/in_review/active/...）とは独立した二軸。review_knowledge_item'
    'による初回遷移時（candidate/in_reviewから抜ける瞬間）のみ自動初期化し、以降は'
    'set_retrieval_status()経由の明示操作でのみ変更する（自動再同期しない）。';
comment on column public.geo_knowledge_items.usage_count is
    'Analytics専用。Retrieval Scoreの算出には使用しない（last_used_atも同様）。';


-- ---- 3. geo_claims: Retrieval Status・Source紐付け ----

alter table public.geo_claims
    drop constraint if exists geo_claims_information_class_check,
    add constraint geo_claims_information_class_check
        check (information_class in (
            'observed_fact', 'actor_claim', 'external_assessment',
            'internal_hypothesis', 'internal_expert_assessment'
        ));

alter table public.geo_claims
    add column if not exists retrieval_status text not null default 'restricted'
        check (retrieval_status in ('available', 'restricted', 'deprecated', 'archived')),
    add column if not exists source_id uuid references public.geo_source_registry(source_id) on delete set null;

comment on column public.geo_claims.source_id is
    'このClaimの情報の出所となる外部Source（origin_typeとは独立概念。originが"どこから'
    '導出されたか"であるのに対し、source_idは"情報が最終的にどのSourceに由来するか"）。'
    'origin_type=documentならgeo_documents.source_idから、event/observationならそれぞれの'
    '証拠(geo_evidence)経由のdocumentから解決する。manual/caseは解決を試みない。';
comment on column public.geo_claims.retrieval_status is
    'source_idが解決できた場合はgeo_source_registry.reference_availabilityをコピーする。'
    'source_idが解決できずorigin_typeがmanual/caseの場合のみavailable。それ以外はdefaultの'
    'restrictedのまま（安全側）。';


-- ---- 4. geo_claim_issues: IssueベースでReference Claimを引けるようにする ----

create table public.geo_claim_issues (
    claim_id   uuid not null references public.geo_claims(claim_id) on delete cascade,
    issue_id   uuid not null references public.geo_issues(issue_id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (claim_id, issue_id)
);

alter table public.geo_claim_issues enable row level security;


-- ---- 5. Expert Review Session / Notes / Changes / Link Tables ----

create table public.geo_expert_review_sessions (
    review_session_id  uuid primary key default gen_random_uuid(),
    case_id            uuid references public.geo_cases(case_id) on delete set null,
    draft_document_id  uuid not null references public.geo_documents(document_id) on delete restrict,
    final_document_id  uuid not null references public.geo_documents(document_id) on delete restrict,
    draft_expert_response_id uuid references public.geo_expert_responses(response_id) on delete set null,
    reviewer_id        text not null,
    reviewer_label     text,
    review_type        text not null default 'case_report'
        check (review_type in ('case_report', 'executive_brief', 'other')),
    started_at         timestamptz not null default now(),
    completed_at       timestamptz,
    delta_extraction_status text not null default 'pending'
        check (delta_extraction_status in ('pending', 'completed', 'failed')),
    metadata           jsonb,
    is_demo            boolean not null default false,
    created_at         timestamptz not null default now()
);

create index geo_expert_review_sessions_case_idx on public.geo_expert_review_sessions(case_id);

alter table public.geo_expert_review_sessions enable row level security;

comment on table public.geo_expert_review_sessions is
    '社内専門家のAI Draft→Expert Final差分からReasoningを学習するためのSession。'
    'draft/final文書はgeo_documents（内部レビュー用固定source_id）として保存する。';

create table public.geo_expert_review_notes (
    note_id            uuid primary key default gen_random_uuid(),
    review_session_id  uuid not null references public.geo_expert_review_sessions(review_session_id) on delete cascade,
    note_type          text not null default 'free_text'
        check (note_type in ('free_text', 'word_comment', 'track_change', 'other')),
    anchor_excerpt     text,
    comment_text       text not null,
    author             text,
    source_format      text,
    created_at         timestamptz not null default now()
);

create index geo_expert_review_notes_session_idx on public.geo_expert_review_notes(review_session_id);

alter table public.geo_expert_review_notes enable row level security;

comment on table public.geo_expert_review_notes is
    '専門家がReview時に残すコメント・注釈。今回はnote_type=free_textのみ入力対象だが、'
    'anchor_excerpt/source_formatで将来のWord Comments/Track Changes取り込みを吸収できる構造。';

create table public.geo_expert_review_changes (
    change_id           uuid primary key default gen_random_uuid(),
    review_session_id   uuid not null references public.geo_expert_review_sessions(review_session_id) on delete cascade,
    change_type         text not null
        check (change_type in (
            'judgment_change', 'main_hypothesis_change', 'alternative_hypothesis_added',
            'stakeholder_added', 'stakeholder_removed', 'evidence_weight_changed',
            'mechanism_added', 'precedent_added', 'comparison_rule', 'reasoning_rule',
            'scenario_change', 'trigger_added', 'causal_chain_change', 'uncertainty_adjustment'
        )),
    draft_excerpt       text,
    final_excerpt       text,
    reasoning_summary   text not null,
    reusability_class    text not null
        check (reusability_class in ('case_specific_assessment', 'reusable_reasoning')),
    related_hypothesis_id uuid references public.geo_case_hypotheses(hypothesis_id) on delete set null,
    confidence          text not null default '判断不能' check (confidence in ('高', '中', '低', '判断不能')),
    extraction_model    text not null,
    disposition         text not null default 'pending'
        check (disposition in ('pending', 'promoted', 'kept_case_only', 'rejected_as_non_reusable', 'reinforced')),
    promoted_knowledge_item_id uuid references public.geo_knowledge_items(knowledge_item_id) on delete set null,
    reinforced_knowledge_item_id uuid references public.geo_knowledge_items(knowledge_item_id) on delete set null,
    disposition_by      text,
    disposition_at      timestamptz,
    created_at          timestamptz not null default now(),
    constraint geo_expert_review_changes_excerpt_check
        check (draft_excerpt is not null or final_excerpt is not null)
);

create index geo_expert_review_changes_session_idx on public.geo_expert_review_changes(review_session_id);
create index geo_expert_review_changes_disposition_idx on public.geo_expert_review_changes(disposition);

alter table public.geo_expert_review_changes enable row level security;

comment on column public.geo_expert_review_changes.reusability_class is
    'case_specific_assessment=当該Case・時点に紐づく判断（promote先はcase_finding）。'
    'reusable_reasoning=案件を越えて再利用可能な思考方法（promote先はreasoning_rule/mechanism/precedent）。';
comment on column public.geo_expert_review_changes.disposition is
    'pending→promoted/kept_case_only/rejected_as_non_reusable/reinforcedのいずれかへ、'
    'disposition=''pending''を条件にした冪等な更新でのみ遷移する（二重生成防止）。';

create table public.geo_expert_review_change_entities (
    change_id  uuid not null references public.geo_expert_review_changes(change_id) on delete cascade,
    entity_id  uuid not null references public.geo_entities(entity_id) on delete cascade,
    primary key (change_id, entity_id)
);

alter table public.geo_expert_review_change_entities enable row level security;

create table public.geo_expert_review_change_issues (
    change_id  uuid not null references public.geo_expert_review_changes(change_id) on delete cascade,
    issue_id   uuid not null references public.geo_issues(issue_id) on delete cascade,
    primary key (change_id, issue_id)
);

alter table public.geo_expert_review_change_issues enable row level security;

create table public.geo_expert_review_change_notes (
    change_id  uuid not null references public.geo_expert_review_changes(change_id) on delete cascade,
    note_id    uuid not null references public.geo_expert_review_notes(note_id) on delete cascade,
    role       text not null default 'context'
        check (role in ('explicit_reason', 'supporting_comment', 'context')),
    primary key (change_id, note_id)
);

alter table public.geo_expert_review_change_notes enable row level security;

comment on table public.geo_expert_review_change_notes is
    'どのReviewer NoteがどのChange抽出の根拠になったかを追跡する。explicit_reason=専門家が'
    '明示した理由（reasoning_summaryへ優先反映）、supporting_comment=補足、context=背景情報。'
    'extract_deltas側でLLMが返すnote_idは、当該Sessionに実在するIDであることをコード側で'
    '検証してからのみここへ保存する。';

-- geo_knowledge_itemsからの遅延FK追加（テーブル作成順の都合）
alter table public.geo_knowledge_items
    add constraint geo_knowledge_items_derived_from_review_session_id_fkey
        foreign key (derived_from_review_session_id)
        references public.geo_expert_review_sessions(review_session_id) on delete set null,
    add constraint geo_knowledge_items_derived_from_change_id_fkey
        foreign key (derived_from_change_id)
        references public.geo_expert_review_changes(change_id) on delete set null;


-- ---- 6. FTS RPC関数の拡張（retrieval_status等での絞り込み・スコアリングに必要な列を追加）----
-- 戻り値の列構成を変更するためCREATE OR REPLACEでは対応できず、DROP→CREATEする
-- （sql/2026-08-05_geo_phase2a_retrieval_trace.sql末尾のロールバック手順に倣う）。

drop function if exists public.search_geo_knowledge_items(text, text[], text[], boolean, int);

create function public.search_geo_knowledge_items(
    p_query text,
    p_status text[] default null,
    p_knowledge_types text[] default null,
    p_include_demo boolean default false,
    p_limit int default 20,
    p_retrieval_status text[] default null
) returns table (
    knowledge_item_id uuid, title text, summary text, knowledge_type text, status text,
    retrieval_status text, information_class text, origin text, support_count integer,
    last_confirmed_at timestamptz, is_demo boolean, text_score real
)
language sql stable
as $$
    select ki.knowledge_item_id, ki.title, ki.summary, ki.knowledge_type, ki.status,
           ki.retrieval_status, ki.information_class, ki.origin, ki.support_count,
           ki.last_confirmed_at, ki.is_demo,
           ts_rank_cd(ki.search_vector, plainto_tsquery('simple', p_query))::real as text_score
    from public.geo_knowledge_items ki
    where ki.search_vector @@ plainto_tsquery('simple', p_query)
      and (p_status is null or ki.status = any(p_status))
      and (p_knowledge_types is null or ki.knowledge_type = any(p_knowledge_types))
      and (p_retrieval_status is null or ki.retrieval_status = any(p_retrieval_status))
      and (p_include_demo or ki.is_demo = false)
    order by text_score desc
    limit p_limit;
$$;

comment on function public.search_geo_knowledge_items is
    'Full Text SearchのTop-KをDB側で計算する。Phase2B-2でretrieval_status/information_class/'
    'origin/support_count/last_confirmed_atを追加（二軸フィルタ・Internal Expert Knowledgeの'
    'スコアリングに使用するため）。';

drop function if exists public.search_geo_claims(text, boolean, int);

create function public.search_geo_claims(
    p_query text,
    p_include_demo boolean default false,
    p_limit int default 20,
    p_retrieval_status text[] default null
) returns table (
    claim_id uuid, claim_text text, information_class text, retrieval_status text,
    is_demo boolean, text_score real
)
language sql stable
as $$
    select c.claim_id, c.claim_text, c.information_class, c.retrieval_status, c.is_demo,
           ts_rank_cd(c.search_vector, plainto_tsquery('simple', p_query))::real as text_score
    from public.geo_claims c
    where c.search_vector @@ plainto_tsquery('simple', p_query)
      and (p_retrieval_status is null or c.retrieval_status = any(p_retrieval_status))
      and (p_include_demo or c.is_demo = false)
    order by text_score desc
    limit p_limit;
$$;

comment on function public.search_geo_claims is
    'Full Text SearchのTop-KをDB側で計算する。Phase2B-2でretrieval_statusを追加。';


-- ---- 7. 既存データへのbackfill ----

-- geo_knowledge_items: statusから初期retrieval_statusを導出（geo_knowledge_service.pyの
-- 初回遷移時マッピングと同一の対応表）
update public.geo_knowledge_items set retrieval_status = case status
    when 'active' then 'available'
    when 'contested' then 'available'
    when 'candidate' then 'restricted'
    when 'in_review' then 'restricted'
    when 'rejected' then 'archived'
    when 'superseded' then 'deprecated'
    when 'deprecated' then 'deprecated'
    when 'archived' then 'archived'
    else 'restricted'
end;
-- origin列は既存行に対して推定せずNULLのまま（危険な自動推定をしない）

-- geo_claims: origin_typeに関わらずsource_idを解決できるものはすべて解決する
-- (1) origin_type='document' → geo_documents.source_id
update public.geo_claims c set source_id = d.source_id
    from public.geo_documents d
    where c.origin_type = 'document' and c.origin_id = d.document_id and c.source_id is null;

-- (2) origin_type='event' → geo_evidence(event_id一致)のいずれか1件のdocument_id経由
--     （1 eventに複数Evidence文書がある場合はあいまいさが残るため、代表として最初の1件を採用する）
update public.geo_claims c set source_id = sub.source_id
    from (
        select distinct on (ev.event_id) ev.event_id, d.source_id
        from public.geo_evidence ev
        join public.geo_documents d on d.document_id = ev.document_id
        order by ev.event_id, ev.created_at asc
    ) sub
    where c.origin_type = 'event' and c.origin_id = sub.event_id and c.source_id is null;

-- (3) origin_type='observation' → geo_observations.evidence_id → geo_evidence.document_id
update public.geo_claims c set source_id = sub.source_id
    from (
        select o.observation_id, d.source_id
        from public.geo_observations o
        join public.geo_evidence ev on ev.evidence_id = o.evidence_id
        join public.geo_documents d on d.document_id = ev.document_id
    ) sub
    where c.origin_type = 'observation' and c.origin_id = sub.observation_id and c.source_id is null;

-- retrieval_statusの確定: 一旦全件restrictedにし、source_id解決分はSource Policyへ、
-- 未解決かつmanual/case originのみavailableへ
update public.geo_claims set retrieval_status = 'restricted';

update public.geo_claims c set retrieval_status = sr.reference_availability
    from public.geo_source_registry sr
    where c.source_id = sr.source_id;

update public.geo_claims set retrieval_status = 'available'
    where source_id is null and origin_type in ('manual', 'case');
