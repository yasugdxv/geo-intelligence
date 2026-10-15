"""Geopolitical Intelligence OS Phase E: geo_knowledge_api.pyのDirect Fetch Bypass対策・
human_visibility強制のテスト（§13D, §25 Direct Fetch/Candidate Draft Leakage）。

geo_knowledge_api.py自体はモジュール読み込み時に実SupabaseClient/Azure Clientを生成するため、
_client属性のみmonkeypatchでFakeSupabaseClientへ差し替え、実ネットワークアクセスを避ける
（api_server.py配線・importの正しさは別途py_compile/importで確認済み）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_identity  # noqa: E402
import geo_knowledge_api  # noqa: E402
import geo_role_guard  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

# Phase 2E: list_knowledge_items/get_knowledge_itemはgeo_ios_identity.get_optional_ios_identity
# 経由でidentityを受け取るようになった（Depends既定値はFastAPI経由でのみ解決されるため、
# 直接呼び出すテストでは明示的に渡す必要がある——既存のreview_knowledge_item系テストと同じ
# 呼び出し方）。既定は未ログイン（匿名）を模したIdentityとする。
_ANONYMOUS = geo_ios_identity.IosIdentity(authenticated=False)


@pytest.fixture()
def fake_client(monkeypatch):
    client = FakeSupabaseClient()
    monkeypatch.setattr(geo_knowledge_api, "_client", client)
    return client


def _seed_item(client, **overrides) -> dict:
    row = {
        "knowledge_type": "reasoning_rule", "title": "t", "summary": "s", "structured_content": {},
        "status": "active", "retrieval_status": "available", "confidence": "中",
        "information_class": "internal_hypothesis", "created_by": "analyst-1", "is_demo": False,
        "human_visibility": "company", "agent_sharing_policy": "geopolitics_only",
    }
    row.update(overrides)
    return client.insert("geo_knowledge_items", [row])[0]


# ---- Direct Fetch Bypass（§13D, §26） ---------------------------------------

def test_get_knowledge_item_returns_active_item_by_default(fake_client):
    item = _seed_item(fake_client, status="active", retrieval_status="available")
    result = geo_knowledge_api.get_knowledge_item(item["knowledge_item_id"], identity=_ANONYMOUS)
    assert result["knowledge_item_id"] == item["knowledge_item_id"]


@pytest.mark.parametrize("status,retrieval_status", [
    ("candidate", "restricted"), ("rejected", "archived"), ("in_review", "restricted"),
    ("superseded", "deprecated"), ("active", "restricted"),
])
def test_get_knowledge_item_hides_non_retrievable_by_default(fake_client, status, retrieval_status):
    item = _seed_item(fake_client, status=status, retrieval_status=retrieval_status)
    with pytest.raises(Exception) as exc_info:
        geo_knowledge_api.get_knowledge_item(item["knowledge_item_id"], identity=_ANONYMOUS)
    assert getattr(exc_info.value, "status_code", None) == 404


def test_get_knowledge_item_privileged_view_requires_reviewer_role(fake_client):
    item = _seed_item(fake_client, status="candidate", retrieval_status="restricted")
    with pytest.raises(Exception) as exc_info:
        geo_knowledge_api.get_knowledge_item(
            item["knowledge_item_id"], include_non_retrievable=True, actor_role="viewer",
            identity=_ANONYMOUS)
    assert getattr(exc_info.value, "status_code", None) == 403
    result = geo_knowledge_api.get_knowledge_item(
        item["knowledge_item_id"], include_non_retrievable=True, actor_role="knowledge_steward",
        identity=_ANONYMOUS)
    assert result["knowledge_item_id"] == item["knowledge_item_id"]


def test_get_knowledge_item_hides_department_only_item_from_other_department(fake_client):
    """Phase 2E: human_visibility="custom"+allowed_org_units（Report/Knowledge Item共通の
    DEPARTMENT_ONLY相当）は、所属部門が一致しない認証済み利用者からはDirect Fetchでも
    見えない。"""
    item = _seed_item(fake_client, status="active", retrieval_status="available",
                      human_visibility="custom", allowed_org_units=["geopolitics"],
                      created_by="owner-1")
    other_dept = geo_ios_identity.IosIdentity(
        identity_id="u2", assigned_role="analyst", department="sustainability", authenticated=True)
    with pytest.raises(Exception) as exc_info:
        geo_knowledge_api.get_knowledge_item(item["knowledge_item_id"], identity=other_dept)
    assert getattr(exc_info.value, "status_code", None) == 404

    same_dept = geo_ios_identity.IosIdentity(
        identity_id="u3", assigned_role="analyst", department="geopolitics", authenticated=True)
    result = geo_knowledge_api.get_knowledge_item(item["knowledge_item_id"], identity=same_dept)
    assert result["knowledge_item_id"] == item["knowledge_item_id"]


def test_freshness_endpoint_same_bypass_fix(fake_client):
    item = _seed_item(fake_client, status="rejected", retrieval_status="archived")
    with pytest.raises(Exception) as exc_info:
        geo_knowledge_api.get_knowledge_item_freshness(item["knowledge_item_id"])
    assert getattr(exc_info.value, "status_code", None) == 404
    result = geo_knowledge_api.get_knowledge_item_freshness(
        item["knowledge_item_id"], include_non_retrievable=True, actor_role="expert_reviewer")
    assert "freshness" in result


def test_unknown_id_returns_404_not_leaking_existence(fake_client):
    with pytest.raises(Exception) as exc_info:
        geo_knowledge_api.get_knowledge_item("does-not-exist", identity=_ANONYMOUS)
    assert getattr(exc_info.value, "status_code", None) == 404


# ---- List default filtering ----------------------------------------------

def test_list_knowledge_items_defaults_to_active_available(fake_client):
    _seed_item(fake_client, status="active", retrieval_status="available")
    _seed_item(fake_client, status="candidate", retrieval_status="restricted")
    _seed_item(fake_client, status="superseded", retrieval_status="deprecated")

    result = geo_knowledge_api.list_knowledge_items(identity=_ANONYMOUS)
    assert len(result["items"]) == 1
    assert result["items"][0]["status"] == "active"


def test_list_knowledge_items_explicit_status_still_works(fake_client):
    """既存/candidates等、明示的にstatusを指定する呼び出しは影響を受けない。"""
    _seed_item(fake_client, status="candidate", retrieval_status="restricted")
    result = geo_knowledge_api.list_knowledge_items(status="candidate", identity=_ANONYMOUS)
    assert len(result["items"]) == 1


def test_list_knowledge_items_hides_department_only_item_from_other_department(fake_client):
    """Phase 2E: 一覧endpointにもhuman_visibility（custom+allowed_org_units）が適用される
    こと（監査で確認済みのgapの再発防止）。"""
    _seed_item(fake_client, status="active", retrieval_status="available",
              human_visibility="custom", allowed_org_units=["geopolitics"])
    other_dept = geo_ios_identity.IosIdentity(
        identity_id="u2", assigned_role="analyst", department="sustainability", authenticated=True)
    result = geo_knowledge_api.list_knowledge_items(identity=other_dept)
    assert result["items"] == []


def test_list_candidates_endpoint_unaffected_by_default_change(fake_client):
    _seed_item(fake_client, status="candidate", retrieval_status="restricted")
    _seed_item(fake_client, status="in_review", retrieval_status="restricted")
    result = geo_knowledge_api.list_knowledge_candidates()
    assert len(result["candidates"]) == 2


# ---- Human Visibility enforcement on search（§13C, §25） --------------------

def test_search_excludes_restricted_visibility_for_anonymous(fake_client, monkeypatch):
    accessible = _seed_item(fake_client, title="公開情報", human_visibility="company")
    restricted = _seed_item(fake_client, title="限定情報", human_visibility="private", created_by="owner-x")

    def fake_search(client, q, *, status=None, knowledge_types=None, include_demo=False, limit=20):
        items = [accessible, restricted] if status is None else [
            i for i in [accessible, restricted] if i["status"] in status]
        return {"results": [{"knowledge_item": i, "final_score": 1.0} for i in items]}

    monkeypatch.setattr(geo_knowledge_api.geo_search_service, "search_knowledge_items", fake_search)

    result = geo_knowledge_api.search_knowledge_items(q="test")
    ids = [r["knowledge_item"]["knowledge_item_id"] for r in result["results"]]
    assert accessible["knowledge_item_id"] in ids
    assert restricted["knowledge_item_id"] not in ids


def test_search_allows_private_item_for_its_owner(fake_client, monkeypatch):
    owned = _seed_item(fake_client, title="自分の下書き", human_visibility="private", created_by="owner-x")

    def fake_search(client, q, *, status=None, knowledge_types=None, include_demo=False, limit=20):
        return {"results": [{"knowledge_item": owned, "final_score": 1.0}]}

    monkeypatch.setattr(geo_knowledge_api.geo_search_service, "search_knowledge_items", fake_search)

    denied = geo_knowledge_api.search_knowledge_items(q="test")
    assert denied["results"] == []
    allowed = geo_knowledge_api.search_knowledge_items(q="test", requester_user_id="owner-x")
    assert len(allowed["results"]) == 1
