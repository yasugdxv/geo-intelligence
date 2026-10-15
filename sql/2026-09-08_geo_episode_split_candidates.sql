-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2B-2B: Episode Split / Outlier Candidate
-- 作成日: 2026-09-08
-- 前提: sql/2026-09-08_geo_episode_clusters.sql（geo_episode_clusters,
--       geo_episode_cluster_members）、sql/2026-09-08_geo_episode_cluster_links.sql
--       （geo_episode_cluster_links）実行済みであること
--
-- 目的: member_count>=2のEpisode Cluster内部に、Evidence Edge Qualification
-- （geo_situation_link_serviceと同じ考え方）とevent_base_code統合で見つかる
-- 「複数の局面が1つのClusterに混在している疑い」を、人間向けの提案層として
-- 提示する。既存geo_episode_clusters/geo_episode_cluster_membersのmembershipは
-- 一切変更しない（実際の分割実行は将来フェーズの課題）。
--
-- 設計（1テーブル + 2関数）:
--   geo_episode_split_candidates            … Split Proposal(candidate_type=
--                                              split_group/artifact_group)と
--                                              Outlier(candidate_type=outlier)
--   geo_episode_split_candidate_apply_replace … candidate行のreplaceをparent_
--                                              cluster_id単位でatomicに行う
--                                              唯一の書き込み経路
--   geo_episode_split_candidate_update_status … Human Review状態更新（sibling
--                                              一括更新＋parent freeze検証）を
--                                              1トランザクションにまとめる
--
-- 作成順: geo_episode_split_candidates → geo_episode_split_candidate_apply_replace
--        → geo_episode_split_candidate_update_status
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select count(*) from information_schema.tables
--     where table_schema='public' and table_name = 'geo_episode_split_candidates';
--   -- 期待値: 0（初回適用時）
--   select count(*) from information_schema.routines
--     where routine_schema='public' and routine_name in (
--       'geo_episode_split_candidate_apply_replace', 'geo_episode_split_candidate_update_status');
--   -- 期待値: 0（初回適用時）
-- =====================================================================


-- ---- 1. geo_episode_split_candidates ----

