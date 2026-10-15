-- =====================================================================
-- Supabase Security Advisor 指摘対応: Function Search Path Mutable
--
-- 対象: Supabase（Supabase SQL Editorで実行）
--
-- 内容:
--   本リポジトリのSQL管理下にあるSECURITY DEFINER/INVOKER関数のうち、search_pathが
--   固定されていないものにALTER FUNCTIONでsearch_path=''を設定する。
--   （関連プロジェクトworld_monitor_dashboardのsql/2026-08-26_move_vector_extension.sqlと
--   同じSecurity Advisor指摘カテゴリへの対応）。
--
--   対象関数は全て本文中のテーブル参照がpublic.xxxの形で完全修飾されているため、
--   search_path=''（空、search_path自体を無効化）へ固定しても既存の挙動は変わらない
--   （確認済み: search_geo_knowledge_items/search_geo_claims/search_geo_entities/
--   get_relationship_graph_for_issue/set_updated_at）。
--   関数の本体（CREATE OR REPLACE FUNCTION）は変更しない。既存migrationファイルの内容も
--   変更しない（本ファイルはALTER FUNCTIONのみの追加migration）。
--
--   Phase 3B Step 5B: increment_article_engagement()はSustainability専用関数
--   （Geo側の機能・テーブルからは一切使用されていない）のため、DB分離に伴い
--   このファイルの対象からも除外した。Sustainability側は別途対応する。
-- =====================================================================

alter function public.search_geo_knowledge_items(text, text[], text[], boolean, int, text[])
    set search_path = '';

alter function public.search_geo_claims(text, boolean, int, text[])
    set search_path = '';

alter function public.search_geo_entities(text, text[], boolean, int)
    set search_path = '';

alter function public.get_relationship_graph_for_issue(uuid, boolean)
    set search_path = '';

alter function public.set_updated_at()
    set search_path = '';
