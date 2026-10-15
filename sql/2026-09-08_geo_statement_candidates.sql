-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2B-3A-1: Statement Candidate
-- 作成日: 2026-09-08
-- 前提: sql/2026-09-08_geo_external_events.sql（geo_external_signals,
--       geo_external_signal_annotations, geo_actor_episode_candidates）、
--       sql/2026-09-08_geo_episode_clusters.sql（geo_episode_clusters,
--       geo_episode_cluster_members）、
--       sql/2026-09-08_geo_episode_split_candidates.sql（geo_episode_split_candidates）
--       実行済みであること
--
-- 目的: GKG annotation（geo_external_signal_annotations）のV2.1QUOTATIONSから
-- 引用・声明のcandidateを抽出し、Human Review向けの提案層として提示する。
-- speaker identityはGDELT Actor1/Actor2やGKG Personsから自動確定せず、抽出時は
-- あくまで候補（speaker_candidates/speaker_attribution_status）として保存する。
-- Phase2B-2BのEpisode Guard（split_group/artifact_group/outlier）をそのまま
-- 再利用し、acceptedなartifact_group Split Candidateに該当する場合のみ行を
-- 生成しない。article body取得は前提とせず、GKG由来Evidenceは常にunverified
-- Statement Candidateとして保存する。acceptedは「Statement/Speakerが確認済み」
-- の状態であり、confirmed_statement_kind/confirmed_statement_text/
-- confirmed_speaker_name/confirmed_speaker_typeの4項目が揃って初めて遷移できる。
--
-- 設計（1テーブル + 2関数）:
--   geo_statement_candidates            … annotation単位でGKG V2.1QUOTATIONSの
--                                          1スニペット=1行として保存する
--   geo_statement_candidate_apply_replace … candidate行のreplaceをannotation_id
--                                          単位でatomicに行う唯一の書き込み経路
--   geo_statement_candidate_update_status … Human Review状態更新（acceptedの
--                                          4項目確認必須化込み）を1トランザクション
--                                          にまとめる
--
-- 作成順: geo_statement_candidates → geo_statement_candidate_apply_replace
--        → geo_statement_candidate_update_status
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select count(*) from information_schema.tables
--     where table_schema='public' and table_name = 'geo_statement_candidates';
--   -- 期待値: 0（初回適用時）
--   select count(*) from information_schema.routines
--     where routine_schema='public' and routine_name in (
--       'geo_statement_candidate_apply_replace', 'geo_statement_candidate_update_status');
--   -- 期待値: 0（初回適用時）
-- =====================================================================


-- ---- 1. geo_statement_candidates ----

