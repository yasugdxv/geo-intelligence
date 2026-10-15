-- 地政学アプリ Closeout Phase3: Report Update/Versioning
--
-- geo_knowledge_itemsと同じ考え方（version整数カウンタ＋supersedes_idで前バージョンを参照）を
-- geo_reportsへ適用する。同一「論理Report」の複数Versionをまとめるためのreport_identity_idを
-- 追加する（最初のVersionはreport_identity_id=自分自身のreport_id）。
--
-- 重要: メタデータのみのVersion（新ファイルなし、同じdocument_idを再利用するVersion）は、
-- 既存のdocument_id単独UNIQUE制約と矛盾する（同じdocument_idを持つgeo_reports行が複数になり得る
-- ため）。単独UNIQUEを外し、(report_identity_id, version)の複合UNIQUEへ置き換える。
-- document_id自体のFK/NOT NULLは維持する（冪等性の担保はDB制約からService層
-- （geo_report_service.ingest_report/create_report_version）の責任へ移す）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは実行しない）。

-- ---- 1. Versioning列の追加 ----

alter table public.geo_reports
    add column if not exists version integer not null default 1,
    add column if not exists supersedes_id uuid references public.geo_reports(report_id) on delete set null,
    add column if not exists report_identity_id uuid;

-- 既存行（Versioning導入前にingestされたReport）は、自分自身をroot（v1）とみなしてbackfillする。
update public.geo_reports set report_identity_id = report_id where report_identity_id is null;
alter table public.geo_reports alter column report_identity_id set not null;

create index if not exists geo_reports_report_identity_id_idx on public.geo_reports(report_identity_id);

-- ---- 2. document_id単独UNIQUE → (report_identity_id, version)複合UNIQUEへ置き換え ----
--
-- 元のCREATE TABLE（sql/2026-08-21_geo_report_upload.sql）はinline `unique` で定義していたため、
-- Postgresの既定命名規則により制約名は geo_reports_document_id_key になっているはずである。
-- 適用前に実際の制約名を確認すること（Supabase Dashboard: Table Editor > geo_reports > 制約一覧、
-- または `select conname from pg_constraint where conrelid = 'public.geo_reports'::regclass;`）。
-- 名前が異なる場合は下記のDROP CONSTRAINT行を実際の名前に置き換えてから適用する。

alter table public.geo_reports drop constraint if exists geo_reports_document_id_key;

alter table public.geo_reports
    add constraint geo_reports_identity_version_key unique (report_identity_id, version);
alter table public.geo_reports
    add constraint geo_reports_version_check check (version >= 1);

comment on column public.geo_reports.version is
    'Report Versioning（Closeout Phase3）: 同一report_identity_id内での通し番号。v1から開始';
comment on column public.geo_reports.supersedes_id is
    'このVersionが置き換えた直前のgeo_reports.report_id（v1はNULL）';
comment on column public.geo_reports.report_identity_id is
    '同一「論理Report」の全Versionが共有する識別子（v1作成時に自分自身のreport_idを設定）';
