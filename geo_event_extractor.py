"""地政学インテリジェンス基盤: 文書からのEvent Candidate抽出（指示書9.2章）。

記事ではなくEventとEvidenceを正本にする、というプロジェクトの絶対原則に基づき、
LLMには「本文に明記された事実の抽出」のみを行わせ、閾値計算やリスク判定はさせない。
不明な値は創作せずnullとする。抽出結果は人またはVerification処理を経るまで
verified=false のEvent Candidateとして扱う。

Phase 2: 各Eventがどの指標（geo_indicator_definitions）に最も近いかをLLMに分類させる
（1事象＝1指標、多重付与しない）。件数集計・閾値判定はgeo_indicator_engine.py側の
コードで行い、ここでは分類のみを行う。
"""

from geo_llm_common import call_llm_structured


def load_indicator_codes(client, theme_id: str) -> list[dict]:
    """テーマ配下の指標一覧を{code, name}の形で返す（表示順）"""
    rows = client.select("geo_indicator_definitions", {
        "select": "indicator_code,name", "theme_id": f"eq.{theme_id}", "order": "display_order.asc",
    })
    return [{"code": r["indicator_code"], "name": r["name"]} for r in rows]


def _build_event_schema(indicator_codes: list[str]) -> dict:
    indicator_code_field = {
        "type": ["string", "null"],
        "description": "この事象が最も近い指標のコード。該当する指標がない/判断できない場合はnull",
    }
    if indicator_codes:
        indicator_code_field["enum"] = [*indicator_codes, None]

    return {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "actor": {"type": ["string", "null"], "description": "行動主体（不明ならnull）"},
                        "action": {"type": ["string", "null"], "description": "行動・事象の内容"},
                        "target": {"type": ["string", "null"], "description": "対象（不明ならnull）"},
                        "location": {"type": ["string", "null"], "description": "場所（不明ならnull）"},
                        "start_at": {"type": ["string", "null"], "description": "ISO8601日時。不明ならnull"},
                        "end_at": {"type": ["string", "null"], "description": "ISO8601日時。不明ならnull"},
                        "quantities": {
                            "type": ["object", "null"],
                            "description": "機数・隻数・回数等の数量。キーは日本語ラベル、値は数値または文字列",
                        },
                        "event_type": {"type": ["string", "null"], "description": "事象の種別（自由記述）"},
                        "indicator_code": indicator_code_field,
                        "confidence": {
                            "type": ["number", "null"],
                            "minimum": 0, "maximum": 1,
                            "description": "本文の記述の明確さに基づく抽出確度（0〜1）。不明ならnull",
                        },
                        "unknown_fields": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                            "description": "本文からは判断できず埋められなかったフィールド名の一覧",
                        },
                        "claim_text": {
                            "type": "string",
                            "description": "この事象の主張内容を1〜2文で要約したもの（Evidenceのclaim_textになる）",
                        },
                        "quote": {
                            "type": ["string", "null"],
                            "description": "根拠となる本文からの原文引用（要約・言い換えではなく原文そのまま）",
                        },
                    },
                    "required": ["claim_text"],
                },
            },
        },
        "required": ["events"],
    }


def _build_system_prompt(indicator_choices: list[dict]) -> str:
    indicator_lines = "\n".join(f"- {c['code']}: {c['name']}" for c in indicator_choices)
    indicator_block = (
        f"\n\nこのテーマで監視している指標一覧（indicator_codeの選択肢）:\n{indicator_lines}\n"
        "各事象について、上記のうち最も近い指標のcodeを1つ選ぶこと。どれにも該当しない・"
        "判断できない場合はnullにする（無理に当てはめない）。"
        if indicator_choices else ""
    )
    return f"""あなたは地政学インテリジェンス基盤のEvent抽出アシスタントです。
与えられた文書本文から、具体的に起きた・起きている事象（Event Candidate）を抽出してください。

厳守事項:
- 本文に明記されていない値は創作せず、必ずnullにする（推測で埋めない）。
- 見出しや前置き、一般論、ビジョン表明のような抽象的な記述は抽出しない。具体的な行動・事象の
  記述のみを抽出する。
- quoteは本文からの原文引用のみとする。要約や言い換えをquoteに入れない。
- 1つの文書に複数の異なる事象が含まれる場合は、それぞれを個別のEventとして抽出する
  （1つのEventに複数の事象をまとめない）。
- リスクレベルや脅威度の判定、確率計算はしない。confidenceは「本文の記述がどれだけ明確か」の
  抽出確度であり、事象の重大性の評価ではない。indicator_codeの選択も分類であり、件数集計や
  重大性の判定はしない（それらはこの後コード側で行う）。
- 事象が見つからない場合は空配列を返す。{indicator_block}
"""


def extract_events(client, model: str, document_text: str, theme_code: str,
                    indicator_choices: list[dict] = None) -> list[dict]:
    """文書本文からEvent Candidateのリストを抽出する。抽出できなければ空リストを返す。
    indicator_choicesを渡すと、各事象にindicator_code（どの指標に近いか）も分類させる"""
    if not document_text or not document_text.strip():
        return []
    indicator_choices = indicator_choices or []
    schema = _build_event_schema([c["code"] for c in indicator_choices])
    system_prompt = _build_system_prompt(indicator_choices)
    user_prompt = (
        f"テーマ: {theme_code}\n\n"
        f"以下は監視対象文書の本文です。この本文から読み取れる具体的な事象のみを抽出してください。\n\n"
        f"---\n{document_text[:12000]}\n---"
    )
    result = call_llm_structured(
        client, model, system_prompt, user_prompt, schema, "geo_event_extraction",
    )
    return result["data"].get("events", [])
