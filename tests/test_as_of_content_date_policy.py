"""評価ハーネス AsOfEvaluationClient の policy_mode="content_date"（2026-10-15追加）のテスト。

- 既定の"strict"は従来どおり（保存日も基準日以前を要求）で、挙動が変わらないこと
- "content_date"は、記事の公開日・出来事の発生日・先例の内容の時期だけで絞り、保存日は問わないこと
- 内容の日付が分からないものは使わない（fail-closed）、年だけ分かる場合は12/31（遅い側）として扱うこと
- 書き込みは従来どおりブロックされること
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval_harness import as_of_evaluation_client as harness  # noqa: E402


class _FakeClient:
    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table

    def select(self, table, params):
        return list(self.rows_by_table.get(table, []))


DOCS = [
    {"document_id": "old_saved_late", "published_at": "2025-06-01", "created_at": "2026-08-10"},   # 内容は過去・保存は後
    {"document_id": "old_saved_early", "published_at": "2025-06-01", "created_at": "2025-07-01"},
    {"document_id": "future_content", "published_at": "2026-03-01", "created_at": "2026-03-02"},
    {"document_id": "undated", "published_at": None, "created_at": "2025-07-01"},
]


def _client(mode, rows, as_of="2026-02-04"):
    return harness.AsOfEvaluationClient(_FakeClient(rows), as_of, policy_mode=mode)


def test_strict_mode_is_unchanged_and_requires_ingestion_date_too():
    kept = _client("strict", {"geo_documents": DOCS}).select("geo_documents", {})
    assert [d["document_id"] for d in kept] == ["old_saved_early"]  # 保存日が後のものは落ちる（従来どおり）


def test_content_date_mode_uses_publication_date_only():
    c = _client("content_date", {"geo_documents": DOCS})
    kept = c.select("geo_documents", {})
    assert sorted(d["document_id"] for d in kept) == ["old_saved_early", "old_saved_late"]
    report = c.audit_report()
    assert report["policy_mode"] == "content_date"
    assert report["tables"]["geo_documents"] == {
        "policy": "_policy_geo_documents_content", "included_count": 2,
        "excluded_future_count": 1, "excluded_unverified_count": 1}


def test_default_mode_is_strict_and_unknown_mode_is_rejected():
    assert harness.AsOfEvaluationClient(_FakeClient({}), "2026-02-04").policy_mode == "strict"
    with pytest.raises(ValueError):
        harness.AsOfEvaluationClient(_FakeClient({}), "2026-02-04", policy_mode="lenient")


def test_events_and_relationships_ignore_registration_date_in_content_mode():
    events = [{"start_at": "2025-01-01", "created_at": "2026-09-01"}, {"start_at": "2026-05-01", "created_at": "2025-01-01"}]
    rels = [
        {"relationship_id": "a", "valid_from": "1960-06-23", "valid_to": None, "created_at": "2026-10-15"},
        {"relationship_id": "ended", "valid_from": "1973-01-01", "valid_to": "2020-01-31", "created_at": "2026-10-15"},
        {"relationship_id": "later", "valid_from": "2025-09-11", "valid_to": None, "created_at": "2026-10-15"},
        {"relationship_id": "nodate", "valid_from": None, "valid_to": None, "created_at": "2025-01-01"},
    ]
    c = _client("content_date", {"geo_events": events, "geo_relationships": rels}, as_of="2025-06-01")
    assert len(c.select("geo_events", {})) == 1
    assert [r["relationship_id"] for r in c.select("geo_relationships", {})] == ["a"]
    strict = _client("strict", {"geo_relationships": rels}, as_of="2025-06-01")
    # strictは登録日(created_at)で絞る（従来どおり）: 登録日が基準日より後の3件は落ち、登録日が早い1件だけ残る
    assert [r["relationship_id"] for r in strict.select("geo_relationships", {})] == ["nodate"]


def test_knowledge_content_date_uses_precedent_period_and_fails_closed_otherwise():
    precedent = {"knowledge_type": "precedent", "title": "米朝首脳会談(2018)の融和とハノイ(2019)の決裂",
                 "structured_content": {"period": "2018-06〜2019-02", "outcome": "決裂"}}
    assert harness.knowledge_content_date(precedent) == date(2019, 12, 31)  # 年だけ→その年の12/31（遅い側）
    assert harness.knowledge_content_date({"knowledge_type": "actor_profile", "title": "Xi Jinping"}) is None
    assert harness.knowledge_content_date({"knowledge_type": "precedent", "title": "年の無い先例", "structured_content": {}}) is None
    future_only = {"knowledge_type": "precedent", "title": "x", "structured_content": {"period": "2099年"}}
    assert harness.knowledge_content_date(future_only) is None  # 未来の年は予測とみなして内容の日付に使わない


def test_knowledge_policy_admits_only_precedents_dated_before_as_of():
    rows = [
        {"knowledge_item_id": "p2018", "knowledge_type": "precedent", "title": "2018年憲法改正", "structured_content": {"period": "2018-03"}, "created_at": "2026-10-01"},
        {"knowledge_item_id": "p2025", "knowledge_type": "precedent", "title": "2025年2月の衝突", "structured_content": {"period": "2025-02"}, "created_at": "2026-10-01"},
        {"knowledge_item_id": "actor", "knowledge_type": "actor_profile", "title": "Xi Jinping", "created_at": "2025-01-01"},
    ]
    c = _client("content_date", {"geo_knowledge_items": rows}, as_of="2024-12-31")
    assert [r["knowledge_item_id"] for r in c.select("geo_knowledge_items", {})] == ["p2018"]
    rep = c.audit_report()["tables"]["geo_knowledge_items"]
    assert (rep["included_count"], rep["excluded_future_count"], rep["excluded_unverified_count"]) == (1, 1, 1)


def test_writes_remain_blocked_in_content_mode():
    c = _client("content_date", {})
    with pytest.raises(harness.WriteBlockedError):
        c.insert("geo_documents", [{}])