create table public.geo_episode_split_candidates (
    split_candidate_id     uuid primary key default gen_random_uuid(),
    parent_cluster_id      uuid not null references public.geo_episode_clusters(episode_cluster_id) on delete cascade,
    candidate_type          text not null check (candidate_type in ('split_group', 'artifact_group', 'outlier')),
    -- split_group: parent_cluster_id内でmeaningful component(member_count>=2)が2つ以上ある
    -- 場合に、そのcomponentごとに1行。ただしそのcomponentの内部連結が専らwire-roundup由来の
    -- 共有source_url（system-wideに閾値以上のGlobalEventIDへ分裂し、かつevent_base_code・
    -- geo(country)の両方に異質性がある、Bridging Member Diagnosticsで実証されたPattern B）
    -- のみで成り立っている場合はcandidate_type=artifact_groupとし、「もう1つの実世界Episode」
    -- ではなく「Cleanup対象（除去すべき混入）」であることを明示する（6回目・7回目レビュー
    -- 修正）。artifact_groupは将来フェーズでも新規Episodeへ自動変換しない。
    -- outlier: member_count==1のtopic groupに属す単独memberごとに1行。
    sibling_group_key       uuid not null,
    -- 同一Split Proposal（1つのSplit判断単位）を束ねるキー。split_group/artifact_group行は兄弟行
    -- （例:root08のHormuz行とAP roundup行）全てが、Proposal全体から決定的に導出された同じ
    -- proposal_fingerprint由来のsibling_group_keyを持ち、常に同じcandidate_statusを保つ
    -- （Human Reviewの単位はsibling_group_key、個々のsplit_candidate_idではない）。outlier行は
    -- 自分自身の（単独memberの）fingerprintから決定的に導出したUUIDをsibling_group_keyとして
    -- 持つ（split_candidate_idとは別の独立した決定的値。1件単位のReviewを、split_group/
    -- artifact_groupと同じ「sibling_group_keyでフィルタして更新」というコードパスで扱うため）。
    fingerprint              text not null,
    -- split_group/artifact_group行はproposal_fingerprintを内包した二層fingerprint、outlier行は
    -- 自己完結fingerprintとする（詳細はgeo_episode_split_candidate_service.py参照）。
    -- delete→ランダムUUID再insertではなく、同じ分析結果には同じidentityを持たせるための安定
    -- キー。generate_split_candidatesの再実行では、既存fingerprint（review済み含む）と一致する
    -- 行には一切触れない。algorithm_versionをキーに含めるため、将来アルゴリズムを変更しても
    -- 過去のfingerprintと衝突しない。
    member_candidate_ids    jsonb not null default '[]'::jsonb,
    -- split_group/artifact_group: そのtopic groupに属す actor_episode_candidate_id の配列（>=2件）。
    -- outlier: 単独memberのactor_episode_candidate_idを1件だけ含む配列。
    dominant_event_base_code text,
    -- topic group内のevent_base_codeが単一値の場合のみ設定する。evidence group内にgeo一致で
    -- 救済されたtrusted edge経由で複数base_codeが混在した場合はNULL（ambiguous）。ambiguousな
    -- evidence groupは他groupとのbase_code統合に使わず単体のtopic groupとする。
    representative_geo      jsonb not null default '[]'::jsonb,
    representative_counterparties jsonb not null default '[]'::jsonb,
    separation_evidence     jsonb not null default '[]'::jsonb,
    -- 例: [{"signal": "base_code_family", "value": "080"},
    --      {"signal": "ambiguous_base_code_family", "value": ["080","087"]},
    --      {"signal": "shared_source_url_wire_roundup",
    --       "value": "http://.../ap-news-in-brief-.../article_....html",
    --       "distinct_global_event_id_count": 4,
    --       "distinct_event_base_codes": ["080","087"],
    --       "distinct_countries": ["US","UA","RU"]}]
    -- artifact_group行はshared_source_url_wire_roundupエントリを必ず1件含み、件数閾値だけでなく
    -- base_code・countryの異質性も判定根拠として記録する（分類根拠を人間が検証できるようにする）。
    cross_cluster_affinity  jsonb not null default '[]'::jsonb,
    -- outlier行専用（split_group/artifact_group行では空配列）: このmemberのsignalが他のどの
    -- Clusterと重なるか、既存geo_episode_cluster_linksが既にそれを裏付けているかを示す。
    -- 例: [{"target_cluster_id": "...", "shared_signal_ids": ["62ec7fd6..."],
    --       "existing_situation_link_id": "...", "existing_link_type": "related_context"}]
    candidate_status        text not null default 'candidate'
        check (candidate_status in ('candidate', 'reviewed', 'accepted', 'rejected')),
    -- 既存Actor/Media/Episode/Situation Candidateと同じHuman Review状態機械。candidateから
    -- reviewed/rejectedへの遷移はparent Cluster(cluster_status)がfreeze済み(reviewed/accepted/
    -- rejectedのいずれか)の場合のみ、acceptedへの遷移はparent Clusterがaccepted状態の場合のみ
    -- 許可する（Situation Linkと同じ思想）。acceptedになっても既存Cluster membershipには
    -- 一切書き込まない（実際の分割実行は将来フェーズの課題）。
    reviewed_at              timestamptz,
    reviewed_by              text,
    review_notes             text,
    raw_metadata             jsonb,
    -- Human Review時点のanalysis snapshotを保存する。
    -- {"algorithm_version": "v1", "parent_cluster_member_composition":
    --   [{"member_candidate_ids": [...], "base_code": "080", "is_ambiguous": false}, ...],
    --  "artifact_source_evaluation": {"source_url": "...", "event_count": 4,
    --   "base_codes": ["080","087"], "countries": ["US","UA","RU"],
    --   "qualifies_as_artifact": true, "min_events": 3, "min_base_codes": 2, "min_countries": 2}}
    -- fingerprintが変わらない限りcandidate行のderived metadata更新パス（apply_replace RPC）で
    -- 他のderived列と一緒にUPDATEされる（元データの変化を追跡できるようにする）。frozen行では
    -- 一切更新しない。
    is_demo                  boolean not null default false,
    -- Python側からの値を信用しない。geo_episode_split_candidate_apply_replace RPC内部で
    -- parent_cluster_id経由でgeo_episode_clusters.is_demoをauthoritativeに取得し、その値だけを
    -- INSERTする全行に設定する。
    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now()
);

