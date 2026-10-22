"""Geopolitical Intelligence OS Phase C: 既存Expert AI（geo_expert_engine.py）と
IOS Analysis Session / Scenario Candidate / Analysis Designを繋ぐAdapter。

絶対条件: geo_expert_engine.py / geo_expert_schema.py / CQASコアロジック（main_scenario・
alternative_scenariosの生成方法そのもの）は一切変更・再実装しない。呼び出すのは既存の唯一の
公開Entry Point run_expert_request_with_mode()のみ（api_server.pyの/api/geo/expert/requestsと
全く同じ呼び出し方）。ranking改善もしない——main_scenarioを常にrank1、alternative_scenariosを
既存の生成順のままrank2以降とする（Expert AI自身が既に行った主/代替の判断をそのまま使う）。

CQAS（Cue-Question-Hypothesis-Scenario）は独立した「N候補を横並び生成する機能」ではなく、
既存Expert AI推論パイプラインの社内呼称であり、1回の呼び出しでmain_scenario 1件+
alternative_scenarios（0件以上）を返す（Phase 0監査で確認済み）。本Adapterはこれを
「候補セット」として正規化するのみで、新しい候補生成アルゴリズムは持たない。
"""
import json

import geo_audit
import geo_expert_engine
import geo_expert_schema
import geo_ios_reading_exposure_composer as reading_exposure_composer
import geo_ios_title_utils as title_utils
import geo_ios_reading_portfolio_service as reading_portfolio_service

# 既存7モードの中で、テーマ入力から「何を調査すべきか」を設計する用途に最も近いモード
# （UI表示ラベル「調査設計」、eco-digest-spark/src/lib/i18n.tsx参照）を既定とする。
# Analysis Session全体（Scenario生成・Design生成・Design改訂）で同一モードを使うことで、
# 挙動の一貫性を保つ（呼び出し側で上書き可能）。
DEFAULT_MODE = "research_design"
DEFAULT_PROFILE = "standard"


class ExpertAIAdapterError(Exception):
    """既存Expert AI呼び出し・応答正規化に関するエラー。"""


def _resolve_mode(mode: str | None, *, default: str = DEFAULT_MODE) -> str:
    mode = mode or default
    if mode not in geo_expert_schema.MODES:
        raise ExpertAIAdapterError(f"未対応のmodeです: {mode}（許可: {geo_expert_schema.MODES}）")
    return mode


def _call_expert_ai(client, azure_client, model, *, mode: str, theme_id: str | None,
                     query_text: str, draft_text: str | None, profile: str | None,
                     execution_mode: str | None, actor_id: str,
                     actor_intelligence_enabled: bool | None = None) -> dict:
    """既存/api/geo/expert/requestsと同じ呼び出し経路。失敗してもSession/Candidateを中途半端な
    状態にしない（§19）——呼び出し元は本関数が例外を投げた場合、DBへは何も書き込まないこと。
    既存のLLM retry policy（call_llm_structured内）以上のretryは追加しない。

    actor_intelligence_enabled: 既定None（operation-scoped override、2026-09-25追加、
    ユーザー指示Option 3）。Noneの場合はgeo_expert_engine側のglobal env flagへそのまま
    委ねる——Consultation/RefineDesign/Draft生成・編集・レビュー（本関数の他5呼び出し元）は
    一切影響を受けない。Reading生成（generate_scenario_candidates）のみが明示的にTrueを渡す。
    GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED自体は変更しない。"""
    try:
        resolved_execution_mode = geo_expert_engine.resolve_execution_mode(execution_mode)
        result = geo_expert_engine.run_expert_request_with_mode(
            client, azure_client, model, mode, theme_id, query_text, draft_text,
            profile=profile or DEFAULT_PROFILE, execution_mode=resolved_execution_mode,
            actor_intelligence_enabled=actor_intelligence_enabled,
        )
    except Exception as e:
        geo_audit.log_audit(client, actor=actor_id, action="ios_expert_ai_adapter_failure",
                             target_table="geo_expert_requests", target_id=None,
                             detail={"mode": mode, "error_type": type(e).__name__, "error": str(e)})
        raise ExpertAIAdapterError(f"Expert AI呼び出しに失敗しました: {type(e).__name__}: {e}") from e
    return result


# ---- CQAS Scenario Candidate生成（§3, §5, §21） ----------------------------

# Reading生成専用のmode/profile（2026-09-25追加、ユーザー指示）。generate_scenario_candidates
# は「Reading/Hypothesis候補を生成する」という単一目的の関数であり、Step1画面のonHandoffから
# 呼ばれるかStep2の再生成ボタンから呼ばれるかという「呼び出し元の画面」ではなく「この関数が
# 何を生成するか」で判定する——両方とも本関数の呼び出しである以上、両方ともhypothesis_
# register（Frontier候補の材料）が有効なcase_analysis/deepで実行されるべき。
#
# 共有のDEFAULT_MODE/DEFAULT_PROFILE（Consultation/RefineDesign/Draft生成・編集・レビュー、
# 計6種類の操作が共有する）は変更しない——本関数専用の別定数として分離する。呼び出し元が
# 明示的にmode/profileを渡した場合（既存のBlind Retrospective Diagnostic等の診断・評価用途）
# はそちらを優先し、この既定値は上書きされる。
READING_GENERATION_MODE = "case_analysis"
READING_GENERATION_PROFILE = "deep"

