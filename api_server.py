"""
地政学インテリジェンス基盤 React版フロントエンド用バックエンドAPI。

eco-digest-spark (React/Vite) から呼び出される。Sustainabilityアプリ
(world_monitor_dashboard)とは完全に独立しており、Sustainability側のコード・DB
テーブルへは一切依存しない（Phase 3B Step 3でSustainability read-surfaceを除去済み）。

起動: python api_server.py  (http://127.0.0.1:8000)
"""
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Post-Freeze Integrity Audit Finding CRITICAL-5(2026-09-22発見)対応: Expert AI
# Capability Flag（GEO_EXPERT_FOUNDATION_CONTEXT_V1_ENABLED等）は、`EXPERT_AI_SESSION_
# PRE_V1` Freezeで「ON」と宣言されているにもかかわらず、デプロイ経路のどこにも設定
# されておらず、再起動・再デプロイ（`python api_server.py`／`uvicorn api_server:app`
# いずれの起動方法でも）のたびに気づかれないまま全てOFFへ戻るリスクがあった
# （geo_expert_engine.pyが素朴なos.environ.getなので、環境変数で明示上書きされない限り
# この既定値が使われる）。モジュールimport時点（どの起動経路でも必ず通る）で設定する。
# setdefaultのため、Application Settings等で明示的に上書きされている場合はそちらが
# 優先される——Freeze仕様の既定値をコードで記録するだけで、上書き経路は塞がない。
os.environ.setdefault("GEO_EXPERT_FOUNDATION_CONTEXT_V1_ENABLED", "1")
os.environ.setdefault("GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED", "1")
os.environ.setdefault("GEO_EXPERT_INSTITUTION_INTELLIGENCE_ENABLED", "1")

sys.path.insert(0, str(Path(__file__).parent))
from config_utils import load_config, make_proxies  # noqa: E402
from geo_db_client import SupabaseClient as GeoSupabaseClient  # noqa: E402
from ai_client import make_openai_client  # noqa: E402

# 地政学インテリジェンス基盤（geo_*）: 既存のサステナビリティ/競合モニタリング機能とは
# 完全に独立させ、/api/geo/* 配下のAPIRouterとしてのみ追加する。既存エンドポイントは無改変。
from fastapi import APIRouter, UploadFile, File, Form  # noqa: E402
import geo_llm_common  # noqa: E402,F401
import geo_event_extractor  # noqa: E402
import geo_documents_store  # noqa: E402
import geo_freshness_service  # noqa: E402
import geo_indicator_engine  # noqa: E402
import geo_theme_seed  # noqa: E402
import geo_audit  # noqa: E402
import geo_official_crawler  # noqa: E402
import geo_expert_schema  # noqa: E402
import geo_expert_engine  # noqa: E402
import geo_knowledge_api  # noqa: E402
import geo_expert_review_api  # noqa: E402
import geo_external_signal_api  # noqa: E402
import geo_cors  # noqa: E402
import geo_external_network_api  # noqa: E402
import geo_intelligence_ai_preset_api  # noqa: E402
import geo_external_event_api  # noqa: E402
import geo_intelligence_api  # noqa: E402
import geo_intelligence_gap_api  # noqa: E402
import geo_intelligence_service  # noqa: E402
import geo_knowledge_gap_service  # noqa: E402
import geo_report_api  # noqa: E402
import geo_report_current_pipeline_api  # noqa: E402
import geo_report_design_api  # noqa: E402
import geo_report_prompt2_api  # noqa: E402
import geo_report_prompt_export_api  # noqa: E402
import geo_report_report_edit_api  # noqa: E402
import geo_report_studio_api  # noqa: E402
import geo_ios_analysis_session_api  # noqa: E402
import geo_ios_intake_api  # noqa: E402
import geo_ios_review_api  # noqa: E402
import geo_ios_approval_api  # noqa: E402
import geo_expert_session_capture_api  # noqa: E402
import geo_ios_learning_api  # noqa: E402
import geo_ios_learning_workbench_api  # noqa: E402
import geo_ios_knowledge_publication_api  # noqa: E402
import geo_ios_identity_api  # noqa: E402
import geo_ios_workflow_board_api  # noqa: E402
import geo_ios_situation_presentation_api  # noqa: E402
import geo_ios_consultation_api  # noqa: E402
import geo_ios_report_draft_api  # noqa: E402
import geo_report_review_v2_api  # noqa: E402
import geo_ios_review_import_api  # noqa: E402
import geo_intelligence_ai_api  # noqa: E402
import geo_theme_candidate_api  # noqa: E402
import geo_source_library_api  # noqa: E402
import geo_monitoring_theme_api  # noqa: E402
import geo_theme_discovery_live_api  # noqa: E402
import geo_operations_api  # noqa: E402
import geo_scheduler_api  # noqa: E402
import geo_daily_brief_api  # noqa: E402

_config = load_config()
_azure_client, _model = make_openai_client(_config)
_geo_client = GeoSupabaseClient(_config)
geo_router = APIRouter(prefix="/api/geo")

app = FastAPI(title="Geopolitics Intelligence API")

app.add_middleware(
    CORSMiddleware,
    # 許可するフロントのアドレス: ローカル開発（Viteの5173・8080〜8083）＋ 環境変数 CORS_ALLOWED_ORIGINS
    # （本番のフロントのアドレス。詳細は geo_cors.py）
    allow_origins=geo_cors.allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
    # バックが Private Endpoint（プライベートIP）でのみ到達できる環境では、公開アドレスのフロントから呼ぶと
    # ChromeのPrivate Network Accessでブロックされるため、明示的に許可する（サステナアプリと同じ。
    # 社内ネットワーク／VPN経由の利用が前提）
    allow_private_network=True,
    # ZIP Export時の無表示問題の修正（2026-10-02）: Content-DispositionはCORSの
    # デフォルト公開ヘッダー一覧に含まれないため、expose_headersで明示しない限り
    # フロントエンドのfetch()からres.headers.get("content-disposition")がnullになる
    # （ダウンロード自体は成功するがファイル名が取得できない既存バグ）。
    expose_headers=["Content-Disposition"],
)


