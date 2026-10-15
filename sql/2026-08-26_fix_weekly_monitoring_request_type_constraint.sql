-- =====================================================================
-- BUGFIX: geo_intelligence_requests.request_type の CHECK制約に
-- 'weekly_monitoring' が含まれておらず、実DBへの weekly_monitoring リクエスト保存が
-- 400 Bad Request で全件失敗していた（Sustainability×Geo Real-data Quality Validation
-- のStep1実施中に発覚）。
--
-- 原因: geo_intelligence_schema.py の GeoIntelligenceRequestType に weekly_monitoring を
-- 追加した際（Weekly Monitoring API Contract拡張）、対応するDB migrationが作成されなかった。
-- 既存データ・既存列は一切変更しない（CHECK制約の許容値を1つ追加するのみ）。
-- =====================================================================

alter table public.geo_intelligence_requests
    drop constraint if exists geo_intelligence_requests_request_type_check;

alter table public.geo_intelligence_requests
    add constraint geo_intelligence_requests_request_type_check
    check (request_type in ('weekly_article', 'user_question', 'weekly_monitoring'));
