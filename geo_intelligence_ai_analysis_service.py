"""Geopolitical Intelligence OS Phase 2F-3: Cross-report Grounded AI Analysis（横断分析）。

一般的なRAG Chatではない。価値は「過去にどう分析していたか／複数レポートで何が共通・
相違しているか／時系列で見方がどう変化したか／Expert Review済みかどうか／どのSourceが
根拠か」を追えること（§0/§1）。

絶対の順序（§2）:
    Normalized Identity → Permission Evaluation → Eligible Corpus → Metadata Filter
    → Retrieval → Context Selection → LLM Synthesis → Source Trace
AI側でPermission判断しない。LLMへ渡す前に利用可能sourceを完全に確定させる
（Permission/Metadata FilterはPhase 2F-1/2F-3のgeo_intelligence_ai_search_service.searchを
そのまま再利用し、新しいPermission Retrievalは作らない、§12）。

Internal-only Grounded Mode（§3）: 保存済みInternal Report/Knowledgeのみを根拠にする。
Web/GDELT/一般LLM知識/外部ニュースを混在させない。十分な内部資料が無ければ、LLM呼び出し
自体を行わずに「保存済みの社内レポートからは確認できません」を返す（§15 Hallucination
Guard、一般知識で穴埋めしない）。

Source-supported/Cross-report synthesis/AI inference（§5）: LLM出力のfindings各要素へ
claim_typeとして必須で持たせる。各findingのsource_refsは、こちらが割り当てたref_id
（[S1]等）のみを許可し、LLMが新しいIDを作った場合や存在しないref_idを指した場合は
そのfindingを落とす（Python側で検証、§15/§16のUnauthorized情報漏洩防止と同じ考え方——
LLM出力を無条件に信用しない）。

No Duplicate Source Inflation（§11）: 同一Report由来のReport版とKnowledge Item版が両方
retrieveされた場合、lineage（report_identity_id）でグルーピングし、1レポート＝1 context
sourceとして扱う（Version Awareness §8も同じ仕組みで実現: approved版があればそれを優先、
無ければ最新版）。
"""
import geo_intelligence_ai_search_service as search_service
import geo_llm_common
import geo_report_service

ANALYSIS_MODES = ("summary", "compare", "temporal", "coverage", "reviewed")

# Context Selection / Token Control（§10）: 検索結果を全部LLMへ渡さない。lineageで
# 重複排除した後、上位N件のみをcontextとする。
MAX_CONTEXT_SOURCES = 8

_INSUFFICIENT_EVIDENCE_MESSAGE = "保存済みの社内レポート・Knowledgeからは確認できません。"

