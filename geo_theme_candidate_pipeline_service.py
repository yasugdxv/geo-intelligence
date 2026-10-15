"""Phase C: Episode Cluster Review → Theme Candidate Sync — 運用可視化・手動実行。

既存のCore Generation/Fingerprint/Lifecycle/Hallucination Guard（geo_theme_candidate_service.py）
・Episode Cluster生成/判定（geo_episode_cluster_service.py）は一切変更しない。本モジュールは
その上に「どこで止まっているか・次に何をすべきか」を可視化し、既存のbuild_candidate_material/
generate_candidate_framing/create_or_merge_candidateをそのまま呼ぶ薄い運用Layerを追加する。

Theme Candidate生成ロジック自体（LLM prompt・grounding・fingerprint計算）はここでは一切
再実装しない——全てgeo_theme_candidate_service.pyの既存関数を呼ぶだけ。
"""
from datetime import datetime, timezone

import geo_connector_base as base
import geo_theme_candidate_service as candidate_service

SYNC_RUN_CONNECTOR_TYPE = "theme_candidate_sync"


class PipelineError(Exception):
    """Episode Cluster Review → Theme Candidate Sync運用操作の失敗。"""


def _material_key(cluster_ids: list) -> tuple:
    return tuple(sorted(cluster_ids))


def _existing_candidates_by_cluster_key(client, *, is_demo: bool) -> dict:
    """既存Theme Candidate（is_demoスコープ）を、カバーしているcluster_ids集合（sorted tuple）
    をキーにして引けるようにする。「accepted clusterのうちTheme Candidate未生成」の決定論的
    判定、および再実行時の重複防止に使う——新しいLLM判定は行わない。"""
    rows = client.select("geo_theme_candidates", {
        "select": "candidate_id,source_episode_cluster_ids", "is_demo": f"eq.{str(is_demo).lower()}",
    })
    return {_material_key(r.get("source_episode_cluster_ids") or []): r for r in rows}


def _existing_candidate_cluster_keys(client, *, is_demo: bool) -> set:
    return set(_existing_candidates_by_cluster_key(client, is_demo=is_demo).keys())


def list_pending_sync_material(client, *, is_demo: bool = False) -> list:
    """既存list_candidate_worthy_material（cheap/deterministic、cluster_status='accepted'＋
    strong single cluster／accepted situation link component）の結果のうち、同じcluster_ids
    集合で既にTheme Candidateが存在するものを除いた「Theme同期待ち」の一覧を返す。"""
    materials = candidate_service.list_candidate_worthy_material(client, is_demo=is_demo)
    existing_keys = _existing_candidate_cluster_keys(client, is_demo=is_demo)
    return [m for m in materials if _material_key(m["cluster_ids"]) not in existing_keys]


def get_pipeline_status(client, *, is_demo: bool = False) -> dict:
    """§3/§15: Episode Review～Theme Candidate～Handoffの各段階の滞留件数。
    単なるDB件数ではなく「次に何をすべきか」が分かる区分（review_pending/accepted/
    pending_theme_sync/theme_candidates_total/handed_off）で返す。"""
    demo_filter = f"eq.{str(is_demo).lower()}"

    review_pending = len(client.select("geo_episode_clusters", {
        "select": "episode_cluster_id", "cluster_status": "eq.candidate", "is_demo": demo_filter}))
    accepted = len(client.select("geo_episode_clusters", {
        "select": "episode_cluster_id", "cluster_status": "eq.accepted", "is_demo": demo_filter}))
    pending_sync = len(list_pending_sync_material(client, is_demo=is_demo))
    theme_candidates_total = len(client.select("geo_theme_candidates", {
        "select": "candidate_id", "is_demo": demo_filter}))
    handed_off = len(client.select("geo_theme_candidates", {
        "select": "candidate_id", "is_demo": demo_filter, "status": "eq.HANDED_OFF"}))

    return {
        "review_pending": review_pending,
        "accepted": accepted,
        "pending_theme_sync": pending_sync,
        "theme_candidates_total": theme_candidates_total,
        "handed_off": handed_off,
    }


