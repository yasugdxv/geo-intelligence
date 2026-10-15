-- =====================================================================
-- 地政学インテリジェンス基盤 Phase3: Knowledge Gap → Intelligence Need管理
--
-- 既存geo_knowledge_gaps（12章、Case向けDeterministic/Analytical Gap）を拡張し、
-- External Request（Geo Intelligence Agent）由来のGapも同じテーブルで扱えるようにする。
-- 同じ概念の別テーブルは新設しない。既存Case向けGap運用（case_id必須の行）は無変更で動作する。
-- 適用順序: このファイル1本のみ（Phase1〜Phase2のmigrationが適用済みであることが前提）。
-- =====================================================================

-- ---- 1. case_idを必須から任意へ（External Request由来はCaseを持たないため） ----
alter table public.geo_knowledge_gaps
    alter column case_id drop not null;

-- ---- 2. origin_type軸の追加（既存行は全てcase_analysis起点として扱う） ----
alter table public.geo_knowledge_gaps
    add column if not exists origin_type text not null default 'case_analysis'
        check (origin_type in ('case_analysis', 'external_app_request', 'manual'));

comment on column public.geo_knowledge_gaps.origin_type is
    'Gapの発生源。case_analysis=既存Case向けDeterministic/Analytical Gap検出、'
    'external_app_request=Geo Intelligence Agent経由の外部Request、manual=人手による直接登録。';

-- ---- 3. External Request由来Gapが保持する項目（既存列で代替できないもののみ追加） ----
alter table public.geo_knowledge_gaps
    add column if not exists requested_by_app text,
    add column if not exists request_type text,
    add column if not exists question text,
    add column if not exists country_region jsonb not null default '[]'::jsonb,
    add column if not exists topic jsonb not null default '[]'::jsonb,
    add column if not exists business_context text,
    add column if not exists missing_knowledge jsonb,
    add column if not exists assigned_to text,
    add column if not exists resolved_at timestamptz,
    add column if not exists resolution_report_id uuid
        references public.geo_documents(document_id) on delete set null,
    add column if not exists duplicate_of_gap_id uuid
        references public.geo_knowledge_gaps(gap_id) on delete set null,
    add column if not exists request_count integer not null default 1;

comment on column public.geo_knowledge_gaps.missing_knowledge is
    'Geo Intelligence Agentが今回の検索で見つからなかったカテゴリ一覧（Phase2の10分類）。'
    '推測で埋めない：見つからなかった事実のみを記録する。';
comment on column public.geo_knowledge_gaps.request_count is
    '同一Intelligence Needに紐づいたRequest数（重複判定でマージされた回数、監査用の非正規化カウンタ。'
    '正の情報源はgeo_knowledge_gap_requestsの行数）。';
comment on column public.geo_knowledge_gaps.duplicate_of_gap_id is
    '重複候補として検出されたが自動マージしなかった場合、より古い（正）Gapを指す。'
    'status=duplicate_candidateの行のみ設定される。';

-- ---- 4. gap_typeへexternal_request由来の値を追加（既存21種は変更しない） ----
alter table public.geo_knowledge_gaps
    drop constraint if exists geo_knowledge_gaps_gap_type_check,
    add constraint geo_knowledge_gaps_gap_type_check
        check (gap_type in (
            'missing_entity', 'missing_actor_profile', 'missing_relationship',
            'missing_dependency', 'missing_historical_context', 'missing_precedent',
            'missing_legal_basis', 'missing_quantitative_data', 'stale_knowledge',
            'conflicting_evidence', 'insufficient_source_diversity', 'missing_indicator',
            'applicability_gap', 'precedent_comparison_gap', 'causal_gap',
            'legal_institutional_gap', 'actor_intent_gap', 'countermeasure_gap',
            'enforcement_gap', 'alternative_explanation_gap', 'falsification_gap',
            'external_request_knowledge_gap'
        ));

-- ---- 5. statusへDashboard運用に必要な値を追加（open/in_progress=既存、researching/dismissed/
--         duplicate_candidate=Phase3で追加。resolvedは既存のまま） ----
alter table public.geo_knowledge_gaps
    drop constraint if exists geo_knowledge_gaps_status_check,
    add constraint geo_knowledge_gaps_status_check
        check (status in ('open', 'in_progress', 'researching', 'resolved', 'dismissed', 'duplicate_candidate'));

-- ---- 6. case_idが必須ではなくなったため、既存indexはそのまま（NULL許容のbtree indexで問題ない） ----
create index if not exists geo_knowledge_gaps_origin_idx on public.geo_knowledge_gaps(origin_type);
create index if not exists geo_knowledge_gaps_status_idx on public.geo_knowledge_gaps(status);

-- ---- 7. Gap↔Request 多対多リンク（Auditability: 同じGapに複数Requestが紐付くことを許容） ----
create table public.geo_knowledge_gap_requests (
    gap_request_id  uuid primary key default gen_random_uuid(),
    gap_id          uuid not null references public.geo_knowledge_gaps(gap_id) on delete cascade,
    request_id      uuid not null references public.geo_intelligence_requests(request_id) on delete cascade,
    created_at      timestamptz not null default now(),
    unique (gap_id, request_id)
);

create index geo_knowledge_gap_requests_gap_idx on public.geo_knowledge_gap_requests(gap_id);
create index geo_knowledge_gap_requests_request_idx on public.geo_knowledge_gap_requests(request_id);

alter table public.geo_knowledge_gap_requests enable row level security;

comment on table public.geo_knowledge_gap_requests is
    'External Request → Geo Response → Knowledge Gapの追跡と、同一Intelligence Needへの'
    '複数Request紐付け（重複質問の集約）を保持するリンクテーブル。';
