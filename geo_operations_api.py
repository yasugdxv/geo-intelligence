"""Phase C: インテリジェンス運用（Pipeline Health）のSummary API。

GETのみ。表示用の集計で、LLMは呼ばない。Theme同期の実行は既存
/api/geo/theme-candidates/sync-cluster・sync-pending を使う（本モジュールでは再実装しない）。
"""
from fastapi import APIRouter

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_operations_service as operations

_client = SupabaseClient(load_config())

geo_operations_router = APIRouter(prefix="/api/geo/operations")


@geo_operations_router.get("/summary")
def get_operations_summary(is_demo: bool = False):
    """収集・レビュー・Theme同期・Theme Candidate・Monitoringの状態と「次にやること」を1回で返す。
    一部のセクションが取得できなくても、できたセクションは返す（errorsに理由）。"""
    return operations.get_operations_summary(_client, is_demo=is_demo)
