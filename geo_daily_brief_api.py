"""Phase E: Daily Intelligence Brief API。

GETのみ。「毎朝、今日どこを見るべきか」を1回のAPI呼び出しで返す。LLMは呼ばない
（geo_daily_brief_service参照）。Collectionの手動実行等、副作用のある操作はここでは
行わない——再取得（refresh）はDBの現在状態を読み直すだけ。
"""
from fastapi import APIRouter

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402

import geo_daily_brief_service as daily_brief

_client = SupabaseClient(load_config())

geo_daily_brief_router = APIRouter(prefix="/api/geo/daily-brief")


@geo_daily_brief_router.get("")
def get_daily_brief(is_demo: bool = False, since: str | None = None):
    """今日のインテリジェンス。sinceは前回確認時刻（クライアントのlocalStorage由来、ISO8601、
    省略時は直近24時間にフォールバック）。NEW状態のTheme Candidate・未確認Alertは
    sinceに関わらず常に対象（未確認を優先）。"""
    return daily_brief.get_daily_brief(_client, is_demo=is_demo, since=since)