@app.get("/api/health")
def health():
    """Geo専用ヘルスチェック。Sustainability側のリソース（knowledge store/competitor DB等）
    には一切触れず、Geo自身のDB到達性とAI機能可否のみを返す。"""
    db_reachable = True
    try:
        _geo_client.select("geo_themes", {"select": "theme_id", "limit": "1"})
    except Exception:
        db_reachable = False
    return {
        "status": "ok",
        "app": "geopolitics",
        "db": "reachable" if db_reachable else "unreachable",
        "chatEnabled": _azure_client is not None,
    }


# =====================================================================
# 地政学インテリジェンス基盤（Phase1 縦切り）
# 台湾テーマseed → 文書アップロード → Event抽出 → Evidence表示 →
# MIL-01のObservation → Executive Overview最小表示 → audit log
# =====================================================================

def _geo_theme_to_ui(t: dict) -> dict:
    return {
        "id": t["theme_id"], "code": t["code"], "nameJa": t["name_ja"],
        "nameEn": t.get("name_en"), "description": t.get("description"),
        "status": t.get("status"),
    }


def _geo_document_to_ui(d: dict) -> dict:
    return {
        "id": d["document_id"], "themeId": d["theme_id"], "title": d.get("title"),
        "url": d.get("url"), "publishedAt": d.get("published_at"), "fetchedAt": d.get("fetched_at"),
        "language": d.get("language"), "mimeType": d.get("mime_type"),
        "fetchStatus": d.get("fetch_status"), "isMock": d.get("is_mock", False),
        "extractedTextPreview": (d.get("extracted_text") or "")[:400],
    }


def _geo_event_to_ui(e: dict) -> dict:
    return {
        "id": e["event_id"], "themeId": e["theme_id"], "actor": e.get("actor"),
        "action": e.get("action"), "target": e.get("target"), "location": e.get("location"),
        "startAt": e.get("start_at"), "endAt": e.get("end_at"), "eventType": e.get("event_type"),
        "indicatorCode": e.get("indicator_code"),
        "confidence": e.get("confidence"), "unknownFields": e.get("unknown_fields") or {},
        "verified": e.get("verified", False), "verifiedBy": e.get("verified_by"),
    }


def _geo_evidence_to_ui(e: dict, documents: dict) -> dict:
    doc = documents.get(e.get("document_id"), {})
    return {
        "id": e["evidence_id"], "eventId": e["event_id"], "claimText": e.get("claim_text"),
        "quote": e.get("quote"), "sourceTier": e.get("source_tier"),
        "verificationStatus": e.get("verification_status"), "confidence": e.get("confidence"),
        "documentId": e.get("document_id"), "documentTitle": doc.get("title"),
        "documentUrl": doc.get("url"), "isMock": doc.get("is_mock", False),
    }


def _indicator_source_info(indicator_code: str) -> dict:
    """指標の計算根拠となっている情報源（名称・URL）をconnector設定から引く。
    指標詳細ページで「この数値は何を根拠にしているか」を示すために使う"""
    found = _find_official_crawl_job(indicator_code)
    if not found:
        return {"sourceName": None, "sourceUrl": None}
    if found["source"] is not None:
        return {"sourceName": found["source"]["name"], "sourceUrl": found["source"]["url"]}
    # DIRECT_METHODS（MKT-01等、ソース登録を経由しない生データ直結方式）
    if found["job"]["method"] == "market_data_api":
        return {"sourceName": "台湾証券取引所 OpenAPI / exchangerate-api.com",
                "sourceUrl": geo_indicator_engine.TWSE_INDEX_URL}
    return {"sourceName": None, "sourceUrl": None}


def _geo_indicator_to_ui(i: dict) -> dict:
    source_info = _indicator_source_info(i["indicator_code"])
    return {
        "id": i["indicator_id"], "themeId": i.get("theme_id"), "code": i["indicator_code"], "name": i["name"],
        "unit": i.get("unit"), "description": i.get("description"),
        "mvpEnabled": i.get("mvp_enabled", False),
        "displayOrder": i.get("display_order"),
        **source_info,
    }


def _geo_observation_to_ui(o: dict) -> dict:
    return {
        "id": o["observation_id"], "indicatorId": o["indicator_id"], "asOf": o.get("as_of"),
        "rawValue": o.get("raw_value"), "insufficientData": o.get("insufficient_data", False),
        "baseline7d": o.get("baseline_7d"), "baseline30d": o.get("baseline_30d"),
        "baseline90d": o.get("baseline_90d"), "deviation": o.get("deviation"),
        "confidence": o.get("confidence"), "dataQuality": o.get("data_quality"),
        "qualitativeLabel": o.get("qualitative_label"), "rationale": o.get("rationale"),
        "isMock": o.get("is_mock", False),
    }


@geo_router.post("/themes/seed/taiwan")
def geo_seed_taiwan_theme():
    result = geo_theme_seed.seed_taiwan_theme(_geo_client)
    geo_audit.log_audit(
        _geo_client, actor="system", action="theme_seed",
        target_table="geo_themes", target_id=result["theme"]["theme_id"],
        detail={"theme_created": result["theme_created"], "indicators_created": result["indicators_created"]},
    )
    return {
        "theme": _geo_theme_to_ui(result["theme"]),
        "themeCreated": result["theme_created"],
        "indicatorsCreated": result["indicators_created"],
        "ruleVersionCreated": result["rule_version_created"],
    }


