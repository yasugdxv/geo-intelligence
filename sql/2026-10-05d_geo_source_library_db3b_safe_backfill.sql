-- =====================================================================
-- Source Library / Monitoring 基盤 — Phase DB-3b: 安全なdomain backfill（データ書き込み）
--
-- 方針: 「domainが一致したから必ず結合」にはしない。
--   - signal側domainを geo_normalize_domain で正規化（source_domain、無ければurl）。
--   - geo_source_domains（match_enabled=true、archived_atがNULLのSourceのみ）を使う。
--   - 1つのdomain → ちょうど1つのSource に確定できるものだけ紐付ける。
--   - 複数Source候補（ambiguous）/ 不一致（unmatched）は source_id=NULL のまま。
--   - 既に source_id が設定済みの行は変更しない（冪等）。
--   - 根拠を geo_external_signal_source_matches に記録する。
--
-- 前提: DB-1 と DB-3 適用済み、Preflightの「unique/ambiguous/unmatched」件数を確認済み。
-- 件数が多い場合はSQL Editorのタイムアウトに注意（その際は本ファイルのinsert/updateに
-- `and s.created_at >= '...'` 等の期間条件を足して分割実行する）。
-- =====================================================================

-- 一意に確定できるdomain → source の対応（一時ビュー相当をCTEで共通化するため、
-- まず対応表を一時テーブルに作る）。
create temp table _geo_unique_domain_map on commit drop as
select d.domain,
       (array_agg(d.source_id))[1]                  as source_id,
       bool_or(d.is_primary)                        as is_primary
  from public.geo_source_domains d
  join public.geo_source_registry r on r.source_id = d.source_id
 where d.match_enabled
   and r.archived_at is null
 group by d.domain
having count(distinct d.source_id) = 1;

-- 1) 監査表へ根拠を記録（未紐付けのsignalのみ）
insert into public.geo_external_signal_source_matches
    (external_signal_id, source_id, match_method, matched_domain, matched_by)
select s.external_signal_id,
       m.source_id,
       case when m.is_primary then 'exact_domain' else 'alias_domain' end,
       m.domain,
       'migration:db3b_safe_backfill'
  from public.geo_external_signals s
  join _geo_unique_domain_map m
    on m.domain = public.geo_normalize_domain(coalesce(nullif(s.source_domain, ''), s.url))
 where s.source_id is null
on conflict (external_signal_id) do nothing;

-- 2) source_id を設定（監査表に根拠がある行のみ）
update public.geo_external_signals s
   set source_id = mt.source_id
  from public.geo_external_signal_source_matches mt
 where mt.external_signal_id = s.external_signal_id
   and mt.matched_by = 'migration:db3b_safe_backfill'
   and mt.source_id is not null
   and s.source_id is null;

-- Rollback（このbackfillで入れた分だけ戻す）:
--   update public.geo_external_signals s set source_id = null
--     from public.geo_external_signal_source_matches mt
--    where mt.external_signal_id = s.external_signal_id
--      and mt.matched_by = 'migration:db3b_safe_backfill';
--   delete from public.geo_external_signal_source_matches
--    where matched_by = 'migration:db3b_safe_backfill';
