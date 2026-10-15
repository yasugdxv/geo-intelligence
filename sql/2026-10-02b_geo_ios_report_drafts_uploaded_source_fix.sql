-- Platform IA / Expert AI Reading UI Cleanup Phase 5（2026-10-02）: 実ブラウザ監査で発見した
-- 既存バグの修正。
--
-- geo_ios_report_draft_service.DRAFT_SOURCES = ("ai_generated", "human_edit", "uploaded") と
-- geo_ios_report_draft_api.import_draft()は、アップロード取込を source="uploaded" として
-- 以前から実装済みだった（sql/2026-09-18_geo_ios_native_report_drafts.sql作成時点でコードは
-- 既にuploadedを使っていた）。しかし同migrationのcheck制約は
-- check (source in ('ai_generated', 'human_edit')) のまま、uploadedを追加し忘れており、
-- 既存の「アップロード」ボタン（レビューフロー・Native Draft Compose双方）が実行時に
-- 必ず400（check constraint violation）で失敗する状態だった——Presentation層の新規
-- テキスト貼り付け入口（§13）を実装・検証する過程で発見した（新機能自体のバグではなく、
-- 既存アップロード機能が元から壊れていた）。
--
-- Expert AI Core・CQAS・Reading生成・Review reasoning logicには一切関係しない、
-- 既存の意図（コードが既に想定していた値）に合わせるだけのcheck制約修正。

alter table public.geo_ios_report_drafts
    drop constraint geo_ios_report_drafts_source_check;

alter table public.geo_ios_report_drafts
    add constraint geo_ios_report_drafts_source_check
    check (source in ('ai_generated', 'human_edit', 'uploaded'));