# 2026-09-23追加、ユーザー指摘「タイトルは短くしよう。読み方のキーポイントを押さえた形で」:
# 従来はdescription[:80]という機械的な80文字打ち切りだったため、文の途中で切れて
# かえって長く・読みにくいタイトルになっていた（実機で確認）。新しい要約文をLLMに
# 生成させる（追加のLLM呼び出し・コスト増）のではなく、既存description内の最初の
# 文区切り（句点/読点）で切り出す、より自然な機械的整形に変える——要約・言い換えは
# 行わない（文章そのものは変えず、どこで打ち切るかだけを賢くする）。
_SHORT_TITLE_MAX_LEN = 45


def _short_title(description: str) -> str:
    # 共通関数へ集約（2026-10-21）: 文の途中の断片（「現時点では」等）で切らない・内部ラベルを外す
    return title_utils.short_title(description, _SHORT_TITLE_MAX_LEN)


def _normalize_alternative(item: dict) -> dict:
    """alternative_scenarios[]の項目にはtitle相当が無いため、既存出力を書き換えず、表示用に
    最小限の派生フィールドのみ追加する（switch_conditionsを"why_this_matters"相当として転用、
    supporting_evidence等の捏造は行わない）。"""
    description = item.get("description") or ""
    switch_conditions = item.get("switch_conditions") or []
    return {
        "title": _short_title(description),
        "why_this_matters": ("この分岐が有力になる条件: " + " / ".join(switch_conditions)
                              if switch_conditions else None),
        "confidence": item.get("confidence"),
        "raw": item,
    }


def _normalize_main(item: dict) -> dict:
    description = item.get("description") or ""
    return {
        "title": _short_title(description),
        "why_this_matters": item.get("selection_rationale"),
        "confidence": item.get("confidence"),
        "raw": item,
    }


def _normalize_portfolio_reading(reading: dict) -> dict:
    """Reading Portfolio（改善②）の1件を、既存candidates content shapeへ正規化する。
    switch_condition/key_cue等の新フィールドはGeoIosScenarioCardが個別レンダリングを
    持たないため、既存のwhy_this_matters（自由記述テキスト）へ人間可読な形でまとめる
    ——UI側の新規コンポーネントを増やさず、既存カードでそのまま表示できるようにする。"""
    parts = []
    if reading.get("key_cue"):
        parts.append(f"着眼点: {reading['key_cue']}")
    if reading.get("supporting_reasons"):
        parts.append("支持する理由: " + " / ".join(reading["supporting_reasons"]))
    if reading.get("weakening_information"):
        parts.append("弱める情報: " + " / ".join(reading["weakening_information"]))
    if reading.get("switch_condition"):
        parts.append(f"切替条件: {reading['switch_condition']}")
    why_this_matters = "\n".join(parts) if parts else None
    return {
        "title": (reading.get("title") or reading.get("reading") or "")[:80],
        "why_this_matters": why_this_matters,
        "main_lens": reading.get("key_actors_and_structure"),
        "confidence": None,
        "raw": reading,
    }


