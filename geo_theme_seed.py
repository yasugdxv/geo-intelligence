# -*- coding: utf-8 -*-
"""台湾有事テーマのseed投入（一度限りの初期投入スクリプト、再実行しても安全）。

config/geo_taiwan_theme_seed.yaml の内容を geo_themes / geo_indicator_definitions /
（MIL-01のみ）geo_indicator_rule_versions へ投入する。既存レコードがあれば追加しない
「select→なければinsert」の冪等パターン（seed_filter_keywords.pyと同じ考え方）。

使い方: sql/2026-08-02_geo_core_schema.sql を実行済みであることを確認した上で
    python geo_theme_seed.py

/api/geo/themes/seed/taiwan からも同じ seed_taiwan_theme() を呼び出す。
"""
import sys
from pathlib import Path

import yaml

from geo_db_client import SupabaseClient
from config_utils import load_config

SEED_PATH = Path(__file__).parent / "config" / "geo_taiwan_theme_seed.yaml"


def _load_seed() -> dict:
    with open(SEED_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def seed_taiwan_theme(client) -> dict:
    """seedを投入する。戻り値: {"theme": dict, "theme_created": bool,
    "indicators_created": int, "rule_version_created": bool}"""
    seed = _load_seed()
    theme_seed = seed["theme"]

    existing_themes = client.select("geo_themes", {"select": "*", "code": f"eq.{theme_seed['code']}"})
    if existing_themes:
        theme = existing_themes[0]
        theme_created = False
    else:
        inserted = client.insert("geo_themes", [{
            "code": theme_seed["code"],
            "name_ja": theme_seed["name_ja"],
            "name_en": theme_seed.get("name_en"),
            "description": theme_seed.get("description"),
        }])
        theme = inserted[0]
        theme_created = True

    theme_id = theme["theme_id"]

    existing_indicators = client.select("geo_indicator_definitions", {
        "select": "indicator_id,indicator_code,description", "theme_id": f"eq.{theme_id}",
    })
    existing_codes = {i["indicator_code"] for i in existing_indicators}

    indicators_created = 0
    descriptions_backfilled = 0
    existing_by_code = {i["indicator_code"]: i for i in existing_indicators}
    for idx, ind in enumerate(seed["indicators"]):
        if ind["code"] in existing_codes:
            # 既存行にdescriptionが無ければ後から追記する（description欄をyamlに
            # 追加した際の一度きりの後方互換バックフィル）
            existing_row = existing_by_code[ind["code"]]
            if not existing_row.get("description") and ind.get("description"):
                client.update(
                    "geo_indicator_definitions",
                    {"indicator_id": f"eq.{existing_row['indicator_id']}"},
                    {"description": ind["description"].strip()},
                )
                descriptions_backfilled += 1
            continue
        client.insert("geo_indicator_definitions", [{
            "theme_id": theme_id,
            "indicator_code": ind["code"],
            "name": ind["name"],
            "unit": ind.get("unit"),
            "description": (ind.get("description") or "").strip() or None,
            "mvp_enabled": ind.get("mvp_enabled", True),
            "display_order": idx + 1,
        }])
        indicators_created += 1

    rule_version_created = False
    mil01 = client.select("geo_indicator_definitions", {
        "select": "indicator_id", "theme_id": f"eq.{theme_id}", "indicator_code": "eq.MIL-01",
    })
    if mil01:
        indicator_id = mil01[0]["indicator_id"]
        existing_rules = client.select("geo_indicator_rule_versions", {
            "select": "rule_version_id", "indicator_id": f"eq.{indicator_id}",
        })
        if not existing_rules:
            client.insert("geo_indicator_rule_versions", [{
                "indicator_id": indicator_id,
                "version": 1,
                "rule_definition": {
                    "method": "raw_event_count_proxy",
                    "window_days": 30,
                    "description": "直近30日間のtheme配下geo_events件数を素朴に集計する暫定ルール（案・校正前）",
                },
                "is_draft": True,
            }])
            rule_version_created = True

    return {
        "theme": theme,
        "theme_created": theme_created,
        "indicators_created": indicators_created,
        "descriptions_backfilled": descriptions_backfilled,
        "rule_version_created": rule_version_created,
    }


def main():
    config = load_config()
    client = SupabaseClient(config)
    result = seed_taiwan_theme(client)
    print(f"descriptions_backfilled={result['descriptions_backfilled']}")
    print(
        f"theme_created={result['theme_created']} "
        f"indicators_created={result['indicators_created']} "
        f"rule_version_created={result['rule_version_created']}"
    )


if __name__ == "__main__":
    sys.exit(main())
