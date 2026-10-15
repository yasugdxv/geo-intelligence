"""地政学ナレッジ＆インテリジェンスDB Phase2A: Hybrid Retrieval（検索）。

全件取得してPython側でランキングするのではなく、PostgreSQL RPC関数
（sql/2026-08-05_geo_phase2a_retrieval_trace.sql、ts_rank_cdでDB側にTop-Kを計算させる）を使う。
'simple'設定のFTSのみでは日本語の複合語・表記ゆれに弱いため、Entity/Knowledge Item/Claimいずれも
以下3方式のスコアを合成する: (1)完全一致 (2)正規化文字列の部分一致 (3)FTS score。
vector_scoreは常にNone（Embedding Deployment未確認のため、docs/geopolitical-retrieval-design.md参照）。
"""
from datetime import datetime, timezone

import geo_entity_resolution
import geo_freshness_service

EXACT_MATCH_SCORE = 1.0
ALIAS_MATCH_SCORE = 0.85
PARTIAL_MATCH_SCORE = 0.5


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def _create_retrieval_run(client, *, query_text: str, filters: dict, retrieval_mode: str,
                           case_id: str = None, expert_request_id: str = None,
                           is_demo: bool = False) -> dict:
    return client.insert("geo_retrieval_runs", [{
        "case_id": case_id, "expert_request_id": expert_request_id, "query_text": query_text,
        "filters": filters, "retrieval_mode": retrieval_mode, "is_demo": is_demo,
    }])[0]


def _complete_retrieval_run(client, retrieval_run_id: str, retrieved_count: int) -> None:
    client.update("geo_retrieval_runs", {"retrieval_run_id": f"eq.{retrieval_run_id}"}, {
        "completed_at": datetime.now(timezone.utc).isoformat(), "retrieved_count": retrieved_count,
    })


def _record_result(client, *, retrieval_run_id: str, result_type: str, result_id: str, rank: int,
                    scores: dict, inclusion_reason: str = None, exclusion_reason: str = None,
                    used_in_final_context: bool = False) -> dict:
    return client.insert("geo_retrieval_results", [{
        "retrieval_run_id": retrieval_run_id, "result_type": result_type, "result_id": result_id,
        "rank": rank, "entity_score": scores.get("entity_score"), "text_score": scores.get("text_score"),
        "vector_score": None, "source_score": scores.get("source_score"),
        "recency_score": scores.get("recency_score"), "approval_score": scores.get("approval_score"),
        "final_score": scores.get("final_score"), "inclusion_reason": inclusion_reason,
        "exclusion_reason": exclusion_reason, "used_in_final_context": used_in_final_context,
    }])[0]


def _partial_match_candidates(client, table: str, name_column: str, query_text: str,
                               include_demo: bool, limit: int = 20) -> list:
    params = {"select": "*", f"{name_column}": f"ilike.*{query_text}*", "limit": str(limit)}
    if not include_demo:
        params["is_demo"] = "eq.false"
    try:
        return client.select(table, params)
    except Exception:
        return []


def search_entities(client, query_text: str, *, entity_types: list = None, include_demo: bool = False,
                     limit: int = 20, case_id: str = None, log_trace: bool = True) -> dict:
    """Entity検索: 完全一致(geo_entity_resolution) + 正規化部分一致(ilike) + FTS(RPC)を合成する。"""
    scores_by_id, rows_by_id = {}, {}

    for match in geo_entity_resolution.find_entity_matches(client, query_text):
        score = EXACT_MATCH_SCORE if match["match_type"] == "exact_canonical" else ALIAS_MATCH_SCORE
        scores_by_id[match["entity_id"]] = max(scores_by_id.get(match["entity_id"], 0), score)

    for row in _partial_match_candidates(client, "geo_entities", "canonical_name", query_text, include_demo):
        rows_by_id[row["entity_id"]] = row
        scores_by_id.setdefault(row["entity_id"], PARTIAL_MATCH_SCORE)

    try:
        fts_rows = client.rpc("search_geo_entities", {
            "p_query": query_text, "p_entity_types": entity_types, "p_include_demo": include_demo,
            "p_limit": limit,
        }) or []
    except Exception:
        fts_rows = []
    text_scores = {}
    for row in fts_rows:
        rows_by_id.setdefault(row["entity_id"], row)
        text_scores[row["entity_id"]] = row.get("text_score", 0)
        scores_by_id.setdefault(row["entity_id"], 0)

    missing_ids = [eid for eid in scores_by_id if eid not in rows_by_id]
    if missing_ids:
        fetched = client.select("geo_entities", {"select": "*", "entity_id": f"in.({','.join(missing_ids)})"})
        for row in fetched:
            rows_by_id[row["entity_id"]] = row

    results = []
    for entity_id, entity_score in scores_by_id.items():
        row = rows_by_id.get(entity_id)
        if not row:
            continue
        if entity_types and row.get("entity_type") not in entity_types:
            continue
        text_score = text_scores.get(entity_id, 0)
        final_score = entity_score + text_score * 0.3
        results.append({"entity": row, "entity_score": entity_score, "text_score": text_score,
                         "final_score": final_score})

    results.sort(key=lambda r: r["final_score"], reverse=True)
    results = results[:limit]

    if log_trace:
        run = _create_retrieval_run(client, query_text=query_text, filters={"entity_types": entity_types},
                                     retrieval_mode="hybrid_fts", case_id=case_id)
        for rank, r in enumerate(results, start=1):
            _record_result(client, retrieval_run_id=run["retrieval_run_id"], result_type="entity",
                            result_id=r["entity"]["entity_id"], rank=rank, scores=r,
                            inclusion_reason="matched_query", used_in_final_context=False)
        _complete_retrieval_run(client, run["retrieval_run_id"], len(results))

    return {"results": results}


