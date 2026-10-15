-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): Identity Roster を DB へ移行
--
-- Phase I-A.1のcredential→identity_id→roleマッピング（geo_ios_identity.py）は、
-- これまでconfig.jsonのgeo_ios.identities（.gitignore対象、手編集必須）でのみ
-- 管理していた。ユーザー指示（2026-09-24、「本番ではrole管理を入れるとして、
-- 一旦はダミーのレビュアーアカウントを登録してほしい。アカウント管理はconfigでは
-- なくDBでやろう」）に基づき、同じ形（identity_id/credential/role/domain）を
-- このテーブルへ移す。verify_ios_identityの認証契約・SEC-1/SEC-2 Trust Boundary
-- （geo_ios_identity.pyのdocstring参照）自体は変更しない——credentialの発行元・
-- 照合先をファイルからDBへ変えるだけで、「authenticated identity→server-side
-- assigned role→authorization」の順序強制はそのまま維持する。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない。geo_db_clientにマイグレーションランナーは存在せず、これが本リポジトリの
-- 既存運用）。destructive文（drop table/drop column）は含まない。
--
-- 適用後、既存config.jsonのgeo_ios.identities 5件をこのテーブルへ移行し、追加で
-- ダミーのexpert_reviewerアカウントを1件作成するシード処理を別スクリプトで行う
-- （credential値を含むため、このSQLファイル自体には実データを書かない——config.json
-- と同じ「秘密情報はgit管理下のファイルに書かない」原則を維持する）。
-- =====================================================================

create table public.geo_ios_identities (
    identity_id   text primary key,
    credential    text not null unique,
    role          text not null check (role in
        ('viewer', 'analyst', 'expert_reviewer', 'knowledge_steward', 'approver', 'administrator')),
    domain        text,
    is_active     boolean not null default true,
    created_by    text,
    created_at    timestamptz not null default now()
);

alter table public.geo_ios_identities enable row level security;

comment on table public.geo_ios_identities is
    'Phase I-A.1 Pilot-grade Authenticated Identity（geo_ios_identity.py）のcredential→'
    'identity_id→roleマッピング。従来config.json側のgeo_ios.identitiesで管理していたものを'
    'DBへ移行した（2026-09-24、ユーザー指示）。credentialは平文で保持する——config.json時代と'
    '同じ扱いであり、このテーブルへのアクセス自体がserver-side（service role経由のみ）である'
    'ことで守る（RLSはenable済み、policyは付与しない=service role以外は全て拒否）。'
    'is_active=falseにすることでアカウントを無効化できる（行を削除しなくてよい）。';
