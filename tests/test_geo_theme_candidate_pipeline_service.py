"""Phase C: Episode Cluster Review → Theme Candidate Sync — 運用可視化・手動実行のテスト。

既存Core Generation（geo_theme_candidate_service.py）は一切変更しないため、ここでは
運用Layer（pipeline status集計・pending sync判定・単一/一括sync・冪等性・失敗時の挙動）
のみを検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_connector_base as base  # noqa: E402
import geo_llm_common  # noqa: E402
import geo_theme_candidate_pipeline_service as pipeline_service  # noqa: E402
import geo_theme_candidate_service as candidate_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402
from tests.test_geo_theme_candidate_service import _cluster, _fake_framing_llm, _member, _tables  # noqa: E402


def _client_with_one_accepted_cluster():
    m1 = _member("cand-1", "cluster-1")
    return FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1")], members=[m1["member"]], candidates=[m1["candidate"]],
    ))


# ---- pipeline status / pending sync判定 --------------------------------------------

def test_pipeline_status_counts_review_pending_and_accepted():
    client = FakeSupabaseClient(tables=_tables(clusters=[
        _cluster("cluster-1", cluster_status="candidate"),
        _cluster("cluster-2", cluster_status="candidate"),
        _cluster("cluster-3", cluster_status="accepted"),
    ]))
    status = pipeline_service.get_pipeline_status(client)
    assert status["review_pending"] == 2
    assert status["accepted"] == 1


def test_accepted_strong_cluster_with_no_candidate_yet_is_pending_sync():
    client = _client_with_one_accepted_cluster()
    pending = pipeline_service.list_pending_sync_material(client)
    assert len(pending) == 1
    assert pending[0]["cluster_ids"] == ["cluster-1"]

    status = pipeline_service.get_pipeline_status(client)
    assert status["pending_theme_sync"] == 1


def test_accepted_weak_cluster_is_not_pending_sync():
    weak = _cluster("cluster-weak", member_count=1, trajectory="stable")
    client = FakeSupabaseClient(tables=_tables(clusters=[weak]))
    assert pipeline_service.list_pending_sync_material(client) == []


def test_cluster_already_synced_is_no_longer_pending():
    client = _client_with_one_accepted_cluster()
    client.tables["geo_theme_candidates"].append({
        "candidate_id": "existing-1", "source_episode_cluster_ids": ["cluster-1"], "is_demo": False,
    })
    assert pipeline_service.list_pending_sync_material(client) == []
    status = pipeline_service.get_pipeline_status(client)
    assert status["pending_theme_sync"] == 0


def test_is_cluster_sync_eligible_true_for_strong_cluster_false_for_weak():
    client = _client_with_one_accepted_cluster()
    assert pipeline_service.is_cluster_sync_eligible(client, "cluster-1") is True

    weak_client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-weak", member_count=1, trajectory="stable")]))
    assert pipeline_service.is_cluster_sync_eligible(weak_client, "cluster-weak") is False


# ---- 単一cluster sync ---------------------------------------------------------------

def test_sync_cluster_success_creates_candidate_and_run_log(monkeypatch):
    client = _client_with_one_accepted_cluster()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())

    result = pipeline_service.sync_cluster(
        client, azure_client=object(), model="gpt", episode_cluster_id="cluster-1",
        requested_by="analyst1")
    assert result["outcome"] == "created"
    assert result["candidate_id"] is not None

    candidates = candidate_service.list_candidates(client)
    assert len(candidates) == 1

    runs = pipeline_service.list_sync_runs(client)
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["inserted_count"] == 1


def test_sync_cluster_rejects_non_accepted_cluster():
    m1 = _member("cand-1", "cluster-1")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1", cluster_status="candidate")],
        members=[m1["member"]], candidates=[m1["candidate"]],
    ))
    try:
        pipeline_service.sync_cluster(
            client, azure_client=object(), model="gpt", episode_cluster_id="cluster-1",
            requested_by="analyst1")
        assert False, "acceptedでないclusterはPipelineErrorになるはず"
    except pipeline_service.PipelineError:
        pass


def test_sync_cluster_insufficient_evidence_when_not_worthy_material():
    weak_cluster = _cluster("cluster-weak", member_count=1, trajectory="stable", cluster_status="accepted")
    client = FakeSupabaseClient(tables=_tables(clusters=[weak_cluster]))
    result = pipeline_service.sync_cluster(
        client, azure_client=object(), model="gpt", episode_cluster_id="cluster-weak",
        requested_by="analyst1")
    assert result["outcome"] == "insufficient_evidence"
    assert candidate_service.list_candidates(client) == []


def test_sync_cluster_llm_failure_does_not_change_cluster_status_and_is_retryable(monkeypatch):
    """§21 Failure Handling: LLM失敗時、clusterはacceptedのまま・Theme Candidateは
    半端に保存されず、再試行可能であること。"""
    client = _client_with_one_accepted_cluster()

    def _raise(*args, **kwargs):
        raise geo_llm_common.GeoLLMError("simulated LLM timeout")
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _raise)

    try:
        pipeline_service.sync_cluster(
            client, azure_client=object(), model="gpt", episode_cluster_id="cluster-1",
            requested_by="analyst1")
    except pipeline_service.PipelineError:
        pass  # GeoLLMErrorはgenerate_candidate_framing内部で捕捉されNoneを返すため、
              # ここに来る場合でもcluster状態・Theme Candidateは変化していないことを検証する

    cluster_row = client.select("geo_episode_clusters", {
        "select": "cluster_status", "episode_cluster_id": "eq.cluster-1"})[0]
    assert cluster_row["cluster_status"] == "accepted"  # クラッシュ・rejected化していない
    assert candidate_service.list_candidates(client) == []  # 半端なCandidateが残っていない

    # 再試行: LLMが復旧すれば今度は成功する
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())
    retry_result = pipeline_service.sync_cluster(
        client, azure_client=object(), model="gpt", episode_cluster_id="cluster-1",
        requested_by="analyst1")
    assert retry_result["outcome"] == "created"


def test_sync_cluster_is_idempotent_no_duplicate_on_repeat(monkeypatch):
    client = _client_with_one_accepted_cluster()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())

    first = pipeline_service.sync_cluster(
        client, azure_client=object(), model="gpt", episode_cluster_id="cluster-1",
        requested_by="analyst1")
    second = pipeline_service.sync_cluster(
        client, azure_client=object(), model="gpt", episode_cluster_id="cluster-1",
        requested_by="analyst1")

    assert first["outcome"] == "created"
    assert second["outcome"] == "merged"
    assert first["candidate_id"] == second["candidate_id"]
    assert len(candidate_service.list_candidates(client)) == 1  # 重複生成されない


def test_sync_cluster_is_idempotent_even_when_llm_output_varies(monkeypatch):
    """§20 Idempotency: 実LLMで確認した重大な再発防止テスト。LLMの出力（actors/regions/
    themes）が毎回変わり、既存のcandidate_fingerprintが一致しなくなる場合でも、同じ
    cluster_ids集合に対する2回目以降のsyncは新しいTheme Candidateを作らず、既存のものを
    そのまま返すこと（LLMを呼ぶ前にcluster_ids一致で判定するため、fingerprint揺れの影響を
    受けない）。"""
    client = _client_with_one_accepted_cluster()
    call_count = {"n": 0}

    def flaky_framing_llm(azure_client, model, system_prompt, user_prompt, schema, schema_name, **kwargs):
        call_count["n"] += 1
        # 1回目と2回目でactors/regions/themesを変える = fingerprintが変わる状況を再現する
        suffix = "A" if call_count["n"] == 1 else "B"
        return {"data": {
            "title": f"米国対中AI半導体政策の変化{suffix}",
            "observed_change": "White Houseが対中交渉上の裁量を確保する方向へ運用が変わっている可能性がある。",
            "why_now": "直近の観測されたActor行動が増加している。",
            "question": "規制強化そのものではなく、裁量確保への運用変化なのかを分析すべきではないか？",
            "actors": ["White House", "Beijing"] if suffix == "A" else ["White House"],
            "regions": ["Washington"],
            "themes": [],
            "candidate_type": "changing",
            "change_type": "policy_shift",
            "business_relevance": ["regulatory"],
        }}
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", flaky_framing_llm)

    first = pipeline_service.sync_cluster(
        client, azure_client=object(), model="gpt", episode_cluster_id="cluster-1",
        requested_by="analyst1")
    second = pipeline_service.sync_cluster(
        client, azure_client=object(), model="gpt", episode_cluster_id="cluster-1",
        requested_by="analyst1")

    assert first["outcome"] == "created"
    # 2回目はLLMを再度呼ぶ前にcluster_ids一致で既存candidateを検出するため、
    # call_countは1のまま（無駄なLLM呼び出し自体も避けられる）。
    assert call_count["n"] == 1
    assert second["outcome"] == "merged"
    assert first["candidate_id"] == second["candidate_id"]
    assert len(candidate_service.list_candidates(client)) == 1


# ---- 一括sync -------------------------------------------------------------------------

def test_batch_sync_pending_processes_all_and_reports_counts(monkeypatch):
    m1 = _member("cand-1", "cluster-1")
    m2 = _member("cand-2", "cluster-2", actor_entity_id="actor-2")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1"), _cluster("cluster-2", actor_entity_id="actor-2")],
        members=[m1["member"], m2["member"]], candidates=[m1["candidate"], m2["candidate"]],
    ))
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())

    result = pipeline_service.batch_sync_pending(client, azure_client=object(), model="gpt",
                                                  requested_by="analyst1")
    assert result["target_count"] == 2
    assert result["counts"]["inserted_count"] == 2
    assert result["counts"]["error_count"] == 0
    assert len(candidate_service.list_candidates(client)) == 2


def test_batch_sync_one_failure_does_not_block_others(monkeypatch):
    m1 = _member("cand-1", "cluster-1")
    m2 = _member("cand-2", "cluster-2", actor_entity_id="actor-2")
    client = FakeSupabaseClient(tables=_tables(
        clusters=[_cluster("cluster-1"), _cluster("cluster-2", actor_entity_id="actor-2")],
        members=[m1["member"], m2["member"]], candidates=[m1["candidate"], m2["candidate"]],
    ))

    call_count = {"n": 0}
    good = _fake_framing_llm()

    def flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient failure")
        return good(*args, **kwargs)
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", flaky)

    result = pipeline_service.batch_sync_pending(client, azure_client=object(), model="gpt",
                                                  requested_by="analyst1")
    assert result["target_count"] == 2
    assert result["counts"]["error_count"] == 1
    assert result["counts"]["inserted_count"] == 1
    assert len(candidate_service.list_candidates(client)) == 1


def test_batch_sync_run_log_reusable_via_existing_ingestion_runs_table(monkeypatch):
    client = _client_with_one_accepted_cluster()
    monkeypatch.setattr(geo_llm_common, "call_llm_structured", _fake_framing_llm())

    pipeline_service.batch_sync_pending(client, azure_client=object(), model="gpt", requested_by="analyst1")
    runs = pipeline_service.list_sync_runs(client)
    assert len(runs) == 1
    assert runs[0]["connector_type"] == pipeline_service.SYNC_RUN_CONNECTOR_TYPE
