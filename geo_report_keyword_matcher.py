"""Report Studio: Narrative keyword matcher v2（2026-10-04、Preprocess第2段階）。

従来の`match_master`は「小文字化したテキストへの単純な部分一致」だったため、`ICE`が`office`/`justice`/
`police`に、`govern`が`Governor`に、`rent`が`parent`に当たっていた（US S1の`ice`ヒット6,051件のうち
アクロニムは142件）。本モジュールは、キーワードの種類ごとに別のmatcherを提供する。

  latin_token    : Latin文字の単独語。トークン境界で一致（`ICE`は`office`に当たらない）。語幹展開はしない
                   （`govern`は`Governor`/`governance`/`governing`に当たらない）。
  latin_phrase   : Latin文字の複数語フレーズ。大文字小文字・連続空白・Unicode空白・句読点・ハイフンの違いを
                   吸収した上で、語順を変えずに一致（`cost of living` = `Cost-of-Living`）。
  latin_acronym  : 全て大文字の2〜3文字の単独語（`ICE`/`AI`/`US`/`GOP`）。大文字小文字を区別して一致
                   （普通名詞の`ice`/`us`を拾わない）。小文字表記も拾いたい場合はmasterへ`gop`を別語として明示する。
  latin_prefix   : 末尾に`*`を明示したLatin語（`rent*`）。語頭だけ境界を要求し、`rents`/`renter`に当たるが
                   `parent`/`current`には当たらない。語形の許可は常にmaster側の明示指定で行う。
  cjk_phrase     : 中国語・日本語・韓国語を含むキーワード。Latin用の語境界は使わず、NFKC正規化した上で
                   空白・記号を除いた文字列の部分一致（CJKは語の区切りが無いため）。

ハッシュタグ・@ハンドルのCamelCaseは語に分割する（`#StopTrumpCorruption` -> `Stop Trump Corruption`、
`@KatieHobbs` -> `Katie Hobbs`）。語幹展開ではなく表記の分割で、全て小文字の`#katiehobbs`は分割できない。
LatinとCJKの境目（`AI規制`）は語境界として扱う。Unicode正規化(NFKC)は照合用の表面にだけ使い、
原文はそのまま保持する（監査列はmasterの原表記`surface`で返す）。

spanは「照合用に正規化した表面」上の[start, end)。include/contextの自己発火判定（同一surfaceの部分一致で
includeとrequiredを両方満たさない）に使う。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

MATCHER_VERSION = "keyword_matcher_v2"

_CJK_CHARS = "぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ가-힯"
_CJK_RE = re.compile(f"[{_CJK_CHARS}]")
_LATIN_CJK_EDGE_RE = re.compile(
    f"(?<=[0-9A-Za-z])(?=[{_CJK_CHARS}])|(?<=[{_CJK_CHARS}])(?=[0-9A-Za-z])")
_APOSTROPHE_RE = re.compile("[’‘`´ʼ]")
_POSSESSIVE_RE = re.compile(r"'s(?![A-Za-z0-9])", re.IGNORECASE)
_TAG_RE = re.compile(r"[#@][A-Za-z0-9_]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM_RE = re.compile(r"[\W_]+")
_ACRONYM_RE = re.compile(r"^[A-Z]{2,3}$")

KIND_TOKEN = "latin_token"
KIND_PHRASE = "latin_phrase"
KIND_ACRONYM = "latin_acronym"
KIND_PREFIX = "latin_prefix"
KIND_CJK = "cjk_phrase"


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _latin_surface(text: str) -> str:
    """Latin照合用の表面（大文字小文字は保持）。単語をスペース1個で区切り、前後にもスペースを付ける。"""
    s = unicodedata.normalize("NFKC", text or "")
    s = _TAG_RE.sub(lambda m: _CAMEL_BOUNDARY_RE.sub(" ", m.group(0)), s)
    s = _APOSTROPHE_RE.sub("'", s)
    s = _POSSESSIVE_RE.sub("", s)
    s = s.replace("'", "")
    s = _LATIN_CJK_EDGE_RE.sub(" ", s)
    s = _NON_ALNUM_RE.sub(" ", s)
    return " " + " ".join(s.split()) + " "


def _cjk_surface(text: str) -> str:
    """CJK照合用の表面: NFKC・小文字化した上で空白と記号を全て除く。"""
    s = unicodedata.normalize("NFKC", text or "").lower()
    return _NON_ALNUM_RE.sub("", s)


@dataclass(frozen=True)
class CompiledTerm:
    surface: str        # masterの原表記（監査列に出す）
    kind: str
    needle: str         # 照合用に正規化した文字列（Latinは前後スペース付き。prefixは先頭スペースのみ）
    case_sensitive: bool


@lru_cache(maxsize=65536)
def compile_term(surface: str) -> Optional[CompiledTerm]:
    original = (surface or "").strip()
    if not original:
        return None
    if has_cjk(original):
        needle = _cjk_surface(original)
        return CompiledTerm(original, KIND_CJK, needle, False) if needle else None
    prefix = original.endswith("*")
    body = original[:-1] if prefix else original
    padded = _latin_surface(body)
    if padded.strip() == "":
        return None
    if prefix:
        return CompiledTerm(original, KIND_PREFIX, padded[:-1].lower(), False)   # 末尾の境界を外す
    words = padded.split()
    if len(words) == 1 and _ACRONYM_RE.match(words[0]) and body.strip().isupper():
        return CompiledTerm(original, KIND_ACRONYM, padded, True)
    if len(words) == 1:
        return CompiledTerm(original, KIND_TOKEN, padded.lower(), False)
    return CompiledTerm(original, KIND_PHRASE, padded.lower(), False)


def term_kind(surface: str) -> str:
    compiled = compile_term(surface)
    return compiled.kind if compiled else ""


class PreparedText:
    """1レコード分のテキストを照合用に一度だけ正規化したもの。"""
    __slots__ = ("cs", "ci", "cjk")

    def __init__(self, text: str):
        self.cs = _latin_surface(text)
        lowered = self.cs.lower()
        self.ci = lowered if len(lowered) == len(self.cs) else self.cs
        self.cjk = _cjk_surface(text)


def prepare_text(text: str) -> PreparedText:
    return PreparedText(text)


def find_spans(term: CompiledTerm, prepared: PreparedText) -> list:
    """termがprepared内で一致する全ての位置[(start, end), ...]。一致しなければ空リスト。"""
    if term.kind == KIND_CJK:
        hay, needle, lead, trail = prepared.cjk, term.needle, 0, 0
    else:
        hay = prepared.cs if term.case_sensitive else prepared.ci
        needle = term.needle
        lead, trail = 1, (0 if term.kind == KIND_PREFIX else 1)
    if not needle or needle not in hay:
        return []
    spans = []
    pos = hay.find(needle)
    while pos != -1:
        spans.append((pos + lead, pos + len(needle) - trail))
        pos = hay.find(needle, pos + 1)
    return spans


def term_spans(surface: str, prepared: PreparedText) -> list:
    compiled = compile_term(surface)
    return find_spans(compiled, prepared) if compiled else []


def covers(context_span: tuple, include_span: tuple) -> bool:
    """contextのspanがincludeのspanを完全に含む（または一致する）か。"""
    return context_span[0] <= include_span[0] and include_span[1] <= context_span[1]


def has_independent_pair(include_spans: list, context_spans: list, include_kind: str, context_kind: str) -> bool:
    """includeとcontextの間に「contextがincludeを丸ごと覆っていない」組が1つでもあるか。
    同一surfaceの部分一致（includeがcontextの一部、または同一）だけで両方を満たすのを防ぐ。
    contextがincludeより短くincludeの内側にある（例: include 'support Katie Hobbs' / context 'Katie Hobbs'）
    のは、includeフレーズが文脈を含むだけなので許可する。LatinとCJKは座標系が違うため常に独立とみなす。"""
    if (include_kind == KIND_CJK) != (context_kind == KIND_CJK):
        return bool(include_spans) and bool(context_spans)
    return any(not covers(c, i) for i in include_spans for c in context_spans)