create unique index geo_episode_split_candidates_fingerprint_idx
    on public.geo_episode_split_candidates(fingerprint);
create index geo_episode_split_candidates_parent_idx
    on public.geo_episode_split_candidates(parent_cluster_id, candidate_status);
create index geo_episode_split_candidates_sibling_idx
    on public.geo_episode_split_candidates(sibling_group_key);

create trigger geo_episode_split_candidates_set_updated_at
    before update on public.geo_episode_split_candidates
    for each row execute function public.set_updated_at();
alter table public.geo_episode_split_candidates enable row level security;

comment on table public.geo_episode_split_candidates is
    'Episode Cluster内部の再分割候補（Phase2B-2B）。既存geo_episode_clusters/geo_episode_cluster_'
    'membersのmembershipは一切変更しない、あくまで人間向けの提案層。candidate_type=split_groupは'
    'evidence edge qualification後にevent_base_codeで統合したtopic groupのうちmember_count>=2の'
    'ものが2つ以上ある場合にのみ生成される（単なるconnected componentの複数存在では生成しない）。'
    'candidate_type=artifact_groupはsplit_groupと同じmeaningful componentだが、その内部連結が'
    'system-wideに閾値以上のGlobalEventID・base_code・countryの異質性を持つ単一source_url'
    '（wire-roundup記事の機械的分割）のみに由来する場合に区別され、「もう1つの実世界Episode」'
    'ではなくCleanup対象として提示される（将来フェーズでも新規Episodeへ自動変換しない）。'
    '同一sibling_group_keyの兄弟行（split_group/artifact_group混在含む）は1つのProposalとして'
    '常に同じcandidate_statusを保つ。candidate_type=outlierはmember_count==1のtopic groupに'
    '属す単独memberごとに生成され、cross_cluster_affinityで他Clusterとの適合可能性（既存'
    'Situation Linkとの整合込み）を示す。fingerprintにより再計算時も同じ分析結果には同じ行を'
    '維持する。acceptedになってもClusterやActor Intelligence本体は一切変更しない';


-- ---- 2. geo_episode_split_candidate_apply_replace（唯一の書き込み経路） ----

create or replace function public.geo_episode_split_candidate_apply_replace(
    p_parent_cluster_id uuid,
    p_expected_frozen_fingerprints text[],     -- Python側SELECT時点のreviewed/accepted/rejected fingerprint集合（このparentのみ）
    p_expected_candidate_fingerprints text[],  -- 同時点のcandidate状態fingerprint集合（このparentのみ）
    p_new_rows jsonb                           -- 新規計算されたcandidate行群。各要素:
                                                -- {fingerprint, candidate_type, sibling_group_key,
                                                --  member_candidate_ids, dominant_event_base_code,
                                                --  representative_geo, representative_counterparties,
                                                --  separation_evidence, cross_cluster_affinity,
                                                --  raw_metadata}
                                                -- is_demoは含めない。RPC内でparent Clusterから
                                                -- authoritativeに取得する。
) returns jsonb
language plpgsql
as $$
declare
    v_lock_key bigint := hashtextextended('geo_episode_split_candidates:' || p_parent_cluster_id::text, 0);
    v_current_frozen text[];
    v_current_candidate text[];
    v_new_fingerprints text[];
    v_cluster record;
    v_row jsonb;
    v_inserted integer := 0;
    v_deleted integer := 0;
