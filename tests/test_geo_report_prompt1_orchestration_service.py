import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_prompt1_orchestration_service as orch  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

# 2026-09-11 Batching最適化: 1バッチ=複数クラスタを1回のLLM呼び出しで処理するようになった
# ため、モックはリクエストのuser prompt本文から見出し（### cluster_id: xxx /
# ### sentiment_cluster_id: xxx）を正規表現で抽出し、各クラスタごとにresponse_fieldsを
# コピーした{"cluster_id": id, **response_fields}を含む{"results": [...]}を動的に返す
# （固定レスポンスだと複数クラスタを含むバッチで全クラスタへ同じcluster_idが返り、
# reconcile_batch_llm_resultsがmissing/unknown扱いにしてしまうため）。
_HEADING_ID_RE = re.compile(r"^### (?:cluster_id|sentiment_cluster_id): (\S+)", re.MULTILINE)


def _mock_azure_client(response_fields: dict):
    client = MagicMock()

    def _create(*args, messages, **kwargs):
        user_content = messages[-1]["content"]
        ids = _HEADING_ID_RE.findall(user_content)
        results = [{"cluster_id": cid, **response_fields} for cid in ids]
        payload = {"results": results}
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))], usage=None)

    client.chat.completions.create.side_effect = _create
    return client


def _seed_run(client, *, preprocess_done=True):
    client.insert("geo_report_collection_runs", [{
        "run_id": "run-1", "project_id": "proj-1",
        "canonical_preprocess_execution_id": "exec-preprocess-1" if preprocess_done else None,
    }])


def _seed_analysis_cluster(client, cluster_id, *, run_id="run-1", signal_id="S1"):
    client.insert("geo_report_analysis_clusters", [{
        "id": f"row-{cluster_id}", "run_id": run_id, "analysis_cluster_id": cluster_id, "signal_id": signal_id,
        "report_topic_label": "Arizona Governor", "cluster_records": 5, "eligible_records": 5,
        "preliminary_consistency": "Medium",
    }])
    client.insert("geo_report_analysis_cluster_representatives", [{
        "id": f"rep-{cluster_id}", "run_id": run_id, "analysis_cluster_id": cluster_id, "representative_no": 1,
        "record_id": "r1", "title": "t", "text_for_review": "some text",
    }])


def _seed_sentiment_cluster(client, cluster_id, *, run_id="run-1", signal_id="S1",
                             source_class_preliminary="Public_Voice_Candidate",
                             narrative_id="S1-01", text_for_review="I support Hobbs strongly.",
                             matched_terms=""):
    client.insert("geo_report_sentiment_clusters", [{
        "id": f"row-{cluster_id}", "run_id": run_id, "sentiment_cluster_id": cluster_id, "signal_id": signal_id,
        "narrative_id": narrative_id,
        "report_topic_label": "Arizona Governor", "cluster_records": 5, "eligible_records": 5,
        "source_class_preliminary": source_class_preliminary, "attitude_hint": "positive",
    }])
    client.insert("geo_report_sentiment_cluster_representatives", [{
        "id": f"rep-{cluster_id}", "run_id": run_id, "sentiment_cluster_id": cluster_id, "representative_no": 1,
        "record_id": "r1", "title": "t", "text_for_review": text_for_review, "matched_terms": matched_terms,
    }])


def _seed_narrative_master_asset(client, run_id="run-1", asset_id="nm-asset-1", *,
                                   narrative_id="S1-01", required_context="Hobbs;Katie Hobbs"):
    client.insert("geo_report_project_assets", [{
        "id": asset_id, "content": {"rows": [{
            "sub_narrative_id": narrative_id, "required_context": required_context,
        }]},
    }])
    client.update("geo_report_collection_runs", {"run_id": f"eq.{run_id}"},
                  {"narrative_master_asset_id": asset_id})


def _seed_sc_classification(client, execution_id, cluster_id, *, theme_relevance="relevant",
                              source_class="Public Voice", apply_to_cluster=True):
    client.insert("geo_report_sentiment_cluster_classifications", [{
        "id": f"cls-{cluster_id}", "execution_id": execution_id, "sentiment_cluster_id": cluster_id,
        "theme_relevance": theme_relevance, "source_class": source_class,
        "apply_to_cluster": apply_to_cluster,
    }])


