# -*- coding: utf-8 -*-
"""日本語キーワード → 英語名の展開（クロスリンガル検索）。

Expert AIの文書検索は、質問文から取ったキーワードをILIKEで探す。質問が日本語（「トランプ」「ホルムズ海峡」）で
公的資料が英語（Trump / Strait of Hormuz）だと一致せず、0件になる。エンティティ表（geo_entities / geo_entity_aliases）
の日本語名・別名から英語名を引き、「同じ意味の別表記」として検索に足す。

- 展開した英語名は、元の日本語キーワードの別名として数える（一致の採用ルールは既存のまま。新しい緩い入口を作らない）。
- 既定はON（2026-10-16、ユーザー指示で有効化）。環境変数 GEO_CROSS_LINGUAL_KEYWORDS=0（false/off/no）でOFFに戻せる。
  OFFのときの挙動は従来と完全に同じ。ベースライン再現が必要な評価では明示的にOFFにすること。
- 1キーワードあたりの展開語は最大2つ（DB問い合わせの増加を抑える）。
"""
import os
import re
import time

MAX_TERMS_PER_KEYWORD = 2
MIN_ALIAS_LEN_FOR_CONTAINMENT = 3     # 「ホルムズ海峡」が別名「ホルムズ」を含む、のような部分一致に使う最短の長さ
_CACHE_TTL_SECONDS = 300
_CACHE: dict = {}
_HAS_JA = re.compile(r"[ぁ-ヶ一-龥]")


def is_enabled() -> bool:
    return os.environ.get("GEO_CROSS_LINGUAL_KEYWORDS", "1").strip().lower() not in ("0", "false", "off", "no")


def _fetch_all(client, table: str, columns: str, page: int = 1000) -> list:
    rows, offset = [], 0
    while True:
        got = client.select(table, {"select": columns, "limit": str(page), "offset": str(offset)})
        rows += got
        if len(got) < page:
            break
        offset += page
    return rows


def _load_index(client) -> list:
    """[(日本語表記, [英語名...]), ...] を返す。同じ接続先では一定時間キャッシュする。"""
    key = id(client)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    entities = _fetch_all(client, "geo_entities", "entity_id,canonical_name,name_en,name_ja")
    aliases = _fetch_all(client, "geo_entity_aliases", "entity_id,alias_text")
    ja_by_entity, en_by_entity = {}, {}
    for e in entities:
        en = [x for x in (e.get("name_en"), e.get("canonical_name")) if x and not _HAS_JA.search(x)]
        en_by_entity[e["entity_id"]] = list(dict.fromkeys(en))
        if e.get("name_ja"):
            ja_by_entity.setdefault(e["entity_id"], []).append(e["name_ja"])
    for a in aliases:
        text = a.get("alias_text") or ""
        if _HAS_JA.search(text):
            ja_by_entity.setdefault(a["entity_id"], []).append(text)
        elif a["entity_id"] in en_by_entity and text not in en_by_entity[a["entity_id"]] and len(text) >= 3:
            en_by_entity[a["entity_id"]].append(text)      # 英語の別名（例: Xi → Xi Jinping の略称）も後ろに足す
    index = []
    for entity_id, ja_names in ja_by_entity.items():
        en = en_by_entity.get(entity_id) or []
        if en:
            for ja in set(ja_names):
                index.append((ja, en))
    _CACHE[key] = (time.time(), index)
    return index


def expand_keywords(client, keywords: list) -> dict:
    """{日本語キーワード: [英語の展開語, ...]} を返す。展開できないキーワードは含めない。
    日本語を含まないキーワード（すでに英語）は展開しない。無効時・エラー時は空 dict（従来動作）。"""
    if not keywords:
        return {}
    try:
        index = _load_index(client)
    except Exception:  # noqa: BLE001 — 展開に失敗しても検索自体は従来どおり動かす
        return {}
    out = {}
    for kw in keywords:
        if not _HAS_JA.search(kw):
            continue
        terms = []
        for ja, en in index:
            hit = ja == kw or (len(ja) >= MIN_ALIAS_LEN_FOR_CONTAINMENT and ja in kw)
            if hit:
                for t in en:
                    if t not in terms:
                        terms.append(t)
        # 完全一致を優先したいので、完全一致が1つでもあれば部分一致由来は使わない
        exact = []
        for ja, en in index:
            if ja == kw:
                for t in en:
                    if t not in exact:
                        exact.append(t)
        chosen = exact or terms
        if chosen:
            out[kw] = chosen[:MAX_TERMS_PER_KEYWORD]
    return out