def generate_scenario_candidates(client, azure_client, model, *, theme: str, theme_id: str = None,
                                  mode: str = None, profile: str = None, execution_mode: str = None,
                                  actor_id: str, include_reading_portfolio: bool = True) -> dict:
    """既存Expert AIを1回呼び出し、main_scenario（rank1）+alternative_scenarios（rank2以降、
    既存の生成順のまま）を候補セットへ正規化する。全候補を保持する前提はAPI/Service層
    （geo_ios_analysis_session_service.record_scenario_candidates）側が担う——本関数はrank付き
    候補のリストを返すだけで、DBへの書き込みは行わない。

    include_reading_portfolio: 既定True。改善②（ユーザー指示、2026-09-24、Step2品質検証結果
    を受けた対応）——CQAS本体（main_scenario/alternative_scenariosの生成方法・ranking）には
    一切触れず、それらを「既に埋まっている視点」として渡した上で、別の因果構造・前提反転・
    Wildcardという3つの追加視点を薄いレイヤーとして補う
    （geo_ios_reading_portfolio_service）。このレイヤーの呼び出しが失敗しても、既にCQASから
    得られている候補（main_scenario/alternative_scenarios）は失わない——失敗時はaudit logへ
    記録し、追加読みなしで既存候補のみを返す（§19 Failure Handlingと同じ「壊れても既存の
    成果を失わない」方針）。"""
    mode = _resolve_mode(mode, default=READING_GENERATION_MODE)
    profile = profile or READING_GENERATION_PROFILE
    # Actor-first化 Step2（2026-09-25追加、ユーザー指示Option 3）: Reading生成のみ
    # operation-scoped overrideでActor Intelligence Packを常時有効化する。global env flag
    # （GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED）自体は変更しない——Consultation/RefineDesign/
    # Draft生成・編集・レビューは_call_expert_aiをNoneのまま呼ぶため無変更。
    result = _call_expert_ai(
        client, azure_client, model, mode=mode, theme_id=theme_id, query_text=theme,
        draft_text=None, profile=profile, execution_mode=execution_mode, actor_id=actor_id,
        actor_intelligence_enabled=True)

    response_row = result["response"]
    response_json = response_row["response_json"]
    request_id = result["request"]["request_id"]
    response_id = response_row["response_id"]
    # 2026-09-25追加、ユーザー指示: 新しいmigrationを追加せず、既存のsource_cqas（jsonb、
    # geo_ios_scenario_candidates.content内）へ実行時のmode/profile/operationを記録する。
    source_cqas = {
        "request_id": request_id, "response_id": response_id,
        "mode": mode, "profile": profile, "operation": "reading_generation",
        "actor_intelligence_enabled": True,
    }

    main_scenario = response_json.get("main_scenario")
    if not main_scenario:
        raise ExpertAIAdapterError(
            "Expert AIの応答にmain_scenarioが含まれていません（モード・profileの組み合わせを"
            "見直してください）")
    alternative_scenarios = response_json.get("alternative_scenarios") or []

    key_actors = [s.get("actor") for s in (response_json.get("stakeholders") or []) if s.get("actor")]

    candidates = []
    main = _normalize_main(main_scenario)
    candidates.append({
        "title": main["title"] or theme[:80],
        "content": {
            "analysis_question": theme, "why_this_matters": main["why_this_matters"],
            "main_lens": None, "key_actors": key_actors, "confidence": main["confidence"],
            "source_cqas": source_cqas, "origin": "main_scenario", "raw": main["raw"],
        },
    })
    for alt in alternative_scenarios:
        normalized = _normalize_alternative(alt)
        candidates.append({
            "title": normalized["title"] or theme[:80],
            "content": {
                "analysis_question": theme, "why_this_matters": normalized["why_this_matters"],
                "main_lens": None, "key_actors": key_actors, "confidence": normalized["confidence"],
                "source_cqas": source_cqas, "origin": "alternative_scenario", "raw": normalized["raw"],
            },
        })

    if include_reading_portfolio:
        try:
            additional_readings = reading_portfolio_service.generate_additional_readings(
                azure_client, model, context_and_materials_text=theme,
                main_scenario=main_scenario, alternative_scenarios=alternative_scenarios)
        except Exception as e:  # noqa: BLE001 — 追加レイヤーの失敗でCQAS本体の成果は失わない
            geo_audit.log_audit(client, actor=actor_id, action="ios_reading_portfolio_failure",
                                 target_table="geo_expert_requests", target_id=request_id,
                                 detail={"error_type": type(e).__name__, "error": str(e)})
            additional_readings = []
        for reading in additional_readings:
            normalized = _normalize_portfolio_reading(reading)
            candidates.append({
                "title": normalized["title"] or theme[:80],
                "content": {
                    "analysis_question": theme, "why_this_matters": normalized["why_this_matters"],
                    "main_lens": normalized["main_lens"], "key_actors": key_actors,
                    "confidence": normalized["confidence"], "source_cqas": source_cqas,
                    "origin": f"reading_portfolio_{reading.get('slot')}", "raw": normalized["raw"],
                },
            })

    return {
        "candidates": candidates, "model": response_row.get("model"),
        "prompt_version": response_row.get("prompt_version"),
        "cqas_knowledge_version": response_row.get("knowledge_version"),
        "request_id": request_id, "response_id": response_id,
    }


# ---- Report Design生成／改訂（§9, §11） -------------------------------------

def _map_response_to_design_fields(response_json: dict) -> dict:
    """既存Expert AI応答から、Analysis Designの各フィールドへ決定的にmappingする（新しい
    重複Schemaは作らない）。LLMを再度呼ばない純粋なデータ変換。"""
    main_scenario = response_json.get("main_scenario") or {}
    hypothesis_register = response_json.get("hypothesis_register") or []
    knowledge_gaps = response_json.get("identified_knowledge_gaps") or []

    main_hypotheses = [h["description"] for h in hypothesis_register if h.get("hypothesis_type") == "main"]
    alt_hypotheses = [h["description"] for h in hypothesis_register if h.get("hypothesis_type") != "main"]
    if not main_hypotheses and main_scenario.get("description"):
        main_hypotheses = [main_scenario["description"]]
    if not alt_hypotheses:
        alt_hypotheses = [
            (a.get("description") or "") for a in (response_json.get("alternative_scenarios") or [])
            if a.get("description")
        ]

    gap_descriptions = [g.get("description") for g in knowledge_gaps if g.get("description")]

    return {
        "key_actors": [s.get("actor") for s in (response_json.get("stakeholders") or []) if s.get("actor")],
        "hypotheses": main_hypotheses,
        "alternative_hypotheses": alt_hypotheses,
        "evidence_needed": list(gap_descriptions),
        "known_evidence": main_scenario.get("supporting_evidence") or [],
        "knowledge_gaps": list(gap_descriptions),
        "watch_points": response_json.get("monitoring_indicators") or [],
        "confidence": main_scenario.get("confidence"),
        "assumptions": main_scenario.get("assumptions") or [],
    }


_HYPOTHESIS_STATUS_LABELS = {
    "retained": "Sは本線には採用しなかったが、現時点でも保持している読み",
    "unresolved": "Sの中でも結論が出ていない、未解決の読み",
    "rejected": "Sは検討した上で採用しなかった読み",
}


