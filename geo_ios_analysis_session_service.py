"""Geopolitical Intelligence OS Phase B: Analysis Session / CQAS Scenario Candidate / Analysis Design（§3-5）。

既存Expert AI推論エンジン（geo_expert_engine.py / geo_expert_schema.py、2026-09-16時点で別チャットが
未コミットで改修中）の内部には一切手を入れない。本モジュールは、そのエンジンが生成した内容を
Analysis Session単位で構造化・履歴保持するための入れ物（Contract）のみを提供する——エンジンの
呼び出し自体は上位のAPI/Adapter層が既存の公開関数経由で行う想定。
"""
import hashlib
import json
from datetime import datetime, timezone

import geo_audit
import geo_ios_reading_exposure_composer as reading_exposure_composer
import geo_ios_reading_presentation_transform_service as reading_presentation_transform_service
import geo_ios_review_import_service as review_import_service

SESSION_STATUSES = ("active", "scenario_selected", "design_in_progress", "handed_off", "archived")


class AnalysisSessionError(Exception):
    """Analysis Session/Scenario Candidate/Analysis Designの操作エラー。"""


# ---- Analysis Session ------------------------------------------------------

def create_session(client, *, title: str, user_id: str, theme: str = None,
                    actor_id: str = None, mode: str = "create",
                    source_theme_candidate_id: str = None) -> dict:
    """mode（§17、2026-09-25追加）: "create"=既存CQASフロー、"review"=既存レポートの
    レビュー(geo_ios_review_importsを参照)。既存呼び出し元は省略時"create"のまま
    （既存動作を変えない）。

    source_theme_candidate_id（Phase 2G-1、2026-09-30追加）: どのTheme Candidateから始まった
    Sessionかのorigin参照（省略可）。Handoffブリッジ自体（Phase 2G-5）はまだ実装しない——
    この引数はmigration/schemaの受け皿のみで、既存呼び出し元は省略時null のまま
    （既存動作を変えない）。"""
    if mode not in ("create", "review"):
        raise AnalysisSessionError(f"未対応のmodeです: {mode}")
    row = {"title": title, "theme": theme, "user_id": user_id, "status": "active", "mode": mode,
           "source_theme_candidate_id": source_theme_candidate_id}
    created = client.insert("geo_ios_analysis_sessions", [row])[0]
    geo_audit.log_audit(client, actor=actor_id or user_id, action="analysis_session_create",
                         target_table="geo_ios_analysis_sessions",
                         target_id=created["analysis_session_id"], detail={"title": title, "mode": mode})
    return created


def find_or_create_session_for_theme_candidate(
        client, candidate_id: str, *, title: str, user_id: str, theme: str = None,
        mode: str = "create", actor_id: str = None) -> tuple[dict, bool]:
    """Phase 2G-5 fix（2026-10-01、concurrent idempotency補強）: Theme Candidate
    Handoff専用。同一candidate_idへ同時に2つのHandoff requestが来ても（Double-click等）、
    Sessionが1件しか作られないことを、Postgres側のtransaction-scoped advisory lock
    （geo_handoff_create_session_if_absent RPC、sql/2026-10-01_...sql）で保証する。
    既存のcreate_session()（他の呼び出し元、逐次呼び出し前提）は変更しない——このRPCは
    Handoffのこの経路専用。戻り値は(session, created)——createdがFalseの場合は既存
    Sessionを返しただけで新規作成していない（呼び出し元はaudit logを再度書かない）。"""
    result = client.rpc("geo_handoff_create_session_if_absent", {
        "p_candidate_id": candidate_id, "p_title": title, "p_theme": theme,
        "p_user_id": user_id, "p_mode": mode,
    })
    if isinstance(result, list):
        result = result[0] if result else {}
    session = result.get("session") or {}
    created = bool(result.get("created"))
    if created:
        geo_audit.log_audit(client, actor=actor_id or user_id, action="analysis_session_create",
                             target_table="geo_ios_analysis_sessions",
                             target_id=session.get("analysis_session_id"),
                             detail={"title": title, "mode": mode, "source_theme_candidate_id": candidate_id})
    return session, created


def get_session(client, analysis_session_id: str) -> dict | None:
    rows = client.select("geo_ios_analysis_sessions", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "limit": "1"})
    return rows[0] if rows else None


