"""Geopolitical Intelligence OS: Workflow Board API（読み取り専用集計）。

SEC: 案件title/workflow state/reviewer/report metadataを含むため、authenticated
IOS identity（geo_ios_identity.verify_ios_identity）を必須とし、role別に返す案件を
絞り込む（geo_ios_workflow_board_service.filter_cases_for_identity）。UI Selectや
フロントエンドのフィルタはSecurity Boundaryではない——unauthorizedなcallerは
そもそも他人の案件のtitle等をレスポンスに含めない。
"""
from fastapi import APIRouter, Depends
from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_ios_identity
import geo_ios_workflow_board_service as board_service

_config = load_config()
_client = SupabaseClient(_config)

geo_ios_workflow_board_router = APIRouter(prefix="/api/geo/ios/workflow-board")


@geo_ios_workflow_board_router.get("")
def get_workflow_board(identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    all_cases = board_service.list_board_cases(_client)
    cases = board_service.filter_cases_for_identity(all_cases, identity)
    return {"cases": cases}


@geo_ios_workflow_board_router.get("/home")
def get_workflow_board_home(identity: geo_ios_identity.IosIdentity = Depends(geo_ios_identity.verify_ios_identity)):
    """Home相当のメトリクス・タスク・再開候補。get_workflow_boardと同じrole別絞り込みを
    適用した上で集計するだけ——このidentityがBoardで見える範囲を超えたcaseは含まない。"""
    all_cases = board_service.list_board_cases(_client)
    cases = board_service.filter_cases_for_identity(all_cases, identity)
    return board_service.compute_home_summary(cases, identity)