def is_cluster_sync_eligible(client, episode_cluster_id: str, *, is_demo: bool = False) -> bool:
    """§9: Acceptした直後、このclusterが（単独で強いsignalか、accepted situation linkの
    componentの一部として）Theme Candidate生成対象になったかどうかを判定する。"""
    materials = candidate_service.list_candidate_worthy_material(client, is_demo=is_demo)
    return any(episode_cluster_id in m["cluster_ids"] for m in materials)


def _resolve_material_for_cluster(client, episode_cluster_id: str, *, is_demo: bool) -> dict | None:
    """指定clusterが属するcandidate-worthy material（単独、またはaccepted situation link
    componentの一部としてのグループ）を解決する。既存list_candidate_worthy_materialの結果から
    探すだけで、新しい判定基準は導入しない。"""
    materials = candidate_service.list_candidate_worthy_material(client, is_demo=is_demo)
    for material in materials:
        if episode_cluster_id in material["cluster_ids"]:
            return material
    return None


def _sync_one_material(client, azure_client, model, material: dict) -> dict:
    """1 material分のsync本体。既存generate_candidate_framing/create_or_merge_candidateを
    そのまま呼ぶだけで、生成ロジック自体はここでは一切実装しない。

    戻り値のoutcome: "created"|"merged"|"insufficient_evidence"|"generation_failed"。
    "insufficient_evidence"（Evidence不足で生成不可）と"generation_failed"（Evidenceはあるが
    LLM呼び出し自体が失敗/malformed）を区別する——generate_candidate_framing自身が持つ
    早期return条件（evidence_bullets空）をそのまま踏襲した事前チェックであり、新しい判定を
    追加するものではない。

    §20 Idempotency（実LLMでの再現確認済みの重要な補強）: candidate_fingerprintはLLMが
    毎回生成するactors/regions/themesに依存するため、同じclusterへ再度sync_theme_candidates
    相当の処理をかけると、LLM出力の揺れでfingerprintが変わり、既存のcreate_or_merge_candidate
    のfingerprint一致判定だけでは重複生成を防げないことを実際のLLM呼び出しで確認した。ここでは
    LLMを呼ぶ前に、同じcluster_ids集合を持つTheme Candidateが既に存在するかを先に確認し、
    あれば既存のcandidateをそのまま返す（Theme Candidate生成ロジック・fingerprint計算自体は
    一切変更しない。呼び出し側での事前ガードのみ追加）。"""
    existing = _existing_candidates_by_cluster_key(client, is_demo=material.get("is_demo", False)).get(
        _material_key(material["cluster_ids"]))
    if existing:
        return {"outcome": "merged", "candidate_id": existing["candidate_id"],
                "cluster_ids": material["cluster_ids"]}

    if not material.get("evidence_bullets"):
        return {"outcome": "insufficient_evidence", "candidate_id": None,
                "cluster_ids": material["cluster_ids"]}

    framing = candidate_service.generate_candidate_framing(azure_client, model, material)
    if framing is None:
        return {"outcome": "generation_failed", "candidate_id": None,
                "cluster_ids": material["cluster_ids"]}

    result = candidate_service.create_or_merge_candidate(client, material=material, framing=framing)
    return {
        "outcome": "merged" if result["merged"] else "created",
        "candidate_id": result["candidate"]["candidate_id"],
        "cluster_ids": material["cluster_ids"],
    }