def search_knowledge_items(client, query_text: str, *, status: list = None, knowledge_types: list = None,
                           include_demo: bool = False, limit: int = 20, case_id: str = None,
                           log_trace: bool = True, retrieval_status: list = None) -> dict:
    """Knowledge Item検索: title/summaryの完全一致・正規化部分一致・FTSを合成する。
    Historical Path Dependency/Precedentは古さだけで減点しない（recency_scoreを中立に保つ）。

    Phase2B-2: statusとretrieval_statusは独立した二軸（一軸への統合はしない）。既存のstatus
    フィルタ（デフォルトactiveのみ）はそのまま維持し、retrieval_status（デフォルトavailableのみ）
    を追加のAND条件として適用する。既存呼び出し側のデフォルト挙動は変わらない
    （backfillでactive+availableが一致するため）。"""
    status = status or ["active"]
    retrieval_status = retrieval_status or ["available"]
    exact_rows = _partial_match_candidates(client, "geo_knowledge_items", "title", query_text, include_demo)

    try:
        fts_rows = client.rpc("search_geo_knowledge_items", {
            "p_query": query_text, "p_status": status, "p_knowledge_types": knowledge_types,
            "p_include_demo": include_demo, "p_limit": limit, "p_retrieval_status": retrieval_status,
        }) or []
    except Exception:
        fts_rows = []

    combined = {}
    for row in exact_rows:
        is_exact_title = _normalize(row["title"]) == _normalize(query_text)
        combined[row["knowledge_item_id"]] = {
            "item": row, "text_score": 0,
            "match_score": EXACT_MATCH_SCORE if is_exact_title else PARTIAL_MATCH_SCORE,
        }
    for row in fts_rows:
        entry = combined.setdefault(row["knowledge_item_id"], {"item": row, "text_score": 0, "match_score": 0})
        entry["text_score"] = row.get("text_score", 0)

    today = datetime.now(timezone.utc).date()
    results = []
    for knowledge_item_id, entry in combined.items():
        item = entry["item"]
        if status and item.get("status") not in status:
            continue
        if retrieval_status and item.get("retrieval_status") not in retrieval_status:
            continue
        if knowledge_types and item.get("knowledge_type") not in knowledge_types:
            continue
        approval_score = 1.0 if item.get("status") == "active" else 0.5
        # Phase2B-2: origin='internal_expert_review'のItemは外部Sourceのtrust_tierに依拠する
        # confidence enumベースのsource_scoreを使わず、専門家Promote済みという事実・support_count・
        # last_confirmed_atの新しさを信号にする（usage_count/last_used_atはAnalytics専用でここでは
        # 使わない。Issue/Entity relevanceは既存のmatch_score/text_scoreがそのまま担う）
        if item.get("origin") == "internal_expert_review":
            confirmed_recency = _recency_score(item.get("last_confirmed_at"), today)
            confidence_score = min(1.0, 0.5 + 0.1 * min(item.get("support_count") or 0, 5) + 0.1 * confirmed_recency)
        else:
            confidence_score = {"高": 1.0, "中": 0.6, "低": 0.3}.get(item.get("confidence"), 0.1)
        # Precedent/Historical Path Dependencyは古さだけで強く減点しない
        no_recency_penalty = item.get("knowledge_type") in ("precedent", "historical_path_dependency")
        recency_score = 1.0 if no_recency_penalty else _recency_score(item.get("updated_at"), today)
        final_score = entry["match_score"] + entry["text_score"] * 0.3 + approval_score * 0.2 + recency_score * 0.1
        results.append({"knowledge_item": item, "text_score": entry["text_score"],
                         "source_score": confidence_score, "recency_score": recency_score,
                         "approval_score": approval_score, "final_score": final_score})

    results.sort(key=lambda r: r["final_score"], reverse=True)
    results = results[:limit]

    if log_trace:
        run = _create_retrieval_run(client, query_text=query_text,
                                     filters={"status": status, "knowledge_types": knowledge_types},
                                     retrieval_mode="hybrid_fts", case_id=case_id)
        for rank, r in enumerate(results, start=1):
            _record_result(client, retrieval_run_id=run["retrieval_run_id"], result_type="knowledge_item",
                            result_id=r["knowledge_item"]["knowledge_item_id"], rank=rank, scores=r,
                            inclusion_reason="matched_query", used_in_final_context=False)
        _complete_retrieval_run(client, run["retrieval_run_id"], len(results))

    return {"results": results}


