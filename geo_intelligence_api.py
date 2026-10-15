"""地政学インテリジェンス基盤 Phase4: Geo Intelligence Agent APIルーター（Inter-App安定化）。

外部アプリ（Sustainability/ERM/SCM/PA等）向けの安定した問い合わせ窓口として、既存の内部向け
/api/geo/* とは独立したパス空間（/api/v1/geo-intelligence/*）にマウントする。既存api_server.py
の変更は「このモジュールのimportとinclude_routerの2行追加」のみ
（既存geo_expert_review_api.py分離時と同じ方針）。

既存Geopolitical Expert AI（既存/api/geo/expert/*経由でのみ利用）を、Geo Intelligence Agent
専用の判断（Knowledge sufficient かつ analysis_requestedの場合のみ）で内部から呼び出す。
外部アプリがExpert AIを直接呼び出す経路にはしない（geo_intelligence_serviceが仲介する）。

Inter-App向け安定化（Phase4 item10）:
    - request id: geo_intelligence_serviceが常にrequest_id付きResponseを返す（既存のまま）
    - idempotency: 同一request_idの再送は既存Responseをそのまま返す（geo_intelligence_service
      側で実装、この層は無変更で恩恵を受ける）
    - structured error: 内部失敗はstatus='failed'+error{code,message}のResponse（200）として
      返す。Timeout/予期しない例外もこの層で同じ形へ揃える
    - timeout handling: ThreadPoolExecutorでパイプライン全体に時間上限を課す
    - API logging / latency logging: geo_intelligence_service.process_request内でgeo_audit
      （既存監査ログ）へlatency_msを記録する
    - Auth: geo_intelligence_auth.verify_api_key（開発時API Key、将来Entra ID等へ差し替え
      可能な抽象化）
"""
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from fastapi import APIRouter, Depends, HTTPException

from config_utils import load_config  # noqa: E402
from geo_db_client import SupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

import geo_intelligence_service
from geo_intelligence_auth import verify_api_key
from geo_intelligence_schema import GeoIntelligenceRequest, GeoIntelligenceResponse

_config = load_config()
_client = SupabaseClient(_config)
_azure_client, _model = make_openai_client(_config)

REQUEST_TIMEOUT_SECONDS = 60
# BUGFIX: weekly_monitoringはpriority_geographies×sustainability_themesを横断探索するため
# user_question（単一の問いへの回答）よりDB往復回数が本質的に多く、60秒では実データ量次第で
# 恒常的にタイムアウトすることがReal-data Quality Validationで判明した（Step1実施時）。
# Retrieval/Ranking等の処理内容自体は変更せず、request_typeごとの時間上限のみ分ける。
WEEKLY_MONITORING_TIMEOUT_SECONDS = 180
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="geo_intelligence_query")

geo_intelligence_router = APIRouter(prefix="/api/v1/geo-intelligence")


@geo_intelligence_router.post("/query", response_model=GeoIntelligenceResponse)
def query(req: GeoIntelligenceRequest, caller_identity=Depends(verify_api_key)):
    """Phase F: caller_identity（認証済み、X-API-Key経由）をprocess_requestへ渡し、
    Access Policyの判定根拠をBody側のrequester_app自己申告からこちらへ移す
    （requester_appへのなりすましによる権限昇格を防ぐ）。"""
    payload = req.model_dump(mode="json")
    request_id = payload.get("request_id") or str(uuid.uuid4())
    payload["request_id"] = request_id
    timeout_seconds = (WEEKLY_MONITORING_TIMEOUT_SECONDS if payload.get("request_type") == "weekly_monitoring"
                        else REQUEST_TIMEOUT_SECONDS)

    try:
        future = _executor.submit(
            geo_intelligence_service.process_request, _client, payload,
            azure_client=_azure_client, model=_model, caller_identity=caller_identity)
        response = future.result(timeout=timeout_seconds)
    except geo_intelligence_service.GeoIntelligenceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FutureTimeoutError:
        return {
            "request_id": request_id, "status": "failed", "geo_assessment": None,
            "key_stakeholders": [], "political_dynamics": None, "outlook": None,
            "cross_domain_implications": None, "references": [], "confidence": "low",
            "knowledge_sufficiency": "insufficient", "knowledge_gap": None,
            "error": {"code": "timeout", "message": f"{timeout_seconds}秒以内に処理が完了しませんでした"},
            "completed_at": None,
        }
    except Exception as e:  # noqa: BLE001 — 予期しない例外もstructured errorへ揃える（生の500を返さない）
        return {
            "request_id": request_id, "status": "failed", "geo_assessment": None,
            "key_stakeholders": [], "political_dynamics": None, "outlook": None,
            "cross_domain_implications": None, "references": [], "confidence": "low",
            "knowledge_sufficiency": "insufficient", "knowledge_gap": None,
            "error": {"code": type(e).__name__, "message": str(e)},
            "completed_at": None,
        }
    return response


@geo_intelligence_router.get("/requests/{request_id}")
def get_request(request_id: str):
    rows = _client.select("geo_intelligence_requests", {
        "select": "*", "request_id": f"eq.{request_id}", "limit": "1",
    })
    if not rows:
        raise HTTPException(status_code=404, detail="request_idが見つかりません")
    return rows[0]


@geo_intelligence_router.get("/requests/{request_id}/response")
def get_response(request_id: str):
    rows = _client.select("geo_intelligence_responses", {
        "select": "*", "request_id": f"eq.{request_id}", "order": "created_at.desc", "limit": "1",
    })
    if not rows:
        raise HTTPException(status_code=404, detail="request_idに対するResponseが見つかりません")
    return rows[0]