@geo_router.get("/themes")
def geo_list_themes():
    themes = _geo_client.select("geo_themes", {"select": "*", "order": "created_at.asc"})
    return {"themes": [_geo_theme_to_ui(t) for t in themes]}


class GeoThemeCreateRequest(BaseModel):
    name_ja: str
    name_en: str | None = None
    description: str | None = None


@geo_router.post("/themes")
def geo_create_theme(req: GeoThemeCreateRequest):
    """Report Upload画面等から、既存Themeに該当が無い場合の新規Theme登録用。
    codeはユーザーに意識させず内部識別子として自動採番する（geo_themes_code_idxの
    一意制約を満たすため衝突がほぼ起こらないuuid由来の値にする）。"""
    name_ja = req.name_ja.strip()
    if not name_ja:
        raise HTTPException(status_code=400, detail="name_jaは必須です")
    code = f"theme_{uuid.uuid4().hex[:12]}"
    inserted = _geo_client.insert("geo_themes", [{
        "code": code, "name_ja": name_ja, "name_en": (req.name_en or "").strip() or None,
        "description": (req.description or "").strip() or None, "status": "有効",
    }])[0]
    return {"theme": _geo_theme_to_ui(inserted)}


@geo_router.post("/documents/upload")
async def geo_upload_document(
    theme_id: str = Form(...),
    title: str = Form(""),
    url: str = Form(""),
    published_at: str = Form(""),
    is_mock: bool = Form(False),
    file: UploadFile = File(...),
):
    themes = _geo_client.select("geo_themes", {"select": "code", "theme_id": f"eq.{theme_id}", "limit": "1"})
    if not themes:
        raise HTTPException(status_code=404, detail="テーマが見つかりません")
    theme_code = themes[0]["code"]
    raw_bytes = await file.read()
    result = geo_documents_store.save_document(
        _geo_client, theme_id=theme_id, theme_code=theme_code, source_id=None,
        filename=file.filename or "upload", mime_type=file.content_type or "",
        raw_bytes=raw_bytes, title=title, url=url,
        published_at=published_at or None, is_mock=is_mock,
    )
    geo_audit.log_audit(
        _geo_client, actor="analyst", action="document_upload",
        target_table="geo_documents", target_id=result["document"]["document_id"],
        detail={"is_new": result["is_new"], "filename": file.filename},
    )
    return {"document": _geo_document_to_ui(result["document"]), "isNew": result["is_new"]}


@geo_router.get("/documents")
def geo_list_documents(theme_id: str = ""):
    params = {"select": "*", "order": "fetched_at.desc"}
    if theme_id:
        params["theme_id"] = f"eq.{theme_id}"
    documents = _geo_client.select("geo_documents", params)
    return {"documents": [_geo_document_to_ui(d) for d in documents]}


@geo_router.post("/documents/{document_id}/extract-events")
def geo_extract_events(document_id: str):
    docs = _geo_client.select("geo_documents", {"select": "*", "document_id": f"eq.{document_id}", "limit": "1"})
    if not docs:
        raise HTTPException(status_code=404, detail="文書が見つかりません")
    document = docs[0]
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")

    themes = _geo_client.select("geo_themes", {
        "select": "code", "theme_id": f"eq.{document['theme_id']}", "limit": "1",
    })
    theme_code = themes[0]["code"] if themes else ""
    indicator_choices = geo_event_extractor.load_indicator_codes(_geo_client, document["theme_id"])
    extracted = geo_event_extractor.extract_events(
        _azure_client, _model, document.get("extracted_text") or "", theme_code, indicator_choices,
    )

    created_events = []
    for ev in extracted:
        event_row = _geo_client.insert("geo_events", [{
            "theme_id": document["theme_id"],
            "actor": ev.get("actor"), "action": ev.get("action"), "target": ev.get("target"),
            "location": ev.get("location"), "start_at": ev.get("start_at"), "end_at": ev.get("end_at"),
            "quantities": ev.get("quantities"), "event_type": ev.get("event_type"),
            "indicator_code": ev.get("indicator_code"),
            "confidence": ev.get("confidence"), "unknown_fields": ev.get("unknown_fields"),
        }])[0]
        _geo_client.insert("geo_evidence", [{
            "event_id": event_row["event_id"], "document_id": document_id,
            "claim_text": ev.get("claim_text") or "", "quote": ev.get("quote"),
            "primary_flag": True, "confidence": ev.get("confidence"),
        }], prefer="return=minimal")
        created_events.append(event_row)

    geo_audit.log_audit(
        _geo_client, actor="analyst", action="extract_events",
        target_table="geo_documents", target_id=document_id,
        detail={"events_created": len(created_events)},
    )
    return {"eventsCreated": len(created_events)}


@geo_router.get("/events")
def geo_list_events(theme_id: str = "", verified: str = ""):
    params = {"select": "*", "order": "created_at.desc"}
    if theme_id:
        params["theme_id"] = f"eq.{theme_id}"
    if verified in ("true", "false"):
        params["verified"] = f"eq.{verified}"
    events = _geo_client.select("geo_events", params)
    return {"events": [_geo_event_to_ui(e) for e in events]}


class GeoEventVerifyRequest(BaseModel):
    verified: bool
    verified_by: str = "analyst"


