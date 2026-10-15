"""Geopolitical Intelligence OS: Report Review V2 — Report Reading Extraction（Step1）。

既存の完成済みレポート本文から、そのレポート自身が採用している地政学的な「読み方」
（Situation/Cue/Actor/Relationship/Primary Reading/Evidence/Scenario/Switch Condition等）
を構造化抽出する共通ロジック。Expert AI Core（geo_expert_engine.py）・CQAS・S Ranking・
Actor Intelligence・Retrieval等は一切呼ばない——既存geo_llm_common.call_llm_structuredに
よる軽量抽出のみ（geo_ios_review_import_service.py・geo_report_service.py
extract_report_metadata_with_aiと同じ既存パターン）。

絶対条件（no-hallucination）: レポートに明示されていない情報を一般知識・外部情報で
補完・推測しない。不明な項目はnull/空のままにし、not_explicit_fieldsへ列挙する。
「推定(inferred)」はこの抽出段階では行わない——それはExpert AI Reading Review側
（Review Adapter経由でExpert AI Coreを呼ぶ段階）の役割であり、本Extractionは
explicit/missingの2値のみを扱う。

旧geo_ios_review_import_service.py（REVIEW MODE Phase2C以前のreconstruction）の
_CONTEXT_SCHEMA/_RECONSTRUCT_SCHEMAと同じno-hallucination規律を引き継ぎ、1つの
統合スキーマへ再構成し、Review V2が必要とするswitch_condition/business_impact/
missing_reading/report_primary_readingを追加したものがextract_report_reading。
旧serviceのAlternative Reading生成・Revision Strategy等、フロー固有ロジックは
ここには持ち込まない（Review V2では使わない。旧serviceの既存2段スキーマ自体も
互換性維持のため変更しない）。
"""
import geo_llm_common

REPORT_READING_SCHEMA_NAME = "geo_report_reading_extraction_v1"

REPORT_READING_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis_theme": {"type": ["string", "null"]},
        "current_situation": {"type": ["string", "null"]},
        "question": {"type": ["string", "null"]},
        "report_context": {"type": ["string", "null"]},
        "cue": {"type": ["string", "null"]},
        "key_actors": {"type": "array", "items": {"type": "string"}},
        "actor_objectives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"actor": {"type": "string"}, "objective": {"type": "string"}},
                "required": ["actor", "objective"], "additionalProperties": False,
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"between": {"type": "string"}, "description": {"type": "string"}},
                "required": ["between", "description"], "additionalProperties": False,
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "report_primary_reading": {"type": ["string", "null"]},
        "causal_logic": {"type": ["string", "null"]},
        "direction_of_travel": {"type": ["string", "null"]},
        "scenario": {"type": ["string", "null"]},
        "switch_condition": {"type": ["string", "null"]},
        "supporting_evidence": {"type": "array", "items": {"type": "string"}},
        "weakening_evidence": {"type": "array", "items": {"type": "string"}},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "missing_reading": {"type": ["string", "null"]},
        "business_impact": {"type": ["string", "null"]},
        "not_explicit_fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "analysis_theme", "current_situation", "question", "report_context", "cue",
        "key_actors", "actor_objectives", "relationships", "assumptions",
        "report_primary_reading", "causal_logic", "direction_of_travel", "scenario",
        "switch_condition", "supporting_evidence", "weakening_evidence", "missing_evidence",
        "missing_reading", "business_impact", "not_explicit_fields",
    ],
    "additionalProperties": False,
}

REPORT_READING_SYSTEM_PROMPT = """あなたは、既存の地政学分析レポートから、そのレポート
自身が採用している「読み方(Reading)」の構造を抽出するアシスタントです。新しい分析・
新しい評価・新しい代替案の提示は行わないでください——ここでは、このレポートが実際に
何を主張しているかを構造化して取り出すだけです。

厳守事項（絶対条件）:
- レポート本文に明示されている内容のみを抽出してください。一般知識・外部情報で
  補完・推測しないでください。
- analysis_theme: このレポートが扱っているテーマ（1文程度）。
- current_situation: 現在の状況説明（2-4文程度、本文の記述に基づく）。
- question: このレポートが答えようとしている問い（本文に明示が無ければ、本文の構成から
  最も妥当な問いを1文で推定してよいが、新しい論点を作らないこと）。
- report_context: レポートの前提・スコープ・対象期間等（本文にあれば）。
- cue/key_actors/actor_objectives/relationships/assumptions/causal_logic/
  direction_of_travel/scenario: 本文に明示されている場合のみ埋めてください。
  scenarioは必ず「1つの文字列」で返すこと。シナリオが複数ある場合は、配列にせず、
  「シナリオ名：説明」を1行ずつ改行でつないだ1つの文字列にまとめてください。
- report_primary_reading: このレポートの中心的な読み・結論を1-3文で（本文の言い換えで
  よい、新しい主張を作らない）。
- switch_condition: 「この条件が変われば見立てが変わる」という趣旨の記述が本文に
  明示されている場合のみ埋めてください（無ければnull）。
- supporting_evidence/weakening_evidence: 本文に明示されている根拠のみ。
- missing_evidence: 本文が「まだ分からない」「今後確認が必要」等、明示的に不足を
  述べている箇所があればそれを使う。無ければ空配列のままにしてください（推測しない）。
- missing_reading: 本文が「他の見方もあるが採用しなかった」等、別の読み方の存在に
  明示的に言及している場合のみ、その内容を埋めてください（無ければnull。AIが
  勝手に代替読みを考案しないこと——それはこの抽出の役割ではありません）。
- business_impact: 当社・自社への示唆や影響が本文に明示されている場合のみ埋めて
  ください（無ければnull）。
- 該当する記述が本文に無い項目は、null（文字列項目の場合）または空配列のままにし、
  not_explicit_fieldsにそのfield名を必ず追加してください（無理に埋めない）。"""


def extract_report_reading(azure_client, model, *, raw_text: str) -> dict:
    """レポート本文からReport Reading（Step1 Extraction）を抽出する。

    Expert AI Engineは呼ばない。戻り値はREPORT_READING_SCHEMAの形そのまま
    （report_primary_reading等）——Expert AI側の読み(Reading)と混同しないよう、
    呼び出し側はこの戻り値を「レポート自身の読み」としてのみ扱うこと。
    """
    user_prompt = f"# レポート全文\n{raw_text}"
    result = geo_llm_common.call_llm_structured(
        azure_client, model, REPORT_READING_SYSTEM_PROMPT, user_prompt,
        REPORT_READING_SCHEMA, REPORT_READING_SCHEMA_NAME, max_attempts=2)
    return result["data"]
