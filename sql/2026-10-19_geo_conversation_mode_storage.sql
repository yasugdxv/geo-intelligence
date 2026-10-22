-- =====================================================================
-- Intelligence AI 会話モード: 会話のサーバー保存（会話保存だけの SQL）
--
-- 【未適用】このファイルは、適用の承認を得てから、Supabase SQL Editor で手動実行する。
--
-- これは sql/2026-10-14_geo_ia_extensions.sql の「3) 会話のサーバー保存」と同じテーブルだけを、
-- 専門領域コメント列・記事本文キャッシュと分けて適用できるようにしたもの。どちらを先に実行しても、
-- 何度実行しても安全（if not exists）。
--
-- 追加のみ（新規テーブル1つ＋インデックス1つ）。既存のテーブル・行・列・制約は変更しない。
-- 未適用でもアプリは動く（会話はブラウザ内のみ保存。サーバー保存・サーバー削除・保存済み呼び出し数の上限は使えない）。
--
-- 保存される内容: 会話の発言本文、テーマ、根拠のスナップショット（題名・日付・要点・主張）、会話の整理、AI呼び出し数。
--   調査内容を含み得る。本人だけがアプリ経由で読める（他人は404。アプリの管理者も不可）。
--   DB管理者（サービスキー・Supabaseプロジェクト管理者・SQLエディタ）は全件読める。
-- 保持: 自動削除なし。1人あたり最新50件まで（アプリが古い会話から削除）。本人が画面から1件ずつ削除できる。
--   DBのバックアップには、保持期間が過ぎるまで残る場合がある。
-- =====================================================================

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
-- RLSを有効化し、policyは付けない＝画面側の鍵（匿名・認証済みロール）からは読み書きできない。サーバー（サービスキー）だけ。
alter table public.geo_intelligence_ai_conversations enable row level security;
comment on table public.geo_intelligence_ai_conversations is
    'Intelligence AI の会話履歴（本人のみ参照）。調査結果の本文を含みうるため、他のユーザーには返さない。';

-- ---- 適用後の確認（読み取りのみ）----
-- select relrowsecurity from pg_class where oid = 'public.geo_intelligence_ai_conversations'::regclass;   -- true であること
-- select count(*) from pg_policies where tablename = 'geo_intelligence_ai_conversations';                  -- 0 であること（policyなし）

-- ---- ロールバック（保存済みの会話がすべて消える。必要なら先にエクスポート）----
-- drop table if exists public.geo_intelligence_ai_conversations;
