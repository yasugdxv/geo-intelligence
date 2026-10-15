"""Geopolitical Intelligence OS Phase E: Access Policy Service（geo_knowledge_access_policy）の
単体テスト。§25 Human Visibility / Agent Sharing / unknown→denyの基本挙動を確認する。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_knowledge_access_policy as policy  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def _item(**overrides) -> dict:
    base = {
        "knowledge_item_id": "ki-1", "status": "active", "retrieval_status": "available",
        "human_visibility": "company", "agent_sharing_policy": "geopolitics_only",
        "created_by": "analyst-1",
    }
    base.update(overrides)
    return base


# ---- is_retrievable（Official Knowledge Definition, §3） -------------------

def test_is_retrievable_requires_active_and_available():
    assert policy.is_retrievable(_item()) is True
    assert policy.is_retrievable(_item(status="candidate")) is False
    assert policy.is_retrievable(_item(status="superseded")) is False
    assert policy.is_retrievable(_item(retrieval_status="restricted")) is False


# ---- Agent Sharing（§10, §14） ----------------------------------------------

def test_internal_caller_always_allowed_regardless_of_policy():
    for p in ("none", "geopolitics_only", "allowed_cross_domain", "custom"):
        assert policy.can_agent_access(_item(agent_sharing_policy=p), is_internal_caller=True) is True


def test_external_caller_default_geopolitics_only_denied():
    item = _item(agent_sharing_policy="geopolitics_only")
    assert policy.can_agent_access(item, is_internal_caller=False) is False


def test_external_caller_allowed_cross_domain_allowed():
    item = _item(agent_sharing_policy="allowed_cross_domain")
    assert policy.can_agent_access(item, is_internal_caller=False) is True


def test_external_caller_none_denied():
    assert policy.can_agent_access(_item(agent_sharing_policy="none"), is_internal_caller=False) is False


def test_external_caller_custom_denies_by_default():
    """§11 unknownをallow扱いにしない: 個別ACL未実装のcustomは安全側でdeny。"""
    assert policy.can_agent_access(_item(agent_sharing_policy="custom"), is_internal_caller=False) is False


def test_external_caller_missing_policy_key_falls_back_to_default_deny():
    item = _item()
    del item["agent_sharing_policy"]
    assert policy.can_agent_access(item, is_internal_caller=False) is False  # DB既定geopolitics_only相当


def test_filter_agent_accessible_knowledge_items_passes_through_non_knowledge_rows():
    """knowledge_item_idを持たない行（Claim等）は対象外でそのまま通す。"""
    claim_row = {"claim_id": "cl-1", "claim_text": "x"}
    items = [claim_row, _item(agent_sharing_policy="geopolitics_only")]
    result = policy.filter_agent_accessible_knowledge_items(items, is_internal_caller=False)
    assert claim_row in result
    assert len(result) == 1  # geopolitics_onlyの方は外部呼び出しなので除外される


def test_filter_agent_accessible_retrieval_dict_handles_internal_expert_knowledge_kind_split():
    retrieval = {
        "foundation": [_item(knowledge_item_id="ki-f", agent_sharing_policy="allowed_cross_domain")],
        "precedent": [_item(knowledge_item_id="ki-p", agent_sharing_policy="geopolitics_only")],
        "internal_expert_knowledge": [
            {"kind": "knowledge_item", **_item(knowledge_item_id="ki-ie", agent_sharing_policy="geopolitics_only")},
            {"kind": "claim", "claim_id": "cl-9"},
        ],
        "observed_fact": [{"claim_id": "cl-1"}],  # Claim系はvisibility対象外でそのまま
    }
    filtered = policy.filter_agent_accessible_retrieval_dict(retrieval, is_internal_caller=False)
    assert [i["knowledge_item_id"] for i in filtered["foundation"]] == ["ki-f"]
    assert filtered["precedent"] == []  # geopolitics_onlyは外部からdeny
    assert len(filtered["internal_expert_knowledge"]) == 1
    assert filtered["internal_expert_knowledge"][0]["kind"] == "claim"
    assert filtered["observed_fact"] == [{"claim_id": "cl-1"}]  # 変更されない


# ---- Human Visibility（§9） --------------------------------------------------

def test_company_visibility_allowed_for_any_context():
    assert policy.can_human_access(_item(human_visibility="company")) is True


def test_private_visibility_only_owner():
    item = _item(human_visibility="private", created_by="owner-1")
    assert policy.can_human_access(item, requester_context=policy.build_requester_context(user_id="owner-1")) is True
    assert policy.can_human_access(item, requester_context=policy.build_requester_context(user_id="someone-else")) is False
    assert policy.can_human_access(item) is False  # anonymous


def test_geopolitics_team_and_department_require_authentication():
    item = _item(human_visibility="geopolitics_team")
    assert policy.can_human_access(item) is False  # anonymous → deny
    assert policy.can_human_access(item, requester_context=policy.build_requester_context(user_id="u1")) is True


def test_custom_visibility_denies_by_default():
    item = _item(human_visibility="custom")
    assert policy.can_human_access(item, requester_context=policy.build_requester_context(user_id="u1")) is False


def test_unknown_requester_never_gets_restricted_items():
    """§11: unknownをallow扱いにしない。匿名（anonymous）はcompanyのみ許可。"""
    for visibility in ("private", "project", "geopolitics_team", "department", "custom"):
        assert policy.can_human_access(_item(human_visibility=visibility)) is False
    assert policy.can_human_access(_item(human_visibility="company")) is True


def test_log_access_filter_summary_only_logs_when_denied(client=None):
    client = FakeSupabaseClient()
    policy.log_access_filter_summary(client, actor="a", action="test_filter", total_before=5, total_after=5)
    assert client.tables.get("geo_audit_logs", []) == []  # 拒否0件なら記録しない（§22のコスト配慮）
    policy.log_access_filter_summary(client, actor="a", action="test_filter", total_before=5, total_after=3)
    logs = client.tables.get("geo_audit_logs", [])
    assert len(logs) == 1
    assert logs[0]["detail"]["denied_count"] == 2
