-- 地政学インテリジェンス基盤 Phase 2E: Report / Knowledge Confidentiality & Retrieval
-- Access Control。
--
-- 既存geo_knowledge_access_policy（human_visibility/agent_sharing_policy、Phase B/E）の
-- 語彙・fail-closed思想をそのまま再利用し、新しい権限基盤は作らない。ここで追加するのは
-- 既存"custom"値に実際の意味（allowed_org_units/allowed_domainsとの照合）を持たせるための
-- 列と、geo_reportsへの同じ2軸の展開のみ。
--
-- UI Label（部内限り/SHD限り/グループ配信可）は、これらの列へ以下のように解決する
-- （geo_knowledge_access_policy.resolve_ui_label_to_policy参照、テーブルには"UI Label"
-- という列は作らない——Label→Policyの変換はService層の責務、§4）:
--   DEPARTMENT_ONLY   → human_visibility='custom', allowed_org_units=[owner_department],
--                       agent_sharing_policy='none'
--   SHD_ONLY          → human_visibility='custom', allowed_org_units=<SHD所属部門一覧>,
--                       agent_sharing_policy='custom', allowed_domains=<SHD所属domain一覧>
--   GROUP_SHAREABLE   → human_visibility='company',
--                       agent_sharing_policy='custom', allowed_domains=<許可グループのdomain一覧>
--
-- 2026-09-28時点でSHD/グループ配信可のallowed_org_units・allowed_domainsの実体（具体的な
-- 部門名・domain値）は未確定（ユーザー判断: 空リストで実装し、後日設定する）。空リストの間は
-- fail-closed（owner本人以外は誰もアクセスできない）——「分からないから広く許可する」を
-- 避けるための意図的な安全側動作であり、バグではない。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは実行しない、
-- 既存migrationと同じ運用）。destructive文（drop table/drop column/既存データのdelete）は
-- 含まない。

-- ---- 1. geo_knowledge_items: custom visibility/sharingの実体列 -----------------
alter table public.geo_knowledge_items
    add column if not exists allowed_org_units jsonb not null default '[]'::jsonb,
    add column if not exists allowed_domains jsonb not null default '[]'::jsonb;

comment on column public.geo_knowledge_items.allowed_org_units is
    'Phase 2E: human_visibility=''custom''の場合にのみ参照する、アクセスを許可する部門名の'
    'リスト（geo_ios_identities.departmentと照合）。空配列=custom指定だが未設定=fail-closed'
    '（作成者本人のみ許可）。';
comment on column public.geo_knowledge_items.allowed_domains is
    'Phase 2E: agent_sharing_policy=''custom''の場合にのみ参照する、アクセスを許可する'
    'caller_domainのリスト（geo_intelligence_auth.CallerIdentity.caller_domainと照合）。'
    '空配列=custom指定だが未設定=fail-closed（他Domain Agentは一切許可しない）。';

-- ---- 2. geo_reports: Knowledge Itemと同じ2軸のAccess Policyを展開 --------------
-- 既存geo_reports.confidentiality（public/internal/restricted/confidential）は
-- このAccess ControlのSource of Truthにしない。削除もしない（Legacy metadataとして保持）。
alter table public.geo_reports
    add column if not exists human_visibility text not null default 'custom'
        check (human_visibility in ('private', 'project', 'geopolitics_team', 'department',
                                     'company', 'custom')),
    add column if not exists agent_sharing_policy text not null default 'none'
        check (agent_sharing_policy in ('none', 'geopolitics_only', 'allowed_cross_domain', 'custom')),
    add column if not exists allowed_org_units jsonb not null default '[]'::jsonb,
    add column if not exists allowed_domains jsonb not null default '[]'::jsonb;

comment on column public.geo_reports.human_visibility is
    'Phase 2E: Report Access Policy（人間軸）。既定''custom''+allowed_org_units未設定=DENY'
    '（作成者/uploaded_by本人のみ許可、fail-closed）。新規登録（/geo/report-registration、'
    'Review Flow Publish）では3つのUI Label経由で明示的に設定することを必須とする。';
comment on column public.geo_reports.agent_sharing_policy is
    'Phase 2E: Report Access Policy（Agent軸）。既定''none''=他Domain Agentからは一切'
    '取得不可（fail-closed）。';
comment on column public.geo_reports.allowed_org_units is
    'human_visibility=''custom''の場合に参照する許可部門リスト。';
comment on column public.geo_reports.allowed_domains is
    'agent_sharing_policy=''custom''の場合に参照する許可caller_domainリスト。';

-- ---- 3. 既存Report（Phase2D以前含む）の移行方針 ----------------------------------
-- §16 Existing Reports Migration: 「B. owner department inherited」を採用する
-- （owner_departmentが既に入力されている行のみ、その部門を許可対象として引き継ぐ。
-- owner_departmentが空の行は空配列のまま=作成者本人のみ、最も安全側）。推測でのmapping・
-- 一律company/group展開は行わない。
update public.geo_reports
    set allowed_org_units = jsonb_build_array(owner_department)
    where (owner_department is not null and owner_department <> '')
      and allowed_org_units = '[]'::jsonb;

-- ---- 4. geo_ios_identities: 人間Identityへdepartment属性を追加 ------------------
-- 既存7件（pilot_*/dummy_*）は全てdomain=nullのままであることを実DBで確認済み（Phase 2E
-- Audit）。departmentも同様にnullのまま追加する（推測で値を入れない）。実際の部門割当は
-- 別途アカウント管理側で行う。
alter table public.geo_ios_identities
    add column if not exists department text;

comment on column public.geo_ios_identities.department is
    'Phase 2E: DEPARTMENT_ONLY/SHD_ONLY判定に使う、認証済みIdentityの所属部門。既存の'
    'domain列（Cross-Agent境界用、geo_intelligence_auth側の概念）とは別軸。未設定(null)の'
    '間はdepartment一致判定が常にfalseになり、fail-closed側へ倒れる。';
