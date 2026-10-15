-- 地政学インテリジェンス基盤 Phase 2D: レポート登録（Direct Report Registration）
--
-- 既存geo_reports（geo_documentsの1:1メタデータ拡張、Phase4 Report Upload）へ、
-- AI Metadata Extractionが埋める追加フィールドのみを列追加する。新しいテーブルは作らない
-- （title/report_date/region/language/keywordsは既存geo_documents.title/published_at/
-- language、geo_reports.country_region/topicsをそのまま使うため重複列を増やさない。
-- ここで追加するのは既存カラムで表現できない項目のみ）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは実行しない、
-- 既存migrationと同じ運用）。

alter table public.geo_reports
    add column if not exists key_actors jsonb not null default '[]'::jsonb,
    add column if not exists keywords jsonb not null default '[]'::jsonb,
    add column if not exists summary text,
    add column if not exists business_context text,
    -- registration_source: このReport行がどの経路で登録されたかを示す（例:
    -- "direct_registration"=Phase 2Dのレポート登録フロー、null=従来のUpload Reportダイアログ
    -- 等、既存の経路から引き続き作られた行）。過去データを推測で埋めないため、既存行は
    -- nullのままにする。
    add column if not exists registration_source text;

comment on column public.geo_reports.key_actors is
    'AI Metadata Extraction（Phase 2D、レポート登録）が抽出したKey Actor名のリスト。'
    '本文に明示されている場合のみ埋める（推測しない）。ユーザーが確認・修正できる。';
comment on column public.geo_reports.keywords is
    'AI Metadata Extractionが抽出したキーワード。country_region/topics（既存Issue解決用）'
    'とは別に、本文由来の自由なキーワードタグとして持つ。';
comment on column public.geo_reports.summary is
    'AI Metadata Extractionが生成したレポート要約。ユーザーが確認・修正できる。';
comment on column public.geo_reports.business_context is
    '当社との潜在的接点等のBusiness Context。本文から取れる場合のみAIが埋める。';
comment on column public.geo_reports.registration_source is
    'この行を作った登録経路（例: "direct_registration"）。過去データは移行時点でnullのまま。';