def _map_selected_candidate_to_design_fields(scenario_candidate: dict, response_json: dict) -> dict:
    """選択されたcandidate自身のcontent.rawからDesign fieldsを組み立てる。response_jsonの
    main_scenarioへ無条件にフォールバックしない——rank2/3のalternative_scenarioや、Reading
    Exposure LayerのFrontier/Exploratoryを選んだ場合でも、そのcandidate自身の内容が
    Draftへ反映されるようにする（従来_map_response_to_design_fieldsを直接使っていた実装は、
    どのcandidateを選んでもresponse_json["main_scenario"]しか見ておらず、選択結果が
    Draftへ反映されないバグがあった——本関数がその修正）。identified_knowledge_gaps・
    stakeholders・monitoring_indicatorsはAnalysis Session全体で共有される文脈情報のため、
    既存どおりresponse_json（Session共通のExpert AI応答）から引く。"""
    content = scenario_candidate.get("content") or {}
    raw = content.get("raw") or {}
    origin = content.get("origin") or ""
    knowledge_gaps = response_json.get("identified_knowledge_gaps") or []
    gap_descriptions = [g.get("description") for g in knowledge_gaps if g.get("description")]
    main_scenario = response_json.get("main_scenario") or {}
    key_actors = content.get("key_actors") or [
        s.get("actor") for s in (response_json.get("stakeholders") or []) if s.get("actor")]
    common = {
        "key_actors": key_actors, "evidence_needed": list(gap_descriptions),
        "knowledge_gaps": list(gap_descriptions),
        "watch_points": response_json.get("monitoring_indicators") or [],
    }

    if origin in ("main_scenario", "alternative_scenario"):
        description = raw.get("description") or content.get("why_this_matters") or ""
        other_descriptions = [
            (a.get("description") or "") for a in (response_json.get("alternative_scenarios") or [])
            if a.get("description") and a.get("description") != description
        ]
        if origin == "alternative_scenario" and main_scenario.get("description"):
            other_descriptions = [main_scenario["description"]] + other_descriptions
        return {
            **common,
            "hypotheses": [description] if description else [],
            "alternative_hypotheses": other_descriptions,
            "known_evidence": raw.get("supporting_evidence") or [],
            "confidence": raw.get("confidence"),
            "assumptions": raw.get("assumptions") or [],
        }

    # Reading Exposure Layer: FRONTIER / EXPLORATORY（hypothesis_register由来・
    # reading_portfolio由来・expert_exploration_generated由来のいずれか）。探索的な読みで
    # あること・weakening evidence・不足しているEvidence・切替条件をDraft生成コンテキスト
    # から落とさないよう、assumptionsへ折り込む（確定事実であるかのようにDraftへ変換
    # しないため）。hypothesis_register由来の場合はFreeze済みComposerの既存展開ロジック
    # （expand_hypothesis_to_reading_card）をそのまま再利用し、判定を重複実装しない。
    if "hypothesis_id" in raw:
        card = reading_exposure_composer.expand_hypothesis_to_reading_card(raw)
        description = card["reasoning_story"]
        known_evidence = card["supporting_basis"] or []
        weakening_texts = card["weakening_basis"] or []
        missing_evidence = card["missing_evidence"]
        status = card["original_hypothesis_status"]
        switch_condition = None
    else:
        description = raw.get("reading") or raw.get("title") or content.get("why_this_matters") or ""
        known_evidence = raw.get("supporting_reasons") or []
        weakening_texts = raw.get("weakening_information") or []
        missing_evidence = None if (known_evidence or weakening_texts) else (
            "supporting_reasons/weakening_informationの明示的な記述が無い")
        status = None
        switch_condition = raw.get("switch_condition")

    assumptions = []
    assumptions.append(_HYPOTHESIS_STATUS_LABELS.get(
        status, "Sが本線として選ばなかった、探索的な読みである"))
    if weakening_texts:
        assumptions.append("弱める情報: " + " / ".join(str(w) for w in weakening_texts))
    if missing_evidence:
        assumptions.append(f"不足しているEvidence: {missing_evidence}")
    if switch_condition:
        assumptions.append(f"切替条件: {switch_condition}")

    return {
        **common,
        "hypotheses": [description] if description else [],
        "alternative_hypotheses": [main_scenario["description"]] if main_scenario.get("description") else [],
        "known_evidence": known_evidence,
        "confidence": raw.get("confidence"),
        "assumptions": assumptions,
    }


def build_design_from_candidate(client, *, theme: str, scenario_candidate: dict) -> dict:
    """選択されたScenario CandidateのCQAS Run（source_cqas.response_id）を、既に永続化済みの
    geo_expert_responsesから再取得してDesignへmappingする——新しいLLM呼び出しは行わない
    （ユーザーが選んだ候補と同じCQAS Runの内容をそのままDesignの初版にする）。Design fields
    自体は選択されたcandidate自身の内容から組み立てる（_map_selected_candidate_to_design_fields）
    ——どのcandidateを選んでもmain_scenarioしか見ない、という状態には戻さない。"""
    source_cqas = (scenario_candidate.get("content") or {}).get("source_cqas") or {}
    response_id = source_cqas.get("response_id")
    if not response_id:
        raise ExpertAIAdapterError("選択されたcandidateにsource_cqas.response_idがありません")

    rows = client.select("geo_expert_responses", {
        "select": "*", "response_id": f"eq.{response_id}", "limit": "1"})
    if not rows:
        raise ExpertAIAdapterError(f"response_id={response_id}のExpert AI応答が見つかりません")
    response_json = rows[0]["response_json"]

    fields = _map_selected_candidate_to_design_fields(scenario_candidate, response_json)
    fields["investigation_question"] = theme
    return fields


# ---- Consultation Turn（切り口相談/調査方針/プレレビュー） -----------------

