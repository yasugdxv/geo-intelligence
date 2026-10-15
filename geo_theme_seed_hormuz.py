# -*- coding: utf-8 -*-
"""イラン/ホルムズ海峡テーマのseed投入（Phase 3-0 Pilot、一度限りの初期投入スクリプト、
再実行しても安全）。

config/geo_hormuz_theme_seed.yaml の内容を geo_themes へ投入する（Theme本体のみ。
Indicator MonitorはPilotの検証範囲外のため投入しない）。既存レコードがあれば追加しない
「select→なければinsert」の冪等パターン（geo_theme_seed.pyと同じ考え方）。

使い方: python geo_theme_seed_hormuz.py
"""
import sys
from pathlib import Path

import yaml

from geo_db_client import SupabaseClient
from config_utils import load_config

SEED_PATH = Path(__file__).parent / "config" / "geo_hormuz_theme_seed.yaml"


def _load_seed() -> dict:
    with open(SEED_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def seed_hormuz_theme(client) -> dict:
    """Theme本体のみ投入する。戻り値: {"theme": dict, "theme_created": bool}"""
    seed = _load_seed()
    theme_seed = seed["theme"]

    existing_themes = client.select("geo_themes", {"select": "*", "code": f"eq.{theme_seed['code']}"})
    if existing_themes:
        return {"theme": existing_themes[0], "theme_created": False}

    inserted = client.insert("geo_themes", [{
        "code": theme_seed["code"],
        "name_ja": theme_seed["name_ja"],
        "name_en": theme_seed.get("name_en"),
        "description": theme_seed.get("description"),
    }])
    return {"theme": inserted[0], "theme_created": True}


def main():
    config = load_config()
    client = SupabaseClient(config)
    result = seed_hormuz_theme(client)
    print(f"theme_created={result['theme_created']} theme_id={result['theme']['theme_id']}")


if __name__ == "__main__":
    sys.exit(main())
