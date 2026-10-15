"""Geopolitical Intelligence OS: Reading Studio Presentation Transformation v1（§23-24）。

「読み方提案」画面(Step2)のReading Cardを人間が読みやすい形へ再編集するための、Expert AI
Coreとは別立ての薄いLLM層。既存Reading Exposure Card（presentation_service.build_reading_
exposureの出力=既にExpert AI構造化出力から整形済みのフィールド）だけを入力とし、以下は
一切行わない:

- 新しいFact・新しいActor・新しいEvidence・新しい因果仮説の追加（§24絶対条件）
- Expert AI Core/CQAS/S Ranking/Reading Exposure選定ロジックへの書き込み・変更
- raw response_json（hypothesis_register等）への再アクセス（Composerの薄いBuilder方針
  ＝「既に計算済みのfieldだけを使う、生データから再判定しない」を踏襲する）

入力に無い情報は「明示されていない」と出力させる（一般常識で埋めさせない）。この層が
失敗しても（LLM未設定・呼び出し失敗）、呼び出し元は既存の生フィールド表示にフォール
バックできるよう、例外はcatchせずそのまま呼び出し元へ伝播する（キャッシュ層側でcatchする）。
"""
import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import geo_ios_expert_review_view as review_view
import geo_llm_common

_logger = logging.getLogger(__name__)

_SCHEMA_NAME = "geo_ios_reading_presentation_transform"

# Actor-first化（2026-09-25追加、ユーザー指示）でprompt/schemaを変更したため、既存cache
# （geo_ios_scenario_candidates.content.presentation_transform）を無条件に無効化する
# バージョン印。get_or_build_reading_presentationのfingerprint計算に含める。
# v3_structured_orientation（2026-09-28追加、Final Expert Session Readiness Validation
# Phase B、ユーザー指示13-4対応）: narrativeへorientation_and_counterparty_view/
# constraints_and_leverageを追加したため再度bump。
_TRANSFORM_PROMPT_VERSION = "v3_structured_orientation"

_SEMANTIC_TAGS = ["王道", "有力な別解", "前提を疑う", "構造から読む", "ジャンプ視点"]