def list_sessions(client, *, user_id: str = None, status: str = None, mode: str = None,
                   limit: int = 100) -> list:
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if user_id:
        params["user_id"] = f"eq.{user_id}"
    if status:
        params["status"] = f"eq.{status}"
    if mode:
        params["mode"] = f"eq.{mode}"
    return client.select("geo_ios_analysis_sessions", params)


def get_session_by_report_project(client, report_project_id: str) -> dict | None:
    """§15 Provenance: Report Project → Analysis Sessionへ逆引きする。"""
    rows = client.select("geo_ios_analysis_sessions", {
        "select": "*", "linked_report_project_id": f"eq.{report_project_id}", "limit": "1"})
    return rows[0] if rows else None


def _require_session(client, analysis_session_id: str) -> dict:
    session = get_session(client, analysis_session_id)
    if not session:
        raise AnalysisSessionError(f"analysis_session_idが見つかりません: {analysis_session_id}")
    return session


def link_report_project(client, analysis_session_id: str, *, report_project_id: str,
                         actor_id: str) -> dict:
    """§6 Handoff to Report Studio: Report Studio側のテーブル・ロジックは変更せず、参照リンクのみ張る。"""
    _require_session(client, analysis_session_id)
    client.update("geo_ios_analysis_sessions", {"analysis_session_id": f"eq.{analysis_session_id}"}, {
        "linked_report_project_id": report_project_id, "status": "handed_off",
    })
    geo_audit.log_audit(client, actor=actor_id, action="analysis_session_handoff",
                         target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
                         detail={"report_project_id": report_project_id})
    return get_session(client, analysis_session_id)


def update_session_status(client, analysis_session_id: str, *, status: str, actor_id: str) -> dict:
    if status not in SESSION_STATUSES:
        raise AnalysisSessionError(f"未対応のstatusです: {status}（許可: {SESSION_STATUSES}）")
    _require_session(client, analysis_session_id)
    client.update("geo_ios_analysis_sessions", {"analysis_session_id": f"eq.{analysis_session_id}"},
                  {"status": status})
    geo_audit.log_audit(client, actor=actor_id, action="analysis_session_status_update",
                         target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
                         detail={"status": status})
    return get_session(client, analysis_session_id)


# ---- CQAS Scenario Candidate（§3） -----------------------------------------

def record_scenario_candidates(client, analysis_session_id: str, *, candidates: list,
                                display_top_n: int = 5, model: str = None,
                                prompt_version: str = None, cqas_knowledge_version: str = None,
                                actor_id: str) -> list:
    """既存Expert AIエンジンが生成した候補群（例: 20件）をまとめて記録する。UIには上位display_top_n
    件のみdisplayed=trueとして見せるが、全件を保持する（§3: 表示しなかった候補も削除しない）。
    candidatesはrank昇順（1が最上位）で渡すこと。各要素は最低limit {"title": str, "content": dict}。"""
    if not candidates:
        raise AnalysisSessionError("candidatesが空です")
    _require_session(client, analysis_session_id)

    generated_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for i, candidate in enumerate(candidates, start=1):
        rows.append({
            "analysis_session_id": analysis_session_id,
            "rank": i,
            "title": candidate["title"],
            "content": candidate.get("content", {}),
            "displayed": i <= display_top_n,
            "selected": False,
            "generated_at": generated_at,
            "model": model,
            "prompt_version": prompt_version,
            "cqas_knowledge_version": cqas_knowledge_version,
        })
    created = client.insert("geo_ios_scenario_candidates", rows)
    geo_audit.log_audit(client, actor=actor_id, action="scenario_candidates_record",
                         target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
                         detail={"candidate_count": len(created), "displayed_count": display_top_n})
    return created


