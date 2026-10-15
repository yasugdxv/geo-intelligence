-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2A: External Event / GKG Annotation
-- / Actor & Media Episode Candidate
-- 作成日: 2026-09-08
-- 前提: sql/2026-09-07_geo_external_signals.sql（geo_external_signals,
--       geo_external_signal_queries, geo_external_signal_query_matches）
--       sql/2026-08-05_geo_phase2a_ingestion.sql（geo_ingestion_runs）
--       sql/2026-08-04_geo_knowledge_core.sql（geo_entities, geo_themes, geo_knowledge_items）
--       実行済みであること
--
-- 目的: GDELT 2.0のraw feed（Event/Mentions/GKG、DOC APIとは別のデータストリーム）を使い、
-- Phase1 News DB（geo_external_signals）をURL経由でEnrichし、Actor Intelligence /
-- Media Intelligence向けのEpisode Candidate（人間レビュー待ちの自動抽出観測記録）を
-- 生成するところまでを実装する（Candidate生成まで。Actor/Media Intelligence本体は
-- 自動更新しない）。GDELT Event/GKGはValidated KnowledgeではなくExternal Observation
-- であり、既存Knowledge/Evidence関連テーブルは一切変更しない。
--
-- 設計（5テーブル）:
--   geo_external_events              … GlobalEventID単位（全世界は保存せず、既存Signalと
--                                       リンクしたものだけ）
--   geo_external_event_signal_links  … Event↔SignalのN:M（Mentions由来）
--   geo_external_signal_annotations  … GKGRECORDID単位（既存SignalのURLと一致したものだけ）
--   geo_actor_episode_candidates     … Actor向け自動抽出候補（新規独立、precedentとは非混在）
--   geo_media_episode_candidates     … Media向け自動抽出候補（新規独立、media_episodeとは非混在）
--
-- 作成順: geo_external_events → geo_external_event_signal_links
--        → geo_external_signal_annotations → geo_actor_episode_candidates
--        → geo_media_episode_candidates
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select count(*) from information_schema.tables
--     where table_schema='public' and (
--       table_name like 'geo_external_event%' or table_name like 'geo_%_episode_candidates'
--     );
--   -- 期待値: 0（初回適用時）
-- =====================================================================


-- ---- 1. geo_external_events（GlobalEventID単位） ----

create table public.geo_external_events (
    external_event_id       uuid primary key default gen_random_uuid(),
    provider                text not null default 'gdelt'
        check (provider in ('gdelt')),
    provider_event_id       text not null,  -- GLOBALEVENTID（桁数が大きいためtext保持）
    event_code              text,
    event_base_code         text,
    event_root_code         text,
    quad_class              integer,
    goldstein_scale         numeric,
    -- num_mentions/num_sources/num_articles/avg_toneはoriginating Event row自体が持つ値の
    -- スナップショットであり、本システムが以後観測したMentionsを加算した「現在の累積値」ではない
    -- （GDELTのbatchファイルは公開後immutableなため、再取得しても通常同じ値になる）
    num_mentions            integer,
    num_sources             integer,
    num_articles             integer,
    avg_tone                 numeric,
    actor1_name              text,
    actor1_country_code      text,
    actor2_name              text,
    actor2_country_code      text,
    action_geo_full_name     text,
    action_geo_country_code  text,
    action_geo_lat           numeric,
    action_geo_long          numeric,
    event_date               date,          -- Event列1 Day/SQLDATE（イベントが実際に起きた日）
    event_date_added         timestamptz,   -- Event列59 DATEADDED（GDELTがEvent streamへ記録した
                                             -- 時刻。Actor Episodeの発生日として使ってはならない）
    source_url               text,          -- Event列60 SOURCEURL
    source_role               text not null default 'event_data'
        check (source_role in ('event_data')),
    evidence_status           text not null default 'unverified'
        check (evidence_status in ('unverified')),
    batch_timestamp           text not null,  -- このEvent行を実際にdownloadしたexport.CSV.zip
                                               -- 自身のtimestamp(YYYYMMDDHHMMSS＝EventTimeDate)。
                                               -- 処理中のMention/GKG batchのtimestampとは別物
    raw_metadata              jsonb,
    ingestion_run_id          uuid references public.geo_ingestion_runs(ingestion_run_id) on delete set null,
    is_demo                   boolean not null default false,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now()
);

create unique index geo_external_events_provider_event_idx
    on public.geo_external_events(provider, provider_event_id);
create index geo_external_events_date_added_idx on public.geo_external_events(event_date_added desc);
create index geo_external_events_event_date_idx on public.geo_external_events(event_date desc);

create trigger geo_external_events_set_updated_at
    before update on public.geo_external_events
    for each row execute function public.set_updated_at();