@geo_router.post("/events/{event_id}/verify")
def geo_verify_event(event_id: str, req: GeoEventVerifyRequest):
    _geo_client.update("geo_events", {"event_id": f"eq.{event_id}"}, {
        "verified": req.verified,
        "verified_by": req.verified_by if req.verified else None,
        "verified_at": datetime.now(timezone.utc).isoformat() if req.verified else None,
    })
    geo_audit.log_audit(
        _geo_client, actor=req.verified_by, action="verify_event",
        target_table="geo_events", target_id=event_id, detail={"verified": req.verified},
    )
    return {"ok": True}


@geo_router.get("/evidence")
def geo_list_evidence(event_id: str = ""):
    params = {"select": "*", "order": "created_at.desc"}
    if event_id:
        params["event_id"] = f"eq.{event_id}"
    evidence = _geo_client.select("geo_evidence", params)
    document_ids = sorted({e["document_id"] for e in evidence if e.get("document_id")})
    documents = {}
    if document_ids:
        docs = _geo_client.select("geo_documents", {
            "select": "document_id,title,url,is_mock", "document_id": f"in.({','.join(document_ids)})",
        })
        documents = {d["document_id"]: d for d in docs}
    return {"evidence": [_geo_evidence_to_ui(e, documents) for e in evidence]}


@geo_router.get("/indicators")
def geo_list_indicators(theme_id: str = ""):
    params = {"select": "*", "order": "display_order.asc"}
    if theme_id:
        params["theme_id"] = f"eq.{theme_id}"
    indicators = _geo_client.select("geo_indicator_definitions", params)
    return {"indicators": [_geo_indicator_to_ui(i) for i in indicators]}


@geo_router.get("/indicators/{indicator_id}/observations")
def geo_indicator_observations(indicator_id: str, limit: int = 30):
    observations = _geo_client.select("geo_observations", {
        "select": "*", "indicator_id": f"eq.{indicator_id}", "order": "as_of.desc", "limit": str(limit),
    })
    return {"observations": [_geo_observation_to_ui(o) for o in observations]}


@geo_router.get("/indicators/monitor")
def geo_indicator_monitor():
    """全テーマ横断でmvp_enabled指標の最新値・乖離・鮮度・直近Alertを一覧表示する
    （Phase2B Indicator Monitor画面向け集約API。新しい計算ロジックは持たず、既存の
    indicator一覧・最新observation取得・geo_indicator_alerts取得をオーケストレーションするのみ）。"""
    indicators = _geo_client.select("geo_indicator_definitions", {
        "select": "*", "mvp_enabled": "eq.true", "order": "display_order.asc",
    })
    rows = []
    for indicator in indicators:
        latest = _geo_client.select("geo_observations", {
            "select": "*", "indicator_id": f"eq.{indicator['indicator_id']}",
            "order": "as_of.desc", "limit": "1",
        })
        latest_observation = latest[0] if latest else None
        freshness = geo_freshness_service.classify_indicator_freshness(indicator, latest_observation)
        alerts = _geo_client.select("geo_indicator_alerts", {
            "select": "*", "indicator_id": f"eq.{indicator['indicator_id']}",
            "order": "triggered_at.desc", "limit": "1",
        })
        rows.append({
            **_geo_indicator_to_ui(indicator),
            "latestObservation": _geo_observation_to_ui(latest_observation) if latest_observation else None,
            "freshness": freshness,
            "latestAlert": alerts[0] if alerts else None,
        })
    return {"indicators": rows}


def _find_official_crawl_job(indicator_code: str):
    """geo_official_crawler.SOURCES・DIRECT_METHODSから、この指標コードに対応する
    source設定・job設定を探す。qualitative_llm_state/regex_level_from_document/
    financial_disclosure_parse方式はソース設定が必要なため、connector側の設定を
    そのまま参照する（重複定義しない）。market_data_api（DIRECT_METHODS）はソース不要のためsource=None"""
    for source in geo_official_crawler.SOURCES:
        for job in source.get("jobs", []):
            if job["indicator_code"] == indicator_code:
                return {"source": source, "job": job}
    for job in geo_official_crawler.DIRECT_METHODS:
        if job["indicator_code"] == indicator_code:
            return {"source": None, "job": job}
    return None