_ANALYSIS_RESPONSE = {
    "final_topic_label": "Support narrative", "source_assessment": "Consistent", "tone_gradient": "Supportive",
    "voice_direction": "Positive", "report_use_status": "include", "continuity_relevance": "ongoing",
    "matched_previous_topic_id": None, "change_signal_summary": "None", "noise_flag": False, "noise_reason": "",
    "voice_summary": "summary", "notable_phrases": "phrase", "representative_quality": "High",
    "notes_for_prompt2": "notes",
}

_SENTIMENT_RESPONSE = {
    "theme_relevance": "relevant", "source_class": "Public Voice", "reaction_type": "Evaluation",
    "attitude": "positive", "cluster_consistency": "High", "apply_to_cluster": True,
    "exception_pattern": [], "classification_reason": "clear",
}

_TARGETED_STANCE_RESPONSE = {"targets": [{
    "target_type": "candidate", "target_name": "Hobbs", "target_normalized_id": "hobbs",
    "stance_polarity": "favorable", "stance_strength": "strong", "stance_confidence": "high",
    "stance_evidence": "quote", "stance_basis": "direct_support", "stance_cluster_consistency": "High",
    "apply_stance_to_cluster": True, "stance_exception_pattern": "", "stance_reason": "reason",
}]}


def test_create_prompt1_execution_requires_completed_preprocess():
    client = FakeSupabaseClient()
    _seed_run(client, preprocess_done=False)
    try:
        orch.create_prompt1_execution(client, "run-1")
        assert False, "should have raised"
    except ValueError as exc:
        assert "Preprocess" in str(exc)


def test_create_prompt1_execution_succeeds_and_links_preprocess():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    rows = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})
    assert rows[0]["stage"] == "prompt1"
    assert rows[0]["input_preprocess_execution_id"] == "exec-preprocess-1"
    assert rows[0]["status"] == "running"


def test_plan_analysis_cluster_batches_splits_by_batch_size():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    for i in range(5):
        _seed_analysis_cluster(client, f"AC_{i}")
    batches = orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=2)
    assert len(batches) == 3  # 5件を2件ずつ -> 2,2,1
    assert batches[0]["stage"] == "prompt1_analysis_cluster"
    assert all(b["status"] == "pending" for b in batches)


def test_plan_targeted_stance_batches_requires_final_public_voice_classification_and_explicit_target():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_narrative_master_asset(client, required_context="Hobbs;Katie Hobbs")

    # SC_1: 最終判定もPublic Voiceで、代表投稿にNarrative Masterの明示target(Hobbs)が現れる -> 対象
    _seed_sentiment_cluster(client, "SC_1", narrative_id="S1-01", text_for_review="I support Hobbs strongly.")
    _seed_sc_classification(client, execution_id, "SC_1", theme_relevance="relevant",
                              source_class="Public Voice", apply_to_cluster=True)

    # SC_2: 最終判定がPublic Voiceでない(Media) -> 対象外
    _seed_sentiment_cluster(client, "SC_2", narrative_id="S1-01", text_for_review="Poll shows Hobbs leading.")
    _seed_sc_classification(client, execution_id, "SC_2", theme_relevance="relevant",
                              source_class="Media", apply_to_cluster=True)

    # SC_3: Public Voiceだが明示target(required_context)が代表投稿に無い -> 対象外
    _seed_sentiment_cluster(client, "SC_3", narrative_id="S1-01", text_for_review="Great weather today.")
    _seed_sc_classification(client, execution_id, "SC_3", theme_relevance="relevant",
                              source_class="Public Voice", apply_to_cluster=True)

    # SC_4: Public Voiceだがapply_to_cluster=False -> 対象外
    _seed_sentiment_cluster(client, "SC_4", narrative_id="S1-01", text_for_review="I support Hobbs strongly.")
    _seed_sc_classification(client, execution_id, "SC_4", theme_relevance="relevant",
                              source_class="Public Voice", apply_to_cluster=False)

    batches = orch.plan_targeted_stance_batches(client, "run-1", execution_id)
    all_ids = [cid for b in batches for cid in b["item_ids"]]
    assert all_ids == ["SC_1"]


