"""地政学ナレッジ＆インテリジェンスDB: 案件終了時のKnowledge Candidate抽出（11章）。

AIは候補を生成するだけで、status='candidate'のままgeo_knowledge_itemsへ挿入する
（絶対にactive化しない）。承認は人間がgeo_knowledge_service.review_knowledge_itemを
通してのみ行う。
"""
import geo_audit
import geo_foundation_pack_service
import geo_knowledge_item_schemas as schemas
import geo_knowledge_service
import geo_llm_common

CANDIDATE_TYPES = ("foundation_update", "precedent", "mechanism", "indicator", "reasoning_rule")

# Phase2A: 外部データ（World Bank/OFAC等）由来のClaimから生成するCandidate種別
EXTERNAL_CANDIDATE_TYPES = (
    "actor_profile_update", "relationship_update", "dependency_update", "sanctions_update",
)

def _extraction_schema(types: tuple) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "KnowledgeCandidateExtraction",
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates"],
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "candidate_type", "proposed_title", "proposed_summary", "structured_content",
                        "why_reusable", "applicable_conditions", "non_applicable_conditions",
                        "confidence", "confidence_rationale", "information_class",
                    ],
                    "properties": {
                        "candidate_type": {"type": "string", "enum": list(types)},
                        "proposed_title": {"type": "string"},
                        "proposed_summary": {"type": "string"},
                        "structured_content": {"type": "object"},
                        "why_reusable": {"type": "string"},
                        "applicable_conditions": {"type": "array", "items": {"type": "string"}},
                        "non_applicable_conditions": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "string", "enum": ["高", "中", "低", "判断不能"]},
                        "confidence_rationale": {"type": "string"},
                        "information_class": {
                            "type": "string",
                            "enum": ["observed_fact", "actor_claim", "external_assessment", "internal_hypothesis"],
                        },
                    },
                },
            },
        },
    }


CANDIDATE_EXTRACTION_SCHEMA = _extraction_schema(CANDIDATE_TYPES)
EXTERNAL_CANDIDATE_EXTRACTION_SCHEMA = _extraction_schema(EXTERNAL_CANDIDATE_TYPES)


def _find_duplicate_candidates(client, candidate_type: str, proposed_title: str) -> list:
    """タイトル完全一致のみの簡易重複検知（Phase1）。あいまい一致・意味的重複検知はPhase2。"""
    return client.select("geo_knowledge_items", {
        "select": "knowledge_item_id,title,status", "knowledge_type": f"eq.{candidate_type}",
        "title": f"eq.{proposed_title}",
    })


_CONFIDENCE_WORD_MAP = {
    "high": "高", "medium": "中", "moderate": "中", "mid": "中", "low": "低",
    "unknown": "判断不能", "undetermined": "判断不能", "n/a": "判断不能", "na": "判断不能",
    "indeterminate": "判断不能",
}


def _structured_content_schema_hint(types: tuple = CANDIDATE_TYPES) -> str:
    """LLMがstructured_contentを各candidate_typeの内部JSON Schema通りに埋められるよう、
    型・enum制約を含むフィールド定義そのものを明示する（キー名だけの明示では、confidenceに
    英語("high"等)を使う、string型フィールドに配列を渡す等の型不一致が実データで頻発した
    ため、property定義全体を渡す形に強化した）。"""
    import json as _json
    lines = []
    for candidate_type in types:
        schema = schemas.SCHEMAS_BY_KNOWLEDGE_TYPE[candidate_type]
        props = {k: v for k, v in schema.get("properties", {}).items()}
        lines.append(f"- candidate_type={candidate_type} のstructured_content必須スキーマ:\n"
                     f"  required={schema.get('required', [])}\n"
                     f"  properties={_json.dumps(props, ensure_ascii=False)}")
    return "\n".join(lines)


