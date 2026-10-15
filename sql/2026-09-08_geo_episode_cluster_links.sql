-- =====================================================================
-- 地政学ナレッジ＆インテリジェンスDB Phase 2B-2A: Situation Linkage
-- 作成日: 2026-09-08
-- 前提: sql/2026-09-08_geo_episode_clusters.sql（geo_episode_clusters,
--       geo_episode_cluster_members）実行済みであること
--
-- 目的: 既存Episode Clusterのmembership・アルゴリズムを一切変更せず、Cluster間の
-- 関係を緩いN:Mリンクとして追加する。品質監査（member_count>=2の8 Cluster）の結果、
-- Cluster内部の品質はおおむね良好だが、別root_codeに分かれたClusterが同一の現実の
-- 局面（Situation）を扱っているケースがあり、これを表現できていないことが判明した。
-- ルールベース（embedding/LLM不使用）で、linked article/geo/counterparty/theme/
-- shared external eventのoverlapからLink候補をdeterministicに生成する。
--
-- 設計（1テーブル + 1関数）:
--   geo_episode_cluster_links       … Episode Cluster間のN:Mリンク（is_demoスコープ、
--                                      candidate状態の間はgeo_situation_link_apply_replace
--                                      により毎回全件から再構築される）
--   geo_situation_link_apply_replace … candidate Linkの削除→再構築を1トランザクション
--                                      で行う唯一の書き込み経路（advisory lock + 4-way
--                                      snapshot比較 + write integrity検証）
--
-- 作成順: geo_episode_cluster_links → geo_situation_link_apply_replace
--
-- preflight確認（適用前に実行し、結果を記録すること）:
--   select count(*) from information_schema.tables
--     where table_schema='public' and table_name = 'geo_episode_cluster_links';
--   -- 期待値: 0（初回適用時）
--   select count(*) from information_schema.routines
--     where routine_schema='public' and routine_name = 'geo_situation_link_apply_replace';
--   -- 期待値: 0（初回適用時）
-- =====================================================================


-- ---- 1. geo_episode_cluster_links（Episode Cluster間のN:Mリンク） ----

