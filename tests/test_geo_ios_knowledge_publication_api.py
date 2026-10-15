"""Geopolitical Intelligence OS: Knowledge Publication API — extract_learningのReview Cycle
単位ループ化のテスト（2026-09-24、Learning Workbench対応 Plan A章）。

extract_learning_candidates自体（Review-bound Before/After解決ロジック）は
test_geo_ios_learning_extraction_service.pyで既に検証済みのため、ここではAPI層の責務
（Project内の各Review Cycleにつき1回ずつ、review_cycle_id指定で呼ぶループ・結果集約・
review_cycleが0件のProjectでの後方互換フォールバック）のみを検証する。
"""
import sys
from pathlib import Path

from fastapi import HTTPException
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_knowledge_publication_api as publication_api  # noqa: E402
import geo_ios_learning_extraction_service as learning_extraction_service  # noqa: E402
import geo_ios_review_service as review_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _req(**overrides):
    base = dict(extraction_model=None, prompt_version=None, require_published=True,
                actor_id="geo_learning_extraction")
    base.update(overrides)
    return publication_api.ExtractLearningRequest(**base)


def test_extract_learning_requires_azure_client(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(publication_api, "_client", client)
    monkeypatch.setattr(publication_api, "_azure_client", None)
    with pytest.raises(HTTPException) as exc_info:
        publication_api.extract_learning("project-1", _req())
    assert exc_info.value.status_code == 503


def test_extract_learning_falls_back_to_project_wide_call_when_no_review_cycles(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(publication_api, "_client", client)
    monkeypatch.setattr(publication_api, "_azure_client", object())
    monkeypatch.setattr(review_service, "list_review_cycles", lambda c, pid: [])

    calls = []

    def fake_extract(client_, azure_client_, model_, *, report_project_id, extraction_model,
                      prompt_version, require_published, actor_id, review_cycle_id=None):
        calls.append({"report_project_id": report_project_id, "review_cycle_id": review_cycle_id})
        return {"candidates": [], "idempotent_reuse": False, "deduplicated_count": 0, "conflict_count": 0}
    monkeypatch.setattr(learning_extraction_service, "extract_learning_candidates", fake_extract)

    result = publication_api.extract_learning("project-1", _req())

    assert len(calls) == 1
    assert calls[0]["review_cycle_id"] is None  # 従来どおりProject全体を対象とする呼び出し
    assert result["candidates"] == []


def test_extract_learning_loops_once_per_review_cycle_and_aggregates(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(publication_api, "_client", client)
    monkeypatch.setattr(publication_api, "_azure_client", object())
    monkeypatch.setattr(review_service, "list_review_cycles", lambda c, pid: [
        {"review_cycle_id": "cycle-1", "round_number": 1},
        {"review_cycle_id": "cycle-2", "round_number": 2},
    ])

    calls = []

    def fake_extract(client_, azure_client_, model_, *, report_project_id, extraction_model,
                      prompt_version, require_published, actor_id, review_cycle_id=None):
        calls.append(review_cycle_id)
        if review_cycle_id == "cycle-1":
            return {"candidates": [{"candidate_id": "c1"}], "idempotent_reuse": False,
                     "deduplicated_count": 1, "conflict_count": 0}
        return {"candidates": [{"candidate_id": "c2"}], "idempotent_reuse": True,
                 "deduplicated_count": 0, "conflict_count": 2}
    monkeypatch.setattr(learning_extraction_service, "extract_learning_candidates", fake_extract)

    result = publication_api.extract_learning("project-1", _req())

    # 1 Round = 1回のLLM抽出呼び出し（Round跨ぎで混在しない）
    assert calls == ["cycle-1", "cycle-2"]
    assert [c["candidate_id"] for c in result["candidates"]] == ["c1", "c2"]
    assert result["deduplicated_count"] == 1
    assert result["conflict_count"] == 2
    assert result["idempotent_reuse"] is True  # いずれかのcycleがidempotent_reuseならTrue
    assert set(result["results_by_review_cycle"].keys()) == {"cycle-1", "cycle-2"}


def test_extract_learning_not_evaluable_round_yields_empty_candidates_without_error(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(publication_api, "_client", client)
    monkeypatch.setattr(publication_api, "_azure_client", object())
    monkeypatch.setattr(review_service, "list_review_cycles", lambda c, pid: [
        {"review_cycle_id": "cycle-1", "round_number": 1},
    ])

    def fake_extract(client_, azure_client_, model_, *, report_project_id, extraction_model,
                      prompt_version, require_published, actor_id, review_cycle_id=None):
        return {"candidates": [], "idempotent_reuse": False, "deduplicated_count": 0,
                "conflict_count": 0, "reason": "not_evaluable", "after_resolution_method": "not_evaluable"}
    monkeypatch.setattr(learning_extraction_service, "extract_learning_candidates", fake_extract)

    result = publication_api.extract_learning("project-1", _req())
    assert result["candidates"] == []


def test_extract_learning_propagates_learning_extraction_error_as_400(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(publication_api, "_client", client)
    monkeypatch.setattr(publication_api, "_azure_client", object())
    monkeypatch.setattr(review_service, "list_review_cycles", lambda c, pid: [
        {"review_cycle_id": "cycle-1", "round_number": 1},
    ])

    def failing_extract(*args, **kwargs):
        raise learning_extraction_service.LearningExtractionError("boom")
    monkeypatch.setattr(learning_extraction_service, "extract_learning_candidates", failing_extract)

    with pytest.raises(HTTPException) as exc_info:
        publication_api.extract_learning("project-1", _req())
    assert exc_info.value.status_code == 400