def _normalize_content(candidate_type: str, content: dict) -> dict:
    """LLM出力の軽微な型ゆれをSchema検証前に補正する（内容の創作は行わない、フォーマットのみ）。
    - stringが期待されるキーにlistが来た場合は結合してstringにする
    - 「高/中/低/判断不能」enumのキーに英語表記("high"等)が来た場合は日本語へ写像する
    どちらも失敗すればそのままSchema検証でエラーとなり、人間が確認できるskipped扱いになる。"""
    schema = schemas.SCHEMAS_BY_KNOWLEDGE_TYPE.get(candidate_type, {})
    props = schema.get("properties", {})
    normalized = dict(content)
    for key, value in list(normalized.items()):
        prop = props.get(key)
        if not isinstance(prop, dict):
            continue
        if prop.get("type") == "string" and isinstance(value, list):
            normalized[key] = "。".join(str(v) for v in value)
        elif prop.get("enum") == ["高", "中", "低", "判断不能"] and isinstance(value, str) and value not in prop["enum"]:
            mapped = _CONFIDENCE_WORD_MAP.get(value.strip().lower())
            if mapped:
                normalized[key] = mapped
    return normalized


def extract_candidates_from_case(client, azure_client, model: str, case_id: str, *,
                                  actor_id: str) -> dict:
    """案件終了時に、Foundation Update/Precedent/Mechanism/Indicator/Reasoning Rule候補を
    LLMで生成し、status='candidate'でgeo_knowledge_itemsへ登録する。
    Schema検証に失敗した候補は登録せずskipped_candidatesとして返す（人間が原文を確認できる）。"""
    pack = geo_foundation_pack_service.build_foundation_pack(client, case_id)
    hypotheses = pack["18_main_hypothesis"] + pack["19_alternative_hypotheses"]

    system_prompt = (
        "あなたは地政学分析のKnowledge Candidate抽出アシスタントです。今回の案件分析から、"
        "他の案件でも再利用できる知識（Foundation Update/Precedent/Mechanism/Indicator/"
        "Reasoning Rule）を提案してください。案件固有の一時的な情報や、事実未確認の憶測は"
        "提案しないでください。各候補には、適用可能な条件(applicable_conditions)と"
        "適用できない条件(non_applicable_conditions)を必ず含めてください。\n\n"
        "structured_contentは自由記述ではなく、以下のcandidate_type別に定義された必須キーを"
        "過不足なく含めること（キー名の言い換え・省略は不可、Schema検証で機械的に弾かれる）。\n"
        f"{_structured_content_schema_hint()}"
    )
    user_prompt = (
        f"案件: {pack['1_event_question_overview']['case_title']}\n"
        f"問い: {pack['1_event_question_overview']['original_question']}\n"
        f"最終評価: {pack['1_event_question_overview']}\n"
        f"主仮説・代替仮説: {[h['hypothesis_statement'] for h in hypotheses]}\n"
        f"既存Claim件数: {len(pack['14_existing_claims_and_evidence'])}"
    )

    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, CANDIDATE_EXTRACTION_SCHEMA,
        "geo_knowledge_candidate_extraction")
    candidates = result["data"].get("candidates", [])

    created, skipped = [], []
    for candidate in candidates:
        candidate_type = candidate["candidate_type"]
        content = dict(candidate["structured_content"])
        content.setdefault("applicable_conditions", candidate["applicable_conditions"])
        content.setdefault("non_applicable_conditions", candidate["non_applicable_conditions"])
        content = _normalize_content(candidate_type, content)
        try:
            schemas.validate_structured_content(candidate_type, content)
        except schemas.KnowledgeContentValidationError as e:
            skipped.append({"candidate_type": candidate_type, "proposed_title": candidate["proposed_title"],
                             "reason": str(e)})
            continue

        duplicates = _find_duplicate_candidates(client, candidate_type, candidate["proposed_title"])
        item = geo_knowledge_service.create_knowledge_item(
            client, knowledge_type=candidate_type, title=candidate["proposed_title"],
            structured_content=content, information_class=candidate["information_class"],
            created_by=actor_id, summary=candidate["proposed_summary"],
            confidence=candidate["confidence"], confidence_rationale=candidate["confidence_rationale"],
        )
        if duplicates:
            client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                           {"duplicate_of_id": duplicates[0]["knowledge_item_id"]})
            item["duplicate_of_id"] = duplicates[0]["knowledge_item_id"]

        # Phase2A: Active化後にgeo_expert_engineのEvidence必須条件を満たせるよう、
        # 案件の既存Claimをsupporting Claimとして紐付ける（Precedent等はCase固有の1claimに
        # 限定できないため、Foundation Packで確認済みの全Claimを対象にする）
        for claim_entry in pack["14_existing_claims_and_evidence"]:
            client.insert("geo_knowledge_item_claims", [{
                "knowledge_item_id": item["knowledge_item_id"], "claim_id": claim_entry["claim"]["claim_id"],
                "relationship": "supporting",
            }], prefer="return=minimal")

        created.append(item)

    geo_audit.log_audit(client, actor=actor_id, action="knowledge_candidate_extract",
                         target_table="geo_cases", target_id=case_id,
                         detail={"created_count": len(created), "skipped_count": len(skipped)})
    return {"created": created, "skipped": skipped}


