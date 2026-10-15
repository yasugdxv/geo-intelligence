"""Expert Session Capture V1（Phase S2-S3、2026-10-02）。

Frozen Expert AI Reading（geo_expert_responses、既存Reading Presentation v2経由でのみ表示、
Phase S1参照）を専門家に評価してもらい、Calibration Caseでは専門家自身の判断をAI Reveal前に
記録するための、読み取り専用Expert AIに対する「薄いCapture Layer」。

Expert AI Core・CQAS・Actor/Relationship Prompt・Evidence Binding・Hypothesis Selection・
Situation Trajectory・Reading Portfolio/Selection・Frozen response・Frozen response_id・
Reading Presentation v2の分析構造には一切触れない。新規LLM callも追加しない——このモジュールは
`geo_expert_session_case_runs`/`geo_expert_session_evaluations`（2026-10-02新設）への
読み書きのみを行う。

Case Registry（§1）: docs/expert-session-case-registry.md をSource of Truthとして、
case_key → {case_type, title, response_id} をここにハードコードする。DB側のname/title
fuzzy searchには依存しない（S0 Auditで、fuzzy searchが誤ったresponse_idを拾ったことが
判明したため）。UAE–Saudi–Qatarは意図的にCASE_REGISTRYへ含めない（UNRESOLVED、新規生成・
代替禁止）。
"""
from datetime import datetime, timezone

import geo_audit

CASE_TYPES = ("gold", "calibration")
RUN_STATUSES = ("not_started", "in_progress", "locked", "revealed", "completed")

CASE_REGISTRY = {
    "gold_hormuz": {
        "case_type": "gold", "title": "Hormuz",
        "response_id": "23a1eebb-72fe-42e8-aba7-c6af1b200799",
    },
    "gold_us_china_semicon": {
        "case_type": "gold", "title": "US–China Semiconductor / AI",
        "response_id": "eed932e3-42e1-4ea1-947a-8a684d59ad2f",
    },
    "gold_nato": {
        "case_type": "gold", "title": "NATO",
        "response_id": "cd1ed3a4-70b1-4001-9643-253eccbcd532",
    },
    "gold_saudi_israel": {
        "case_type": "gold", "title": "Saudi–Israel",
        "response_id": "914191ea-9fe6-4efd-9ab5-554428fe7305",
    },
    "gold_tsmc": {
        "case_type": "gold", "title": "TSMC",
        "response_id": "1ab3abe0-6a8b-435f-b54b-1cd79f845d71",
    },
    "calib_takaichi": {
        "case_type": "calibration", "title": "高市政権 / 経済安全保障",
        "response_id": "ace4bb0a-b303-418b-a550-3617c7cfed86",
    },
    "calib_scs": {
        "case_type": "calibration", "title": "South China Sea",
        "response_id": "304ee051-7a16-46a2-a317-ebc65862b5ce",
    },
}

# UNRESOLVED_CASES: response_idが無いままSession Homeに「対象外」として出すためだけの
# 表示用メタデータ。case_type以外の実データは一切持たない（新規生成・代替禁止）。
UNRESOLVED_CASES = {
    "gold_uae_saudi_qatar": {"case_type": "gold", "title": "UAE–Saudi–Qatar"},
}

# Calibration CaseでLock前に必須とするExpert First Inputのフィールド（ユーザー指示§7:
# 「Situation / What should we look at / Primary Reading程度はLock前必須にしてよい」）。
CALIBRATION_REQUIRED_BEFORE_LOCK = ("situation", "what_to_look_at", "primary_reading")


class ExpertSessionCaptureError(Exception):
    """Case Run / Evaluationの操作エラー（400系）。"""


