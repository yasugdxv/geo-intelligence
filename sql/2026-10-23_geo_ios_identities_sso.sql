-- =====================================================================
-- SSO（Azure App Service の認証 / Microsoft Entra ID）対応: geo_ios_identities に、SSO用の列を足す
--
-- 仕組み（geo_ios_identity.py）:
--   ・Azureがログインを担当し、リクエストにユーザーの識別子（Entra の oid）とUPN（メール）を付ける。
--   ・アプリは、oid（sso_subject）でユーザーを探す。無ければ、メール（email）で既存のIDに紐づける（初回ログイン）。
--     それも無ければ、analyst で新規登録する。昇格（approver等）は、このテーブルの role を直接更新して行う。
--   ・既存の identity_id（auto_xxxx / pilot_xxxx）は変えない。メールを入れておけば、初回のSSOログインで、
--     そのIDに紐づき、それまでの専門家相談・コンタクト・面談ログが、そのままそのユーザーのものになる。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは実行しない。既存運用）。
-- destructive文（drop table/drop column）は含まない。何度実行しても安全（if not exists）。
-- =====================================================================

alter table public.geo_ios_identities add column if not exists email text;
alter table public.geo_ios_identities add column if not exists sso_subject text;
alter table public.geo_ios_identities add column if not exists last_login_at timestamptz;

-- 同じSSOアカウント・同じメールが、複数のIDに紐づかないようにする（null は対象外）
create unique index if not exists geo_ios_identities_sso_subject_uq
    on public.geo_ios_identities (sso_subject) where sso_subject is not null;
create unique index if not exists geo_ios_identities_email_uq
    on public.geo_ios_identities (lower(email)) where email is not null;

comment on column public.geo_ios_identities.email is
    'SSOのメールアドレス（UPN）。既存IDに、初回のSSOログインで紐づけるための手がかり。大文字小文字は区別しない';
comment on column public.geo_ios_identities.sso_subject is
    'Microsoft Entra ID の oid（ユーザーの不変の識別子）。初回のSSOログインで設定される';
comment on column public.geo_ios_identities.last_login_at is
    '最後にSSOでログインして、ユーザーを対応づけた時刻（紐づけ・新規登録のときに更新）';

-- ロールを上げる例（approver 等への昇格は、DBで行う。メールは、SSOのUPN）:
--   update public.geo_ios_identities set role = 'approver' where lower(email) = lower('taro.yamada@example.com');
-- アカウントの無効化:
--   update public.geo_ios_identities set is_active = false where lower(email) = lower('taro.yamada@example.com');

-- ロールバック（必要なときだけ。destructiveのため、通常は実行しない）:
--   drop index if exists public.geo_ios_identities_sso_subject_uq;
--   drop index if exists public.geo_ios_identities_email_uq;
--   alter table public.geo_ios_identities drop column if exists last_login_at;
--   alter table public.geo_ios_identities drop column if exists sso_subject;
--   alter table public.geo_ios_identities drop column if exists email;
