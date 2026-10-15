-- Report Studio Design Chat: 「提案→FIX」ワークフロー対応（2026-09-15）
--
-- 従来はDesign Chatの1ターンごとにreport_design/signals/queriesへ即座に保存していた
-- （AIの提案がそのまま確定データになる）。ユーザー要望により、チャット中の提案は
-- pending_*列へ保持するだけにし、ユーザーが明示的に「FIX」した時点で初めて
-- report_design/signals/queriesへコミットするワークフローに変更する。
--
-- pending_*が非NULLの間は「未確定の提案がある」状態を表す。FIX時にconfirmed列へ
-- コピーしてpending_*をNULLへ戻す（geo_report_project_assets_service.fix_design_state）。

alter table public.geo_report_design_state
    add column if not exists pending_report_design jsonb,
    add column if not exists pending_signals jsonb,
    add column if not exists pending_queries jsonb;