def list_scenario_candidates(client, analysis_session_id: str, *, displayed_only: bool = False) -> list:
    params = {"select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "order": "rank.asc"}
    if displayed_only:
        params["displayed"] = "eq.true"
    return client.select("geo_ios_scenario_candidates", params)


def get_scenario_candidate(client, scenario_candidate_id: str) -> dict | None:
    rows = client.select("geo_ios_scenario_candidates", {
        "select": "*", "scenario_candidate_id": f"eq.{scenario_candidate_id}", "limit": "1"})
    return rows[0] if rows else None


def select_scenario_candidate(client, scenario_candidate_id: str, *, actor_id: str,
                               confirm_after_handoff: bool = False) -> dict:
    """ユーザーが候補を1つ選択する（§3）。同一セッション内の他候補のselectedは解除する
    （「選択」は排他的——複数同時選択は現行仕様上想定しない）。

    Phase C §8: Report作成済み（status=handed_off）後の選択変更は明示的操作にする——
    confirm_after_handoff=Trueを渡さない限りエラーにする（誤操作でCreate Report後に
    こっそり選択が変わることを防ぐ）。"""
    rows = client.select("geo_ios_scenario_candidates", {
        "select": "*", "scenario_candidate_id": f"eq.{scenario_candidate_id}", "limit": "1"})
    if not rows:
        raise AnalysisSessionError(f"scenario_candidate_idが見つかりません: {scenario_candidate_id}")
    candidate = rows[0]
    session_id = candidate["analysis_session_id"]

    session = _require_session(client, session_id)
    if session["status"] == "handed_off" and not confirm_after_handoff:
        raise AnalysisSessionError(
            "このAnalysis Sessionは既にReport作成済みです（status=handed_off）。選択を変更する"
            "場合はconfirm_after_handoff=Trueを明示してください。")

    others = client.select("geo_ios_scenario_candidates", {
        "select": "scenario_candidate_id", "analysis_session_id": f"eq.{session_id}",
        "selected": "eq.true"})
    for other in others:
        client.update("geo_ios_scenario_candidates",
                       {"scenario_candidate_id": f"eq.{other['scenario_candidate_id']}"},
                       {"selected": False})

    client.update("geo_ios_scenario_candidates", {"scenario_candidate_id": f"eq.{scenario_candidate_id}"},
                  {"selected": True})
    # handed_off後の明示的な選択変更では、既にReportへ引き継ぎ済みという事実自体は変えない
    # （statusをscenario_selectedへ巻き戻さない）。それ以外は通常どおりscenario_selectedへ進める。
    next_status = session["status"] if session["status"] == "handed_off" else "scenario_selected"
    client.update("geo_ios_analysis_sessions", {"analysis_session_id": f"eq.{session_id}"}, {
        "selected_scenario_id": scenario_candidate_id, "status": next_status,
    })
    geo_audit.log_audit(client, actor=actor_id, action="scenario_candidate_select",
                         target_table="geo_ios_scenario_candidates", target_id=scenario_candidate_id,
                         detail={"analysis_session_id": session_id, "after_handoff": session["status"] == "handed_off"})
    rows = client.select("geo_ios_scenario_candidates", {
        "select": "*", "scenario_candidate_id": f"eq.{scenario_candidate_id}", "limit": "1"})
    return rows[0]


# ---- Reading Exposure（READING_EXPOSURE_LAYER_V1配線、2026-09-24） ----------
#
# geo_ios_reading_exposure_composer.compose_reading_exposure()（Freeze済み、selection
# logic自体は一切変更しない）を、既に永続化済みのgeo_ios_scenario_candidates/
# geo_expert_responsesから呼び出すだけの薄いオーケストレーション層。新しいtable/
# migrationは作らない——Frontier/Exploratory等、まだgeo_ios_scenario_candidatesに
# 対応する行が無いReadingは、既存のrecord_scenario_candidatesと同じinsert経路で
# 新規行として追記し、Reading Exposure専用metadata（reading_role/exposure_rank/
# exposure_source等）は既存のjsonb `content` 列へ埋め込む（新しい列は増やさない）。
# 何度呼んでも同じ行を再利用する（identity keyで検索してから無ければ挿入、
# 二重生成しない）。

def _find_source_cqas(candidates: list) -> dict | None:
    for candidate in candidates:
        source_cqas = (candidate.get("content") or {}).get("source_cqas") or {}
        if source_cqas.get("response_id"):
            return source_cqas
    return None


def _reading_identity_key(reading: dict) -> str:
    """既存scenario_candidate行と、Composerが都度計算するReading Exposure結果とを
    同一視するためのkey。main/alternative/Reading Portfolio由来はscenario_candidate_id
    そのもの（Composerの出力にもTop3はcontentがspreadされているため残る）。
    hypothesis_register由来（Frontier、まれにExploratory）はhypothesis_idを使う
    （Composerの出力にscenario_candidate_idが無いため）。"""
    if reading.get("scenario_candidate_id"):
        return f"sc:{reading['scenario_candidate_id']}"
    raw = (reading.get("content") or {}).get("raw") or {}
    hyp_id = raw.get("hypothesis_id")
    if hyp_id:
        return f"hyp:{hyp_id}"
    return f"title:{reading.get('title')}"


def get_or_build_reading_exposure(client, analysis_session_id: str, *, actor_id: str) -> list:
    """Freeze済みcompose_reading_exposure()を呼び、最大5件のReadingを、実在する
    scenario_candidate_id付きで返す。S/CQAS/Reading Portfolio生成・Composerの
    selection logicには一切触れない——ここで行うのは(1)既存データの組み立て、
    (2)Composer呼び出し、(3)まだ行が無いReadingの追記、(4)Composer出力順での整列、
    のみ。新しいLLM呼び出しはしない（azure_client/modelを渡さないため、Expert
    Explorationの新規生成はこの読み取り経路では発火しない——既存Pool内で
    Reading5が埋まらない場合は4件のまま返す。GETの副作用として新規LLM呼び出しを
    起こさない、という既存Freezeの方針を踏襲）。"""
    candidates = list_scenario_candidates(client, analysis_session_id)
    if not candidates:
        return []
    source_cqas = _find_source_cqas(candidates)
    if not source_cqas:
        return []
    response_id = source_cqas["response_id"]
    resp_rows = client.select("geo_expert_responses", {
        "select": "response_json", "response_id": f"eq.{response_id}", "limit": "1"})
    if not resp_rows:
        return []
    response_json = resp_rows[0].get("response_json") or {}
    hypothesis_register = response_json.get("hypothesis_register") or []
    portfolio_candidates = [
        c for c in candidates
        if str((c.get("content") or {}).get("origin") or "").startswith("reading_portfolio_")
    ]
    main_scenario = response_json.get("main_scenario") or {}
    situation_text = main_scenario.get("description") or response_json.get("one_line_assessment") or ""

    composed = reading_exposure_composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hypothesis_register,
        portfolio_candidates=portfolio_candidates, situation_text=situation_text)

    # Generate More Readings（2026-09-25追加、ユーザー指示§13）: ユーザーが方向性を指定して
    # generate_additional_readingで追加生成・永続化した読み方を、Composerの通常最大5件の
    # 後ろに追記する。Composerの選定ロジック・スコアリングには一切触れない——永続化済みの
    # scenario_candidate_id付きDB行をそのまま末尾に足すだけ（重複挿入はしない、
    # _ensure_reading_exposure_persisted側がscenario_candidate_id有りのreadingを
    # 素通しすることで担保する）。
    generated_extras = [
        c for c in candidates
        if (c.get("content") or {}).get("exposure_source") == "user_requested_direction"
    ]
    generated_extras.sort(key=lambda c: (c.get("content") or {}).get("generated_exposure_order") or 0)
    next_exposure_rank = len(composed) + 1
    for extra in generated_extras:
        extra_content = extra.get("content") or {}
        composed.append({
            "scenario_candidate_id": extra.get("scenario_candidate_id"),
            "reading_role": "EXPLORATORY", "exploratory": True,
            "source": "user_requested_direction",
            "title": extra.get("title"), "content": extra_content,
            "supporting_basis": extra_content.get("supporting_basis"),
            "weakening_basis": extra_content.get("weakening_basis"),
            "original_hypothesis_status": None, "why_not_selected_by_s": None,
            "missing_evidence": extra_content.get("missing_evidence"),
            "exposure_rank": next_exposure_rank,
        })
        next_exposure_rank += 1

    return _ensure_reading_exposure_persisted(client, analysis_session_id, candidates, composed,
                                               source_cqas=source_cqas, actor_id=actor_id)


