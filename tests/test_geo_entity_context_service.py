"""エンティティ詳細の「中身」（geo_entity_context_service）のテスト。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_entity_context_service as svc  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

TRUMP = "ent-trump"


def _item(i, title, *, status="active", retrieval="available", visibility="company", entity=None, ktype="actor_profile"):
    return {"knowledge_item_id": f"k{i}", "knowledge_type": ktype, "title": title, "status": status,
            "retrieval_status": retrieval, "human_visibility": visibility, "is_demo": False,
            "structured_content": {"entity_id": entity} if entity else {}, "updated_at": f"2026-10-0{i}T00:00:00+00:00"}


def _client():
    return FakeSupabaseClient(tables={
        "geo_entity_issues": [{"entity_id": TRUMP, "issue_id": "i1"}, {"entity_id": "other", "issue_id": "i9"}],
        "geo_issues": [{"issue_id": "i1", "code": "tariff", "name_ja": "米国の関税政策", "name_en": "US tariffs",
                        "description": "関税", "status": "active"},
                       {"issue_id": "i9", "code": "x", "name_ja": "無関係", "status": "active"}],
        "geo_knowledge_items": [
            _item(1, "Donald Trump", entity=TRUMP),                                         # 承認済み・直接
            _item(2, "Trump候補A", status="candidate", retrieval="restricted", entity=TRUMP),  # 承認前
            _item(3, "Trump候補B", status="in_review", retrieval="restricted", entity=TRUMP),  # 承認前
            _item(4, "Trump非公開", visibility="private", entity=TRUMP),                      # 閲覧権限なし
            _item(5, "関税の先例", ktype="precedent"),                                        # 同じ論点（間接）
            _item(6, "他人のプロファイル", entity="other")],
        "geo_knowledge_item_issues": [{"knowledge_item_id": "k5", "issue_id": "i1"},
                                      {"knowledge_item_id": "k1", "issue_id": "i1"}]})


def test_context_returns_issues_and_approved_own_knowledge_with_content():
    out = svc.get_entity_context(_client(), TRUMP)
    assert [i["name_ja"] for i in out["issues"]] == ["米国の関税政策"]          # 他エンティティの論点は出ない
    assert [k["title"] for k in out["knowledge_items"]] == ["Donald Trump"]
    assert out["knowledge_items"][0]["structured_content"]["entity_id"] == TRUMP   # 内容が返る


def test_unapproved_items_are_only_counted_never_shown():
    out = svc.get_entity_context(_client(), TRUMP)
    shown = [k["title"] for k in out["knowledge_items"] + out["issue_knowledge_items"]]
    assert "Trump候補A" not in shown and "Trump候補B" not in shown
    assert out["pending_count"] == 2                                                   # 件数だけ


def test_private_items_are_hidden_for_anonymous_requester():
    out = svc.get_entity_context(_client(), TRUMP)
    assert "Trump非公開" not in [k["title"] for k in out["knowledge_items"]]


def test_issue_knowledge_excludes_the_entitys_own_items():
    out = svc.get_entity_context(_client(), TRUMP)
    assert [k["title"] for k in out["issue_knowledge_items"]] == ["関税の先例"]   # 直接の項目(k1)は重複させない


def test_entity_without_links_returns_empty_lists():
    out = svc.get_entity_context(_client(), "nobody")
    assert out == {"issues": [], "knowledge_items": [], "issue_knowledge_items": [], "pending_count": 0}