def extract_candidates_from_external_claims(client, azure_client, model: str, claim_ids: list, *,
                                             actor_id: str) -> dict:
    """World Bank/OFAC等の外部取得由来Claimから、actor_profile_update/relationship_update/
    dependency_update/sanctions_update候補を生成する（Caseに紐づかない抽出経路）。
    生成された候補は、根拠となったclaim_ids全件へgeo_knowledge_item_claims（supporting）で
    紐付ける。これによりgeo_expert_engine.build_case_contextのEvidence必須条件を満たせる。"""
    claims = []
    for claim_id in claim_ids:
        rows = client.select("geo_claims", {"select": "*", "claim_id": f"eq.{claim_id}", "limit": "1"})
        if rows:
            claims.append(rows[0])
    if not claims:
        return {"created": [], "skipped": []}

    system_prompt = (
        "あなたは地政学分析のKnowledge Candidate抽出アシスタントです。外部データソース"
        "（World Bank統計、OFAC制裁リスト等）から抽出されたClaim群を読み、他の案件でも"
        "再利用できる構造的な知識更新（Actor Profile/Relationship/Dependency/Sanctionsの更新）"
        "を提案してください。Claimに書かれていない事実を創作しないでください。各候補には、"
        "適用可能な条件(applicable_conditions)と適用できない条件(non_applicable_conditions)を"
        "必ず含めてください。\n\n"
        "structured_contentは自由記述ではなく、以下のcandidate_type別に定義された必須キーを"
        "過不足なく含めること（キー名の言い換え・省略は不可、Schema検証で機械的に弾かれる）。\n"
        f"{_structured_content_schema_hint(EXTERNAL_CANDIDATE_TYPES)}"
    )
    user_prompt = "以下は外部データソースから取得したClaim一覧です:\n" + "\n".join(
        f"- ({c['information_class']}) {c['claim_text']}" for c in claims
    )

    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, EXTERNAL_CANDIDATE_EXTRACTION_SCHEMA,
        "geo_external_knowledge_candidate_extraction")
    candidates = result["data"].get("candidates", [])

    created, skipped = [], []
    for candidate in candidates:
        candidate_type = candidate["candidate_type"]
        content = dict(candidate["structured_content"])
        content.setdefault("applicable_conditions", candidate["applicable_conditions"])
        content.setdefault("non_applicable_conditions", candidate["non_applicable_conditions"])
        content = _normalize_content(candidate_type, content)
        try:
            schemas.validate_structured_content(candidate_type, content)
        except schemas.KnowledgeContentValidationError as e:
            skipped.append({"candidate_type": candidate_type, "proposed_title": candidate["proposed_title"],
                             "reason": str(e)})
            continue

        duplicates = _find_duplicate_candidates(client, candidate_type, candidate["proposed_title"])
        item = geo_knowledge_service.create_knowledge_item(
            client, knowledge_type=candidate_type, title=candidate["proposed_title"],
            structured_content=content, information_class=candidate["information_class"],
            created_by=actor_id, summary=candidate["proposed_summary"],
            confidence=candidate["confidence"], confidence_rationale=candidate["confidence_rationale"],
        )
        if duplicates:
            client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"},
                           {"duplicate_of_id": duplicates[0]["knowledge_item_id"]})
            item["duplicate_of_id"] = duplicates[0]["knowledge_item_id"]

        for claim in claims:
            client.insert("geo_knowledge_item_claims", [{
                "knowledge_item_id": item["knowledge_item_id"], "claim_id": claim["claim_id"],
                "relationship": "supporting",
            }], prefer="return=minimal")

        created.append(item)

    geo_audit.log_audit(client, actor=actor_id, action="knowledge_candidate_extract_external",
                         target_table="geo_claims", target_id=claim_ids[0] if claim_ids else None,
                         detail={"created_count": len(created), "skipped_count": len(skipped),
                                  "source_claim_count": len(claims)})
    return {"created": created, "skipped": skipped}
