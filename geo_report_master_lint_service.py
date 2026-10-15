"""Report Studio: Narrative Master の静的lint（2026-10-03追加、Preprocess潜在不具合監査 #5）。

`geo_report_current_pipeline_service.match_master`は、include/required/exclude を
すべて「小文字化したテキストへの単純な部分一致（単語境界なし・正規化なし）」で判定する。
そのため、Masterの書き方次第で「発火しないNarrative」「発火しすぎるNarrative」が
静かに生まれる（例: requiredがincludeの部分文字列だとrequiredが形骸化、
`US-China`だけを書くと`US China`にマッチしない、等）。

このモジュールはRun作成時（またはMaster保存時）に呼ぶ想定の、副作用の無い検査関数のみを
提供する。lint結果はwarning/infoであり、Runの実行自体は止めない（既存Runとの互換性維持）。
errorは「Masterの記述がマッチング上ほぼ確実に意図通り動かない」ものだけに限定する。

使い方:
    findings = lint_narrative_master(master_rows)
    summary = summarize_findings(findings)

データ駆動の検査（任意）:
    term_hit_counts: {signal: {term_lower: ヒット件数}} と signal_totals: {signal: 総件数}
    を渡すと、ヒット率が高すぎる（汎用語による過剰マッチの疑い）includeを報告する。
    language_distribution: {signal: {language名: 件数}} を渡すと、非ラテン文字言語が
    多いのにキーワードがラテン文字のみのSignalを報告する。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Optional

import geo_report_keyword_matcher as matcher

from geo_report_processing_service import clean_value, parse_bool, parse_date_safe, parse_int, split_phrases

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿豈-﫿ｦ-ﾟ]")
_LATIN_RE = re.compile(r"[A-Za-z]")
# 単語境界なしの部分一致で、別の語の一部に誤マッチしやすい短いラテン語の上限長
_BOUNDARY_RISK_MAX_LEN = 5
_SHORT_LATIN_MAX_LEN = 3
_VARIANT_DASH_RE = re.compile(r"[-‐-―−]")
_PUNCT_TRAILING_RE = re.compile(r"^[\W_]+|[\W_]+$")
# 汎用的すぎて単独でNarrativeを特徴づけない語（小さな既定リスト、Master側で上書きしない）
_GENERIC_WORDS = {
    "vote", "voting", "voter", "voters", "election", "elections", "support", "oppose", "poll", "polls",
    "news", "policy", "policies", "government", "president", "state", "states", "people", "party", "candidate",
    "campaign", "tax", "taxes", "trade", "tariff", "tariffs", "china", "chinese", "us", "usa", "america",
    "american", "ai", "technology", "summit", "economy", "economic", "market", "markets", "security", "war",
    # 2026-10-04: 単独ではNarrativeの意味を確定できない動詞・名詞（US masterのレビューで追加）
    "win", "wins", "lose", "loses", "back", "beat", "race", "run", "lead",
}
# 語形（inflection）とみなす接尾辞。語幹が一致して残りがこれなら「同じ語の別の形」と判断する。
_INFLECTION_SUFFIXES = {
    "s", "es", "d", "ed", "ing", "ings", "ion", "ions", "ive", "ives", "ic", "ical", "al", "ally", "ly", "ity",
    "ness", "ment", "ments", "er", "ers", "rs", "y", "ate", "ates", "ation", "ations", "ist", "ists", "ship",
    "hood", "ful", "less", "ish",
}
_VOCAB_MIN_COUNT = 50          # 語彙ベースの指摘を出す最小件数
_VOCAB_MAX_EXAMPLES = 4
_PREFIX_COLLISION_MIN_COUNT = 250   # `*`が語形以外の語（ハッシュタグ等の長い連結語を含む）に当たる件数がこれ以上

TERM_TYPE_BY_FIELD = {
    "include_keywords": "include", "required_context_keywords": "required_context",
    "exclude_keywords": "exclude", "public_voice_cues": "cue", "cluster_split_terms": "split_term",
}
RECOMMENDED_ACTION = {
    "short_keyword": "3文字以下のLatin語。大文字のアクロニムはcase-sensitive exact tokenで一致する（小文字表記は別語として明示）。普通名詞は句に広げる",
    "substring_collision": "matcher v2のexact-token照合で回避済み。`*`を付けて語頭一致にする場合は衝突語を再確認する",
    "inflection_not_covered": "語形をmasterに明示する（例: `term*`、または`term; terms; termed`のように列挙）。matcherは語幹展開しない",
    "prefix_collision": "`*`（語頭一致）が意図しない語にも当たる。列挙に切り替えるか語を長くする",
    "include_inside_required": "includeをrequired contextと別の語・句にする（同一surfaceでinclude+contextを満たさない）",
    "include_equals_required": "includeまたはrequiredのどちらかを別の語にする",
    "include_contains_exclude": "includeかexcludeを修正する（このincludeは永久に発火しない）",
    "include_inside_exclude": "excludeの範囲を狭めるか、includeを別表記にする",
    "generic_word": "単独語をやめ、意味を確定できる句または文脈条件に置き換える",
    "self_firing_names_only": "候補者名・州名・役職名だけで発火しないよう、意見・争点を表す語を含める",
    "punctuation_variant": "照合は句読点・ハイフンを吸収するが、引用符・連続記号などの表記を確認する",
    "alias_collision": "同じ語が複数Narrativeにある。意図した重複か確認し、priorityで決まる前提を理解する",
    "include_exclude_cross_conflict": "Narrative間でinclude/excludeが対立していないか確認する",
    "cues_not_phrases": "cueは本文に出る短いキーフレーズにする",
    "split_term_vs_phrase": "`A vs B`形式は本文に現れにくい。単語に分ける",
    "no_fallback_rule": "Signalごとにactiveなfallback行をちょうど1つ置く",
    "duplicate_narrative_id": "sub_narrative_idをMaster全体で一意にする",
    "no_include": "include_keywordsを設定する",
    "high_hit_rate": "汎用語による過剰マッチの疑い。句に置き換えるか文脈条件を足す",
    "multilingual_keywords_missing": "非ラテン文字言語のキーワードを追加する",
}
LINT_CSV_COLUMNS = ["signal_id", "narrative_id", "term", "term_type", "issue_type", "collision_example",
                    "severity", "recommended_action"]
# 「名前だけで発火する」判定に使う、役職・選挙の汎用語
_OFFICE_WORDS = {
    "governor", "gov", "senate", "senator", "sen", "election", "elections", "candidate", "mayor", "congress",
    "president", "state", "race", "campaign", "vote", "voting",
}
_HIGH_HIT_RATE = 0.05  # 1つのincludeが同一Signalの5%超にヒットしたら汎用語による過剰マッチを疑う
_HIGH_HIT_MIN_COUNT = 200


def _terms(row: dict, key: str) -> list:
    return split_phrases(row.get(key, ""))


def _signal(row: dict) -> str:
    """signal_source（Preprocessが読む列）が無い行は signal_id で代替する。"""
    return clean_value(row.get("signal_source", "") or row.get("signal_id", "")).upper()


def _has_cjk(term: str) -> bool:
    return bool(_CJK_RE.search(term))


def _is_latin_only(term: str) -> bool:
    return bool(_LATIN_RE.search(term)) and not _has_cjk(term)


def _name_tokens(row: dict) -> set:
    """theme_name/signal_name/required_context_keywordsから「候補者・州・役職名」相当の
    トークン集合を作る（self_firing判定用）。"""
    tokens: set = set()
    for source in (row.get("theme_name", ""), row.get("signal_name", ""), row.get("report_topic_label", "")):
        tokens.update(t for t in re.findall(r"[A-Za-z0-9]+", clean_value(source).lower()) if len(t) >= 2)
    for term in _terms(row, "required_context_keywords"):
        tokens.update(t for t in re.findall(r"[A-Za-z0-9]+", term.lower()) if len(t) >= 2)
    return tokens | _OFFICE_WORDS


def _finding(severity: str, code: str, row: dict, field: str, term: str, message: str,
             example: str = "") -> dict:
    return {
        "severity": severity, "code": code,
        "signal_source": _signal(row),
        "narrative_id": clean_value(row.get("sub_narrative_id", "")),
        "field": field, "term": term, "message": message,
        "term_type": TERM_TYPE_BY_FIELD.get(field, field), "collision_example": example,
        "recommended_action": RECOMMENDED_ACTION.get(code, ""),
    }


def _lint_row(row: dict) -> list:
    findings: list = []
    is_fallback = parse_bool(row.get("is_fallback"), False)
    includes = _terms(row, "include_keywords")
    required = _terms(row, "required_context_keywords")
    excludes = _terms(row, "exclude_keywords")
    cues = _terms(row, "public_voice_cues")
    split_terms = _terms(row, "cluster_split_terms")
    lower_inc = [t.lower() for t in includes]
    lower_req = [t.lower() for t in required]
    lower_exc = [t.lower() for t in excludes]

    if is_fallback:
        if includes:
            findings.append(_finding(
                SEVERITY_WARNING, "fallback_has_include", row, "include_keywords", "|".join(includes[:3]),
                "Fallback行のinclude_keywordsはmatch_masterで無視される（Fallbackは通常のマッチ対象外）。"))
    elif not includes:
        findings.append(_finding(
            SEVERITY_ERROR, "no_include", row, "include_keywords", "",
            "active/非FallbackなのにincludeがなくこのNarrativeにはマッチしない。"))

    # --- 短すぎる/単語境界リスク（Latin） ---
    for field, items in (("include_keywords", includes), ("required_context_keywords", required),
                          ("exclude_keywords", excludes)):
        for term in items:
            stripped = term.strip()
            if _has_cjk(stripped):
                if len(stripped) <= 1:
                    findings.append(_finding(SEVERITY_WARNING, "short_keyword", row, field, term,
                                              "CJK1文字のキーワードは過剰マッチしやすい。"))
                continue
            if len(stripped) <= _SHORT_LATIN_MAX_LEN:
                if matcher.term_kind(stripped) == matcher.KIND_ACRONYM:
                    findings.append(_finding(
                        SEVERITY_INFO, "short_keyword", row, field, term,
                        f"{len(stripped)}文字の大文字アクロニム。matcher v2はcase-sensitiveなexact tokenで照合する"
                        "（'office'等には当たらない。小文字表記は拾わない）。"))
                else:
                    findings.append(_finding(
                        SEVERITY_WARNING, "short_keyword", row, field, term,
                        f"{len(stripped)}文字の短いLatin語。exact tokenで照合されるが、短い語は意味が曖昧になりやすい"
                        "（普通名詞と同形になる等）。"))

    # --- include と required の包含関係 ---
    for inc, inc_l in zip(includes, lower_inc):
        for req, req_l in zip(required, lower_req):
            if inc_l == req_l:
                findings.append(_finding(
                    SEVERITY_WARNING, "include_equals_required", row, "include_keywords", inc,
                    f"includeと同一のrequired('{req}')があり、required gateが形骸化している。"))
            elif req_l in inc_l:
                # 候補者名をrequiredにし、includeが「support <候補者名>」の形になるMaster（US中間選挙等）では
                # 全行がこの形になる。無害な冗長性（required gateが追加の絞り込みにならないだけ）なので
                # warningではなくinfoにする。
                findings.append(_finding(
                    SEVERITY_INFO, "required_inside_include", row, "required_context_keywords", req,
                    f"required('{req}')がinclude('{inc}')の部分文字列。includeがヒットすれば必ず"
                    "requiredも満たされ、required gateは追加の絞り込みにならない（無害な冗長性）。"))
            elif inc_l in req_l:
                findings.append(_finding(
                    SEVERITY_WARNING, "include_inside_required", row, "include_keywords", inc,
                    f"include('{inc}')がrequired('{req}')の部分文字列。requiredを満たすだけで"
                    "includeも満たされ、include判定が冗長（requiredだけでNarrativeが発火する）。"))

    # --- include と exclude の衝突 ---
    for inc, inc_l in zip(includes, lower_inc):
        for exc, exc_l in zip(excludes, lower_exc):
            if exc_l in inc_l:
                findings.append(_finding(
                    SEVERITY_ERROR, "include_contains_exclude", row, "include_keywords", inc,
                    f"include('{inc}')がexclude('{exc}')を含む。このincludeがヒットすると必ず除外され、"
                    "このincludeは永久に発火しない。"))
            elif inc_l in exc_l:
                findings.append(_finding(
                    SEVERITY_WARNING, "include_inside_exclude", row, "include_keywords", inc,
                    f"include('{inc}')がexclude('{exc}')の部分文字列。excludeの文脈では常に除外される。"))
    for req, req_l in zip(required, lower_req):
        for exc, exc_l in zip(excludes, lower_exc):
            if exc_l in req_l or req_l in exc_l:
                findings.append(_finding(
                    SEVERITY_WARNING, "required_exclude_overlap", row, "required_context_keywords", req,
                    f"required('{req}')とexclude('{exc}')が包含関係にあり、互いを打ち消し得る。"))

    # --- 候補者/州/役職名だけで自己発火 ---
    if not is_fallback and includes:
        name_tokens = _name_tokens(row)
        name_only = [t for t in includes
                     if (tokens := [x for x in re.findall(r"[A-Za-z0-9]+", t.lower()) if len(x) >= 2])
                     and all(x in name_tokens for x in tokens)]
        if name_only and len(name_only) == len(includes):
            findings.append(_finding(
                SEVERITY_WARNING, "self_firing_names_only", row, "include_keywords", "|".join(name_only[:3]),
                "include_keywordsが全て候補者/州/役職名等のみで、名前が出るだけでこのNarrativeが発火する。"))
        elif name_only:
            findings.append(_finding(
                SEVERITY_INFO, "include_names_only_term", row, "include_keywords", "|".join(name_only[:3]),
                "名前・役職名だけのincludeが含まれる。requiredが同じ名前だと文脈ゲートにならない。"))

    # --- 汎用語 ---
    for inc in includes:
        t = inc.strip().lower()
        if t in _GENERIC_WORDS:
            findings.append(_finding(
                SEVERITY_WARNING, "generic_word", row, "include_keywords", inc,
                "汎用的な単独語のため、無関係な文脈にも広くマッチする。"))

    # --- 表記ゆれ（ハイフン/引用符/記号）: 正規化なしの部分一致のため別表記にマッチしない ---
    for field, items, lowers in (("include_keywords", includes, lower_inc),
                                  ("required_context_keywords", required, lower_req),
                                  ("exclude_keywords", excludes, lower_exc)):
        present = set(lowers)
        for term, term_l in zip(items, lowers):
            problems = []
            if _VARIANT_DASH_RE.search(term_l):
                variant = re.sub(r"\s+", " ", _VARIANT_DASH_RE.sub(" ", term_l)).strip()
                if variant not in present:
                    problems.append(f"ハイフン区切りのみ（'{variant}'のような空白区切り表記にはマッチしない）")
            if re.search(r"[‘’“”]", term):
                problems.append("全角/カーリー引用符を含む（直線引用符の本文にはマッチしない）")
            if re.search(r"\s{2,}", term):
                problems.append("連続空白を含む（本文はclean_valueで改行のみ空白化されるため一致しにくい）")
            if _PUNCT_TRAILING_RE.search(term.rstrip("*")) and not _has_cjk(term):
                problems.append("先頭/末尾に記号を含む")
            if problems:
                findings.append(_finding(SEVERITY_WARNING, "punctuation_variant", row, field, term,
                                          "; ".join(problems)))

    # --- cue / split_termsが「本文に現れる表現」ではない（説明文）---
    long_cues = [c for c in cues if len(c) > 60 or re.search(r"[。「」『』]", c)]
    if cues and len(long_cues) == len(cues):
        findings.append(_finding(
            SEVERITY_WARNING, "cues_not_phrases", row, "public_voice_cues", long_cues[0][:40],
            "public_voice_cuesが全て長文の説明文で、本文に部分一致する可能性が実質無い"
            "（cue加点が働かない）。短いキーフレーズにする。"))
    vs_terms = [t for t in split_terms if re.search(r"\bvs\.?\b", t.lower())]
    if vs_terms:
        findings.append(_finding(
            SEVERITY_WARNING, "split_term_vs_phrase", row, "cluster_split_terms", vs_terms[0],
            "cluster_split_termsに'A vs B'形式の語がある。本文に'A vs B'という表現が現れない限り"
            "一致せず、cluster_cueが常に汎用cue(general/question等)へ落ちる。"))

    # --- 設定値 ---
    max_records = parse_int(row.get("max_cluster_records", ""), 200)
    if max_records < 20:
        findings.append(_finding(
            SEVERITY_INFO, "max_cluster_records_below_floor", row, "max_cluster_records", str(max_records),
            "run_preprocessは max(max_cluster_records, 20) を使うため、20未満の指定は実質20になる。"))
    rep_count = parse_int(row.get("representative_count", ""), 8)
    if rep_count < 3:
        findings.append(_finding(
            SEVERITY_INFO, "representative_count_below_floor", row, "representative_count", str(rep_count),
            "run_preprocessは max(representative_count, 3) を使うため、3未満の指定は実質3になる。"))
    priority = clean_value(row.get("priority", "")).lower()
    if priority and priority not in ("high", "medium", "low"):
        findings.append(_finding(
            SEVERITY_WARNING, "invalid_priority", row, "priority", priority,
            "priorityはHigh/Medium/Low以外だとLow相当（重み10）として扱われる。"))
    if clean_value(row.get("trend_role", "")).lower() == "primary":
        start = clean_value(row.get("trend_start_date", ""))
        if not start or parse_date_safe(start) is None:
            findings.append(_finding(
                SEVERITY_WARNING, "primary_without_valid_start", row, "trend_start_date", start,
                "trend_role=primaryなのにtrend_start_dateが空/解釈不能。common_primary_graph_startに"
                "反映されない。"))
    return findings


def _lint_cross_rows(rows: list) -> list:
    """Signal内・Signal全体での行間チェック（alias collision / Fallback構成 / ID重複）。"""
    findings: list = []
    by_signal: dict = defaultdict(list)
    for row in rows:
        if parse_bool(row.get("active"), True):
            by_signal[_signal(row)].append(row)

    for signal, signal_rows in by_signal.items():
        fallbacks = [r for r in signal_rows if parse_bool(r.get("is_fallback"), False)]
        if not fallbacks:
            findings.append({
                "severity": SEVERITY_ERROR, "code": "no_fallback_rule", "signal_source": signal,
                "narrative_id": "", "field": "is_fallback", "term": "",
                "message": "このSignalにFallback行が無い。run_preprocessは先頭のNarrative行を暗黙のFallbackとして"
                           "使うため、未マッチ記録がそのNarrativeへ流れ込む。"})
        elif len(fallbacks) > 1:
            findings.append({
                "severity": SEVERITY_WARNING, "code": "multiple_fallback_rules", "signal_source": signal,
                "narrative_id": ",".join(clean_value(r.get("sub_narrative_id", "")) for r in fallbacks),
                "field": "is_fallback", "term": "",
                "message": "Fallback行が複数ある。build_master_rulesは最後の1行だけを採用し、他は無視される。"})

        ids = [clean_value(r.get("sub_narrative_id", "")) for r in signal_rows]
        for dup_id in sorted({i for i in ids if i and ids.count(i) > 1}):
            findings.append({
                "severity": SEVERITY_ERROR, "code": "duplicate_narrative_id", "signal_source": signal,
                "narrative_id": dup_id, "field": "sub_narrative_id", "term": dup_id,
                "message": "同一Signal内でsub_narrative_idが重複。cluster_idのbase_keyが衝突し、別Narrativeの"
                           "記録が同じClusterに混ざる。"})

        # alias collision: 同じincludeが複数のNarrativeにある / あるNarrativeのincludeが別のexcludeにある
        include_owner: dict = defaultdict(list)
        exclude_owner: dict = defaultdict(list)
        for row in signal_rows:
            if parse_bool(row.get("is_fallback"), False):
                continue
            nid = clean_value(row.get("sub_narrative_id", ""))
            for t in _terms(row, "include_keywords"):
                include_owner[t.lower()].append(nid)
            for t in _terms(row, "exclude_keywords"):
                exclude_owner[t.lower()].append(nid)
        for term_l, owners in sorted(include_owner.items()):
            if len(set(owners)) > 1:
                findings.append({
                    "severity": SEVERITY_INFO, "code": "alias_collision", "signal_source": signal,
                    "narrative_id": ",".join(sorted(set(owners))), "field": "include_keywords", "term": term_l,
                    "message": "同じincludeが複数Narrativeにある。priority重み+ヒット長+cueのスコア差で"
                               "どちらかへ決まり、同点なら先に定義された行が勝つ（順序依存）。"})
            for other in exclude_owner.get(term_l, []):
                findings.append({
                    "severity": SEVERITY_WARNING, "code": "include_exclude_cross_conflict",
                    "signal_source": signal, "narrative_id": ",".join(sorted(set(owners + [other]))),
                    "field": "include_keywords/exclude_keywords", "term": term_l,
                    "message": "あるNarrativeのincludeが別Narrativeのexcludeに同じ表記で存在する（意図確認）。"})
    return findings


def _lint_language_and_data(rows: list, language_distribution: Optional[dict],
                             term_hit_counts: Optional[dict], signal_totals: Optional[dict]) -> list:
    findings: list = []
    by_signal: dict = defaultdict(list)
    for row in rows:
        if parse_bool(row.get("active"), True) and not parse_bool(row.get("is_fallback"), False):
            by_signal[_signal(row)].append(row)

    for signal, signal_rows in by_signal.items():
        all_terms = [t for r in signal_rows for k in ("include_keywords", "required_context_keywords")
                     for t in _terms(r, k)]
        has_cjk = any(_has_cjk(t) for t in all_terms)
        has_latin = any(_is_latin_only(t) for t in all_terms)
        if has_cjk and has_latin:
            findings.append({
                "severity": SEVERITY_INFO, "code": "cjk_latin_mixed_matcher", "signal_source": signal,
                "narrative_id": "", "field": "include_keywords", "term": "",
                "message": "CJKとラテン文字のキーワードが混在。同じ部分一致matcherを使うが、CJKは語の区切りが無く"
                           "ラテン文字は短語で別語に誤マッチし得る（性質が違う）。"})
        if language_distribution and signal in language_distribution and not has_cjk:
            dist = language_distribution[signal]
            total = sum(dist.values()) or 1
            non_latin = sum(v for lang, v in dist.items()
                            if re.search(r"Chinese|Japanese|Korean|Hindi|Arabic|Russian|Ukrainian|Thai|Hebrew|Persian",
                                         lang or "", re.IGNORECASE))
            if non_latin / total >= 0.10:
                findings.append({
                    "severity": SEVERITY_WARNING, "code": "multilingual_keywords_missing",
                    "signal_source": signal, "narrative_id": "", "field": "include_keywords", "term": "",
                    "message": f"入力の{non_latin / total:.0%}が非ラテン文字言語だが、キーワードはラテン文字のみ。"
                               "これらの記録はほぼFallbackへ回る。"})
        if term_hit_counts and signal_totals and signal in term_hit_counts and signal_totals.get(signal):
            total = signal_totals[signal]
            for term_l, count in sorted(term_hit_counts[signal].items(), key=lambda kv: -kv[1]):
                if count >= _HIGH_HIT_MIN_COUNT and count / total >= _HIGH_HIT_RATE:
                    findings.append({
                        "severity": SEVERITY_WARNING, "code": "high_hit_rate", "signal_source": signal,
                        "narrative_id": "", "field": "include_keywords", "term": term_l,
                        "message": f"このincludeが{signal}の{count / total:.1%}（{count}件）にヒット。"
                                   "汎用語による過剰マッチの疑い。"})
    return findings


def _term_vocabulary_stats(term: str, vocabulary: dict):
    """1つのキーワードの語彙統計: (kind, inflections, others, substrings)。語彙全体の走査が重いのでキャッシュ対象。"""
    compiled = matcher.compile_term(term)
    if compiled is None or compiled.kind not in (matcher.KIND_TOKEN, matcher.KIND_ACRONYM, matcher.KIND_PREFIX):
        return None
    base = term.strip().rstrip("*").lower()
    if not base.isalpha() or len(base) < 2:
        return None
    inflections, others, substrings = Counter(), Counter(), Counter()
    for word, count in vocabulary.items():
        if word == base or base not in word:
            continue
        if word.startswith(base):
            if word[len(base):] in _INFLECTION_SUFFIXES:
                inflections[word] += count
            else:
                others[word] += count
        else:
            substrings[word] += count
    return compiled.kind, inflections, others, substrings


def _term_vocabulary_findings(term: str, vocabulary: dict, covered_words: Optional[set] = None,
                              _stats=None) -> list:
    """語彙(corpusの語→件数)を使い、1つのキーワードについて (issue_code, severity, example, message) を返す。
    - substring_collision: 語を部分文字列に含むが別の語（'ICE'→'office'）。matcher v2では回避済みなのでINFO。
    - inflection_not_covered: 語幹が同じで接尾辞だけ違う語（'vote'→'voters'）。matcherは語幹展開しないため、
      masterに明示しないと取りこぼす（再現率の低下）。WARNING。
    - prefix_collision: `*`（語頭一致）が語形ではない語にも当たる。WARNING。"""
    stats = _stats if _stats is not None else _term_vocabulary_stats(term, vocabulary)
    if stats is None:
        return []
    kind, inflections, others, substrings = stats
    covered_words = covered_words or set()
    # 同じ行の別の語として明示済みの語形（例: `Governor`に対する`Governors`）は未カバーとみなさない
    inflections = Counter({w: n for w, n in inflections.items() if w not in covered_words})
    out = []

    def examples(counter):
        return ";".join(f"{w}({n})" for w, n in counter.most_common(_VOCAB_MAX_EXAMPLES))

    if kind == matcher.KIND_PREFIX:
        if sum(others.values()) >= _PREFIX_COLLISION_MIN_COUNT:
            out.append(("prefix_collision", SEVERITY_WARNING, examples(others),
                        "`*`（語頭一致）が語形ではない語にも当たる。"))
        return out
    if sum(inflections.values()) >= _VOCAB_MIN_COUNT:
        out.append(("inflection_not_covered", SEVERITY_WARNING, examples(inflections),
                    "語幹が同じ語形が多数あるが、masterに明示されていないため一致しない（matcherは語幹展開しない）。"))
    collisions = substrings + others
    if sum(collisions.values()) >= _VOCAB_MIN_COUNT:
        out.append(("substring_collision", SEVERITY_INFO, examples(collisions),
                    "部分文字列としては別の語に当たる。matcher v2のexact-token照合では一致しない。"))
    return out


def _lint_vocabulary(rows: list, vocabulary: dict) -> list:
    findings: list = []
    stats_cache: dict = {}
    for row in rows:
        if not parse_bool(row.get("active"), True):
            continue
        # この行で明示されている全ての語（Latin語）。語形が別の語として列挙済みかの判定に使う。
        row_words = set()
        for field in TERM_TYPE_BY_FIELD:
            for term in _terms(row, field):
                compiled = matcher.compile_term(term)
                if compiled is not None and compiled.kind in (matcher.KIND_TOKEN, matcher.KIND_ACRONYM):
                    row_words.add(term.strip().lower())
        seen = set()
        for field in TERM_TYPE_BY_FIELD:
            for term in _terms(row, field):
                if (field, term) in seen or len(term) > 40:
                    continue
                seen.add((field, term))
                if term not in stats_cache:
                    stats_cache[term] = _term_vocabulary_stats(term, vocabulary)
                for code, severity, example, message in _term_vocabulary_findings(
                        term, vocabulary, row_words, _stats=stats_cache[term]):
                    findings.append(_finding(severity, code, row, field, term, message, example))
    return findings


def findings_to_csv_rows(findings: list) -> list:
    """`narrative_master_lint.csv`の行（LINT_CSV_COLUMNS）へ変換する。severityは大文字（ERROR/WARNING/INFO）。"""
    order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    rows = []
    for f in sorted(findings, key=lambda f: (order.get(f["severity"], 3), f["signal_source"], f["narrative_id"],
                                              f["code"], f["term"])):
        rows.append({
            "signal_id": f["signal_source"], "narrative_id": f["narrative_id"], "term": f["term"],
            "term_type": f.get("term_type", ""), "issue_type": f["code"],
            "collision_example": f.get("collision_example", ""), "severity": f["severity"].upper(),
            "recommended_action": f.get("recommended_action", "") or f["message"],
        })
    return rows


def lint_narrative_master(master_rows: list, *, language_distribution: Optional[dict] = None,
                           term_hit_counts: Optional[dict] = None,
                           signal_totals: Optional[dict] = None,
                           vocabulary: Optional[dict] = None) -> list:
    """Narrative Master行のリストを検査し、findingのリスト（dict）を返す。副作用なし。
    非activeな行は検査しない（match対象外のため）。"""
    active_rows = [r for r in master_rows if parse_bool(r.get("active"), True)]
    findings: list = []
    for row in active_rows:
        findings.extend(_lint_row(row))
    findings.extend(_lint_cross_rows(master_rows))
    findings.extend(_lint_language_and_data(active_rows, language_distribution, term_hit_counts, signal_totals))
    if vocabulary:
        findings.extend(_lint_vocabulary(active_rows, vocabulary))
    return findings


def summarize_findings(findings: list) -> dict:
    """{'by_severity': {...}, 'by_code': {code: 件数}, 'errors': [findings...]} を返す。"""
    by_severity: dict = defaultdict(int)
    by_code: dict = defaultdict(int)
    for f in findings:
        by_severity[f["severity"]] += 1
        by_code[f["code"]] += 1
    return {
        "by_severity": dict(by_severity), "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
        "errors": [f for f in findings if f["severity"] == SEVERITY_ERROR],
    }


def format_findings_for_warning(findings: list, *, max_items: int = 10) -> list:
    """Run作成時のwarning表示用に、error/warningを重要度順に短い文字列へ整形する。"""
    order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    shown = sorted((f for f in findings if f["severity"] != SEVERITY_INFO),
                   key=lambda f: (order[f["severity"]], f["signal_source"], f["narrative_id"]))
    lines = [f"[{f['severity']}] {f['signal_source']}/{f['narrative_id'] or '-'} {f['code']}: "
             f"{f['term']} — {f['message']}" for f in shown[:max_items]]
    if len(shown) > max_items:
        lines.append(f"...他 {len(shown) - max_items} 件（詳細はlint_narrative_master()の戻り値を参照）")
    return lines
