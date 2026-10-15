-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2B-1: Event / Episode Clustering
-- 作成日: 2026-09-08
-- 前提: sql/2026-09-08_geo_external_events.sql（geo_actor_episode_candidates,
--       geo_external_events）実行済みであること
--
-- 目的: geo_actor_episode_candidatesを actor_entity_id × quad_class × event_root_code
-- × is_demo × time proximity でdeterministicにグルーピングし、Actorごとの局面
-- （Episode Cluster）を作る。Candidate生成のみだったPhase2Aの上に、Clustering層を
-- 追加するのみで、Actor Intelligence本体（Core Profile/Decision Pattern/Current
-- Posture等）は自動更新しない。embedding/LLMは使わずルールベースのみで構築する。
--
-- 設計（2テーブル + 1関数）:
--   geo_episode_clusters         … Actor単位のEpisode Cluster（cluster_status=candidateの
--                                   間はgeo_episode_cluster_apply_rebuildにより毎回全件
--                                   から再構築される。reviewed/accepted/rejectedでfreeze）
--   geo_episode_cluster_members  … geo_actor_episode_candidatesとのN:1所属
--   geo_episode_cluster_apply_rebuild … rebuildable Clusterの削除→再構築を1トランザクション
--                                   で行う唯一の書き込み経路（advisory lock + 3-way snapshot
--                                   比較 + write integrity検証）
--
-- 作成順: geo_episode_clusters → geo_episode_cluster_members → geo_episode_cluster_apply_rebuild
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select count(*) from information_schema.tables
--     where table_schema='public' and table_name like 'geo_episode_cluster%';
--   -- 期待値: 0（初回適用時）
--   select count(*) from information_schema.routines
--     where routine_schema='public' and routine_name = 'geo_episode_cluster_apply_rebuild';
--   -- 期待値: 0（初回適用時）
-- =====================================================================


-- ---- 1. geo_episode_clusters（Actor単位のEpisode Cluster） ----

create table public.geo_episode_clusters (
    episode_cluster_id       uuid primary key default gen_random_uuid(),
    actor_entity_id           uuid not null references public.geo_entities(entity_id) on delete cascade,
    quad_class                integer not null,
    event_root_code           text not null,
    is_demo                   boolean not null default false,
    -- is_demoをidentityに含め、demo/real Candidateが同一Clusterへ混在しないようにする
    -- （unique indexにも含める）。
    cluster_sequence          integer not null default 1,
    -- 同一(actor_entity_id, quad_class, event_root_code, is_demo)キー内でも、
    -- geo_episode_cluster_apply_rebuild実行時にgapで複数グループに分かれた場合の一意化用の
    -- 通番。時系列順の意味は持たない（purely a uniqueness tie-breaker。時系列順は
    -- first_event_date/last_event_dateで判断する）。frozen Clusterが使用中の番号をskipし、
    -- 1から決定論的に再割当てされる（同じ入力なら常に同じ番号になる）。

    member_count              integer not null default 0,
    first_event_date          date,
    last_event_date           date,
    goldstein_min             numeric,
    goldstein_max             numeric,
    trajectory                 text not null default 'insufficient_data'
        check (trajectory in ('insufficient_data', 'stable', 'escalating', 'de_escalating', 'mixed')),
    -- goldstein_scaleのみをbasisとして算出する（geo_episode_cluster_service._compute_trajectory
    -- 参照）。avg_toneは判定にもrepresentative_*にも使わない（混同防止）。
    -- Actor Current Posture/Decision Pattern等への自動書き込みは一切行わない。

    representative_counterparties  jsonb not null default '[]'::jsonb,  -- 補助表示専用、cluster分割には使わない
    representative_geo             jsonb not null default '[]'::jsonb,  -- 補助表示専用

    cluster_status             text not null default 'candidate'
        check (cluster_status in ('candidate', 'reviewed', 'accepted', 'rejected')),
    -- cluster_status='candidate'のClusterのみgeo_episode_cluster_apply_rebuildによる自動
    -- 再構築（削除→再生成）の対象。reviewed/accepted/rejectedになった時点でmembershipは
    -- freezeされ、以後自動再構築の対象から常に除外される。Rejectは「この観測群をEpisode
    -- 追跡対象から外す」という隔離の意味で運用し、個々のCandidateの事実の正誤を意味しない。
    reviewed_at                 timestamptz,
    reviewed_by                 text,
    review_notes                 text,

    raw_metadata                jsonb,
    created_at                  timestamptz not null default now(),
    updated_at                   timestamptz not null default now()
);

create unique index geo_episode_clusters_identity_idx
    on public.geo_episode_clusters(actor_entity_id, quad_class, event_root_code, is_demo, cluster_sequence);
