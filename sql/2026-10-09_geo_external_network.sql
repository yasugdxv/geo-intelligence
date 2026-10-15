-- =====================================================================
-- External Network v1: 外部コンタクト / 面談ログ（Contact Log）/ 添付 / AI整理
-- 2026-10-09
--
-- 適用方法: Supabase SQL Editor で全文を1回実行する（手動適用。アプリ側は適用前でもエラーにならず、
--   「テーブル未作成」と表示する）。既存テーブルは変更しない（新規作成のみ）。
--
-- 設計の要点:
--   - Contact と Contact Log は別エンティティ。Log は複数Contact・複数の社内参加者で共有する（1レコード）。
--   - 社内ユーザーIDは geo_ios_identities.identity_id（text）と同じ値を持つ。外部キー制約は張らない
--     （identityの無効化・自動発行と独立させ、履歴を壊さないため）。
--   - 物理削除しない。Contact は status='inactive'、Log は status='deleted' の論理削除。
--     Contact を指す log_contacts は ON DELETE RESTRICT（履歴を不用意に消さない）。
--   - AI整理は ai_generated（AIの元出力）と edited（人の編集）を分けて持つ。元メモは logs.memo に保持する。
--   - RLS は有効化し、policy は付与しない（service role 経由のサーバー側アクセスのみ許可）。
--   - Expert AI の Retrieval 対象には入れない（既存の知識系テーブルとは完全に別。将来の接続は
--     Contact Log → 人手確認 → Knowledge Candidate → Human Gate を経由する）。
-- =====================================================================

-- 外部コンタクト（基本情報）
create table if not exists public.geo_external_contacts (
    contact_id        uuid primary key default gen_random_uuid(),
    name              text not null check (length(btrim(name)) > 0),
    affiliation_title text,                                   -- 所属・役職（自由記述。分離入力を強制しない）
    memo              text,
    status            text not null default 'active' check (status in ('active', 'inactive')),
    created_by        text not null,
    updated_by        text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);
create index if not exists geo_external_contacts_name_idx on public.geo_external_contacts (lower(name));
create index if not exists geo_external_contacts_status_idx on public.geo_external_contacts (status);

-- Contact と 社内管理者（多対多）
create table if not exists public.geo_external_contact_owners (
    contact_id uuid not null references public.geo_external_contacts (contact_id) on delete restrict,
    owner_id   text not null,
    added_by   text,
    added_at   timestamptz not null default now(),
    primary key (contact_id, owner_id)
);
create index if not exists geo_external_contact_owners_owner_idx on public.geo_external_contact_owners (owner_id);

-- 面談ログ（1回の面談・会議 = 1レコード）
create table if not exists public.geo_external_contact_logs (
    log_id       uuid primary key default gen_random_uuid(),
    contact_date date not null,
    memo         text,                                        -- 元メモ（AI整理前の原文。必ず保持する）
    status       text not null default 'active' check (status in ('active', 'deleted')),
    ai_status    text not null default 'not_run' check (ai_status in ('not_run', 'running', 'completed', 'failed')),
    created_by   text not null,
    updated_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists geo_external_contact_logs_date_idx on public.geo_external_contact_logs (contact_date desc);
create index if not exists geo_external_contact_logs_status_idx on public.geo_external_contact_logs (status);

-- Log と 外部コンタクト（多対多。複数コンタクトの履歴に、同じLogを共有して表示する）
create table if not exists public.geo_external_contact_log_contacts (
    log_id     uuid not null references public.geo_external_contact_logs (log_id) on delete cascade,
    contact_id uuid not null references public.geo_external_contacts (contact_id) on delete restrict,
    primary key (log_id, contact_id)
);
create index if not exists geo_external_contact_log_contacts_contact_idx
    on public.geo_external_contact_log_contacts (contact_id);

-- Log と 社内インタビュー者・参加者（多対多。管理者とは別の関連）
create table if not exists public.geo_external_contact_log_participants (
    log_id  uuid not null references public.geo_external_contact_logs (log_id) on delete cascade,
    user_id text not null,
    primary key (log_id, user_id)
);
create index if not exists geo_external_contact_log_participants_user_idx
    on public.geo_external_contact_log_participants (user_id);

-- 添付資料のメタデータ（本体は公開されない領域に保存。ダウンロードは認可つきAPIのみ）
create table if not exists public.geo_external_contact_log_attachments (
    attachment_id     uuid primary key default gen_random_uuid(),
    log_id            uuid not null references public.geo_external_contact_logs (log_id) on delete cascade,
    filename          text not null,                          -- 表示用（安全化済み）。保存先の名前とは別
    mime_type         text,
    size_bytes        bigint not null check (size_bytes >= 0),
    storage_path      text not null,
    content_hash      text not null,
    extracted_text    text,                                   -- AI整理に使う抽出テキスト（抽出できた形式のみ）
    extraction_status text not null default 'unsupported' check (extraction_status in ('ok', 'failed', 'unsupported')),
    uploaded_by       text not null,
    created_at        timestamptz not null default now(),
    deleted_at        timestamptz
);
create index if not exists geo_external_contact_log_attachments_log_idx
    on public.geo_external_contact_log_attachments (log_id);

-- AI整理結果（AI生成と人手修正を分けて保持する）
create table if not exists public.geo_external_contact_log_ai_summaries (
    summary_id    uuid primary key default gen_random_uuid(),
    log_id        uuid not null unique references public.geo_external_contact_logs (log_id) on delete cascade,
    status        text not null default 'not_run' check (status in ('not_run', 'running', 'completed', 'failed')),
    ai_generated  jsonb,                                      -- AIの元出力（検証済み）
    edited        jsonb,                                      -- 人が編集した内容（あれば、表示ではこちらを優先）
    edited_by     text,
    edited_at     timestamptz,
    ai_updated_after_edit boolean not null default false,     -- 編集後にAIが再実行された（編集内容は上書きしていない）
    model         text,
    source_hash   text,                                       -- 整理に使った入力（メモ＋添付テキスト）のハッシュ
    error_message text,
    last_run_by   text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

alter table public.geo_external_contacts enable row level security;
alter table public.geo_external_contact_owners enable row level security;
alter table public.geo_external_contact_logs enable row level security;
alter table public.geo_external_contact_log_contacts enable row level security;
alter table public.geo_external_contact_log_participants enable row level security;
alter table public.geo_external_contact_log_attachments enable row level security;
alter table public.geo_external_contact_log_ai_summaries enable row level security;

comment on table public.geo_external_contacts is
    'External Network v1: 外部コンタクト。社内限定の個人情報を含む。Expert AIのRetrievalには自動投入しない。';
comment on table public.geo_external_contact_logs is
    'External Network v1: 面談ログ。memoは元メモ（AI整理前）。複数のContact・社内参加者で1レコードを共有する。';
comment on table public.geo_external_contact_log_ai_summaries is
    'External Network v1: AI整理結果。ai_generated=AIの元出力、edited=人手修正。AIの推測は事実として保存しない。';
