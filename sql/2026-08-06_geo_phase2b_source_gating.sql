-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2B: Source Registry 3ゲート分離
-- 作成日: 2026-08-06
--
-- automation_allowed（Phase1、terms/steward承認。コードから絶対に自動変更しない）に加えて、
-- 「Connectorが運用上有効か」「APIキーが実際に利用可能か」を別々の列として分離する。
-- 3つとも独立した意味を持ち、いずれか1つでもfalse相当ならConnectorは実行できない
-- （geo_connector_base.check_automation_allowedがこの3つをすべて検証する）。
-- =====================================================================

alter table public.geo_source_registry
    add column if not exists connector_enabled boolean not null default true,
    add column if not exists credential_configured boolean not null default true;

comment on column public.geo_source_registry.connector_enabled is
    '運用者が一時停止/再開するための独立したスイッチ。automation_allowed（terms/steward承認）'
    'とは別概念。既存のWorld Bank/OFAC Connectorはtrueのまま移行され、動作に影響しない';

comment on column public.geo_source_registry.credential_configured is
    'APIキーが実際に利用可能かを表す。geo_connector_base.sync_credential_status()が'
    'Connector実行のたびに都度再評価して書き込む列であり、automation_allowedには一切波及しない。'
    '無認証Connector（World Bank/OFAC等）はこの列を触らず、既定trueのまま「該当しない」を意味する';


-- =====================================================================
-- ロールバック手順（Phase 2Bでは実行しない。記録のみ）
--
-- alter table public.geo_source_registry drop column if exists credential_configured;
-- alter table public.geo_source_registry drop column if exists connector_enabled;
-- =====================================================================