create table public.geo_episode_cluster_links (
    cluster_link_id       uuid primary key default gen_random_uuid(),
    source_cluster_id     uuid not null references public.geo_episode_clusters(episode_cluster_id) on delete cascade,
    target_cluster_id     uuid not null references public.geo_episode_clusters(episode_cluster_id) on delete cascade,
    -- source_cluster_id < target_cluster_id（uuidのbyte比較）で正規化して1ペア1行にする
    -- （無向グラフをA-B/B-Aの重複無しで表現する）。この不等号CHECKがself-link禁止も兼ねる
    -- （source==targetはCHECK違反になる）。Python側でも同じ順序へ正規化してから
    -- ペイロードを組み立てる。
    check (source_cluster_id < target_cluster_id),
    is_demo               boolean not null default false,
    -- real/demo Clusterを同一母集団・同一Linkに混ぜない。source/target両Clusterのis_demoが
    -- 一致する場合のみ許可する（write integrity検証はgeo_situation_link_apply_replace側で行う）。
    link_type             text not null check (link_type in ('same_situation_candidate', 'related_context')),
    link_score            numeric not null,
    link_reasons          jsonb not null default '[]'::jsonb,
    -- 例: [{"signal": "shared_external_event", "value": [...]}, {"signal": "geo_overlap", "value": ["Oman"]}]
    -- 人間がAcceptする前にスコアの根拠を確認できるようにする（ブラックボックス化しない）。
    link_status           text not null default 'candidate'
        check (link_status in ('candidate', 'reviewed', 'accepted', 'rejected')),
    -- 既存Actor/Media/Episode Candidateと同じHuman Review状態機械。acceptedになってもCluster
    -- membership・Actor Intelligence本体には一切書き込まない（表示・将来のSituation集約用の
    -- 補助情報に留まる）。
    reviewed_at           timestamptz,
    reviewed_by           text,
    review_notes          text,
    raw_metadata          jsonb,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

create unique index geo_episode_cluster_links_pair_idx
    on public.geo_episode_cluster_links(source_cluster_id, target_cluster_id);
create index geo_episode_cluster_links_source_idx on public.geo_episode_cluster_links(source_cluster_id);
create index geo_episode_cluster_links_target_idx on public.geo_episode_cluster_links(target_cluster_id);

create trigger geo_episode_cluster_links_set_updated_at
    before update on public.geo_episode_cluster_links
    for each row execute function public.set_updated_at();
alter table public.geo_episode_cluster_links enable row level security;

comment on table public.geo_episode_cluster_links is
    'Episode Cluster間の緩いN:Mリンク（Situation Linkage、Phase2B-2A）。Clusterのmembership・'
    'アルゴリズムは一切変更しない。同一actor内・別actor間どちらのペアも対象（実データでcross-actor'
    'linkの実例を確認済み）。is_demoの異なるCluster同士はリンクしない。link_status=candidateの間は'
    'geo_situation_link_apply_replaceにより当該is_demoスコープ内で再計算される。'
    'candidate→reviewed/rejectedの遷移は、source/target両Clusterが既にfreeze済み（cluster_status'
    'がreviewed/accepted/rejectedのいずれか）の場合のみ許可する。candidate→acceptedの遷移は'
    'さらに厳しく、source/target両Clusterがともにaccepted状態の場合のみ許可する'
    '（geo_situation_link_service.update_situation_link_status参照。理由: candidate状態のCluster'
    'はrecluster_keyで削除・再生成されうるため、そのCluster宛のLinkがreviewed以上のままだと'
    'on delete cascadeで人間のReview記録が予告なく失われる）。AcceptしてもClusterやActor '
    'Intelligence本体は一切変更しない';


-- ---- 2. geo_situation_link_apply_replace（唯一の書き込み経路） ----
-- この関数のみがgeo_episode_cluster_linksのcandidate状態行に対するDELETE/INSERTを行う。
-- geo_situation_link_service.recompute_situation_linksから呼ばれ、Python側から直接
-- client.delete/client.insertでこのテーブルを操作しない。

create or replace function public.geo_situation_link_apply_replace(
    p_is_demo boolean,                        -- このスコープのcandidate Linkだけをreplaceする
    p_expected_frozen_link_ids uuid[],        -- Python側SELECT時点のreviewed/accepted/rejected link id集合（このis_demoスコープ内）
    p_expected_candidate_link_ids uuid[],     -- 同時点のcandidate状態link id集合（このis_demoスコープ内）
    p_expected_population_cluster_ids uuid[], -- hub判定・ペア生成の基礎になった
                                               -- 「cluster_status<>'rejected' AND is_demo=p_is_demo」の
                                               -- Cluster id集合。ロック取得後に現在値と突合する
    p_new_links jsonb                         -- 新規に作るcandidate Link群。各要素:
                                               -- {source_cluster_id, target_cluster_id, link_type,
                                               --  link_score, link_reasons}
) returns jsonb
language plpgsql
as $$
declare
    v_lock_key bigint := hashtextextended('geo_episode_cluster_links_global', 0);
    v_current_frozen_ids uuid[];
    v_current_candidate_ids uuid[];
    v_current_population_ids uuid[];
    v_current_frozen_pairs jsonb;  -- 既存frozen linkの(source,target)ペア集合（衝突回避用）
    v_rejected_cluster_ids uuid[];
    v_demo_mismatch_count integer;
    v_link jsonb;
    v_source uuid;
    v_target uuid;
    v_inserted integer := 0;
    v_inserted_same_situation integer := 0;
    v_inserted_related_context integer := 0;
    v_skipped_frozen integer := 0;
begin
    perform pg_advisory_xact_lock(v_lock_key);

    select coalesce(array_agg(cluster_link_id order by cluster_link_id), '{}'::uuid[])
      into v_current_frozen_ids
      from geo_episode_cluster_links where link_status in ('reviewed', 'accepted', 'rejected') and is_demo = p_is_demo;
    select coalesce(array_agg(cluster_link_id order by cluster_link_id), '{}'::uuid[])
      into v_current_candidate_ids
      from geo_episode_cluster_links where link_status = 'candidate' and is_demo = p_is_demo;
    select coalesce(array_agg(episode_cluster_id order by episode_cluster_id), '{}'::uuid[])
      into v_current_population_ids
      from geo_episode_clusters where cluster_status <> 'rejected' and is_demo = p_is_demo;

    if v_current_frozen_ids is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_frozen_link_ids, '{}'::uuid[])) x), '{}'::uuid[])
       or v_current_candidate_ids is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_candidate_link_ids, '{}'::uuid[])) x), '{}'::uuid[])
       or v_current_population_ids is distinct from
        coalesce((select array_agg(x order by x) from unnest(coalesce(p_expected_population_cluster_ids, '{}'::uuid[])) x), '{}'::uuid[])
    then
        raise exception 'situation_link_conflict: snapshot changed since read, retry';
    end if;

    -- write integrity: rejected状態のClusterを新規Linkのendpointにしない
    select coalesce(array_agg(episode_cluster_id), '{}'::uuid[]) into v_rejected_cluster_ids
      from geo_episode_clusters where cluster_status = 'rejected';
    if exists (
        select 1 from jsonb_array_elements(p_new_links) l
         where (l->>'source_cluster_id')::uuid = any(v_rejected_cluster_ids)
            or (l->>'target_cluster_id')::uuid = any(v_rejected_cluster_ids)
    ) then
        raise exception 'situation_link_validation_error: new link references a rejected cluster';
    end if;

    -- write integrity: 全endpointが本当にp_is_demoスコープのClusterであることを確認する
    -- （cross-demo Linkの混入防止）
    select count(*) into v_demo_mismatch_count
      from jsonb_array_elements(p_new_links) l
      join geo_episode_clusters c1 on c1.episode_cluster_id = (l->>'source_cluster_id')::uuid
      join geo_episode_clusters c2 on c2.episode_cluster_id = (l->>'target_cluster_id')::uuid
     where c1.is_demo <> p_is_demo or c2.is_demo <> p_is_demo or c1.is_demo <> c2.is_demo;
    if v_demo_mismatch_count > 0 then
        raise exception 'situation_link_validation_error: new link references a cluster outside the is_demo scope, or mixes is_demo across endpoints';
    end if;

    delete from geo_episode_cluster_links where link_status = 'candidate' and is_demo = p_is_demo;

    select coalesce(jsonb_agg(jsonb_build_array(source_cluster_id, target_cluster_id)), '[]'::jsonb)
      into v_current_frozen_pairs
      from geo_episode_cluster_links where link_status in ('reviewed', 'accepted', 'rejected');

    for v_link in select * from jsonb_array_elements(p_new_links)
    loop
        v_source := least((v_link->>'source_cluster_id')::uuid, (v_link->>'target_cluster_id')::uuid);
        v_target := greatest((v_link->>'source_cluster_id')::uuid, (v_link->>'target_cluster_id')::uuid);
        -- frozen linkと同じペアなら黙ってskip（frozenを優先、衝突エラーにしない）
        if v_current_frozen_pairs @> jsonb_build_array(jsonb_build_array(v_source, v_target)) then
            v_skipped_frozen := v_skipped_frozen + 1;
            continue;
        end if;
        insert into geo_episode_cluster_links (
            source_cluster_id, target_cluster_id, is_demo, link_type, link_score, link_reasons, link_status
        ) values (
            v_source, v_target, p_is_demo, v_link->>'link_type', (v_link->>'link_score')::numeric,
            coalesce(v_link->'link_reasons', '[]'::jsonb), 'candidate'
        );
        v_inserted := v_inserted + 1;
        if v_link->>'link_type' = 'same_situation_candidate' then
            v_inserted_same_situation := v_inserted_same_situation + 1;
        else
            v_inserted_related_context := v_inserted_related_context + 1;
        end if;
    end loop;

    return jsonb_build_object(
        'inserted', v_inserted, 'inserted_same_situation_candidate', v_inserted_same_situation,
        'inserted_related_context', v_inserted_related_context, 'skipped_frozen_pair', v_skipped_frozen
    );
