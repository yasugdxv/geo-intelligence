"""Geopolitical Intelligence OS Phase 2F-4: Intelligence AI — True Multi-turn Chat。

目的はMulti-turn化のみ（INTELLIGENCE_AI_GROUNDED_ANALYSIS_V1のFreeze Manifest参照）。
既存Retrieval/Analysis Core（geo_intelligence_ai_search_service.search / geo_intelligence_ai_
analysis_service.analyze）は一切変更せず、その上に薄いレイヤーとして積む:

    Conversation（history）→ Current query resolution → 既存analyze() → Grounded Answer
    → Conversation state（呼び出し側=Frontendがlocalstorage等で保持、既存/api/geo/chatの
    ChatMsg[]と同じ「毎request全履歴を送る」ステートレスBackendパターンを踏襲）

絶対条件: 前turnのSource本文やPermission結果を一切信用しない。Current query resolutionは
会話の「話題（テキスト）」だけを見て検索クエリを作る補助的なテキスト変換であり、Permission
判定・Retrieval・Context Selectionは行わない——それらはこの関数が呼ぶanalyze()が、
本requestのrequester_context/is_internal_callerを使って毎回ゼロから評価する
（キャッシュ・引き継ぎ一切なし）。

Technical Debt（2026-09-30、Phase 2F-4完了時点で明示）: resolve_current_query()は
「Lexical Retrieval向けQuery Resolver」である。既存Retrieval（'simple' config Full Text
Search、AND一致、形態素解析辞書なし）向けに、話題を2〜4語程度の短いキーワード列へ
圧縮するよう意図的に設計している。将来Semantic/Vector Retrievalを導入する場合、この
圧縮方針はむしろ情報を失わせる（Semantic検索は短いキーワードより文脈を保った自然文の
方が有利なため）。Retrieval側がSemantic化された場合は、本関数（またはRetrieval種別ごとの
別実装）も合わせて差し替えることを前提とする——このprompt方針をSemantic Retrieval導入後も
そのまま使い回さないこと。
"""
import geo_intelligence_ai_analysis_service as analysis_service
import geo_llm_common

# 会話履歴が長くなっても、query resolutionへ渡すのは直近のやり取りだけに絞る
# （token control。Analysis Service本体のcontext selectionとは別の、この層独自の制御）。
MAX_HISTORY_MESSAGES = 8

_QUERY_RESOLUTION_SCHEMA_NAME = "geo_intelligence_ai_chat_query_resolution_v1"
_QUERY_RESOLUTION_SCHEMA = {
    "type": "object",
    "properties": {"resolved_query": {"type": "string"}},
    "required": ["resolved_query"],
    "additionalProperties": False,
}

_QUERY_RESOLUTION_SYSTEM_PROMPT = """あなたは社内Report/Knowledge検索のための検索クエリ解決
アシスタントです。

役割: 会話履歴と最新のユーザー発言から、検索エンジンへそのまま渡せる自己完結した短い検索
クエリを1つ作ること。

厳守事項:
- 代名詞・省略表現（「それ」「さっきの」「続き」「他には」等）は、会話履歴から具体的な
  話題語へ置き換えること。
- 会話履歴に含まれる過去の回答内容（過去に提示されたSourceの記載内容等）を、新しい事実として
  採用したり検索クエリへ埋め込んだりしないこと——あなたの役割は話題の特定だけであり、
  検索・Permission判定・回答生成は一切行わない（それらは後続の別処理が本requestの権限で
  毎回ゼロから行う）。
- 検索クエリは長い自然文にせず、話題を表す2〜4語程度の短いキーワード列にすること
  （既存検索エンジンはキーワードのAND一致のため、語を増やしすぎると本来ヒットすべき
  Sourceが見つからなくなる。会話から読み取れる推測の修飾語・言い換えを付け足さない）。
- 出力は検索クエリの文字列のみ（説明文・前置きを含めない）。"""


def _render_history(history: list) -> str:
    trimmed = history[-MAX_HISTORY_MESSAGES:]
    lines = []
    for m in trimmed:
        role = "User" if m.get("role") == "user" else "Assistant"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def resolve_current_query(azure_client, model, *, history: list, message: str) -> str:
    """§Current query resolution。historyが空（会話の最初のturn）なら、解決の必要が無いため
    LLM呼び出し自体を省略し、messageをそのまま返す（無駄なLLM呼び出しを増やさない）。"""
    if not history:
        return message
    history_text = _render_history(history)
    if not history_text:
        return message
    user_prompt = f"# 会話履歴\n{history_text}\n\n# 最新のユーザー発言\n{message}"
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _QUERY_RESOLUTION_SYSTEM_PROMPT, user_prompt,
        _QUERY_RESOLUTION_SCHEMA, _QUERY_RESOLUTION_SCHEMA_NAME, max_attempts=2)
    resolved = (result["data"].get("resolved_query") or "").strip()
    return resolved or message


def chat(client, azure_client, model, *, history: list, message: str, mode: str = "summary",
         filters: dict = None, requester_context: dict = None, is_internal_caller: bool = True,
         caller_domain: str = None, include_demo: bool = False, limit: int = 50) -> dict:
    """Phase 2F-4本体。既存analyze()を無変更で呼ぶだけ——Permission/Retrieval/Context
    Selection/Grounding/Source Traceのロジックは一切ここに複製しない。requester_context等は
    必ず「この関数の呼び出し元（API層）がこのrequestのために今組み立てた値」を渡すこと
    （historyの中に前turnのidentityやpermission結果を保持・再利用してはならない）。"""
    resolved_query = resolve_current_query(azure_client, model, history=history, message=message)
    result = analysis_service.analyze(
        client, azure_client, model, resolved_query, mode=mode, filters=filters,
        requester_context=requester_context, is_internal_caller=is_internal_caller,
        caller_domain=caller_domain, include_demo=include_demo, limit=limit)
    return {**result, "resolved_query": resolved_query}