begin
    perform pg_advisory_xact_lock(v_lock_key);

    -- parent Cluster行をFOR UPDATEでロックする。これによりEpisode Cluster PATCHハンドラの
    -- cluster_status='rejected'更新＋purge_candidate_rows_for_rejected_parent呼び出しと、この
    -- RPCの実行が直列化される：先にrejectが確定していればここで拒否されるし、先にこのRPCが
    -- ロックを取っていればrejectはこのトランザクションの完了を待つ。is_demoもPython側の値を
    -- 信用せず、ここで直接取得する。
    select * into v_cluster from geo_episode_clusters
     where episode_cluster_id = p_parent_cluster_id for update;
    if not found then
        raise exception 'split_candidate_parent_cluster_not_found: %', p_parent_cluster_id;
    end if;
    if v_cluster.cluster_status = 'rejected' then
        raise exception 'split_candidate_parent_rejected: cannot write split candidates for rejected parent %',
            p_parent_cluster_id;
    end if;

    select coalesce(array_agg(fingerprint order by fingerprint), '{}'::text[])
      into v_current_frozen
      from geo_episode_split_candidates
     where parent_cluster_id = p_parent_cluster_id and candidate_status in ('reviewed', 'accepted', 'rejected');
    select coalesce(array_agg(fingerprint order by fingerprint), '{}'::text[])
      into v_current_candidate
      from geo_episode_split_candidates
     where parent_cluster_id = p_parent_cluster_id and candidate_status = 'candidate';

    if v_current_frozen is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_frozen_fingerprints, '{}'::text[])) x), '{}'::text[])
       or v_current_candidate is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_candidate_fingerprints, '{}'::text[])) x), '{}'::text[])
    then
        raise exception 'split_candidate_conflict: snapshot changed since read, retry';
    end if;

    select coalesce(array_agg(fingerprint), '{}'::text[]) into v_new_fingerprints
      from jsonb_array_elements(p_new_rows) r, lateral (select r->>'fingerprint' as fingerprint) x;

    -- 新しい計算結果に含まれなくなったcandidate状態の行だけ削除する（frozenは対象外、既に
    -- snapshot一致確認済みなので触らない）。v_current_candidate（削除前のスナップショット）は
    -- この後の「更新か新規insertか」の判定にそのまま使う。
    delete from geo_episode_split_candidates
     where parent_cluster_id = p_parent_cluster_id and candidate_status = 'candidate'
       and not (fingerprint = any(v_new_fingerprints));
    get diagnostics v_deleted = row_count;

    for v_row in select * from jsonb_array_elements(p_new_rows)
    loop
        if v_row->>'fingerprint' = any(v_current_frozen) then
            -- frozen行は完全に不可侵。identityもderived metadataも一切変更しない。
            continue;
        end if;
        if v_row->>'fingerprint' = any(v_current_candidate) then
            -- candidate状態で既に同じfingerprintが存在する行: split_candidate_id/candidate_status/
            -- created_atは維持したまま、candidate_type・raw_metadata込みのderived metadataのみ
            -- 最新の計算結果でUPDATEする（split_group⇔artifact_group再判定込み、
            -- cross_cluster_affinity等の陳腐化を防ぐ）。
            update geo_episode_split_candidates set
                candidate_type = v_row->>'candidate_type',
                dominant_event_base_code = v_row->>'dominant_event_base_code',
                representative_geo = coalesce(v_row->'representative_geo', '[]'::jsonb),
                representative_counterparties = coalesce(v_row->'representative_counterparties', '[]'::jsonb),
                separation_evidence = coalesce(v_row->'separation_evidence', '[]'::jsonb),
                cross_cluster_affinity = coalesce(v_row->'cross_cluster_affinity', '[]'::jsonb),
                raw_metadata = v_row->'raw_metadata'
             where parent_cluster_id = p_parent_cluster_id and fingerprint = v_row->>'fingerprint';
            continue;
        end if;
        insert into geo_episode_split_candidates (
            parent_cluster_id, candidate_type, sibling_group_key, fingerprint, member_candidate_ids,
            dominant_event_base_code, representative_geo, representative_counterparties,
            separation_evidence, cross_cluster_affinity, candidate_status, is_demo, raw_metadata
        ) values (
            p_parent_cluster_id, v_row->>'candidate_type', (v_row->>'sibling_group_key')::uuid,
            v_row->>'fingerprint', coalesce(v_row->'member_candidate_ids', '[]'::jsonb),
            v_row->>'dominant_event_base_code', coalesce(v_row->'representative_geo', '[]'::jsonb),
            coalesce(v_row->'representative_counterparties', '[]'::jsonb),
            coalesce(v_row->'separation_evidence', '[]'::jsonb),
            coalesce(v_row->'cross_cluster_affinity', '[]'::jsonb), 'candidate', v_cluster.is_demo,
            v_row->'raw_metadata'
        );
        v_inserted := v_inserted + 1;
    end loop;

    return jsonb_build_object('inserted', v_inserted, 'deleted', v_deleted);
