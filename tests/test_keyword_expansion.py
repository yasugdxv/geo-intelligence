"""クロスリンガル検索（geo_keyword_expansion、2026-10-15追加）のテスト。

- 日本語名・別名から英語名へ展開できる（完全一致を優先、展開語は最大3つ）
- 既定はOFF（環境変数 GEO_CROSS_LINGUAL_KEYWORDS=1 のときだけ有効）
- _relevant_rows_by_keywords: expansionsを渡さなければ従来どおり、渡せば英語文書が日本語キーワードの一致として数えられる
- 英語展開語にも単語境界チェックがかかる（"Trumpet"は"Trump"に一致しない）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_expert_engine as eng  # noqa: E402
import geo_keyword_expansion as kx  # noqa: E402


class _Client:
    """geo_entities / geo_entity_aliases / geo_documents だけを持つ偽クライアント（ilikeを簡易再現）。"""

    def __init__(self, entities, aliases, documents):
        self.t = {"geo_entities": entities, "geo_entity_aliases": aliases, "geo_documents": documents}

    def select(self, table, params):
        rows = list(self.t.get(table, []))
        for col, cond in params.items():
            if isinstance(cond, str) and cond.startswith("ilike.*"):
                needle = cond[len("ilike.*"):-1].lower()
                rows = [r for r in rows if needle in str(r.get(col) or "").lower()]
        off = int(params.get("offset", 0))
        return rows[off: off + int(params.get("limit", 1000))]


ENTITIES = [
    {"entity_id": "e1", "canonical_name": "Donald Trump", "name_en": "Donald Trump", "name_ja": "ドナルド・トランプ"},
    {"entity_id": "e2", "canonical_name": "Strait of Hormuz", "name_en": "Strait of Hormuz", "name_ja": "ホルムズ海峡"},
    {"entity_id": "e3", "canonical_name": "Takaichi Sanae (高市早苗)", "name_en": "Sanae Takaichi", "name_ja": "高市早苗"},
]
ALIASES = [
    {"entity_id": "e1", "alias_text": "トランプ"},
    {"entity_id": "e2", "alias_text": "ホルムズ"},
]


def _fresh(client):
    kx._CACHE.clear()
    return client


def test_expand_exact_alias_and_containment():
    c = _fresh(_Client(ENTITIES, ALIASES, []))
    out = kx.expand_keywords(c, ["トランプ", "ホルムズ海峡", "高市早苗", "MoU", "存在しない語"])
    assert out["トランプ"] == ["Donald Trump"]
    assert out["ホルムズ海峡"] == ["Strait of Hormuz"]
    assert out["高市早苗"] == ["Sanae Takaichi"]          # 日本語を含むcanonical_nameは使わない
    assert "MoU" not in out and "存在しない語" not in out   # 英語は展開しない／該当なしは含めない


def test_expand_containment_for_compound_keyword():
    c = _fresh(_Client(ENTITIES, ALIASES, []))
    assert kx.expand_keywords(c, ["ホルムズ海峡封鎖"]) == {"ホルムズ海峡封鎖": ["Strait of Hormuz"]}


def test_enabled_by_default_and_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("GEO_CROSS_LINGUAL_KEYWORDS", raising=False)
    assert kx.is_enabled() is True           # 2026-10-16 有効化（既定ON）
    for off in ("0", "false", "OFF", "no"):
        monkeypatch.setenv("GEO_CROSS_LINGUAL_KEYWORDS", off)
        assert kx.is_enabled() is False
    monkeypatch.setenv("GEO_CROSS_LINGUAL_KEYWORDS", "1")
    assert kx.is_enabled() is True


def test_expand_failure_falls_back_to_empty():
    class Boom:
        def select(self, *a, **k):
            raise RuntimeError("db down")
    kx._CACHE.clear()
    assert kx.expand_keywords(Boom(), ["トランプ"]) == {}


DOCS = [
    {"document_id": "d_en", "title": "Trump statement", "extracted_text": "Donald Trump met leaders.", "is_mock": False},
    {"document_id": "d_trumpet", "title": "Trumpet festival", "extracted_text": "A trumpet concert.", "is_mock": False},
    {"document_id": "d_ja", "title": "トランプ氏の発言", "extracted_text": "", "is_mock": False},
]


def test_retrieval_unchanged_without_expansions_and_finds_english_with_them():
    c = _Client(ENTITIES, ALIASES, DOCS)
    base = eng._relevant_rows_by_keywords(c, "geo_documents", "document_id", ["title", "extracted_text"], ["トランプ"], limit=10)
    assert [r["document_id"] for r in base] == ["d_ja"]
    exp = {"トランプ": ["Trump"]}
    got = eng._relevant_rows_by_keywords(c, "geo_documents", "document_id", ["title", "extracted_text"], ["トランプ"],
                                          limit=10, expansions=exp)
    assert sorted(r["document_id"] for r in got) == ["d_en", "d_ja"]     # Trumpet（単語境界違反）は入らない
    assert all(r["_strong_anchor_match"] for r in got)


def test_situation_heading_is_not_extracted_as_keyword():
    # 質問の定型見出し「Situation:」が固有語として検索に使われない（"Situation Report"系の大量誤採用を防ぐ）
    kws = eng._extract_query_keywords("Situation: Trump と Netanyahu の関係")
    assert "Situation" not in kws and "Trump" in kws