alter table public.geo_external_events enable row level security;

comment on table public.geo_external_events is
    'GDELT Event(export.CSV)由来のGlobalEventID単位レコード。全世界のEventは保存せず、'
    'geo_external_signals（Phase1 News DB）のURLとMentions経由で一致したGlobalEventIDのみ'
    'geo_connector_gdelt_stream.run_gdelt_structured_ingestionが選別して保存する。'
    'source_role=event_data/evidence_status=unverified固定＝GDELT Eventは自動抽出Observationであり'
    'Validated Eventではない。event_date(Day)とevent_date_added(DATEADDED)は意味が異なる別列で'
    'あり、Actor Episodeの時系列にはevent_dateのみを使う。theme_id列は持たない（theme分類は'
    'geo_external_event_signal_links→geo_external_signal_query_matches→'
    'geo_external_signal_queries.theme_idという既存N:M経路から導出する）';


-- ---- 2. geo_external_event_signal_links（Event↔SignalのN:M、Mentions由来） ----

create table public.geo_external_event_signal_links (
    link_id             uuid primary key default gen_random_uuid(),
    external_event_id   uuid not null references public.geo_external_events(external_event_id) on delete cascade,
    external_signal_id  uuid not null references public.geo_external_signals(external_signal_id) on delete cascade,
    link_method         text not null
        check (link_method in ('gdelt_mention_exact_url', 'gdelt_mention_normalized_url')),
    mention_time_date   timestamptz,
    confidence          integer,
    mention_doc_tone    numeric,
    ingestion_run_id    uuid references public.geo_ingestion_runs(ingestion_run_id) on delete set null,
    created_at          timestamptz not null default now()
);

create unique index geo_external_event_signal_links_pair_idx
    on public.geo_external_event_signal_links(external_event_id, external_signal_id);
create index geo_external_event_signal_links_signal_idx
    on public.geo_external_event_signal_links(external_signal_id);
alter table public.geo_external_event_signal_links enable row level security;

comment on table public.geo_external_event_signal_links is
    'GDELT MentionsのMentionIdentifier(記事URL)がgeo_external_signals.url/canonical_urlと'
    '完全一致(gdelt_mention_exact_url)、または正規化後一致(gdelt_mention_normalized_url)した'
    '場合のみ作る。Phase2Aではfuzzy linkを使わない。同一ペアに複数Mentionが該当する場合は'
    'confidence最大(同点ならmention_time_date早い方)の1件のみinsertし、既存行がある場合は'
    '上書きしない（Immutable方針）';


-- ---- 3. geo_external_signal_annotations（GKGRECORDID単位） ----

create table public.geo_external_signal_annotations (
    annotation_id             uuid primary key default gen_random_uuid(),
    provider                  text not null default 'gdelt'
        check (provider in ('gdelt')),
    provider_record_id        text not null,  -- GKGRECORDID
    external_signal_id        uuid not null references public.geo_external_signals(external_signal_id) on delete cascade,
    source_common_name        text,           -- V2SourceCommonName（GKG生値。geo_external_signals
                                               -- .source_domainとは別物のため名前を分けて混同を防ぐ）
    document_identifier       text,           -- V2DocumentIdentifier
    gkg_date                  timestamptz,    -- V21DATE
    themes                    jsonb not null default '[]'::jsonb,
    persons                   jsonb not null default '[]'::jsonb,        -- [{"name","char_offset"}]
    organizations             jsonb not null default '[]'::jsonb,
    locations                 jsonb not null default '[]'::jsonb,
    tone                      numeric,
    tone_positive             numeric,
    tone_negative             numeric,
    tone_polarity             numeric,
    tone_activity_density     numeric,
    tone_self_group_density   numeric,
    tone_word_count           integer,
    batch_timestamp           text not null,
    raw_metadata              jsonb,
    ingestion_run_id          uuid references public.geo_ingestion_runs(ingestion_run_id) on delete set null,
    is_demo                   boolean not null default false,
    created_at                timestamptz not null default now()
);

create unique index geo_external_signal_annotations_provider_record_idx
    on public.geo_external_signal_annotations(provider, provider_record_id);
create index geo_external_signal_annotations_signal_idx
    on public.geo_external_signal_annotations(external_signal_id);
alter table public.geo_external_signal_annotations enable row level security;

comment on table public.geo_external_signal_annotations is
    'GDELT GKG由来のGKGRECORDID単位注釈。external_signal_idはNOT NULL＝既存geo_external_signals'
    'のurl/canonical_url/normalized_urlと一致したGKG行のみ保存する（全世界のGKGは保存しない）。'
    'persons/organizationsはActor/Media Episode Candidate生成の入力になる。GKGのraw文字列は'
    'そのままIntelligenceに使わず、ここで正規化しraw_metadataも保持する';