def test_plan_targeted_stance_batches_empty_when_no_sc_classification_yet():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_sentiment_cluster(client, "SC_1")
    # SC final classificationがまだ無い(SC stage未完了)場合は空になる
    batches = orch.plan_targeted_stance_batches(client, "run-1", execution_id)
    assert batches == []


def test_run_next_pending_batch_processes_analysis_cluster_and_marks_completed():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_1")
    orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=10)

    azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
    outcome = orch.run_next_pending_batch(client, azure_client, "gpt-4o", "run-1", execution_id,
                                            "prompt1_analysis_cluster")
    assert outcome == {"processed": True, "batch_id": outcome["batch_id"], "status": "completed"}

    saved = client.select("geo_report_analysis_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}", "cluster_id": "eq.AC_1",
    })
    assert len(saved) == 1
    assert saved[0]["final_topic_label"] == "Support narrative"

    execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})[0]
    assert execution["progress"]["batches_completed"] == 1
    assert execution["progress"]["batches_total"] == 1

    # バッチが無くなったのでNoneが返る
    outcome2 = orch.run_next_pending_batch(client, azure_client, "gpt-4o", "run-1", execution_id,
                                             "prompt1_analysis_cluster")
    assert outcome2 == {"processed": False}


def test_run_next_pending_batch_processes_sentiment_cluster():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_sentiment_cluster(client, "SC_1")
    orch.plan_sentiment_cluster_batches(client, "run-1", execution_id, batch_size=10)

    azure_client = _mock_azure_client(_SENTIMENT_RESPONSE)
    orch.run_next_pending_batch(client, azure_client, "gpt-4o", "run-1", execution_id,
                                  "prompt1_sentiment_cluster")

    saved = client.select("geo_report_sentiment_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}", "sentiment_cluster_id": "eq.SC_1",
    })
    assert len(saved) == 1
    assert saved[0]["theme_relevance"] == "relevant"
    assert saved[0]["exception_pattern"] == ""  # 空配列は空文字列にjoinされる


def test_run_next_pending_batch_processes_targeted_stance_and_upsert_replaces_targets():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_narrative_master_asset(client, required_context="Hobbs;Katie Hobbs")
    _seed_sentiment_cluster(client, "SC_1")
    _seed_sc_classification(client, execution_id, "SC_1")
    orch.plan_targeted_stance_batches(client, "run-1", execution_id, batch_size=10)

    azure_client = _mock_azure_client(_TARGETED_STANCE_RESPONSE)
    orch.run_next_pending_batch(client, azure_client, "gpt-4o", "run-1", execution_id,
                                  "prompt1_targeted_stance")

    saved = client.select("geo_report_targeted_stance", {
        "select": "*", "execution_id": f"eq.{execution_id}", "sentiment_cluster_id": "eq.SC_1",
    })
    assert len(saved) == 1
    assert saved[0]["target_name"] == "Hobbs"

    # 同じクラスタを再度処理(retry相当)しても重複しない(delete-then-insertのため)
    orch.save_targeted_stance(client, execution_id, "run-1", "SC_1", _TARGETED_STANCE_RESPONSE["targets"])
    saved2 = client.select("geo_report_targeted_stance", {
        "select": "*", "execution_id": f"eq.{execution_id}", "sentiment_cluster_id": "eq.SC_1",
    })
    assert len(saved2) == 1