@geo_router.post("/indicators/{indicator_id}/recalculate")
def geo_recalculate_indicator(indicator_id: str):
    indicators = _geo_client.select("geo_indicator_definitions", {
        "select": "*", "indicator_id": f"eq.{indicator_id}", "limit": "1",
    })
    if not indicators:
        raise HTTPException(status_code=404, detail="指標が見つかりません")
    indicator = indicators[0]
    theme_id = indicator["theme_id"]

    rules = _geo_client.select("geo_indicator_rule_versions", {
        "select": "*", "indicator_id": f"eq.{indicator_id}", "order": "version.desc", "limit": "1",
    })
    if rules:
        rule = rules[0]
    else:
        # ルールversion未登録の場合、connector側(geo_official_crawler.SOURCES)に登録済みの
        # methodがあればそれを使って自動作成する（事前手動登録を必須にしない）
        found_job = _find_official_crawl_job(indicator["indicator_code"])
        if not found_job:
            raise HTTPException(status_code=404, detail="この指標のルールversionが見つかりません")
        method_ = found_job["job"]["method"]
        extra = {"window_days": 30} if method_ == "event_count_window" else None
        rule = geo_indicator_engine.ensure_rule_version(_geo_client, indicator_id, method_, extra)
    rule_definition = rule.get("rule_definition") or {}
    method = rule_definition.get("method", "event_count_window")

    if method == "event_count_window":
        observation_row = geo_indicator_engine.compute_event_count_observation(
            _geo_client, indicator_id, indicator["indicator_code"], rule["rule_version_id"], theme_id,
            window_days=rule_definition.get("window_days", 30),
        )
    elif method == "qualitative_llm_state":
        found = _find_official_crawl_job(indicator["indicator_code"])
        if not found or not _azure_client:
            raise HTTPException(status_code=503, detail="定性分類の設定(ソース・states・LLM)が未整備です")
        source_row = geo_official_crawler.ensure_source(_geo_client, theme_id, found["source"])
        observation_row = geo_indicator_engine.compute_qualitative_observation(
            _geo_client, _azure_client, _model, indicator_id, rule["rule_version_id"],
            theme_id, source_row["source_id"], found["job"]["states"],
        )
    elif method == "regex_level_from_document":
        found = _find_official_crawl_job(indicator["indicator_code"])
        if not found:
            raise HTTPException(status_code=503, detail="ソース設定が未整備です")
        source_row = geo_official_crawler.ensure_source(_geo_client, theme_id, found["source"])
        observation_row = geo_indicator_engine.compute_regex_level_observation(
            _geo_client, indicator_id, rule["rule_version_id"], theme_id, source_row["source_id"],
        )
    elif method == "financial_disclosure_parse":
        found = _find_official_crawl_job(indicator["indicator_code"])
        if not found:
            raise HTTPException(status_code=503, detail="ソース設定が未整備です")
        observation_row = geo_indicator_engine.compute_financial_disclosure_observation(
            _geo_client, indicator_id, rule["rule_version_id"],
            make_proxies(_config), _config.get("ssl", {}).get("verify", True), url=found["source"]["url"],
        )
    elif method == "market_data_api":
        observation_row = geo_indicator_engine.compute_market_data_observation(
            _geo_client, indicator_id, rule["rule_version_id"],
            make_proxies(_config), _config.get("ssl", {}).get("verify", True),
        )
    else:
        raise HTTPException(status_code=400, detail=f"未対応の計算方式です: {method}")

    inserted = _geo_client.insert("geo_observations", [observation_row])[0]
    geo_audit.log_audit(
        _geo_client, actor="system", action="recalculate_indicator",
        target_table="geo_observations", target_id=inserted["observation_id"],
        detail={"indicator_code": indicator["indicator_code"]},
    )

    # Phase2B: 閾値超過アラート（Observation単位で冪等、rule_definition.thresholds未定義なら何もしない）
    severity = geo_indicator_engine.evaluate_threshold(inserted, rule_definition)
    alert = geo_indicator_engine.create_alert_if_needed(_geo_client, observation=inserted, severity=severity)

    return {"observation": _geo_observation_to_ui(inserted), "alert": alert}


@geo_router.post("/official-sources/crawl")
def geo_crawl_official_sources(theme_code: str = geo_official_crawler.THEME_CODE):
    """登録済み公式ソース（台湾国防部・海巡署・大陸委員会・総統府・外務省・NICS等）を
    手動トリガーでクロールする。Phase2bで定期スケジュール化する想定のMVP実装"""
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")
    results = geo_official_crawler.run_theme_crawl(theme_code)
    return {"results": results}


@geo_router.get("/overview")
def geo_overview(theme_id: str = ""):
    if theme_id:
        themes = _geo_client.select("geo_themes", {"select": "*", "theme_id": f"eq.{theme_id}", "limit": "1"})
        if not themes:
            raise HTTPException(status_code=404, detail="テーマが見つかりません")
    else:
        themes = _geo_client.select("geo_themes", {"select": "*", "order": "created_at.asc", "limit": "1"})
        if not themes:
            return {"theme": None}
    theme = themes[0]
    theme_id = theme["theme_id"]

    mil01 = _geo_client.select("geo_indicator_definitions", {
        "select": "*", "theme_id": f"eq.{theme_id}", "indicator_code": "eq.MIL-01", "limit": "1",
    })
    latest_observation = None
    if mil01:
        obs = _geo_client.select("geo_observations", {
            "select": "*", "indicator_id": f"eq.{mil01[0]['indicator_id']}",
            "order": "as_of.desc", "limit": "1",
        })
        if obs:
            latest_observation = _geo_observation_to_ui(obs[0])

    unverified_events = _geo_client.select("geo_events", {
        "select": "event_id", "theme_id": f"eq.{theme_id}", "verified": "eq.false",
    })
    recent_audit = _geo_client.select("geo_audit_logs", {
        "select": "*", "order": "created_at.desc", "limit": "10",
    })

    return {
        "theme": _geo_theme_to_ui(theme),
        "mil01Observation": latest_observation,
        "unverifiedEventCount": len(unverified_events),
        "recentAuditLogs": [{
            "id": a["audit_id"], "actor": a["actor"], "action": a["action"],
            "targetTable": a.get("target_table"), "createdAt": a.get("created_at"),
        } for a in recent_audit],
    }


class GeoChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    theme_id: str = ""
    lang: str = "ja"


class GeoChatResponse(BaseModel):
    reply: str
    sources: list[str] = []
    # Phase1 Closeout: Intelligence AI画面を本格geo_intelligence_serviceパイプラインへ接続した
    # ことに伴う追加フィールド（すべてoptional、既存reply/sourcesの契約は変更しない）。
    sufficiency: str | None = None
    expert_ai_called: bool = False
    intelligence_need_created: bool = False
    intelligence_need_id: str | None = None


# 実DB/実Azure OpenAIでのE2E検証で、sufficient→Expert AI呼び出しを含む実処理の所要時間に
# 81秒〜150秒超まで変動があることを確認したため、150秒でも不足するケースがあった。
# 余裕を持たせて280秒とする。
_GEO_CHAT_TIMEOUT_SECONDS = 280
_geo_chat_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="geo_chat_bridge")


