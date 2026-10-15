-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase1: External Signal Seed修正
-- 作成日: 2026-09-07
-- 前提: sql/2026-09-07_geo_external_signals.sql 実行済み
--
-- 目的: 直前のmigrationのSeed INSERT文に不具合があり、"Taiwan Strait"クエリにのみ
-- 紐付けるはずのtheme_id（taiwan_strait）が、相関を持たない定数サブクエリだったため
-- Iran sanctions / US China semiconductor export controls / Ukraine ceasefireの
-- 3件にも誤って同じtheme_idが入ってしまった。本ファイルはそれを是正する
-- （データのみの修正、スキーマ変更なし）。
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select name, theme_id from public.geo_external_signal_queries order by name;
--   -- 修正前は4件とも同一theme_idが入っているはず
-- =====================================================================

update public.geo_external_signal_queries
set theme_id = null
where name in (
    'Iran sanctions',
    'US China semiconductor export controls',
    'Ukraine ceasefire'
);

-- =====================================================================
-- ロールバック手順（本migrationでは実行しない、記録のみ。誤って再度theme_idを
-- 埋め戻したい場合のみ使う。通常は不要）
--
-- update public.geo_external_signal_queries
-- set theme_id = (select theme_id from public.geo_themes where code = 'taiwan_strait' limit 1)
-- where name in ('Iran sanctions', 'US China semiconductor export controls', 'Ukraine ceasefire');
-- =====================================================================
