-- =====================================================================
-- Geopolitical Intelligence Operating System (IOS): Native Report Drafts
-- source列へ 'uploaded' を追加する（アップロードされた文書をAIがセクション分割
-- して取り込むDraft作成経路。ユーザー指示: 「ドラフトのページは、生成以外にも
-- アップロードするオプションがあるべき」「AIがセクション分割して取り込む」）。
--
-- uploaded = ai_generatedと同じくLLM呼び出しを伴うためstatus="pending"で作成され、
-- 完了後にsectionsが埋まる。ただしExpert AI Engine（geo_expert_engine.py）は経由
-- しない——地政学分析ではなく文書構造化タスクのため、geo_llm_common.
-- call_llm_structuredを直接呼ぶ軽量経路にする（geo_report_synthesis_service.
-- run_synthesis等と同じ既存パターン）。
--
-- 適用方法: Supabase DashboardのSQL Editorで手動適用する（このセッションからは
-- 実行しない）。destructive文（drop table/drop column）は含まない。
-- =====================================================================

alter table public.geo_ios_report_drafts
    drop constraint geo_ios_report_drafts_source_check;

alter table public.geo_ios_report_drafts
    add constraint geo_ios_report_drafts_source_check
    check (source in ('ai_generated', 'human_edit', 'uploaded'));

comment on column public.geo_ios_report_drafts.source is
    'ai_generated=Expert AI(draft_generationモード)による自動生成。human_edit=人間に'
    'よる手直し（LLM呼び出しなし、即completed）。uploaded=アップロードされた文書を'
    'AIがセクション分割して取り込んだもの（LLM呼び出しを伴うため一旦pendingで作成）。';