_TRANSFORM_SCHEMA = {
    "type": "object",
    "properties": {
        "readings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "semantic_tag": {"type": "string", "enum": _SEMANTIC_TAGS},
                    "title": {"type": "string"},
                    "one_line_thesis": {"type": "string"},
                    "narrative": {
                        "type": "object",
                        "properties": {
                            "actors": {"type": "string"},
                            "objectives": {"type": "string"},
                            "action": {"type": "string"},
                            "interaction": {"type": "string"},
                            "current_situation": {"type": "string"},
                            "direction": {"type": "string"},
                            "orientation_and_counterparty_view": {
                                "type": "string",
                                "description": "13-4対応（2026-09-28追加）: このActorの持続的な"
                                                "志向（Enduring Orientation）と、相手Actorをどう"
                                                "見ているか（Counterparty View）を、一読で分かる"
                                                "短い記述としてまとめたもの。narrative本文と重複"
                                                "してよい——本文から抜き出して構造化するだけ。",
                            },
                            "constraints_and_leverage": {
                                "type": "string",
                                "description": "13-4対応（2026-09-28追加）: このActorが現在何に"
                                                "縛られているか（Constraint）と、何を使えるか"
                                                "（Available Leverage）を、一読で分かる短い記述"
                                                "としてまとめたもの。narrative本文と重複してよい。",
                            },
                        },
                        "required": [
                            "actors", "objectives", "action", "interaction",
                            "current_situation", "direction",
                            "orientation_and_counterparty_view", "constraints_and_leverage",
                        ],
                        "additionalProperties": False,
                    },
                    "reasoning": {
                        "type": "object",
                        "properties": {
                            "observation": {"type": "string"},
                            "why_it_matters": {"type": "string"},
                            "interpretation": {"type": "string"},
                        },
                        "required": ["observation", "why_it_matters", "interpretation"],
                        "additionalProperties": False,
                    },
                    "reaction_chain": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "actor": {"type": "string"},
                                "trigger": {"type": "string"},
                                "likely_reaction": {"type": "string"},
                            },
                            "required": ["actor", "trigger", "likely_reaction"],
                            "additionalProperties": False,
                        },
                    },
                    "expected_direction": {"type": "string"},
                    "actor_dynamics_check": {
                        "type": "object",
                        "description": "EVENT_ONLY_READING検出用の自己申告（2026-09-25追加）。"
                                        "このReading自体の判定結果ではなく、正直な自己評価。",
                        "properties": {
                            "actor_is_grammatical_subject": {
                                "type": "boolean",
                                "description": "narrativeの主語が出来事(法案・政策・声明)ではなく、"
                                                "具体的なActor名になっているか。",
                            },
                            "states_actor_objective_or_orientation": {
                                "type": "boolean",
                                "description": "そのActorが何を望む/避けたいか、または持続的な"
                                                "志向・行動原理が示されているか。",
                            },
                            "states_counterparty_or_constraint": {
                                "type": "boolean",
                                "description": "相手Actor・制約・関係性のいずれかが示されているか。",
                            },
                            "is_event_only": {
                                "type": "boolean",
                                "description": "上記3つが実質的に満たされておらず、出来事の意味"
                                                "分類・要約に留まっている場合はtrue。",
                            },
                        },
                        "required": [
                            "actor_is_grammatical_subject", "states_actor_objective_or_orientation",
                            "states_counterparty_or_constraint", "is_event_only",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "index", "semantic_tag", "title", "one_line_thesis", "narrative",
                    "reasoning", "reaction_chain", "expected_direction", "actor_dynamics_check",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["readings"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = """あなたは、既に生成済みの地政学分析（Reading）を、意思決定者が読みやすい
形に**再編集**するPresentation担当です。新しい分析・新しい推論を行うことは禁止です。

## Readingの定義（2026-09-25改訂、最重要）

Readingとは、観測された出来事（法案・政策・声明）の**意味分類**ではありません。

Readingとは、Key ActorのObjective（何を実現したいか）・Constraint（何に縛られているか）・
Counterparty（誰を動かそう/牽制しようとしているか）・Leverage（何を使えるか）を基に、
**Actor間の力学が、なぜ今の出来事を生み、次に何を起こし得るか**についての検証可能な仮説です。

出来事（法案・政策・声明）は分析の主役ではありません。**Actor Dynamicsが表面化した
観測事象・Evidence・道具**として位置づけてください。

厳守事項（絶対条件、既存のまま）:
1. 各Readingの入力フィールド（description/key_actors/why_this_matters/what_was_seen/
   what_was_doubted/hypothesis_formed/selection_rationale/why_not_main/outlook/
   actor_profiles）に**書かれている内容だけ**を使ってください。
2. 新しいFact・新しいActor・新しいEvidence・新しい因果仮説を追加することは禁止です。
   与えられていない固有名詞・数値・出来事を作らないでください。
3. あるフィールドに対応する情報が入力に存在しない場合、無理に埋めず「明示されていない」
   とだけ書いてください。一般常識で補完しないでください。

## narrative（actors/objectives/action/interaction/current_situation/direction）の
組み立て方（2026-09-25改訂）

4. narrativeはEvent（法案・政策）ではなく、**Actor（具体的な人物・組織名）を文の主語に
   すること**。「この法案は〜」「この政策は〜」のような出来事主語の文で始めないでください。
5. actor_profilesが与えられているActorについては、以下の**優先順位**で組み立てること
   （このActorに関するEnduring Orientationを、現在の出来事から逆算して作文しては
   いけません——actor_profiles自体に既に書かれている内容を、この順序で並べるだけです）:
   a. Enduring orientation（persistent_preference/strategic_relationship_preference:
      このActorが持続的に何を志向するか、相手との関係をどう捉えているか）
   b. Preferred operating model（perceived_path: このActor自身が、目的達成の経路を
      どう考えているか）
   c. Current situational objective（issue_objective/ultimate_objective: 今回の局面で
      何を実現したいか）
   d. Constraint / available leverage（driving_forces/red_line/available_leverage:
      何に縛られ、何を使えるか）
   e. Observed event as instrument（event_role: 今回の出来事が、このActorにとって
      目的そのものか、手段・交渉カード・シグナル・制約・譲歩・制度化のいずれかとして
      機能しているか。intended_target_actor_refがあれば、それは「誰に対する手段か」
      を意味する）
   a→eの順で並べ、eを最後に置いてください（出来事の説明を先頭に置かない）。
5b. 13-4対応（2026-09-28追加、ユーザー指示）: 上記a〜eの内容から、Reading Detail画面で
   一読できるよう2つの短い構造化フィールドを合わせて作成してください（新しい情報を足す
   のではなく、narrative本文で既に述べた内容から該当部分を抜き出して要約するだけです）:
   - orientation_and_counterparty_view: 上記a（Enduring orientation）とb（相手との関係の
     捉え方）から、このActorの持続的な志向・相手観を1〜2文でまとめる。
   - constraints_and_leverage: 上記d（Constraint/available leverage）から、このActorが
     現在何に縛られ、何を使えるかを1〜2文でまとめる。
   actor_profilesが無い、または該当情報が無い場合は「明示されていない」としてください
   （一般常識で埋めない、絶対条件3と同じ）。
6. actor_profilesが与えられていないActor・Readingについては、既存のdescription/
   why_this_matters等から可能な範囲でActor中心の文章を組み立てるが、情報が無い場合は
   「明示されていない」としてください（actor_profilesを持つReadingと同じ深さの記述を
   無理に作らない）。
7. interactionは、複数Actorのobjective/leverageが競合・一致するかを説明してください
   （例:「AとBの利害は"カードを持つこと"では一致するが、"いつ・誰に使うか"では緊張する」
   のような構造）。単一Actorしか登場しないReadingの場合は、そのActorとCounterpartyの
   関係で構わない。

8. reasoning（observation/why_it_matters/interpretation）は、what_was_seen→
   what_was_doubted/hypothesis_formed→selection_rationaleの内容をそれぞれの観点に
   割り当てるだけにしてください。
9. reaction_chainのactorは、そのReadingのkey_actorsに含まれる主体のみを使ってください
   （新しいActorを登場させない）。likely_reactionは、そのReadingのnarrative/outlookに
   既にある内容の言い換えに留めてください。該当する情報が無いReadingではreaction_chainを
   空配列にしてください。
10. semantic_tagは、与えられた全Readingを比較した上で、内容そのものの性質から判断して
   ください（内部の役割ラベルではなく、実際に何が書かれているかで判断すること）:
   - 王道: 与えられた証拠に最も忠実で、最も自然な読み方
   - 有力な別解: 王道とは異なるが、十分な根拠を持つ読み方
   - 前提を疑う: 他の読みが当然視している前提を疑う読み方
   - 構造から読む: Actor間の構造・関係性・制度から読み解く読み方
   - ジャンプ視点: 証拠は限定的だが、非自明で検討する価値のある読み方
   同じタグが複数のReadingに付くことは避け、内容が実際にそう判断できる場合のみ使って
   ください。
11. titleは、**Actor名を文の主語**にし、「何を望み」「どう動いているか」「それが何を
   意味するか」のうち少なくとも2〜3要素を1文に含む、具体的な文にしてください
   （「〜の構造変化」「〜への転換」のような抽象的なタイトル、「この法案は」「この政策は」
   のような出来事主語のタイトルは禁止）。ただし、入力に無い固有名詞・数値を新しく作らない
   でください。情報が不足しActor主語で具体化できない場合は、既存titleをそのまま使って
   ください。
12. actor_dynamics_checkは、完成したnarrative/titleについての**正直な自己評価**です。
   点数を良く見せるための自己申告ではなく、実際にactor_is_grammatical_subject/
   states_actor_objective_or_orientation/states_counterparty_or_constraintの3つが
   満たされているかをそれぞれ独立に判定し、3つとも満たされていなければ
   is_event_only=trueとしてください。
13. 各Readingについて、必ずindexの値をそのまま返してください（並べ替えない）。"""


def _v(item):
    return (item or {}).get("value")


def _reading_for_prompt(card: dict) -> dict:
    outlook = card.get("outlook") or {}
    return {
        "reading_role": card.get("reading_role"),
        "title": card.get("title"),
        "description": _v(card.get("description")),
        "key_actors": _v(card.get("key_actors")),
        "why_this_matters": _v(card.get("why_this_matters")),
        "what_was_seen": _v(card.get("what_was_seen")),
        "what_was_doubted": _v(card.get("what_was_doubted")),
        "hypothesis_formed": _v(card.get("hypothesis_formed")),
        "selection_rationale": _v(card.get("selection_rationale")),
        "why_not_main": _v(card.get("why_not_main")),
        "outlook_if_correct": _v(outlook.get("if_correct")),
        "outlook_weakens_if": _v(outlook.get("weakens_if")),
    }


def _format_readings_for_prompt(readings: list) -> str:
    lines = []
    for i, r in enumerate(readings):
        lines.append(f"\n## Reading index={i} (内部role: {r.get('reading_role')})")
        for key in (
            "title", "description", "key_actors", "why_this_matters", "what_was_seen",
            "what_was_doubted", "hypothesis_formed", "selection_rationale", "why_not_main",
            "outlook_if_correct", "outlook_weakens_if",
        ):
            value = r.get(key)
            if value in (None, "", [], {}):
                continue
            lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _actor_profile_for_prompt(profile: dict) -> dict:
    return {
        "actor": profile.get("actor") or profile.get("actor_ref"),
        "persistent_preference": profile.get("persistent_preference"),
        "strategic_relationship_preference": profile.get("strategic_relationship_preference"),
        "perceived_path": profile.get("perceived_path"),
        "issue_objective": profile.get("issue_objective"),
        "ultimate_objective": profile.get("ultimate_objective"),
        "driving_forces": profile.get("driving_forces"),
        "red_line": profile.get("red_line"),
        "available_leverage": profile.get("available_leverage"),
        "event_role": profile.get("event_role"),
    }


def _format_actor_profiles_for_prompt(key_actor_profiles: list) -> str:
    """key_actor_profiles（Expert AI Stage1が常に生成する、Actorごとの持続的な志向・
    経路認識・イベントの位置づけ）を、Reading個々ではなくReading一式に共通するActor参照
    ブロックとして整形する。特定のReadingにひもづく情報ではないため、全Readingへ共通の
    文脈として渡す（各Reading側で該当Actorが登場する場合のみ参照させる）。"""
    if not key_actor_profiles:
        return "（Actor Intelligenceは提供されていません。narrativeはdescription等の既存\n" \
               "フィールドのみからActor中心に組み立ててください。）"
    lines = []
    for profile in key_actor_profiles:
        formatted = _actor_profile_for_prompt(profile)
        actor_name = formatted.get("actor")
        if not actor_name:
            continue
        lines.append(f"\n## Actor: {actor_name}")
        for key in (
            "persistent_preference", "strategic_relationship_preference", "perceived_path",
            "issue_objective", "ultimate_objective", "driving_forces", "red_line",
            "available_leverage", "event_role",
        ):
            value = formatted.get(key)
            if value in (None, "", [], {}):
                continue
            lines.append(f"- {key}: {value}")
    if not lines:
        return "（Actor Intelligenceは提供されていません。narrativeはdescription等の既存\n" \
               "フィールドのみからActor中心に組み立ててください。）"
    return "\n".join(lines)


def build_reading_presentation_transform(azure_client, model, *, situation_text: str,
                                          question_text: str | None, reading_cards: list,
                                          key_actor_profiles: list | None = None) -> list:
    """reading_cards（presentation_service.build_reading_exposureの出力）を入力順のまま
    reading_presentation配列として返す。各要素はLLMが対応するindexを返さなかった場合None
    になる（呼び出し元がその要素だけキャッシュ済みの旧値/生表示へフォールバックすること）。

    key_actor_profiles（Expert AI response_jsonのkey_actor_profiles、Actor Intelligence
    フラグの有無に関わらず常に生成される）が与えられた場合、Reading個別ではなくReading一式
    に共通するActor参照ブロックとしてpromptへ渡す（§Prompt Rule 5参照）。"""
    if not reading_cards:
        return []
    readings_for_prompt = [_reading_for_prompt(c) for c in reading_cards]
    user_prompt = (
        f"# Situation\n{situation_text or '（明示されていない）'}\n\n"
        f"# Question\n{question_text or '（明示されていない）'}\n\n"
        f"# Actor Intelligence Reference（Reading一式に共通、該当Actorが登場するReadingで"
        f"参照すること）\n{_format_actor_profiles_for_prompt(key_actor_profiles or [])}\n\n"
        f"# 対象Reading一覧（全{len(readings_for_prompt)}件、全てについてindex順に出力すること）"
        f"{_format_readings_for_prompt(readings_for_prompt)}"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _SYSTEM_PROMPT, user_prompt, _TRANSFORM_SCHEMA, _SCHEMA_NAME)
    by_index = {item.get("index"): item for item in result["data"].get("readings", [])}
    return [by_index.get(i) for i in range(len(reading_cards))]


# ============================================================================
# Expert Review Comment Transformation（2026-10-04追加、読み方UI改善）
# ============================================================================
#
# Reading Cardのタイトルと2〜4文の「Expert Review Comment」だけを、保存済みExpert AI応答
# （Frozen Reasoning）の既存内容から「専門家が同僚へ返すレビュー調」へ編集し直すPresentation専用の
# LLM Transformation。上のbuild_reading_presentation_transform（Sessionページ用、6段落narrative）
# とは出力が別（title/commentのみ）だが、LLM呼び出し基盤（geo_llm_common）・「新しい分析を
# しない」という契約・失敗時Fallbackの方針は同じものを再利用する。
#
# - 新しい分析・Actor・事実・Reading・順位変更は禁止（プロンプト＋出力検証の両方で担保）
# - 結果はファイルへキャッシュする（入力指紋が同じ=同じresponse_idなら同じ出力、画面を開く
#   たびにLLMを呼ばない）。Frozenページ（Sessionを持たない）でも使えるよう、キャッシュ
#   キーはscenario_candidate_idではなく入力内容の指紋にしている
# - LLM失敗・出力検証NGのカードは、呼び出し元がDeterministic Fallback（既存文章を短く整えた
#   もの、geo_ios_expert_review_view.fallback_headline_and_comment）を表示する

# Writing Guide/プロンプト/出力形式を更新したら必ずversionを上げる（キャッシュ指紋に含まれるため、
# 古いキャッシュは自動的に無効になり、次回生成で新しい版へ置き換わる）。
EXPERT_REVIEW_PROMPT_VERSION = "expert_review_comment_v4_flow_evidence"
_EXPERT_REVIEW_SCHEMA_NAME = "geo_ios_expert_review_comment"
_EXPERT_REVIEW_PATTERNS = ["A_anomaly_first", "B_contrast_with_consensus", "C_incentive_dynamics", "other"]

_EXPERT_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        # 「ここまでの流れ」（局面全体で共通）。どういう変化・力学で現在の局面になったかの連鎖。
        "flow": {
            "type": "array",
            "items": {"type": "string"},
        },
        "readings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title": {"type": "string"},
                    "comment": {"type": "string"},
                    "pattern": {"type": "string", "enum": _EXPERT_REVIEW_PATTERNS},
                    # 既存Evidenceの「この読みにどう効くか」（保存済みの理由が無い項目だけ）
                    "evidence_notes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "side": {"type": "string", "enum": ["supporting", "contradicting"]},
                                "i": {"type": "integer"},
                                "note": {"type": "string"},
                            },
                            "required": ["side", "i", "note"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["index", "title", "comment", "pattern", "evidence_notes"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["flow", "readings"],
    "additionalProperties": False,
}

_EXPERT_REVIEW_SYSTEM_PROMPT = """あなたは地政学分析チームのシニア専門家です。後輩やマネージャーから
「この局面をどう読むか」を相談され、既に出ている分析（Reading）について、同僚へメールで
レビューコメントを返す立場です。あなたの仕事は**既存の読みを、専門家のレビュー調へ編集し直すこと**だけです。
新しい分析・新しい推論は一切しません。

## 絶対に守ること（禁止）
1. 入力に存在しない新しいActor（人物・組織）・事実・数値・固有名詞を加えない。
2. 新しいReadingを発明しない。各Readingの主張を言い換えるだけで、別の主張にすり替えない。
3. Readingの優劣・順位を変えない。「本線」は現時点で最も有力な読みとして扱い、別の見方が
   本線より有力であるかのように書かない（本線を覆す条件は、入力にある「崩れる条件」の範囲で触れるだけ）。
4. 入力のEvidence（支える材料・合わない材料）に無い事実を根拠にしない。
5. 原文より強く断定しない。原文が「可能性」なら「可能性」のまま、原文が留保付きなら留保を残す。
6. 切替条件・崩れる条件を変更しない。
7. 内部用語・分析手法の用語（hypothesis、H0等の記号、Evidence Fit、as_of、CQAS、
   Primary/Alternative/Switch Condition等）を出力に使わない。IDやJSON風の表記も出さない。

## 許可されること（編集）
- 冗長な説明を圧縮する／結論を先頭へ移す／文の順序を読みやすく入れ替える。
- 国ではなく、入力に存在する具体的なActor（人物・組織・部署）を主語として明示する
  （入力のActor情報にある名前だけ。根拠なく細分化しない）。
- 最初の違和感（入力のInitial Anomaly等）を「ここが気になる」という読みの起点として使う。
- 一般的な見方（表面的な説明）と、このReadingの見方との差分を分かりやすくする。
- 論文調・AIレポート調を、専門家が同僚に返す自然な日本語へ変える。

## 出力仕様
各Readingについて title と comment を出力する。
- title: このReadingで**何を読んでいるのか**が一目で分かる1文（目安25〜60字）。出来事の名称
  ではなく「誰が何を狙っている/何が本質だと読むか」を言い切るか、「〜ではないか」の形にする。
  疑問形は毎回使わない（根拠が十分なら言い切ってよい）。Actor名を主語にできるなら主語にする。
- comment: **2〜4文、合計120〜250字（絶対に300字を超えない。長くなりそうなら3文に削る）**。1文は長くても
  60字程度まで。構成の基本:
  1文目＝このReadingの結論（先に結論）。
  2文目＝なぜそう見るのか。そのActorの思惑・制約・違和感（「なぜ今そう動くのか」）。
  3文目＝必要なら一般的な見方との差分（「一般には〜と受け取られやすいが」「表面上は〜だが」）。
  4文目＝必要なら留保、または今後見るべき点（入力の「見るべき点」「崩れる条件」の範囲で）。
  出来事の再説明（「〇〇が発言した」だけ）で終わらず、「そのActorにとって何を意味し、何を狙って
  いる可能性があるか」まで踏み込む。ただし根拠は入力にあるものだけ。
- pattern: 実際に使った説明の型を自己申告する（品質確認用）。
  A_anomaly_first＝違和感→Actorの思惑→別の説明→どう読むか
  B_contrast_with_consensus＝一般的な見方→ただしこの行動は不自然→Actorの立場から見る→別の読み
  C_incentive_dynamics＝この状況を変えるインセンティブを持つのは誰か→Actor間の力学→Reading
  other＝上記以外。入力のデータが支える型を選ぶ。無理に型へ当てはめない。

## 文体（専門家が同僚へ返すレビューコメント）
- コメントは「この読みは〜という整理です」と読み自体を解説するのではなく、**専門家本人の見立てとして
  直接語る**（「〜と見ています」「〜ではないでしょうか」）。「ここでは」「ここは」「このカードでは」
  「この読みは」「このReadingは」で書き出さない。「カード」「シナリオ」「逆張り」というメタな言葉も使わない。
- 書き出しは観察・違和感・結論から入る（「〜という説明が一般的だが、〜の点は少し引っかかる」
  「まず気になるのは、〜がなぜ今〜なのかという点」など。毎カード同じ書き出しにしない）。
- 観察は「〜ですね」、読みは「〜と思います／〜と見るのが自然です」、確度が低い所は
  「〜ではないか／〜の可能性があります」、まだ言い切れない所は「現時点では〜／まだ〜とは言いにくい」。
- 因果は「だから」ではなく、そのActorの立場からの逆算でつなぐ（「〜の立場に立つと〜を優先する理由が
  あるはず」「仮に〜が目的なら〜という動きは筋が通る」）。
- 避ける表現: 「以上を総合的に勘案すると」「複合的な要因が作用している」「多角的に検討する必要がある」
  「当該事象における主要な示唆は」「〜を示唆している」「〜と言える」「本稿では」など。
- 本線とそれ以外のReadingで、同じ言い回し・同じ書き出しを繰り返さない。
- 入力が少ない／Actor情報が無いReadingは、無理にActorの思惑まで書かず、入力にある範囲で短く、
  留保を付けて書く（捏造より短い方がよい）。

## flow（ここまでの流れ、局面全体で1つ）
「どういう変化によって今の力学になったか」を、3〜6ステップの連鎖で書く。単なる出来事の年表・羅列にしない。
- 各ステップは1文（目安25〜60字）。「Aが〜した」「そのためBの立場が〜になった」「その結果Cが〜」のように、
  Actorや状況の立場の変化が次のステップを生む形でつなぐ。最後のステップは「現在の局面」。
- 材料は入力の「局面の力学」「Actor情報」「関係・認識」「最初の違和感」だけ。入力に無い過去の経緯・日付・
  数値・因果は書かない。因果が入力から読み取れない箇所は断定せず、「〜を背景に」程度にとどめる。
- 材料が乏しく3ステップに満たない場合は、無理に埋めず空配列 [] を返す（捏造より空の方がよい）。
- 各ステップは矢印・番号を付けず、文だけを書く。

## evidence_notes（証拠の「だから何」）
各Readingの「supporting_materials」「contradicting_materials」の各項目には i（番号）と、
saved_reason（保存済みの理由があるか）が付いている。**saved_reason が null の項目だけ**について、
「その証拠が、このReadingをどう支持（または弱化）するか」を1文（目安25〜90字）で書く。
- Actor → 観測された動き → このReadingにとっての意味、の順で、証拠の内容と読みの関係だけを述べる。
- 証拠に書かれていない事実を足さない。「確実に」「証明する」等の過剰な断定をしない（「補強する」
  「整合的」「〜の可能性を支える」程度）。順位や他のReadingへ言及しない。
- saved_reason があるものは出力しない。書けない項目も出力しない（空配列でよい）。
- side は supporting / contradicting、i は入力の番号。

## その他
- 入力の「元のタイトル」「要旨」は硬い文章のことがある。意味は保ったまま上記の文体へ直す。
- 必ず各Readingのindexをそのまま返す（並べ替えない）。"""

_FORBIDDEN_PAPER_STYLE = [
    "総合的に", "複合的な要因", "多角的", "当該", "示唆している", "本稿", "以上を踏まえ", "以上を総合",
    "逆張り",
]
# 検証NGのReadingだけを修正指示付きで再生成する上限回数（全体の再生成はしない）
_EXPERT_REVIEW_MAX_RETRIES = 2
_FORBIDDEN_META_OPENINGS = ("ここでは", "ここでの", "ここは", "このカード", "この読み", "このReading", "本カード")
_FORBIDDEN_ASSERTIVE = ["必ず", "間違いなく", "確実に", "断言", "疑いなく"]


def _expert_review_cache_dir() -> Path:
    override = os.environ.get("GEO_EXPERT_REVIEW_CACHE_DIR")
    return Path(override) if override else Path(__file__).parent / "expert_review_cache"


def _compact(values, limit, max_chars=150):
    out = []
    for v in values or []:
        text = review_view.first_sentences(v, 1, max_chars)
        if text:
            out.append(text)
    return out[:limit]


def _evidence_materials(rows, limit):
    """expert_reviewの証拠行を、LLMへ渡す番号付きの材料にする。saved_reasonは既存の理由（あれば）。
    LLMは saved_reason が null の項目についてだけ、読みとの関係を書く。"""
    return [{"i": i, "actor": r.get("actor"), "what": r.get("what"),
             "saved_reason": r.get("meaning")} for i, r in enumerate((rows or [])[:limit])]


def _reading_input_for_expert_review(card: dict) -> dict:
    review = card.get("expert_review") or {}
    v = review_view._value
    outlook = card.get("outlook") or {}
    return {
        "perspective": (review.get("perspective") or {}).get("label"),
        "is_primary": bool(review.get("is_primary")),
        "original_title": review_view.first_sentences(card.get("title"), 1, 90) or None,
        "summary": review_view.first_sentences(v(card.get("description")), 2, 260) or None,
        "why_selected_or_why_it_matters": review_view.first_sentences(
            v(card.get("selection_rationale")) or v(card.get("why_this_matters")), 2, 260) or None,
        "doubt_that_led_here": review_view.first_sentences(v(card.get("what_was_doubted")), 2, 220) or None,
        "formed_reading": review_view.first_sentences(v(card.get("hypothesis_formed")), 2, 220) or None,
        "why_not_main": review_view.first_sentences(v(card.get("why_not_main")), 1, 120) or None,
        "actors_in_this_reading": [s["name"] for s in review.get("stakeholders") or []],
        "supporting_materials": _evidence_materials(review.get("supporting"), 5),
        "contradicting_materials": _evidence_materials(review.get("contradicting"), 3),
        "reading_breaks_if": _compact(v(outlook.get("weakens_if")), 2),
        "things_to_watch": _compact(v(outlook.get("if_correct")), 2),
    }


def build_expert_review_inputs(presentation: dict, response_json: dict, cards: list) -> dict:
    """LLMに渡す入力（保存済みresponseの既存内容のみ）。fingerprint計算にも使う。"""
    response_json = response_json or {}
    kp = presentation.get("key_point") or {}
    v = review_view._value
    actor_profiles = []
    for p in (response_json.get("key_actor_profiles") or [])[:6]:
        name = review_view.short_actor_name(p.get("actor") or p.get("actor_ref"))
        if not name:
            continue
        actor_profiles.append({k: val for k, val in {
            "actor": name,
            "enduring_orientation": review_view.first_sentences(p.get("persistent_preference"), 1, 140),
            "view_of_counterparty": review_view.first_sentences(
                p.get("strategic_relationship_preference"), 1, 140),
            "how_it_thinks_it_wins": review_view.first_sentences(p.get("perceived_path"), 1, 140),
            "objective_now": review_view.first_sentences(
                p.get("issue_objective") or p.get("ultimate_objective"), 1, 140),
            "constraint_or_red_line": review_view.first_sentences(p.get("red_line"), 1, 140),
            "observed_action": review_view.first_sentences(p.get("observed_action"), 1, 140),
            "role_of_this_event_for_actor": review_view.first_sentences(
                json.dumps(p.get("event_role"), ensure_ascii=False)
                if isinstance(p.get("event_role"), (dict, list)) else p.get("event_role"), 1, 140),
        }.items() if val})
    relationships = []
    for dp in ((presentation.get("relationships") or {}).get("perceptions") or [])[:4]:
        text = review_view.first_sentences(v(dp.get("perception")), 1, 140)
        if text:
            relationships.append({
                "observer": review_view.short_actor_name(dp.get("observer")),
                "target": review_view.short_actor_name(dp.get("target")), "perception": text})
    # 「ここまでの流れ」の材料: Trajectory/Foundation Contextがあればそれ、無ければ保存済みの
    # 局面の力学（dynamics）・何が変わったか・志向と行動のずれ。いずれも保存済み応答の既存内容。
    trajectory = presentation.get("trajectory") or {}
    executive = response_json.get("executive_summary") or {}
    dynamics = {k: val for k, val in {
        "trajectory": review_view.first_sentences(v(trajectory.get("summary")), 4, 600),
        "dynamics_of_the_situation": review_view.first_sentences(response_json.get("dynamics"), 5, 700),
        "what_changed": review_view.first_sentences(executive.get("what_changed"), 3, 400),
        "preference_vs_action": review_view.first_sentences(
            response_json.get("preference_action_gap_summary"), 3, 400),
    }.items() if val}
    return {
        "situation": review_view.first_sentences(v(kp.get("one_line_assessment")), 2, 300) or None,
        "initial_anomaly": review_view.first_sentences(v(kp.get("initial_anomaly")), 2, 260) or None,
        "key_point": review_view.first_sentences(v(kp.get("primary_key_point")), 1, 160) or None,
        "situation_dynamics": dynamics,
        "actors": actor_profiles,
        "relationships": relationships,
        "readings": [_reading_input_for_expert_review(c) for c in cards],
    }


def _inputs_fingerprint(inputs: dict) -> str:
    payload = {"prompt_version": EXPERT_REVIEW_PROMPT_VERSION, "inputs": inputs}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _format_expert_review_user_prompt(inputs: dict, flow_note: str | None = None) -> str:
    def dump(obj):
        return json.dumps(obj, ensure_ascii=False, indent=1)
    prompt = (
        f"# 局面（保存済みの分析の要約）\n{inputs.get('situation') or '（明示されていない）'}\n\n"
        f"# 最初の違和感\n{inputs.get('initial_anomaly') or '（明示されていない）'}\n\n"
        f"# 今回の鍵\n{inputs.get('key_point') or '（明示されていない）'}\n\n"
        f"# 局面の力学（保存済み。flowの材料。ここに無い経緯・因果は書かない）\n"
        f"{dump(inputs.get('situation_dynamics') or {})}\n\n"
        f"# Actor情報（保存済み。ここにある名前だけを主語にしてよい）\n{dump(inputs.get('actors') or [])}\n\n"
        f"# Actor間の関係・認識\n{dump(inputs.get('relationships') or [])}\n\n"
        f"# 対象Reading（全{len(inputs['readings'])}件、index順に全て出力すること）\n"
        + "\n".join(f"## Reading index={i}\n{dump(r)}" for i, r in enumerate(inputs["readings"]))
    )
    if flow_note:
        prompt += f"\n\n# flowの修正指示\n{flow_note}"
    return prompt


def _sentence_count(text: str) -> int:
    n = len(re.findall(r"[。！？]", text))
    return n if text.rstrip().endswith(("。", "！", "？")) else n + 1


def _new_fact_or_style_problem(text: str, label: str, corpus_lower: str) -> str | None:
    """内部用語・論文調・強い断定・入力に無い語/数値が混ざっていないか（title/comment/note/flow共通）。"""
    if review_view.has_forbidden_term(text):
        return f"{label}_forbidden_term"
    for phrase in _FORBIDDEN_PAPER_STYLE:
        if phrase in text:
            return f"{label}_paper_style:{phrase}"
    for phrase in _FORBIDDEN_ASSERTIVE:
        if phrase in text and phrase not in corpus_lower:
            return f"{label}_too_assertive:{phrase}"
    for token in re.findall(r"[A-Za-z][A-Za-z0-9\-\.]+", text):
        if token.lower() not in corpus_lower:
            return f"{label}_new_term:{token}"
    for number in re.findall(r"\d+(?:[.,]\d+)?", text):
        if number not in corpus_lower:
            return f"{label}_new_number:{number}"
    return None


def validate_expert_review_item(item: dict | None, corpus_lower: str) -> str | None:
    """LLM出力1件（title/comment）の検証。問題があれば理由文字列、問題なければNone。「新しい事実・
    Actor・数値を足していないか」「論文調/強い断定/内部用語が混ざっていないか」を機械的に確認する。"""
    if not item:
        return "missing"
    title, comment = (item.get("title") or "").strip(), (item.get("comment") or "").strip()
    if not (6 <= len(title) <= 75):
        return f"title_length={len(title)}"
    if not (90 <= len(comment) <= 320):
        return f"comment_length={len(comment)}"
    if comment.startswith(_FORBIDDEN_META_OPENINGS):
        return "comment_meta_opening"
    sentences = _sentence_count(comment)
    if not (2 <= sentences <= 5):
        return f"comment_sentences={sentences}"
    for label, text in (("title", title), ("comment", comment)):
        problem = _new_fact_or_style_problem(text, label, corpus_lower)
        if problem:
            return problem
    return None


def validate_flow(steps, corpus_lower: str) -> tuple[list | None, str | None]:
    """flow（ここまでの流れ）の検証。空配列は「材料が乏しいので出さない」というLLMの正当な回答として
    受け入れる（呼び出し元がDeterministic Fallbackを表示）。戻り値: (整えたsteps | None, NG理由)。"""
    if steps is None:
        return None, "flow_missing"
    cleaned = [s.strip() for s in steps if isinstance(s, str) and s.strip()]
    if not cleaned:
        return [], None
    if not (3 <= len(cleaned) <= 6):
        return None, f"flow_steps={len(cleaned)}"
    if len(set(cleaned)) != len(cleaned):
        return None, "flow_duplicate_steps"
    for s in cleaned:
        if not (8 <= len(s) <= 80):
            return None, f"flow_step_length={len(s)}"
        if re.search(r"[→↓⇒]|^\s*[0-9０-９]+[.．)）]", s):
            return None, "flow_step_has_marker"
        problem = _new_fact_or_style_problem(s, "flow", corpus_lower)
        if problem:
            return None, problem
    return cleaned, None


def clean_evidence_notes(item: dict | None, reading_input: dict, corpus_lower: str) -> list:
    """LLMが返したevidence_notesのうち、(a)入力に実在し(b)保存済みの理由が無い項目で(c)検証に通る
    ものだけを残す。通らないものは黙って捨てる（意味づけを出さないだけで、Evidence自体は不変）。"""
    allowed = {}
    for side, key in (("supporting", "supporting_materials"), ("contradicting", "contradicting_materials")):
        for m in reading_input.get(key) or []:
            if not m.get("saved_reason"):
                allowed[(side, m["i"])] = True
    notes, seen = [], set()
    for n in (item or {}).get("evidence_notes") or []:
        key = (n.get("side"), n.get("i"))
        note = (n.get("note") or "").strip()
        if key not in allowed or key in seen or not (18 <= len(note) <= 130):
            continue
        if _sentence_count(note) > 2 or _new_fact_or_style_problem(note, "note", corpus_lower):
            continue
        seen.add(key)
        notes.append({"side": key[0], "i": key[1], "note": note})
    return notes


def _corpus_lower(inputs: dict, user_prompt: str) -> str:
    return (user_prompt + " " + json.dumps(inputs, ensure_ascii=False)).lower()


# ---- キャッシュ（ファイル） ---------------------------------------------------------------
# キー=入力内容の指紋（EXPERT_REVIEW_PROMPT_VERSIONを含む）。同じ保存済み応答なら同じキーになり、
# Writing Guide/プロンプトを更新してversionを上げれば自動的に別キー（新しい版）になる。ファイル名に
# versionを含めるのは、人間が見て版を判別できるようにするため（検索はfingerprintで行う）。
# 保存先は環境変数GEO_EXPERT_REVIEW_CACHE_DIRで永続ボリューム等へ切り替えられる（既定はプロジェクト
# 直下のexpert_review_cache/、Docker imageへ同梱すればデプロイ後も事前生成分が残る）。

def _cache_path(fingerprint: str) -> Path:
    return _expert_review_cache_dir() / f"{EXPERT_REVIEW_PROMPT_VERSION}__{fingerprint}.json"


def _load_cache(fingerprint: str) -> dict | None:
    try:
        data = json.loads(_cache_path(fingerprint).read_text(encoding="utf-8"))
        if (data.get("fingerprint") == fingerprint
                and data.get("prompt_version") == EXPERT_REVIEW_PROMPT_VERSION
                and isinstance(data.get("readings"), list)):
            return data
    except (OSError, ValueError):
        pass
    return None


def _save_cache(fingerprint: str, readings: list, meta: dict, flow: list | None = None) -> None:
    directory = _expert_review_cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fingerprint, "prompt_version": EXPERT_REVIEW_PROMPT_VERSION,
               "created_at": datetime.now(timezone.utc).isoformat(), "meta": meta,
               "flow": flow, "readings": readings}
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp_path, _cache_path(fingerprint))
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _retry_note(reason: str) -> str:
    """検証NGの理由を、再試行時にLLMへ伝える短い指示文にする。"""
    if reason.startswith("comment_length="):
        n = int(reason.split("=")[1])
        return (f"前回のcommentは{n}字で長すぎました。120〜250字・3文以内に圧縮してください。"
                if n > 200 else f"前回のcommentは{n}字で短すぎました。120字以上にしてください。")
    if reason == "comment_meta_opening":
        return "前回は「ここでは」「この読みは」等の書き出しでした。観察・違和感・結論から書き出してください。"
    return f"前回の出力は検証に通りませんでした（{reason}）。入力にある語・数値だけで、指示どおりに書き直してください。"


def _call_expert_review_llm(azure_client, model, inputs: dict, only_indexes: list | None = None,
                            notes: dict | None = None, flow_note: str | None = None) -> dict:
    """1回のLLM呼び出し（title/comment/evidence_notes/flowを同時に出力）。
    only_indexes指定時は、そのindexのReadingだけを渡して再生成する（検証NGの再試行用）。
    notes（index→前回NG理由）があれば、そのReadingへ修正指示を添える。flow_noteがあればflowの
    修正指示を添える。戻り値: {"readings": {元index: item|None}, "flow": [str]|None}。"""
    call_inputs = inputs
    if only_indexes is not None:
        readings = []
        for i in only_indexes:
            reading = dict(inputs["readings"][i])
            if notes and notes.get(i):
                reading["previous_attempt_problem"] = _retry_note(notes[i])
            readings.append(reading)
        call_inputs = dict(inputs, readings=readings)
    user_prompt = _format_expert_review_user_prompt(call_inputs, flow_note)
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _EXPERT_REVIEW_SYSTEM_PROMPT, user_prompt,
        _EXPERT_REVIEW_SCHEMA, _EXPERT_REVIEW_SCHEMA_NAME)
    data = result["data"]
    by_index = {it.get("index"): it for it in data.get("readings", [])}
    mapping = only_indexes if only_indexes is not None else list(range(len(inputs["readings"])))
    return {"readings": {orig: by_index.get(local) for local, orig in enumerate(mapping)},
            "flow": data.get("flow")}