def test_save_targeted_stance_dedupes_same_target_within_one_cluster():
    """2026-09-12実機確認: LLMが同一クラスタ内で同じ(target_type, target_key)の
    targetを重複して返し、DBのunique制約違反(409)でinsert全体が失敗する事象が
    発生した。事前に重複排除することで回避する。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    duplicate_targets = [dict(_TARGETED_STANCE_RESPONSE["targets"][0]) for _ in range(2)]
    orch.save_targeted_stance(client, execution_id, "run-1", "SC_1", duplicate_targets)

    saved = client.select("geo_report_targeted_stance", {
        "select": "*", "execution_id": f"eq.{execution_id}", "sentiment_cluster_id": "eq.SC_1",
    })
    assert len(saved) == 1  # 重複は先勝ちで1件だけ保存される


def test_process_batch_contains_save_failure_without_crashing_pipeline():
    """2026-09-12実機確認: 保存(config['save'])が例外を送出すると、以前は
    _process_batchの外へそのまま伝播し、ThreadPoolExecutor経由でPipeline全体
    （Prompt1〜Prompt2統合実行）をクラッシュさせていた。1クラスタの保存失敗が
    batch自体は'failed'にしつつ、例外はここで止まる（呼び出し元へ伝播しない）ことを
    確認する。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_1")
    batches = orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=10)

    def _boom(*args, **kwargs):
        raise Exception("simulated DB unique constraint violation (409)")

    orig_save = orch._STAGE_BATCH_CONFIG["prompt1_analysis_cluster"]["save"]
    orch._STAGE_BATCH_CONFIG["prompt1_analysis_cluster"]["save"] = _boom
    try:
        azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
        outcome = orch._process_batch(client, azure_client, "gpt-4o", "run-1", execution_id, batches[0])
    finally:
        orch._STAGE_BATCH_CONFIG["prompt1_analysis_cluster"]["save"] = orig_save

    assert outcome["status"] == "failed"  # 例外を送出せず、batch failedとして戻る
    assert "AC_1" in outcome["error"]


def test_batch_idempotency_on_retry_does_not_duplicate_classification_rows():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_1")
    batches = orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=10)
    batch_id = batches[0]["id"]

    azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
    orch.run_next_pending_batch(client, azure_client, "gpt-4o", "run-1", execution_id,
                                  "prompt1_analysis_cluster")
    # 手動でretryをシミュレート(同じbatchを再度processed実行)
    client.update("geo_report_execution_batches", {"id": f"eq.{batch_id}"}, {"status": "pending"})
    orch.run_next_pending_batch(client, azure_client, "gpt-4o", "run-1", execution_id,
                                  "prompt1_analysis_cluster")

    saved = client.select("geo_report_analysis_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}", "cluster_id": "eq.AC_1",
    })
    assert len(saved) == 1  # upsertなので重複しない


def test_retry_batch_reprocesses_failed_batch():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    # クラスタを登録せず、意図的に失敗させる
    client.insert("geo_report_execution_batches", [{
        "id": "batch-1", "run_id": "run-1", "execution_id": execution_id, "stage": "prompt1_analysis_cluster",
        "batch_no": 1, "item_ids": ["AC_missing"], "status": "failed", "retry_count": 0,
    }])
    azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
    outcome = orch.retry_batch(client, azure_client, "gpt-4o", "run-1", execution_id, "batch-1")
    assert outcome["status"] == "failed"  # クラスタが無いので今回も失敗するはず

    batch = client.select("geo_report_execution_batches", {"select": "*", "id": "eq.batch-1"})[0]
    assert batch["retry_count"] == 1


def test_complete_prompt1_execution_if_done_requires_all_batches_finished():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_1")
    _seed_analysis_cluster(client, "AC_2")
    orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=1)

    assert orch.complete_prompt1_execution_if_done(client, "run-1", execution_id) is False

    azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
    orch.run_all_pending_batches(client, azure_client, "gpt-4o", "run-1", execution_id,
                                   "prompt1_analysis_cluster")

    assert orch.complete_prompt1_execution_if_done(client, "run-1", execution_id) is True
    run = client.select("geo_report_collection_runs", {"select": "*", "run_id": "eq.run-1"})[0]
    assert run["canonical_prompt1_execution_id"] == execution_id
    execution = client.select("geo_report_executions", {"select": "*", "id": f"eq.{execution_id}"})[0]
    assert execution["status"] == "completed"


