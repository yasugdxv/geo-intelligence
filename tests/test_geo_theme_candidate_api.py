"""Phase 2G-3: Theme Candidate Internal Coverage API（Runtime統合）。

internal_coverageをgeo_theme_candidatesへ一切永続化しないこと、GETは既存方針通り
未認証でも呼べること（Permission判定自体はassess_internal_coverage内部のPhase 2F-6
Stable Coreが担う）、Stage 2（LLM）は既定で呼ばれないことを検証する。
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.params import Depends as DependsMarker  # noqa: E402

import geo_ios_identity  # noqa: E402
import geo_theme_candidate_api as candidate_api  # noqa: E402
import geo_theme_candidate_service as candidate_service  # noqa: E402
import geo_intelligence_ai_theme_coverage_service as theme_coverage_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _candidate_row(**overrides) -> dict:
    row = {
        "candidate_id": "cand-1", "title": "米国対中AI半導体政策の変化",
        "question": "規制強化そのものではなく、裁量確保への運用変化なのか？",
        "signal_summary": "複数Actorが同時期に関連行動", "observed_change": "...", "why_now": "...",
        "actors": ["White House", "Beijing"], "regions": ["Washington"], "themes": ["半導体"],
        "candidate_type": "changing", "change_type": "policy_shift",
    }
    row.update(overrides)
    return row


def _tables(candidates=None) -> dict:
    return {"geo_theme_candidates": candidates or []}


def test_candidate_to_theme_candidate_input_maps_fields_correctly():
    candidate = _candidate_row()
    mapped = candidate_service.candidate_to_theme_candidate_input(candidate)
    assert mapped["theme_title"] == candidate["title"]
    assert mapped["theme_description"] == candidate["question"]
    assert mapped["actors"] == candidate["actors"]
    assert mapped["regions"] == candidate["regions"]
    assert mapped["keywords"] == candidate["themes"]
    assert mapped["signal_summary"] == candidate["signal_summary"]


def test_internal_coverage_endpoint_uses_optional_identity_not_mandatory():
    """§GETは既存方針（Phase 2G-1）を維持——未認証でも呼べる（mutationのみ認証必須）。"""
    sig = inspect.signature(candidate_api.get_theme_candidate_internal_coverage)
    found_optional = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.get_optional_ios_identity
        for p in sig.parameters.values())
    found_mandatory = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.verify_ios_identity
        for p in sig.parameters.values())
    assert found_optional, "get_theme_candidate_internal_coverage がget_optional_ios_identityを使っていません"
    assert not found_mandatory, "internal-coverage（GET）がmutationと同じ必須認証になっています"


def test_internal_coverage_endpoint_returns_404_for_missing_candidate():
    client = FakeSupabaseClient(tables=_tables())
    candidate_api._client = client
    anon = geo_ios_identity.IosIdentity(authenticated=False)
    try:
        candidate_api.get_theme_candidate_internal_coverage("missing-id", identity=anon)
        assert False, "存在しないcandidate_idで404にならなかった"
    except Exception as e:  # noqa: BLE001 — HTTPExceptionをここでは型import省略
        assert getattr(e, "status_code", None) == 404


def test_internal_coverage_does_not_persist_anything_and_defaults_to_stage1(monkeypatch):
    """internal_coverageはgeo_theme_candidatesへ一切書き込まない（Runtime計算のみ）。
    include_analysis省略時はStage 1（LLM無し）のまま——assess_internal_coverageへ
    include_analysis=Falseで渡されることを確認する。"""
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    candidate_api._client = client
    candidate_api._azure_client = object()  # Stage2が明示要求されても実行可能な状態にしておく

    captured = {}

    def _fake_assess(client_, azure_client, model, theme_candidate, *, requester_context=None,
                      include_analysis=False, **kwargs):
        captured["include_analysis"] = include_analysis
        captured["theme_candidate"] = theme_candidate
        captured["requester_context"] = requester_context
        return {"coverage": {}, "recency": {}, "similar_internal_intelligence": [],
                "key_actors_already_covered": [], "potential_coverage_gaps": [],
                "existing_analysis_themes": [], "source_refs": [], "stage": "metadata_only",
                "warnings": []}
    monkeypatch.setattr(theme_coverage_service, "assess_internal_coverage", _fake_assess)

    anon = geo_ios_identity.IosIdentity(authenticated=False)
    candidate_api.get_theme_candidate_internal_coverage("cand-1", identity=anon)

    assert captured["include_analysis"] is False
    assert captured["theme_candidate"]["theme_title"] == "米国対中AI半導体政策の変化"
    # geo_theme_candidatesへ一切書き込まれていないこと
    assert client.updated == []
    assert "geo_theme_candidates" not in client.inserted or client.inserted["geo_theme_candidates"] == []


def test_internal_coverage_requester_context_reflects_current_identity_each_call(monkeypatch):
    """§他部門Reportの存在を匂わせない/各requestで現在のidentityを使う——同じcandidateでも
    呼び出しごとに異なるidentityを渡せば、assess_internal_coverageへ渡るrequester_contextが
    それに追従することを確認する（キャッシュ・使い回しが無い）。"""
    client = FakeSupabaseClient(tables=_tables(candidates=[_candidate_row()]))
    candidate_api._client = client

    captured_contexts = []

    def _fake_assess(client_, azure_client, model, theme_candidate, *, requester_context=None,
                      include_analysis=False, **kwargs):
        captured_contexts.append(requester_context)
        return {"coverage": {}, "recency": {}, "similar_internal_intelligence": [],
                "key_actors_already_covered": [], "potential_coverage_gaps": [],
                "existing_analysis_themes": [], "source_refs": [], "stage": "metadata_only",
                "warnings": []}
    monkeypatch.setattr(theme_coverage_service, "assess_internal_coverage", _fake_assess)

    identity_a = geo_ios_identity.IosIdentity(
        identity_id="user-a", assigned_role="viewer", domain=None, department="geopolitics", authenticated=True)
    identity_b = geo_ios_identity.IosIdentity(
        identity_id="user-b", assigned_role="viewer", domain=None, department="other-dept", authenticated=True)

    candidate_api.get_theme_candidate_internal_coverage("cand-1", identity=identity_a)
    candidate_api.get_theme_candidate_internal_coverage("cand-1", identity=identity_b)

    assert captured_contexts[0]["department"] == "geopolitics"
    assert captured_contexts[1]["department"] == "other-dept"


# ---- Phase 2G-4: Wall-bounce / Refinement API配線 ----------------------------------

def test_wall_bounce_endpoint_uses_optional_identity_not_mandatory():
    sig = inspect.signature(candidate_api.wall_bounce_chat)
    found_optional = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.get_optional_ios_identity
        for p in sig.parameters.values())
    found_mandatory = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.verify_ios_identity
        for p in sig.parameters.values())
    assert found_optional, "wall_bounce_chat がget_optional_ios_identityを使っていません"
    assert not found_mandatory, "wall-bounce（GET相当のchat）がmutationと同じ必須認証になっています"


def test_refinement_endpoint_requires_ios_identity():
    sig = inspect.signature(candidate_api.apply_theme_candidate_refinement)
    found = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.verify_ios_identity
        for p in sig.parameters.values())
    assert found, "apply_theme_candidate_refinement に Depends(geo_ios_identity.verify_ios_identity) が無い"


def test_refinement_request_schema_has_no_status_or_actor_id_field():
    """§Human-edit ownership/No Automatic Status Change: Request schema自体にstatus/
    actor_idが無いことを構造的に保証する（クライアントから送らせない、送れない）。"""
    fields = set(candidate_api.RefinementRequest.model_fields)
    assert "status" not in fields
    assert "actor_id" not in fields
    assert fields == {"title", "question", "actors", "regions", "themes", "business_relevance"}


def test_wall_bounce_endpoint_raises_404_for_missing_candidate(monkeypatch):
    import geo_theme_candidate_wallbounce_service as wallbounce_service

    def _raise_not_found(*a, **kw):
        raise candidate_service.ThemeCandidateError("not found")
    monkeypatch.setattr(wallbounce_service, "chat", _raise_not_found)
    candidate_api._azure_client = object()

    anon = geo_ios_identity.IosIdentity(authenticated=False)
    req = candidate_api.WallBounceRequest(message="hello", history=[])
    try:
        candidate_api.wall_bounce_chat("missing-id", req, identity=anon)
        assert False, "存在しないcandidate_idで404にならなかった"
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "status_code", None) == 404


# ---- Phase 2G-5: Expert AI Handoff API配線 -----------------------------------------

def test_handoff_preview_endpoint_uses_optional_identity_not_mandatory():
    sig = inspect.signature(candidate_api.preview_theme_candidate_handoff)
    found_optional = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.get_optional_ios_identity
        for p in sig.parameters.values())
    found_mandatory = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.verify_ios_identity
        for p in sig.parameters.values())
    assert found_optional, "preview_theme_candidate_handoff がget_optional_ios_identityを使っていません"
    assert not found_mandatory, "handoff-preview（読み取り専用）がmutationと同じ必須認証になっています"


def test_handoff_endpoint_requires_ios_identity():
    sig = inspect.signature(candidate_api.handoff_theme_candidate)
    found = any(
        isinstance(p.default, DependsMarker) and p.default.dependency is geo_ios_identity.verify_ios_identity
        for p in sig.parameters.values())
    assert found, "handoff_theme_candidate に Depends(geo_ios_identity.verify_ios_identity) が無い"


def test_handoff_request_schema_has_only_investigation_questions():
    """§13: user_id/actor_idをクライアントから送らせない——Request schemaにそもそも無い。"""
    assert set(candidate_api.HandoffRequest.model_fields) == {"investigation_questions"}


def test_handoff_endpoint_returns_503_when_llm_unavailable():
    candidate_api._azure_client = None
    req = candidate_api.HandoffRequest(investigation_questions=[])
    identity = geo_ios_identity.IosIdentity(
        identity_id="user-a", assigned_role="viewer", domain=None, department="geopolitics", authenticated=True)
    try:
        candidate_api.handoff_theme_candidate("cand-1", req, identity=identity)
        assert False, "AI未設定時に503にならなかった"
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "status_code", None) == 503


def test_handoff_endpoint_raises_400_for_missing_candidate(monkeypatch):
    import geo_theme_candidate_handoff_service as handoff_service

    def _raise_not_found(*a, **kw):
        raise handoff_service.ThemeCandidateHandoffError("not found")
    monkeypatch.setattr(handoff_service, "handoff_theme_candidate_to_expert_ai", _raise_not_found)
    candidate_api._azure_client = object()

    req = candidate_api.HandoffRequest(investigation_questions=[])
    identity = geo_ios_identity.IosIdentity(
        identity_id="user-a", assigned_role="viewer", domain=None, department="geopolitics", authenticated=True)
    try:
        candidate_api.handoff_theme_candidate("missing-id", req, identity=identity)
        assert False, "存在しないcandidate_idで400にならなかった"
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "status_code", None) == 400


def test_handoff_preview_endpoint_raises_404_for_missing_candidate(monkeypatch):
    import geo_theme_candidate_handoff_service as handoff_service

    def _raise_not_found(*a, **kw):
        raise handoff_service.ThemeCandidateHandoffError("not found")
    monkeypatch.setattr(handoff_service, "build_handoff_preview", _raise_not_found)

    anon = geo_ios_identity.IosIdentity(authenticated=False)
    req = candidate_api.HandoffRequest(investigation_questions=[])
    try:
        candidate_api.preview_theme_candidate_handoff("missing-id", req, identity=anon)
        assert False, "存在しないcandidate_idで404にならなかった"
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "status_code", None) == 404