end;
$$;

comment on function public.geo_episode_split_candidate_apply_replace is
    'geo_episode_split_candidate_service.generate_split_candidatesから呼ばれる唯一の書き込み経路。'
    'parent_cluster_id単位のadvisory lock、parent Cluster行のFOR UPDATE（rejected拒否）、'
    'frozen/candidate fingerprint集合の2-way snapshot比較を経てから、candidate状態行のみを'
    '洗い替える。既存candidate行とfingerprintが一致すればidentityを保ったままderived metadataの'
    'みUPDATEし、frozen行（reviewed/accepted/rejected）は一切変更しない';

revoke execute on function public.geo_episode_split_candidate_apply_replace(uuid, text[], text[], jsonb) from public, anon, authenticated;
grant execute on function public.geo_episode_split_candidate_apply_replace(uuid, text[], text[], jsonb) to service_role;


-- ---- 3. geo_episode_split_candidate_update_status（Human Review状態更新） ----

create or replace function public.geo_episode_split_candidate_update_status(
    p_split_candidate_id uuid,
    p_new_status text,
    p_reviewed_by text,
    p_review_notes text default null
) returns jsonb
language plpgsql
as $$
declare
    v_parent_cluster_id uuid;
    v_lock_key bigint;
    v_row record;
    v_cluster record;
    v_sibling_statuses text[];
    v_current_status text;
    v_updated_count integer;