def test_complete_prompt1_execution_blocked_by_failed_batch():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    client.insert("geo_report_execution_batches", [{
        "id": "batch-1", "run_id": "run-1", "execution_id": execution_id, "stage": "prompt1_analysis_cluster",
        "batch_no": 1, "item_ids": ["AC_x"], "status": "failed",
    }])
    assert orch.complete_prompt1_execution_if_done(client, "run-1", execution_id) is False


def test_run_all_pending_batches_processes_every_batch():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    for i in range(3):
        _seed_analysis_cluster(client, f"AC_{i}")
    orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=1)

    azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
    results = orch.run_all_pending_batches(client, azure_client, "gpt-4o", "run-1", execution_id,
                                             "prompt1_analysis_cluster")
    assert len(results) == 3
    assert all(r["status"] == "completed" for r in results)


# ---------------------------------------------------------------------------
# Batching最適化（2026-09-11）: item-level reconciliation/retry, token-aware batching
# ---------------------------------------------------------------------------

def test_pack_token_aware_batches_splits_on_item_count():
    batches = orch._pack_token_aware_batches(
        ["A", "B", "C", "D", "E"], {}, max_items=2, max_tokens=100000)
    assert batches == [["A", "B"], ["C", "D"], ["E"]]


def test_pack_token_aware_batches_splits_on_token_budget_before_item_count():
    weight_by_id = {"A": 40, "B": 40, "C": 40}
    batches = orch._pack_token_aware_batches(
        ["A", "B", "C"], weight_by_id, max_items=20, max_tokens=50)
    # A(40)単独 -> Bを追加すると80>50なのでAだけで閉じる。B(40)単独->Cを追加すると80>50。
    assert batches == [["A"], ["B"], ["C"]]


def test_pack_token_aware_batches_empty_input_returns_no_batches():
    assert orch._pack_token_aware_batches([], {}, max_items=20, max_tokens=1000) == []