def run_consultation_turn(client, azure_client, model, *, mode: str, query_text: str,
                           draft_text: str | None = None, theme_id: str | None = None,
                           profile: str = None, execution_mode: str = None, actor_id: str) -> dict:
    """相談モード（geo_ios_consultation_service.MODE_TO_EXPERT_MODE経由で選ばれた既存Expert AI
    mode）を、既存run_expert_request_with_modeへそのまま委譲する。CQAS候補化やDesign field
    へのmapping等の追加加工は一切行わない——結果はそのまま呼び出し元(geo_ios_consultation_
    service)がConsultation Turnとして保存する。"""
    mode = _resolve_mode(mode)
    result = _call_expert_ai(
        client, azure_client, model, mode=mode, theme_id=theme_id, query_text=query_text,
        draft_text=draft_text, profile=profile, execution_mode=execution_mode, actor_id=actor_id)
    return {
        "request_id": result["request"]["request_id"],
        "response_id": result["response"]["response_id"],
        "response_json": result["response"]["response_json"],
    }


def refine_design(client, azure_client, model, *, current_design: dict, user_message: str,
                   theme: str, theme_id: str = None, mode: str = None, profile: str = None,
                   execution_mode: str = None, actor_id: str) -> dict:
    """refine_analysis_design(session_id, user_message)相当（§11）。現Designをdraft_text
    （既存Expert AIの全モード共通の汎用パラメータ、api_server.py:/api/geo/expert/requestsと
    同じ経路）として渡し、user_messageをquery_textへ埋め込むことで、新しいLLMモード・
    Schemaを追加せずに「今のDesign＋ユーザー要望」を踏まえた応答を得る。"""
    mode = _resolve_mode(mode)
    design_snapshot = {k: v for k, v in current_design.items()
                        if k in ("investigation_question", "scope", "key_actors", "hypotheses",
                                  "alternative_hypotheses", "watch_points", "assumptions")}
    query_text = (
        f"以下は現在のAnalysis Design（投資質問: {theme}）に対するユーザーからの追加要望です。"
        f"この要望を踏まえて再分析してください。\n\n【ユーザーの要望】\n{user_message}"
    )
    draft_text = json.dumps(design_snapshot, ensure_ascii=False, indent=2)

    result = _call_expert_ai(
        client, azure_client, model, mode=mode, theme_id=theme_id, query_text=query_text,
        draft_text=draft_text, profile=profile, execution_mode=execution_mode, actor_id=actor_id)

    response_row = result["response"]
    response_json = response_row["response_json"]
    updated_fields = _map_response_to_design_fields(response_json)
    updated_fields["investigation_question"] = current_design.get("investigation_question") or theme

    changed_fields = sorted(
        k for k in updated_fields
        if json.dumps(updated_fields.get(k), sort_keys=True, ensure_ascii=False)
        != json.dumps(current_design.get(k), sort_keys=True, ensure_ascii=False)
    )
    change_summary = {
        "user_message": user_message, "changed_fields": changed_fields,
        "request_id": result["request"]["request_id"], "response_id": response_row["response_id"],
    }
    return {
        "updated_fields": updated_fields, "change_summary": change_summary,
        "model": response_row.get("model"), "prompt_version": response_row.get("prompt_version"),
        "cqas_knowledge_version": response_row.get("knowledge_version"),
    }


# ---- IOS Native Report Draft: 生成／AI Review ------------------------------

def _serialize_grounding_context(*, design: dict, situation: dict) -> str:
    """Draft生成・AI Reviewの両方で共有するGrounding Context（Design＋局面を読む出力）を
    1つのJSONブロックへシリアライズする。新しい構造化Schemaは作らず、既存フィールドを
    そのまま転記するだけ（決定的処理、LLM呼び出しなし）。"""
    design_block = {
        k: design.get(k) for k in (
            "investigation_question", "scope", "hypotheses", "alternative_hypotheses",
            "known_evidence", "evidence_needed", "knowledge_gaps", "key_actors", "watch_points",
        )
    }
    return json.dumps(
        {"design": design_block, "situation_assessment": situation},
        ensure_ascii=False, indent=2,
    )


def run_draft_generation(client, azure_client, model, *, design: dict, situation: dict,
                          theme: str, theme_id: str | None = None, profile: str | None = None,
                          actor_id: str) -> dict:
    """IOS Native Report Draft生成（draft_generationモード）。既存Expert AI Engine・Schema
    自体は変更せず、新規追加したmode一つを既存run_expert_request_with_mode経由で呼ぶだけ。
    Stage1/Stage2の段階実行モジュール構成には一切手を加えず、execution_mode="monolithic"
    （既存の一括方式、他モードのロールバック手段としても使われている既存の実行経路）を明示
    指定することで、RESPONSE_SCHEMA全体（draft_sectionsを含む）をそのまま使う。"""
    context_text = _serialize_grounding_context(design=design, situation=situation)
    # 2026-09-24変更、ユーザー指摘「生成レポートやレビューの記載がおかしい。ユーザーは
    # などという言い回しはおかしい。レポート作成者はアナリストとして局面を分析し、
    # シナリオを提示するものとしてレポートを書き、AIはそれをサポートする」への対応:
    # 従来のquery_textは「何を根拠に書くか」しか指定しておらず、「誰の声として書くか」
    # の指定が無かった。Grounding Context側にactor/hypothesis関連の語彙（例:
    # persistent_preference等）が含まれるため、指定が無いとLLMが「ユーザーは〜と
    # 考えている」のような第三者記述に流れることを確認した。レポート本文は常に
    # 分析担当者(アナリスト)自身が書いた一人称の分析・シナリオ提示として執筆する
    # ことを明示する。
    query_text = (
        f"以下のAnalysis Design・局面を読む出力(Situation Assessment)のみを根拠に、"
        f"レポート本文をdraft_sectionsとして構成してください（テーマ: {theme}）。"
        f"新しい分析・新しい主張は行わず、与えられた内容の範囲で執筆してください。\n\n"
        f"重要（文体）: このレポートは、分析担当者（アナリスト）自身が局面を分析し、"
        f"シナリオを提示するレポートです。「ユーザーは〜と考えている」「ユーザー前提として」"
        f"のような、第三者（ユーザー）の見解を紹介する書き方はしないでください。"
        f"分析担当者自身の評価・シナリオとして（例:「〜と評価する」「〜というシナリオを"
        f"主軸に置く」）一人称的に執筆してください。"
    )
    result = _call_expert_ai(
        client, azure_client, model, mode="draft_generation", theme_id=theme_id,
        query_text=query_text, draft_text=context_text, profile=profile,
        execution_mode="monolithic", actor_id=actor_id)

    response_row = result["response"]
    response_json = response_row["response_json"]
    draft_sections = response_json.get("draft_sections") or []
    return {
        "draft_sections": draft_sections,
        "request_id": result["request"]["request_id"],
        "response_id": response_row["response_id"],
    }