-- ---- 4. geo_actor_episode_candidates（新規独立、Actor向け自動抽出候補） ----

create table public.geo_actor_episode_candidates (
    actor_episode_candidate_id  uuid primary key default gen_random_uuid(),
    actor_entity_id             uuid not null references public.geo_entities(entity_id) on delete cascade,
    matched_name_text           text not null,   -- Event Actor1Name/Actor2Nameの生文字列（例: "TRUMP"）
    actor_role                  text not null check (actor_role in ('actor1', 'actor2')),
    match_method                text not null check (match_method in ('exact_canonical', 'alias')),
    fingerprint                 text not null,   -- sha256(actor_entity_id : provider_event_id : actor_role)

    -- 直接FKではなく既存N:M経路からの導出値（derived cache）であり、authoritativeな分類ではない。
    -- external_event_id → geo_external_event_signal_links → external_signal_id →
    -- geo_external_signal_query_matches → geo_external_signal_queries.theme_id という経路で
    -- 集めたtheme_idのdistinctが1件だけならその値を設定し、0件または複数件ならNULL。
    -- 表示・簡易フィルタ用の補助情報にすぎない
    theme_id                    uuid references public.geo_themes(theme_id) on delete set null,

    -- 必ずEvent（Actor1/Actor2）起点でのみ生成する。GKG Personsに名前が登場しただけの
    -- Actorへaction/counterpartyを帰属させない（誤帰属防止）
    external_event_id           uuid not null references public.geo_external_events(external_event_id) on delete cascade,
    observed_action              text,           -- event_code/action_geo等から組み立てた定型記述（事実記述のみ）
    counterparty_text            text,           -- 相手方Actor（actor_role='actor1'ならActor2Name、逆も同様）
    counterparty_entity_id       uuid references public.geo_entities(entity_id) on delete set null,
    event_date                   date,           -- geo_external_events.event_dateを複製（表示・検索用）

    -- external_event_idが既に単一FKでfingerprintもActor×Event×role単位のため、複数Eventを
    -- またぐ配列は持たない（複数EventをまたぐEpisode統合はPhase2A対象外）
    external_signal_ids          jsonb not null default '[]'::jsonb,  -- このEventにlinkした複数Signal
    supporting_gkg_record_ids    jsonb not null default '[]'::jsonb,  -- GKG Personsとして補助的に
                                                                       -- 言及されたGKGRECORDID（action根拠にはしない）
    source_count                 integer not null default 0,
    evidence_count                integer not null default 0,

    confidence                    text not null default '判断不能' check (confidence in ('高', '中', '低', '判断不能')),
    candidate_status               text not null default 'candidate'
        check (candidate_status in ('candidate', 'reviewed', 'accepted', 'rejected')),
    reviewed_at                    timestamptz,
    reviewed_by                    text,
    review_notes                   text,
    promoted_knowledge_item_id     uuid references public.geo_knowledge_items(knowledge_item_id) on delete set null,

    raw_metadata                   jsonb,
    ingestion_run_id               uuid references public.geo_ingestion_runs(ingestion_run_id) on delete set null,
    is_demo                        boolean not null default false,
    created_at                     timestamptz not null default now(),
    updated_at                     timestamptz not null default now(),
    last_observed_at               timestamptz not null default now()
);

create unique index geo_actor_episode_candidates_fingerprint_idx
    on public.geo_actor_episode_candidates(fingerprint);
create index geo_actor_episode_candidates_actor_idx
    on public.geo_actor_episode_candidates(actor_entity_id, candidate_status);

create trigger geo_actor_episode_candidates_set_updated_at
    before update on public.geo_actor_episode_candidates
    for each row execute function public.set_updated_at();
alter table public.geo_actor_episode_candidates enable row level security;

comment on table public.geo_actor_episode_candidates is
    '必ずGDELT Event(Actor1Name/Actor2Name)を起点に生成する。GKG Personsに名前が登場しただけの'
    'Actorへaction/counterpartyを帰属させない（記事に名前が書かれていることと、そのEventの実行'
    '主体であることは別、という誤帰属防止の原則）。GKG Personsはsupporting_gkg_record_idsとして'
    '補助的に参照するのみ。EventのActor1Name/Actor2Nameが既存geo_entities（find_entity_matches'
    'の完全一致、かつgeo_actor_intelligence_service.ACTOR_ENTITY_TYPESに含まれるentity_typeの'
    'みが対象）と1件だけ一致した場合にのみ、その役割(actor1/actor2)単位で自動生成する。あいまい'
    '一致(2件以上)・不一致・非actor型entity_typeは保存しない。同一fingerprint（同一Actor×同一'
    'Event×同一役割）への追加観測はexternal_signal_ids/supporting_gkg_record_idsへ追記し'
    'source_count/evidence_count/last_observed_atを更新するのみで新規行を作らない。'
    'precedent（geo_knowledge_items）とは独立のテーブルであり、Accept後の正式precedentへの'
    '転記フローはPhase2B以降の課題（promoted_knowledge_item_idは準備列のみ、Phase2Aでは常に'
    'null）。AcceptしてもActor Core Profile/Decision Pattern/Current Posture/Issue Positionは'
    '一切変更しない';