def _finalize_reading(item: dict, reading_input: dict, corpus: str) -> dict:
    return {"title": item["title"].strip(), "comment": item["comment"].strip(),
            "pattern": item.get("pattern"),
            "evidence_notes": clean_evidence_notes(item, reading_input, corpus)}


def get_or_build_expert_review_comments(azure_client, model, *, presentation: dict, response_json: dict,
                                         cards: list, meta: dict | None = None,
                                         allow_generate: bool = True) -> dict:
    """cards（presentationのdisplayedカード、expert_review付き）に対応するtitle/comment/証拠メモと、
    局面全体の「ここまでの流れ」を返す。1回のLLM呼び出しで全て生成する（LLM経路は1本）。

    戻り値: {"comments": [{"title","comment","pattern","evidence_notes"} | None, ...](cardsと同順),
    "flow": [str] | None, "status": "cache_hit" | "generated" | "completed" | "pending",
    "failures": {index: reason}}。flowが[]なら「材料が乏しく出さない」、Noneなら未生成/検証NG
    （どちらも呼び出し元がDeterministic Fallbackを表示）。
    キャッシュ済みなら新規LLM呼び出しは行わない。allow_generate=Falseでキャッシュが無い場合は、
    LLMを呼ばずstatus="pending"を返す（画面表示のたびにLLM（生成に数十秒〜数分かかり得る）を待たせ
    ないため、GETはこちらを使う）。LLM呼び出し自体が例外の場合はそのまま例外を伝播する（呼び出し元が
    Fallback、この場合はキャッシュしない＝次回再試行）。"""
    if not cards:
        return {"comments": [], "flow": None, "status": "empty", "failures": {}}
    inputs = build_expert_review_inputs(presentation, response_json, cards)
    fingerprint = _inputs_fingerprint(inputs)
    cached = _load_cache(fingerprint)
    cached_ok = cached is not None and len(cached["readings"]) == len(cards)
    # POST/スクリプト（allow_generate）の時だけ、過去に検証NGでNoneだったReading/flowを補完する
    # （既にキャッシュ済みの良い出力は変更せず、欠けたものだけ修正指示付きで再生成）。
    incomplete = cached_ok and allow_generate and (
        any(c is None for c in cached["readings"]) or cached.get("flow") is None)
    if cached_ok and not incomplete:
        return {"comments": cached["readings"], "flow": cached.get("flow"), "status": "cache_hit",
                "failures": {}, "fingerprint": fingerprint}
    if not allow_generate:
        return {"comments": [None] * len(cards), "flow": None, "status": "pending", "failures": {},
                "fingerprint": fingerprint}

    corpus = _corpus_lower(inputs, _format_expert_review_user_prompt(inputs))
    flow = None
    llm_calls = 0  # このキャッシュ生成で実際に行ったLLM呼び出し回数（運用観測用）
    if incomplete:
        final = {i: c for i, c in enumerate(cached["readings"]) if c is not None}
        failures = {i: "cached_missing" for i, c in enumerate(cached["readings"]) if c is None}
        flow = cached.get("flow")
        flow_reason = "cached_missing" if flow is None else None
    else:
        raw = _call_expert_review_llm(azure_client, model, inputs)
        llm_calls += 1
        final, failures = {}, {}
        for i in range(len(cards)):
            item = raw["readings"].get(i)
            reason = validate_expert_review_item(item, corpus)
            if reason:
                failures[i] = reason
            else:
                final[i] = _finalize_reading(item, inputs["readings"][i], corpus)
        flow, flow_reason = validate_flow(raw["flow"], corpus)
    for _attempt in range(_EXPERT_REVIEW_MAX_RETRIES):
        if not failures and not flow_reason:
            break
        llm_calls += 1
        retry = _call_expert_review_llm(
            azure_client, model, inputs, only_indexes=sorted(failures), notes=dict(failures),
            flow_note=(f"前回のflowは検証に通りませんでした（{flow_reason}）。3〜6ステップ、1文25〜60字、"
                       "矢印・番号なし、入力に無い語・数値・因果なしで書き直すか、材料が乏しければ空配列。"
                       if flow_reason else None))
        for i in list(failures):
            item = retry["readings"].get(i)
            reason = validate_expert_review_item(item, corpus)
            if reason is None:
                final[i] = _finalize_reading(item, inputs["readings"][i], corpus)
                del failures[i]
            else:
                failures[i] = reason
        if flow_reason:
            flow, flow_reason = validate_flow(retry["flow"], corpus)
    comments = [final.get(i) for i in range(len(cards))]
    if failures or flow_reason:
        _logger.warning("expert_review validation rejected: readings=%s flow=%s", failures, flow_reason)
    if any(comments):
        _save_cache(fingerprint, comments,
                    dict(meta or {}, failures={str(k): v for k, v in failures.items()},
                         flow_failure=flow_reason), flow=None if flow_reason else flow)
    return {"comments": comments, "flow": None if flow_reason else flow,
            "status": "completed" if incomplete else "generated",
            "failures": failures, "fingerprint": fingerprint, "llm_calls": llm_calls}
