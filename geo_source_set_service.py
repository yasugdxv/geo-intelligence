"""Source Set: 複数Sourceの束。Theme Explorer / Monitoring / Research Studioで共有する共通オブジェクト。

Source SetはTheme専用にしない（Themeとの接続はMonitoring等のContext側が持つ）。
Sourceがarchived（soft delete）されてもmember行は残す（参照を壊さない）が、解決時
（resolve_set_sources）にarchivedかどうかを明示して返す。

Source Set自体の削除も論理削除（archived_at。migration DB-6）。物理DELETEはしない:
- 通常一覧はarchived_at IS NULLのみ。ID指定のDetailはarchived済みも取得できる。
- archiveしてもSource・member行は変更しない。
- 有効なMonitoringが参照中の場合は、既定ではarchiveを拒否する（SourceSetInUse）。
  force=Trueでarchiveした場合も、Monitoringは従来どおりmemberを読み続けて動作する
  （Source Set側の変更はMonitoringの監視対象を変えない）。
- archived済みSource Setを新規にMonitoringへ紐付けることはできない。
"""
from __future__ import annotations

from datetime import datetime, timezone

import geo_source_library_service as lib


class SourceSetInUse(Exception):
    """有効なMonitoringが参照中のためarchiveできない。"""

    def __init__(self, themes: list):
        super().__init__("有効なMonitoringが参照中のためarchiveできません")
        self.themes = themes


def _in_list(values) -> str:
    return "in.(" + ",".join(str(v) for v in values) + ")"


def list_source_sets(client, *, include_archived: bool = False, limit: int = 100) -> list:
    params = {"select": "*", "order": "updated_at.desc", "limit": str(limit)}
    if not include_archived:
        params["archived_at"] = "is.null"
    try:
        sets = client.select("geo_source_sets", params)
    except Exception as exc:  # noqa: BLE001
        if not lib._is_missing_column_error(exc) or include_archived:
            raise
        # migration DB-6未適用（archived_at列なし）: 絞り込まずに返す（読み取りのみ）
        params.pop("archived_at")
        sets = client.select("geo_source_sets", params)
    if not sets:
        return []
    members = client.select("geo_source_set_members", {
        "select": "source_set_id", "source_set_id": _in_list([s["source_set_id"] for s in sets]),
        "limit": "5000"})
    counts: dict = {}
    for m in members:
        counts[m["source_set_id"]] = counts.get(m["source_set_id"], 0) + 1
    for s in sets:
        s["member_count"] = counts.get(s["source_set_id"], 0)
    return sets


def get_source_set(client, source_set_id: str) -> dict | None:
    rows = client.select("geo_source_sets", {"select": "*", "source_set_id": f"eq.{source_set_id}"})
    if not rows:
        return None
    s = rows[0]
    s["sources"] = resolve_set_sources(client, source_set_id)
    s["member_count"] = len(s["sources"])
    return s


def resolve_set_sources(client, source_set_id: str) -> list:
    """Source Setのmember（Source情報つき、sort_order順）。archived Sourceも
    archived_atつきで返し、利用側（Monitoring等）が判断できるようにする。"""
    members = client.select("geo_source_set_members", {
        "select": "*", "source_set_id": f"eq.{source_set_id}", "order": "sort_order.asc", "limit": "1000"})
    if not members:
        return []
    sources = {r["source_id"]: r for r in client.select("geo_source_registry", {
        "select": "source_id,name,url,media_type,region,is_primary,source_tier,source_type,archived_at",
        "source_id": _in_list([m["source_id"] for m in members])})}
    out = []
    for m in members:
        src = sources.get(m["source_id"])
        if src:
            out.append({**src, "sort_order": m.get("sort_order", 0)})
    return out