def _geo_chat_theme_context(theme: dict | None) -> tuple[str, list]:
    """テーマ起点のContext構築（既存MVPロジックをそのまま再利用、削除しない）。
    戻り値: (Theme Contextの文字列ブロック, 参照したverified event一覧)。"""
    events_params = {"select": "*", "verified": "eq.true", "order": "created_at.desc", "limit": "8"}
    if theme:
        events_params["theme_id"] = f"eq.{theme['theme_id']}"
    events = _geo_client.select("geo_events", events_params)

    lines = []
    if theme:
        lines.append(f"監視テーマ: {theme['name_ja']}")

    if events:
        event_ids = [e["event_id"] for e in events]
        evidence = _geo_client.select("geo_evidence", {
            "select": "*", "event_id": f"in.({','.join(event_ids)})", "limit": "20",
        })
        evidence_by_event = {}
        for ev in evidence:
            evidence_by_event.setdefault(ev["event_id"], []).append(ev)
        lines.append("直近の検証済みEvent:")
        for e in events:
            quotes = [ev.get("quote") for ev in evidence_by_event.get(e["event_id"], []) if ev.get("quote")]
            line = f"- {e.get('actor') or '?'} が {e.get('action') or e.get('event_type') or '?'}"
            if e.get("start_at"):
                line += f"（{e['start_at']}）"
            if quotes:
                line += f" 根拠引用: 「{quotes[0]}」"
            lines.append(line)

    if theme:
        mil01 = _geo_client.select("geo_indicator_definitions", {
            "select": "indicator_id", "theme_id": f"eq.{theme['theme_id']}",
            "indicator_code": "eq.MIL-01", "limit": "1",
        })
        if mil01:
            obs = _geo_client.select("geo_observations", {
                "select": "*", "indicator_id": f"eq.{mil01[0]['indicator_id']}",
                "order": "as_of.desc", "limit": "1",
            })
            if obs:
                o = obs[0]
                if o.get("insufficient_data"):
                    lines.append("MIL-01（PLA航空活動・中間線越境）: データ不足")
                else:
                    mock_note = "（モックデータ）" if o.get("is_mock") else ""
                    lines.append(
                        f"MIL-01（PLA航空活動・中間線越境）直近30日観測値: {o.get('raw_value')}{mock_note}"
                    )

    return "\n".join(lines), events


def _geo_chat_conversation_context(history: list) -> str:
    """直近の会話履歴を「Conversation Context」として整形する。会話継続の解決
    （指示語・省略された主語の補完等）にのみ使うことを明示し、Evidence/Referenceとして
    扱わないよう文中で明示的に指示する（過去のAI回答の自己引用を防ぐ）。"""
    lines = []
    for m in history[-10:]:
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and content:
            speaker = "ユーザー" if role == "user" else "AI"
            lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


# Phase1 Closeout: Intelligence AI画面（geo.intelligence-ai.tsx / geo.theme.$themeId.tsx）を、
# Retrieval/Sufficiency判定/Expert AI Escalation/Intelligence Needs自動登録を備えた本格
# geo_intelligence_service.process_requestパイプラインへ接続する（推奨A、既存/api/geo/chatの
# 契約を壊さず内部実装のみ差し替える）。geo_intelligence_api.pyのHTTPルート・verify_api_keyは
# 経由しない（同一プロセス内の直接呼び出し、api_server.py内の他ハンドラと同じ既存パターン）。
@geo_router.post("/chat", response_model=GeoChatResponse)
def geo_chat(req: GeoChatRequest):
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")

    theme = None
    if req.theme_id:
        themes = _geo_client.select("geo_themes", {"select": "*", "theme_id": f"eq.{req.theme_id}", "limit": "1"})
        theme = themes[0] if themes else None

    theme_context, events = _geo_chat_theme_context(theme)
    conversation_context = _geo_chat_conversation_context(req.history)

    context_blocks = []
    if theme_context:
        context_blocks.append(f"【Theme Context（直近の検証済みEvent・指標）】\n{theme_context}")
    if conversation_context:
        context_blocks.append(
            "【Conversation Context（会話継続の解決にのみ使用。Evidence／Referenceとして"
            "扱わないこと。過去のAI回答をEvidenceとして自己引用しないこと）】\n" + conversation_context
        )
    context = "\n\n".join(context_blocks) if context_blocks else None

    payload = {
        "requester_app": geo_knowledge_gap_service.CHAT_UI_REQUESTER_APP,
        "request_type": "user_question",
        "question": req.message,
        "themes": [theme["name_ja"]] if theme else [],
        "theme_id": theme["theme_id"] if theme else None,
        "context": context,
        "analysis_requested": True,
    }

    try:
        future = _geo_chat_executor.submit(
            geo_intelligence_service.process_request, _geo_client, payload,
            azure_client=_azure_client, model=_model)
        response = future.result(timeout=_GEO_CHAT_TIMEOUT_SECONDS)
    except geo_intelligence_service.GeoIntelligenceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FutureTimeoutError:
        raise HTTPException(status_code=504,
                             detail=f"{_GEO_CHAT_TIMEOUT_SECONDS}秒以内に処理が完了しませんでした")
    except Exception as e:  # noqa: BLE001 — geo_intelligence_service自体はFailure Isolation済みのため、
        # ここに到達するのは実行基盤側（executor等）の予期しない失敗のみ
        raise HTTPException(status_code=502,
                             detail=f"Intelligence Pipeline呼び出しに失敗しました: {type(e).__name__}: {e}")

    geo_audit.log_audit(
        _geo_client, actor="analyst", action="geo_chat",
        target_table="geo_themes", target_id=req.theme_id or None,
        detail={"message_preview": req.message[:200]},
    )

    references = response.get("references") or []
    # 実E2E検証で判明: 単純な[:5]切り詰めだと、internal_report/internal_expert_assessment
    # （Report Library統合の主目的）がreferences末尾にある場合に表示から漏れる。
    # internal種別を優先して並べ替えてから切り詰める。
    internal_refs = [r for r in references if r.get("internal_external") == "internal"]
    external_refs = [r for r in references if r.get("internal_external") != "internal"]
    sources = [label for label in (
        r.get("title") or r.get("reference_id") for r in internal_refs + external_refs
    ) if label][:8]
    if not sources:
        # Reference取得前（insufficient等）でも、既存動作との後方互換のためTheme Context由来の
        # Event主体だけは引き続きsourcesへ出す。
        sources = [s for s in ({e.get("actor") or e.get("event_type") or "" for e in events}) if s][:5]

    knowledge_gap = response.get("knowledge_gap")
    return {
        "reply": response.get("geo_assessment") or "",
        "sources": sources,
        "sufficiency": response.get("knowledge_sufficiency"),
        "expert_ai_called": bool(response.get("expert_ai_called")),
        "intelligence_need_created": bool(knowledge_gap and knowledge_gap.get("exists")),
        "intelligence_need_id": (knowledge_gap.get("gap_id") if knowledge_gap else None),
    }