begin
    -- advisory lockのキー算出にparent_cluster_idが要るので、lock取得前に一度だけ軽く引く。
    -- ここで読んだ内容（statusなど）は一切信用せず、lock取得後に必ず再読込する。
    select parent_cluster_id into v_parent_cluster_id
      from geo_episode_split_candidates where split_candidate_id = p_split_candidate_id;
    if not found then
        raise exception 'split_candidate_not_found: %', p_split_candidate_id;
    end if;

    -- apply_replaceと全く同じlock namespace/キーを使う（'geo_episode_split_candidates:'||parent_cluster_id）。
    -- これにより同一parentに対するregenerateとreview状態更新が直列化される。
    v_lock_key := hashtextextended('geo_episode_split_candidates:' || v_parent_cluster_id::text, 0);
    perform pg_advisory_xact_lock(v_lock_key);

    -- lock取得後に対象行を再読込する（stale ID検知：この間に行が削除・regenerateで
    -- 差し替えられていないか）。
    select * into v_row from geo_episode_split_candidates where split_candidate_id = p_split_candidate_id;
    if not found then
        raise exception 'split_candidate_not_found: %', p_split_candidate_id;
    end if;

    -- parent Cluster行もFOR UPDATEでロックする。Episode Cluster自体のPATCH（cluster_status変更）
    -- はgeo_episode_clustersをUPDATEする通常のトランザクションであるため、この行ロックにより
    -- 「review中にparentのstatusが変わる」という競合が自然にブロック/直列化される。
    select * into v_cluster from geo_episode_clusters
     where episode_cluster_id = v_row.parent_cluster_id for update;
    if not found then
        raise exception 'split_candidate_parent_cluster_not_found: %', v_row.parent_cluster_id;
    end if;

    -- 兄弟行のstatusを再確認する（TOCTOU対策）。全兄弟が同じstatusでなければ既に矛盾した
    -- 状態にあるということなので、silentに進めず例外にする。
    select array_agg(distinct candidate_status) into v_sibling_statuses
      from geo_episode_split_candidates where sibling_group_key = v_row.sibling_group_key;
    if array_length(v_sibling_statuses, 1) <> 1 then
        raise exception 'split_candidate_sibling_status_mismatch: sibling_group_key % has statuses %',
            v_row.sibling_group_key, v_sibling_statuses;
    end if;
    v_current_status := v_sibling_statuses[1];

    if not (
        (v_current_status = 'candidate' and p_new_status in ('reviewed', 'accepted', 'rejected'))
        or (v_current_status = 'reviewed' and p_new_status in ('accepted', 'rejected'))
    ) then
        raise exception 'split_candidate_invalid_transition: % -> %', v_current_status, p_new_status;
    end if;

    if p_new_status = 'accepted' then
        if v_cluster.cluster_status is distinct from 'accepted' then
            raise exception 'split_candidate_parent_not_accepted: parent % status=%',
                v_row.parent_cluster_id, v_cluster.cluster_status;
        end if;
    else
        if v_cluster.cluster_status not in ('reviewed', 'accepted', 'rejected') then
            raise exception 'split_candidate_parent_not_frozen: parent % status=%',
                v_row.parent_cluster_id, v_cluster.cluster_status;
        end if;
    end if;

    update geo_episode_split_candidates
       set candidate_status = p_new_status, reviewed_by = p_reviewed_by,
           review_notes = p_review_notes, reviewed_at = now()
     where sibling_group_key = v_row.sibling_group_key;
    get diagnostics v_updated_count = row_count;
    if v_updated_count = 0 then
        raise exception 'split_candidate_update_no_rows_affected: sibling_group_key %', v_row.sibling_group_key;
    end if;

    return jsonb_build_object('updated_rows', v_updated_count, 'sibling_group_key', v_row.sibling_group_key);
end;
$$;

comment on function public.geo_episode_split_candidate_update_status is
    'geo_episode_split_candidate_service.update_split_candidate_statusから呼ばれる唯一の書き込み'
    '経路。geo_episode_split_candidate_apply_replaceと同一のparent_cluster_id単位advisory lockを'
    '取得したうえで対象行を再読込（stale ID検知）、parent Cluster行をFOR UPDATEでロックし'
    '（regenerate/他のreview更新/Episode Cluster自体のPATCHと直列化）、sibling全行のstatus一致・'
    'parent freeze要件・遷移可否を検証してから、sibling_group_key単位で一括UPDATEする';

revoke execute on function public.geo_episode_split_candidate_update_status(uuid, text, text, text) from public, anon, authenticated;
grant execute on function public.geo_episode_split_candidate_update_status(uuid, text, text, text) to service_role;


-- =====================================================================
-- ロールバック手順（本migrationでは実行しない、記録のみ）
--
-- drop function if exists public.geo_episode_split_candidate_update_status(
--     uuid, text, text, text);
-- drop function if exists public.geo_episode_split_candidate_apply_replace(
--     uuid, text[], text[], jsonb);
-- drop table if exists public.geo_episode_split_candidates;
-- =====================================================================
