# -*- coding: utf-8 -*-
"""地政学インテリジェンス基盤: 公式ソースの定期クロール（Phase2 connector）。

指示書8.1章の縦切り方針に従い、実URLで到達確認済みの6ソース（台湾国防部・海巡署・
國家資通安全研究院・大陸委員会・総統府・外務省海外安全ホームページ）を対象とする。
TSMC Investor Relations（月次売上の公開HTML表を決定的にパース、SCM-01）も対応。

MKT-01（TAIEX・TWD）は文書クロール不要の「生データ直結」方式のため、DIRECT_METHODSで
別途扱う（台湾証券取引所OpenAPI・exchangerate-api.comの無料公開APIから直接数値取得）。

LOG-01（商業通航のAIS）・FIN-01（海上保険料率）は無料の公開データ源が無く、有料契約
または手動データ入力が必要なため、今回も計算ロジックは実装していない
（docs/assumptions.md参照）。

冪等性: geo_source_registryは(theme_id, url)で既存確認してから登録し、geo_documentsは
content_hashで重複を防ぐ（既存のgeo_documents_store.save_documentと同じ仕組み）。
Event抽出は新規文書の場合のみ実行する（同じ文書から重複してEventを作らない）。

使い方: sql/2026-08-02_geo_core_schema.sql・2026-08-03_geo_indicator_tagging.sql の
実行、python geo_theme_seed.py 実行後に
    python geo_official_crawler.py
"""
import sys

import geo_audit
import geo_documents_store as store
import geo_event_extractor as extractor
import geo_indicator_engine as engine
from ai_client import make_openai_client
from geo_db_client import SupabaseClient
from config_utils import load_config, make_proxies

THEME_CODE = "taiwan_strait"

# ソースURL・処理方式・対象指標のマッピング。geo_source_registryのsource_id・
# geo_indicator_definitionsのindicator_idは実行時にDBから解決する（ハードコードしない）。
SOURCES = [
    {
        "name": "台湾国防部 即時軍事動態",
        "url": "https://www.mnd.gov.tw/Publish.aspx?p=79096&title=%E5%9C%8B%E9%98%B2%E6%B6%88%E6%81%AF&SelectStyle=%E5%8D%B3%E6%99%82%E8%BB%8D%E4%BA%8B%E5%8B%95%E6%85%8B",
        "source_type": "official_site", "source_tier": "一次",
        "jobs": [
            {"method": "event_count_window", "indicator_code": "MIL-01"},
            {"method": "event_count_window", "indicator_code": "MIL-02"},
        ],
    },
    {
        "name": "台湾海巡署 海巡新聞",
        "url": "https://www.cga.gov.tw/GipOpen/wSite/lp?ctNode=650&mp=999",
        "source_type": "official_site", "source_tier": "一次",
        "jobs": [{"method": "event_count_window", "indicator_code": "MAR-01"}],
    },
    {
        "name": "國家資通安全研究院 資安週報",
        "url": "https://www.nics.nat.gov.tw/core_business/information_security_information_sharing/cybersecuritynews/",
        "source_type": "official_site", "source_tier": "一次",
        "jobs": [{"method": "event_count_window", "indicator_code": "CYB-01"}],
    },
    {
        "name": "台湾大陸委員会 最新消息",
        "url": "https://www.mac.gov.tw/News.aspx?n=05B73310C5C3A632&sms=1A40B00E4C745211",
        "source_type": "official_site", "source_tier": "一次",
        "jobs": [
            {"method": "event_count_window", "indicator_code": "ECO-01"},
            {"method": "qualitative_llm_state", "indicator_code": "POL-01", "states": [
                {"label": "融和的", "value": 0, "description": "対話・現状維持を強調する表現が中心"},
                {"label": "中立", "value": 1, "description": "従来方針の反復にとどまり、変化が明確でない"},
                {"label": "強硬", "value": 2, "description": "統一圧力・威嚇・非難を強める表現が中心"},
            ]},
        ],
    },
    {
        "name": "台湾総統府 總統府新聞",
        "url": "https://www.president.gov.tw/Page/35",
        "source_type": "official_site", "source_tier": "一次",
        "jobs": [{"method": "qualitative_llm_state", "indicator_code": "DIP-02", "states": [
            {"label": "正常", "value": 0, "description": "通常の対話・交流に関する言及が中心"},
            {"label": "縮小", "value": 1, "description": "対話停滞・自制の呼びかけ等、関係悪化を示唆する言及がある"},
            {"label": "停止", "value": 2, "description": "対話チャンネルの停止・断絶が明示されている"},
        ]}],
    },
    {
        "name": "外務省海外安全ホームページ 危険情報(台湾)",
        "url": "https://www.anzen.mofa.go.jp/info/pchazardspecificinfo_2026T030.html",
        "source_type": "official_site", "source_tier": "一次",
        "jobs": [{"method": "regex_level_from_document", "indicator_code": "JPN-01"}],
    },
    {
        "name": "TSMC Investor Relations 月次売上",
        "url": "https://investor.tsmc.com/english/monthly-revenue/2026",
        "source_type": "official_site", "source_tier": "一次",
        "jobs": [{"method": "financial_disclosure_parse", "indicator_code": "SCM-01"}],
    },
]