def _ensure_reading_exposure_persisted(client, analysis_session_id: str, existing_candidates: list,
                                        composed_readings: list, *, source_cqas: dict, actor_id: str) -> list:
    # Composer由来のreading dictは、Top1-3(main/alternative/reading_portfolio)なら元の
    # candidate dictをspreadしているためcontentが同一オブジェクト参照になる。これを
    # 使って既に永続化済みのscenario_candidate_idを取り戻す（identity一致、コピーしない）。
    content_id_to_scenario_id = {
        id(c.get("content")): c.get("scenario_candidate_id") for c in existing_candidates
    }
    existing_by_key = {}
    for c in existing_candidates:
        key = (c.get("content") or {}).get("exposure_identity_key")
        if key:
            existing_by_key[key] = c

    next_rank = max((c.get("rank") or 0) for c in existing_candidates) + 1
    new_rows = []
    finalized = []
    for reading in composed_readings:
        scenario_candidate_id = reading.get("scenario_candidate_id") or content_id_to_scenario_id.get(
            id(reading.get("content")))
        if scenario_candidate_id:
            finalized.append((reading, scenario_candidate_id, None))
            continue
        key = _reading_identity_key(reading)
        if key in existing_by_key:
            finalized.append((reading, existing_by_key[key]["scenario_candidate_id"], None))
            continue
        # まだ行が無い（Frontier、または新規生成されたExploratory）——新規行として追記する。
        # source_cqasを明示的に引き継ぐ（無ければbuild_design_from_candidate等が
        # response_idを解決できず、選択してもDesign生成に進めなくなる）。
        content = dict(reading.get("content") or {})
        content.setdefault("source_cqas", source_cqas)
        content["exposure_identity_key"] = key
        content["reading_role"] = reading.get("reading_role")
        content["exposure_source"] = reading.get("source")
        for field in ("exploratory", "original_hypothesis_status", "why_not_selected_by_s",
                      "supporting_basis", "weakening_basis", "missing_evidence"):
            if field in reading:
                content[field] = reading[field]
        row = {
            "analysis_session_id": analysis_session_id, "rank": next_rank,
            "title": reading.get("title"), "content": content, "displayed": True,
            "selected": False, "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": None, "prompt_version": None, "cqas_knowledge_version": None,
        }
        next_rank += 1
        new_rows.append((reading, row))

    if new_rows:
        created = client.insert("geo_ios_scenario_candidates", [row for _, row in new_rows])
        geo_audit.log_audit(client, actor=actor_id, action="reading_exposure_materialize",
                             target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
                             detail={"created_count": len(created)})
        for (reading, _), created_row in zip(new_rows, created):
            finalized.append((reading, created_row["scenario_candidate_id"], created_row))

    scenario_id_to_row = {c["scenario_candidate_id"]: c for c in existing_candidates}

    result = []
    for reading, scenario_candidate_id, created_row in finalized:
        row = created_row or scenario_id_to_row.get(scenario_candidate_id) or {}
        merged_content = dict(row.get("content") or reading.get("content") or {})
        merged_content.setdefault("reading_role", reading.get("reading_role"))
        merged_content.setdefault("exposure_source", reading.get("source"))
        result.append({
            "scenario_candidate_id": scenario_candidate_id,
            "rank": row.get("rank"),
            "displayed": row.get("displayed", True),
            "selected": row.get("selected", False),
            "title": reading.get("title"),
            "content": merged_content,
            "reading_role": reading.get("reading_role"),
            "exposure_rank": reading.get("exposure_rank"),
            "exploratory": reading.get("exploratory", False),
            "source": reading.get("source"),
            "original_hypothesis_status": reading.get("original_hypothesis_status"),
            "why_not_selected_by_s": reading.get("why_not_selected_by_s"),
            "supporting_basis": reading.get("supporting_basis"),
            "weakening_basis": reading.get("weakening_basis"),
            "missing_evidence": reading.get("missing_evidence"),
        })
    result.sort(key=lambda r: r.get("exposure_rank") or 999)
    return result


