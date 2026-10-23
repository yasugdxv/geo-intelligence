"""読み方（Reading）のタイトル短縮の共通関数。

従来は geo_ios_expert_ai_adapter と geo_ios_reading_exposure_composer に同じ `_short_title` が
別々にあり、「最初の読点（、）で切る」だけだったため、「現時点では」「〜への注目は」のような
文の途中の断片がタイトルになっていた（2026-10-21、デモ準備で発見）。

ここでは要約・言い換えはせず（LLM呼び出しを増やさない）、次の3点だけを賢くする:
1. 先頭の内部ラベル（「H1_instrumental_indirectに基づく読み筋：」等）と、「現時点では、」のような前置きを外す。
2. 読点で切るときは、助詞・接続（「は」「が」「より」「ものの」等）で終わる断片では切らない。
3. 適切な切れ目が無ければ、上限の長さで切って「…」を付ける（従来どおり）。
"""
import re

SHORT_TITLE_MAX_LEN = 45
_MIN_CLAUSE_LEN = 12
_MIN_FALLBACK_CLAUSE_LEN = 20          # 読点で切った断片が短すぎるときは、上限の長さで切る方が情報が多い

# 先頭の内部ラベル: 「H1」「H1_instrumental_indirect」「H2（H3_constraint_domestic_driver）」＋「に基づく読み筋：」等
_LEADING_LABEL = re.compile(
    r"^\s*H\d(?!\d)(?:_[A-Za-z][A-Za-z0-9_]*)?(?:\s*[（(][^）)]*[）)])?"
    r"(?:\s*(?:仮説|シナリオ))?(?:\s*に基づく読み筋)?\s*[：:]\s*")
# 先頭の前置き（本題ではない導入）: 「現時点では、」「現時点の公開情報では、」
_LEADING_INTRO = re.compile(r"^\s*現時点(?:の[^、。]{0,20})?では、\s*")
# 読点で切ったとき、文の途中に見える終わり方（助詞・接続・比較など）
_DANGLING_ENDINGS = ("は", "が", "を", "に", "で", "の", "と", "も", "へ", "や", "より", "ものの", "ため",
                     "ので", "ば", "つつ", "ながら", "では", "には", "とは", "として", "について", "による", "なく", "ず")


def _is_dangling(fragment: str) -> bool:
    return fragment.endswith(_DANGLING_ENDINGS)


def looks_broken(title: str) -> bool:
    """保存済みのタイトルが、作り直す対象か（内部ラベル付き・文の途中で終わる・短すぎる断片）。"""
    t = (title or "").strip()
    if not t:
        return True
    return bool(_LEADING_LABEL.match(t)) or _is_dangling(t.rstrip("…")) or len(t.rstrip("…")) < _MIN_CLAUSE_LEN


def short_title(description: str, max_len: int = SHORT_TITLE_MAX_LEN) -> str:
    if not description:
        return ""
    text = _LEADING_LABEL.sub("", description, count=1)
    text = _LEADING_INTRO.sub("", text, count=1).strip()
    if not text:
        return ""
    idx = text.find("。")
    if 0 < idx <= max_len:
        return text[:idx]
    if len(text) <= max_len:
        return text
    pos = -1
    last_clause = ""
    while True:                                   # 読点の位置を前から順に調べ、最初に条件を満たす切れ目を使う
        pos = text.find("、", pos + 1)
        if pos < 0 or pos > max_len:
            break
        fragment = text[:pos]
        if len(fragment) >= _MIN_CLAUSE_LEN:
            if not _is_dangling(fragment):
                return fragment
            last_clause = fragment                # 文の途中に見える切れ目は、最後の手段として覚えておく
    if len(last_clause) >= _MIN_FALLBACK_CLAUSE_LEN:
        return last_clause + "…"                  # 単語の途中ではなく、読点の位置で切って「…」を付ける
    return text[:max_len] + "…"