def test_estimate_representative_weight_by_cluster_counts_title_and_text_length():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_analysis_cluster(client, "AC_short")  # title="t"(1) + text_for_review="some text"(9) = 10 chars
    weights = orch._estimate_representative_weight_by_cluster(
        client, "run-1", "geo_report_analysis_cluster_representatives", "analysis_cluster_id")
    assert weights["AC_short"] == max(1, 10 // orch._CHARS_PER_TOKEN_ESTIMATE)


def test_plan_analysis_cluster_batches_splits_when_estimated_tokens_exceed_budget():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_short")
    # 非常に長い代表投稿を持つクラスタをもう1件追加し、token budgetにより別バッチへ
    # 分かれることを確認する（デフォルトのMAX_BATCH_INPUT_TOKENSに依存せず、明示的に
    # 小さいmax_tokensでplan_batchesを直接呼ぶことで決定的にテストする）。
    client.insert("geo_report_analysis_clusters", [{
        "id": "row-AC_long", "run_id": "run-1", "analysis_cluster_id": "AC_long", "signal_id": "S1",
        "report_topic_label": "Arizona Governor", "cluster_records": 5, "eligible_records": 5,
        "preliminary_consistency": "Medium",
    }])
    client.insert("geo_report_analysis_cluster_representatives", [{
        "id": "rep-AC_long", "run_id": "run-1", "analysis_cluster_id": "AC_long", "representative_no": 1,
        "record_id": "r1", "title": "t", "text_for_review": "x" * 4000,
    }])
    weight_by_id = orch._estimate_representative_weight_by_cluster(
        client, "run-1", "geo_report_analysis_cluster_representatives", "analysis_cluster_id")
    batches = orch.plan_batches(
        client, "run-1", execution_id, "prompt1_analysis_cluster", ["AC_short", "AC_long"],
        batch_size=20, weight_by_id=weight_by_id, max_tokens=500)
    by_batch = {b["batch_no"]: b["item_ids"] for b in batches}
    assert len(batches) == 2
    assert not any({"AC_short", "AC_long"} <= set(ids) for ids in by_batch.values())


def test_classify_batch_with_retry_recovers_missing_item_on_second_round():
    """1回目の呼び出しでB欠落、2回目(item-level retry)でBが返る場合を模す。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_A")
    _seed_analysis_cluster(client, "AC_B")

    call_count = {"n": 0}

    def batch_classify(azure_client, model, clusters):
        call_count["n"] += 1
        ids = [c["cluster_row"]["cluster_id"] for c in clusters]
        if call_count["n"] == 1:
            # AC_Bを欠落させる
            returned = [cid for cid in ids if cid != "AC_B"]
        else:
            returned = ids
        results = [{"cluster_id": cid, **_ANALYSIS_RESPONSE} for cid in returned]
        return {"data": {"results": results}}

    valid, unresolved = orch._classify_batch_with_retry(
        client, None, "gpt-4o", "run-1", ["AC_A", "AC_B"],
        batch_loader=orch._load_analysis_clusters_and_representatives_batch,
        batch_classify=batch_classify,
        item_schema=__import__("geo_report_prompt1_classification_service").ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA,
        build_item=orch._analysis_cluster_build_item,
    )
    assert unresolved == []
    assert set(valid.keys()) == {"AC_A", "AC_B"}
    assert call_count["n"] == 2  # 1回目+item-level retry1回で回収できた


def test_classify_batch_with_retry_gives_up_after_max_rounds_and_reports_unresolved():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_A")
    _seed_analysis_cluster(client, "AC_B")

    def batch_classify(azure_client, model, clusters):
        ids = [c["cluster_row"]["cluster_id"] for c in clusters]
        # AC_Bは常に欠落させる(何度リトライしても解決しない)
        returned = [cid for cid in ids if cid != "AC_B"]
        results = [{"cluster_id": cid, **_ANALYSIS_RESPONSE} for cid in returned]
        return {"data": {"results": results}}

    import geo_report_prompt1_classification_service as classify_mod
    valid, unresolved = orch._classify_batch_with_retry(
        client, None, "gpt-4o", "run-1", ["AC_A", "AC_B"],
        batch_loader=orch._load_analysis_clusters_and_representatives_batch,
        batch_classify=batch_classify,
        item_schema=classify_mod.ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA,
        build_item=orch._analysis_cluster_build_item,
        max_extra_rounds=2,
    )
    assert unresolved == ["AC_B"]
    assert set(valid.keys()) == {"AC_A"}  # AC_Aは毎回正常に返ってきているので保存対象になる


def test_process_batch_saves_valid_items_even_when_batch_marked_failed():
    """20件中1件だけ欠落/invalidのケースでも、残りの正常なアイテムは保存される
    （バッチ全体を無駄に再送しない、というBatching最適化の要件）。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_A")
    _seed_analysis_cluster(client, "AC_B")
    batches = orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=10)
    batch = batches[0]

    def _create(*args, messages, **kwargs):
        user_content = messages[-1]["content"]
        found = __import__("re").findall(r"^### cluster_id: (\S+)", user_content, __import__("re").MULTILINE)
        # AC_Bだけ常に欠落させる
        results = [{"cluster_id": cid, **_ANALYSIS_RESPONSE} for cid in found if cid != "AC_B"]
        payload = {"results": results}
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))], usage=None)

    azure_client = MagicMock()
    azure_client.chat.completions.create.side_effect = _create

    outcome = orch._process_batch(client, azure_client, "gpt-4o", "run-1", execution_id, batch)
    assert outcome["status"] == "failed"
    assert "AC_B" in outcome["error"]

    saved = client.select("geo_report_analysis_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}", "cluster_id": "eq.AC_A",
    })
    assert len(saved) == 1  # AC_Aは正常だったので保存されている
    saved_b = client.select("geo_report_analysis_cluster_classifications", {
        "select": "*", "execution_id": f"eq.{execution_id}", "cluster_id": "eq.AC_B",
    })
    assert len(saved_b) == 0


# ---------------------------------------------------------------------------
# 処理のキャンセル（協調的キャンセル、2026-09-11）
# ---------------------------------------------------------------------------