def run_draft_ai_edit(client, azure_client, model, *, draft_sections: list, design: dict,
                       situation: dict, theme: str, instruction: str,
                       additional_material_text: str | None = None,
                       theme_id: str | None = None, profile: str | None = None,
                       actor_id: str) -> dict:
    """チャット指示によるDraft書き換え（ユーザー指示: 「チャット機能でドラフトをAIと編集する
    ボックスが必要」）。新しいExpert AIモードは追加せず、既存draft_generationモードを
    そのまま再利用する——入力がAnalysis Design/Situationのみか、それに現在のDraft
    （draft_sections）＋ユーザー指示を加えたものかの違いだけで、Schema・Stage構成・
    execution_mode="monolithic"は変更しない。

    additional_material_text（2026-09-23追加、ユーザー指示「AIで編集するところに追加資料を
    アップする機能がいるね」）: ユーザーが編集指示と一緒に添付した参考資料の抽出済み
    テキスト（geo_documents_store.extract_text、既存importDraftと同じ抽出処理）。
    grounding_contextの範囲を超えた新しい主張を禁じる指示はそのままに、この資料に
    基づく記述だけは例外的に許可する一文を追加する——資料が無い場合は何も変えない。"""
    context_text = _serialize_grounding_context(design=design, situation=situation)
    current_draft_block = json.dumps(
        {"current_draft_sections": draft_sections, "grounding_context": json.loads(context_text)},
        ensure_ascii=False, indent=2,
    )
    query_text = (
        f"以下のCurrent Draft(current_draft_sections)を、次のユーザー指示に従って書き直し、"
        f"新しいdraft_sections全体を返してください（テーマ: {theme}）。"
        "指示に関係しない部分は元の内容をできるだけ保持し、grounding_contextの範囲を超えた"
        f"新しい主張は追加しないでください。\n\n"
        # 2026-09-24変更、run_draft_generationと同じ理由: 「ユーザー指示」はこの
        # 編集リクエスト自体の呼び方として正しい（チャットでの指示を指す）が、
        # 書き直した後のdraft_sections本文自体は引き続き分析担当者(アナリスト)の
        # 一人称レポートとして書くこと（「ユーザーは〜」という書き方に変えないこと）
        # を明示する。
        f"重要（文体）: draft_sections本文自体は、分析担当者（アナリスト）自身の"
        f"分析・シナリオ提示として一人称的に書いてください（「ユーザーは〜と考えている」"
        f"という書き方はしないでください）。この指示はチャットでの編集指示であり、"
        f"レポート本文の書き手を変えるものではありません。\n\nユーザー指示: {instruction}"
    )
    if additional_material_text:
        query_text += (
            "\n\n以下はユーザーが今回の編集指示と合わせて追加提供した参考資料です。"
            "この資料に基づく具体的な記述はここから追加してかまいませんが、"
            "資料に書かれていない推測や、資料の内容を超えた一般化は行わないでください。"
            f"\n\n【追加資料】\n{additional_material_text}"
        )
    result = _call_expert_ai(
        client, azure_client, model, mode="draft_generation", theme_id=theme_id,
        query_text=query_text, draft_text=current_draft_block, profile=profile,
        execution_mode="monolithic", actor_id=actor_id)

    response_row = result["response"]
    response_json = response_row["response_json"]
    draft_sections_out = response_json.get("draft_sections") or []
    return {
        "draft_sections": draft_sections_out,
        "request_id": result["request"]["request_id"],
        "response_id": response_row["response_id"],
    }


