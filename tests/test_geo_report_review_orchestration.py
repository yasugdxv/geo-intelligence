"""地政学アプリClooseout Phase5: Report VersioningとReport Review Loopの接続の検証。

Report v1 → Expert Review → v2 → Diff → Knowledge Candidate → Human Promote → geo_knowledge_items
→ 次回分析で再利用、というEnd-to-Endフローのオーケストレーション部分（Service層）を検証する。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_service as report_service  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402

THEME_ID = "theme-1"


def _base_tables() -> dict:
    return {
        "geo_themes": [{"theme_id": THEME_ID, "code": "test_theme", "name_ja": "テストテーマ"}],
        "geo_entities": [{"entity_id": "entity-iran", "canonical_name": "Iran", "entity_type": "country",
                          "is_demo": False}],
        "geo_entity_issues": [{"entity_id": "entity-iran", "issue_id": "issue-1"}],
        "geo_issues": [{"issue_id": "issue-1", "theme_id": THEME_ID, "code": "test-issue"}],
    }


def _mock_llm_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(response)))],
    )
    return client


CHANGE_RESPONSE = {"changes": [{
    "change_type": "main_hypothesis_change", "reusability_class": "case_specific_assessment",
    "reasoning_summary": "レビューにより見立てが更新された", "confidence": "中",
    "draft_excerpt": "旧見立て", "final_excerpt": "新しい見立て", "grounding_note_ids": [],
}]}


def _ingest_v1(client, tmp_path, monkeypatch, *, text="v1本文（旧見立て）"):
    monkeypatch.setattr(report_service.geo_documents_store, "UPLOAD_ROOT", tmp_path)
    return report_service.ingest_report(
        client, filename="v1.txt", mime_type="text/plain", raw_bytes=text.encode("utf-8"),
        country_region=["Iran"], actor_id="analyst_taro", author="analyst_taro",
    )


def test_start_report_review_and_version_creates_v2_session_and_changes(tmp_path, monkeypatch):
    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch)["report"]
    azure_client = _mock_llm_client(CHANGE_RESPONSE)

    result = report_service.start_report_review_and_version(
        client, azure_client, "gpt-4o", report_id=v1["report_id"],
        reviewed_filename="v2.txt", reviewed_mime_type="text/plain",
        reviewed_raw_bytes="v1本文（新しい見立て）".encode("utf-8"),
        reviewer_id="reviewer1", actor_id="analyst_taro",
    )

    v2 = result["version"]["report"]
    assert v2["version"] == 2
    assert v2["supersedes_id"] == v1["report_id"]
    # Sessionのreport_idはv1ではなくv2を指す
    assert result["review_session"]["report_id"] == v2["report_id"]
    assert result["review_session"]["case_id"] is None
    assert len(result["extraction"]["changes"]) == 1


def test_start_report_review_and_version_promote_reaches_knowledge_items(tmp_path, monkeypatch):
    """End-to-End（Service層）: v1 → Review → v2 → Diff → Candidate → Promote →
    geo_knowledge_itemsまで一気通貫で繋がることを確認する。"""
    import geo_expert_review_service as review_service

    client = FakeSupabaseClient(tables=_base_tables())
    v1 = _ingest_v1(client, tmp_path, monkeypatch)["report"]
    azure_client = _mock_llm_client(CHANGE_RESPONSE)

    result = report_service.start_report_review_and_version(
        client, azure_client, "gpt-4o", report_id=v1["report_id"],
        reviewed_filename="v2.txt", reviewed_mime_type="text/plain",
        reviewed_raw_bytes="v1本文（新しい見立て）".encode("utf-8"),
        reviewer_id="reviewer1", actor_id="analyst_taro",
    )
    change = result["extraction"]["changes"][0]

    promoted = review_service.promote_change(client, change_id=change["change_id"], reviewer_id="reviewer1")

    assert promoted["knowledge_item"]["knowledge_type"] == "report_finding"
    assert promoted["knowledge_item"]["status"] == "active"
    assert promoted["knowledge_item"]["structured_content"]["report_id"] == result["version"]["report"]["report_id"]

    # diff_report_versionsでv1/v2間の差分も確認できる
    diff = report_service.diff_report_versions(client, v1["report_id"], result["version"]["report"]["report_id"])
    assert diff["text_diff"]
