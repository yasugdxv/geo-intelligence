-- Report Studio Pipeline: geo_report_execution_logへPrompt1バッチ実行の実測分析用の列を追加。
-- 2026-09-11、ユーザーからの要望「AC/SC/Targeted Stance call数・batch平均cluster数・
-- input/output tokens・latency・実concurrency・429 retry回数・重複送信tokenを出して」への対応。
-- これまでこのテーブルには何も書き込まれていなかった（token/コストログとして用意されていた
-- だけで、実装が追いついていなかった）。

alter table public.geo_report_execution_log
    add column if not exists stage text;

comment on column public.geo_report_execution_log.stage is
    'このLLM呼び出しがどのstage(prompt1_analysis_cluster等)に属するか。';

alter table public.geo_report_execution_log
    add column if not exists latency_ms integer;

alter table public.geo_report_execution_log
    add column if not exists rate_limit_retries integer not null default 0;

comment on column public.geo_report_execution_log.rate_limit_retries is
    'この呼び出しが成功するまでに429(rate_limit_exceeded)で何回リトライしたか。';

alter table public.geo_report_execution_log
    add column if not exists item_count integer;

comment on column public.geo_report_execution_log.item_count is
    'このバッチ呼び出しに含まれていたクラスタ数（1回のLLM呼び出しでまとめて処理した件数）。';

alter table public.geo_report_execution_log
    add column if not exists cached_tokens integer;

comment on column public.geo_report_execution_log.cached_tokens is
    'Azure/OpenAI Prompt Cachingが実際にヒットしたinput token数（prompt_tokens_details.'
    'cached_tokens）。system prompt(Prompt1指示文、約4,609token)の重複送信が'
    'キャッシュされコスト・latency軽減に寄与しているかを実測するための列。';

create index if not exists geo_report_execution_log_stage_idx
    on public.geo_report_execution_log(execution_id, stage);