def create_source_set(client, *, name: str, description: str | None = None,
                      source_ids: list | None = None, created_by: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise lib.SourceValidationError("Source Setのnameは必須です")
    inserted = client.insert("geo_source_sets", [{
        "name": name, "description": (description or None), "created_by": created_by}])
    row = inserted[0]
    if source_ids:
        set_members(client, row["source_set_id"], source_ids, added_by=created_by)
    return get_source_set(client, row["source_set_id"]) or row


def update_source_set(client, source_set_id: str, *, name: str | None = None,
                      description: str | None = None) -> dict | None:
    patch = {}
    if name is not None:
        if not name.strip():
            raise lib.SourceValidationError("Source Setのnameは空にできません")
        patch["name"] = name.strip()
    if description is not None:
        patch["description"] = description.strip() or None
    if patch:
        client.update("geo_source_sets", {"source_set_id": f"eq.{source_set_id}"}, patch)
    return get_source_set(client, source_set_id)


def set_members(client, source_set_id: str, source_ids: list, *, added_by: str | None = None) -> list:
    """memberをsource_idsの内容（順序つき）へ置き換える。存在しないsource_idは無視する
    （推測で作らない）。"""
    ordered, seen = [], set()
    for sid in source_ids:
        if sid and sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    valid = {r["source_id"] for r in client.select("geo_source_registry", {
        "select": "source_id", "source_id": _in_list(ordered)})} if ordered else set()
    client.delete("geo_source_set_members", {"source_set_id": f"eq.{source_set_id}"})
    rows = [{"source_set_id": source_set_id, "source_id": sid, "sort_order": i, "added_by": added_by}
            for i, sid in enumerate(s for s in ordered if s in valid)]
    if rows:
        client.insert("geo_source_set_members", rows, prefer="return=minimal")
    return resolve_set_sources(client, source_set_id)


def add_member(client, source_set_id: str, source_id: str, *, added_by: str | None = None) -> list:
    current = [s["source_id"] for s in resolve_set_sources(client, source_set_id)]
    if source_id not in current:
        current.append(source_id)
    return set_members(client, source_set_id, current, added_by=added_by)


def monitoring_themes_using(client, source_set_id: str) -> list:
    """このSource Setを参照中の有効なMonitoring（archived以外）。"""
    try:
        return client.select("geo_monitoring_themes", {
            "select": "monitoring_theme_id,display_name,status", "source_set_id": f"eq.{source_set_id}",
            "status": "neq.archived", "limit": "100"})
    except Exception as exc:  # noqa: BLE001
        if lib._is_missing_column_error(exc):
            return []
        raise


def archive_source_set(client, source_set_id: str, *, force: bool = False) -> dict | None:
    """論理削除。戻り値: {"source_set": 行, "in_use_by": [Monitoring]}（存在しなければNone）。
    有効なMonitoringが参照中でforce=Falseの場合は SourceSetInUse を送出する（何も変更しない）。"""
    rows = client.select("geo_source_sets", {"select": "*", "source_set_id": f"eq.{source_set_id}"})
    if not rows:
        return None
    in_use = monitoring_themes_using(client, source_set_id)
    if in_use and not force:
        raise SourceSetInUse(in_use)
    if not rows[0].get("archived_at"):
        client.update("geo_source_sets", {"source_set_id": f"eq.{source_set_id}"},
                      {"archived_at": datetime.now(timezone.utc).isoformat()})
    return {"source_set": get_source_set(client, source_set_id), "in_use_by": in_use}


def restore_source_set(client, source_set_id: str) -> dict | None:
    if not client.select("geo_source_sets", {"select": "source_set_id", "source_set_id": f"eq.{source_set_id}"}):
        return None
    client.update("geo_source_sets", {"source_set_id": f"eq.{source_set_id}"}, {"archived_at": None})
    return get_source_set(client, source_set_id)


def ensure_usable(client, source_set_id: str | None) -> None:
    """Monitoring等へ新規に紐付けるSource Setが存在し、archivedでないことを確認する。"""
    if not source_set_id:
        return
    try:
        rows = client.select("geo_source_sets", {"select": "source_set_id,archived_at",
                                                 "source_set_id": f"eq.{source_set_id}"})
    except Exception as exc:  # noqa: BLE001
        if lib._is_missing_column_error(exc):   # DB-6未適用: archived判定ができないので通す
            return
        raise
    if not rows:
        raise lib.SourceValidationError("指定されたSource Setが見つかりません")
    if rows[0].get("archived_at"):
        raise lib.SourceValidationError("アーカイブ済みのSource Setは新しく紐付けられません")
