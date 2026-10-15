"""Geopolitical Intelligence OS: Reading Portfolio（Step2「読み方提案」改善②）。

ユーザー指示（2026-09-24、Step2品質検証結果を受けた改善）: 既存CQAS
（geo_expert_engine.py、main_scenario/alternative_scenarios）はRanking/Reasoning品質
そのものは損なわれていない（Gold Case Blind Testで確認済み、結論の方向はEguchiさんの
実際の読みと一致）——ただし自然に出てくる候補は「継続/緩和/激化」という単一の強度軸に
偏りがちで、前提そのものを疑う読み（Contrarian）や、別Actor・別構造からの読み
（Wildcard）が自発的に出てこない。

CQAS本体（main_scenarioの生成方法・ranking・Reasoning）は一切変更しない。CQASが
すでに生成したmain_scenario/alternative_scenariosを「既に埋まっている視点」として
渡した上で、それとは別の視点を追加生成する薄いレイヤーとしてのみ新設する
——「CQASに全部やらせようとしない」役割分離（ユーザー指示）。

4案構成の役割分担:
1. mainstream: 既存CQASのmain_scenarioをそのまま採用（呼び出し元が既に持っている、
   本モジュールでは再生成しない）
2. alternative_causal_structure: 同じ結論でも別の因果構造・別Actorの視点から読んだ場合の
   もう一つの有力な読み
3. premise_reversal: 現在の前提（資料・CQASの読みが当然視していること）の一部を反転
   させた場合に成立する読み
4. wildcard_non_obvious: 発生確率は低くても、もし起きればこの局面を大きく変える、
   見落とされがちな読み

本モジュールが生成するのは2〜4のみ（最大3件）。支持する材料が不足していれば無理に
埋めず、insufficient_material=Trueで明示的にスキップしてよい（捏造しない、
Case-specificなhardcodeもしない——テーマ非依存の汎用schema/promptのみ）。
"""
import geo_llm_common

_READING_SLOTS = ("alternative_causal_structure", "premise_reversal", "wildcard_non_obvious")

_PORTFOLIO_SCHEMA_NAME = "geo_ios_reading_portfolio"
_PORTFOLIO_SCHEMA = {
    "type": "object",
    "properties": {
        "readings": {
            "type": "array",
            "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string", "enum": list(_READING_SLOTS)},
                    "insufficient_material": {"type": "boolean"},
                    "insufficient_material_reason": {"type": "string"},
                    "title": {"type": "string"},
                    "reading": {"type": "string"},
                    "key_cue": {"type": "string"},
                    "key_actors_and_structure": {"type": "string"},
                    "supporting_reasons": {"type": "array", "items": {"type": "string"}},
                    "weakening_information": {"type": "array", "items": {"type": "string"}},
                    "switch_condition": {"type": "string"},
                },
                "required": ["slot", "insufficient_material"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["readings"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """あなたは地政学インテリジェンス分析の専門家AIです。この局面については、
既に「主流の読み（Mainstream Reading）」が別途生成済みです。あなたの役割は、
そのMainstream Readingを繰り返さず、それを補完する3つの追加の読み方候補を
提示することです。

各案は単なるキーワード列挙ではなく、「何が起きているため、誰がどう動き、その結果
どうなると読むのか」という一連のストーリーとして書いてください。

**重要（Actor-first、2026-09-25追加）**: readingとtitleは、出来事（法案・政策・声明）
そのものを主語にしないでください。「この法案は」「この政策は」のような出来事主語ではなく、
key_actors_and_structureに登場する**具体的なActorを主語**にして、そのActorが何を
望み・何に縛られ・その出来事をどう使おうとしているかを中心に組み立ててください。
出来事は、Actorの目的・力学が表面化した結果・道具として位置づけること。

3案は以下の枠に対応させてください（無理に奇抜な案を捏造せず、与えられた資料・
Evidence・Actor・Structure・既存Mainstream Readingから成立する範囲で広げること）:

- alternative_causal_structure: Mainstream Readingと似た結論であっても、別の因果構造・
  別のActorの視点から読んだ場合の、もう一つの有力な読み
- premise_reversal: 資料やMainstream Readingが当然の前提としていることの一部を反転
  させた場合に成立する読み（前提そのものへの懐疑から出発する）
- wildcard_non_obvious: 発生確率は低くても、もし起きればこの局面を大きく変える、
  見落とされがちな読み

**重要**: 与えられた資料・Contextだけでは、ある枠を裏付ける材料が本当に不足している
場合、無理に読みを捏造しないでください。その場合はinsufficient_material=trueとし、
insufficient_material_reasonに「何が不足しているか」を一言書くだけでよく、
それ以外のフィールドは省略してかまいません。

insufficient_material=falseの場合、各案には必ず以下を含めてください:
- title: 読み方タイトル（一文の見出し）
- reading: この読みが何を意味するかの1〜2文の要約
- key_cue: この読みの起点となった、資料中の具体的な着眼点（何に引っかかったか）
- key_actors_and_structure: この読みで重要になるActor・力学構造
- supporting_reasons: この読みを支持する理由（配列、資料の記述に基づくこと）
- weakening_information: この読みを弱める・反証し得る情報（配列）
- switch_condition: 何が観測されたらこの読みへ確度が寄るか（一文）

捏造したEvidence・Actor・数値を使わないこと。与えられた資料に無い具体的事実は
書かないこと（一般的な推論・構造的な仮説は可）。"""


def _format_mainstream_for_prompt(main_scenario: dict, alternative_scenarios: list) -> str:
    lines = [f"# Mainstream Reading（CQAS main_scenario、繰り返さないこと）",
             main_scenario.get("description") or ""]
    if main_scenario.get("selection_rationale"):
        lines.append(f"選定理由: {main_scenario['selection_rationale']}")
    if alternative_scenarios:
        lines.append("\n# CQASが既に生成済みのAlternative（これらとも重複しないこと）")
        for i, alt in enumerate(alternative_scenarios, start=1):
            lines.append(f"- Alt{i}: {alt.get('description') or ''}")
    return "\n".join(lines)


def generate_additional_readings(azure_client, model: str, *, context_and_materials_text: str,
                                  main_scenario: dict, alternative_scenarios: list | None = None) -> list[dict]:
    """CQASのmain_scenario/alternative_scenariosと重複しない、追加の読み最大3件を生成する。
    insufficient_material=Trueの案は戻り値から除外する（呼び出し元へ空のカードを渡さない）。
    戻り値の各要素: {slot, title, reading, key_cue, key_actors_and_structure,
    supporting_reasons, weakening_information, switch_condition}"""
    user_prompt = (
        f"# 分析前提・参考資料\n{context_and_materials_text}\n\n"
        f"{_format_mainstream_for_prompt(main_scenario, alternative_scenarios or [])}"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _SYSTEM_PROMPT, user_prompt, _PORTFOLIO_SCHEMA, _PORTFOLIO_SCHEMA_NAME)
    readings = result["data"]["readings"]
    return [r for r in readings if not r.get("insufficient_material")]
