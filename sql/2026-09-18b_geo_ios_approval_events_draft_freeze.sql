-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): Approval Eventへの
-- Draft Freeze追跡列（sql/2026-09-18_geo_ios_native_report_drafts.sqlの追補）
--
-- Native経路のApprovalは、Current Draftを再構築せず、Expert Review申請時点で
-- geo_ios_review_cyclesにFreezeされたsource_report_draft_id/source_draft_version/
-- source_content_hashをそのままコピーして使う。承認後にどのDraft versionを
-- 承認したかを一意に追跡できるよう、Approval Event自身にも同じ列を残す。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する。destructive文は含まない。
-- =====================================================================

alter table public.geo_ios_approval_events
    add column source_report_draft_id uuid
        references public.geo_ios_report_drafts(report_draft_id) on delete set null,
    add column source_draft_version integer,
    add column source_content_hash text;

comment on column public.geo_ios_approval_events.source_report_draft_id is
    'Native経路: 承認対象として選定したReview Cycle(geo_ios_review_cycles)からそのまま'
    'コピーした値。Report Studio経路(legacy)ではnullのまま。Current Draftからの再構築は'
    '行わない（Approvalは常にExpert ReviewでFreezeされた内容のみを見る）。';
comment on column public.geo_ios_approval_events.source_draft_version is
    'Freeze時点のgeo_ios_report_drafts.version（source_report_draft_idと同時に設定）。';
comment on column public.geo_ios_approval_events.source_content_hash is
    'Freeze時点のcontent_hash（コピーのみ、再計算しない）。';