# =====================================================================
# 地政学専門家AI本格版（7モード）
# geo_expert_engine.pyがSource Pack作成→構造化LLM呼び出し→Quality Guard→保存を担う。
# ここではリクエストの受け口とUI向け整形のみを行う。
# =====================================================================

class GeoExpertRequestBody(BaseModel):
    mode: str
    theme_id: str | None = None
    query_text: str
    draft_text: str | None = None
    profile: str | None = None  # v1.2: quick/standard/deep。未指定時は"standard"
    execution_mode: str | None = None  # v1.2: staged/monolithic。未指定時はGEO_EXPERT_EXECUTION_MODE環境変数


def _geo_expert_result_to_ui(result: dict) -> dict:
    request_row, response_row, evaluation_row = result["request"], result["response"], result["quality_evaluation"]
    execution_metadata = result.get("execution_metadata") or {}
    return {
        "requestId": request_row["request_id"],
        "responseId": response_row["response_id"],
        "mode": request_row["mode"],
        "themeId": request_row.get("theme_id"),
        "queryText": request_row["query_text"],
        "createdAt": request_row.get("created_at"),
        "response": response_row["response_json"],
        "promptVersion": response_row["prompt_version"],
        "knowledgeVersion": response_row["knowledge_version"],
        "model": response_row["model"],
        "qualityEvaluation": {
            "checks": evaluation_row["checks"],
            "passedCount": evaluation_row["passed_count"],
            "failedCount": evaluation_row["failed_count"],
            # v1.2(Session2): 修復ループの完了状況。geo_quality_evaluationsに対応カラムが無い
            # （DBマイグレーションなしの方針）ため、生成直後のレスポンスにのみ含まれる。
            # response_idで後から再取得した場合（evaluation_rowがDB行そのまま）はNoneになる。
            "completenessStatus": evaluation_row.get("completeness_status"),
            "initialFailedChecks": evaluation_row.get("initial_failed_checks"),
            "repairAttempted": evaluation_row.get("repair_attempted"),
            "remainingFailedChecks": evaluation_row.get("remaining_failed_checks"),
        },
        # v1.2 段階実行(Staged Execution): 実行方式・プロファイル・Stage別メトリクスは
        # geo_audit_logsへも記録されるが、生成直後のこのレスポンスにもそのまま含める
        "executionMetadata": {
            "executionMode": execution_metadata.get("execution_mode"),
            "profile": execution_metadata.get("profile"),
            "stage2Modules": (execution_metadata.get("executed") or {}).get("stage2_modules"),
        } if execution_metadata else None,
    }