def test_process_batch_skips_and_leaves_pending_when_run_is_cancelled():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_1")
    batches = orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=10)
    client.update("geo_report_collection_runs", {"run_id": "eq.run-1"}, {"cancel_requested": True})

    azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
    outcome = orch._process_batch(client, azure_client, "gpt-4o", "run-1", execution_id, batches[0])
    assert outcome["status"] == "cancelled"
    assert not azure_client.chat.completions.create.called

    batch = client.select("geo_report_execution_batches", {"select": "*", "id": f"eq.{batches[0]['id']}"})[0]
    assert batch["status"] == "pending"  # キャンセル解除後に再実行できるよう変更しない


def test_run_all_pending_batches_returns_cancelled_status_without_calling_llm():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_1")
    orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=10)
    client.update("geo_report_collection_runs", {"run_id": "eq.run-1"}, {"cancel_requested": True})

    azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
    results = orch.run_all_pending_batches(client, azure_client, "gpt-4o", "run-1", execution_id,
                                             "prompt1_analysis_cluster")
    assert len(results) == 1
    assert results[0]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# LLM呼び出しログ（実行効率の実測分析、2026-09-11）
# ---------------------------------------------------------------------------

def test_process_batch_logs_llm_call_to_execution_log():
    client = FakeSupabaseClient()
    _seed_run(client)
    execution_id = orch.create_prompt1_execution(client, "run-1")
    _seed_analysis_cluster(client, "AC_1")
    _seed_analysis_cluster(client, "AC_2")
    batches = orch.plan_analysis_cluster_batches(client, "run-1", execution_id, batch_size=10)

    azure_client = _mock_azure_client(_ANALYSIS_RESPONSE)
    orch._process_batch(client, azure_client, "gpt-4o", "run-1", execution_id, batches[0])

    logs = client.select("geo_report_execution_log", {"select": "*", "execution_id": f"eq.{execution_id}"})
    assert len(logs) == 1
    assert logs[0]["stage"] == "prompt1_analysis_cluster"
    assert logs[0]["model"] == "gpt-4o"
    assert logs[0]["item_count"] == 2
    assert logs[0]["rate_limit_retries"] == 0
    assert logs[0]["latency_ms"] is not None and logs[0]["latency_ms"] >= 0


# ---------------------------------------------------------------------------
# DB batch fetch（2026-09-12性能改善: cluster毎のSELECTをIN取得へ集約）
# ---------------------------------------------------------------------------

def test_load_analysis_clusters_and_representatives_batch_groups_by_cluster():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_analysis_cluster(client, "AC_1")
    _seed_analysis_cluster(client, "AC_2")

    loaded = orch._load_analysis_clusters_and_representatives_batch(client, "run-1", ["AC_1", "AC_2"])
    assert set(loaded.keys()) == {"AC_1", "AC_2"}
    cluster_row, reps = loaded["AC_1"]
    assert cluster_row["cluster_id"] == "AC_1"
    assert len(reps) == 1
    assert reps[0]["record_id"] == "r1"


def test_load_analysis_clusters_and_representatives_batch_omits_missing_ids():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_analysis_cluster(client, "AC_1")

    loaded = orch._load_analysis_clusters_and_representatives_batch(client, "run-1", ["AC_1", "AC_missing"])
    assert set(loaded.keys()) == {"AC_1"}  # 見つからないIDはキー自体が無い


def test_load_analysis_clusters_and_representatives_batch_empty_input():
    client = FakeSupabaseClient()
    assert orch._load_analysis_clusters_and_representatives_batch(client, "run-1", []) == {}


def test_load_sentiment_clusters_and_representatives_batch_groups_by_cluster():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_sentiment_cluster(client, "SC_1")

    loaded = orch._load_sentiment_clusters_and_representatives_batch(client, "run-1", ["SC_1"])
    assert set(loaded.keys()) == {"SC_1"}
    cluster_row, reps = loaded["SC_1"]
    assert cluster_row["cluster_id"] == "SC_1"
    assert len(reps) == 1


# ---------------------------------------------------------------------------
# Content Filter隔離（2026-09-12実機確認: Azureのresponsible AI policyにより
# バッチ全体が決定的に拒否され、何度リトライしても解消しない事象への対応）
# ---------------------------------------------------------------------------