def sync_cluster(client, azure_client, model, *, episode_cluster_id: str, requested_by: str,
                  is_demo: bool = False) -> dict:
    """§5: 指定された1件のaccepted Episode Cluster（situation link componentの一部なら
    そのグループ全体）からTheme Candidateを生成する。既存sync_theme_candidatesの対象を
    1 clusterへ絞り込むだけで、生成ロジックは完全に共通。"""
    rows = client.select("geo_episode_clusters", {
        "select": "cluster_status", "episode_cluster_id": f"eq.{episode_cluster_id}", "limit": "1"})
    if not rows:
        raise PipelineError(f"episode_cluster_idが見つかりません: {episode_cluster_id}")
    if rows[0]["cluster_status"] != candidate_service.APPROVED_CLUSTER_STATUS:
        raise PipelineError(
            f"acceptedでないClusterはTheme Candidate対象にできません（現在: {rows[0]['cluster_status']}）")

    material = _resolve_material_for_cluster(client, episode_cluster_id, is_demo=is_demo)
    if material is None:
        return {"outcome": "insufficient_evidence", "candidate_id": None, "cluster_ids": [episode_cluster_id]}

    run = base.create_ingestion_run(
        client, source_id=None, connector_type=SYNC_RUN_CONNECTOR_TYPE, run_type="cli",
        requested_by=requested_by, request_parameters={"mode": "single", "cluster_ids": material["cluster_ids"]},
        is_demo=is_demo)
    try:
        result = _sync_one_material(client, azure_client, model, material)
    except Exception as e:  # noqa: BLE001 — §21 Failure Handling: 失敗してもclusterはaccepted
        # のまま保持し、再試行可能にする（半端なTheme Candidate保存・cluster状態変更はしない）。
        base.complete_ingestion_run(
            client, ingestion_run_id=run["ingestion_run_id"], status="failed",
            counts={"fetched_count": 1, "error_count": 1},
            error_summary=f"{type(e).__name__}: {e}")
        raise PipelineError(f"Theme Candidate生成に失敗しました（再試行可能）: {e}") from e

    status = "completed" if result["outcome"] in ("created", "merged") else "completed_with_warnings"
    counts = {"fetched_count": 1,
              "inserted_count": 1 if result["outcome"] == "created" else 0,
              "updated_count": 1 if result["outcome"] == "merged" else 0,
              "skipped_count": 1 if result["outcome"] == "insufficient_evidence" else 0,
              "error_count": 1 if result["outcome"] == "generation_failed" else 0}
    base.complete_ingestion_run(
        client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
        error_summary=None if result["outcome"] != "generation_failed" else "LLM生成失敗（詳細はログ参照）")
    return result


def batch_sync_pending(client, azure_client, model, *, requested_by: str, is_demo: bool = False) -> dict:
    """§6/§7: Theme同期待ちの全material（単一clusterのSyncボタンと全く同じ
    build_candidate_material/generate_candidate_framing/create_or_merge_candidateを使う）を
    まとめて処理する。1件の失敗が他の処理を止めない（§Fail-safe、既存sync_theme_candidatesと
    同じ設計）。"""
    pending = list_pending_sync_material(client, is_demo=is_demo)
    run = base.create_ingestion_run(
        client, source_id=None, connector_type=SYNC_RUN_CONNECTOR_TYPE, run_type="cli",
        requested_by=requested_by,
        request_parameters={"mode": "batch", "target_count": len(pending)}, is_demo=is_demo)

    results = []
    for material in pending:
        try:
            results.append(_sync_one_material(client, azure_client, model, material))
        except Exception as e:  # noqa: BLE001 — §21: 1件の失敗で残りの処理を止めない
            results.append({"outcome": "generation_failed", "candidate_id": None,
                             "cluster_ids": material["cluster_ids"], "error": f"{type(e).__name__}: {e}"})

    counts = {
        "fetched_count": len(pending),
        "inserted_count": sum(1 for r in results if r["outcome"] == "created"),
        "updated_count": sum(1 for r in results if r["outcome"] == "merged"),
        "skipped_count": sum(1 for r in results if r["outcome"] == "insufficient_evidence"),
        "error_count": sum(1 for r in results if r["outcome"] == "generation_failed"),
    }
    status = "completed" if counts["error_count"] == 0 else "completed_with_warnings"
    base.complete_ingestion_run(
        client, ingestion_run_id=run["ingestion_run_id"], status=status, counts=counts,
        error_summary=None if counts["error_count"] == 0 else f"{counts['error_count']}件のTheme Candidate生成に失敗")

    return {"target_count": len(pending), "results": results, "counts": counts,
            "sync_run_id": run["ingestion_run_id"]}


def list_sync_runs(client, *, is_demo: bool = False, limit: int = 50) -> list:
    """§11: Sync履歴。新規table追加はせず既存geo_ingestion_runsを再利用する。"""
    return client.select("geo_ingestion_runs", {
        "select": "*", "connector_type": f"eq.{SYNC_RUN_CONNECTOR_TYPE}",
        "is_demo": f"eq.{str(is_demo).lower()}", "order": "started_at.desc", "limit": str(limit),
    })
