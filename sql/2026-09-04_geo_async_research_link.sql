-- Phase B-C/B-D: Intelligence AI / Expert AI Role Separation
--
-- 他部門からのCross-Department Inquiry（geo_knowledge_gaps.origin_type='external_app_request'）で
-- Knowledgeが不足する場合、Expert AIをHTTP同期経路では呼ばず、Gap記録→非同期Research（
-- run_research_request）→candidate Knowledge作成→Human Review→active昇格、という既存
-- candidate/active lifecycleに乗せた学習ループへ切り替える。
--
-- 新規テーブルは追加しない。既存geo_knowledge_gaps/geo_expert_responses/geo_knowledge_itemsの
-- 拡張のみで対応する:
--   1. geo_knowledge_gapsへ1列追加: そのGapに対して「現在採用されているExpert Research
--      Response」への参照（実行履歴そのものはgeo_expert_requests/geo_expert_responsesに残る。
--      本列はRetry/失敗履歴を保持する場所ではなく、あくまで現在の採用結果1件だけを指す。
--      Human ReviewでRejectされた場合はNULLへ戻す、reopen_research_gap_after_rejection参照）。
--   2. geo_knowledge_items.originのCHECK制約へ1値追加: 非同期Research由来のcandidate
--      Knowledge Itemを既存originの中から選ばず（既存5値はいずれも意味が微妙にずれるため）
--      新設する。
--
-- 適用前preflight（2026-09-04実施済み、本番Supabaseに対する読み取り専用SELECTで確認）:
--   - geo_knowledge_items.origin実データ分布: manual(108), NULL(7), case_candidate_extraction(6)
--     → 新CHECK制約（既存5値+async_expert_research）はいずれも許容する。NULLはCHECK制約の
--     対象外（Postgres仕様）のため無条件で安全。
--   - geo_knowledge_gaps.gap_id / geo_expert_responses.response_id はいずれもuuid型で一致。
--   - 適用直前のベースライン件数: geo_knowledge_items=121件、geo_knowledge_gaps=24件
--     （open=21, resolved=1, duplicate_candidate=2）、geo_expert_responses=160件
--     （本migrationはこれらの件数・status分布を変更しない。適用後に再SELECTして一致を確認する）。

begin;

alter table public.geo_knowledge_gaps
    add column if not exists resolution_expert_response_id uuid
        references public.geo_expert_responses(response_id) on delete set null;

comment on column public.geo_knowledge_gaps.resolution_expert_response_id is
    'このGapに対して現在採用されているExpert Research Responseへの参照（1件のみ、履歴ではない）。'
    'Research完了時点で設定するが、Gap自体のstatus=resolvedはHuman ReviewでKnowledgeがactiveへ'
    '昇格した時点まで別に管理する（run_research_request()は本列の設定までが責務、resolvedへの'
    '遷移はresolve_intelligence_need_from_promoted_knowledge()が別途行う。Rejectされた場合は'
    'reopen_research_gap_after_rejection()がNULLへ戻す）。';

alter table public.geo_knowledge_items drop constraint if exists geo_knowledge_items_origin_check;
alter table public.geo_knowledge_items
    add constraint geo_knowledge_items_origin_check
        check (origin in (
            'manual', 'connector_derived', 'case_candidate_extraction',
            'external_claim_extraction', 'internal_expert_review',
            'async_expert_research'
        ));

comment on constraint geo_knowledge_items_origin_check on public.geo_knowledge_items is
    'async_expert_research: Phase B-C、Cross-Department Inquiryの非同期Research Request'
    '（run_research_request）が生成したcandidate。既存candidate→review→active lifecycleを'
    'そのまま使う（本Originだからといって承認プロセスを迂回しない）。';

commit;

-- ---- 適用後のSchema Verification（そのままSQL Editorで実行して結果を確認する） ----
--
-- 1. 新列が正しい型・FKで追加されたか
-- select column_name, data_type, is_nullable
--   from information_schema.columns
--  where table_name = 'geo_knowledge_gaps' and column_name = 'resolution_expert_response_id';
--
-- select conname, pg_get_constraintdef(oid)
--   from pg_constraint
--  where conrelid = 'public.geo_knowledge_gaps'::regclass
--    and conname like '%resolution_expert_response_id%';
--
-- 2. 新CHECK制約の中身が想定通りか
-- select conname, pg_get_constraintdef(oid)
--   from pg_constraint
--  where conrelid = 'public.geo_knowledge_items'::regclass
--    and conname = 'geo_knowledge_items_origin_check';
--
-- 3. 既存データの件数・status分布がmigration前後で変わっていないか
--    （上記preflightコメントの件数と一致するはず: 121 / 24(open21,resolved1,duplicate_candidate2) / 160）
-- select count(*) from public.geo_knowledge_items;
-- select status, count(*) from public.geo_knowledge_gaps group by status order by status;
-- select count(*) from public.geo_expert_responses;
--
-- 4. 既存originデータが引き続き読めるか（新CHECK制約による既存行への影響が無いことの最終確認）
-- select origin, count(*) from public.geo_knowledge_items group by origin order by origin;