create index geo_episode_clusters_actor_idx
    on public.geo_episode_clusters(actor_entity_id, cluster_status);

create trigger geo_episode_clusters_set_updated_at
    before update on public.geo_episode_clusters
    for each row execute function public.set_updated_at();
alter table public.geo_episode_clusters enable row level security;

comment on table public.geo_episode_clusters is
    'GDELT Actor Episode Candidateをactor_entity_id×quad_class×event_root_code×is_demo×time '
    'proximityでdeterministicにグルーピングしたEpisode Cluster。cluster_status=candidateの間は'
    'geo_episode_cluster_apply_rebuild経由で毎回全件から再構築される（削除→再生成、投入順に'
    '依存しない）。reviewed/accepted/rejectedになった時点でmembershipはfreezeされ、以後自動'
    '再構築の対象外になる。trajectoryはgoldstein_scaleのみを根拠に機械的に算出した候補値で'
    'あり、Actor Current Posture/Decision Pattern等へは一切自動反映しない。Media Episode '
    'Candidateとの関連付けは本フェーズでは行わない';


-- ---- 2. geo_episode_cluster_members（Candidate所属） ----

create table public.geo_episode_cluster_members (
    cluster_member_id            uuid primary key default gen_random_uuid(),
    episode_cluster_id            uuid not null references public.geo_episode_clusters(episode_cluster_id) on delete cascade,
    actor_episode_candidate_id    uuid not null references public.geo_actor_episode_candidates(actor_episode_candidate_id) on delete cascade,
    created_at                     timestamptz not null default now()
);

create unique index geo_episode_cluster_members_candidate_idx
    on public.geo_episode_cluster_members(actor_episode_candidate_id);
-- 1 Candidate = 1 Cluster所属のみ。同一EventのActor1側/Actor2側は別々のactor_episode_candidate_id
-- を持つため、自然に別のClusterへ所属できる（Eventそのものは複製しない）。
create index geo_episode_cluster_members_cluster_idx
    on public.geo_episode_cluster_members(episode_cluster_id);
alter table public.geo_episode_cluster_members enable row level security;

comment on table public.geo_episode_cluster_members is
    'geo_actor_episode_candidatesとgeo_episode_clustersのN:1所属。candidate_status=rejectedの'
    'Actor Episode Candidateはここに含まれない（除外される）。freeze済み(reviewed/accepted/'
    'rejected)Clusterの行はgeo_episode_cluster_apply_rebuildによって削除・変更されない';


-- ---- 3. geo_episode_cluster_apply_rebuild（唯一の書き込み経路） ----
-- この関数のみがgeo_episode_clusters/geo_episode_cluster_membersのrebuildable
-- （candidate状態）行に対するDELETE/INSERTを行う。geo_episode_cluster_service.recluster_key
-- から呼ばれ、Python側から直接client.delete/client.insertでこの2テーブルを操作しない。

create or replace function public.geo_episode_cluster_apply_rebuild(
    p_actor_entity_id uuid,
    p_quad_class integer,
    p_event_root_code text,
    p_is_demo boolean,
    p_expected_frozen_member_ids uuid[],       -- Python側SELECT時点のfrozen Cluster所属candidate_id集合
    p_expected_rebuildable_cluster_ids uuid[], -- 同時点のcandidate状態Cluster ids
    p_expected_eligible_candidate_ids uuid[],  -- 同時点のeligible Candidate id集合
    -- 3つのsnapshot全てが一致する場合のみ書き込みを行う。いずれか1つでもロック取得後の
    -- 現在値と異なれば episode_cluster_key_conflict とし、Python側にSELECTからのやり直し
    -- （リトライ）をさせる。これにより (a) 同一keyへの同時recluster、(b) Python SELECTから
    -- RPC呼び出しまでの間の新規Candidate追加、をどちらも検知できる。
    -- 削除対象(p_delete_cluster_ids相当)は引数として受け取らない。外部入力を信用せず
    -- 関数内部でkey条件から再導出する（write integrity boundaryの強化）。
    p_new_clusters jsonb                   -- 新規に作るCluster群。各要素:
                                            -- {cluster_sequence, member_count, first_event_date,
                                            --  last_event_date, goldstein_min, goldstein_max,
                                            --  trajectory, representative_counterparties,
                                            --  representative_geo, member_candidate_ids: [uuid...]}
) returns jsonb
language plpgsql
as $$
declare
    v_lock_key bigint;
    v_current_frozen_ids uuid[];
    v_current_rebuildable_ids uuid[];
    v_current_eligible_ids uuid[];
    v_new_member_total_count integer;
    v_new_member_distinct_count integer;
    v_new_member_ids uuid[];
    v_cluster jsonb;
    v_new_cluster_id uuid;
    v_inserted_clusters integer := 0;