# ---- Reading Studio Presentation Transformation（§23-24、2026-09-25追加） ------
#
# Step2「読み方提案」画面のReading Cardを人間が読みやすい形へ再編集するための、Expert AI
# Coreとは別立てのLLM呼び出し。GETのたびに新規LLM呼び出しを起こさない既存Freezeの方針
# （get_or_build_reading_exposureと同じ）を踏襲し、結果をscenario_candidates.content.
# presentation_transformへcacheする。fingerprintが変わらない限り再生成しない。

def _presentation_transform_fingerprint(card: dict) -> str:
    def _v(item):
        return (item or {}).get("value")
    outlook = card.get("outlook") or {}
    basis = {
        "title": card.get("title"),
        "description": _v(card.get("description")),
        "why_this_matters": _v(card.get("why_this_matters")),
        "key_actors": _v(card.get("key_actors")),
        "what_was_seen": _v(card.get("what_was_seen")),
        "what_was_doubted": _v(card.get("what_was_doubted")),
        "hypothesis_formed": _v(card.get("hypothesis_formed")),
        "selection_rationale": _v(card.get("selection_rationale")),
        "why_not_main": _v(card.get("why_not_main")),
        "outlook_if_correct": _v(outlook.get("if_correct")),
        "outlook_weakens_if": _v(outlook.get("weakens_if")),
        "reading_role": card.get("reading_role"),
        "prompt_version": reading_presentation_transform_service._TRANSFORM_PROMPT_VERSION,
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def get_or_build_reading_presentation(client, azure_client, model, analysis_session_id: str,
                                       reading_exposure_cards: list, *, situation_text: str,
                                       question_text: str | None, actor_id: str,
                                       key_actor_profiles: list | None = None) -> dict:
    """reading_exposure_cards（presentation_service.build_reading_exposureの出力）に対応する
    reading_presentation（§23の`reading_presentation`shape）を、scenario_candidate_id単位の
    辞書として返す。azure_client/modelが無い、入力が空、またはLLM呼び出しが失敗した場合は
    可能な限りcache済みの結果のみを返す（§28: 既存表示を壊さない。フロント側は結果が
    無いカードについて既存の生フィールド表示にフォールバックすること）。"""
    cards = [c for c in reading_exposure_cards if c.get("scenario_candidate_id")]
    if not azure_client or not model or not cards:
        return {}

    fingerprints = {c["scenario_candidate_id"]: _presentation_transform_fingerprint(c) for c in cards}
    ids_filter = ",".join(fingerprints)
    rows = client.select("geo_ios_scenario_candidates", {
        "select": "scenario_candidate_id,content", "scenario_candidate_id": f"in.({ids_filter})"})
    row_by_id = {r["scenario_candidate_id"]: r for r in rows}

    set_fingerprint = hashlib.sha256(
        json.dumps(sorted(fingerprints.values()), ensure_ascii=False).encode("utf-8")).hexdigest()

    cached = {}
    stale_cards = []
    for card in cards:
        scid = card["scenario_candidate_id"]
        row_content = (row_by_id.get(scid) or {}).get("content") or {}
        if (row_content.get("presentation_transform")
                and row_content.get("presentation_transform_set_fingerprint") == set_fingerprint):
            cached[scid] = row_content["presentation_transform"]
        else:
            stale_cards.append(card)

    if not stale_cards:
        return cached

    try:
        transformed = reading_presentation_transform_service.build_reading_presentation_transform(
            azure_client, model, situation_text=situation_text, question_text=question_text,
            reading_cards=stale_cards, key_actor_profiles=key_actor_profiles)
    except Exception as e:  # noqa: BLE001 — Presentation Transformation失敗時は既存表示を壊さない
        geo_audit.log_audit(
            client, actor=actor_id, action="reading_presentation_transform_failure",
            target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
            detail={"error_type": type(e).__name__, "error": str(e)})
        return cached

    for card, transform in zip(stale_cards, transformed):
        if not transform:
            continue
        scid = card["scenario_candidate_id"]
        content = dict((row_by_id.get(scid) or {}).get("content") or {})
        content["presentation_transform"] = transform
        content["presentation_transform_set_fingerprint"] = set_fingerprint
        client.update("geo_ios_scenario_candidates", {"scenario_candidate_id": f"eq.{scid}"},
                      {"content": content})
        # EVENT_ONLY_READING検出（Actor-first化 Phase3、2026-09-25追加）: is_event_only=trueは
        # 表示をブロックしない（新しい大規模LLM callで再判定・棄却はしない、既存表示は壊さない）。
        # observability用の非blocking audit logのみ行う。
        check = (transform or {}).get("actor_dynamics_check") or {}
        if check.get("is_event_only"):
            geo_audit.log_audit(
                client, actor=actor_id, action="reading_presentation_event_only_detected",
                target_table="geo_ios_scenario_candidates", target_id=scid,
                detail={"analysis_session_id": analysis_session_id,
                        "reading_role": card.get("reading_role"), "actor_dynamics_check": check})
        cached[scid] = transform
    return cached


# ---- REVIEW MODE reading exposure（§17-22、2026-09-25追加） -------------------

def get_review_reading_exposure(client, analysis_session_id: str) -> list:
    """REVIEW MODE用: Composer（S Rank/Frontier/Exploratory Pool選定）を一切介さず、
    既にgeo_ios_review_import_serviceがmaterialize済みのscenario_candidates
    （CURRENT=Reading A + ALTERNATIVE=B以降）を、そのままrank順に整形して返す。
    CQAS/hypothesis_registerには一切触れない（REVIEW MODEにはS/CQASの実行自体が無い）。"""
    candidates = list_scenario_candidates(client, analysis_session_id)
    results = []
    for c in candidates:
        content = c.get("content") or {}
        results.append({
            "scenario_candidate_id": c.get("scenario_candidate_id"),
            "rank": c.get("rank"), "displayed": c.get("displayed", True),
            "selected": c.get("selected", False), "title": c.get("title"), "content": content,
            "reading_role": content.get("reading_role"),
            "exposure_rank": c.get("rank"),
            "exploratory": content.get("reading_role") != "CURRENT",
            "source": content.get("exposure_source"),
        })
    results.sort(key=lambda r: r.get("exposure_rank") or 999)
    return results


# ---- Generate More Readings（§13、2026-09-25追加、REVIEW MODE対応込み） --------

_MAX_TOTAL_READINGS = 8


def generate_additional_reading(client, azure_client, model, analysis_session_id: str, *,
                                 direction: str, custom_text: str | None, actor_id: str) -> dict:
    """ユーザーが指定した方向性から、既存のReading Exposure（最大5件、または既に追加済みの
    ものを含む）のいずれとも重複しない追加の読み方を1件生成・永続化する。既存Reading Exposure
    Composerの選定ロジック（S Rank・Frontier選定・Exploratory Pool選定）には一切触れない
    ——既存Poolが尽きた場合と同じ`generate_expert_exploration_reading`系のLLM生成経路
    （source-grounded anchor必須、insufficient_materialなら無理に作らない）を、ユーザー指定の
    観点付きで呼ぶだけ。新しい候補が既存のいずれかと意味的に重複する場合は、Prompt側の
    既存Reading列挙により生成自体を避けさせる（既存`generate_expert_exploration_reading`と
    同じ設計方針）。

    REVIEW MODE（§13, §9のGenerate More経路、2026-09-25追加）: session.mode="review"の
    場合、CQAS/response_jsonを一切参照せず、review_importsのextracted_context・既存
    Reading Exposure(Current+Alternative)のみをcontextに使う。"""
    if not azure_client or not model:
        return {"created": False, "reason": "llm_unavailable"}

    session = get_session(client, analysis_session_id)
    is_review = bool(session and session.get("mode") == "review")

    if is_review:
        existing_exposure = get_review_reading_exposure(client, analysis_session_id)
    else:
        existing_exposure = get_or_build_reading_exposure(client, analysis_session_id, actor_id=actor_id)
    if not existing_exposure:
        return {"created": False, "reason": "no_existing_readings"}
    if len(existing_exposure) >= _MAX_TOTAL_READINGS:
        return {"created": False, "reason": "max_readings_reached"}

    candidates = list_scenario_candidates(client, analysis_session_id)
    source_cqas = None
    if is_review:
        review_import = review_import_service.get_review_import(client, analysis_session_id)
        if not review_import:
            return {"created": False, "reason": "no_source_cqas"}
        extracted_context = review_import.get("extracted_context") or {}
        situation_text = extracted_context.get("current_situation") or extracted_context.get("analysis_theme") or ""
    else:
        source_cqas = _find_source_cqas(candidates)
        if not source_cqas:
            return {"created": False, "reason": "no_source_cqas"}
        resp_rows = client.select("geo_expert_responses", {
            "select": "response_json", "response_id": f"eq.{source_cqas['response_id']}", "limit": "1"})
        if not resp_rows:
            return {"created": False, "reason": "response_not_found"}
        response_json = resp_rows[0].get("response_json") or {}
        main_scenario = response_json.get("main_scenario") or {}
        situation_text = main_scenario.get("description") or response_json.get("one_line_assessment") or ""

    generated = reading_exposure_composer.generate_directed_exploration_reading(
        azure_client, model, situation_text=situation_text, existing_readings=existing_exposure,
        direction=direction, custom_text=custom_text)
    if not generated:
        return {"created": False, "reason": "insufficient_material"}

    generated_order = sum(
        1 for c in candidates
        if (c.get("content") or {}).get("exposure_source") == "user_requested_direction") + 1
    next_rank = max((c.get("rank") or 0) for c in candidates) + 1

    if is_review:
        why_this_matters = generated.get("reading") or ""
        structure_note = generated.get("key_actors_and_structure")
        if structure_note:
            why_this_matters = f"{why_this_matters} {structure_note}".strip()
        content = {
            "origin": "review_alternative_reading", "raw": generated,
            "why_this_matters": why_this_matters, "key_actors": None,
            "supporting_basis": generated.get("supporting_reasons"),
            "weakening_basis": generated.get("weakening_information"),
            "missing_evidence": None,
            "reading_role": "ALTERNATIVE", "exposure_source": "user_requested_direction",
            "requested_direction": direction, "requested_custom_text": custom_text,
            "generated_exposure_order": generated_order,
        }
    else:
        content = {
            "origin": "user_requested_exploration", "raw": generated,
            "source_cqas": source_cqas,
            "exposure_identity_key": f"generated:{analysis_session_id}:{next_rank}",
            "reading_role": "EXPLORATORY", "exposure_source": "user_requested_direction",
            "requested_direction": direction, "requested_custom_text": custom_text,
            "generated_exposure_order": generated_order,
            "supporting_basis": generated.get("supporting_reasons"),
            "weakening_basis": generated.get("weakening_information"),
            "missing_evidence": None, "why_this_matters": generated.get("reading"),
        }
    row = {
        "analysis_session_id": analysis_session_id, "rank": next_rank,
        "title": generated.get("title"), "content": content, "displayed": True,
        "selected": False, "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model, "prompt_version": None, "cqas_knowledge_version": None,
    }
    created = client.insert("geo_ios_scenario_candidates", [row])[0]
    geo_audit.log_audit(
        client, actor=actor_id, action="reading_exposure_user_generated",
        target_table="geo_ios_analysis_sessions", target_id=analysis_session_id,
        detail={"scenario_candidate_id": created["scenario_candidate_id"], "direction": direction})

    updated_exposure = (get_review_reading_exposure(client, analysis_session_id) if is_review
                        else get_or_build_reading_exposure(client, analysis_session_id, actor_id=actor_id))
    return {"created": True, "scenario_candidate_id": created["scenario_candidate_id"],
            "total_readings": len(updated_exposure)}


# ---- Analysis Design（§5） --------------------------------------------------

DESIGN_FIELDS = (
    "investigation_question", "scope", "key_actors", "analytical_lenses", "hypotheses",
    "alternative_hypotheses", "evidence_needed", "known_evidence", "knowledge_gaps",
    "watch_points", "report_structure", "confidence", "assumptions",
)


def get_current_design(client, analysis_session_id: str) -> dict | None:
    rows = client.select("geo_ios_analysis_designs", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}",
        "order": "version.desc", "limit": "1"})
    return rows[0] if rows else None