def run_draft_ai_review(client, azure_client, model, *, draft_sections: list, design: dict,
                         situation: dict, theme: str, theme_id: str | None = None,
                         profile: str | None = None, actor_id: str) -> dict:
    """Draft AI Review（既存draft_reviewモードを無改修のまま転用）。draft_text（既存の
    全モード共通パラメータ、draft_reviewが本来レビュー対象として受け取る入力）に
    Draft本文＋Grounding Context(Design/Situation)をまとめて渡すことで、既存モードの
    プロンプト・Schema・geo_expert_quality_guard.pyには一切触れずに、unsupported claim・
    evidence不足・hypothesisをfactとして断定・evidenceとの矛盾・reasoningの飛躍を
    検出できるようにする。execution_modeは既存draft_review利用箇所（Consultation
    pre_reviewモード）と同じくデフォルト（未指定=staged）のままにする。"""
    context_text = _serialize_grounding_context(design=design, situation=situation)
    draft_text_block = json.dumps(
        {"draft_sections": draft_sections, "grounding_context": json.loads(context_text)},
        ensure_ascii=False, indent=2,
    )
    # 2026-09-24変更、ユーザー指摘「PO/P1にdocument_idやknown_evidence等の内部フィールド名が
    # そのまま出ている、担当者向けレポートレビューの場ではこのような内容は出さない」への対応:
    # Grounding Contextをそのまま与えるとLLMがそこに書かれているフィールド名（known_evidence/
    # document_id/driving_force等）をfinding/recommendationの文章にそのまま引用してしまう
    # ことを確認した（内部Schemaの漏洩、プロンプトの出力スタイル指定が不足していたバグ）。
    # チェック内容（Evidence不足/Hypothesisの事実断定/矛盾/推論の飛躍）自体は変更せず、
    # 出力の書き方だけを担当者向けの自然な日本語に限定する指示を追加する。
    #
    # 2026-09-24追加、ユーザー指摘「生成レポートやレビューの記載がおかしい。ユーザーは
    # などという言い回しはおかしい...承認者側に対しても同じように」への対応: 上記の
    # 個別フィールド名禁止だけでは「ユーザー前提」「ユーザーは〜と考えている」のような
    # 一般的な言い回しの流入までは防げていなかった（Grounding Context中のactor/
    # hypothesis関連語彙から連想して書いてしまう）。この指摘はfinding/recommendation
    # として分析担当者(アナリスト)本人にも、後続の専門家レビュアー/承認者にもそのまま
    # 見える文章のため、両方に向けて「レポートはアナリスト自身の分析・シナリオ提示で
    # あり、指摘もそれに対するレビューとして書く」ことを明示する（このAdapter層の
    # 修正のみで、承認者側に別のLLM呼び出しは無いため、同じfindingsを見る両方の画面に
    # 自動的に反映される）。
    query_text = (
        f"以下のDraft本文(draft_sections)を、同時に与えるGrounding Context"
        f"(Design/Situation Assessment)に基づいてレビューしてください（テーマ: {theme}）。"
        f"Grounding Contextに無い主張が断定されていないか、Evidenceが不足していないか、"
        f"Hypothesisが事実として書かれていないか、Evidenceと矛盾していないか、"
        f"推論の飛躍がないかを確認し、review_findingsのevidence_idsには可能な限り"
        f"Grounding Context中のknown_evidenceのIDを残してください。\n\n"
        f"重要: finding/recommendationの文章は、レポート内容を読む担当者（人間のレビュー担当）"
        f"に向けた指摘として書いてください。document_id・known_evidence・evidence_needed・"
        f"supporting_evidence_ids・driving_force・persistent_preference等の内部フィールド名や"
        f"JSON構造上の用語をそのまま文章中に書かないでください。「出典となる根拠が示されていない」"
        f"「この記述はGrounding Context上の仮説であり、確定した事実ではない」のように、"
        f"内部データ構造を知らない読み手にも伝わる自然な日本語で説明してください。\n\n"
        f"重要（文体）: このDraftは分析担当者（アナリスト）自身が局面を分析し、シナリオを"
        f"提示したレポートです。指摘文の中で「ユーザーは〜と考えている」「ユーザー前提として」"
        f"のように、レポートの内容を第三者（ユーザー）の見解であるかのように書かないで"
        f"ください。指摘はあくまで分析担当者自身が書いたレポート本文・その根拠づけに対する"
        f"レビューとして書いてください。この指摘は分析担当者本人だけでなく、後続の専門家"
        f"レビュアー・承認者にもそのまま表示されます。"
    )
    result = _call_expert_ai(
        client, azure_client, model, mode="draft_review", theme_id=theme_id,
        query_text=query_text, draft_text=draft_text_block, profile=profile,
        execution_mode=None, actor_id=actor_id)

    response_row = result["response"]
    response_json = response_row["response_json"]
    findings = response_json.get("review_findings") or []
    return {
        "findings": findings,
        "request_id": result["request"]["request_id"],
        "response_id": response_row["response_id"],
    }


# ---- Report Review V2: Report Reading vs Expert AI Reading比較 ----------------
#
# 新しいExpert AIモードは追加しない——既存draft_reviewモードをrun_draft_ai_reviewと
# 全く同じ型で転用する（プロンプト・Schema・geo_expert_quality_guard.pyには触れない）。
# 違いは入力のみ: Draft本文+Grounding Contextではなく、レビュー対象レポート自身が
# 採用している読み(report_reading、geo_report_reading_extraction_service.
# extract_report_readingの出力)と、Expert AIが同じ状況に対して独立に生成した読み
# (expert_reading、既存generate_scenario_candidatesの出力をそのまま渡す)を比較する。
#
# 既存Review Mode（geo_ios_review_import_service.py、Phase2C以前のreconstruction）の
# 「Review ModeではExpert AI Engineを呼ばない」というno-Expert-AI invariantには一切
# 触れない——本関数はReview V2専用の別経路として追加するものであり、既存Review Modeの
# 呼び出し元からは呼ばれない。

