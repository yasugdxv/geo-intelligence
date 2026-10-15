"""Expert Session Capture V1 API（Phase S2-S3、2026-10-02）。

/api/geo/expert-session/* にマウントする。全endpointをgeo_ios_identity.verify_ios_identityで
認証する——client自己申告のexpert_identityでの成りすましを防ぐため（§20 Security）。
response_idやReading Presentation JSON自体はこのrouterでは一切組み立てない——Reveal後、
フロントエンドが既存のPhase S1 route（/api/geo/situation-presentation/{response_id}）を
そのまま呼ぶ（Reading表示ロジックを二重実装しない、ユーザー指示§13）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config_utils import load_config
from geo_db_client import SupabaseClient

import geo_expert_session_capture_service as capture_service
import geo_ios_identity

_config = load_config()
_client = SupabaseClient(_config)

geo_expert_session_capture_router = APIRouter(prefix="/api/geo/expert-session")


def _case_def_or_404(case_key: str) -> dict:
    try:
        return capture_service.get_case_definition(case_key)
    except capture_service.ExpertSessionCaptureError as e:
        raise HTTPException(status_code=404, detail=str(e))


@geo_expert_session_capture_router.get("/cases")
def list_cases(identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """Session Home向け: runnable 7件 + unresolved 1件の一覧。認証済みidentity自身のrun
    進捗（無ければnot_started）を添えて返す。Calibrationでrevealed_atが無い場合、
    response_idキー自体を含めない（build_public_case_view参照、Blind Integrity）。"""
    registry = capture_service.list_case_definitions()
    runnable = []
    for case_key, case_def in registry["runnable"].items():
        run = capture_service.get_run(_client, case_key, identity.identity_id)
        runnable.append(capture_service.build_public_case_view(case_key, case_def, run))
    unresolved = [
        {"case_key": k, "case_type": v["case_type"], "title": v["title"], "status": "unresolved"}
        for k, v in registry["unresolved"].items()
    ]
    return {"runnable": runnable, "unresolved": unresolved}


@geo_expert_session_capture_router.get("/cases/{case_key}")
def get_case(case_key: str,
             identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    case_def = _case_def_or_404(case_key)
    run = capture_service.get_run(_client, case_key, identity.identity_id)
    view = capture_service.build_public_case_view(case_key, case_def, run)
    if run:
        evaluation = capture_service.get_evaluation(_client, run["run_id"])
        view["evaluation"] = evaluation
    return view


@geo_expert_session_capture_router.post("/cases/{case_key}/start")
def start_case(case_key: str,
               identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    case_def = _case_def_or_404(case_key)
    run = capture_service.start_run(_client, case_key, expert_identity_id=identity.identity_id)
    return capture_service.build_public_case_view(case_key, case_def, run)


class ExpertFirstInputRequest(BaseModel):
    situation: str | None = None
    what_to_look_at: str | None = None
    key_actor: str | None = None
    relationship_structure: str | None = None
    multiple_readings: str | None = None
    primary_reading: str | None = None
    supporting_evidence: str | None = None
    weakening_evidence: str | None = None
    switch_condition: str | None = None


@geo_expert_session_capture_router.patch("/cases/{case_key}/expert-first-input")
def save_expert_first_input(case_key: str, req: ExpertFirstInputRequest,
                             identity: geo_ios_identity.IosIdentity = Depends(
                                 geo_ios_identity.verify_ios_identity)):
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        run = capture_service.save_expert_first_input(
            _client, case_key, expert_identity_id=identity.identity_id, expert_first_input=payload)
    except capture_service.ExpertSessionCaptureForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except capture_service.ExpertSessionCaptureError as e:
        raise HTTPException(status_code=400, detail=str(e))
    case_def = _case_def_or_404(case_key)
    return capture_service.build_public_case_view(case_key, case_def, run)


@geo_expert_session_capture_router.post("/cases/{case_key}/lock")
def lock_case(case_key: str,
              identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    try:
        run = capture_service.lock_run(_client, case_key, expert_identity_id=identity.identity_id)
    except capture_service.ExpertSessionCaptureError as e:
        raise HTTPException(status_code=422, detail=str(e))
    case_def = _case_def_or_404(case_key)
    return capture_service.build_public_case_view(case_key, case_def, run)


@geo_expert_session_capture_router.post("/cases/{case_key}/reveal")
def reveal_case(case_key: str,
                 identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    try:
        run = capture_service.reveal_run(_client, case_key, expert_identity_id=identity.identity_id)
    except capture_service.ExpertSessionCaptureForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except capture_service.ExpertSessionCaptureError as e:
        raise HTTPException(status_code=400, detail=str(e))
    case_def = _case_def_or_404(case_key)
    return capture_service.build_public_case_view(case_key, case_def, run)


@geo_expert_session_capture_router.post("/cases/{case_key}/submit")
def submit_case(case_key: str,
                identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    try:
        run = capture_service.submit_run(_client, case_key, expert_identity_id=identity.identity_id)
    except capture_service.ExpertSessionCaptureError as e:
        raise HTTPException(status_code=400, detail=str(e))
    case_def = _case_def_or_404(case_key)
    return capture_service.build_public_case_view(case_key, case_def, run)


class SaveEvaluationRequest(BaseModel):
    rubric_assessments: dict | None = None
    feedback_tags: list[str] | None = None
    expert_comment: str | None = None
    gap_review: dict | None = None


@geo_expert_session_capture_router.patch("/cases/{case_key}/evaluation")
def save_evaluation(case_key: str, req: SaveEvaluationRequest,
                     identity: geo_ios_identity.IosIdentity = Depends(
                         geo_ios_identity.verify_ios_identity)):
    try:
        return capture_service.save_evaluation(
            _client, case_key, expert_identity_id=identity.identity_id,
            rubric_assessments=req.rubric_assessments, feedback_tags=req.feedback_tags,
            expert_comment=req.expert_comment, gap_review=req.gap_review)
    except capture_service.ExpertSessionCaptureForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except capture_service.ExpertSessionCaptureError as e:
        raise HTTPException(status_code=400, detail=str(e))
