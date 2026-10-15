"""Geopolitical Intelligence OS Phase H §28-31: Issue Link Suggestion / CQAS Signal集計。

§29絶対条件: AI suggestion → automatic issue link は禁止。本モジュールは候補（スコア付き
issue一覧）を返すだけで、geo_knowledge_item_issuesへの実際のリンクは行わない——リンク自体は
Knowledge Stewardが既存geo_knowledge_service.link_to_issue（Phase G以前からの既存関数）を
明示的に呼んで初めて成立する。

CQAS Signal集計は既存geo_ios_learning_candidates.trigger_context.cqas_signal（Phase G）を
読むだけで、CQAS ranking algorithm自体には一切触れない（§30絶対条件）。
"""
import geo_ios_learning_candidate_service as learning_service
import geo_knowledge_gap_service  # text_similarity（既存Dedupロジック）を再利用

# 既存Dedup（geo_knowledge_gap_service）と同じ考え方: 一定以上の類似度のみ候補として見せる
# （無関係なIssueを大量に提示しない）。
SUGGESTION_MIN_SIMILARITY = 0.1


def _candidate_search_text(candidate: dict) -> str:
    tc = candidate.get("trigger_context") or {}
    return " ".join(filter(None, [
        candidate.get("generalized_rule"), candidate.get("observation"),
        tc.get("title"), tc.get("trigger"),
    ]))


def suggest_related_issues(client, candidate_id: str, *, limit: int = 5) -> list:
    """§28: Candidate内容から既存geo_issuesを検索し、スコア付きで返す（自動リンクしない）。"""
    candidate = learning_service.get_candidate(client, candidate_id)
    if not candidate:
        raise learning_service.LearningCandidateError(f"candidate_idが見つかりません: {candidate_id}")

    search_text = _candidate_search_text(candidate)
    issues = client.select("geo_issues", {"select": "*", "status": "eq.有効", "limit": "500"})

    scored = []
    for issue in issues:
        issue_text = " ".join(filter(None, [issue.get("name_ja"), issue.get("name_en"), issue.get("description")]))
        score = geo_knowledge_gap_service.text_similarity(search_text, issue_text)
        if score >= SUGGESTION_MIN_SIMILARITY:
            scored.append({
                "issue_id": issue["issue_id"], "name_ja": issue.get("name_ja"),
                "name_en": issue.get("name_en"), "score": round(score, 3),
            })
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:limit]


# ---- §30-31 CQAS Signal集計 ---------------------------------------------------

CQAS_SIGNAL_TYPES = ("generation_miss", "ranking_miss", "selection_miss", "validated_candidate")


def summarize_cqas_signals(client, *, limit: int = 500) -> dict:
    """§30: Report単位/期間集計向けの最小限の集計。ranking algorithm自体は変更しない、
    記録済みSignalの集計のみ。"""
    candidates = learning_service.list_candidates(client, limit=limit)
    counts = {signal: 0 for signal in CQAS_SIGNAL_TYPES}
    drilldown = []
    for candidate in candidates:
        cqas_signal = (candidate.get("trigger_context") or {}).get("cqas_signal")
        if not cqas_signal:
            continue
        signal = cqas_signal.get("signal")
        if signal in counts:
            counts[signal] += 1
        drilldown.append({
            "candidate_id": candidate["candidate_id"], "candidate_type": candidate["candidate_type"],
            "generalized_rule": candidate["generalized_rule"], "signal": signal,
            "similarity": cqas_signal.get("similarity"),
            "closest_candidate_id": cqas_signal.get("closest_candidate_id"),
            "status": candidate["status"],
            "source_report_project_id": (candidate.get("trigger_context") or {}).get("source_report_project_id"),
        })
    return {"counts": counts, "total_with_signal": len(drilldown), "drilldown": drilldown}