def run_report_reading_comparison_review(client, azure_client, model, *, report_reading: dict,
                                          expert_reading: dict, theme_id: str | None = None,
                                          profile: str | None = None, actor_id: str) -> dict:
    """report_reading（レポート自身の読み）とexpert_reading（Expert AI自身の独立した読み）を
    比較し、Evidence十分性・因果の飛躍・Scenario妥当性・見落としている代替読みについての
    指摘(review_findings)を得る。report_readingに明記されていない内容を、あたかも本文に
    書かれているかのように扱わないよう明示する（no-hallucination規律の継承）。"""
    draft_text_block = json.dumps(
        {"report_reading": report_reading, "expert_ai_reading": expert_reading},
        ensure_ascii=False, indent=2,
    )
    query_text = (
        "report_readingは、レビュー対象レポート自身が採用している読み方"
        "(Situation/Actor/Evidence/Scenario)です。expert_ai_readingは、同じ状況に対して"
        "専門家AIが独立に生成した読み方です。両者を比較し、次の観点で指摘してください:\n"
        "(1) Primary Readingの異同（report_reading.report_primary_readingと"
        "expert_ai_readingの中心的な読みが一致しているか、異なる場合は何がどう違うか）。\n"
        "(2) report_reading側のEvidenceが十分か、causal_logicに飛躍がないか、"
        "scenarioの妥当性に疑問がないか。\n"
        "(3) report_readingが見落としている重要な代替読み(missing reading)がないか。\n\n"
        "重要: report_readingに明記されていない内容を、あたかも本文に書かれているかの"
        "ように扱わないでください。report_reading側の指摘は、report_reading自身に"
        "含まれる情報の範囲内で行ってください。"
    )
    result = _call_expert_ai(
        client, azure_client, model, mode="draft_review", theme_id=theme_id,
        query_text=query_text, draft_text=draft_text_block, profile=profile,
        execution_mode=None, actor_id=actor_id)

    response_row = result["response"]
    response_json = response_row["response_json"]
    findings = response_json.get("review_findings") or []
    return {
        "findings": findings,
        "request_id": result["request"]["request_id"],
        "response_id": response_row["response_id"],
    }


# ---- Report Review V2: Content Review（Phase R1） -----------------------------
#
# 新しいExpert AIモードは追加しない——run_report_reading_comparison_reviewと同じく
# 既存draft_reviewモードを転用する。Reading Review（上記、読み方の構造比較）とは
# 別レイヤーとして保持する（ユーザー指示）——本関数はレポート本文(draft_text)そのものの
# 品質（Evidence不足・因果の飛躍・Scenarioの弱さ・Business implication・構成/重複/
# 不明瞭表現）をレビューする。report_reading/expert_readingは「何を根拠に本文を
# 直すべきか」の参考情報としてのみ渡す（比較はReading Review側の役割のまま）。

def run_report_content_review(client, azure_client, model, *, report_text: str, report_reading: dict,
                               expert_reading: dict, theme_id: str | None = None,
                               profile: str | None = None, actor_id: str) -> dict:
    """レポート本文(report_text)の内容レビュー。report_reading/expert_readingは
    Grounding Context（本文がどういう読みを前提にしているか、Expert AIなら何を
    重視するか）として参考に渡すのみで、比較そのものはrun_report_reading_comparison_
    reviewの役割のまま変えない。"""
    draft_text_block = json.dumps(
        {"report_text": report_text, "report_reading": report_reading,
         "expert_ai_reading_for_reference": expert_reading},
        ensure_ascii=False, indent=2,
    )
    query_text = (
        "report_textは、レビュー対象レポートの本文そのものです。report_readingは"
        "このレポート自身が採用している読み方、expert_ai_reading_for_referenceは"
        "同じ状況に対して専門家AIが独立に生成した読み方です（参考情報、比較結果は"
        "別途提示済みのため、ここでは本文の内容レビューに専念してください）。\n\n"
        "report_text本文について、次の観点で指摘してください:\n"
        "(1) 重要な修正（本文の記述に誤り・内部矛盾・看過できない欠落があるか）。\n"
        "(2) 補強すべき点（記述はあるが根拠・具体性が薄い箇所）。\n"
        "(3) Evidence不足（主張を支える根拠が本文中に示されていない箇所）。\n"
        "(4) 因果関係の飛躍（前提から結論への論理が飛躍している箇所）。\n"
        "(5) Scenarioの弱さ（提示されているシナリオの前提・条件が曖昧な箇所）。\n"
        "(6) Business implication（自社・関連業界への示唆が不足・不明瞭な箇所）。\n"
        "(7) 構成・重複・不明瞭表現（本文の構成上の問題、重複記述、読み手に伝わりにくい"
        "表現）。\n\n"
        "重要: report_textに明記されていない内容を、あたかも本文に書かれているかの"
        "ように扱わないでください。指摘はreport_text本文に実際にある記述、または"
        "その欠落に基づいて行ってください。"
    )
    result = _call_expert_ai(
        client, azure_client, model, mode="draft_review", theme_id=theme_id,
        query_text=query_text, draft_text=draft_text_block, profile=profile,
        execution_mode=None, actor_id=actor_id)

    response_row = result["response"]
    response_json = response_row["response_json"]
    findings = response_json.get("review_findings") or []
    return {
        "findings": findings,
        "request_id": result["request"]["request_id"],
        "response_id": response_row["response_id"],
    }
