-- =====================================================================
-- 社内ユーザーの表示名（External Network の担当者表示用）
-- 2026-10-09
--
-- geo_ios_identities は identity_id（内部管理用ID。例: auto_xxxxxxxxxxxx）と権限しか持たず、
-- 画面に担当者名を出せなかった。本人が自分の表示名を登録できるよう、列を1つ追加する。
--
-- 適用方法: Supabase SQL Editor で全文を1回実行する（手動適用）。
-- 追加のみ（列追加）。既存の行・列・制約は変更しない。何度実行しても安全（if not exists）。
-- 未適用でもアプリは動く（表示名なし＝IDで表示）。
-- =====================================================================
alter table public.geo_ios_identities add column if not exists display_name text;

comment on column public.geo_ios_identities.display_name is
    '画面表示用の氏名。本人がExternal Networkの画面から登録する。認証（credential）には使わない。';
