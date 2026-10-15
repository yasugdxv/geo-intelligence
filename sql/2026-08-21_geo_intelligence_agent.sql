-- =====================================================================
-- 地政学インテリジェンス基盤 Phase1: Geo Intelligence Agent・API基盤
--
-- 外部アプリ（Sustainability/ERM/SCM/PA等）からの地政学Knowledge問い合わせ履歴を保存する。
-- 既存テーブル・列の削除やデータ上書きは一切行わない（すべて新規テーブルの追加のみ）。
-- 適用順序: このファイル1本のみ（Phase1〜Phase2B-2のmigrationが適用済みであることが前提）。
-- =====================================================================

create table public.geo_intelligence_requests (
    request_id          uuid primary key default gen_random_uuid(),
    requester_app       text not null,
    request_type        text not null
        check (request_type in ('weekly_article', 'user_question')),
    question            text not null,
    country_region      jsonb not null default '[]'::jsonb,
    themes              jsonb not null default '[]'::jsonb,
    context             text,
    time_horizon        text,
    request_payload     jsonb not null,
    status              text not null default 'received'
        check (status in ('received', 'completed', 'partial', 'failed')),
    is_demo             boolean not null default false,
    created_at          timestamptz not null default now(),
    completed_at        timestamptz
);

create index geo_intelligence_requests_requester_app_idx
    on public.geo_intelligence_requests(requester_app);
create index geo_intelligence_requests_created_at_idx
    on public.geo_intelligence_requests(created_at);

alter table public.geo_intelligence_requests enable row level security;

comment on table public.geo_intelligence_requests is
    'Geo Intelligence Agent（外部アプリ向け問い合わせ窓口、Phase1）が受け付けたRequestの履歴。'
    'request_payloadに正規化後の完全なRequestを保存する（将来のフィールド追加を吸収するため、'
    'question/country_region/themes等の主要項目のみ個別列としても複製する）。';

create table public.geo_intelligence_responses (
    response_id         uuid primary key default gen_random_uuid(),
    request_id          uuid not null references public.geo_intelligence_requests(request_id) on delete cascade,
    response_payload    jsonb not null,
    knowledge_sufficiency text not null
        check (knowledge_sufficiency in ('sufficient', 'partial', 'insufficient')),
    confidence          text,
    retrieval_run_id    uuid references public.geo_retrieval_runs(retrieval_run_id) on delete set null,
    expert_response_id  uuid references public.geo_expert_responses(response_id) on delete set null,
    created_at          timestamptz not null default now()
);

create index geo_intelligence_responses_request_idx
    on public.geo_intelligence_responses(request_id);

alter table public.geo_intelligence_responses enable row level security;

comment on table public.geo_intelligence_responses is
    'Geo Intelligence Agent（Phase1）が返したResponseの履歴。retrieval_run_idは既存'
    'geo_retrieval_runsのうち、本Agent用に1本へ統合記録したRunを指す。expert_response_idは'
    'Phase1では常にNULL（既存Geopolitical Expert AIはPhase1では呼び出さない。Phase2以降で'
    '接続する場合に埋める想定の列）。';