-- ---- 5. geo_media_episode_candidates（新規独立、Media向け自動抽出候補、GKG document単位） ----

create table public.geo_media_episode_candidates (
    media_episode_candidate_id  uuid primary key default gen_random_uuid(),
    media_source_id             text not null,  -- media_source_domain_map.jsonのsource_id
    source_domain               text not null,  -- 一致元ドメイン（例: "reuters.com"）
    provider_record_id          text not null,  -- GKGRECORDID（geo_external_signal_annotations
                                                 -- .provider_record_idと同値）
    annotation_id               uuid not null references public.geo_external_signal_annotations(annotation_id) on delete cascade,
    external_signal_id          uuid not null references public.geo_external_signals(external_signal_id) on delete cascade,
    fingerprint                 text not null,   -- sha256(media_source_id : provider_record_id)。
                                                 -- GKGRECORDID単位＝1 document=1候補にしかならない

    -- document-level observation（自動抽出のみ）
    headline                     text,           -- geo_external_signals.titleを複製（表示用）
    document_identifier          text,           -- V2DocumentIdentifier
    persons_mentioned             jsonb not null default '[]'::jsonb,
    organizations_mentioned       jsonb not null default '[]'::jsonb,
    themes_observed                jsonb not null default '[]'::jsonb,
    tone                            numeric,      -- このdocument単体のTone（GKG V15Tone、Event評価ではない）
    published_at                    timestamptz,  -- geo_external_signals.published_atを複製

    -- Eventは補助情報のみ。特定Eventへの評価としてtone/themes等を帰属させない
    related_external_event_ids      jsonb not null default '[]'::jsonb,

    confidence                       text not null default '判断不能' check (confidence in ('高', '中', '低', '判断不能')),
    candidate_status                  text not null default 'candidate'
        check (candidate_status in ('candidate', 'reviewed', 'accepted', 'rejected')),
    reviewed_at                        timestamptz,
    reviewed_by                        text,
    review_notes                       text,
    promoted_knowledge_item_id          uuid references public.geo_knowledge_items(knowledge_item_id) on delete set null,

    raw_metadata                        jsonb,
    ingestion_run_id                    uuid references public.geo_ingestion_runs(ingestion_run_id) on delete set null,
    is_demo                             boolean not null default false,
    created_at                          timestamptz not null default now(),
    updated_at                          timestamptz not null default now()
);

create unique index geo_media_episode_candidates_fingerprint_idx
    on public.geo_media_episode_candidates(fingerprint);
create index geo_media_episode_candidates_source_idx
    on public.geo_media_episode_candidates(media_source_id, candidate_status);

create trigger geo_media_episode_candidates_set_updated_at
    before update on public.geo_media_episode_candidates
    for each row execute function public.set_updated_at();
alter table public.geo_media_episode_candidates enable row level security;

comment on table public.geo_media_episode_candidates is
    'GKG document（geo_external_signal_annotations）単位のMedia観測候補。source_domainを'
    'domain→source_id対応表（sidecar docs/media-intelligence/media_source_domain_map.json、'
    '主要十数媒体のみ）で解決できた場合のみ1 document=1行で生成する。既存media_source_registry'
    '.json(103媒体、DB化なし)は変更しない。対応が無いdomainはunmatchedとして扱い行を作らない。'
    'tone/themes/personsはdocument単体のobservationであり、related_external_event_idsは補助'
    '参照に留め特定Eventへの評価として帰属させない（document全体のToneを「このEventへの論調」'
    'と解釈しない）。pro-China/anti-US/biased/editorial intent等は一切書き込まない（事実記述の'
    'み）。AcceptしてもMedia Profile/Media Patternは一切変更しない';


-- =====================================================================
-- ロールバック手順（本migrationでは実行しない、記録のみ）
--
-- drop table if exists public.geo_media_episode_candidates;
-- drop table if exists public.geo_actor_episode_candidates;
-- drop table if exists public.geo_external_signal_annotations;
-- drop table if exists public.geo_external_event_signal_links;
-- drop table if exists public.geo_external_events;
-- =====================================================================
