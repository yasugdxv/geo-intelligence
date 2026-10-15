"""Expert Session Capture V1 API層: 認証境界の配線確認（既存tests/test_geo_ios_review_platform_
p0_remediation.pyのパターンを踏襲）。client自己申告のexpert_identityを受け取るフィールドが
一切無いこと（body modelにidentity/expert_identity_id系のフィールドが存在しないこと）も
ここで確認する——なりすまし経路自体が存在しないことの配線確認。
"""
import inspect

import pytest
from fastapi.params import Depends as DependsMarker

import geo_expert_session_capture_api as capture_api
import geo_ios_identity


def _requires_ios_identity(fn) -> bool:
    for param in inspect.signature(fn).parameters.values():
        default = param.default
        if isinstance(default, DependsMarker) and default.dependency is geo_ios_identity.verify_ios_identity:
            return True
    return False


@pytest.mark.parametrize("fn", [
    capture_api.list_cases,
    capture_api.get_case,
    capture_api.start_case,
    capture_api.save_expert_first_input,
    capture_api.lock_case,
    capture_api.reveal_case,
    capture_api.submit_case,
    capture_api.save_evaluation,
])
def test_all_endpoints_require_ios_identity(fn):
    assert _requires_ios_identity(fn), (
        f"{fn.__name__} に Depends(geo_ios_identity.verify_ios_identity) が無い"
        "（なりすまし経路になり得る）")


def test_request_models_have_no_client_submittable_identity_field():
    """ExpertFirstInputRequest/SaveEvaluationRequestに、expert_identity_id等をclientが
    自己申告できるフィールドが無いこと（identityは常にDependsの認証結果からのみ取る設計）。"""
    for model in (capture_api.ExpertFirstInputRequest, capture_api.SaveEvaluationRequest):
        field_names = set(model.model_fields.keys())
        assert not any("identity" in f for f in field_names), (
            f"{model.__name__} に identity自己申告フィールドがある: {field_names}")