def search_claims(client, query_text: str, *, include_demo: bool = False, limit: int = 20,
                  case_id: str = None, log_trace: bool = True, retrieval_status: list = None) -> dict:
    """Phase2B-2: retrieval_status（デフォルトavailableのみ）をAND条件として追加する
    （restricted/deprecated/archivedのClaimが検索結果に漏れないようにする。従来この概念自体が
    存在せず無フィルタだったため、これは新規の絞り込みだが、backfillで大半のClaimがavailableに
    揃っているため既存動作への実質的な影響はない想定）。"""
    retrieval_status = retrieval_status or ["available"]
    exact_rows = _partial_match_candidates(client, "geo_claims", "claim_text", query_text, include_demo)
    try:
        fts_rows = client.rpc("search_geo_claims", {
            "p_query": query_text, "p_include_demo": include_demo, "p_limit": limit,
            "p_retrieval_status": retrieval_status,
        }) or []
    except Exception:
        fts_rows = []

    combined = {}
    for row in exact_rows:
        combined[row["claim_id"]] = {"claim": row, "text_score": 0, "match_score": PARTIAL_MATCH_SCORE}
    for row in fts_rows:
        entry = combined.setdefault(row["claim_id"], {"claim": row, "text_score": 0, "match_score": 0})
        entry["text_score"] = row.get("text_score", 0)

    results = []
    for claim_id, entry in combined.items():
        claim = entry["claim"]
        if retrieval_status and claim.get("retrieval_status") not in retrieval_status:
            continue
        final_score = entry["match_score"] + entry["text_score"] * 0.5
        results.append({"claim": claim, "text_score": entry["text_score"], "final_score": final_score})
    results.sort(key=lambda r: r["final_score"], reverse=True)
    results = results[:limit]

    if log_trace:
        run = _create_retrieval_run(client, query_text=query_text, filters={},
                                     retrieval_mode="hybrid_fts", case_id=case_id)
        for rank, r in enumerate(results, start=1):
            _record_result(client, retrieval_run_id=run["retrieval_run_id"], result_type="claim",
                            result_id=r["claim"]["claim_id"], rank=rank, scores=r,
                            inclusion_reason="matched_query", used_in_final_context=False)
        _complete_retrieval_run(client, run["retrieval_run_id"], len(results))

    return {"results": results}


def search_reports(client, query_text: str, *, include_demo: bool = False, limit: int = 20) -> list:
    """Report検索（地政学アプリClooseout Phase2）: 高度なVector Searchは構築せず、既存の
    汎用ILIKEヘルパー（_partial_match_candidates）のみで最低限の部分一致検索を実現する
    （新規RPC・新規migrationは作らない）。

    geo_reportsの`author`/`report_type`へのILIKEに加えて、Reportの実体（geo_documents.title）
    も検索対象にする。ただしgeo_documentsへ素のILIKEをかけると、World Bank/OFAC/週次モニタリング
    等のReport以外の文書もtitleマッチしてしまう（geo_documentsにReport限定フラグが無いため）。
    そのため、既にgeo_reportsに存在するdocument_idのみへ絞り込んでからtitleをILIKEする。"""
    reports_by_id = {}
    for column in ("author", "report_type"):
        for row in _partial_match_candidates(client, "geo_reports", column, query_text, include_demo, limit=limit):
            reports_by_id[row["report_id"]] = row

    all_reports_params = {"select": "*"}
    if not include_demo:
        all_reports_params["is_demo"] = "eq.false"
    all_reports = client.select("geo_reports", all_reports_params)
    report_by_document_id = {r["document_id"]: r for r in all_reports}

    if report_by_document_id:
        doc_matches = _partial_match_candidates(
            client, "geo_documents", "title", query_text, include_demo, limit=limit)
        for doc in doc_matches:
            report = report_by_document_id.get(doc["document_id"])
            if report:
                reports_by_id.setdefault(report["report_id"], report)

    return list(reports_by_id.values())[:limit]


def _recency_score(updated_at: str, today) -> float:
    parsed = geo_freshness_service._parse_date(updated_at)
    if not parsed:
        return 0.5
    age_days = max((today - parsed).days, 0)
    if age_days <= 30:
        return 1.0
    if age_days <= 180:
        return 0.7
    if age_days <= 365:
        return 0.4
    return 0.2