begin
    -- key単位のadvisory lock。関数を包む暗黙トランザクション終了時に自動解放される。
    v_lock_key := hashtextextended(
        p_actor_entity_id::text || '|' || p_quad_class::text || '|' || p_event_root_code || '|' || p_is_demo::text,
        0);
    perform pg_advisory_xact_lock(v_lock_key);

    -- (1) frozen Clusterのmember集合
    select coalesce(array_agg(m.actor_episode_candidate_id order by m.actor_episode_candidate_id), '{}'::uuid[])
      into v_current_frozen_ids
      from geo_episode_cluster_members m
      join geo_episode_clusters c on c.episode_cluster_id = m.episode_cluster_id
     where c.actor_entity_id = p_actor_entity_id and c.quad_class = p_quad_class
       and c.event_root_code = p_event_root_code and c.is_demo = p_is_demo
       and c.cluster_status in ('reviewed', 'accepted', 'rejected');

    -- (2) rebuildable(candidate状態)Cluster id集合 — この関数が実際に削除する対象そのもの。
    select coalesce(array_agg(episode_cluster_id order by episode_cluster_id), '{}'::uuid[])
      into v_current_rebuildable_ids
      from geo_episode_clusters
     where actor_entity_id = p_actor_entity_id and quad_class = p_quad_class
       and event_root_code = p_event_root_code and is_demo = p_is_demo
       and cluster_status = 'candidate';

    -- (3) eligible Candidate id集合（Python側と全く同じ定義: rejectedでない、frozenの一員でない、
    -- event_dateがnullでない）。Pythonが渡す集計元データと必ず一致するべき集合。
    select coalesce(array_agg(c.actor_episode_candidate_id order by c.actor_episode_candidate_id), '{}'::uuid[])
      into v_current_eligible_ids
      from geo_actor_episode_candidates c
      join geo_external_events e on e.external_event_id = c.external_event_id
     where c.actor_entity_id = p_actor_entity_id and e.quad_class = p_quad_class
       and e.event_root_code = p_event_root_code and c.is_demo = p_is_demo
       and c.candidate_status <> 'rejected'
       and e.event_date is not null
       and not exists (
           select 1 from geo_episode_cluster_members m2
           join geo_episode_clusters c2 on c2.episode_cluster_id = m2.episode_cluster_id
          where m2.actor_episode_candidate_id = c.actor_episode_candidate_id
            and c2.cluster_status in ('reviewed', 'accepted', 'rejected')
       );

    -- expected側もcoalesceでcanonicalize。unnest('{}'::uuid[])はゼロ行を返すためarray_aggは
    -- NULLになり、canonicalizeしないと「空集合同士」でもNULL IS DISTINCT FROM '{}' が
    -- trueになってしまい誤ってconflict扱いになるバグを防ぐ。
    if v_current_frozen_ids is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_frozen_member_ids, '{}'::uuid[])) x), '{}'::uuid[])
       or v_current_rebuildable_ids is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_rebuildable_cluster_ids, '{}'::uuid[])) x), '{}'::uuid[])
       or v_current_eligible_ids is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_eligible_candidate_ids, '{}'::uuid[])) x), '{}'::uuid[])
    then
        raise exception 'episode_cluster_key_conflict: snapshot changed since read, retry';
    end if;

    -- write integrity検証: p_new_clusters内の全member_candidate_idsが
    -- (a) 重複なく（同一candidateが複数の新規Clusterへ二重登録されていない）、
    -- (b) v_current_eligible_idsと完全一致する（eligible外の混入も、eligibleの取りこぼしも無い）
    -- ことをDELETE前に確認する。Pythonから渡されたペイロードを無条件に信用しない。
    select count(*), count(distinct elem::uuid)
      into v_new_member_total_count, v_new_member_distinct_count
      from jsonb_array_elements(p_new_clusters) c,
           jsonb_array_elements_text(coalesce(c->'member_candidate_ids', '[]'::jsonb)) elem;

    if v_new_member_total_count <> v_new_member_distinct_count then
        raise exception 'episode_cluster_validation_error: a candidate is assigned to more than one new cluster';
    end if;

    select coalesce(array_agg(distinct elem::uuid order by elem::uuid), '{}'::uuid[])
      into v_new_member_ids
      from jsonb_array_elements(p_new_clusters) c,
           jsonb_array_elements_text(coalesce(c->'member_candidate_ids', '[]'::jsonb)) elem;

    if v_new_member_ids is distinct from v_current_eligible_ids then
        raise exception 'episode_cluster_validation_error: new_clusters member set does not exactly match the eligible candidate snapshot';
    end if;

    -- 各Cluster要素ごとにも、宣言されたmember_countと実際のmember_candidate_ids配列長が
    -- 一致することを確認する（全体集合は一致していても、特定の1クラスタだけ内訳がずれている、
    -- というPython側の集計バグを追加で検出する）。
    if exists (
        select 1 from jsonb_array_elements(p_new_clusters) c
         where (c->>'member_count')::integer <> jsonb_array_length(coalesce(c->'member_candidate_ids', '[]'::jsonb))
    ) then
        raise exception 'episode_cluster_validation_error: member_count does not match member_candidate_ids length for at least one cluster';
    end if;

    -- rebuildable(candidate状態)Clusterを削除（on delete cascadeでmemberも削除される）。
    -- 削除対象は外部から受け取らず、上のsnapshotチェックで確認済みの条件そのもので再導出する。
    delete from geo_episode_clusters
     where actor_entity_id = p_actor_entity_id and quad_class = p_quad_class
       and event_root_code = p_event_root_code and is_demo = p_is_demo
       and cluster_status = 'candidate';

    -- 新規Clusterと所属Candidateを挿入。
    for v_cluster in select * from jsonb_array_elements(p_new_clusters)
    loop
        insert into geo_episode_clusters (
            actor_entity_id, quad_class, event_root_code, is_demo, cluster_sequence,
            member_count, first_event_date, last_event_date, goldstein_min, goldstein_max,
            trajectory, representative_counterparties, representative_geo, cluster_status
        ) values (
            p_actor_entity_id, p_quad_class, p_event_root_code, p_is_demo,
            (v_cluster->>'cluster_sequence')::integer,
            (v_cluster->>'member_count')::integer,
            (v_cluster->>'first_event_date')::date,
            (v_cluster->>'last_event_date')::date,
            (v_cluster->>'goldstein_min')::numeric,
            (v_cluster->>'goldstein_max')::numeric,
            v_cluster->>'trajectory',
            coalesce(v_cluster->'representative_counterparties', '[]'::jsonb),
            coalesce(v_cluster->'representative_geo', '[]'::jsonb),
            'candidate'
        ) returning episode_cluster_id into v_new_cluster_id;

        v_inserted_clusters := v_inserted_clusters + 1;

        insert into geo_episode_cluster_members (episode_cluster_id, actor_episode_candidate_id)
        select v_new_cluster_id, (elem)::uuid
          from jsonb_array_elements_text(coalesce(v_cluster->'member_candidate_ids', '[]'::jsonb)) elem;
    end loop;

    return jsonb_build_object('inserted_clusters', v_inserted_clusters);
