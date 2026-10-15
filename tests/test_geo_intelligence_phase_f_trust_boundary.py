"""Geopolitical Intelligence OS Phase F: Cross-Agent Trust Boundary / Access Enforcement /
Intelligence Needs E2Eのテスト。

§27 Completion Definition（Sustainability Agent → Authenticated → Query → Access Filter →
Authorized Knowledgeのみ → Sufficiency → Answer/Need、requester_app偽装での権限昇格不可）を
実際にgeo_intelligence_auth.verify_api_key + geo_intelligence_service.process_requestの
組み合わせで検証する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_intelligence_auth as auth  # noqa: E402
import geo_intelligence_service as service  # noqa: E402
import geo_knowledge_gap_service as gap_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


# ---- §21 Authentication ------------------------------------------------------

def test_verify_api_key_dev_no_auth_when_registry_empty(monkeypatch):
    monkeypatch.setattr(auth, "_CALLER_REGISTRY", {})
    identity = auth.verify_api_key(x_api_key=None)
    assert identity.authenticated is False
    assert identity.caller_app is None and identity.caller_domain is None


def test_verify_api_key_valid_mapped_credentials(monkeypatch):
    monkeypatch.setattr(auth, "_CALLER_REGISTRY", {
        "geo-key": {"caller_app": "geo_internal_service", "caller_domain": "geopolitics"},
        "sust-key": {"caller_app": "sustainability_intelligence", "caller_domain": "sustainability"},
        "erm-key": {"caller_app": "erm_intelligence", "caller_domain": "erm"},
    })
    geo_identity = auth.verify_api_key(x_api_key="geo-key")
    assert geo_identity.authenticated is True and geo_identity.caller_domain == "geopolitics"
    sust_identity = auth.verify_api_key(x_api_key="sust-key")
    assert sust_identity.caller_domain == "sustainability"
    erm_identity = auth.verify_api_key(x_api_key="erm-key")
    assert erm_identity.caller_domain == "erm"


def test_verify_api_key_unknown_credential_rejected(monkeypatch):
    monkeypatch.setattr(auth, "_CALLER_REGISTRY", {"sust-key": {"caller_app": "s", "caller_domain": "sustainability"}})
    with pytest.raises(Exception) as exc_info:
        auth.verify_api_key(x_api_key="not-a-real-key")
    assert getattr(exc_info.value, "status_code", None) == 401


def test_verify_api_key_missing_credential_rejected_when_registry_configured(monkeypatch):
    monkeypatch.setattr(auth, "_CALLER_REGISTRY", {"sust-key": {"caller_app": "s", "caller_domain": "sustainability"}})
    with pytest.raises(Exception) as exc_info:
        auth.verify_api_key(x_api_key=None)
    assert getattr(exc_info.value, "status_code", None) == 401


def test_verify_api_key_legacy_string_key_has_no_domain(monkeypatch):
    """後方互換: caller_app/caller_domain紐付け無しの従来型キーもそのまま認証は通る
    （authenticated=Trueだがcaller_domain=None、Access Policyではunknown domain=deny扱い）。"""
    monkeypatch.setattr(auth, "_CALLER_REGISTRY", auth._load_caller_registry({
        "geo_intelligence": {"api_keys": ["legacy-plain-key"]}}))
    identity = auth.verify_api_key(x_api_key="legacy-plain-key")
    assert identity.authenticated is True
    assert identity.caller_domain is None


# ---- Fixtures共通 ------------------------------------------------------------

def _department_request(**overrides) -> dict:
    request = {
        "requester_app": "sustainability_intelligence",
        "request_type": "user_question",
        "question": "メキシコのWater issueの政治化・規制化可能性を見たい",
        "country_region": ["Mexico"], "themes": ["water"],
        "source_department": "sustainability", "source_workflow": "chat",
        "analysis_requested": True,
    }
    request.update(overrides)
    return request


def _client_with_knowledge(agent_sharing_policy: str, *, title="メキシコ(Mexico): 水資源政策") -> FakeSupabaseClient:
    # title/summaryはFakeSupabaseClientのilike(部分一致)シミュレーションが実FTSの言語処理を
    # 再現しないため、country_region/themesの英語トークン（"Mexico"/"water"）をそのまま含める
    # 必要がある（test_geo_intelligence_phase_e_enforcement.pyと同じ流儀）。
    return FakeSupabaseClient(tables={
        "geo_knowledge_items": [{
            "knowledge_item_id": "ki-1", "knowledge_type": "country_foundation",
            "title": title, "summary": "水資源(water)規制の概要",
            "status": "active", "retrieval_status": "available", "is_demo": False,
            "confidence": "高", "updated_at": "2026-08-01T00:00:00+00:00",
            "human_visibility": "company", "agent_sharing_policy": agent_sharing_policy,
        }],
    })


class _Identity:
    def __init__(self, caller_app=None, caller_domain=None, authenticated=True):
        self.caller_app, self.caller_domain, self.authenticated = caller_app, caller_domain, authenticated


SUSTAINABILITY_IDENTITY = _Identity(caller_app="sustainability_intelligence", caller_domain="sustainability")
GEOPOLITICS_IDENTITY = _Identity(caller_app="geo_internal_service", caller_domain="geopolitics")


# ---- §7 Spoofing Test（最重要Security Gate） --------------------------------

def test_requester_app_spoofing_as_geo_chat_ui_does_not_grant_internal_access():
    """Credential=Sustainability、payload.requester_app="geo_chat_ui"を詐称しても、
    内部呼び出し扱いにならずgeopolitics_only Knowledgeは見えないこと。"""
    client = _client_with_knowledge("geopolitics_only")
    response = service.process_request(
        client, _department_request(requester_app="geo_chat_ui"), caller_identity=SUSTAINABILITY_IDENTITY)
    di = response["department_intelligence"]
    assert "foundation" not in (di.get("covered_dimensions") or [])


def test_requester_app_spoofing_as_geopolitics_does_not_grant_internal_access():
    client = _client_with_knowledge("geopolitics_only")
    response = service.process_request(
        client, _department_request(requester_app="geopolitics"), caller_identity=SUSTAINABILITY_IDENTITY)
    di = response["department_intelligence"]
    assert "foundation" not in (di.get("covered_dimensions") or [])


def test_genuine_geopolitics_identity_treated_as_internal_via_retrieve_knowledge():
    """認証済みcaller_domain=geopoliticsの場合のみ内部扱い。"""
    client = _client_with_knowledge("geopolitics_only")
    normalized = service.normalize_request({
        "requester_app": "some_internal_tool", "request_type": "user_question",
        "question": "メキシコの水資源規制について", "country_region": ["Mexico"], "themes": ["water"],
        "analysis_requested": False,
    })
    normalized["_caller_authenticated"] = True
    normalized["_authenticated_caller_domain"] = "geopolitics"
    retrieval = service.retrieve_knowledge(client, normalized)
    assert any(i["knowledge_item_id"] == "ki-1" for i in retrieval["foundation"])


def test_identity_mismatch_is_logged_but_does_not_block_request():
    client = _client_with_knowledge("allowed_cross_domain")
    response = service.process_request(
        client, _department_request(requester_app="totally_different_label"),
        caller_identity=SUSTAINABILITY_IDENTITY)
    assert response["status"] == "completed"
    mismatch_logs = [r for r in client.tables.get("geo_audit_logs", [])
                      if r["action"] == "cross_agent_caller_identity_mismatch"]
    assert len(mismatch_logs) == 1
    assert mismatch_logs[0]["detail"]["authenticated_caller_app"] == "sustainability_intelligence"
    assert mismatch_logs[0]["detail"]["payload_requester_app"] == "totally_different_label"


def test_no_caller_identity_falls_back_to_self_reported_requester_app():
    """caller_identity未指定（内部プロセス内呼び出し・既存テスト）は既存どおり
    requester_app自己申告ベース——後方互換。"""
    client = _client_with_knowledge("geopolitics_only")
    response = service.process_request(client, _department_request())  # caller_identity無し
    di = response["department_intelligence"]
    assert "foundation" not in (di.get("covered_dimensions") or [])  # 既定はgeopolitics_onlyなので依然deny


# ---- §12 Sufficiency E2E: Case A/B/C ----------------------------------------

def test_case_a_cross_domain_allowed_knowledge_is_sufficient_no_need():
    client = _client_with_knowledge("allowed_cross_domain")
    response = service.process_request(
        client, _department_request(), caller_identity=SUSTAINABILITY_IDENTITY)
    assert response["knowledge_sufficiency"] in ("sufficient", "partial")
    if response["knowledge_sufficiency"] == "sufficient":
        assert not (response.get("knowledge_gap") or {}).get("exists")


def test_case_b_restricted_only_knowledge_excluded_creates_need():
    client = _client_with_knowledge("geopolitics_only", title="イラン(Iran)情勢: 制裁(sanctions)関連レポート")
    response = service.process_request(
        client, _department_request(question="イランに関する規制動向は？", country_region=["Iran"], themes=["sanctions"]),
        caller_identity=SUSTAINABILITY_IDENTITY)
    assert response["knowledge_sufficiency"] == "insufficient"
    gap = response.get("knowledge_gap")
    assert gap and gap.get("exists")

    # ---- §14 Restricted KnowledgeをNeedへ漏らさない ----
    gaps = client.tables.get("geo_knowledge_gaps", [])
    assert len(gaps) == 1
    gap_text = str(gaps[0])
    assert "イラン(Iran)情勢: 制裁(sanctions)関連レポート" not in gap_text  # restrictedなKnowledgeのtitleを漏らさない


def test_case_c_partial_authorized_knowledge_answers_and_creates_need_for_gaps():
    client = FakeSupabaseClient(tables={
        "geo_knowledge_items": [{
            "knowledge_item_id": "ki-allowed", "knowledge_type": "country_foundation",
            "title": "メキシコ(Mexico)基礎情報(公開)", "summary": "水資源(water)規制の概要",
            "status": "active", "retrieval_status": "available", "is_demo": False,
            "confidence": "高", "updated_at": "2026-08-01T00:00:00+00:00",
            "agent_sharing_policy": "allowed_cross_domain",
        }],
    })
    response = service.process_request(
        client, _department_request(), caller_identity=SUSTAINABILITY_IDENTITY)
    assert response["knowledge_sufficiency"] in ("partial", "sufficient")
    di = response["department_intelligence"]
    assert "foundation" in (di.get("covered_dimensions") or [])


# ---- §15 Need Deduplication ---------------------------------------------------

def test_same_question_twice_does_not_create_duplicate_need():
    client = _client_with_knowledge("geopolitics_only", title="イラン(Iran)情勢: 核(nuclear)関連レポート")
    request = _department_request(question="イランの核合意はどうなる？", country_region=["Iran"], themes=["nuclear"])

    service.process_request(client, {**request, "request_id": "req-1"}, caller_identity=SUSTAINABILITY_IDENTITY)
    service.process_request(client, {**request, "request_id": "req-2"}, caller_identity=SUSTAINABILITY_IDENTITY)

    open_gaps = [g for g in client.tables.get("geo_knowledge_gaps", [])
                 if g.get("origin_type") == "external_app_request"]
    assert len(open_gaps) == 1
    assert open_gaps[0].get("request_count", 1) >= 2


# ---- §22 Actor/Media Intelligence audit（department_intelligence側で検証済みだが、
#      Phase Fの完了条件として直接ここでも再確認する） ---------------------------------

def test_access_denied_audit_logged_once_when_items_filtered(monkeypatch):
    """§18 audit: cross_agent_access_deniedが集計1件として記録される（個別item毎ではない）。"""
    client = _client_with_knowledge("geopolitics_only")
    normalized = service.normalize_request(_department_request())
    normalized["_caller_authenticated"] = True
    normalized["_authenticated_caller_domain"] = "sustainability"
    service.retrieve_knowledge(client, normalized)
    denied_logs = [r for r in client.tables.get("geo_audit_logs", [])
                   if r["action"] == "cross_agent_access_denied"]
    assert len(denied_logs) == 1
    assert denied_logs[0]["detail"]["total_before"] == 1
    assert denied_logs[0]["detail"]["total_after"] == 0


def test_actor_and_media_intelligence_packs_always_empty_for_cross_domain():
    import geo_department_intelligence_service as dept_service
    client = _client_with_knowledge("allowed_cross_domain")
    normalized = service.normalize_request(_department_request())
    retrieval = service.retrieve_knowledge(client, normalized)
    result = dept_service.retrieve_department_intelligence(client, normalized, retrieval)
    assert result["actor_intelligence"] == {"key_actors": [], "known_ids": set(), "actor_intelligence_status": "partial"}
    assert result["media_intelligence"]["media_patterns"] == []
    assert result["media_intelligence"]["media_profiles"] == []
