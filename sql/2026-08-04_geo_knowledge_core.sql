-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 1: コアテーブル一式
-- 作成日: 2026-08-04
-- 対象: Supabase（PostgreSQL）。vector拡張には依存しない（pgvectorは
-- 2026-08-04_geo_knowledge_pgvector_optional.sql に分離）。
--
-- 前提: 以下が実行済みであること
--   sql/2026-08-02_geo_core_schema.sql（geo_themes, geo_source_registry,
--     geo_documents, geo_events, geo_evidence, geo_observations,
--     geo_audit_logs 等）
--   public.set_updated_at()（共通トリガー関数）
--
-- 目的: Case作成→既存ナレッジ検索→Foundation Pack生成→Knowledge Gap特定→
-- 追加Evidence登録→Case分析→Knowledge Candidate抽出→人間レビュー→
-- Active Knowledge化→次のCaseで再利用、という中核ループのためのデータモデル
-- （詳細: docs/geopolitical-data-model.md）。
--
-- 安全性: 既存テーブルの列削除・データ上書きは一切行わない。既存テーブルへの
-- ALTERはすべて ADD COLUMN IF NOT EXISTS（nullableまたはdefault付き）のみ。
-- ロールバック手順は本ファイル末尾のコメントを参照。
--
-- 作成順: geo_entities → geo_entity_aliases → geo_issues → geo_relationships
--        → geo_assets → geo_dependencies → geo_knowledge_items
--        → geo_knowledge_reviews → geo_cases → geo_case_hypotheses
--        → geo_claims → geo_document_chunks → geo_claim_evidence
--        → geo_knowledge_gaps → geo_entity_issues → geo_relationship_issues
--        → geo_knowledge_item_issues → geo_case_issues
--        → ALTER geo_source_registry → ALTER geo_documents
-- =====================================================================


-- ---- 1. geo_entities（Entity Master） ----