create table public.geo_statement_candidates (
    statement_candidate_id   uuid primary key default gen_random_uuid(),
    external_signal_id       uuid not null references public.geo_external_signals(external_signal_id) on delete cascade,
    annotation_id            uuid not null references public.geo_external_signal_annotations(annotation_id) on delete cascade,
    -- GKG V2.1QUOTATIONSの1スニペット=1行。同一annotationから複数スニペットが取れれば複数行になる。
    quote_offset             integer not null,
    quote_length             integer not null,
    quote_text               text not null,
    fingerprint              text not null,
    -- sha256(algorithm_version : external_signal_id : annotation.provider_record_id : quote_offset :
    -- quote_length)。annotationsはimmutable（Phase2A方針）なので、この組み合わせは安定して
    -- 再現可能な決定的キーになる。再実行のたびに同じスニペットは同じidentityを保つ。

    statement_kind           text not null
        check (statement_kind in ('direct_quote_candidate', 'attributed_statement_candidate',
                                    'quotation_fragment')),
    -- 抽出時のベストエフォート分類（geo_statement_candidate_service.py参照）。journalist/analyst
    -- interpretationは抽出時点では機械的に判別できないため、statement_kindの値としては保存
    -- しない。判別不能なものは全てquotation_fragmentとし、Human Reviewで実際にjournalist/
    -- analyst interpretationだと判明した場合はcandidate_status='rejected'＋rejection_reason=
    -- 'interpretive_fragment'として記録する（下記rejection_reason列）。

    speaker_candidates        jsonb not null default '[]'::jsonb,
    -- [{"name": "...", "speaker_type": "person"|"organization"|"unknown",
    --   "source": "allnames"|"persons"|"organizations", "distance_chars": 42,
    --   "role_text": "Iranian Parliament Speaker..."}], quote_offsetからの近さ昇順。
    -- 話者は個人・組織どちらもありうる（wsws.org "Foreign Ministry"の実例）。sourceがallnames
    -- の候補はGDELTのNERで型付けされていない生の名前列挙のため、person/organizationを機械的
    -- に判別できない場合はspeaker_type="unknown"のままにし、"person"へ安易にデフォルトしない。
    speaker_attribution_status text not null
        check (speaker_attribution_status in ('explicit', 'proximate_name', 'ambiguous', 'unknown')),
    -- explicit: quote本文/GKG verbに明示的帰属動詞+話者が隣接する場合のみ（現状データでは
    -- 未観測、将来のarticle body等での利用を見込んだ区分）。proximate_name: 近傍に候補が
    -- 1件明確にある（実データの主要ケース）。ambiguous: 複数候補が拮抗。unknown: 候補なし。
    -- いずれもHuman Review前のcandidateに過ぎず、自動確定しない。

    episode_link_status       text not null
        check (episode_link_status in ('linked', 'ambiguous', 'unlinked')),
    -- linked: external_signal_idに紐づくActor Episode Candidateが1件以上あり、それらが解決する
    -- Episode evidence context（episode_cluster_id×episode_evidence_status×split_candidate_id）
    -- がdistinctで1種類にまとまる（同一Clusterに属す複数Candidateが同じSignalを参照するだけの
    -- 正常なケースも含む）。
    -- ambiguous: 複数のdistinct Episode evidence contextに跨る（複数actor/複数Clusterにまたがる
    -- hub的signal）ため、1つのEpisode contextへ一意に決められない。
    -- unlinked: どのactor_episode_candidateにも紐づかない。捨てずに保存する。
    linked_actor_episode_candidate_ids jsonb not null default '[]'::jsonb,  -- 0/1/複数件
    episode_cluster_id        uuid references public.geo_episode_clusters(episode_cluster_id) on delete set null,
    -- episode_link_status='linked'で一意にClusterが定まる場合のみ設定。
    episode_evidence_status   text
        check (episode_evidence_status in ('normal', 'split_group', 'outlier_pending', 'unresolved_hold')),
    -- episode_link_status='linked'の場合のみ設定（Episode Guard、2B-2Bのcandidate_typeを踏襲
    -- しつつ精緻化）。artifact_excludedは値として現れない——**accepted**のartifact_group Split
    -- Candidateに該当する場合のみ行自体を生成しない（サービス側参照）。一方、まだacceptedで
    -- ない（candidate/reviewed状態の）Split/Artifact/Outlier Proposalが該当memberを覆っている
    -- 場合はunresolved_holdとして**行を生成する**（黙って除外せず、Human Reviewに「親Cluster
    -- の分割提案が未解決である」ことを可視化するため）。ambiguous/unlinkedの場合はnull。
    split_candidate_id        uuid references public.geo_episode_split_candidates(split_candidate_id) on delete set null,
    -- episode_evidence_status='split_group'の場合のみ、対応するsplit_group行を指す。

    candidate_status          text not null default 'candidate'
        check (candidate_status in ('candidate', 'reviewed', 'accepted', 'rejected')),
    rejection_reason          text check (rejection_reason in ('interpretive_fragment', 'other')),
    -- candidate_status='rejected'の場合のみ設定する。'interpretive_fragment'はHuman Reviewが
    -- journalist/analyst interpretationと判定した場合の標準理由。それ以外の却下理由は
    -- 'other'＋review_notesの自由記述で表現する。
    confirmed_statement_kind  text
        check (confirmed_statement_kind in ('direct_quote', 'attributed_statement')),
    -- 値の語彙をcandidate側のstatement_kind（direct_quote_candidate/attributed_statement_
    -- candidate/quotation_fragment）と意図的に分離する——confirmed_statement_kindはHuman
    -- Reviewが元記事等の一次情報を確認して初めて設定できる「確定されたStatement種別」であり、
    -- 抽出時の推測にすぎないcandidate側statement_kindとは別の語彙にすることで、値を見ただけで
    -- 「これは機械推測か人間確定か」を混同しないようにする。acceptedへの遷移時は必須（下記
    -- 1.3節のRPCガード）。statement_kind自体はfrozen前の抽出結果のまま変えない。
    confirmed_statement_text  text,
    -- quote_textはGDELT由来のimmutable/unverified evidence（GKG V2.1QUOTATIONSがそのまま入る
    -- だけで、正しく切り出せているか・文意が通っているかは未検証）。confirmed_statement_text
    -- はHuman Reviewが元記事・一次情報を確認したうえで確定させる「実際のStatement本文」であり、
    -- quote_textとは意図的に別列にして意味を分離する。acceptedへの遷移時は必須。
    confirmed_speaker_name    text,
    confirmed_speaker_type    text check (confirmed_speaker_type in ('person', 'organization')),
    -- speaker_candidatesの中から人間が選ぶか、自由入力で確定する。confirmed_speaker_typeは
    -- "unknown"を許容しない（speaker_candidates候補側のunknownとは異なり、Human Reviewが
    -- 一次情報等で確認して初めてperson/organizationのどちらかに確定する）。未設定のまま候補
    -- 段階に留まっている限りspeaker identityは一切確定していない。acceptedへの遷移時は
    -- confirmed_statement_kind/confirmed_statement_text/confirmed_speaker_name/confirmed_
    -- speaker_typeの4項目ともに必須（GDELT Evidenceのみでは確定扱いにせず、元記事等の
    -- 一次情報をHuman Reviewで確認して初めてacceptedにする）。

    reviewed_at               timestamptz,
    reviewed_by               text,
    review_notes              text,
    raw_metadata              jsonb,
    -- {"algorithm_version": "v1", "gkg_quote_tuple": {"offset":..,"length":..,"verb":"","quote":"..."},
    --  "considered_speaker_candidates": [...全候補...], "episode_guard_snapshot": {...}}
    is_demo                   boolean not null default false,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now()
);

