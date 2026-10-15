"""Media Intelligence Build（M8.5）: Media Intelligence Packの構築。

geo_actor_intelligence_service.build_actor_intelligence_packと対称的な構造だが、
現時点ではCase単位のSource選定テーブル（geo_case_actorsに相当するもの）がまだ存在しない
ため、Retrievalは「DB上のmedia_pattern/media_profileを全件走査し、char budget内へ
trim する」という単純な方式に留める（依頼書M9で本格的なCase-scoped Retrievalへ拡張する
際の設計論点として、07_gate_m5_m8_stop_report.md item16に明記済み）。

Human Review未了のCandidateを本番のExpert Engine呼び出し（run_expert_request、
DB保存あり）へ混入させないよう、既定は`statuses=("active",)`のみ。テスト・比較実験
（run_expert_request_staged、DB非保存）でのみ、呼び出し元が明示的に
`statuses=("active","candidate")`を渡すことを許容する（Actor Intelligence Phase AI-3の
Golden Case A/B/C比較と同じ考え方）。
"""
import json

import geo_knowledge_service

# 2026-09-02: 8 pattern分だけで実測約7,500字となり、既定の6000ではProfileがほぼ全て
# 切り捨てられていた（docs/expert-ai-validation/m10a3_case3_real_evidence/
# report.md参照）。Profileも複数件残せるよう12000へ引き上げる。
MEDIA_PACK_CHAR_BUDGET = 12000
MEDIA_PACK_PATTERN_TOP_K = 8
MEDIA_PACK_PROFILE_TOP_K = 8


def _list_media_items(client, knowledge_type: str, *, statuses=("active",), limit: int = 200) -> list:
    items = []
    for status in statuses:
        items += geo_knowledge_service.list_knowledge_items(
            client, knowledge_type=knowledge_type, status=status, include_demo=True, limit=limit)
    return items


def _trim_to_char_budget(patterns: list, profiles: list, char_budget: int) -> tuple:
    def _size() -> int:
        return len(json.dumps({"media_patterns": patterns, "media_profiles": profiles}, ensure_ascii=False))

    patterns = list(patterns)
    profiles = list(profiles)
    while _size() > char_budget and (patterns or profiles):
        if profiles:
            profiles.pop()
        elif patterns:
            patterns.pop()
    return patterns, profiles


def build_media_intelligence_pack(client, *, statuses: tuple = ("active",),
                                   char_budget: int = MEDIA_PACK_CHAR_BUDGET) -> dict:
    """Media Pattern Candidate / Media Profileから、Stage1/2 Promptへ注入するための
    Media Intelligence Packを組み立てる。Raw Episode本文は含めずID+titleのみ参照する
    （Actor IntelligenceのrelevantPrecedentsと同じtoken対策）。"""
    pattern_items = _list_media_items(client, "media_pattern", statuses=statuses)
    profile_items = _list_media_items(client, "media_profile", statuses=statuses)

    known_ids = set()
    patterns = []
    for item in pattern_items[:MEDIA_PACK_PATTERN_TOP_K]:
        content = item.get("structured_content") or {}
        known_ids.add(item.get("knowledge_item_id"))
        known_ids.update(content.get("evidence_episode_ids") or [])
        known_ids.update(content.get("exception_episode_ids") or [])
        patterns.append({
            "knowledge_item_id": item.get("knowledge_item_id"),
            "pattern": content.get("pattern"),
            "when_it_applies": content.get("when_it_applies"),
            "typical_source_pattern": content.get("typical_source_pattern"),
            "typical_framing_pattern": content.get("typical_framing_pattern"),
            "pattern_role": content.get("pattern_role"),
            "exception_conditions": content.get("exception_conditions"),
            "confidence": content.get("confidence"),
            "evidence_episode_count": len(content.get("evidence_episode_ids") or []),
        })

    profiles = []
    for item in profile_items[:MEDIA_PACK_PROFILE_TOP_K]:
        content = item.get("structured_content") or {}
        known_ids.add(item.get("knowledge_item_id"))
        profiles.append({
            "knowledge_item_id": item.get("knowledge_item_id"),
            "source_id": content.get("source_id"),
            "source_name": content.get("source_name"),
            "institutional_perspective": content.get("institutional_perspective"),
            "historical_framing_characteristics": content.get("historical_framing_characteristics"),
            "use_for": content.get("use_for"),
            "do_not_use_for": content.get("do_not_use_for"),
            "known_limitations": content.get("known_limitations"),
            "confidence": content.get("confidence"),
        })

    patterns, profiles = _trim_to_char_budget(patterns, profiles, char_budget)
    known_ids.discard(None)

    return {
        "media_patterns": patterns,
        "media_profiles": profiles,
        "statuses_included": list(statuses),
        "known_ids": known_ids,
    }
