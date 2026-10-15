"""エンティティ詳細の「中身」: 紐づく論点・関連するナレッジ項目をまとめて返す（読み取りのみ）。

ナレッジ検索のエンティティ詳細は、これまでエンティティ自身の項目（名称・状態など）しか出さなかった。
実際の中身（アクターのプロファイル等）は、ナレッジ項目（geo_knowledge_items）に入っており、
エンティティとは structured_content.entity_id と、論点（geo_entity_issues / geo_knowledge_item_issues）で
つながっている。本モジュールはそのつながりを集める。

守っていること:
  - ナレッジ項目の内容を返すのは、通常の参照と同じ条件（承認済み・参照可能・human_visibility）を
    満たすものだけ。承認前（候補・レビュー中）の項目は、内容を返さず、件数だけ返す
    （承認の手順を迂回しない）。
  - DBへは何も書かない。
"""
from __future__ import annotations

import geo_knowledge_access_policy as access

_PENDING_STATUSES = ("candidate", "in_review")


def _accessible(items: list, ctx: dict | None) -> list:
    return [i for i in items if access.is_retrievable(i) and access.can_human_access(i, requester_context=ctx)]


def get_entity_context(client, entity_id: str, *, requester_context: dict | None = None) -> dict:
    """戻り値: {"issues": [...], "knowledge_items": [...], "issue_knowledge_items": [...],
    "pending_count": int}。エンティティが無ければ呼び出し側が404にする。"""
    # 論点（このエンティティが関わる論点）
    links = client.select("geo_entity_issues", {"select": "issue_id", "entity_id": f"eq.{entity_id}", "limit": "100"})
    issue_ids = [l["issue_id"] for l in links]
    issues: list = []
    if issue_ids:
        issues = client.select("geo_issues", {
            "select": "issue_id,code,name_ja,name_en,description,status",
            "issue_id": "in.(" + ",".join(issue_ids) + ")", "order": "name_ja.asc"})

    # このエンティティ自身のナレッジ項目（structured_content.entity_id が一致するもの）
    # JSON項目での絞り込みはDB側に任せず、取得してからコード側で絞る（件数は百数十件で、負担は小さい）
    everything = client.select("geo_knowledge_items", {
        "select": "*", "is_demo": "eq.false", "order": "updated_at.desc", "limit": "1000"})
    direct = [i for i in everything if isinstance(i.get("structured_content"), dict)
              and i["structured_content"].get("entity_id") == entity_id]
    shown = _accessible(direct, requester_context)
    pending = [i for i in direct if i.get("status") in _PENDING_STATUSES]

    # 同じ論点に紐づく、承認済みのナレッジ項目（他のアクター・先例など。直接の項目は除く）
    related: list = []
    if issue_ids:
        ki = client.select("geo_knowledge_item_issues", {
            "select": "knowledge_item_id", "issue_id": "in.(" + ",".join(issue_ids) + ")", "limit": "300"})
        ids = sorted({r["knowledge_item_id"] for r in ki} - {i["knowledge_item_id"] for i in direct})
        if ids:
            related = _accessible(client.select("geo_knowledge_items", {
                "select": "*", "knowledge_item_id": "in.(" + ",".join(ids) + ")", "is_demo": "eq.false",
                "order": "updated_at.desc", "limit": "30"}), requester_context)
    return {"issues": issues, "knowledge_items": shown, "issue_knowledge_items": related,
            "pending_count": len(pending)}