create unique index geo_statement_candidates_fingerprint_idx on public.geo_statement_candidates(fingerprint);
create index geo_statement_candidates_signal_idx on public.geo_statement_candidates(external_signal_id);
create index geo_statement_candidates_annotation_idx on public.geo_statement_candidates(annotation_id);
create index geo_statement_candidates_status_idx on public.geo_statement_candidates(candidate_status);
create index geo_statement_candidates_episode_link_idx on public.geo_statement_candidates(episode_link_status);

create trigger geo_statement_candidates_set_updated_at
    before update on public.geo_statement_candidates
    for each row execute function public.set_updated_at();
alter table public.geo_statement_candidates enable row level security;

comment on table public.geo_statement_candidates is
    'GKG V2.1QUOTATIONS由来の引用・声明候補（Phase2B-3A-1）。speaker identityはGDELT Actor1/'
    'Actor2やGKG Personsから自動確定しない——quotation/statement本文中の明示的attribution '
    'evidenceのみを候補材料とし、確定はHuman Reviewのみが行う。acceptedは「Statement/Speaker'
    'が確認済み」の状態を意味し、confirmed_statement_kind/confirmed_statement_text/'
    'confirmed_speaker_name/confirmed_speaker_typeの4項目が揃って初めて遷移できる。'
    'Episode Guard（Phase2B-2B）を再利用し、acceptedなartifact_group Split Candidateに該当'
    'する場合は行を生成しない。Human Review前はActor Intelligence（Issue Position/Current '
    'Posture）を一切変更しない';


