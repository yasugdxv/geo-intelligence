-- Report Studio Pipeline: Run名（人が読める名前）とキャンセル要求フラグの追加。
-- 2026-09-11、ユーザー報告「Runの名前が適当な英数字(UUID)になっていてどれかわからない」
-- 「処理をアボートできる方がいい」への対応。

alter table public.geo_report_collection_runs
    add column if not exists run_name text;

comment on column public.geo_report_collection_runs.run_name is
    '画面表示用の人が読める名前（省略時はアプリ側で作成日時から自動生成する）。';

alter table public.geo_report_collection_runs
    add column if not exists cancel_requested boolean not null default false;

comment on column public.geo_report_collection_runs.cancel_requested is
    'trueの間、実行中のPipeline（Prompt1/Prompt2バッチ処理）は次のチェックポイントで'
    '処理を止める（協調的キャンセル。実行中のバッチ自体を強制終了するわけではない）。'
    '新しい実行を開始する際にfalseへリセットされる。';
