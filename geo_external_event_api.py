"""地政学ナレッジ＆インテリジェンスDB Phase2A: External Event / Actor & Media Episode
Candidate APIルーター。

既存route構造を踏襲し、/api/geo/* に独立してマウントする。既存api_server.pyの変更は
「このモジュールのimportとinclude_routerの2行追加」のみ。

external-eventsとactor-episode-candidates/media-episode-candidatesは別リソースであり、
単一routerの下に複数のtop-levelパスとして定義する（前者のsub-pathにしない）。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_actor_episode_service as actor_svc
import geo_connector_gdelt_stream as gdelt_stream
import geo_episode_cluster_service as cluster_svc
import geo_episode_split_candidate_service as split_svc
import geo_media_episode_service as media_svc
import geo_situation_link_service as link_svc
import geo_statement_candidate_service as statement_svc

_config = load_config()
_client = SupabaseClient(_config)

geo_external_intelligence_router = APIRouter(prefix="/api/geo")


# ---------------------------------------------------------------------------
# External Events
# ---------------------------------------------------------------------------

@geo_external_intelligence_router.get("/external-events")
def list_external_events(event_root_code: str | None = None, since: str | None = None,
                          limit: int = 20, offset: int = 0):
    params = {"select": "*",
              "order": "event_date.desc.nullslast,event_date_added.desc.nullslast,created_at.desc",
              "limit": str(limit), "offset": str(offset)}
    if event_root_code:
        params["event_root_code"] = f"eq.{event_root_code}"
    if since:
        params["event_date_added"] = f"gte.{since}"
    return {"events": _client.select("geo_external_events", params)}


@geo_external_intelligence_router.get("/external-events/{external_event_id}/signals")
def get_signals_for_event(external_event_id: str):
    link_rows = _client.select("geo_external_event_signal_links", {
        "select": "*", "external_event_id": f"eq.{external_event_id}",
    })
    signal_ids = [r["external_signal_id"] for r in link_rows]
    if not signal_ids:
        return {"signals": []}
    signals = _client.select("geo_external_signals", {
        "select": "*", "external_signal_id": f"in.({','.join(signal_ids)})",
    })
    return {"signals": signals, "links": link_rows}


class IngestExternalEventsRequest(BaseModel):
    since: str | None = None
    until: str | None = None
    max_batches: int = 1
    requested_by: str = "manual"


@geo_external_intelligence_router.post("/external-events/ingest")
def ingest_external_events(req: IngestExternalEventsRequest):
    try:
        return gdelt_stream.run_gdelt_structured_ingestion(
            _client, since=req.since, until=req.until, max_batches=req.max_batches,
            requested_by=req.requested_by)
    except gdelt_stream.GdeltStreamDisabledError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ---------------------------------------------------------------------------
# Actor Episode Candidates
# ---------------------------------------------------------------------------

@geo_external_intelligence_router.get("/actor-episode-candidates")
def list_actor_episode_candidates(actor_entity_id: str | None = None, candidate_status: str | None = None,
                                   limit: int = 50, offset: int = 0):
    return actor_svc.list_actor_episode_candidates(
        _client, actor_entity_id=actor_entity_id, candidate_status=candidate_status,
        limit=limit, offset=offset)


class UpdateActorEpisodeCandidateRequest(BaseModel):
    candidate_status: str
    reviewed_by: str
    review_notes: str | None = None


@geo_external_intelligence_router.patch("/actor-episode-candidates/{candidate_id}")
def update_actor_episode_candidate(candidate_id: str, req: UpdateActorEpisodeCandidateRequest):
    try:
        result = actor_svc.update_actor_episode_candidate_status(
            _client, candidate_id=candidate_id, candidate_status=req.candidate_status,
            reviewed_by=req.reviewed_by, review_notes=req.review_notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if req.candidate_status == "rejected":
        # Phase2B-1: rejected化されたCandidateが属していたkeyを再clusterし、
        # freeze済みClusterの一員でなければmembershipから除外する。
        cluster_svc.recluster_affected_keys(_client, actor_episode_candidate_ids=[candidate_id])
        # Phase2B-2A: Cluster IDを変えずLink入力だけが変わる経路を残さないよう、
        # recluster直後に同じis_demoスコープのSituation Linkも再計算する。
        link_svc.recompute_situation_links(_client, is_demo=bool(result.get("is_demo")))
    return result


# ---------------------------------------------------------------------------
# Actor Episode Clusters
# ---------------------------------------------------------------------------

@geo_external_intelligence_router.get("/episode-clusters")
def list_episode_clusters(actor_entity_id: str | None = None, cluster_status: str | None = None,
                           limit: int = 50, offset: int = 0):
    return cluster_svc.list_episode_clusters(
        _client, actor_entity_id=actor_entity_id, cluster_status=cluster_status,
        limit=limit, offset=offset)


@geo_external_intelligence_router.get("/episode-clusters/{episode_cluster_id}/members")
def get_episode_cluster_members(episode_cluster_id: str):
    return {"members": cluster_svc.get_episode_cluster_members(_client, episode_cluster_id)}


class UpdateEpisodeClusterRequest(BaseModel):
    cluster_status: str
    reviewed_by: str
    review_notes: str | None = None


@geo_external_intelligence_router.patch("/episode-clusters/{episode_cluster_id}")
def update_episode_cluster(episode_cluster_id: str, req: UpdateEpisodeClusterRequest):
    try:
        result = cluster_svc.update_episode_cluster_status(
            _client, episode_cluster_id=episode_cluster_id, cluster_status=req.cluster_status,
            reviewed_by=req.reviewed_by, review_notes=req.review_notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if req.cluster_status == "rejected":
        # Phase2B-2A: rejected状態のClusterをSituation Link母集団から即時除外し、
        # そのClusterを参照するcandidate状態Linkを残留させない。
        link_svc.recompute_situation_links(_client, is_demo=bool(result.get("is_demo")))
        # Phase2B-2B: rejected状態のClusterに紐づくcandidate状態のSplit/Outlier行を即時削除する
        # （geo_episode_split_candidate_apply_replace RPCがparent Cluster行をFOR UPDATEするため、
        # 並行実行中のregenerateとはこの呼び出しも含めて直列化される）。
        split_svc.purge_candidate_rows_for_rejected_parent(_client, parent_cluster_id=episode_cluster_id)
    return result


# ---------------------------------------------------------------------------
# Situation Links
# ---------------------------------------------------------------------------

@geo_external_intelligence_router.get("/situation-links")
def list_situation_links(cluster_id: str | None = None, link_status: str | None = None,
                          link_type: str | None = None, limit: int = 50, offset: int = 0):
    return link_svc.list_situation_links(
        _client, cluster_id=cluster_id, link_status=link_status, link_type=link_type,
        limit=limit, offset=offset)


class UpdateSituationLinkRequest(BaseModel):
    link_status: str
    reviewed_by: str
    review_notes: str | None = None


@geo_external_intelligence_router.patch("/situation-links/{cluster_link_id}")
def update_situation_link(cluster_link_id: str, req: UpdateSituationLinkRequest):
    try:
        return link_svc.update_situation_link_status(
            _client, cluster_link_id=cluster_link_id, link_status=req.link_status,
            reviewed_by=req.reviewed_by, review_notes=req.review_notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Episode Split / Outlier Candidates (Phase2B-2B)
# ---------------------------------------------------------------------------

@geo_external_intelligence_router.get("/split-candidates")
def list_split_candidates(parent_cluster_id: str | None = None, candidate_type: str | None = None,
                           candidate_status: str | None = None, limit: int = 50, offset: int = 0):
    return split_svc.list_split_candidates(
        _client, parent_cluster_id=parent_cluster_id, candidate_type=candidate_type,
        candidate_status=candidate_status, limit=limit, offset=offset)


class UpdateSplitCandidateRequest(BaseModel):
    candidate_status: str
    reviewed_by: str
    review_notes: str | None = None


@geo_external_intelligence_router.patch("/split-candidates/{split_candidate_id}")
def update_split_candidate(split_candidate_id: str, req: UpdateSplitCandidateRequest):
    try:
        return split_svc.update_split_candidate_status(
            _client, split_candidate_id=split_candidate_id, candidate_status=req.candidate_status,
            reviewed_by=req.reviewed_by, review_notes=req.review_notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Statement Candidates (Phase2B-3A-1)
# ---------------------------------------------------------------------------

@geo_external_intelligence_router.get("/statement-candidates")
def list_statement_candidates(external_signal_id: str | None = None, episode_link_status: str | None = None,
                               statement_kind: str | None = None, candidate_status: str | None = None,
                               limit: int = 50, offset: int = 0):
    return statement_svc.list_statement_candidates(
        _client, external_signal_id=external_signal_id, episode_link_status=episode_link_status,
        statement_kind=statement_kind, candidate_status=candidate_status, limit=limit, offset=offset)


class UpdateStatementCandidateRequest(BaseModel):
    candidate_status: str
    reviewed_by: str
    review_notes: str | None = None
    rejection_reason: str | None = None
    confirmed_statement_kind: str | None = None
    confirmed_statement_text: str | None = None
    confirmed_speaker_name: str | None = None
    confirmed_speaker_type: str | None = None


@geo_external_intelligence_router.patch("/statement-candidates/{statement_candidate_id}")
def update_statement_candidate(statement_candidate_id: str, req: UpdateStatementCandidateRequest):
    try:
        return statement_svc.update_statement_candidate_status(
            _client, statement_candidate_id=statement_candidate_id, candidate_status=req.candidate_status,
            reviewed_by=req.reviewed_by, review_notes=req.review_notes, rejection_reason=req.rejection_reason,
            confirmed_statement_kind=req.confirmed_statement_kind,
            confirmed_statement_text=req.confirmed_statement_text,
            confirmed_speaker_name=req.confirmed_speaker_name, confirmed_speaker_type=req.confirmed_speaker_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Media Episode Candidates
# ---------------------------------------------------------------------------

@geo_external_intelligence_router.get("/media-episode-candidates")
def list_media_episode_candidates(media_source_id: str | None = None, candidate_status: str | None = None,
                                   limit: int = 50, offset: int = 0):
    return media_svc.list_media_episode_candidates(
        _client, media_source_id=media_source_id, candidate_status=candidate_status,
        limit=limit, offset=offset)


class UpdateMediaEpisodeCandidateRequest(BaseModel):
    candidate_status: str
    reviewed_by: str
    review_notes: str | None = None


@geo_external_intelligence_router.patch("/media-episode-candidates/{candidate_id}")
def update_media_episode_candidate(candidate_id: str, req: UpdateMediaEpisodeCandidateRequest):
    try:
        return media_svc.update_media_episode_candidate_status(
            _client, candidate_id=candidate_id, candidate_status=req.candidate_status,
            reviewed_by=req.reviewed_by, review_notes=req.review_notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