_ANALYSIS_SCHEMA_NAME = "geo_intelligence_ai_cross_report_analysis_v1"
_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "insufficient_evidence": {"type": "boolean"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "claim_type": {
                        "type": "string",
                        "enum": ["source_supported", "cross_report_synthesis", "ai_inference"],
                    },
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim", "claim_type", "source_refs"],
                "additionalProperties": False,
            },
        },
        "coverage": {
            "type": "object",
            "properties": {
                "regions": {"type": "array", "items": {"type": "string"}},
                "themes": {"type": "array", "items": {"type": "string"}},
                "key_actors": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["regions", "themes", "key_actors"],
            "additionalProperties": False,
        },
        "temporal_notes": {"type": ["string", "null"]},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "insufficient_evidence", "findings", "coverage", "temporal_notes", "warnings"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """あなたは社内Geopolitical Intelligenceの複数レポートを横断して整理する
アシスタントです。

絶対条件:
- 与えられた社内Report/Knowledgeの抜粋（[S1]、[S2]...のref_idで示す）のみを根拠にすること。
  Webや一般知識、外部ニュース、あなたの一般知識で穴埋めしないこと。
- insufficient_evidence=trueにするのは、質問に対して意味のある回答が実質的に何も作れない
  場合（例: 該当するsourceが無い、質問の対象について一切言及が無い）に限ること。answerで
  「保存済みの社内レポートからは確認できません」の趣旨を述べる。単に一部の詳細（数値・
  対策案など）が抜粋に無いだけで、質問の主旨には答えられている場合はinsufficient_evidence=
  falseとし、その不足点はwarningsやanswer内の注記として書くこと（「材料不足」と「一部の
  詳細が無いこと」を混同しない）。
- findings内の各claimについて、claim_typeを3種のいずれかで明確に区別すること:
  - source_supported: 個別sourceに明示されている内容
  - cross_report_synthesis: 複数sourceを比較してあなたが統合した整理
  - ai_inference: sourceに直接書かれていない推論（必要最小限にとどめ、事実であるかのように
    書かないこと）
- findingsのsource_refsには、与えられたref_id（[S1]等）のみを使い、新しいIDを作らないこと。
- 未レビュー（review_status=uploaded_only）のsourceを、Expert Review済みであるかのように
  書かないこと。各sourceのreview_statusを尊重し、必要なら回答内で明示すること。
- 時系列を問われた場合は、各sourceのdateの順で見方がどう変化したかを整理すること
  （version/reviewの日付と混同しないこと）。
- 同じlineage（同一Report由来）のsourceを、あたかも独立した複数の裏付けであるかのように
  水増ししないこと（すでに1 sourceとして統合済み）。
"""

_MODE_INSTRUCTIONS = {
    "summary": "検索された複数資料の要点を整理してください。",
    "compare": "複数レポート間の共通点と相違点を明確に分けて整理してください。",
    "temporal": "各sourceのdateの順に、分析・見方・Watchpointがどう変化したかを時系列で整理してください。",
    "coverage": "扱われているActor/Region/Themeの範囲を整理してください。",
    "reviewed": "Expert Review済み（review_status=approved）のsourceを中心に整理し、"
                "未レビューのsourceは補助的に扱ってください。",
}


def clean_snippet(snippet: str | None) -> str | None:
    if not snippet:
        return None
    return snippet.replace("\x02", "").replace("\x03", "")


def _assign_lineage_ids(client, entries: list) -> None:
    """§11 No Duplicate Source Inflation / §8 Version Awareness。Report-hitは
    report_identity_id、Knowledge Item-hitはそのsource_report_id（特定Versionのreport_id）
    を経由してsame lineageのreport_identity_idへ正規化する。"""
    for e in entries:
        if e["source_type"] == "report":
            e["_lineage_id"] = f"identity:{e.get('report_identity_id') or e['report_id']}"

    ki_source_report_ids = sorted({
        e["source_report_id"] for e in entries
        if e["source_type"] == "knowledge_item" and e.get("source_report_id")
    })
    identity_by_report_id = {}
    if ki_source_report_ids:
        rows = client.select("geo_reports", {
            "select": "report_id,report_identity_id",
            "report_id": f"in.({','.join(ki_source_report_ids)})"})
        identity_by_report_id = {r["report_id"]: r.get("report_identity_id") or r["report_id"] for r in rows}

    for e in entries:
        if e["source_type"] == "knowledge_item" and e.get("source_report_id"):
            identity = identity_by_report_id.get(e["source_report_id"], e["source_report_id"])
            e["_lineage_id"] = f"identity:{identity}"
        elif "_lineage_id" not in e:
            e["_lineage_id"] = f"knowledge:{e['knowledge_item_id']}"


def _pick_primary(group: list) -> dict:
    """§8 Version Awareness（簡易ルール）: approved版があればそれを優先、
    無ければ最新版（version最大）。Reportが無くKnowledge Itemのみのlineageは
    そのKnowledge Itemをそのまま使う。"""
    reports = [e for e in group if e["source_type"] == "report"]
    knowledge_items = [e for e in group if e["source_type"] == "knowledge_item"]
    approved = [e for e in reports if e.get("review_status") == "approved"]
    if approved:
        return max(approved, key=lambda e: e.get("version") or 0)
    if reports:
        return max(reports, key=lambda e: e.get("version") or 0)
    return knowledge_items[0]


def _dedupe_by_lineage(client, entries: list) -> list:
    if not entries:
        return []
    _assign_lineage_ids(client, entries)
    groups: dict = {}
    for e in entries:
        groups.setdefault(e["_lineage_id"], []).append(e)
    return [_pick_primary(g) for g in groups.values()]


def _select_context(entries: list, *, max_sources: int) -> list:
    """§10 Context Selection/Token Control。rankingが無い（selected_source_refs経由）場合は
    text_scoreがNoneのため0.0として扱い、入力順を保つ。上位max_sources件のみ採用したうえで、
    提示順は§9 Temporal Analysisを見据えてdate昇順に揃える。"""
    ranked = sorted(entries, key=lambda e: e.get("text_score") or 0.0, reverse=True)
    capped = ranked[:max_sources]
    capped.sort(key=lambda e: e.get("date") or "")
    return capped


def _assign_ref_ids(entries: list) -> list:
    for i, e in enumerate(entries, start=1):
        e["ref_id"] = f"S{i}"
    return entries


def _render_source_block(e: dict) -> str:
    meta = [f"source_type={e['source_type']}"]
    if e.get("date"):
        meta.append(f"date={e['date'][:10]}")
    if e.get("version"):
        meta.append(f"version={e['version']}")
    if e.get("review_status"):
        meta.append(f"review_status={e['review_status']}")
    if e.get("confidentiality_label"):
        meta.append(f"confidentiality={e['confidentiality_label']}")
    if e.get("region"):
        meta.append(f"region={','.join(e['region'])}")
    if e.get("theme"):
        meta.append(f"theme={e['theme']}")
    if e.get("key_actors"):
        meta.append(f"key_actors={','.join(e['key_actors'])}")
    lines = [f"[{e['ref_id']}] title=\"{e.get('title') or ''}\" " + " ".join(meta)]
    if e.get("summary"):
        lines.append(f"  summary: {e['summary']}")
    snippet = clean_snippet(e.get("snippet"))
    if snippet:
        lines.append(f"  snippet: {snippet}")
    return "\n".join(lines)


def _render_user_prompt(query_text: str, mode: str, context_sources: list) -> str:
    instruction = _MODE_INSTRUCTIONS.get(mode, _MODE_INSTRUCTIONS["summary"])
    blocks = "\n\n".join(_render_source_block(e) for e in context_sources)
    return f"# 質問\n{query_text}\n\n# 分析方針\n{instruction}\n\n# 利用可能なSource\n{blocks}"


def _citation_entry(e: dict) -> dict:
    base = {
        "ref_id": e["ref_id"], "source_type": e["source_type"], "title": e.get("title"),
        "date": e.get("date"), "version": e.get("version"), "review_status": e.get("review_status"),
        "confidentiality_label": e.get("confidentiality_label"), "snippet": clean_snippet(e.get("snippet")),
    }
    if e["source_type"] == "report":
        base["report_id"] = e["report_id"]
    else:
        base["knowledge_item_id"] = e["knowledge_item_id"]
    return base


def _resolve_selected_sources(client, selected_source_refs: list, *, requester_context, is_internal_caller,
                               caller_domain, include_demo) -> tuple:
    """§13/§16 Selected Source Analysis: FrontendがどのIDを渡してきても、必ずここで
    Permissionを再評価する（Frontend選択を信用しない）。§16.7「selected source permissionを
    毎request再確認」も、この関数がrequestの都度呼ばれることでそのまま満たす（キャッシュしない）。
    戻り値: (entries, unauthorized_count)——unauthorized_countはtitle等を含まない件数のみ。"""
    requested_report_ids = sorted({
        r["id"] for r in selected_source_refs if r.get("source_type") == "report" and r.get("id")})
    requested_ki_ids = sorted({
        r["id"] for r in selected_source_refs if r.get("source_type") == "knowledge_item" and r.get("id")})

    eligible_report_ids = search_service.resolve_eligible_report_ids(
        client, requester_context=requester_context, is_internal_caller=is_internal_caller,
        caller_domain=caller_domain, include_demo=include_demo) if requested_report_ids else set()
    eligible_ki_ids = search_service.resolve_eligible_knowledge_item_ids(
        client, requester_context=requester_context, is_internal_caller=is_internal_caller,
        caller_domain=caller_domain, include_demo=include_demo) if requested_ki_ids else set()

    validated_report_ids = [rid for rid in requested_report_ids if rid in eligible_report_ids]
    validated_ki_ids = [kid for kid in requested_ki_ids if kid in eligible_ki_ids]
    unauthorized_count = (
        (len(requested_report_ids) - len(validated_report_ids))
        + (len(requested_ki_ids) - len(validated_ki_ids))
    )

    entries = []
    if validated_report_ids:
        for r in geo_report_service.get_reports_by_ids(client, validated_report_ids):
            entry = search_service.report_result(r)
            entry["text_score"], entry["snippet"] = None, None
            entries.append(entry)
    if validated_ki_ids:
        items = client.select("geo_knowledge_items", {
            "select": "*", "knowledge_item_id": f"in.({','.join(validated_ki_ids)})"})
        for item in items:
            entry = search_service.knowledge_item_result(item)
            entry["text_score"], entry["snippet"] = None, None
            entries.append(entry)
    return entries, unauthorized_count


def analyze(client, azure_client, model, query_text: str, *, mode: str = "summary",
            filters: dict = None, selected_source_refs: list = None,
            requester_context: dict = None, is_internal_caller: bool = True,
            caller_domain: str = None, include_demo: bool = False, limit: int = 50,
            max_sources: int = MAX_CONTEXT_SOURCES) -> dict:
    """Phase 2F-3本体。selected_source_refsが与えられればそちら優先（§13、Permissionのみ
    再検証・Metadata Filterは適用しない＝ユーザーの明示選択を尊重）。無ければquery_text+
    filtersでgeo_intelligence_ai_search_service.searchを呼ぶ（Permission→Metadata Filter→
    Retrievalの順は同関数が保証、新しいPermission Retrievalはここでは作らない）。"""
    if mode not in ANALYSIS_MODES:
        mode = "summary"

    if selected_source_refs:
        entries, unauthorized_count = _resolve_selected_sources(
            client, selected_source_refs, requester_context=requester_context,
            is_internal_caller=is_internal_caller, caller_domain=caller_domain, include_demo=include_demo)
        retrieval_metadata = {
            "source_selection": "explicit", "mode": mode,
            "requested_count": len(selected_source_refs), "unauthorized_selected_count": unauthorized_count,
        }
    else:
        search_result = search_service.search(
            client, query_text, requester_context=requester_context, is_internal_caller=is_internal_caller,
            caller_domain=caller_domain, include_demo=include_demo, limit=limit, filters=filters)
        entries = search_result["results"]
        retrieval_metadata = {
            "source_selection": "query", "mode": mode, "candidate_count": len(entries),
            "filters_applied": filters or {},
        }

    context_sources = _select_context(_dedupe_by_lineage(client, entries), max_sources=max_sources)
    _assign_ref_ids(context_sources)
    retrieval_metadata["context_source_count"] = len(context_sources)

    if not context_sources:
        return {
            "answer": _INSUFFICIENT_EVIDENCE_MESSAGE, "insufficient_evidence": True, "findings": [],
            "coverage": {"regions": [], "themes": [], "key_actors": []}, "temporal_notes": None,
            "warnings": [], "source_refs": [], "retrieval_metadata": retrieval_metadata,
        }

    user_prompt = _render_user_prompt(query_text, mode, context_sources)
    llm_result = geo_llm_common.call_llm_structured(
        azure_client, model, _SYSTEM_PROMPT, user_prompt, _ANALYSIS_SCHEMA, _ANALYSIS_SCHEMA_NAME,
        max_attempts=2)
    data = llm_result["data"]

    # §15 Hallucination Guard / §16 Security: LLMが存在しないref_idを引用した場合はそのfinding
    # を落とす（LLM出力の引用を無条件に信用しない、これによりUnauthorized sourceが
    # source_refsへ混入することも構造的に無くなる——context_sourcesは既にPermission/Metadata
    # Filter済みの集合だけで構成されているため）。
    known_ref_ids = {e["ref_id"] for e in context_sources}
    warnings = list(data.get("warnings") or [])
    cleaned_findings = []
    for f in (data.get("findings") or []):
        valid_refs = [r for r in (f.get("source_refs") or []) if r in known_ref_ids]
        if not valid_refs:
            warnings.append("finding_dropped_no_valid_source_ref")
            continue
        cleaned_findings.append({
            "claim": f.get("claim"), "claim_type": f.get("claim_type"), "source_refs": valid_refs,
        })

    used_ref_ids = {r for f in cleaned_findings for r in f["source_refs"]}
    source_trace = [_citation_entry(e) for e in context_sources if e["ref_id"] in used_ref_ids]
    if not source_trace:
        # findingsが1件もref_idを引用しなかった場合でも、実際にLLMへ渡したsourceは
        # 透明性のため全件trace対象にする（何を根拠に判断したかを追えることが最重要、§21末尾）。
        source_trace = [_citation_entry(e) for e in context_sources]

    return {
        "answer": data.get("answer"),
        "insufficient_evidence": bool(data.get("insufficient_evidence")),
        "findings": cleaned_findings,
        "coverage": data.get("coverage") or {"regions": [], "themes": [], "key_actors": []},
        "temporal_notes": data.get("temporal_notes"),
        "warnings": warnings,
        "source_refs": source_trace,
        "retrieval_metadata": retrieval_metadata,
    }