# MKT-01のみ、geo_source_registry/geo_documentsを経由しない「生データ直結」方式
# （台湾証券取引所OpenAPI・exchangerate-api.comの無料公開APIから直接数値を取得する）。
DIRECT_METHODS = [
    {"method": "market_data_api", "indicator_code": "MKT-01"},
]


def ensure_source(client, theme_id: str, source: dict) -> dict:
    existing = client.select("geo_source_registry", {
        "select": "*", "theme_id": f"eq.{theme_id}", "url": f"eq.{source['url']}", "limit": "1",
    })
    if existing:
        return existing[0]
    inserted = client.insert("geo_source_registry", [{
        "theme_id": theme_id, "name": source["name"], "url": source["url"],
        "source_tier": source.get("source_tier"), "source_type": source.get("source_type"),
    }])
    return inserted[0]


def crawl_source(client, azure_client, model: str, theme_id: str, theme_code: str,
                  indicator_choices: list[dict], indicator_by_code: dict, source: dict,
                  proxies: dict, verify: bool) -> list[dict]:
    """1ソースを取得・保存し、対象指標のObservationを計算する。結果サマリーのlistを返す"""
    results = []
    source_row = ensure_source(client, theme_id, source)
    source_id = source_row["source_id"]

    fetch_result = store.fetch_and_save_document(
        client, theme_id=theme_id, theme_code=theme_code, source_id=source_id,
        url=source["url"], proxies=proxies, verify=verify,
    )
    if "error" in fetch_result:
        geo_audit.log_audit(
            client, actor="system", action="official_crawl_failed",
            target_table="geo_source_registry", target_id=source_id,
            detail={"url": source["url"], "error": fetch_result["error"]},
        )
        return [{"source": source["name"], "ok": False, "error": fetch_result["error"]}]

    document = fetch_result["document"]
    is_new = fetch_result["is_new"]

    needs_extraction = is_new and any(j["method"] == "event_count_window" for j in source.get("jobs", []))
    if needs_extraction:
        if not azure_client:
            results.append({"source": source["name"], "ok": False, "error": "Azure OpenAI未設定のためEvent抽出をスキップ"})
        else:
            try:
                extracted = extractor.extract_events(
                    azure_client, model, document.get("extracted_text") or "", theme_code, indicator_choices,
                )
            except Exception as e:
                # 1ソースのLLM呼び出し失敗（JSON整形崩れ等）で他ソースのクロールまで
                # 止めないよう、ここで吸収してエラーとして記録する
                extracted = []
                results.append({"source": source["name"], "ok": False,
                                 "error": f"Event抽出に失敗: {type(e).__name__}: {e}"})
            for ev in extracted:
                event_row = client.insert("geo_events", [{
                    "theme_id": theme_id, "actor": ev.get("actor"), "action": ev.get("action"),
                    "target": ev.get("target"), "location": ev.get("location"),
                    "start_at": ev.get("start_at"), "end_at": ev.get("end_at"),
                    "quantities": ev.get("quantities"), "event_type": ev.get("event_type"),
                    "indicator_code": ev.get("indicator_code"), "confidence": ev.get("confidence"),
                    "unknown_fields": ev.get("unknown_fields"),
                }])[0]
                client.insert("geo_evidence", [{
                    "event_id": event_row["event_id"], "document_id": document["document_id"],
                    "claim_text": ev.get("claim_text") or "", "quote": ev.get("quote"),
                    "primary_flag": True, "confidence": ev.get("confidence"),
                }], prefer="return=minimal")

    for job in source.get("jobs", []):
        indicator = indicator_by_code.get(job["indicator_code"])
        if not indicator:
            results.append({"source": source["name"], "indicator": job["indicator_code"],
                             "ok": False, "error": "指標が未登録"})
            continue
        extra_definition = {"window_days": 30} if job["method"] == "event_count_window" else None
        rule = engine.ensure_rule_version(client, indicator["indicator_id"], job["method"], extra_definition)

        try:
            if job["method"] == "event_count_window":
                observation = engine.compute_event_count_observation(
                    client, indicator["indicator_id"], job["indicator_code"], rule["rule_version_id"], theme_id,
                    window_days=(rule.get("rule_definition") or {}).get("window_days", 30),
                )
            elif job["method"] == "qualitative_llm_state":
                if not azure_client:
                    results.append({"source": source["name"], "indicator": job["indicator_code"],
                                     "ok": False, "error": "Azure OpenAI未設定のため定性分類をスキップ"})
                    continue
                observation = engine.compute_qualitative_observation(
                    client, azure_client, model, indicator["indicator_id"], rule["rule_version_id"],
                    theme_id, source_id, job["states"],
                )
            elif job["method"] == "regex_level_from_document":
                observation = engine.compute_regex_level_observation(
                    client, indicator["indicator_id"], rule["rule_version_id"], theme_id, source_id,
                )
            elif job["method"] == "financial_disclosure_parse":
                observation = engine.compute_financial_disclosure_observation(
                    client, indicator["indicator_id"], rule["rule_version_id"], proxies, verify, url=source["url"],
                )
            else:
                continue
        except Exception as e:
            # 1指標の計算失敗（LLM呼び出し失敗等）で他の指標・他ソースの処理まで
            # 止めないよう、ここで吸収してエラーとして記録する
            results.append({"source": source["name"], "indicator": job["indicator_code"],
                             "ok": False, "error": f"{type(e).__name__}: {e}"})
            continue

        inserted = client.insert("geo_observations", [observation])[0]
        results.append({
            "source": source["name"], "indicator": job["indicator_code"], "ok": True,
            "raw_value": inserted.get("raw_value"), "insufficient_data": inserted.get("insufficient_data"),
        })

    return results