class ExpertSessionCaptureForbiddenError(Exception):
    """Blind Integrity Gate違反等、403で返すべきエラー。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_case_definition(case_key: str) -> dict:
    case = CASE_REGISTRY.get(case_key)
    if not case:
        raise ExpertSessionCaptureError(f"case_keyが見つかりません（未解決または未登録）: {case_key}")
    return case


def list_case_definitions() -> dict:
    """Session Home向け: runnable caseとunresolved caseの両方を返す。"""
    return {"runnable": CASE_REGISTRY, "unresolved": UNRESOLVED_CASES}


# ---- Case Run ---------------------------------------------------------------

def get_run(client, case_key: str, expert_identity_id: str) -> dict | None:
    rows = client.select("geo_expert_session_case_runs", {
        "select": "*", "case_key": f"eq.{case_key}",
        "expert_identity_id": f"eq.{expert_identity_id}", "limit": "1"})
    return rows[0] if rows else None


def start_run(client, case_key: str, *, expert_identity_id: str) -> dict:
    """run未作成なら作成（status=in_progress）、既存ならそのまま返す（idempotent、
    §11A「同じexpert×caseについてSession内で重複runが発生しないようにする」）。"""
    case = get_case_definition(case_key)
    existing = get_run(client, case_key, expert_identity_id)
    if existing:
        return existing
    row = {
        "case_key": case_key, "case_type": case["case_type"],
        "expert_identity_id": expert_identity_id, "status": "in_progress",
    }
    created = client.insert("geo_expert_session_case_runs", [row])[0]
    geo_audit.log_audit(client, actor=expert_identity_id, action="expert_session_run_start",
                         target_table="geo_expert_session_case_runs", target_id=created["run_id"],
                         detail={"case_key": case_key})
    return created


def _require_run(client, case_key: str, expert_identity_id: str) -> dict:
    run = get_run(client, case_key, expert_identity_id)
    if not run:
        raise ExpertSessionCaptureError(f"runが見つかりません（先にstartが必要）: {case_key}")
    return run


def save_expert_first_input(client, case_key: str, *, expert_identity_id: str,
                             expert_first_input: dict) -> dict:
    """Calibration CaseのみのBlind Input保存（部分更新、jsonbを丸ごと置き換えずmerge）。
    §8最重要Gate: locked_atが既にある場合は更新を拒否する（Frontend Button disabledだけに
    依存しない、Server-side保証）。"""
    run = _require_run(client, case_key, expert_identity_id)
    if run["case_type"] != "calibration":
        raise ExpertSessionCaptureError("Expert First InputはCalibration Caseのみで使用できます")
    if run.get("locked_at"):
        raise ExpertSessionCaptureForbiddenError(
            "既にLock済みのため、Expert First Inputは更新できません")
    merged = {**(run.get("expert_first_input") or {}), **expert_first_input}
    client.update("geo_expert_session_case_runs", {"run_id": f"eq.{run['run_id']}"}, {
        "expert_first_input": merged, "status": "in_progress", "updated_at": _now(),
    })
    geo_audit.log_audit(client, actor=expert_identity_id,
                         action="expert_session_first_input_save",
                         target_table="geo_expert_session_case_runs", target_id=run["run_id"],
                         detail={"case_key": case_key})
    return get_run(client, case_key, expert_identity_id)


def lock_run(client, case_key: str, *, expert_identity_id: str) -> dict:
    """§8: Server-side Lock。既にLock済みならidempotentにそのまま返す（二重クリック対策）。
    未入力の必須フィールドがあればLockさせない（422相当、呼び出し側API層でHTTPへ変換）。"""
    run = _require_run(client, case_key, expert_identity_id)
    if run["case_type"] != "calibration":
        raise ExpertSessionCaptureError("LockはCalibration Caseのみで使用できます")
    if run.get("locked_at"):
        return run
    first_input = run.get("expert_first_input") or {}
    missing = [f for f in CALIBRATION_REQUIRED_BEFORE_LOCK if not (first_input.get(f) or "").strip()]
    if missing:
        raise ExpertSessionCaptureError(
            f"Lock前に必須の入力が不足しています: {', '.join(missing)}")
    client.update("geo_expert_session_case_runs", {"run_id": f"eq.{run['run_id']}"}, {
        "locked_at": _now(), "status": "locked", "updated_at": _now(),
    })
    geo_audit.log_audit(client, actor=expert_identity_id, action="expert_session_lock",
                         target_table="geo_expert_session_case_runs", target_id=run["run_id"],
                         detail={"case_key": case_key})
    return get_run(client, case_key, expert_identity_id)


def reveal_run(client, case_key: str, *, expert_identity_id: str) -> dict:
    """§9: Server-side Reveal Gate。locked_atが無ければ403相当（呼び出し側API層で変換）。
    既にrevealed_atがあればidempotentにそのまま返す。"""
    run = _require_run(client, case_key, expert_identity_id)
    if run["case_type"] != "calibration":
        raise ExpertSessionCaptureError("RevealはCalibration Caseのみで使用できます")
    if not run.get("locked_at"):
        raise ExpertSessionCaptureForbiddenError("Lock前はRevealできません")
    if run.get("revealed_at"):
        return run
    client.update("geo_expert_session_case_runs", {"run_id": f"eq.{run['run_id']}"}, {
        "revealed_at": _now(), "status": "revealed", "updated_at": _now(),
    })
    geo_audit.log_audit(client, actor=expert_identity_id, action="expert_session_reveal",
                         target_table="geo_expert_session_case_runs", target_id=run["run_id"],
                         detail={"case_key": case_key})
    return get_run(client, case_key, expert_identity_id)


def submit_run(client, case_key: str, *, expert_identity_id: str) -> dict:
    """Gold: status=in_progress（またはcompleted、idempotent）からcompletedへ。
    Calibration: status=revealedのみからcompletedへ（Reveal前のSubmitは許可しない）。"""
    run = _require_run(client, case_key, expert_identity_id)
    if run["status"] == "completed":
        return run
    if run["case_type"] == "calibration" and run["status"] != "revealed":
        raise ExpertSessionCaptureError(
            f"status={run['status']}からはsubmitできません（calibrationはrevealed必須）")
    client.update("geo_expert_session_case_runs", {"run_id": f"eq.{run['run_id']}"}, {
        "status": "completed", "submitted_at": _now(), "updated_at": _now(),
    })
    geo_audit.log_audit(client, actor=expert_identity_id, action="expert_session_submit",
                         target_table="geo_expert_session_case_runs", target_id=run["run_id"],
                         detail={"case_key": case_key})
    return get_run(client, case_key, expert_identity_id)


# ---- Blind Integrity: Case公開View（response_idの有無を制御） -------------------

def build_public_case_view(case_key: str, case_def: dict, run: dict | None) -> dict:
    """§9/§15 Blind Integrity: Goldは常にresponse_idを含める。CalibrationはrunがRevealed/
    Completedの場合のみresponse_idを含める——それ以外（run無し/in_progress/locked）は
    response_idキー自体を応答へ含めない（null化ではなくキー省略、devtoolsで見ても存在しない
    ことを明示するため）。"""
    view = {
        "case_key": case_key, "case_type": case_def["case_type"], "title": case_def["title"],
        "status": (run or {}).get("status", "not_started"),
        "expert_first_input": (run or {}).get("expert_first_input"),
        "locked_at": (run or {}).get("locked_at"),
        "revealed_at": (run or {}).get("revealed_at"),
        "submitted_at": (run or {}).get("submitted_at"),
        "run_id": (run or {}).get("run_id"),
    }
    reveal_allowed = case_def["case_type"] == "gold" or bool((run or {}).get("revealed_at"))
    if reveal_allowed:
        view["response_id"] = case_def["response_id"]
    return view


# ---- Evaluation / Gap Review --------------------------------------------------

def get_evaluation(client, run_id: str) -> dict | None:
    rows = client.select("geo_expert_session_evaluations", {
        "select": "*", "run_id": f"eq.{run_id}", "limit": "1"})
    return rows[0] if rows else None


def save_evaluation(client, case_key: str, *, expert_identity_id: str,
                     rubric_assessments: dict = None, feedback_tags: list = None,
                     expert_comment: str = None, gap_review: dict = None) -> dict:
    """部分更新のupsert（draft保存、§16 Reload Testのための永続化）。Calibrationの
    gap_reviewは、Reveal前（revealed_atが無い）には書き込ませない——Reveal前にGapを埋める
    操作自体が、Blind Integrityの趣旨（先にAIを見ない）に反するため。"""
    run = _require_run(client, case_key, expert_identity_id)
    if gap_review is not None and run["case_type"] == "calibration" and not run.get("revealed_at"):
        raise ExpertSessionCaptureForbiddenError("Reveal前はGap Reviewを保存できません")

    existing = get_evaluation(client, run["run_id"])
    patch = {}
    if rubric_assessments is not None:
        existing_rubric = (existing or {}).get("rubric_assessments") or {}
        patch["rubric_assessments"] = {**existing_rubric, **rubric_assessments}
    if feedback_tags is not None:
        patch["feedback_tags"] = feedback_tags
    if expert_comment is not None:
        patch["expert_comment"] = expert_comment
    if gap_review is not None:
        existing_gap_review = (existing or {}).get("gap_review") or {}
        patch["gap_review"] = {**existing_gap_review, **gap_review}

    if not existing:
        row = {"run_id": run["run_id"], **patch}
        created = client.insert("geo_expert_session_evaluations", [row])[0]
        geo_audit.log_audit(client, actor=expert_identity_id,
                             action="expert_session_evaluation_create",
                             target_table="geo_expert_session_evaluations",
                             target_id=created["evaluation_id"], detail={"case_key": case_key})
        return created
    patch["updated_at"] = _now()
    client.update("geo_expert_session_evaluations", {"run_id": f"eq.{run['run_id']}"}, patch)
    geo_audit.log_audit(client, actor=expert_identity_id,
                         action="expert_session_evaluation_update",
                         target_table="geo_expert_session_evaluations",
                         target_id=existing["evaluation_id"], detail={"case_key": case_key})
    return get_evaluation(client, run["run_id"])