def list_design_history(client, analysis_session_id: str) -> list:
    return client.select("geo_ios_analysis_designs", {
        "select": "*", "analysis_session_id": f"eq.{analysis_session_id}", "order": "version.asc"})


def create_design_version(client, analysis_session_id: str, *, fields: dict, change_note: str,
                           created_by: str) -> dict:
    """Chat上での修正のたびに新versionを追記する（§5: 上書きせずVersion/Eventとして履歴保持）。"""
    _require_session(client, analysis_session_id)
    unknown = set(fields) - set(DESIGN_FIELDS)
    if unknown:
        raise AnalysisSessionError(f"未対応のfieldsです: {sorted(unknown)}")

    current = get_current_design(client, analysis_session_id)
    next_version = (current["version"] + 1) if current else 1
    row = {"analysis_session_id": analysis_session_id, "version": next_version,
           "change_note": change_note, "created_by": created_by}
    if current:
        for field in DESIGN_FIELDS:
            row[field] = current.get(field)
    row.update(fields)

    created = client.insert("geo_ios_analysis_designs", [row])[0]
    client.update("geo_ios_analysis_sessions", {"analysis_session_id": f"eq.{analysis_session_id}"},
                  {"status": "design_in_progress"})
    geo_audit.log_audit(client, actor=created_by, action="analysis_design_new_version",
                         target_table="geo_ios_analysis_designs", target_id=created["analysis_design_id"],
                         detail={"analysis_session_id": analysis_session_id, "version": next_version})
    return created