def run_direct_methods(client, indicator_by_code: dict, proxies: dict, verify: bool) -> list[dict]:
    """geo_source_registry/geo_documentsを経由しない指標（DIRECT_METHODS）を計算する"""
    results = []
    for job in DIRECT_METHODS:
        indicator = indicator_by_code.get(job["indicator_code"])
        if not indicator:
            results.append({"source": "(direct)", "indicator": job["indicator_code"],
                             "ok": False, "error": "指標が未登録"})
            continue
        rule = engine.ensure_rule_version(client, indicator["indicator_id"], job["method"])
        try:
            if job["method"] == "market_data_api":
                observation = engine.compute_market_data_observation(
                    client, indicator["indicator_id"], rule["rule_version_id"], proxies, verify,
                )
            else:
                continue
        except Exception as e:
            results.append({"source": "(direct)", "indicator": job["indicator_code"],
                             "ok": False, "error": f"{type(e).__name__}: {e}"})
            continue

        inserted = client.insert("geo_observations", [observation])[0]
        results.append({
            "source": "(direct)", "indicator": job["indicator_code"], "ok": True,
            "raw_value": inserted.get("raw_value"), "insufficient_data": inserted.get("insufficient_data"),
        })
    return results


def run_theme_crawl(theme_code: str = THEME_CODE) -> list[dict]:
    config = load_config()
    client = SupabaseClient(config)
    proxies = make_proxies(config)
    verify = config.get("ssl", {}).get("verify", True)
    azure_client, model = make_openai_client(config)
    if not azure_client:
        print("⚠ Azure OpenAI / OpenAI のAPIキーが未設定です。Event抽出・定性分類は行われません")

    themes = client.select("geo_themes", {"select": "*", "code": f"eq.{theme_code}", "limit": "1"})
    if not themes:
        print(f"テーマ({theme_code})が見つかりません。先に python geo_theme_seed.py を実行してください")
        return []
    theme = themes[0]
    theme_id = theme["theme_id"]

    indicators = client.select("geo_indicator_definitions", {"select": "*", "theme_id": f"eq.{theme_id}"})
    indicator_by_code = {i["indicator_code"]: i for i in indicators}
    indicator_choices = [{"code": i["indicator_code"], "name": i["name"]} for i in indicators]

    all_results = []
    for source in SOURCES:
        source_results = crawl_source(
            client, azure_client, model, theme_id, theme_code, indicator_choices, indicator_by_code, source,
            proxies, verify,
        )
        all_results.extend(source_results)
        for r in source_results:
            label = r.get("indicator", "")
            if r["ok"]:
                print(f"[OK] {r['source']} {label}: raw_value={r.get('raw_value')} "
                      f"insufficient_data={r.get('insufficient_data')}")
            else:
                print(f"[NG] {r['source']} {label}: {r.get('error')}")

    direct_results = run_direct_methods(client, indicator_by_code, proxies, verify)
    all_results.extend(direct_results)
    for r in direct_results:
        if r["ok"]:
            print(f"[OK] {r['source']} {r.get('indicator', '')}: raw_value={r.get('raw_value')} "
                  f"insufficient_data={r.get('insufficient_data')}")
        else:
            print(f"[NG] {r['source']} {r.get('indicator', '')}: {r.get('error')}")

    geo_audit.log_audit(
        client, actor="system", action="official_crawl_run",
        detail={"sources_processed": len(SOURCES), "results": len(all_results)},
    )
    return all_results


if __name__ == "__main__":
    run_theme_crawl()
    sys.exit(0)
