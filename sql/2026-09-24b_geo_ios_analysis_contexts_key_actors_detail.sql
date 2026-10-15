-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): 前提整理（Intake）
-- Analysis Context への key_actors_detail 列追加
--
-- 背景（ユーザー指示、2026-09-24、Step2「読み方提案」品質検証の結果を受けた改善①）:
-- Gold Case（イラン）でのBlind Testにより、既存relevant_actors（機関・国家レベルの
-- 文字列配列）だけでは、担当者がアップロードした実資料に登場する個人名（例:
-- ガリバフ・イラン国会議長、ネタニヤフ首相）・役職・レバレッジ・関係性・制約が
-- Analysis Contextへ一切反映されず、専門家AIへ渡すtheme_overrideの解像度が下がる
-- ことが確認された。担当者に入力させるのではなく、引き続きAIが会話・添付資料本文から
-- 抽出する既存方針（geo_ios_intake_service.pyの絶対条件）を維持したまま、
-- relevant_actorsとは別のオブジェクト配列として追加する（既存列は変更しない）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない。geo_db_clientにマイグレーションランナーは存在せず、これが本リポジトリの
-- 既存運用）。destructive文（drop table/drop column）は含まない。
-- =====================================================================

alter table public.geo_ios_analysis_contexts
    add column if not exists key_actors_detail jsonb not null default '[]'::jsonb;

comment on column public.geo_ios_analysis_contexts.key_actors_detail is
    '会話・添付資料の中に個人名・具体的役職が登場した場合のみ、AIが抽出する'
    '{name, role, controls_or_leverage, key_relationships, constraints}の配列。'
    'relevant_actors（機関・国家レベルの文字列配列）とは別軸——資料に個人名が'
    '登場しない場合は空のままでよい（無理に埋めない）。';
