-- ============================================================================
-- RECOMMENDED / NOT EXECUTED（2026-10-04）: geo_report_records の取得を高速化する索引（1本だけ）
--
-- 背景: 実DBでは、geo_report_records に (run_id, id) の索引が無いため、Prompt 2 base の
--       record 取得（run_id で絞り、id でキーセットページング）が1ページ（1000件）あたり約20〜40秒かかる。
--       US（21万件=約210ページ）で80分超、Evidence（39万件=約392ページ）はその倍以上。
--       一部のクエリは statement timeout（約8秒、HTTP 500 / code 57014）で失敗する。
--       アプリ側は「必要な列だけ取得・5xxで再試行とページ縮小・id空間16分割の並列取得」で耐えているが、
--       DB側のコストは run 全体の走査のままで、並列度を上げても限界がある。
--
-- 効果の見込み: 1ページが数十ミリ秒になり、US/Evidenceとも数分で取得できる。
-- リスク: 索引の追加のみ（データ・既存列は変更しない）。作成中は書き込みを待たせないよう CONCURRENTLY を使う。
--         CONCURRENTLY はトランザクション内で実行できないので、Supabase SQL Editorでは単独のクエリとして実行する。
-- これは DB schema の変更に当たるため、承認のうえ、あなたがSQL Editorで実行してください。
-- ============================================================================

create index concurrently if not exists geo_report_records_run_id_id_idx
    on public.geo_report_records (run_id, id);

-- 確認（SELECTのみ）: 索引が使われているか
-- explain analyze
-- select id, record_id from public.geo_report_records
--  where run_id = '73aa5253-f727-4b57-b0dc-25c4ae06aabc' and id > '00000000-0000-0000-0000-000000000000'
--  order by id limit 1000;