def _content_filter_batch_classify(poison_id: str):
    def _classify(azure_client, model, clusters):
        ids = [c["cluster_row"]["cluster_id"] for c in clusters]
        if poison_id in ids:
            raise Exception(
                "Error code: 400 - {'error': {'code': 'content_filter', "
                "'message': 'The response was filtered due to the prompt "
                "triggering Azure OpenAI's content management policy.'}}")
        results = [{"cluster_id": cid, **_ANALYSIS_RESPONSE} for cid in ids]
        return {"data": {"results": results}}
    return _classify


def test_classify_batch_with_retry_isolates_content_filter_poison_item_with_placeholder():
    client = FakeSupabaseClient()
    _seed_run(client)
    for cid in ["AC_A", "AC_B", "AC_C", "AC_D"]:
        _seed_analysis_cluster(client, cid)

    import geo_report_prompt1_classification_service as classify_mod
    valid, unresolved = orch._classify_batch_with_retry(
        client, None, "gpt-4o", "run-1", ["AC_A", "AC_B", "AC_C", "AC_D"],
        batch_loader=orch._load_analysis_clusters_and_representatives_batch,
        batch_classify=_content_filter_batch_classify("AC_C"),
        item_schema=classify_mod.ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA,
        build_item=orch._analysis_cluster_build_item,
        content_filter_placeholder=orch._content_filter_placeholder_analysis_cluster,
    )
    assert unresolved == []
    assert set(valid.keys()) == {"AC_A", "AC_B", "AC_C", "AC_D"}
    # 汚染源のクラスタはシステム除外プレースホルダーになっている(LLM判断ではない)
    assert valid["AC_C"]["report_use_status"] == "Exclude"
    assert valid["AC_C"]["noise_flag"] is True
    assert "content filter" in valid["AC_C"]["noise_reason"].lower() or "content_filter" in valid["AC_C"]["noise_reason"].lower()
    # 他のクラスタは正常に分類されている(隔離処理の巻き添えになっていない)
    assert valid["AC_A"]["final_topic_label"] == _ANALYSIS_RESPONSE["final_topic_label"]
    assert valid["AC_B"]["final_topic_label"] == _ANALYSIS_RESPONSE["final_topic_label"]
    assert valid["AC_D"]["final_topic_label"] == _ANALYSIS_RESPONSE["final_topic_label"]


def test_classify_batch_with_retry_content_filter_without_placeholder_stays_unresolved():
    """content_filter_placeholderを渡さない場合は、従来通り除外クラスタがunresolvedへ
    残る（後方互換の確認）。"""
    client = FakeSupabaseClient()
    _seed_run(client)
    for cid in ["AC_A", "AC_C"]:
        _seed_analysis_cluster(client, cid)

    import geo_report_prompt1_classification_service as classify_mod
    valid, unresolved = orch._classify_batch_with_retry(
        client, None, "gpt-4o", "run-1", ["AC_A", "AC_C"],
        batch_loader=orch._load_analysis_clusters_and_representatives_batch,
        batch_classify=_content_filter_batch_classify("AC_C"),
        item_schema=classify_mod.ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA,
        build_item=orch._analysis_cluster_build_item,
    )
    assert set(valid.keys()) == {"AC_A"}
    assert unresolved == ["AC_C"]


def test_bisect_content_filtered_single_poison_item_returns_excluded():
    client = FakeSupabaseClient()
    _seed_run(client)
    _seed_analysis_cluster(client, "AC_C")
    loaded = orch._load_analysis_clusters_and_representatives_batch(client, "run-1", ["AC_C"])

    import geo_report_prompt1_classification_service as classify_mod
    valid, excluded, retry_needed = orch._bisect_content_filtered(
        client, None, "gpt-4o", ["AC_C"], loaded,
        _content_filter_batch_classify("AC_C"),
        classify_mod.ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA, orch._analysis_cluster_build_item,
    )
    assert valid == {}
    assert retry_needed == []
    assert "AC_C" in excluded
