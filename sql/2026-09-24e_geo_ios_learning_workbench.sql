-- =====================================================================
-- Geopolitical Intelligence OS: Learning Workbench / Learning Management
--
-- 専門家レビュー前後のBefore/After差分・レビュー指摘からExpert AIを継続的に
-- 育成するための「人間が開いて・比較して・AIと相談しながら編集し・承認・
-- 公開する」ワークフロー層。既存のLearning Candidate抽出
-- （geo_ios_learning_extraction_service.py）・Dedup判定・Human Gate #1/#2
-- （geo_ios_learning_candidate_service.py / geo_knowledge_service.py）・
-- Knowledge Publication/Retrievalには一切手を加えず、その上に編集・承認
-- ワークフロー層を1枚足すだけ（ユーザー指示、2026-09-24、3回のPlan Mode
-- レビューで確定）。Expert AI本体（geo_expert_engine.py/geo_expert_schema.py）・
-- CQAS・Reading Portfolioには触れない。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない。geo_db_clientにマイグレーションランナーは存在せず、これが本リポジトリの
-- 既存運用）。destructive文（drop table/drop column）は含まない。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. geo_ios_learning_candidatesへの列追加
--
-- 既存status列(candidate/approved/rejected/superseded)はpromote_to_
-- knowledge_item/_validate_active_lineageが参照する意味を持つため、
-- workbench_statusは別軸として追加する（同じ意味を持たせない）。
-- ---------------------------------------------------------------------

alter table public.geo_ios_learning_candidates
    add column workbench_status text not null default 'pending'
        check (workbench_status in
            ('pending', 'editing', 'ready_for_approval', 'approved', 'published', 'rejected')),
    add column generalization_risk text
        check (generalization_risk in ('low', 'medium', 'high')),
    add column generalization_risk_reason text,
    add column target_knowledge_type text,
    add column dedup_verdict text
        check (dedup_verdict in ('NEW', 'DUPLICATE', 'UPDATE', 'CONFLICT', 'RELATED')),
    add column dedup_target_knowledge_item_id uuid references public.geo_knowledge_items(knowledge_item_id),
    add column dedup_related_knowledge_item_ids uuid[],
    add column dedup_conflict_resolution_note text,
    add column human_visibility text,
    add column agent_sharing_policy text;

create index geo_ios_learning_candidates_workbench_status_idx
    on public.geo_ios_learning_candidates(workbench_status);

comment on column public.geo_ios_learning_candidates.workbench_status is
    $$Learning Workbenchの編集ワークフロー状態（既存statusとは別軸）。遷移はgeo_ios_learning_workbench_service.py内の専用関数のみが行う（pending→editing→ready_for_approval→approved→published、または各段階からrejected）。UIから任意状態への直接書き換えは行わない。$$;
comment on column public.geo_ios_learning_candidates.dedup_verdict is
    $$人間が確定させたDedup最終判断。既存の自動判定trigger_context.dedup_status(new/duplicate/possible_conflict_or_overlap)とは別物——そちらは無変更。DUPLICATE/CONFLICTのcandidateはPublish不可（CONFLICTはresolution_noteの有無に関わらず不可、人間がverdict自体を変更するまで解除されない）。$$;
comment on column public.geo_ios_learning_candidates.human_visibility is
    $$geo_knowledge_items作成時に使う公開範囲の現在値（Revision snapshotは履歴、これはCurrent State）。未設定のままPublishしようとした場合は拒否する（既定値company/geopolitics_onlyへのフォールバックはしない）。$$;

-- ---------------------------------------------------------------------
-- 2. Migration Backfill（重要: 既存candidateを一律pendingにしない）
--
-- 列をdefault 'pending'付きで追加すると、既にapproved/rejected/Knowledge化
-- 済みの既存candidateまで一律pendingになってしまう。列追加直後に以下の
-- backfillを行う。
-- ---------------------------------------------------------------------

update public.geo_ios_learning_candidates
    set workbench_status = 'rejected'
    where status = 'rejected';

update public.geo_ios_learning_candidates
    set workbench_status = 'published'
    where status = 'approved'
      and approved_knowledge_item_id in (
          select knowledge_item_id from public.geo_knowledge_items
          where status = 'active' and retrieval_status = 'available'
      );

