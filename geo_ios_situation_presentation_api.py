"""Geopolitical Intelligence OS: Situation Analysis Presentation API（読み取り専用、局面を読む）。

新しいLLM呼び出しは行わない——既存のAnalysis Session/Scenario Candidate/Analysis Design/
Expert AI Response（geo_expert_responses、既に永続化済み）を読み取り、
geo_ios_situation_presentation_serviceで整形するだけ。CQAS candidate生成・ranking・
Expert AI core prompt等の既存経路には一切書き込みを行わない。

Reading Presentation Layer v1(ユーザー指示): 表示ロジックは1本（geo_ios_situation_
presentation_service.build_situation_presentation）のまま、入口を2つ持つ。
- 既存: /api/geo/ios/situation-presentation/{analysis_session_id}（IOS CQAS経由）
- 新設: /api/geo/situation-presentation/{response_id}（response_id直接指定、Gold Case
  評価等、IOSのAnalysis Session/Scenario Candidateを経由しないresponse_jsonを表示する
  ための汎用route。case_idからlatestを暗黙選択する設計は避け、response_idを一意な
  Primary Identifierとする）。
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_client import make_openai_client
from config_utils import load_config
from geo_db_client import SupabaseClient

import geo_audit
import geo_ios_analysis_session_service as session_service
import geo_ios_expert_review_view as expert_review_view
import geo_ios_reading_presentation_transform_service as reading_transform_service
import geo_ios_review_import_service as review_import_service
import geo_ios_situation_presentation_service as presentation_service

_logger = logging.getLogger(__name__)

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

geo_ios_situation_presentation_router = APIRouter(prefix="/api/geo/ios/situation-presentation")
geo_situation_presentation_router = APIRouter(prefix="/api/geo/situation-presentation")

# Expert Review Comment（タイトル＋2〜4文のレビューコメント）のPresentation LLM Transformationを
# 使うか。Falseの場合はDeterministic Fallback（既存文章を短く整えたもの）のみになる。テストでは
# 実LLMを呼ばないよう、tests/conftest.pyでFalseにする。
EXPERT_REVIEW_LLM_ENABLED = True


def _merge_reading_presentation(client, analysis_session_id: str, presentation: dict,
                                 response_json: dict, *, actor_id: str) -> None:
    """§23 Presentation Transformation LayerをGET応答へ合成する。失敗時は presentation を
    無変更のまま返す（既存の生フィールド表示にフォールバックできるよう、reading_presentation
    fieldを単に持たせないだけにする）。新しいLLM呼び出しが常に失敗しても既存表示は壊れない
    ことを、この関数のtry/exceptで保証する。"""
    reading_cards = presentation.get("reading_exposure") or []
    if not reading_cards:
        return
    situation_text = (presentation.get("key_point") or {}).get("one_line_assessment") or {}
    situation_text = situation_text.get("value") if isinstance(situation_text, dict) else None
    if not situation_text:
        main_scenario = response_json.get("main_scenario") or {}
        situation_text = main_scenario.get("description") or ""
    question_item = (presentation.get("key_point") or {}).get("investigation_question") or {}
    question_text = question_item.get("value") if isinstance(question_item, dict) else None
    key_actor_profiles = response_json.get("key_actor_profiles") or []

    try:
        transform_by_id = session_service.get_or_build_reading_presentation(
            client, _azure_client, _model, analysis_session_id, reading_cards,
            situation_text=situation_text, question_text=question_text, actor_id=actor_id,
            key_actor_profiles=key_actor_profiles)
    except Exception as e:  # noqa: BLE001 — Presentation Transformation失敗時は既存表示を壊さない
        _logger.warning("reading_presentation_transform failed for session=%s: %s", analysis_session_id, e)
        geo_audit.log_audit(
            client, actor=actor_id, action="reading_presentation_transform_failure",
            target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
            detail={"error_type": type(e).__name__, "error": str(e)})
        return

    for card in reading_cards:
        scid = card.get("scenario_candidate_id")
        card["reading_presentation"] = transform_by_id.get(scid)


def _attach_expert_review(presentation: dict, response_json: dict, *, use_llm: bool = True,
                          generate: bool = False) -> None:
    """Expert Review View（読み方UI用の表示構造）をpresentationへ追加する。

    ①決定論的なView（タグ・表面/裏側・流れ・証拠の意味づけ・レイヤー等）は必ず付ける。
    ②タイトル/コメント文章は、キャッシュ済みPresentation LLM Transformationがあればそれを使う。
    画面表示（GET、generate=False）ではLLMを呼ばない——キャッシュが無ければcomment_status=
    "pending"のままDeterministic Fallback（既存文章を短く整えたもの）を表示し、生成はPOST
    （generate=True）で一度だけ行ってキャッシュする（同じ入力なら以後LLMを呼ばない）。
    どの段階で失敗しても、既存のpresentation（既存キー）は壊さない。"""
    try:
        # ユーザー向け文章の衛生処理（内部表現・壊れた断片の除去）→新UI用Viewの付与の順。
        # 対象はこのレスポンス用のコピーのみで、保存済みの応答（Frozen）は書き換えない。
        expert_review_view.sanitize_presentation_in_place(presentation)
        expert_review_view.attach_expert_review(presentation, response_json)
    except Exception as e:  # noqa: BLE001 — 新UI用追加情報の失敗で既存表示を壊さない
        _logger.warning("expert_review view build failed: %s", e)
        presentation.pop("expert_review", None)
        for card in (presentation.get("reading_exposure") or []) + (presentation.get("interpretations") or []):
            card.pop("expert_review", None)
        return
    if not (use_llm and EXPERT_REVIEW_LLM_ENABLED and _azure_client and _model):
        return
    cards = [c for c in expert_review_view.display_cards(presentation) if c.get("displayed", True)]
    if not cards:
        return
    try:
        result = reading_transform_service.get_or_build_expert_review_comments(
            _azure_client, _model, presentation=presentation, response_json=response_json, cards=cards,
            allow_generate=generate)
        expert_review_view.apply_comments(cards, result["comments"], presentation.get("actors"))
        expert_review_view.apply_flow(presentation, result.get("flow"))
        presentation["expert_review"]["comment_status"] = result["status"]
        presentation["expert_review"]["llm_calls"] = result.get("llm_calls", 0)
    except Exception as e:  # noqa: BLE001 — LLM失敗時はDeterministic Fallbackのまま表示する
        _logger.warning("expert_review comment transformation failed: %s", e)
        presentation["expert_review"]["comment_status"] = "fallback"


def _fetch_response_row(client, response_id: str) -> dict:
    rows = client.select("geo_expert_responses", {
        "select": "*", "response_id": f"eq.{response_id}", "limit": "1"})
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"response_id={response_id}のExpert AI応答が見つかりません")
    return rows[0]


def _fetch_response_json(client, response_id: str) -> dict:
    return _fetch_response_row(client, response_id)["response_json"]


def _synthesize_candidates_from_response(response_json: dict) -> list:
    """Reading Presentation Layer v1(ユーザー指示): IOSのscenario_candidates（CQAS由来、
    title/why_this_matters等の追加情報を持つ）が存在しない場合に、response_json自身が
    既に持っているmain_scenario/alternative_scenariosから、build_interpretations()が
    期待する形へ詰め替えるだけの合成関数。新しいReading・causal logicは一切生成しない
    （main_scenario/alternative_scenarios自体は、hypothesis_registerの中からモデルが
    既に選定・統合済みのPresentation対象——複数hypothesisを統合したPrimary Reading
    (例: H0+H4)であっても、その統合結果はmain_scenario.description/selection_rationale
    に既に反映されているため、ここで追加の統合処理は行わない）。"""
    candidates = []
    main_scenario = response_json.get("main_scenario")
    if main_scenario:
        candidates.append({
            "scenario_candidate_id": None, "rank": 1, "displayed": True, "title": None,
            "content": {"origin": "main_scenario", "raw": main_scenario, "key_actors": None,
                        "why_this_matters": None},
        })
    for i, alt in enumerate(response_json.get("alternative_scenarios") or []):
        candidates.append({
            "scenario_candidate_id": None, "rank": i + 2, "displayed": True, "title": None,
            "content": {"origin": "alternative_scenario", "raw": alt, "key_actors": None,
                        "why_this_matters": None},
        })
    return candidates


def _find_session_id_for_response(client, response_id: str) -> str | None:
    """response_id直接route（Gold Case評価用）向け: 同じresponse_idを参照するIOS
    Scenario Candidateが既に存在する場合、そのanalysis_session_idを逆引きする。
    geo_ios_scenario_candidatesはresponse_id単位でインデックスされていないため、
    _find_response_id等と同じ「広めに取得しPython側で照合」方針を踏襲する（JSON path
    server-side filterには依存しない）。見つからない場合はNone——新しいUI/API/
    Historical Modeは追加せず、既存のsynthesizeフォールバックへそのまま進む。"""
    rows = client.select("geo_ios_scenario_candidates", {
        "select": "analysis_session_id,content", "order": "generated_at.desc", "limit": "500"})
    for row in rows:
        source_cqas = (row.get("content") or {}).get("source_cqas") or {}
        if source_cqas.get("response_id") == response_id:
            return row.get("analysis_session_id")
    return None


@geo_situation_presentation_router.get("/{response_id}")
def get_situation_presentation_by_response_id(response_id: str, debug: bool = False):
    return _build_response_presentation(response_id, debug=debug, generate_expert_review=False)


@geo_situation_presentation_router.post("/{response_id}/expert-review")
def generate_expert_review_by_response_id(response_id: str):
    """Expert Review Comment（タイトル＋レビューコメント）のPresentation LLM Transformationを、
    まだキャッシュが無い場合に限り一度だけ実行してキャッシュする（Frozen Responseの内容は
    変更しない・Expert AI Coreは再実行しない）。キャッシュ済みなら何も呼ばずstatus=cache_hitを返す。
    フロントはstatus応答後にGETを再取得する。"""
    result = _build_response_presentation(response_id, debug=False, generate_expert_review=True)
    overview = result["presentation"].get("expert_review") or {}
    return {"response_id": response_id, "status": overview.get("comment_status"),
            "llm_calls": overview.get("llm_calls", 0)}


def _build_response_presentation(response_id: str, *, debug: bool, generate_expert_review: bool):
    response_row = _fetch_response_row(_client, response_id)
    response_json = response_row["response_json"]

    design = None
    request_id = response_row.get("request_id")
    if request_id:
        requests_ = _client.select("geo_expert_requests", {
            "select": "query_text", "request_id": f"eq.{request_id}", "limit": "1"})
        if requests_:
            design = {"investigation_question": requests_[0].get("query_text")}

    # READING_EXPOSURE_STEP2_INTEGRATION_V1配線の抜け漏れ修正（2026-09-24）: このroute
    # はIOS Scenario Candidateが存在しないケース向けの_synthesize_candidates_from_
    # response()フォールバックしか持たず、main_scenario/alternative_scenariosのみを
    # 合成する（Reading Portfolio/hypothesis_registerは一切見ない）ため、reading_
    # exposureが常に空になっていた。同じresponse_idを参照する既存のAnalysis
    # Session/Scenario Candidateが既に存在する場合（例: Blind Retrospective
    # Diagnosticで生成済みのケース）は、そのSessionの既存データ・既存機構
    # （get_or_build_reading_exposure、Freeze済み・無変更）をそのまま再利用する
    # ——新しいUI/API/Historical Modeは追加しない。存在しない場合は従来通り
    # synthesizeフォールバックのまま（regressionなし）。
    session_id = _find_session_id_for_response(_client, response_id)
    candidates = session_service.list_scenario_candidates(_client, session_id) if session_id else []
    if not candidates:
        candidates = _synthesize_candidates_from_response(response_json)
    if not candidates:
        raise HTTPException(
            status_code=404, detail=f"response_id={response_id}にmain_scenarioがありません")

    reading_exposure_items = []
    if session_id:
        try:
            reading_exposure_items = session_service.get_or_build_reading_exposure(
                _client, session_id, actor_id="system")
        except Exception as e:  # noqa: BLE001 — 失敗時は既存表示を壊さない
            _logger.warning("reading_exposure build failed for response_id=%s: %s", response_id, e)
            geo_audit.log_audit(
                _client, actor="system", action="reading_exposure_build_failure",
                target_table="geo_expert_responses", target_id=response_id,
                detail={"error_type": type(e).__name__, "error": str(e)})
            reading_exposure_items = []

    presentation = presentation_service.build_situation_presentation(
        design=design, candidates=candidates, response_json=response_json,
        reading_exposure_items=reading_exposure_items)
    presentation_service.enrich_evidence_dates(_client, presentation)
    _attach_expert_review(presentation, response_json, generate=generate_expert_review)
    if not debug:
        presentation = presentation_service.strip_debug_sources(presentation)

    return {
        "response_id": response_id,
        "presentation_version": presentation_service.PRESENTATION_VERSION,
        "raw_analysis": {"expert_response_id": response_id},
        "presentation": presentation,
    }


def _find_response_id(candidates: list) -> str | None:
    for candidate in candidates:
        response_id = ((candidate.get("content") or {}).get("source_cqas") or {}).get("response_id")
        if response_id:
            return response_id
    return None


def _get_review_mode_presentation(analysis_session_id: str, *, debug: bool) -> dict:
    """REVIEW MODE（§17-22、2026-09-25追加）: CQAS/response_jsonを一切参照しない経路。
    geo_ios_review_import_serviceがmaterialize済みのReading Exposure(Current+
    Alternative)のみをbuild_situation_presentationへ渡す。既存CREATE MODE経路
    （get_situation_presentation本体）には一切分岐を混在させない——別関数として完結させる。"""
    reading_exposure_items = session_service.get_review_reading_exposure(_client, analysis_session_id)
    presentation = presentation_service.build_situation_presentation(
        design=None, candidates=[], response_json={}, reading_exposure_items=reading_exposure_items)

    review_import = review_import_service.get_review_import(_client, analysis_session_id)
    extracted_context = (review_import or {}).get("extracted_context") or {}
    situation_text = extracted_context.get("current_situation") or extracted_context.get("analysis_theme") or ""
    question_text = extracted_context.get("question")

    try:
        transform_by_id = session_service.get_or_build_reading_presentation(
            _client, _azure_client, _model, analysis_session_id, presentation["reading_exposure"],
            situation_text=situation_text, question_text=question_text, actor_id="system")
        for card in presentation["reading_exposure"]:
            card["reading_presentation"] = transform_by_id.get(card.get("scenario_candidate_id"))
    except Exception as e:  # noqa: BLE001 — 失敗時は既存の生フィールド表示にフォールバックする
        _logger.warning("reading_presentation_transform failed for review session=%s: %s",
                         analysis_session_id, e)

    presentation_service.enrich_evidence_dates(_client, presentation)
    # REVIEW MODEは従来表示のまま（Expert Review UIの対象外。CQAS/response_jsonを持たないため）
    if not debug:
        presentation = presentation_service.strip_debug_sources(presentation)

    return {
        "analysis_session_id": analysis_session_id,
        "presentation_version": presentation_service.PRESENTATION_VERSION,
        "raw_analysis": {
            "expert_response_id": None,
            "selected_scenario_candidate_id": (review_import or {}).get("selected_scenario_candidate_id"),
        },
        "presentation": presentation,
        "review_import": review_import,
    }


@geo_ios_situation_presentation_router.get("/{analysis_session_id}")
def get_situation_presentation(analysis_session_id: str, debug: bool = False):
    return _build_session_presentation(analysis_session_id, debug=debug, generate_expert_review=False)


@geo_ios_situation_presentation_router.post("/{analysis_session_id}/expert-review")
def generate_expert_review_for_session(analysis_session_id: str):
    """Sessionページ版: response_id直接ページ（generate_expert_review_by_response_id）と同じ
    Expert Review Comment生成（キャッシュ未作成時のみ一度だけLLM、REVIEW MODEは対象外）。"""
    result = _build_session_presentation(analysis_session_id, debug=False, generate_expert_review=True)
    return {"analysis_session_id": analysis_session_id,
            "status": (result["presentation"].get("expert_review") or {}).get("comment_status")}


def _build_session_presentation(analysis_session_id: str, *, debug: bool, generate_expert_review: bool):
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")

    if session.get("mode") == "review":
        return _get_review_mode_presentation(analysis_session_id, debug=debug)

    candidates = session_service.list_scenario_candidates(_client, analysis_session_id)
    if not candidates:
        raise HTTPException(status_code=404, detail="Scenario候補がまだ生成されていません")

    response_id = _find_response_id(candidates)
    if not response_id:
        raise HTTPException(
            status_code=404, detail="scenario candidateにsource_cqas.response_idがありません")
    response_json = _fetch_response_json(_client, response_id)

    design = session_service.get_current_design(_client, analysis_session_id)

    # READING_EXPOSURE_LAYER_V1配線（2026-09-24）: 既存のinterpretations組み立てには
    # 一切影響させない——ここが失敗しても、既存Step2表示（interpretations）は今まで
    # 通り返す（Backward Compatibility要件）。失敗はaudit logへ記録する。
    reading_exposure_items = []
    try:
        reading_exposure_items = session_service.get_or_build_reading_exposure(
            _client, analysis_session_id, actor_id="system")
    except Exception as e:  # noqa: BLE001 — Reading Exposure配線の失敗で既存表示を壊さない
        _logger.warning("reading_exposure build failed for session=%s: %s", analysis_session_id, e)
        geo_audit.log_audit(
            _client, actor="system", action="reading_exposure_build_failure",
            target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
            detail={"error_type": type(e).__name__, "error": str(e)})
        reading_exposure_items = []

    presentation = presentation_service.build_situation_presentation(
        design=design, candidates=candidates, response_json=response_json,
        reading_exposure_items=reading_exposure_items)
    presentation_service.enrich_evidence_dates(_client, presentation)
    _attach_expert_review(presentation, response_json, generate=generate_expert_review)
    # LLM経路を1本にする（2026-10-04）: 新Expert Review UIが使われる場合（expert_reviewが付いた場合）は、
    # 旧「6段落Presentation Transformation」（reading_presentation）を実行しない——新UIは使わず、
    # 同じ内容を別のLLM呼び出しで二重生成して待ち時間・コスト・文章の不整合を増やすだけになるため。
    # expert_reviewが付かなかった場合（View構築の失敗等）だけ、従来UIのFallback用に旧LLMを使う。
    if "expert_review" not in presentation:
        _merge_reading_presentation(_client, analysis_session_id, presentation, response_json,
                                     actor_id="system")
    if not debug:
        presentation = presentation_service.strip_debug_sources(presentation)

    return {
        "analysis_session_id": analysis_session_id,
        "presentation_version": presentation_service.PRESENTATION_VERSION,
        "raw_analysis": {
            "expert_response_id": response_id,
            "selected_scenario_candidate_id": session.get("selected_scenario_id"),
        },
        "presentation": presentation,
    }


class GenerateMoreReadingsRequest(BaseModel):
    direction: str
    custom_text: str | None = None
    actor_id: str = "analyst"


@geo_ios_situation_presentation_router.post("/{analysis_session_id}/generate-more-readings")
def generate_more_readings(analysis_session_id: str, req: GenerateMoreReadingsRequest):
    """§13「＋ 別の読み方を探す」。既存のCQAS candidate生成・S Ranking・Reading Exposure
    選定ロジックには一切書き込まない——既存Poolまたは新規LLM生成から1件だけ追加し、
    既存A-E(以降)の末尾へ追記する。"""
    session = session_service.get_session(_client, analysis_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="analysis_session_idが見つかりません")
    try:
        result = session_service.generate_additional_reading(
            _client, _azure_client, _model, analysis_session_id,
            direction=req.direction, custom_text=req.custom_text, actor_id=req.actor_id)
    except session_service.AnalysisSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 新しく作られたReadingの整形済みCardは、このresponseで組み立てずに返す
    # (build_reading_exposureの二重呼び出しを避ける)。フロント側はこのmutation成功後、
    # 既存のsituation-presentation GETをrefetchして最新の全readingを取得すること。
    return {"created": result.get("created", False), "reason": result.get("reason"),
            "total_readings": result.get("total_readings")}