create table public.geo_entities (
    entity_id           uuid primary key default gen_random_uuid(),
    entity_type         text not null
        check (entity_type in (
            'country', 'territory', 'government', 'ministry', 'political_party',
            'faction', 'person', 'military', 'security_service',
            'international_organization', 'state_owned_enterprise', 'company',
            'industry_group', 'labor_union', 'religious_group', 'ethnic_group',
            'armed_group', 'civil_society', 'media', 'geographic_location',
            'port', 'strait', 'canal', 'military_base', 'strategic_asset'
        )),
    canonical_name      text not null,
    local_name          text,
    name_en             text,
    name_ja             text,
    abbreviations       text[] not null default '{}',
    former_names        text[] not null default '{}',
    parent_entity_id    uuid references public.geo_entities(entity_id) on delete set null,
    successor_entity_id uuid references public.geo_entities(entity_id) on delete set null,
    predecessor_entity_id uuid references public.geo_entities(entity_id) on delete set null,
    country_code        text,
    active_from          date,
    active_to            date,
    status               text not null default 'active'
        check (status in ('active', 'inactive', 'dissolved')),
    external_ids         jsonb not null default '{}'::jsonb,
    is_demo              boolean not null default false,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

create index geo_entities_type_idx on public.geo_entities(entity_type);
create index geo_entities_country_code_idx on public.geo_entities(country_code);
create index geo_entities_is_demo_idx on public.geo_entities(is_demo);

create trigger geo_entities_set_updated_at
    before update on public.geo_entities
    for each row execute function public.set_updated_at();

alter table public.geo_entities enable row level security;

comment on table public.geo_entities is
    'Actor×Relationship×Issue/Theater×Timeの中心となるEntity Master。国・組織・政党・'
    '企業・軍・国際機関・地理的資産等を横断的に保持する（7.1章）';


-- ---- 2. geo_entity_aliases（多言語・旧名称・略称） ----

create table public.geo_entity_aliases (
    alias_id    uuid primary key default gen_random_uuid(),
    entity_id   uuid not null references public.geo_entities(entity_id) on delete cascade,
    alias_text  text not null,
    alias_type  text not null
        check (alias_type in ('local_language', 'abbreviation', 'former_name', 'transliteration')),
    language    text,
    created_at  timestamptz not null default now()
);

create unique index geo_entity_aliases_unique_idx
    on public.geo_entity_aliases(entity_id, alias_text, alias_type);
create index geo_entity_aliases_entity_idx on public.geo_entity_aliases(entity_id);
create index geo_entity_aliases_text_idx on public.geo_entity_aliases(alias_text);

alter table public.geo_entity_aliases enable row level security;

comment on table public.geo_entity_aliases is
    'Entityの多言語名・旧名称・略称。entity resolution（別名解決）の照合対象';


-- ---- 3. geo_issues（Theater配下の個別論点） ----

create table public.geo_issues (
    issue_id    uuid primary key default gen_random_uuid(),
    theme_id    uuid not null references public.geo_themes(theme_id) on delete cascade,
    code        text not null,
    name_ja     text not null,
    name_en     text,
    description text,
    status      text not null default '有効'
        check (status in ('有効', '無効')),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create unique index geo_issues_theme_code_idx on public.geo_issues(theme_id, code);

create trigger geo_issues_set_updated_at
    before update on public.geo_issues
    for each row execute function public.set_updated_at();

alter table public.geo_issues enable row level security;

comment on table public.geo_issues is
    '既存geo_themesをTheater（例: US-China-Taiwan-Japan）として扱い、その配下の個別論点'
    '（例: Sanctions, Technology Export Controls）を本テーブルで管理する（7.8章）';


-- ---- 4. geo_relationships（Relationship Edge） ----

create table public.geo_relationships (
    relationship_id       uuid primary key default gen_random_uuid(),
    from_entity_id        uuid not null references public.geo_entities(entity_id) on delete cascade,
    to_entity_id          uuid not null references public.geo_entities(entity_id) on delete cascade,
    relationship_domain   text not null
        check (relationship_domain in (
            'diplomacy', 'security', 'military', 'trade', 'investment', 'finance',
            'energy', 'technology', 'information', 'political_support', 'social',
            'legal', 'logistics'
        )),
    relationship_type     text not null,
    description           text,
    dependency_direction  text
        check (dependency_direction is null or dependency_direction in (
            'from_depends_on_to', 'to_depends_on_from', 'mutual', 'none'
        )),
    dependency_level      text
        check (dependency_level is null or dependency_level in ('low', 'medium', 'high', 'critical')),
    leverage_direction    text
        check (leverage_direction is null or leverage_direction in (
            'from_has_leverage', 'to_has_leverage', 'mutual', 'none'
        )),
    cooperation_area      text,
    conflict_area         text,
    formal_basis          text,
    trend                 text
        check (trend is null or trend in ('strengthening', 'weakening', 'stable', 'volatile')),
    valid_from            date,
    valid_to              date,
    confidence            text not null default '判断不能'
        check (confidence in ('高', '中', '低', '判断不能')),
    supporting_claim_ids    uuid[] not null default '{}',
    contradicting_claim_ids uuid[] not null default '{}',
    is_demo               boolean not null default false,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

create index geo_relationships_from_idx on public.geo_relationships(from_entity_id);
create index geo_relationships_to_idx on public.geo_relationships(to_entity_id);
create index geo_relationships_domain_idx on public.geo_relationships(relationship_domain);

create trigger geo_relationships_set_updated_at
    before update on public.geo_relationships
    for each row execute function public.set_updated_at();

alter table public.geo_relationships enable row level security;

comment on table public.geo_relationships is
    '方向付き・多次元の関係（7.2章）。「同盟国/敵対国」の単純な一分類にせず、'
    'relationship_domain×dependency×leverageの組み合わせで表現する';


-- ---- 5. geo_assets（継続的に保有する能力・資源） ----

create table public.geo_assets (
    asset_id             uuid primary key default gen_random_uuid(),
    entity_id            uuid not null references public.geo_entities(entity_id) on delete cascade,
    asset_type           text not null
        check (asset_type in (
            'market_size', 'military_power', 'naval_power', 'base', 'veto_power',
            'critical_resource', 'technology', 'financial_network', 'logistics_hub',
            'strait_canal_control', 'production_capacity', 'information_capability',
            'diplomatic_influence'
        )),
    description          text not null,
    quantitative_value   jsonb,
    valid_from           date,
    valid_to             date,
    confidence           text not null default '判断不能'
        check (confidence in ('高', '中', '低', '判断不能')),
    supporting_claim_ids uuid[] not null default '{}',
    is_demo              boolean not null default false,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

create index geo_assets_entity_idx on public.geo_assets(entity_id);
create index geo_assets_type_idx on public.geo_assets(asset_type);

create trigger geo_assets_set_updated_at
    before update on public.geo_assets
    for each row execute function public.set_updated_at();

alter table public.geo_assets enable row level security;

comment on table public.geo_assets is
    'Entityが継続的に保有する能力・資源（7.3章）。Leverage Cardとは分離し、'
    '保有能力そのものを表す';


-- ---- 6. geo_dependencies（特定主体への依存） ----

create table public.geo_dependencies (
    dependency_id        uuid primary key default gen_random_uuid(),
    entity_id            uuid not null references public.geo_entities(entity_id) on delete cascade,
    counterpart_entity_id uuid references public.geo_entities(entity_id) on delete set null,
    dependency_type      text not null
        check (dependency_type in (
            'energy_import', 'export_market', 'weapons_supply', 'technology',
            'settlement', 'food', 'security', 'investment', 'components_materials'
        )),
    description          text not null,
    severity             text not null default '判断不能'
        check (severity in ('low', 'medium', 'high', 'critical', '判断不能')),
    valid_from           date,
    valid_to             date,
    confidence           text not null default '判断不能'
        check (confidence in ('高', '中', '低', '判断不能')),
    supporting_claim_ids uuid[] not null default '{}',
    is_demo              boolean not null default false,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

create index geo_dependencies_entity_idx on public.geo_dependencies(entity_id);
create index geo_dependencies_counterpart_idx on public.geo_dependencies(counterpart_entity_id);

create trigger geo_dependencies_set_updated_at
    before update on public.geo_dependencies
    for each row execute function public.set_updated_at();

alter table public.geo_dependencies enable row level security;

comment on table public.geo_dependencies is
    '特定主体（または不特定の相手）への依存（7.4章）。エネルギー・輸出市場・武器供給・'
    '技術・決済・食料・安全保障・投資・部品原材料等';


-- ---- 7. geo_knowledge_items（Reusable Intelligence Knowledge本体） ----

create table public.geo_knowledge_items (
    knowledge_item_id  uuid primary key default gen_random_uuid(),
    knowledge_type     text not null
        check (knowledge_type in (
            'country_foundation', 'actor_profile', 'party_profile',
            'relationship_assessment', 'historical_path_dependency', 'precedent',
            'mechanism', 'reasoning_rule', 'indicator', 'foundation_update',
            'case_finding', 'leverage_card'
        )),
    title               text not null,
    summary             text,
    structured_content  jsonb not null,
    status              text not null default 'candidate'
        check (status in (
            'candidate', 'in_review', 'active', 'contested', 'rejected',
            'superseded', 'deprecated', 'archived'
        )),
    confidence           text not null default '判断不能'
        check (confidence in ('高', '中', '低', '判断不能')),
    confidence_rationale text,
    information_class    text not null
        check (information_class in (
            'observed_fact', 'actor_claim', 'external_assessment', 'internal_hypothesis'
        )),
    valid_from          date,
    valid_to            date,
    as_of_date          date not null default current_date,
    created_by          text not null,
    reviewed_by         text,
    approved_by         text,
    supersedes_id       uuid references public.geo_knowledge_items(knowledge_item_id) on delete set null,
    version             integer not null default 1,
    next_review_at      date,
    stale_after_days    integer,
    duplicate_of_id     uuid references public.geo_knowledge_items(knowledge_item_id) on delete set null,
    conflicting_ids     uuid[] not null default '{}',
    is_demo             boolean not null default false,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create index geo_knowledge_items_type_idx on public.geo_knowledge_items(knowledge_type);
create index geo_knowledge_items_status_idx on public.geo_knowledge_items(status);
create index geo_knowledge_items_is_demo_idx on public.geo_knowledge_items(is_demo);

create trigger geo_knowledge_items_set_updated_at
    before update on public.geo_knowledge_items
    for each row execute function public.set_updated_at();

alter table public.geo_knowledge_items enable row level security;

comment on table public.geo_knowledge_items is
    'Precedent/Mechanism/Reasoning Rule/Indicator/Country Foundation/Actor Profile/'
    'Party Profile/Relationship Assessment/Historical Path Dependency/Foundation Update/'
    'Case Finding/Leverage Cardを knowledge_type で区別する汎用テーブル（7.6〜7.9, 8章）。'
    'structured_contentはPython側(geo_knowledge_item_schemas.py)でtypeごとのJSON Schema '
    '検証を行う。statusはcandidate→in_review→active/contested/rejected→superseded/'
    'deprecated/archivedの遷移のみ許可し、コード側で強制する（10章）';


-- ---- 8. geo_knowledge_reviews（承認履歴） ----

create table public.geo_knowledge_reviews (
    review_id            uuid primary key default gen_random_uuid(),
    knowledge_item_id    uuid not null references public.geo_knowledge_items(knowledge_item_id) on delete cascade,
    reviewer_id          text not null,
    reviewer_role        text not null
        check (reviewer_role in ('expert_reviewer', 'knowledge_steward', 'administrator')),
    review_decision      text not null
        check (review_decision in (
            'start_review', 'approve_active', 'approve_contested', 'reject', 'supersede',
            'deprecate', 'archive'
        )),
    review_comment       text,
    evidence_adequacy    text not null
        check (evidence_adequacy in ('sufficient', 'partial', 'insufficient')),
    applicability_checked boolean not null default false,
    change_diff          jsonb,
    previous_status      text not null,
    new_status           text not null,
    reviewed_at          timestamptz not null default now()
);

create index geo_knowledge_reviews_item_idx on public.geo_knowledge_reviews(knowledge_item_id);

alter table public.geo_knowledge_reviews enable row level security;

comment on table public.geo_knowledge_reviews is
    'Knowledge Itemの状態遷移を伴うレビュー記録。LLMは提案するだけで確定しない — '
    '権限を持つ人間の明示的な操作を、コードが権限・状態遷移・必須項目を検証した'
    '場合のみDBへ反映する（10章）。reviewer_id/roleは開発専用の軽量Role制御であり、'
    '本番の認証・認可を意味しない';


-- ---- 9. geo_cases（Case Intelligence） ----

create table public.geo_cases (
    case_id               uuid primary key default gen_random_uuid(),
    case_title            text not null,
    original_question     text not null,
    normalized_question   text,
    scope                 text,
    status                text not null default 'open'
        check (status in ('open', 'analyzing', 'closed')),
    analyst               text,
    expert_reviewer       text,
    start_date            date not null default current_date,
    as_of_date            date not null default current_date,
    assessment            text,
    business_implications text,
    final_output          jsonb,
    actual_outcome        text,
    post_case_evaluation  jsonb,
    closed_at             timestamptz,
    is_demo               boolean not null default false,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

create index geo_cases_status_idx on public.geo_cases(status);

create trigger geo_cases_set_updated_at
    before update on public.geo_cases
    for each row execute function public.set_updated_at();

alter table public.geo_cases enable row level security;

comment on table public.geo_cases is
    '個別案件（Case Intelligence、9章）。案件終了後もactual_outcome/'
    'post_case_evaluationで事後評価を保存し、過去の仮説は削除しない';


-- ---- 10. geo_case_hypotheses（主仮説・代替仮説） ----

create table public.geo_case_hypotheses (
    hypothesis_id             uuid primary key default gen_random_uuid(),
    case_id                   uuid not null references public.geo_cases(case_id) on delete cascade,
    hypothesis_statement      text not null,
    hypothesis_type           text not null
        check (hypothesis_type in ('main', 'alternative', 'rejected', 'superseded')),
    rationale                 text,
    supporting_claim_ids      uuid[] not null default '{}',
    contradicting_claim_ids   uuid[] not null default '{}',
    assumptions               text[] not null default '{}',
    confirmation_indicators   text[] not null default '{}',
    falsification_indicators  text[] not null default '{}',
    confidence                text not null default '判断不能'
        check (confidence in ('高', '中', '低', '判断不能')),
    status                    text not null default 'open'
        check (status in ('open', 'confirmed', 'rejected', 'superseded')),
    evaluation_result         text,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now()
);

create index geo_case_hypotheses_case_idx on public.geo_case_hypotheses(case_id);
create index geo_case_hypotheses_type_idx on public.geo_case_hypotheses(hypothesis_type);

create trigger geo_case_hypotheses_set_updated_at
    before update on public.geo_case_hypotheses
    for each row execute function public.set_updated_at();

alter table public.geo_case_hypotheses enable row level security;

comment on table public.geo_case_hypotheses is
    '案件の主仮説(main)・代替仮説(alternative)・却下(rejected)・置換済み(superseded)（9章）';


-- ---- 11. geo_claims（一般化された主張） ----

create table public.geo_claims (
    claim_id           uuid primary key default gen_random_uuid(),
    claim_text         text not null,
    normalized_claim   text,
    information_class  text not null
        check (information_class in (
            'observed_fact', 'actor_claim', 'external_assessment', 'internal_hypothesis'
        )),
    subject_entity_id  uuid references public.geo_entities(entity_id) on delete set null,
    predicate          text,
    object_entity_id   uuid references public.geo_entities(entity_id) on delete set null,
    object_value       jsonb,
    confidence         text not null default '判断不能'
        check (confidence in ('高', '中', '低', '判断不能')),
    valid_from         date,
    valid_to           date,
    origin_type        text not null
        check (origin_type in ('event', 'observation', 'document', 'knowledge_item', 'case', 'manual')),
    origin_id          uuid,
    is_demo            boolean not null default false,
    created_at         timestamptz not null default now()
);

create index geo_claims_subject_idx on public.geo_claims(subject_entity_id);
create index geo_claims_origin_idx on public.geo_claims(origin_type, origin_id);

alter table public.geo_claims enable row level security;

comment on table public.geo_claims is
    'イベント・文書・構造情報・分析から抽出した個別の主張（7.10章）。既存geo_eventsは'
    '「日時・場所・主体を持つ観測」、geo_claimsは「そこから派生する解釈を伴う主張」を'
    '表す。origin_type/origin_idで発生源を1つ持つ（DB FKは型が混在するため張らず、'
    'アプリ側(geo_claim_service.py)で整合性を検証する）。同一内容をgeo_eventsと'
    'geo_claimsへ二重に生登録しないこと（docs/geopolitical-knowledge-architecture.md 7章）';


-- ---- 12. geo_document_chunks（文書の分割・引用追跡） ----

create table public.geo_document_chunks (
    chunk_id       uuid primary key default gen_random_uuid(),
    document_id    uuid not null references public.geo_documents(document_id) on delete cascade,
    chunk_index    integer not null,
    page           integer,
    section        text,
    quoted_excerpt text not null,
    parser_version text,
    content_hash   text not null,
    created_at     timestamptz not null default now()
);

create unique index geo_document_chunks_unique_idx
    on public.geo_document_chunks(document_id, chunk_index);
create index geo_document_chunks_document_idx on public.geo_document_chunks(document_id);
create index geo_document_chunks_hash_idx on public.geo_document_chunks(content_hash);

alter table public.geo_document_chunks enable row level security;

comment on table public.geo_document_chunks is
    '既存geo_documents.extracted_text（全文）はそのまま維持しつつ、ページ・節・引用箇所'
    'を保持する分割単位を追加する（14章 Raw Snapshot／根拠追跡）';


-- ---- 13. geo_claim_evidence（Claimの根拠） ----

create table public.geo_claim_evidence (
    claim_evidence_id uuid primary key default gen_random_uuid(),
    claim_id          uuid not null references public.geo_claims(claim_id) on delete cascade,
    document_chunk_id uuid references public.geo_document_chunks(chunk_id) on delete cascade,
    event_id          uuid references public.geo_events(event_id) on delete cascade,
    observation_id    uuid references public.geo_observations(observation_id) on delete cascade,
    evidence_role     text not null
        check (evidence_role in ('supporting', 'contradicting', 'contextual', 'qualification')),
    directness        text not null default 'direct'
        check (directness in ('direct', 'indirect')),
    created_at        timestamptz not null default now(),
    constraint geo_claim_evidence_one_target check (
        (case when document_chunk_id is not null then 1 else 0 end
       + case when event_id is not null then 1 else 0 end
       + case when observation_id is not null then 1 else 0 end) = 1
    )
);

create index geo_claim_evidence_claim_idx on public.geo_claim_evidence(claim_id);

alter table public.geo_claim_evidence enable row level security;

comment on table public.geo_claim_evidence is
    'Claimを document_chunk / event / observation のいずれか1つへ接続する根拠関係'
    '（7.10章）。conflicting evidenceは削除せず並存させ、evidence_role=contradictingで'
    '明示する';


-- ---- 14. geo_knowledge_gaps（Deterministic Gap + Analytical Gap Candidate） ----

create table public.geo_knowledge_gaps (
    gap_id                       uuid primary key default gen_random_uuid(),
    case_id                      uuid not null references public.geo_cases(case_id) on delete cascade,
    gap_category                 text not null
        check (gap_category in ('deterministic', 'analytical_candidate')),
    gap_type                     text not null
        check (gap_type in (
            'missing_entity', 'missing_actor_profile', 'missing_relationship',
            'missing_dependency', 'missing_historical_context', 'missing_precedent',
            'missing_legal_basis', 'missing_quantitative_data', 'stale_knowledge',
            'conflicting_evidence', 'insufficient_source_diversity', 'missing_indicator',
            'applicability_gap', 'precedent_comparison_gap', 'causal_gap',
            'legal_institutional_gap', 'actor_intent_gap', 'countermeasure_gap',
            'enforcement_gap', 'alternative_explanation_gap', 'falsification_gap'
        )),
    gap_description               text not null,
    why_needed                    text,
    related_hypothesis_id         uuid references public.geo_case_hypotheses(hypothesis_id) on delete set null,
    available_evidence            text,
    missing_evidence              text,
    recommended_source_categories text[] not null default '{}',
    recommended_queries           text[] not null default '{}',
    priority                      text not null default 'medium'
        check (priority in ('high', 'medium', 'low')),
    uncertainty                   text,
    owner                         text,
    status                        text not null default 'open'
        check (status in ('open', 'in_progress', 'resolved')),
    created_at                    timestamptz not null default now()
);

create index geo_knowledge_gaps_case_idx on public.geo_knowledge_gaps(case_id);
create index geo_knowledge_gaps_category_idx on public.geo_knowledge_gaps(gap_category);

alter table public.geo_knowledge_gaps enable row level security;

comment on table public.geo_knowledge_gaps is
    'Deterministic Gap（コードが機械的に判定）とAnalytical Gap Candidate（LLMがEvidence'
    'に基づき提案、推測補完しない）をgap_categoryで区別する（12章）';


-- ---- 15. Issue Link 個別テーブル（汎用junctionは使わない） ----

create table public.geo_entity_issues (
    issue_id   uuid not null references public.geo_issues(issue_id) on delete cascade,
    entity_id  uuid not null references public.geo_entities(entity_id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (issue_id, entity_id)
);

alter table public.geo_entity_issues enable row level security;

create table public.geo_relationship_issues (
    issue_id        uuid not null references public.geo_issues(issue_id) on delete cascade,
    relationship_id uuid not null references public.geo_relationships(relationship_id) on delete cascade,
    created_at      timestamptz not null default now(),
    primary key (issue_id, relationship_id)
);

alter table public.geo_relationship_issues enable row level security;

create table public.geo_knowledge_item_issues (
    issue_id           uuid not null references public.geo_issues(issue_id) on delete cascade,
    knowledge_item_id  uuid not null references public.geo_knowledge_items(knowledge_item_id) on delete cascade,
    created_at         timestamptz not null default now(),
    primary key (issue_id, knowledge_item_id)
);

alter table public.geo_knowledge_item_issues enable row level security;

create table public.geo_case_issues (
    issue_id   uuid not null references public.geo_issues(issue_id) on delete cascade,
    case_id    uuid not null references public.geo_cases(case_id) on delete cascade,
    created_at timestamptz not null default now(),
    primary key (issue_id, case_id)
);

alter table public.geo_case_issues enable row level security;

comment on table public.geo_entity_issues is
    'EntityをIssueへ紐付ける個別junction（PostgREST/RLS/Cascadeの安定性を優先し、'
    '汎用resource_type/resource_id方式は採用しない）';
comment on table public.geo_relationship_issues is 'RelationshipをIssueへ紐付ける個別junction';
comment on table public.geo_knowledge_item_issues is 'Knowledge ItemをIssueへ紐付ける個別junction';
comment on table public.geo_case_issues is 'CaseをIssueへ紐付ける個別junction';


-- ---- 16. 既存 geo_source_registry の拡張（列削除・データ上書きなし） ----

alter table public.geo_source_registry
    add column if not exists source_owner text,
    add column if not exists trust_tier text
        check (trust_tier is null or trust_tier in ('A1', 'A2', 'B', 'C', 'D', 'E')),
    add column if not exists perspective text,
    add column if not exists geographic_scope text,
    add column if not exists thematic_scope text,
    add column if not exists languages text[] not null default '{}',
    add column if not exists access_method text,
    add column if not exists connector_type text,
    add column if not exists authentication_type text,
    add column if not exists credential_reference text,
    add column if not exists update_cadence text,
    add column if not exists expected_release_cycle text,
    add column if not exists automation_allowed boolean not null default false,
    add column if not exists content_storage_allowed boolean not null default false,
    add column if not exists embedding_allowed boolean not null default false,
    add column if not exists internal_redistribution_allowed boolean not null default false,
    add column if not exists external_redistribution_allowed boolean not null default false,
    add column if not exists license_notes text,
    add column if not exists terms_url text,
    add column if not exists robots_checked_at timestamptz,
    add column if not exists is_demo boolean not null default false;

comment on column public.geo_source_registry.trust_tier is
    'A1(法律/条約/政府決定/選管一次情報)/A2(政府公式統計)/B(権威ある研究機関)/'
    'C(契約済み専門家分析)/D(報道/OSINT)/E(内部分析/AI仮説)。既存source_tier'
    '(一次/二次/三次/四次)とは独立の列として保持する（6章）';


-- ---- 17. 既存 geo_documents の拡張（列削除・データ上書きなし） ----

alter table public.geo_documents
    add column if not exists request_parameters jsonb,
    add column if not exists response_headers jsonb,
    add column if not exists connector_version text,
    add column if not exists is_demo boolean not null default false;


-- =====================================================================
-- ロールバック手順（Phase 1では実行しない。記録のみ）
--
-- drop table if exists public.geo_case_issues;
-- drop table if exists public.geo_knowledge_item_issues;
-- drop table if exists public.geo_relationship_issues;
-- drop table if exists public.geo_entity_issues;
-- drop table if exists public.geo_knowledge_gaps;
-- drop table if exists public.geo_claim_evidence;
-- drop table if exists public.geo_document_chunks;
-- drop table if exists public.geo_claims;
-- drop table if exists public.geo_case_hypotheses;
-- drop table if exists public.geo_cases;
-- drop table if exists public.geo_knowledge_reviews;
-- drop table if exists public.geo_knowledge_items;
-- drop table if exists public.geo_dependencies;
-- drop table if exists public.geo_assets;
-- drop table if exists public.geo_relationships;
-- drop table if exists public.geo_issues;
-- drop table if exists public.geo_entity_aliases;
-- drop table if exists public.geo_entities;
--
-- alter table public.geo_documents
--     drop column if exists request_parameters,
--     drop column if exists response_headers,
--     drop column if exists connector_version,
--     drop column if exists is_demo;
--
-- alter table public.geo_source_registry
--     drop column if exists source_owner, drop column if exists trust_tier,
--     drop column if exists perspective, drop column if exists geographic_scope,
--     drop column if exists thematic_scope, drop column if exists languages,
--     drop column if exists access_method, drop column if exists connector_type,
--     drop column if exists authentication_type, drop column if exists credential_reference,
--     drop column if exists update_cadence, drop column if exists expected_release_cycle,
--     drop column if exists automation_allowed, drop column if exists content_storage_allowed,
--     drop column if exists embedding_allowed, drop column if exists internal_redistribution_allowed,
--     drop column if exists external_redistribution_allowed, drop column if exists license_notes,
--     drop column if exists terms_url, drop column if exists robots_checked_at,
--     drop column if exists is_demo;
-- =====================================================================