update public.geo_ios_learning_candidates
    set workbench_status = 'approved'
    where status = 'approved' and workbench_status <> 'published';

-- 残り(status='candidate'で未処理のもの)はdefault値'pending'のまま。

-- ---------------------------------------------------------------------
-- 3. 版履歴（追記式）
-- ---------------------------------------------------------------------

create table public.geo_ios_learning_candidate_revisions (
    revision_id             uuid primary key default gen_random_uuid(),
    candidate_id            uuid not null references public.geo_ios_learning_candidates(candidate_id)
                                on delete cascade,
    revision_number         integer not null,
    revised_by              text not null check (revised_by in ('ai', 'human')),
    workbench_turn_id       uuid,
    -- revision_snapshot: その時点の編集可能項目を丸ごと保存する。新しい編集可能項目が
    -- 増えても個別列を追加せずこのjsonbへ足すだけで済むようにする（2回目レビュー⑦）。
    -- 想定キー: generalized_rule/observation/scope/confidence/generalization_risk/
    -- generalization_risk_reason/candidate_type/target_knowledge_type/dedup_verdict/
    -- dedup_target_knowledge_item_id/dedup_related_knowledge_item_ids/
    -- dedup_conflict_resolution_note/human_visibility/agent_sharing_policy
    revision_snapshot       jsonb not null default '{}'::jsonb,
    change_summary          text,
    created_by              text not null,
    created_at              timestamptz not null default now(),
    unique (candidate_id, revision_number)
);

create index geo_ios_learning_candidate_revisions_candidate_idx
    on public.geo_ios_learning_candidate_revisions(candidate_id, revision_number);

alter table public.geo_ios_learning_candidate_revisions enable row level security;

comment on table public.geo_ios_learning_candidate_revisions is
    $$Learning Candidateの編集履歴（追記式、削除・上書きしない）。candidate本体の現在値はgeo_ios_learning_candidatesの該当列にあり、このテーブルは閲覧用の履歴のみ。revision_number=1は常にInitial Extraction（geo_ios_learning_extraction_service.extract_learning_candidates自身がcandidate insert直後に書き込む）、revision_number=2は通常Generalization Risk Assessment。unique(candidate_id, revision_number)制約があるため、採番はgeo_ios_learning_workbench_service._allocate_next_revisionのみを経由すること（個別にrevision_numberを計算しない）。$$;

-- ---------------------------------------------------------------------
-- 4. 編集Chat（既存geo_ios_consultation_turnsと同じpending/completed/failed
--    ポーリング形状、candidate_idでスコープ）
-- ---------------------------------------------------------------------

create table public.geo_ios_learning_workbench_turns (
    workbench_turn_id       uuid primary key default gen_random_uuid(),
    candidate_id            uuid not null references public.geo_ios_learning_candidates(candidate_id)
                                on delete cascade,
    human_instruction       text not null,
    -- base_revision_number: この指示を送った時点でcandidateが最新だったrevision_number
    -- （楽観ロック用、2回目レビュー⑤）。AI完了時にcandidateの現在最新revision_numberと
    -- 比較し、不一致なら新revisionを書かずstatus='stale'にする（自動上書きしない）。
    base_revision_number    integer not null,
    status                  text not null default 'pending'
                                check (status in ('pending', 'completed', 'failed', 'stale')),
    resulting_revision_id   uuid references public.geo_ios_learning_candidate_revisions(revision_id),
    ai_response_text        text,
    error_message           text,
    created_by              text not null,
    created_at              timestamptz not null default now(),
    completed_at            timestamptz
);

create index geo_ios_learning_workbench_turns_candidate_idx
    on public.geo_ios_learning_workbench_turns(candidate_id, created_at);

alter table public.geo_ios_learning_workbench_turns enable row level security;

alter table public.geo_ios_learning_candidate_revisions
    add constraint geo_ios_learning_candidate_revisions_turn_fk
    foreign key (workbench_turn_id) references public.geo_ios_learning_workbench_turns(workbench_turn_id);

comment on table public.geo_ios_learning_workbench_turns is
    $$Learning Candidate編集Chatの対話ログ（1行=1往復）。既存geo_ios_consultation_turnsと同じpending/completed/failedポーリングパターンだが、Expert AI本体は経由しない（geo_llm_common.call_llm_structuredを直接使う軽量な専用prompt）。$$;
