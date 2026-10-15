-- =====================================================================
-- Geopolitical Intelligence OS Phase E: Approved Knowledge Publication + Provenance列
--
-- 既存geo_knowledge_items / geo_ios_approval_eventsへの追加型ALTERのみ。新規テーブルは
-- 作らない（既存Publication/Approval機構を最大限再利用する方針、docs不要なmigration回避）。
-- destructive文（drop table/drop column）は含まない。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは実行しない、
-- 既存運用を踏襲）。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. knowledge_typeへ"approved_report"を追加（既存21種は変更しない）
--    Report Studio/外部Uploadで正式承認されたReport全体を表すKnowledge Type。
--    既存report_finding（Case由来の教訓・要約型）とは別物——report_findingは「Reportから
--    得られた再利用可能な知見の要約」、approved_reportは「承認されたReportそのものの
--    Publication記録」（本文全体ではなくsyntheses要約+参照を保持、schemaはgeo_knowledge_item_
--    schemas.pyのAPPROVED_REPORT_SCHEMA参照）。
-- ---------------------------------------------------------------------
alter table public.geo_knowledge_items
    drop constraint if exists geo_knowledge_items_knowledge_type_check,
    add constraint geo_knowledge_items_knowledge_type_check
        check (knowledge_type in (
            'country_foundation', 'actor_profile', 'party_profile',
            'relationship_assessment', 'historical_path_dependency', 'precedent',
            'mechanism', 'reasoning_rule', 'indicator', 'foundation_update',
            'case_finding', 'leverage_card',
            'actor_profile_update', 'relationship_update', 'dependency_update', 'sanctions_update',
            'report_finding',
            'media_profile', 'media_episode', 'media_pattern',
            'institution_profile',
            'approved_report'
        ));

-- ---------------------------------------------------------------------
-- 2. Provenance列（§7）: Knowledge Item → Approved Report Version → Approval Event。
--    Route A（Report Studio Project由来）とRoute B（既存geo_reports Upload由来）の
--    両方をカバーする（どちらか一方のみ設定される想定、Service層で相互排他を検証する）。
-- ---------------------------------------------------------------------
alter table public.geo_knowledge_items
    add column if not exists source_report_project_id uuid
        references public.geo_report_projects(project_id) on delete set null,
    add column if not exists source_report_version_label text,
    add column if not exists source_approval_event_id uuid
        references public.geo_ios_approval_events(approval_event_id) on delete set null,
    add column if not exists source_report_id uuid
        references public.geo_reports(report_id) on delete set null;

create index if not exists geo_knowledge_items_source_report_project_idx
    on public.geo_knowledge_items(source_report_project_id);
create index if not exists geo_knowledge_items_source_approval_event_idx
    on public.geo_knowledge_items(source_approval_event_id);
create index if not exists geo_knowledge_items_source_report_idx
    on public.geo_knowledge_items(source_report_id);

comment on column public.geo_knowledge_items.source_report_project_id is
    'Route A: 発生源のReport Studio Project（geo_report_projects）。Provenance chain '
    '（Knowledge Item → Report Project → Analysis Session → Selected Scenario → CQAS Candidates）'
    'を辿るための起点、§15/§7。';
comment on column public.geo_knowledge_items.source_approval_event_id is
    'Route A: このKnowledge Itemを発生させたgeo_ios_approval_events行。Approval無しで'
    'Official Knowledgeにしない、を機械的に裏付ける参照（§4の絶対条件）。';
comment on column public.geo_knowledge_items.source_report_id is
    'Route B: 発生源の既存geo_reports（Upload型）行。Analysis Session/CQAS provenanceを'
    '持たない外部承認済みReportの場合のみ設定する（§20）。';

-- ---------------------------------------------------------------------
-- 3. geo_ios_approval_eventsへPublication状態を追加（§19: approved/publication_pending/
--    publishedを分離し、監査可能性を高める。ApprovalとPublicationは同一Transactionに
--    しない）。
-- ---------------------------------------------------------------------
alter table public.geo_ios_approval_events
    add column if not exists publication_status text not null default 'not_published'
        check (publication_status in ('not_published', 'pending', 'published')),
    add column if not exists published_knowledge_item_id uuid
        references public.geo_knowledge_items(knowledge_item_id) on delete set null;

create index if not exists geo_ios_approval_events_publication_status_idx
    on public.geo_ios_approval_events(publication_status);

comment on column public.geo_ios_approval_events.publication_status is
    '§19: このApproval EventがOfficial Knowledgeとして公開済みかどうか。approval_status='
    'approvedであってもpublication_status=not_publishedのままのことがある（PublishはApproval'
    'とは独立した明示操作、§19）。';
