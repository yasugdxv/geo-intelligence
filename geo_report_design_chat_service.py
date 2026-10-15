"""Report Studio「Design」領域: Design Chat（Phase C）。

`ReportDesignExpertAdapter`（geo_report_design_expert_adapter.py）越しにExpert AIの
部品（Knowledge/Retrieval）を使い、`geo_llm_common.call_llm_structured`で1ターン
1コールの構造化応答を得る。会話は`geo_report_design_chat_turns`へ永続化し（サーバー側
保存、要件通り）、Design State（`geo_report_design_state`）への反映は
`geo_report_project_assets_service.save_design_state`を通す。

Signal単位のNarrative Master生成・Report Template骨格生成もここに置く（いずれも
Design State/Design Guidanceを入力に1コールで完結させる、既存Synthesisと同じ
「1呼び出し=1判断単位」の粒度）。

Template Extraction（`extract_template_from_report`）＋決定的Leakage Scan
（`scan_template_for_old_content_leakage`）も本モジュールが担う（LLM呼び出しを
含む処理はDesign Chat側、というPlanの責務分担に合わせる。Leakage Scan自体は
LLMを使わないが、Extractionと対で使うためここに置く）。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Optional

import geo_llm_common
import geo_report_current_pipeline_service as cp
import geo_report_project_assets_service as assets
import geo_report_prompt2_composition_service as composition
import geo_report_run_provisioning_service as provisioning
from geo_report_design_expert_adapter import GeoExpertDesignAdapter, ReportDesignExpertAdapter

CHAT_TURN_SCHEMA_NAME = "geo_report_design_chat_turn"
NARRATIVE_MASTER_GENERATION_SCHEMA_NAME = "geo_report_narrative_master_generation"
TEMPLATE_EXTRACTION_SCHEMA_NAME = "geo_report_template_extraction"
REPORT_TEMPLATE_GENERATION_SCHEMA_NAME = "geo_report_template_generation"

_REPORT_DESIGN_FIELDS = [
    "purpose", "decision_use_case", "audience", "region_scope", "period_scope", "cadence",
    "central_hypothesis",
]
_REPORT_DESIGN_LIST_FIELDS = [
    "key_questions", "analytical_axes", "comparison_axes", "drivers", "stakeholders",
    "monitoring_points", "assumptions", "primary_graph_signals",
]

CHAT_TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "assistant_reply": {"type": "string"},
        "report_design_patch": {
            "type": "object",
            "properties": {
                **{f: {"type": ["string", "null"]} for f in _REPORT_DESIGN_FIELDS},
                **{f: {"type": ["array", "null"], "items": {"type": "string"}} for f in _REPORT_DESIGN_LIST_FIELDS},
                "common_primary_graph_start": {"type": ["string", "null"]},
                "analysis_mode": {
                    "type": ["string", "null"],
                    "enum": ["public_voice_monitor", "evidence_intelligence", None],
                    "description": "2026-09-15追加、外部監査で発覚したContract mismatchへの対応: "
                                    "このプロジェクトのレポートが一般大衆の声・世論（Public Voice/"
                                    "Sentiment、SNS等の投稿を主対象）を扱う定点モニタリングなら"
                                    "'public_voice_monitor'。政府・議会・産業界・専門家・主要メディア等の"
                                    "発言・立場・バーゲニングポジションを一次情報として扱う地政学"
                                    "インテリジェンス分析なら'evidence_intelligence'。後者では"
                                    "Public Voice sentimentを集計の分母にしない。まだ確認していない"
                                    "場合はnullのままにし、assistant_reply内で確認すること。",
                },
            },
            "additionalProperties": False,
        },
        "signals": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "signal_id": {"type": "string"},
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                    "what_to_capture": {"type": "string"},
                    "expected_public_voice": {"type": "string"},
                    "risk_or_relevance": {"type": "string"},
                    "continuity_role": {"type": "string", "enum": ["Core", "Event", "Watch", "Context"]},
                    "status": {"type": "string"},
                    "discovery_mode": {
                        "type": "boolean",
                        "description": "2026-09-16追加、外部監査指摘への対応: このSignalが"
                                        "「既知のNarrativeへ分類する」のではなく「S1〜S5等の既存Signalに"
                                        "分類されない未知の新規論点をBroad Corpusから発見する」ことを"
                                        "目的とする場合はtrue（例: Emerging/Surprise Issue用のSignal）。"
                                        "true の場合、Preprocessはkeyword matchingをPrimary routingに"
                                        "使わず、dedup/coarse clustering/noise reductionのみ行い、"
                                        "既存Narrativeへのfallback率をQA failureの単独理由にしない。"
                                        "通常のSignal（既知テーマへの分類が目的）はfalse。",
                    },
                },
                "required": ["signal_id", "name", "purpose", "what_to_capture", "expected_public_voice",
                             "risk_or_relevance", "continuity_role", "status", "discovery_mode"],
                "additionalProperties": False,
            },
        },
        "queries": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "signal_id": {"type": "string"},
                    "report_topic_label": {"type": "string"},
                    "fixed_query_group_a": {"type": "array", "items": {"type": "string"}},
                    "fixed_query_group_b": {"type": "array", "items": {"type": "string"}},
                    "event_terms": {"type": "array", "items": {"type": "string"}},
                    "exclude_terms": {"type": "array", "items": {"type": "string"}},
                    "continuity_role": {"type": "string"},
                    "collection_notes": {"type": "string"},
                },
                "required": ["signal_id", "report_topic_label", "fixed_query_group_a", "fixed_query_group_b",
                             "event_terms", "exclude_terms", "continuity_role", "collection_notes"],
                "additionalProperties": False,
            },
        },
        "mentioned_entity_names": {"type": "array", "items": {"type": "string"}},
        "clarifying_questions": {"type": "array", "items": {"type": "string"}},
        "narrative_master_generation_requested": {
            "type": "boolean",
            "description": "ユーザーがsignal_narrative_master.csv（Narrative Master）の生成・作成を"
                            "依頼した場合のみtrue。trueの場合、assistant_reply内にCSV本文やNarrative行の"
                            "詳細を書き出さないこと（実際の生成・保存は別処理が担当するため、ここで"
                            "テキストとして書いても保存されずユーザーを混乱させる）。",
        },
        "report_template_generation_requested": {
            "type": "boolean",
            "description": "ユーザーがReport Template（章立て・構成）の生成・作成・修正を依頼した"
                            "場合のみtrue。trueの場合、assistant_reply内にHTML/CSSやSection定義の"
                            "詳細JSONを書き出さないこと（実際の生成・保存・プレビューURL案内は別処理が"
                            "担当するため、ここで内容を書いても保存されずユーザーを混乱させる）。",
        },
    },
    "required": ["assistant_reply", "report_design_patch", "signals", "queries",
                 "mentioned_entity_names", "clarifying_questions",
                 "narrative_master_generation_requested", "report_template_generation_requested"],
    "additionalProperties": False,
}

NARRATIVE_MASTER_GENERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narrative_id": {"type": "string"},
                    "narrative_name": {"type": "string"},
                    "include_keywords": {
                        "type": "string",
                        "description": "複数フレーズはセミコロン(;)区切りで並べること。"
                                        "カンマ(,)で区切ってはいけない——Shellのマッチング処理は"
                                        "セミコロンのみを区切り文字として認識するため、カンマ区切りだと"
                                        "全体が1つの巨大な文字列として扱われ、絶対にマッチしなくなる。"
                                        "例: 'stabilization; guardrails; managed competition'",
                    },
                    "exclude_keywords": {
                        "type": "string",
                        "description": "include_keywordsと同様、複数フレーズはセミコロン(;)区切り。カンマ禁止。",
                    },
                    "required_context_keywords": {
                        "type": "string",
                        "description": "include_keywordsと同様、複数フレーズはセミコロン(;)区切り。カンマ禁止"
                                        "（この列が壊れると、そのSignal全体が100% fallbackになる）。",
                    },
                    "public_voice_cues": {"type": "string"},
                    "cluster_split_terms": {
                        "type": "string",
                        "description": "include_keywordsと同様、複数フレーズはセミコロン(;)区切り。カンマ禁止。",
                    },
                    "tone_hint": {"type": "string"},
                    "public_voice_hint": {"type": "string"},
                    "report_use_hint": {"type": "string"},
                    "continuity_hint": {"type": "string"},
                    "notes": {"type": "string"},
                    "is_fallback": {"type": "boolean"},
                    "priority": {"type": "string", "enum": ["High", "Medium", "Low"]},
                },
                "required": ["narrative_id", "narrative_name", "include_keywords", "exclude_keywords",
                             "required_context_keywords", "public_voice_cues", "cluster_split_terms",
                             "tone_hint", "public_voice_hint", "report_use_hint", "continuity_hint",
                             "notes", "is_fallback", "priority"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rows"],
    "additionalProperties": False,
}

TEMPLATE_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "style_css": {"type": "string"},
        "shell_html": {
            "type": "string",
            "description": "元HTMLの外側構造（<html><head>...<body>...）から、本文コンテンツ部分を"
                            "'{sections}'というプレースホルダ文字列1つに置き換えたもの。<style>の中身は"
                            "style_cssと重複してよい。実データ(数値・固有名詞・URL・日付・引用文)は"
                            "一切含めないこと。",
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "title": {"type": "string"},
                    "order": {"type": "integer"},
                    "component_type": {"type": "string", "enum": sorted(assets.TEMPLATE_COMPONENT_TYPES)},
                    "required_fields": {"type": "array", "items": {"type": "string"}},
                    "example_markup": {
                        "type": "string",
                        "description": "このSectionの元のCSSクラス構造をそのまま保った、プレースホルダの"
                                       "例示マークアップ。実データは一切含めないこと。",
                    },
                },
                "required": ["section_id", "title", "order", "component_type", "required_fields",
                             "example_markup"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["style_css", "shell_html", "sections"],
    "additionalProperties": False,
}

# Leakage Scanが検出対象とする「実データらしさ」の決定的パターン（LLMを使わない、
# Plan I.2「Python Leakage Scan」）。URL・日付・2桁以上の具体的数値・引用符付き文字列。
_LEAKAGE_URL_RE = re.compile(r'https?://[^\s"\'<>]+')
# 日本語文中では数値・日付の前後に空白/句読点が無いことが多く、\bはUnicode上
# 平仮名等も\w扱いのため機能しない（例: 「62%が」の直後で境界判定に失敗する）。
# 代わりに数字が連続しないことだけを(?<!\d)/(?!\d)で保証する。
_LEAKAGE_DATE_RE = re.compile(r'(?<!\d)(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?)(?!\d)')
_LEAKAGE_NUMBER_RE = re.compile(r'(?<!\d)\d{2,}(?:[.,]\d+)?%?(?!\d)')
# プレーンな二重引用符(")はHTML属性(class="...")にも大量に現れ、無関係な区間を
# 跨いで誤マッチするため対象に含めない（日本語のカギ括弧のみを対象にする）。
_LEAKAGE_QUOTE_RE = re.compile(r'[「『][^「『」』]{8,}[」』]')

# <style>...</style>のCSS宣言値（font-size/line-height/opacity/border-radius等の数値、
# 例: 10.5px, 100%）は、Report Template Assetのstyle_cssへ意図的にそのままコピーされる
# （Plan I.1）。これを漏洩候補に含めると、正しくコピーされたCSSが毎回「実データ漏洩」として
# 誤検知される（本文の実データではなくデザイン値であり、保護対象ではない）。
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)


def _extract_leakage_candidates(html: str) -> set:
    body_only = _STYLE_BLOCK_RE.sub("", html)
    candidates = set()
    candidates.update(_LEAKAGE_URL_RE.findall(body_only))
    candidates.update(_LEAKAGE_DATE_RE.findall(body_only))
    candidates.update(_LEAKAGE_NUMBER_RE.findall(body_only))
    candidates.update(_LEAKAGE_QUOTE_RE.findall(body_only))
    return {c for c in candidates if len(c) >= 3}


def scan_template_for_old_content_leakage(original_html: str, extracted_template: dict) -> dict:
    """Extraction結果（Section定義＋example_markup＋shell_html）に、元HTMLの実データ
    （URL・日付・具体的数値・引用文）がそのまま残っていないかを機械的に照合する
    （Plan I.2、LLM不使用）。PASSしない限りTemplate Draftを作らない前提の呼び出し元
    ゲートとして使う。"""
    candidates = _extract_leakage_candidates(original_html)
    if not candidates:
        return {"passed": True, "leaked_items": []}
    template_text = json.dumps(extracted_template, ensure_ascii=False)
    leaked = sorted(c for c in candidates if c in template_text)
    return {"passed": not leaked, "leaked_items": leaked}


def extract_template_from_report(client, azure_client, model: str, final_report_html: str, *,
                                  adapter: Optional[ReportDesignExpertAdapter] = None) -> dict:
    """既存final_report.htmlから、CSS/Section骨格＋見本マークアップを抽出する
    （実データはプレースホルダへ置換、Plan I.2）。戻り値には決定的Leakage Scanの結果
    （`leakage_scan`）を必ず含める——呼び出し元は`leakage_scan["passed"]`がTrueの場合
    のみTemplate Draftとして保存してよい（FAILの場合は保存せず、再抽出するか
    人間レビューへ回す）。"""
    adapter = adapter or GeoExpertDesignAdapter()
    system_prompt = (
        "あなたはReport Studioの「Template Extraction」を担当する専門家AIです。"
        "与えられたfinal_report.htmlから、CSS・章立て（Section）構造・各Sectionの見本"
        "マークアップを抽出してください。\n\n"
        "絶対厳守: 数値・固有名詞（人名・組織名・地名等）・URL・日付・本文中の引用文といった"
        "実データは一切残さないこと。構造・デザイン・書式（見出しレベル・クラス名・レイアウト）"
        "だけを抽出し、内容は「ここに○○を書く」のような一般的なプレースホルダ説明に置き換える"
        "こと。\n\n"
        f"# Expert Knowledge\n{adapter.build_knowledge_context()}"
    )
    user_prompt = f"以下のfinal_report.htmlからTemplateを抽出してください:\n\n{final_report_html}"
    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, TEMPLATE_EXTRACTION_SCHEMA,
        TEMPLATE_EXTRACTION_SCHEMA_NAME)

    extracted = {
        "style_css": result["data"]["style_css"],
        "shell_html": result["data"]["shell_html"],
        "sections": result["data"]["sections"],
    }
    leakage_scan = scan_template_for_old_content_leakage(final_report_html, extracted)
    return {"template": extracted, "leakage_scan": leakage_scan}


REPORT_TEMPLATE_GENERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "title": {"type": "string"},
                    "order": {"type": "integer"},
                    "component_type": {"type": "string", "enum": sorted(assets.TEMPLATE_COMPONENT_TYPES)},
                    "required_fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["section_id", "title", "order", "component_type", "required_fields"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sections"],
    "additionalProperties": False,
}

# 新規プロジェクトでゼロから生成する場合の最小限のCSS/Shell（AIには書かせない、
# Plan I.1/I.3「Composition AIはHTMLを書かない」原則をTemplate骨格生成にも適用する。
# 実運用時はI.2 Template Extraction — 実際のfinal_report.htmlからの抽出 — で
# 案件固有のCSS/構造へ置き換わる想定）。
_DEFAULT_STYLE_CSS = (
    "<style>.page{max-width:960px;margin:0 auto;font-family:sans-serif}"
    ".kicker{font-size:12px;color:#666}.title{font-size:28px;font-weight:700}"
    ".card{border:1px solid #ddd;border-radius:8px;padding:16px;margin:12px 0}</style>"
)
_DEFAULT_SHELL_HTML = '<html><head>{style}</head><body><div class="page">{sections}</div></body></html>'


def _new_id() -> str:
    return str(uuid.uuid4())


def list_turns(client, project_id: str) -> list:
    return client.select("geo_report_design_chat_turns", {
        "select": "*", "project_id": f"eq.{project_id}", "order": "turn_no.asc",
    })


def _next_turn_no(client, project_id: str) -> int:
    turns = list_turns(client, project_id)
    return max((t["turn_no"] for t in turns), default=0) + 1


def append_turn(client, project_id: str, turn_no: int, role: str, content: str, *,
                 applied_diff: Optional[dict] = None, llm_usage: Optional[dict] = None,
                 latency_ms: Optional[int] = None) -> dict:
    payload = {
        "id": _new_id(), "project_id": project_id, "turn_no": turn_no, "role": role, "content": content,
        "applied_diff": applied_diff, "llm_usage": llm_usage, "latency_ms": latency_ms,
    }
    inserted = client.insert("geo_report_design_chat_turns", [payload])
    return inserted[0] if inserted else payload


def _mentioned_entity_names_from_design_state(design_state: dict) -> list:
    """直近までのDesign Stateで既に確認済みのstakeholdersを、次のRetrievalの手がかりに
    使う（今回のuser_messageから抽出するのではなく、確定済みDesign Stateを根拠にする
    ——初回ターンはstakeholders未設定なのでRetrievalは空になり、対話が進むにつれて
    自然にActor Intelligenceが効いてくる設計）。"""
    return list((design_state.get("report_design") or {}).get("stakeholders") or [])


def _build_system_prompt(adapter: ReportDesignExpertAdapter, design_state: dict, retrieved_context: str) -> str:
    knowledge = adapter.build_knowledge_context()
    guidance = adapter.build_design_guidance()
    parts = [
        "あなたはReport Studioの「Design Chat」を担当する専門家AIです。"
        "ユーザーと自然言語で対話しながら、定点レポートの設計"
        "（Report Design / Signal設計 / Query提案）をFIXしていきます。",
        f"# Expert Knowledge\n{knowledge}",
        f"# Report Design Framework（Prompt0）\n{guidance}",
    ]
    if retrieved_context:
        parts.append(f"# 関連するActor/Media Intelligence\n{retrieved_context}")
    parts.append(f"# 現在のDesign State（Working Draft、まだFIXされていない提案を含む）\n"
                 f"{json.dumps(design_state, ensure_ascii=False, indent=2)}")
    parts.append(
        "出力は指定されたJSON Schemaに厳密に従ってください。今回の対話で変更しない項目は"
        "null（配列の場合、変更しないならnull、明示的に空にしたいなら空配列）にしてください。\n"
        "2026-09-15追加: このDesign StateはまだFIXされていないWorking Draftです"
        "（ユーザーが画面の「FIX」ボタンを押すまでは、ここでの変更は正式なDesignとして"
        "保存されません）。assistant_replyには、report_design_patch/signals/queriesの"
        "生JSONをそのまま書き出さないでください。代わりに、今回の変更を踏まえた"
        "「現在の提案内容」の要点（目的・対象読者・主要Signal・論点など）を、ユーザーが"
        "読んで議論・修正しやすい自然な日本語の文章（箇条書き可）でまとめてください。"
        "ユーザーが内容に納得したら「FIX」ボタンを押して確定する、という前提の"
        "受け答えにしてください。\n"
        "report_design_patch.primary_graph_signalsには、既存Signal一覧のsignal_id"
        "（例:'S1'）のみを含めてください。Signal名や説明文をここへ書かないでください"
        "（このフィールドはPythonの集計処理が完全一致でsignal_idと突き合わせるため、"
        "説明文が入ると集計が0件になります）。**重要**: primary_graph_signalsは"
        "ユーザーが明示的に言及しない限り、いつまでもnullのままになりがちですが、"
        "これが未設定のままFIX・Publishされると、後でRunを作った際に"
        "Aggregation（集計・グラフ生成）が失敗します。Signal一覧が一通り固まった"
        "タイミング（新規追加や大きな変更が無くなった頃）で、まだprimary_graph_signals"
        "が未設定なら、assistant_reply内で「グラフの主軸にするSignalはどれですか"
        "（例: 全SignalかS1〜S3のみか等、またはこのレポートではweekly/biweeklyの"
        "定量グラフを使わない、という選択肢もあります）」と必ず確認してください。"
        "ユーザーから回答があれば、そのsignal_idをここへ設定してください。"
        "ユーザーが「グラフは使わない」「定量集計は不要」のように明確に不要と答えた"
        "場合は、primary_graph_signalsへ**空配列`[]`を明示的に設定**してください"
        "（nullのまま放置しないこと——nullは『まだ確認していない』、空配列`[]`は"
        "『確認した上でグラフ機能を使わないと決めた』という別の意味を持ちます。"
        "2026-09-15実機報告: この区別が無いと、アプリ側の安全装置が『未確認』と"
        "誤判定し、勝手にSignalを補完してしまう不具合がありました）。\n"
        "report_design_patch.common_primary_graph_startは、必ずYYYY-MM-DD形式の"
        "日付文字列のみにしてください（例:'2026-09-01'）。'Pre-summit'のようなラベルや"
        "説明文は書かないでください（このフィールドはDB上date型カラムへそのまま保存され、"
        "日付として解釈できない値だとRun作成時にエラーになります）。日付が確定していない"
        "場合はnullのままにし、assistant_reply内で確認してください。\n"
        "**重要**: report_design_patch.analysis_modeは、目的・対象読者・主要な入力ソースが"
        "一通り固まったタイミングで必ず確認してください。まだnullのままなら、"
        "assistant_reply内で「このレポートは一般大衆の声・世論（SNS等の投稿）を主対象に"
        "する定点モニタリングですか、それとも政府・議会・産業界・専門家・主要メディア等の"
        "発言・立場を一次情報として扱う地政学インテリジェンス分析ですか」と確認し、"
        "回答に応じて'public_voice_monitor'または'evidence_intelligence'を設定してください。"
        "この判断はPrompt1が生成するEvidence契約（Public Voice sentiment分類 vs "
        "Actor/Evidence構造分析）を丸ごと切り替えるため、未確認のままFIX・Publishされると、"
        "実行時に生成されたPrompt1 Packageの前提（report_title・分母ルール）が"
        "レポートの実際の目的と食い違うContract mismatchが起きます。\n"
        "各Signalのdiscovery_modeは、そのSignalが「既知テーマへの分類」ではなく"
        "「S1〜S5等の既存Signalに分類されない未知の新規論点をBroad Corpusから発見する」"
        "ことを目的とする場合のみtrueにしてください（例:「Emerging Issue」"
        "「Surprise Issue」「その他・想定外」のようなSignal）。通常のSignal"
        "（特定の既知テーマ・立場・論調を追跡するもの）はfalseにしてください。"
        "discovery_mode=trueのSignalは、Preprocessでkeyword matchingが使われず"
        "fallback率が高くなることが構造的に正常であるため、Publish前にユーザーへ"
        "「このSignalは新規論点発見用（discovery_mode）にしますか、それとも通常の"
        "分類用にしますか」と一度確認してください。\n"
        "ユーザーが「ナラティブマスターを作って/生成して」のように"
        "signal_narrative_master.csvの生成を依頼した場合は、"
        "narrative_master_generation_requested=trueにしてください。この場合、"
        "assistant_replyにはCSVの内容やNarrative行の詳細を書かず、"
        "「現在登録されている全Signal分のNarrative Masterを生成します」といった"
        "短い受け答えのみにしてください（実際の生成はこのフラグを見て別処理が行うため、"
        "ここで内容を書いてもDraftへは保存されません）。\n"
        "ユーザーが「テンプレート作って/構成を提案して/この章をこう直して」のように"
        "Report Template（章立て・構成）の生成・修正を依頼した場合は、"
        "report_template_generation_requested=trueにしてください。この場合、"
        "assistant_replyにはHTML/CSSやSection定義のJSONを書かず、"
        "「Report Templateの構成を生成/更新します」といった短い受け答えのみに"
        "してください（実際の生成・保存・プレビューURLの案内は別処理が行います）。"
    )
    return "\n\n".join(parts)


# report_design内でDB上date型カラムへそのまま書き込まれるフィールド（J.3の
# common_primary_graph_start date）。ここ以外のreport_designフィールドは自由記述文字列。
_DATE_FIELD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REPORT_DESIGN_DATE_FIELDS = ("common_primary_graph_start",)


def _reject_invalid_date_fields(patch: dict) -> dict:
    """2026-09-14発覚: AIがcommon_primary_graph_startへ"Pre-summit"のような自然文
    ラベルを書き込み、Run作成時にPostgreSQLのdate型カラムへ挿入しようとして
    "invalid input syntax for type date"で失敗、ブラウザには「Failed to fetch」としか
    見えず原因が分からなかった。YYYY-MM-DD形式でない値は保存前にここで弾き、
    理由をrejected_fieldsとして呼び出し元へ返す（patchは破壊的に変更する）。"""
    rejected = {}
    for field in _REPORT_DESIGN_DATE_FIELDS:
        if field in patch and not _DATE_FIELD_RE.match(str(patch[field])):
            rejected[field] = patch.pop(field)
    return rejected


def _effective_design_state(design_state: dict) -> dict:
    """confirmed値とpending提案をマージした「現在の実効的な作業中Design State」を返す
    （2026-09-15、提案→FIXワークフロー）。AIへの表示・Retrieval双方でこれを使うことで、
    pending_*へ保存するようになった後もチャットの継続性（前ターンで提案した内容を
    次ターンでも認識できること）を保つ——pendingがあればそれを優先し、まだこの
    セッションで触れていない項目のみconfirmed値を使う。"""
    pending_report_design = design_state.get("pending_report_design")
    pending_signals = design_state.get("pending_signals")
    pending_queries = design_state.get("pending_queries")
    return {
        "project_id": design_state.get("project_id"),
        "report_design": pending_report_design if pending_report_design is not None
        else (design_state.get("report_design") or {}),
        "signals": pending_signals if pending_signals is not None else design_state.get("signals"),
        "queries": pending_queries if pending_queries is not None else design_state.get("queries"),
        "has_unconfirmed_changes": bool(
            pending_report_design is not None or pending_signals is not None or pending_queries is not None),
    }


def process_chat_turn(client, azure_client, model: str, project_id: str, user_message: str, *,
                       adapter: Optional[ReportDesignExpertAdapter] = None) -> dict:
    adapter = adapter or GeoExpertDesignAdapter()
    design_state = assets.get_design_state(client, project_id)
    effective_state = _effective_design_state(design_state)
    mentioned_names = _mentioned_entity_names_from_design_state(effective_state)
    retrieved_context = adapter.retrieve_context(client, mentioned_names)
    system_prompt = _build_system_prompt(adapter, effective_state, retrieved_context)

    user_turn_no = _next_turn_no(client, project_id)
    append_turn(client, project_id, user_turn_no, "user", user_message)

    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_message, CHAT_TURN_SCHEMA, CHAT_TURN_SCHEMA_NAME)
    data = result["data"]

    # 2026-09-15変更: チャットの提案はconfirmed列を直接書き換えず、pending_*へのみ
    # 保存する（ユーザーが「FIX」ボタンを押すまで正式なDesignにはならない）。
    patch = {k: v for k, v in (data.get("report_design_patch") or {}).items() if v is not None}
    rejected_fields = _reject_invalid_date_fields(patch)
    new_report_design = {**(effective_state.get("report_design") or {}), **patch}
    signals_replaced = data.get("signals") is not None
    queries_replaced = data.get("queries") is not None
    # 現時点で実効的なsignals/queries（narrative_master_generation_requested処理など、
    # このターン内で使うためのもの）。pendingへの保存はこれとは別に、今回変更が
    # 無かった項目にはNoneを渡す（save_pending_design_stateはNoneのフィールドを
    # 更新しないため）——effective値をそのまま渡すと、変更していないのに空リスト等で
    # pendingへ書き込まれ、FIX時に既存confirmed値を誤って上書きしてしまうバグになる
    # （2026-09-15発覚）。
    effective_signals = data["signals"] if signals_replaced else effective_state.get("signals")
    effective_queries = data["queries"] if queries_replaced else effective_state.get("queries")

    assets.save_pending_design_state(
        client, project_id, report_design=new_report_design,
        signals=data["signals"] if signals_replaced else None,
        queries=data["queries"] if queries_replaced else None,
    )

    applied_diff = {
        "report_design_patch": patch, "signals_replaced": signals_replaced, "queries_replaced": queries_replaced,
    }
    if rejected_fields:
        applied_diff["rejected_fields"] = rejected_fields

    narrative_master_generation = None
    if data.get("narrative_master_generation_requested"):
        narrative_master_generation = _generate_narrative_master_for_all_signals(
            client, azure_client, model, project_id, effective_signals or [], adapter=adapter)
        applied_diff["narrative_master_generation"] = narrative_master_generation

    # 2026-09-16追加: チャットでの生成・修正依頼はTemplate Draftへ自動保存する
    # （ユーザー要望:「テンプレートもチャット上でHTMLリンクをだして、内容やり取りした
    # あとに保存する」）。Narrative Masterと同様、チャットでの依頼＝即座にDraftへ反映し、
    # 明示的な保存操作を挟まない（Draftなので、後で気に入らなければ再度チャットで
    # 修正依頼すればそのまま上書きされる）。
    report_template_generation = None
    if data.get("report_template_generation_requested"):
        report_template_generation = _generate_and_save_report_template(
            client, azure_client, model, project_id, user_message, adapter=adapter)
        applied_diff["report_template_generation"] = report_template_generation

    append_turn(
        client, project_id, user_turn_no + 1, "assistant", data["assistant_reply"],
        applied_diff=applied_diff, llm_usage=result.get("usage"), latency_ms=result.get("latency_ms"),
    )

    return {
        "assistant_reply": data["assistant_reply"],
        "clarifying_questions": data.get("clarifying_questions", []),
        "mentioned_entity_names": data.get("mentioned_entity_names", []),
        "design_state": assets.get_design_state(client, project_id),
        "applied_diff": applied_diff,
        "narrative_master_generation": narrative_master_generation,
        "report_template_generation": report_template_generation,
    }


def fix_design(client, azure_client, model: str, project_id: str, *,
               adapter: Optional[ReportDesignExpertAdapter] = None) -> dict:
    """Design Chatの「FIX」ボタン用エントリポイント（2026-09-15、ユーザー要望）。
    チャットで提案されたreport_design/signals/queries（pending_*）を確定させ、
    確定したSignal全件分のNarrative Masterを自動生成・保存する
    （ユーザー要望:「(Designが)FIXしたらナラティブマスターもFIXできるから
    自動で作成して保存する」）。Signalが1件も無い場合は生成をスキップする。
    2026-09-16追加: FIX結果として、設計/Signal/Narrative Master/Query/Templateの
    ダウンロードURL（相対パス）を返す（ユーザー要望:「チャット内でFIXしたという
    確認をとったら、下部にFIXした設計、シグナル、ナラティブマスター、クエリ、
    テンプレートのダウンロードリンクがでる」）。まだ存在しないアセット
    （Narrative Master/Template）はリンクに含めない（404になるリンクを出さない）。"""
    design_state = assets.fix_design_state(client, project_id)
    signals = design_state.get("signals") or []
    narrative_master_generation = None
    if signals:
        narrative_master_generation = _generate_narrative_master_for_all_signals(
            client, azure_client, model, project_id, signals, adapter=adapter)

    base = f"/api/geo/report-studio/projects/{project_id}"
    downloads = {
        "report_design": f"{base}/design/report-design/download",
        "signals": f"{base}/design/signals/download",
        "queries": f"{base}/design/queries/export",
    }
    if assets.get_draft(client, project_id, "narrative_master"):
        downloads["narrative_master"] = f"{base}/assets/narrative-master/download?status=draft"
    if assets.get_draft(client, project_id, "report_template"):
        downloads["report_template"] = f"{base}/assets/report-template/download?status=draft"

    # 2026-09-16追加: primary_graph_signals/common_primary_graph_startがDesignで
    # 一度も設定されないままFIX（→Publish→Run作成）されると、Run作成後の
    # Aggregation実行で初めて「Calculating trendsが失敗します」という分かりにくい
    # エラーに気づくことになり、同種のユーザー報告が繰り返し発生した。FIXの時点で
    # 早期に警告する（FIX自体はブロックしない——グラフを使わない探索的な設計も
    # あり得るため）。
    report_design = design_state.get("report_design") or {}
    valid_signal_ids = {s.get("signal_id") for s in signals if s.get("signal_id")}
    # primary_graph_signalsは`or []`で正規化せず、report_designの値をそのまま渡す
    # （2026-09-15修正）。validate_prompt2_report_config側がNone（未設定）と空リスト
    # （グラフ機能を使わないという明示的な選択）を区別するため——正規化してしまうと
    # 意図的に空にしたプロジェクトでも毎回「未設定」警告が出てしまう。
    report_config_warnings = provisioning.validate_prompt2_report_config(
        {"final_graph_rule": report_design.get("final_graph_rule") or "default"},
        report_design.get("primary_graph_signals"),
        # 履歴の基準開始日（historical_trend_start_date）があれば、グラフ開始日の設定として扱う（2026-10-05）。
        # 実際のグラフ開始日はRun作成時に観測期間と組み合わせて決まる。
        report_design.get("common_primary_graph_start") or report_design.get("historical_trend_start_date"),
        valid_signal_ids,
    )

    return {
        "design_state": design_state,
        "narrative_master_generation": narrative_master_generation,
        "downloads": downloads,
        "report_config_warnings": report_config_warnings,
    }


def handle_chat_file_upload(client, azure_client, model: str, project_id: str,
                             file_name: str, file_bytes: bytes, *,
                             adapter: Optional[ReportDesignExpertAdapter] = None) -> dict:
    """Design Chatへのファイル添付を処理する（2026-09-16、ユーザー要望:
    「デザインパートはチャットUIだけで、ファイルアップできる形にしよう。それであれば、
    途中のものもそこから始められる」）。拡張子で振り分ける:
    - .csv → 既存のsignal_narrative_master.csvとしてパースし、Narrative Master Draft
      へ直接保存する（LLM呼び出し不要）。
    - .html/.htm → 既存final_report.htmlとしてTemplate Extractionを実行し、
      決定的Leakage ScanがPASSした場合のみReport Template Draftへ保存する。
    どちらもチャットturnとして記録するため、以後の会話でアップロード内容を踏まえて
    対話を続けられる（design_state自体は変更しない——Signal/Report Designの提案は
    従来通りprocess_chat_turn経由）。"""
    adapter = adapter or GeoExpertDesignAdapter()
    ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    user_turn_no = _next_turn_no(client, project_id)
    append_turn(client, project_id, user_turn_no, "user", f"[ファイル添付: {file_name}]")

    if ext == "csv":
        try:
            csv_text = file_bytes.decode("utf-8-sig")
            asset = assets.upload_narrative_master_csv(client, project_id, csv_text)
        except (ValueError, UnicodeDecodeError) as exc:
            reply = f"アップロードされたCSVをNarrative Masterとして読み込めませんでした: {exc}"
            append_turn(client, project_id, user_turn_no + 1, "assistant", reply)
            return {"kind": "narrative_master", "success": False, "message": str(exc)}
        rows = asset["content"].get("rows", [])
        signal_ids = sorted({r.get("signal_id") for r in rows if r.get("signal_id")})
        reply = (
            f"Narrative Master（{len(rows)}行、Signal: {', '.join(signal_ids) or 'なし'}）を"
            "アップロードし、Draftとして保存しました。ここから続けて調整できます。"
        )
        append_turn(
            client, project_id, user_turn_no + 1, "assistant", reply,
            applied_diff={"uploaded_narrative_master": {"row_count": len(rows), "signal_ids": signal_ids}})
        return {"kind": "narrative_master", "success": True, "assistant_reply": reply,
                "row_count": len(rows), "signal_ids": signal_ids}

    if ext in ("html", "htm"):
        html_text = file_bytes.decode("utf-8", errors="replace")
        result = extract_template_from_report(client, azure_client, model, html_text, adapter=adapter)
        if not result["leakage_scan"]["passed"]:
            reply = (
                "アップロードされたHTMLからTemplateを抽出しましたが、実データの残留（数値・"
                "固有名詞・URL・引用文等）が検出されたため保存を見送りました。抽出元のHTMLを"
                "見直すか、チャットで「テンプレート作って」と依頼してゼロから提案させてください。"
            )
            append_turn(
                client, project_id, user_turn_no + 1, "assistant", reply,
                applied_diff={"leakage_scan": result["leakage_scan"]})
            return {"kind": "report_template", "success": False, "leakage_scan": result["leakage_scan"]}
        asset = assets.save_report_template_draft(client, project_id, result["template"])
        section_count = len(result["template"].get("sections", []))
        reply = (
            f"アップロードされたHTMLからReport Template（{section_count}セクション）を抽出し、"
            "Draftとして保存しました。ここから続けて調整できます。"
        )
        append_turn(
            client, project_id, user_turn_no + 1, "assistant", reply,
            applied_diff={"uploaded_report_template": {"section_count": section_count}})
        return {"kind": "report_template", "success": True, "assistant_reply": reply,
                "section_count": section_count, "asset_id": asset["id"]}

    reply = f"対応していないファイル形式です（.csv または .html/.htm のみ対応、渡されたファイル: {file_name}）。"
    append_turn(client, project_id, user_turn_no + 1, "assistant", reply)
    return {"kind": "unsupported", "success": False, "message": reply}


def _generate_narrative_master_for_all_signals(client, azure_client, model: str, project_id: str,
                                                 signals: list, *,
                                                 adapter: Optional[ReportDesignExpertAdapter] = None) -> dict:
    """Design Chatで生成依頼が来た際、登録済みの全Signalに対して1件ずつ
    generate_narrative_master_rows_for_signalを呼び、既存Draftへマージ保存する。
    1つのSignalで失敗しても他のSignalの生成は続行し、失敗分はerrorsへ記録して
    ユーザーへ返す（Signal数が多い場合に1件のエラーで全滅させないため）。"""
    generated_signal_ids = []
    errors = []
    all_new_rows = []
    for signal_def in signals:
        signal_id = signal_def.get("signal_id")
        if not signal_id:
            continue
        try:
            rows = generate_narrative_master_rows_for_signal(
                client, azure_client, model, project_id, signal_id, adapter=adapter)
        except Exception as exc:  # noqa: BLE001 — 1 Signal分の失敗を他へ波及させない
            errors.append(f"signal_id={signal_id}: {exc}")
            continue
        all_new_rows.extend(rows)
        generated_signal_ids.append(signal_id)

    if all_new_rows:
        existing = assets.get_draft(client, project_id, "narrative_master")
        existing_rows = (existing.get("content") or {}).get("rows", []) if existing else []
        merged = [r for r in existing_rows if r.get("signal_id") not in generated_signal_ids] + all_new_rows
        assets.save_narrative_master_draft(client, project_id, merged, source="chat_ai")

    return {
        "signals_generated": generated_signal_ids,
        "row_count": len(all_new_rows),
        "errors": errors,
    }


def generate_narrative_master_for_all_signals(client, azure_client, model: str, project_id: str, *,
                                               adapter: Optional[ReportDesignExpertAdapter] = None) -> dict:
    """Narrative Masterカードの「全Signal一括生成」ボタン用の公開エントリポイント。
    2026-09-14発覚: 唯一の生成手段が「Signal 1件ずつテキスト入力して生成」または
    「Design Chatで自然言語で依頼する」のどちらかで、Signal数が多いと手間がかかる上、
    Publish時に「Narrative Masterがありません」と言われても保存する明確な導線が
    画面上に無かった。現在Design Stateに登録されている全Signalへ、Design Chat側の
    narrative_master_generation_requestedと同じ処理（_generate_narrative_master_for_all_signals）
    をそのまま使って一括生成・既存Draftへマージ保存する。"""
    design_state = assets.get_design_state(client, project_id)
    signals = design_state.get("signals") or []
    if not signals:
        raise ValueError("Design StateにSignalが1件も登録されていません。先にDesign ChatでSignalを設計してください。")
    return _generate_narrative_master_for_all_signals(
        client, azure_client, model, project_id, signals, adapter=adapter)


def generate_narrative_master_rows_for_signal(client, azure_client, model: str, project_id: str, signal_id: str, *,
                                               user_instruction: Optional[str] = None,
                                               adapter: Optional[ReportDesignExpertAdapter] = None) -> list:
    """1 Signal分のNarrative Master行を1コールで生成する（保存はしない——呼び出し元が
    既存Draftへ追記/置換した上でgeo_report_project_assets_service.save_narrative_master_draft
    を呼ぶ）。user_instructionは1 Signalだけを狙い撃ちで修正依頼したい場合に使う
    （2026-09-15追加、ユーザー要望: 外部レビューで特定Signalのみ設計意図とズレている
    と指摘された際、他の良好なSignalを巻き込まずピンポイントで直せるようにする）。"""
    adapter = adapter or GeoExpertDesignAdapter()
    design_state = assets.get_design_state(client, project_id)
    signal_defs = [s for s in (design_state.get("signals") or []) if s.get("signal_id") == signal_id]
    if not signal_defs:
        raise ValueError(f"signal_id={signal_id} がDesign Stateのsignalsに見つかりません。先にSignalを設計してください。")
    signal_def = signal_defs[0]

    mentioned_names = _mentioned_entity_names_from_design_state(design_state)
    retrieved_context = adapter.retrieve_context(client, mentioned_names)
    system_prompt = _build_system_prompt(adapter, design_state, retrieved_context)
    user_prompt = (
        f"以下のSignal定義に基づき、signal_narrative_master.csvのNarrative行を生成してください"
        f"（is_fallback=trueの行を最低1つ必ず含めること）。\n\n"
        f"重要な設計原則（2026-09-15追加、外部レビューで発覚した問題への対応）: "
        f"このSignalのpurpose/what_to_captureが「未知の新規論点をDiscoveryする」"
        f"「S1〜S5（他Signal）に分類されない」「まだ分類されていない」「想定外の"
        f"争点を拾う」といった探索的・発見的な意図を示している場合、narrative_name/"
        f"sub_narrativeを具体的な既知テーマ名（例: 人権、気候変動、データ規制、"
        f"南シナ海など）にしないでください。そのSignalの目的自体が「まだ分類されて"
        f"いないものを拾う」ことなのに、Narrativeを既知テーマへ事前分類してしまうと、"
        f"Signalの意図と矛盾します。代わりに、論点の『性質・新規性のタイプ・発生"
        f"パターン』を表す抽象的な分類軸（例: 新しい政策・規制論点、新しい地政学・"
        f"安全保障論点、複数Signalにまたがる新規論点、分類不能な新規論点、等）を"
        f"narrative_nameにしてください。既知テーマの具体例は、include_keywords/"
        f"public_voice_cues/notes等の『例示』としてのみ含めてよい"
        f"（narrative_name自体にはしない）。\n\nSignal定義:\n"
        f"{json.dumps(signal_def, ensure_ascii=False, indent=2)}"
    )
    if user_instruction:
        user_prompt += f"\n\n以下の追加指示を必ず反映してください:\n{user_instruction}"
    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt,
        NARRATIVE_MASTER_GENERATION_SCHEMA, NARRATIVE_MASTER_GENERATION_SCHEMA_NAME)

    # 2026-09-15発覚: NARRATIVE_MASTER_GENERATION_SCHEMAはNarrative単位の項目
    # （narrative_id/narrative_name/include_keywords等）しかAIに生成させない設計のため、
    # Shell（Preprocess）が実際に読むCurrent契約19必須列のうち signal_name/theme_id/
    # theme_name/main_narrative がここで補完しないと空のまま残り、Prompt1 Package
    # Export後のcluster_labeling_input.csv（report_topic_label列はtheme_nameから
    # 転記される——geo_report_current_pipeline_service.build_master_rules参照）まで
    # 空欄が伝播していた（実データのPrompt1 Package Exportで確認・実害あり）。
    # trend_role/trend_start_dateはPrimary Graph（3-wayグラフ）機能専用で、
    # 未設定でもvalidate_narrative_masterは通り、グラフを使わないRunでは実害が
    # 無いため今回は補完しない。
    # - signal_name: Signal定義自体のname
    # - theme_id: 1 Signal = 1 Themeとして扱いsignal_idをそのまま使う
    # - theme_name: Signal定義自体のname（signal_nameと同じ値でよい）
    # - report_topic_label: このSignalに紐づくQuery（queries）のreport_topic_label
    #   （Design側UI表示用の列。Shellは読まないが人間可読情報として埋めておく）
    # - main_narrative: 生成されたnarrative_name自身（1 narrative = 1 main narrativeの
    #   単純化、複数Narrativeを束ねる上位の物語軸はこの案件では設計していないため）
    report_topic_label = ""
    for q in (design_state.get("queries") or []):
        if q.get("signal_id") == signal_id:
            report_topic_label = q.get("report_topic_label", "") or ""
            break
    signal_name = signal_def.get("name", "")

    # 2026-09-15発覚: active/signal_name_internalも同様にsetdefaultしていなかった。
    # activeは`geo_report_project_assets_service.validate_narrative_master`が
    # `parse_bool(row.get("active"), True)`（デフォルトTrue）で読むためPublish Gate上の
    # 実害は無いが、Export CSVに値が一切出力されず、Excel等で目視した際や外部検証
    # （ChatGPT等）で「activeが全行空」という誤解を招いていた。明示的にTrueを書き込む。
    # signal_name_internalはCurrent契約34列のうちShellのPreprocessが読まない
    # AI/UI専用列（Phase A監査結果1）だが、同じ理由でsignal_nameと同じ値を明示する。
    rows = []
    for generated in result["data"]["rows"]:
        row = dict(generated)
        row["signal_id"] = signal_id
        row.setdefault("signal_name", signal_name)
        row.setdefault("signal_name_internal", signal_name)
        row.setdefault("theme_id", signal_id)
        row.setdefault("theme_name", signal_name)
        row.setdefault("report_topic_label", report_topic_label)
        row.setdefault("main_narrative", row.get("narrative_name", ""))
        row.setdefault("active", True)
        # 2026-09-15追加: max_cluster_records/representative_countも同様にAI生成
        # スキーマに含まれず全行空欄になっていた（外部レビュー指摘）。Shell側
        # （geo_report_current_pipeline_service.build_rule_from_row）が実際に空欄
        # フォールバックとして使っている正式Default値をそのままimportして使う
        # （推測で新しい値を作らない——同モジュールが単一の真実源）。
        row.setdefault("max_cluster_records", cp.DEFAULT_MAX_CLUSTER_RECORDS)
        row.setdefault("representative_count", cp.DEFAULT_REPRESENTATIVE_COUNT)
        rows.append(assets.sync_shell_mirror_columns(row))
    return rows


def generate_report_template(client, azure_client, model: str, project_id: str, *,
                              user_instruction: Optional[str] = None,
                              adapter: Optional[ReportDesignExpertAdapter] = None) -> dict:
    """Report Design/Signalsから、Report Templateの章立て骨格（Section定義のみ）を
    1コールで提案する。style_css/shell_htmlはAIに書かせず、最小限の既定値を使う
    （実運用ではI.2 Template Extractionで既存final_report.htmlから差し替える想定）。
    既存のTemplate Draftがあれば「現在の構成」として渡し、user_instructionを修正依頼
    として反映する（Design Chatからの生成・修正リクエスト向け、2026-09-16追加）。"""
    adapter = adapter or GeoExpertDesignAdapter()
    design_state = assets.get_design_state(client, project_id)
    mentioned_names = _mentioned_entity_names_from_design_state(design_state)
    retrieved_context = adapter.retrieve_context(client, mentioned_names)
    system_prompt = _build_system_prompt(adapter, design_state, retrieved_context)

    prompt_parts = [
        "現在のReport Design/Signalsに基づき、Final Reportの章立て（Section定義）を提案してください。"
        "各Sectionはcomponent_typeを1つ選び、そのSectionを生成するために必要なrequired_fieldsを"
        "具体的に列挙してください。HTMLやCSSは書かず、構成のみ提案してください。\n"
        "重要な設計原則（2026-09-15追加、外部レビューで発覚した問題への対応）: "
        "過去の期間・過去の会談・前回レポートとの比較など、Composition時にEvidenceが"
        "存在しない可能性があるSection（例: 「前回からの変化」「経緯」「Before/After比較」"
        "系のSection）には、Evidence不足を明示する項目（例: "
        "evidence_sufficiency_status、値はSufficient evidence/Insufficient evidence"
        "等）をrequired_fieldsへ必ず含めてください。これはComposition AIが、"
        "入力に無いEvidenceをモデル一般知識で補完・推測することを防ぐための"
        "必須項目です。当社事業への影響評価を含むSection（例:"
        "「示唆・Watchpoint」系）には、影響度合い（例: High/Medium/Low/Unable to"
        "assess）と対応の緊急度（例: Immediate/Monitor/No immediate action/"
        "Unable to assess）を表すフィールドも含めてください。"
    ]
    existing_draft = assets.get_draft(client, project_id, "report_template")
    if existing_draft:
        prompt_parts.append(
            "既存のSection構成（前回までの提案）:\n"
            f"{json.dumps(existing_draft['content'].get('sections', []), ensure_ascii=False, indent=2)}\n"
            "これをベースに、指示と関係するSectionだけ調整してください"
            "（指示と無関係なSectionはそのまま維持してください）。"
        )
    if user_instruction:
        prompt_parts.append(f"ユーザーからの今回の指示:\n{user_instruction}")
    user_prompt = "\n\n".join(prompt_parts)

    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt,
        REPORT_TEMPLATE_GENERATION_SCHEMA, REPORT_TEMPLATE_GENERATION_SCHEMA_NAME)

    return {
        "style_css": _DEFAULT_STYLE_CSS,
        "shell_html": _DEFAULT_SHELL_HTML,
        "sections": result["data"]["sections"],
    }


def _generate_and_save_report_template(client, azure_client, model: str, project_id: str,
                                         user_instruction: str, *,
                                         adapter: Optional[ReportDesignExpertAdapter] = None) -> dict:
    """Design Chatでのテンプレート生成/修正依頼を処理し、結果をDraftへ自動保存する
    （2026-09-16）。プレビューは`GET .../design/report-template/preview`が常にこの
    最新Draftを参照するため、ここではURLを組み立てず、Draftのsection数のみ返す。"""
    template = generate_report_template(
        client, azure_client, model, project_id, user_instruction=user_instruction, adapter=adapter)
    asset = assets.save_report_template_draft(client, project_id, template)
    return {"asset_id": asset["id"], "section_count": len(template.get("sections", []))}


# ---------------------------------------------------------------------------
# Report Templateプレビュー（2026-09-15、ユーザー要望:
# 「テンプレートもチャット上でHTMLリンクをだして、内容やり取りしたあとに保存する」）
# ---------------------------------------------------------------------------

def _placeholder_composed_data(component_type: str, section_def: dict) -> dict:
    """Design段階（実データがまだ無い）でのTemplateプレビュー用に、実際の
    Composition AI（geo_report_prompt2_composition_service）と同じSchema形状の
    プレースホルダーを埋める。実データは一切使わない（Plan I.2 Leakage防止の
    考え方と同じく、プレビュー段階では常にダミー値のみを表示する）。"""
    if component_type == "narrative_block":
        return {
            "central_conclusion": "（このSectionの中心的な結論をここに記述）",
            "change_from_previous": "（前回からの変化をここに記述）",
            "drivers": ["（背景要因の例1）", "（背景要因の例2）"],
            "confidence_note": "（このセクションの確度・留意点）",
        }
    if component_type == "metric_grid":
        return {"metrics": [
            {"label": "指標名", "value": "値", "note": "補足"},
            {"label": "指標名2", "value": "値2", "note": "補足2"},
        ]}
    if component_type == "stacked_bar_chart":
        return {"interpretation": "（グラフの解釈をここに記述）",
                "notable_shifts": ["（注目すべき変化の例）"]}
    if component_type == "scenario_cards":
        return {"scenarios": [{
            "title": "シナリオ名", "likelihood": "Medium",
            "description": "シナリオの説明をここに記述", "business_implication": "事業への含意",
        }]}
    if component_type == "comparison_table":
        return {"columns": ["項目", "内容"], "rows": [["例1", "説明"], ["例2", "説明"]]}
    if component_type == "example_quotes":
        return {"quotes": [{
            "quote_text": "（代表投稿の引用例）", "voice_direction": "neutral", "context_note": "補足",
        }]}
    if component_type == "data_limits_note":
        return {"limitations": ["（データの制約事項の例）"], "methodology_note": "（方法論の注記）"}
    return {}


_PREVIEW_CHART_PLACEHOLDER = (
    '<div class="chart-placeholder" style="padding:24px;border:1px dashed #999;'
    'text-align:center;color:#666">（グラフはFinal Report生成時に実データで描画されます）</div>'
)


def render_template_design_preview(content: dict) -> str:
    """Report Templateの構造定義（style_css/shell_html/sections）から、実データの代わりに
    プレースホルダーを埋めたプレビューHTMLを生成する。既存のPrompt2 Composition
    Renderer（実際のFinal Report生成で使うものと同じ関数）をそのまま再利用することで、
    プレビューと本番のHTML/CSS組み立てロジックを二重管理しない。"""
    sections = sorted(content.get("sections", []), key=lambda s: s.get("order", 0))
    section_html_by_id = {}
    for section_def in sections:
        component_type = section_def.get("component_type")
        composed = _placeholder_composed_data(component_type, section_def)
        chart_svg = _PREVIEW_CHART_PLACEHOLDER if component_type == "stacked_bar_chart" else None
        try:
            section_html_by_id[section_def["section_id"]] = composition.render_report_section(
                section_def, composed, chart_svg=chart_svg)
        except ValueError:
            continue  # 未対応component_typeは実際のFinal Report生成と同じくスキップする
    return composition.render_final_report(content, section_html_by_id)