-- ---- 2. geo_statement_candidate_apply_replace（annotation_id単位、唯一の書き込み経路） ----

create or replace function public.geo_statement_candidate_apply_replace(
    p_annotation_id uuid,
    p_expected_frozen_fingerprints text[],     -- このannotation_idのみのreviewed/accepted/rejected fingerprint集合
    p_expected_candidate_fingerprints text[],  -- 同時点のcandidate状態fingerprint集合（このannotation_idのみ）
    p_new_rows jsonb   -- 各要素は本テーブルのderived列一式（fingerprint, statement_kind,
                        -- speaker_candidates, speaker_attribution_status, episode_link_status,
                        -- linked_actor_episode_candidate_ids, episode_cluster_id,
                        -- episode_evidence_status, split_candidate_id, quote_offset, quote_length,
                        -- quote_text, raw_metadata）。external_signal_id/is_demoは含めない
                        -- （RPC内でannotation行からauthoritativeに取得する）
) returns jsonb
language plpgsql
as $$
declare
    v_lock_key bigint := hashtextextended('geo_statement_candidates:' || p_annotation_id::text, 0);
    v_annotation record;
    v_current_frozen text[];
    v_current_candidate text[];
    v_new_fingerprints text[];
    v_row jsonb;
    v_inserted integer := 0;
    v_deleted integer := 0;
begin
    perform pg_advisory_xact_lock(v_lock_key);

    -- external_signal_id/is_demoはPython側の値を信用せず、annotation行から直接取得する
    -- （Split Candidateのparent Cluster is_demo authoritative取得と同じ思想）。annotationは
    -- Immutable（Phase2A方針）なのでFOR UPDATEしても実質競合は起きないが、将来の削除等に
    -- 備えて一貫してロックする。
    select * into v_annotation from geo_external_signal_annotations
     where annotation_id = p_annotation_id for update;
    if not found then
        raise exception 'statement_candidate_annotation_not_found: %', p_annotation_id;
    end if;

    select coalesce(array_agg(fingerprint order by fingerprint), '{}'::text[]) into v_current_frozen
      from geo_statement_candidates
     where annotation_id = p_annotation_id and candidate_status in ('reviewed', 'accepted', 'rejected');
    select coalesce(array_agg(fingerprint order by fingerprint), '{}'::text[]) into v_current_candidate
      from geo_statement_candidates
     where annotation_id = p_annotation_id and candidate_status = 'candidate';

    if v_current_frozen is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_frozen_fingerprints, '{}'::text[])) x), '{}'::text[])
       or v_current_candidate is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_candidate_fingerprints, '{}'::text[])) x), '{}'::text[])
    then
        raise exception 'statement_candidate_conflict: snapshot changed since read, retry';
    end if;

    select coalesce(array_agg(fingerprint), '{}'::text[]) into v_new_fingerprints
      from jsonb_array_elements(p_new_rows) r, lateral (select r->>'fingerprint' as fingerprint) x;

    delete from geo_statement_candidates
     where annotation_id = p_annotation_id and candidate_status = 'candidate'
       and not (fingerprint = any(v_new_fingerprints));
    get diagnostics v_deleted = row_count;

    for v_row in select * from jsonb_array_elements(p_new_rows)
    loop
        if v_row->>'fingerprint' = any(v_current_frozen) then
            continue;  -- frozen行は完全に不可侵
        end if;
        if v_row->>'fingerprint' = any(v_current_candidate) then
            -- candidate状態で既にfingerprint一致する行: derived metadata（episode_link_status
            -- 等、後からEpisode/Split状態が変わって変化しうる列）のみ更新する。
            update geo_statement_candidates set
                statement_kind = v_row->>'statement_kind',
                speaker_candidates = coalesce(v_row->'speaker_candidates', '[]'::jsonb),
                speaker_attribution_status = v_row->>'speaker_attribution_status',
                episode_link_status = v_row->>'episode_link_status',
                linked_actor_episode_candidate_ids = coalesce(v_row->'linked_actor_episode_candidate_ids', '[]'::jsonb),
                episode_cluster_id = nullif(v_row->>'episode_cluster_id', '')::uuid,
                episode_evidence_status = v_row->>'episode_evidence_status',
                split_candidate_id = nullif(v_row->>'split_candidate_id', '')::uuid,
                raw_metadata = v_row->'raw_metadata'
             where annotation_id = p_annotation_id and fingerprint = v_row->>'fingerprint';
            continue;
        end if;
        insert into geo_statement_candidates (
            external_signal_id, annotation_id, quote_offset, quote_length, quote_text, fingerprint,
            statement_kind, speaker_candidates, speaker_attribution_status,
            episode_link_status, linked_actor_episode_candidate_ids, episode_cluster_id,
            episode_evidence_status, split_candidate_id, candidate_status, raw_metadata, is_demo
        ) values (
            v_annotation.external_signal_id, p_annotation_id,
            (v_row->>'quote_offset')::integer, (v_row->>'quote_length')::integer, v_row->>'quote_text',
            v_row->>'fingerprint', v_row->>'statement_kind',
            coalesce(v_row->'speaker_candidates', '[]'::jsonb), v_row->>'speaker_attribution_status',
            v_row->>'episode_link_status', coalesce(v_row->'linked_actor_episode_candidate_ids', '[]'::jsonb),
            nullif(v_row->>'episode_cluster_id', '')::uuid, v_row->>'episode_evidence_status',
            nullif(v_row->>'split_candidate_id', '')::uuid, 'candidate', v_row->'raw_metadata', v_annotation.is_demo
        );
        v_inserted := v_inserted + 1;
    end loop;

    return jsonb_build_object('inserted', v_inserted, 'deleted', v_deleted);
