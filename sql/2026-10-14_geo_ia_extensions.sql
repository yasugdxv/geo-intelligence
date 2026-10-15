-- =====================================================================
-- Intelligence AI / External Network 拡張（2026-10-14）
--
-- 1. geo_external_contacts.expertise_note : コンタクト登録時に任意で書く「専門領域・コメント」
-- 2. geo_external_signal_bodies           : 記事の本文キャッシュ（記事URLから取得した本文）
-- 3. geo_intelligence_ai_conversations    : Intelligence AI の会話のサーバー保存（本人だけが読める）
--
-- 適用方法: Supabase SQL Editor で全文を1回実行する（手動適用）。
-- 追加のみ（列追加・新規テーブル）。既存の行・列・制約は変更しない。何度実行しても安全（if not exists）。
-- 未適用でもアプリは動く（専門領域コメントは保存できない／本文は毎回取得してキャッシュしない／
-- 会話はブラウザ内のみ保存）。
-- =====================================================================

-- 1) 専門領域・コメント（任意）
alter table public.geo_external_contacts add column if not exists expertise_note text;
comment on column public.geo_external_contacts.expertise_note is
    '登録時に任意で書く専門領域・コメント（人が書いた登録情報）。会話ログからのAI推定とは別に扱う。';

-- 2) 記事本文キャッシュ
create table if not exists public.geo_external_signal_bodies (
    external_signal_id uuid primary key references public.geo_external_signals (external_signal_id) on delete cascade,
    status             text not null check (status in ('ok', 'failed', 'blocked', 'too_short')),
    body_text          text,
    char_count         integer not null default 0,
    content_hash       text,
    http_status        integer,
    error              text,
    fetched_at         timestamptz not null default now()
);
create index if not exists geo_external_signal_bodies_status_idx on public.geo_external_signal_bodies (status);
alter table public.geo_external_signal_bodies enable row level security;
comment on table public.geo_external_signal_bodies is
    '記事URLから取得した本文のキャッシュ。社内の分析用途のみ。blocked=アクセス拒否/ボット対策、failed=取得失敗、too_short=本文が取れなかった。';

-- 3) 会話のサーバー保存（所有者のみ。RLSは有効化し、policyは付けない＝サーバー側アクセスのみ）
create table if not exists public.geo_intelligence_ai_conversations (
    conversation_id text primary key,
    owner_id        text not null,
    title           text,
    messages        jsonb not null default '[]'::jsonb,
    preset          jsonb,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists geo_intelligence_ai_conversations_owner_idx
    on public.geo_intelligence_ai_conversations (owner_id, updated_at desc);
alter table public.geo_intelligence_ai_conversations enable row level security;
comment on table public.geo_intelligence_ai_conversations is
    'Intelligence AI の会話履歴（本人のみ参照）。調査結果の本文を含みうるため、他のユーザーには返さない。';