end;
$$;

comment on function public.geo_situation_link_apply_replace is
    'geo_situation_link_service.recompute_situation_linksから呼ばれる唯一の書き込み経路。1回の'
    'RPC呼び出し＝1つの暗黙トランザクションであるため、途中でinsertが失敗すればDELETEも含めて'
    '全体がロールバックされ、旧Link構造がそのまま維持される。pg_advisory_xact_lockで直列化のうえ、'
    'frozen link ids／candidate link ids／population cluster idsの3つ全てをis_demoスコープで'
    'snapshot比較し、かつ新規Linkがrejected Cluster・is_demoスコープ外を参照しないことを'
    '検証してから書き込む（write integrity boundary）';

-- このアプリは既存の全テーブルに対してservice_role keyのみを使い、ブラウザから直接Supabaseを
-- 叩く経路は存在しない（frontendは常にFastAPI経由）。本関数はgeo_episode_cluster_apply_rebuild
-- と同様のWRITE系RPCであり、意図しないロール（anon/authenticated）から直接呼べる状態を残さない
-- よう明示的に権限を絞る。
revoke execute on function public.geo_situation_link_apply_replace(boolean, uuid[], uuid[], uuid[], jsonb) from public, anon, authenticated;
grant execute on function public.geo_situation_link_apply_replace(boolean, uuid[], uuid[], uuid[], jsonb) to service_role;


-- =====================================================================
-- ロールバック手順（本migrationでは実行しない、記録のみ）
--
-- drop function if exists public.geo_situation_link_apply_replace(
--     boolean, uuid[], uuid[], uuid[], jsonb);
-- drop table if exists public.geo_episode_cluster_links;
-- =====================================================================