end;
$$;

comment on function public.geo_statement_candidate_apply_replace is
    'geo_statement_candidate_service.generate_statement_candidatesから呼ばれる唯一の書き込み'
    '経路。annotation_id単位のadvisory lockと2-way snapshot比較（frozen/candidate fingerprint）'
    'を経てから、candidate状態行のみを洗い替える。既存candidate行とfingerprintが一致すれば'
    'identityを保ったままderived metadataのみUPDATEし、frozen行（reviewed/accepted/rejected）'
    'は一切変更しない。external_signal_id/is_demoはannotation行からauthoritativeに取得する';

revoke execute on function public.geo_statement_candidate_apply_replace(uuid, text[], text[], jsonb) from public, anon, authenticated;
grant execute on function public.geo_statement_candidate_apply_replace(uuid, text[], text[], jsonb) to service_role;


-- ---- 3. geo_statement_candidate_update_status（Human Review状態更新） ----

create or replace function public.geo_statement_candidate_update_status(
    p_statement_candidate_id uuid,
    p_new_status text,
    p_reviewed_by text,
    p_review_notes text default null,
    p_rejection_reason text default null,       -- p_new_status='rejected'の場合のみ意味を持つ
    p_confirmed_statement_kind text default null,
    p_confirmed_statement_text text default null,
    p_confirmed_speaker_name text default null,
    p_confirmed_speaker_type text default null
) returns jsonb
language plpgsql
as $$
declare
    v_row record;
    v_lock_key bigint;
