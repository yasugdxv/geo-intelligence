"""Phase 2E: Report / Knowledge Confidentiality & Retrieval Access Control。

§12 Cross-department AI Test。まずPolicy Matrixを定義し、その後テストする。

対象Report:
    A = DEPARTMENT_ONLY（owner_department="geopolitics"）
    B = SHD_ONLY（SHD_ALLOWED_ORG_UNITS/DOMAINSは2026-09-28時点で空リストのまま
        ——ユーザー判断により推測実装しない。空である間は「所有部門のみ」に縮退する）
    C = GROUP_SHAREABLE（human_visibility=company、agent軸はallowed_domains未設定のため
        Agentからは依然deny）

Caller x Report x Operation の期待結果（○=許可, ×=拒否）:

| Caller                                          | A (DEPT_ONLY) | B (SHD_ONLY, 未設定) | C (GROUP_SHAREABLE) |
|--------------------------------------------------|:---:|:---:|:---:|
| 1. 同じ部門のHuman（department=geopolitics）        |  ○  |  ×  |  ○  |
| 2. SHD内の別部門Human（department=sustainability）  |  ×  |  ×  |  ○  |
| 3. 許可グループ内だがSHD外のHuman                    |  ×  |  ×  |  ○  |
| 4. 同domainのAgent（internal, is_internal=True）    |  ○  |  ○  |  ○  |
| 5. 別domainのAgent（例: sustainability, custom未設定）|  ×  |  ×  |  ×  |
| 6. Unknown/malformed caller（identity不明）          |  ×  |  ×  |  ○（company=誰でも可視、
                                                                  Human軸の既存仕様通り）|

注: Row2/3はSHD_ONLYが実際の組織範囲を持つまで区別できない（未設定=空リスト=fail-closed）。
    設定後は「SHD内メンバー」のみ許可へ変わる想定（allowed_org_unitsへ実際の部門名を追加）。
    Row6の"Unknown"はcaller_identity自体が無い/認証情報が無いケース——Human軸はcompanyの
    項目だけ元々「認証すら不要で全員に見える」設計のため○になる（human_visibility=company
    の既存挙動、Phase 2Eで変更していない）。Agent軸のUnknown（is_internal_caller不明）は
    §13の通り常にdenyへ倒す（is_internal_caller=Falseかつcaller_domain=Noneとして扱う）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_knowledge_access_policy as policy  # noqa: E402


def _report(label: str, **overrides) -> dict:
    resolved = policy.resolve_ui_label_to_policy(label, owner_department="geopolitics")
    row = {"report_id": f"report-{label}", "uploaded_by": "owner-1", **resolved}
    row.update(overrides)
    return row


REPORT_A = _report("DEPARTMENT_ONLY")
REPORT_B = _report("SHD_ONLY")
REPORT_C = _report("GROUP_SHAREABLE")


def _human_ctx(department: str = None, user_id: str = None) -> dict:
    return policy.build_requester_context(user_id=user_id, department=department, is_authenticated=bool(user_id))


# ---- Row 1: 同じ部門のHuman ------------------------------------------------

def test_same_department_human_matrix():
    ctx = _human_ctx(department="geopolitics", user_id="analyst-geo")
    assert policy.can_access_report(REPORT_A, operation="read", requester_context=ctx) is True
    assert policy.can_access_report(REPORT_B, operation="read", requester_context=ctx) is False
    assert policy.can_access_report(REPORT_C, operation="read", requester_context=ctx) is True


# ---- Row 2: SHD内（と想定される）別部門のHuman ------------------------------

def test_different_department_human_matrix():
    ctx = _human_ctx(department="sustainability", user_id="analyst-sus")
    assert policy.can_access_report(REPORT_A, operation="read", requester_context=ctx) is False
    assert policy.can_access_report(REPORT_B, operation="read", requester_context=ctx) is False
    assert policy.can_access_report(REPORT_C, operation="read", requester_context=ctx) is True


# ---- Row 3: 許可グループ内だがSHD外のHuman（別部門と同じ扱いになる） --------

def test_outside_shd_but_allowed_group_human_matrix():
    ctx = _human_ctx(department="erm", user_id="analyst-erm")
    assert policy.can_access_report(REPORT_A, operation="read", requester_context=ctx) is False
    assert policy.can_access_report(REPORT_B, operation="read", requester_context=ctx) is False
    assert policy.can_access_report(REPORT_C, operation="read", requester_context=ctx) is True


# ---- Row 4: 同domainのAgent（地政学アプリ自身の内部利用） -------------------

def test_internal_agent_matrix():
    for report in (REPORT_A, REPORT_B, REPORT_C):
        assert policy.can_access_report(report, operation="retrieve_for_ai", is_internal_caller=True) is True


# ---- Row 5: 別domainのAgent（例: Sustainability Intelligence AI） -----------

def test_cross_domain_agent_denied_when_not_explicitly_shared():
    for report in (REPORT_A, REPORT_B, REPORT_C):
        assert policy.can_access_report(
            report, operation="retrieve_for_ai", is_internal_caller=False,
            caller_domain="sustainability") is False


def test_cross_domain_agent_allowed_when_explicitly_shared_with_matching_domain():
    shared = _report("SHD_ONLY", agent_sharing_policy="custom", allowed_domains=["sustainability"])
    assert policy.can_access_report(
        shared, operation="retrieve_for_ai", is_internal_caller=False,
        caller_domain="sustainability") is True
    assert policy.can_access_report(
        shared, operation="retrieve_for_ai", is_internal_caller=False,
        caller_domain="erm") is False


# ---- Row 6: Unknown/malformed caller ---------------------------------------

def test_unknown_human_caller_matrix():
    """認証情報が一切無い（匿名）Human——human_visibility=companyの項目だけは元々
    「認証すら不要」という既存仕様のため見える。custom（DEPARTMENT_ONLY/SHD_ONLY）は
    作成者本人以外は常にdeny。"""
    assert policy.can_access_report(REPORT_A, operation="read", requester_context=None) is False
    assert policy.can_access_report(REPORT_B, operation="read", requester_context=None) is False
    assert policy.can_access_report(REPORT_C, operation="read", requester_context=None) is True


def test_unknown_agent_caller_matrix():
    """caller_domainが一切解決できないAgent（is_internal_caller=False, caller_domain=None）
    は、custom共有先が設定されていても常にdeny（§13 fail-closed、malformed callerを許可
    しない）。"""
    shared = _report("SHD_ONLY", agent_sharing_policy="custom", allowed_domains=["sustainability"])
    assert policy.can_access_report(
        shared, operation="retrieve_for_ai", is_internal_caller=False, caller_domain=None) is False


# ---- §7 Report → Knowledge inheritance（Matrix横断の確認） ------------------

def test_report_owner_always_has_access_regardless_of_label():
    """作成者本人（uploaded_by一致）は、custom visibility/sharingの設定に関わらず常に
    自分のReportを閲覧できる（fail-closedであっても登録者自身が締め出されない）。"""
    ctx = _human_ctx(user_id="owner-1")
    for report in (REPORT_A, REPORT_B):
        assert policy.can_access_report(report, operation="read", requester_context=ctx) is True
