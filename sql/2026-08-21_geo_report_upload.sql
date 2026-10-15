-- =====================================================================
-- 地政学インテリジェンス基盤 Phase4: Internal Report Upload → Knowledge化 →
-- Intelligence Need Resolve
--
-- 既存geo_documents（Document ingestion）・geo_document_chunks・geo_claims・
-- geo_knowledge_gaps（Phase3で拡張済み）をそのまま再利用する。新しい文書基盤・
-- 新しい検索エンジンは作らない。geo_reportsはgeo_documentsの1:1メタデータ拡張のみ。
-- 適用順序: このファイル1本のみ（Phase1〜Phase3のmigrationが適用済みであることが前提）。
-- =====================================================================

-- ---- 1. geo_reports: geo_documentsの1:1メタデータ拡張 ----
-- title/as_of(published_at)/theme_id等、既存geo_documentsで保持できる項目は重複させない。
create table public.geo_reports (
    report_id        uuid primary key default gen_random_uuid(),
    document_id      uuid not null unique references public.geo_documents(document_id) on delete cascade,
    author           text,
    owner_department text,
    report_type      text,
    country_region   jsonb not null default '[]'::jsonb,
    topics           jsonb not null default '[]'::jsonb,
    confidentiality  text not null default 'internal'
        check (confidentiality in ('public', 'internal', 'restricted', 'confidential')),
    report_kind      text not null default 'internal_report'
        check (report_kind in ('internal_report', 'external_report')),
    uploaded_by      text,
    uploaded_at      timestamptz not null default now(),
    is_demo          boolean not null default false
);

create index geo_reports_document_idx on public.geo_reports(document_id);
create index geo_reports_kind_idx on public.geo_reports(report_kind);

alter table public.geo_reports enable row level security;

comment on table public.geo_reports is
    '地政学担当者がアップロードしたReportの、geo_documentsだけでは表せないメタデータ。'
    'report_kindでinternal_report（社内専門家作成）とexternal_report（外部入手レポートの'
    '手動投入）を識別する。Ingestion（Document保存・Chunk化・Claim化）は既存機構をそのまま使う。';

-- ---- 2. geo_report_gaps: Report↔Intelligence Need（geo_knowledge_gaps） 多対多リンク ----
create table public.geo_report_gaps (
    report_gap_id  uuid primary key default gen_random_uuid(),
    report_id      uuid not null references public.geo_reports(report_id) on delete cascade,
    gap_id         uuid not null references public.geo_knowledge_gaps(gap_id) on delete cascade,
    created_at     timestamptz not null default now(),
    unique (report_id, gap_id)
);

create index geo_report_gaps_report_idx on public.geo_report_gaps(report_id);
create index geo_report_gaps_gap_idx on public.geo_report_gaps(gap_id);

alter table public.geo_report_gaps enable row level security;

comment on table public.geo_report_gaps is
    'Report Upload時、または Intelligence Need詳細画面のAdd Resolution Reportから'
    '「どのIntelligence Needに対するReportか」を紐付けるリンクテーブル。1件のReportが'
    '複数のIntelligence Needを解決することを許容する。';

-- ---- 3. geo_knowledge_gaps: Resolve Workflowに必要な列を追加（Phase3のresolved_at/
--         resolution_report_idは既存、resolved_by/resolution_noteを追加） ----
alter table public.geo_knowledge_gaps
    add column if not exists resolved_by text,
    add column if not exists resolution_note text;

comment on column public.geo_knowledge_gaps.resolved_by is
    'resolved状態にした人（analyst名/id）。resolved_at/resolution_report_idと同時に設定する'
    '（Statusだけをresolvedにして、Reportと紐付かない状態にはしない。dismissed（調査不要）は例外）。';
