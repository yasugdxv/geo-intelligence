-- 地政学アプリ Closeout Phase4: Report Review → Knowledge Learning Loop
--
-- 既存のCase Draft-vs-Final Expert Review Loop（geo_expert_review_sessions/changes、
-- Phase2B-2）を、Report（geo_reports）のレビューにも使えるよう拡張する。新しいKnowledge
-- システムは作らず、既存geo_knowledge_itemsへ"report_finding"（既存case_findingと対になる
-- 型）を1つ追加するのみ。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは実行しない）。
-- 実DB確認済み: 2026-08-29時点でgeo_expert_review_sessionsの既存行は0件のため、
-- 「case_id/report_idのどちらか一方のみ」CHECK制約はNOT VALIDにせず、そのまま検証込みで
-- 追加してよい。

-- ---- 1. geo_expert_review_sessionsへreport_id列を追加 ----

alter table public.geo_expert_review_sessions
    add column if not exists report_id uuid references public.geo_reports(report_id) on delete set null;

create index if not exists geo_expert_review_sessions_report_id_idx
    on public.geo_expert_review_sessions(report_id);

-- review_typeへ'report_review'を追加する。
alter table public.geo_expert_review_sessions
    drop constraint if exists geo_expert_review_sessions_review_type_check;
alter table public.geo_expert_review_sessions
    add constraint geo_expert_review_sessions_review_type_check
        check (review_type in ('case_report', 'executive_brief', 'other', 'report_review'));

-- case_id/report_idは「どちらか一方のみ」（Service層でも二重に検証する）。
alter table public.geo_expert_review_sessions
    add constraint geo_expert_review_sessions_exactly_one_subject_check
        check (num_nonnulls(case_id, report_id) = 1);

comment on column public.geo_expert_review_sessions.report_id is
    'Report Review Loop（Closeout Phase4）: レビュー対象がReportの場合、レビュー確定後の新Version
     （v2）のgeo_reports.report_idを指す（v1ではない。v1はv2.supersedes_id経由で追跡する）。
     case_idとreport_idは排他（どちらか一方のみ設定可能）';

-- ---- 2. geo_knowledge_items.knowledge_typeへ"report_finding"を追加 ----

alter table public.geo_knowledge_items
    drop constraint if exists geo_knowledge_items_knowledge_type_check,
    add constraint geo_knowledge_items_knowledge_type_check
        check (knowledge_type in (
            'country_foundation', 'actor_profile', 'party_profile',
            'relationship_assessment', 'historical_path_dependency', 'precedent',
            'mechanism', 'reasoning_rule', 'indicator', 'foundation_update',
            'case_finding', 'leverage_card',
            'actor_profile_update', 'relationship_update', 'dependency_update', 'sanctions_update',
            'report_finding'
        ));