@geo_router.post("/expert/requests")
def geo_expert_submit_request(req: GeoExpertRequestBody):
    if req.mode not in geo_expert_schema.MODES:
        raise HTTPException(status_code=400, detail=f"未対応のmodeです: {req.mode}")
    if not _azure_client:
        raise HTTPException(status_code=503, detail="Azure OpenAI / OpenAI のAPIキーが未設定です")
    try:
        execution_mode = geo_expert_engine.resolve_execution_mode(req.execution_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = geo_expert_engine.run_expert_request_with_mode(
            _geo_client, _azure_client, _model, req.mode, req.theme_id, req.query_text, req.draft_text,
            profile=req.profile or "standard", execution_mode=execution_mode,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"地政学専門家AIの処理に失敗しました: {type(e).__name__}: {e}")

    geo_audit.log_audit(
        _geo_client, actor="analyst", action="expert_request",
        target_table="geo_expert_requests", target_id=result["request"]["request_id"],
        detail={"mode": req.mode, "execution_mode": execution_mode, "profile": req.profile or "standard"},
    )
    return _geo_expert_result_to_ui(result)


@geo_router.get("/expert/requests")
def geo_expert_list_requests(theme_id: str = "", limit: int = 20):
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if theme_id:
        params["theme_id"] = f"eq.{theme_id}"
    requests_ = _geo_client.select("geo_expert_requests", params)
    return {"requests": [{
        "requestId": r["request_id"], "mode": r["mode"], "themeId": r.get("theme_id"),
        "queryText": r["query_text"], "createdAt": r.get("created_at"),
    } for r in requests_]}


@geo_router.get("/expert/requests/{request_id}")
def geo_expert_get_request(request_id: str):
    requests_ = _geo_client.select("geo_expert_requests", {
        "select": "*", "request_id": f"eq.{request_id}", "limit": "1",
    })
    if not requests_:
        raise HTTPException(status_code=404, detail="依頼が見つかりません")
    responses = _geo_client.select("geo_expert_responses", {
        "select": "*", "request_id": f"eq.{request_id}", "order": "created_at.desc", "limit": "1",
    })
    if not responses:
        raise HTTPException(status_code=404, detail="レスポンスが見つかりません")
    evaluations = _geo_client.select("geo_quality_evaluations", {
        "select": "*", "response_id": f"eq.{responses[0]['response_id']}", "limit": "1",
    })
    if not evaluations:
        raise HTTPException(status_code=404, detail="Quality Guard評価が見つかりません")
    return _geo_expert_result_to_ui({
        "request": requests_[0], "response": responses[0], "quality_evaluation": evaluations[0],
    })


class GeoExpertFeedbackRequest(BaseModel):
    rating: str
    comment: str = ""


@geo_router.post("/expert/responses/{response_id}/feedback")
def geo_expert_submit_feedback(response_id: str, req: GeoExpertFeedbackRequest):
    _geo_client.insert("geo_expert_feedback", [{
        "response_id": response_id, "rating": req.rating, "comment": req.comment or None,
    }], prefer="return=minimal")
    geo_audit.log_audit(
        _geo_client, actor="analyst", action="expert_feedback",
        target_table="geo_expert_responses", target_id=response_id, detail={"rating": req.rating},
    )
    return {"ok": True}


app.include_router(geo_router)
app.include_router(geo_knowledge_api.geo_knowledge_router)
app.include_router(geo_expert_review_api.geo_expert_review_router)
app.include_router(geo_external_signal_api.geo_external_signal_router)
app.include_router(geo_external_event_api.geo_external_intelligence_router)
app.include_router(geo_intelligence_api.geo_intelligence_router)
app.include_router(geo_intelligence_gap_api.geo_intelligence_gap_router)
app.include_router(geo_report_api.geo_report_router)
app.include_router(geo_report_studio_api.geo_report_studio_router)
app.include_router(geo_report_design_api.geo_report_design_router)
app.include_router(geo_report_current_pipeline_api.geo_report_current_pipeline_router)
app.include_router(geo_report_prompt2_api.geo_report_prompt2_router)
app.include_router(geo_report_prompt_export_api.geo_report_prompt_export_router)
app.include_router(geo_report_report_edit_api.geo_report_report_edit_router)
app.include_router(geo_ios_analysis_session_api.geo_ios_analysis_session_router)
app.include_router(geo_ios_intake_api.geo_ios_intake_router)
app.include_router(geo_ios_review_api.geo_ios_review_router)
app.include_router(geo_expert_session_capture_api.geo_expert_session_capture_router)
app.include_router(geo_ios_approval_api.geo_ios_approval_router)
app.include_router(geo_ios_learning_api.geo_ios_learning_router)
app.include_router(geo_ios_learning_workbench_api.geo_ios_learning_workbench_router)
app.include_router(geo_ios_knowledge_publication_api.geo_ios_knowledge_publication_router)
app.include_router(geo_ios_identity_api.geo_ios_identity_router)
app.include_router(geo_ios_workflow_board_api.geo_ios_workflow_board_router)
app.include_router(geo_ios_situation_presentation_api.geo_ios_situation_presentation_router)
app.include_router(geo_ios_situation_presentation_api.geo_situation_presentation_router)
app.include_router(geo_ios_consultation_api.geo_ios_consultation_router)
app.include_router(geo_ios_report_draft_api.geo_ios_report_draft_router)
app.include_router(geo_report_review_v2_api.geo_report_review_v2_router)
app.include_router(geo_ios_review_import_api.geo_ios_review_import_router)
app.include_router(geo_intelligence_ai_api.geo_intelligence_ai_router)
app.include_router(geo_theme_candidate_api.geo_theme_candidate_router)
app.include_router(geo_source_library_api.geo_source_library_router)
app.include_router(geo_source_library_api.geo_source_set_router)
app.include_router(geo_source_library_api.geo_source_research_router)
app.include_router(geo_monitoring_theme_api.geo_theme_explorer_router)
app.include_router(geo_monitoring_theme_api.geo_monitoring_theme_router)
app.include_router(geo_theme_discovery_live_api.geo_theme_discovery_live_router)
app.include_router(geo_operations_api.geo_operations_router)
app.include_router(geo_scheduler_api.geo_scheduler_router)
app.include_router(geo_daily_brief_api.geo_daily_brief_router)
app.include_router(geo_external_network_api.geo_external_network_router)
app.include_router(geo_intelligence_ai_preset_api.geo_intelligence_ai_preset_router)
app.include_router(geo_intelligence_ai_preset_api.geo_intelligence_ai_conversation_router)

# 画面（静的ビルド）の配信。必ず全ルーターの登録の後に呼ぶ（全パス受けのため）。配信元（_shell.html）が
# 無ければ何もしない＝API専用として動く（ローカル開発は従来どおり。Azureでは同じサイトで画面も配信する）。
import geo_frontend_static  # noqa: E402
geo_frontend_static.mount_frontend(app)


if __name__ == "__main__":
    import uvicorn
    # ローカル開発では127.0.0.1のみ、コンテナ内ではAPI_HOST=0.0.0.0を環境変数で指定する
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
