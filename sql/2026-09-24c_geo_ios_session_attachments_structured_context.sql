-- =====================================================================
-- Geopolitical Intelligence OS: 添付資料の構造化要約（structured_context）
--
-- ユーザー指示（2026-09-24b、Hungary Gold Case Blind Testで発覚した欠陥への対応）:
-- geo_ios_intake_service._format_attachments_for_promptが添付資料の先頭2000文字だけを
-- Intake Chatへ見せていたため、Hungary資料（4316文字）の後半にあるPutin/Trump・MAGA関連の
-- 記述が何ターン重ねても一度もAIに見えず、Actor Hitが弱くなる実根拠のある原因になっていた。
--
-- 対応: 添付資料全文をchunk分割→断片ごとに人物・事実を抽出→1つに統合したJSONを
-- 本カラムへ保存する。Expert AI/CQAS/Reading Portfolioには一切触れない
-- （Intake Chatへ渡す添付資料の表現方法だけを変える）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない。geo_db_clientにマイグレーションランナーは存在せず、これが本リポジトリの
-- 既存運用）。destructive文（drop table/drop column）は含まない。
-- =====================================================================

alter table public.geo_ios_session_attachments
    add column if not exists structured_context jsonb;

comment on column public.geo_ios_session_attachments.structured_context is
    '添付資料全文をchunk分割し、断片ごとにkey_actors（name/role/controls_or_leverage/'
    'key_relationships/constraints/notable_actions）とrelevant_facts_or_eventsを抽出した後、'
    '1つに統合した構造化要約。NULLの場合は、Azure OpenAI未設定・生成失敗などでまだ生成されて'
    'いない状態——_format_attachments_for_promptは先頭2000文字の抜粋へフォールバックする'
    '（この場合、長い資料では後半の人物・事実が欠落し得る）。';