end;
$$;

comment on function public.geo_episode_cluster_apply_rebuild is
    'geo_episode_cluster_service.recluster_keyから呼ばれる唯一の書き込み経路。1回のRPC呼び出し'
    '＝1つの暗黙トランザクションであるため、途中でinsertが失敗（制約違反等）すればDELETEも含めて'
    '全体がロールバックされ、旧Cluster構造がそのまま維持される。pg_advisory_xact_lockでkey単位'
    '直列化のうえ、frozen membership／rebuildable cluster ids／eligible candidate idsの3つ全てを'
    'snapshot比較し、かつ新規member集合がeligible candidate集合と完全一致することを検証してから'
    '書き込む（write integrity boundary）';

-- このアプリは既存の全テーブルに対してservice_role keyのみを使い（geo_db_client.SupabaseClient、
-- config_utils.py参照）、ブラウザから直接Supabaseを叩く経路は存在しない（frontendは常に
-- FastAPI経由）。既存の読み取り専用RPC（get_relationship_graph_for_issue等）にはこれまで
-- GRANT/REVOKEの明示指定が無かった（Postgres関数はデフォルトでPUBLICにEXECUTE権限が付与
-- される）。本関数は初のWRITE系RPCであり、意図しないロール（anon/authenticated）から直接
-- 呼べる状態を残さないよう、本関数に限り明示的に権限を絞る（既存の読み取り専用RPC群への
-- 遡及適用はスコープ外）。
revoke execute on function public.geo_episode_cluster_apply_rebuild(
    uuid, integer, text, boolean, uuid[], uuid[], uuid[], jsonb) from public, anon, authenticated;
grant execute on function public.geo_episode_cluster_apply_rebuild(
    uuid, integer, text, boolean, uuid[], uuid[], uuid[], jsonb) to service_role;


-- =====================================================================
-- ロールバック手順（本migrationでは実行しない、記録のみ）
--
-- drop function if exists public.geo_episode_cluster_apply_rebuild(
--     uuid, integer, text, boolean, uuid[], uuid[], uuid[], jsonb);
-- drop table if exists public.geo_episode_cluster_members;
-- drop table if exists public.geo_episode_clusters;
-- =====================================================================