begin
    select * into v_row from geo_statement_candidates where statement_candidate_id = p_statement_candidate_id;
    if not found then
        raise exception 'statement_candidate_not_found: %', p_statement_candidate_id;
    end if;

    -- apply_replaceと全く同じlock namespace/キーを使う（'geo_statement_candidates:'||annotation_id）。
    -- これにより同一annotationに対するregenerateとreview状態更新が直列化される。
    v_lock_key := hashtextextended('geo_statement_candidates:' || v_row.annotation_id::text, 0);
    perform pg_advisory_xact_lock(v_lock_key);

    -- lock取得後に再読込（stale状態検知：apply_replaceが同時にこの行をdelete/更新していないか）
    select * into v_row from geo_statement_candidates where statement_candidate_id = p_statement_candidate_id;
    if not found then
        raise exception 'statement_candidate_not_found: %', p_statement_candidate_id;
    end if;

    if not (
        (v_row.candidate_status = 'candidate' and p_new_status in ('reviewed', 'accepted', 'rejected'))
        or (v_row.candidate_status = 'reviewed' and p_new_status in ('accepted', 'rejected'))
    ) then
        raise exception 'statement_candidate_invalid_transition: % -> %', v_row.candidate_status, p_new_status;
    end if;

    if p_new_status = 'rejected' and p_rejection_reason is null then
        raise exception 'statement_candidate_rejection_reason_required: %', p_statement_candidate_id;
    end if;

    if p_new_status = 'accepted' then
        -- acceptedは「Statement/Speakerが確認済み」の状態を意味する。GDELT Evidenceのみでは
        -- 確定扱いにせず、Human Reviewがこの呼び出しで確認済みの4項目を明示的に渡すことを
        -- 強制する。quotation_fragmentのままの確定、confirmed_statement_text未設定のままの
        -- 確定はいずれも許さない。
        if coalesce(p_confirmed_statement_kind, v_row.confirmed_statement_kind) is null
            or coalesce(p_confirmed_statement_kind, v_row.confirmed_statement_kind)
                not in ('direct_quote', 'attributed_statement')
            or coalesce(p_confirmed_statement_text, v_row.confirmed_statement_text) is null
            or coalesce(p_confirmed_speaker_name, v_row.confirmed_speaker_name) is null
            or coalesce(p_confirmed_speaker_type, v_row.confirmed_speaker_type) is null
        then
            raise exception 'statement_candidate_accepted_requires_confirmation: %', p_statement_candidate_id;
        end if;
    end if;

    update geo_statement_candidates set
        candidate_status = p_new_status, reviewed_by = p_reviewed_by, review_notes = p_review_notes,
        reviewed_at = now(),
        rejection_reason = case when p_new_status = 'rejected' then p_rejection_reason else rejection_reason end,
        confirmed_statement_kind = coalesce(p_confirmed_statement_kind, confirmed_statement_kind),
        confirmed_statement_text = coalesce(p_confirmed_statement_text, confirmed_statement_text),
        confirmed_speaker_name = coalesce(p_confirmed_speaker_name, confirmed_speaker_name),
        confirmed_speaker_type = coalesce(p_confirmed_speaker_type, confirmed_speaker_type)
     where statement_candidate_id = p_statement_candidate_id;

    return jsonb_build_object('statement_candidate_id', p_statement_candidate_id, 'candidate_status', p_new_status);
end;
$$;

comment on function public.geo_statement_candidate_update_status is
    'geo_statement_candidate_service.update_statement_candidate_statusから呼ばれる唯一の書き込み'
    '経路。apply_replaceと同一のannotation_id単位advisory lockを取得したうえで対象行を再読込'
    '（stale ID検知）、遷移可否・rejection_reason必須化・accepted時のconfirmed_*4項目必須化を'
    '検証してからUPDATEする';

revoke execute on function public.geo_statement_candidate_update_status(uuid, text, text, text, text, text, text, text, text) from public, anon, authenticated;
grant execute on function public.geo_statement_candidate_update_status(uuid, text, text, text, text, text, text, text, text) to service_role;


-- =====================================================================
-- ロールバック手順（本migrationでは実行しない、記録のみ）
--
-- drop function if exists public.geo_statement_candidate_update_status(
--     uuid, text, text, text, text, text, text, text, text);
-- drop function if exists public.geo_statement_candidate_apply_replace(
--     uuid, text[], text[], jsonb);
-- drop table if exists public.geo_statement_candidates;
-- =====================================================================
