"""Report Studio「Design」領域: Current Prompt1/Prompt2パイプライン向けPreprocess
（Shell Parity層、Phase D）。

既存`geo_report_processing_service.py`とは意図的に別モジュールにする。既存モジュールは
Iran/EUDR等の実プロジェクトが使う旧Narrative Master契約（dimension_config_json/古い
WIDE列構成）・旧`run.ps1 v6.1.6`アルゴリズムのままで維持し、回帰させない
（Phase A監査で`get_source_bucket`/`get_review_text`等が実際の
`run.ps1 build 2026-08-26-v4.4.0-generic-public-voice`と大きく乖離していることを
確認済み——例えば旧実装の`official`判定は`\\.go\\.|\\.gov\\b|government|...`だが、
実際のShellは`\\.go\\.jp|\\.lg\\.jp|mhlw|caa\\.go|maff|meti|gov`。単純な拡張ではなく
書き直しが必要な差分のため、既存の実運用中Runへの影響を避けて新設した）。

本モジュールはkind-napping-hare.md C/D章のCurrent契約
（34列Narrative Master、33列cluster_labeling_input.csv等）に厳密に一致させる、
`run.ps1`の忠実な移植。既存モジュールと純粋にアルゴリズムが一致することを確認できた
汎用関数（clean_value/escape_csv/parse_bool/parse_int/parse_date_safe/get_hash/
normalize_text/get_dedup_url_key/get_week_start/get_priority_weight/
get_match_signature/detect_input_encoding/resolve_header_name/各種ALIASES/
detect_signal_from_file）はimportして再利用する。それ以外
（get_source_bucket/get_review_text/get_reaction_types/get_attitude_hint/
get_source_class_preliminary/get_cluster_cue/get_sharded_cluster_id）は本モジュールで
新規に実装し直す。
"""

from __future__ import annotations

import csv
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional
from urllib.parse import urlparse

import geo_report_cluster_semantic_service as semantic
import geo_report_keyword_matcher as matcher
import geo_report_master_lint_service as master_lint
from geo_report_processing_service import normalize_text, build_population_table

from geo_report_processing_service import (  # noqa: F401 — 意図的な再エクスポート
    AUTHOR_ALIASES,
    DOCUMENT_TYPE_ALIASES,
    ENGAGEMENT_ALIASES,
    LANGUAGE_ALIASES,
    RECORD_ID_ALIASES,
    SIGNAL_ALIASES,
    SOURCE_COUNTRY_ALIASES,
    SOURCE_NAME_ALIASES,
    TEXT_ALIASES,
    TIMESTAMP_ALIASES,
    TITLE_ALIASES,
    URL_ALIASES,
    clean_value,
    detect_input_encoding,
    detect_signal_from_file,
    escape_csv,
    get_dedup_url_key,
    get_hash,
    get_match_signature,
    get_priority_weight,
    get_week_start,
    parse_bool,
    parse_date_safe,
    parse_int,
    resolve_header_name,
    shorten,
    split_phrases,
)

# ---------------------------------------------------------------------------
# Narrative Master: Current契約（34列、D章）向けのマッチングルール
# ---------------------------------------------------------------------------

REQUIRED_SIGNALS_MIN_COUNT = 1  # Phase A監査結果1: 「S1〜S7必須」は1案件専用のハードコード。
                                 # 一般化し、実在するSignalのみ検証対象にする（validate_narrative_masterと同じ方針）。

# Narrative Master行でmax_cluster_records/representative_countが空欄の場合に使う
# Shell運用の正式Default値（2026-09-15、外部レビュー指摘: Design Chat生成のNarrative
# Masterでこの2列が常に空欄になっており、明示値が無いと実運用上不安との指摘を受けた。
# ここが唯一の定義元——geo_report_design_chat_service.pyのNarrative Master生成時の
# デフォルト補完も、値のズレを防ぐためこの定数を直接importして使う）。
DEFAULT_MAX_CLUSTER_RECORDS = 200
DEFAULT_REPRESENTATIVE_COUNT = 8


@dataclass
class Rule:
    signal_source: str
    signal_name: str
    theme_id: str
    theme_name: str
    trend_role: str
    trend_start_date: str
    main_narrative: str
    sub_narrative: str
    sub_narrative_id: str
    stance_default: str
    include: list = field(default_factory=list)
    required: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    cues: list = field(default_factory=list)
    split_terms: list = field(default_factory=list)
    priority: str = "Medium"
    max_cluster_records: int = DEFAULT_MAX_CLUSTER_RECORDS
    representative_count: int = DEFAULT_REPRESENTATIVE_COUNT
    is_fallback: bool = False
    active: bool = True


def build_rule_from_row(row: dict, *, default_max_cluster_records: int = DEFAULT_MAX_CLUSTER_RECORDS,
                          default_representative_count: int = DEFAULT_REPRESENTATIVE_COUNT) -> Rule:
    """Narrative Master行（Current 34列契約、`sync_shell_mirror_columns`済み想定）から
    run.ps1の`Build-MasterRule`相当のRuleを組み立てる。"""
    return Rule(
        signal_source=clean_value(row.get("signal_source", "")).upper(),
        signal_name=clean_value(row.get("signal_name", "")),
        theme_id=clean_value(row.get("theme_id", "")),
        theme_name=clean_value(row.get("theme_name", "")),
        trend_role=clean_value(row.get("trend_role", "")),
        trend_start_date=clean_value(row.get("trend_start_date", "")),
        main_narrative=clean_value(row.get("main_narrative", "")),
        sub_narrative=clean_value(row.get("sub_narrative", "")),
        sub_narrative_id=clean_value(row.get("sub_narrative_id", "")),
        stance_default=clean_value(row.get("stance_default", "")),
        include=split_phrases(row.get("include_keywords", "")),
        required=split_phrases(row.get("required_context_keywords", "")),
        exclude=split_phrases(row.get("exclude_keywords", "")),
        cues=split_phrases(row.get("public_voice_cues", "")),
        split_terms=split_phrases(row.get("cluster_split_terms", "")),
        priority=clean_value(row.get("priority", "")) or "Medium",
        max_cluster_records=parse_int(row.get("max_cluster_records", ""), default_max_cluster_records),
        representative_count=parse_int(row.get("representative_count", ""), default_representative_count),
        is_fallback=parse_bool(row.get("is_fallback"), False),
        active=parse_bool(row.get("active"), True),
    )


def build_master_rules(master_rows: list, *, default_max_cluster_records: int = DEFAULT_MAX_CLUSTER_RECORDS,
                         default_representative_count: int = DEFAULT_REPRESENTATIVE_COUNT) -> tuple:
    """{signal: [Rule,...]}, {signal: fallback Rule}, {signal: representative Rule}を返す。"""
    rules_by_signal: dict = {}
    fallback_by_signal: dict = {}
    signal_meta: dict = {}
    for row in master_rows:
        rule = build_rule_from_row(
            row, default_max_cluster_records=default_max_cluster_records,
            default_representative_count=default_representative_count)
        if not rule.active:
            continue
        rules_by_signal.setdefault(rule.signal_source, []).append(rule)
        if rule.is_fallback:
            fallback_by_signal[rule.signal_source] = rule
        signal_meta.setdefault(rule.signal_source, rule)
    return rules_by_signal, fallback_by_signal, signal_meta


def _term_hits(terms: list, prepared) -> dict:
    """{原表記: (kind, [span,...])}。一致した語だけを返す。"""
    hits = {}
    for term in terms:
        compiled = matcher.compile_term(term)
        if compiled is None:
            continue
        spans = matcher.find_spans(compiled, prepared)
        if spans:
            hits[term] = (compiled.kind, spans)
    return hits


def _independent_include_hits(include_hits: dict, context_hits: dict) -> dict:
    """requiredがある場合、contextがincludeを丸ごと覆っているだけの組（同一surfaceの部分一致）を除き、
    少なくとも1つのcontext hitと独立に成立するinclude hitだけを残す。"""
    kept = {}
    for term, (kind, spans) in include_hits.items():
        include_needle = matcher.compile_term(term).needle
        for ctx_term, (ctx_kind, ctx_spans) in context_hits.items():
            # 語レベルの自己発火: includeの語がcontextの語に含まれる/同一なら、位置に関係なく別根拠にならない
            # （例: include `governor` / context `Governor` は、`Governor`が2回出ても成立させない）。
            if (kind == matcher.KIND_CJK) == (ctx_kind == matcher.KIND_CJK) \
                    and include_needle in matcher.compile_term(ctx_term).needle:
                continue
            if matcher.has_independent_pair(spans, ctx_spans, kind, ctx_kind):
                kept[term] = (kind, spans)
                break
    return kept


def _format_span(hit: tuple) -> str:
    return f"{hit[1][0][0]}-{hit[1][0][1]}"


def match_master(rules: list, fallback: Optional[Rule], text: str) -> dict:
    """run.ps1の`Match-Master`の移植（keyword_matcher_v2）。include必須1件以上ヒットしたルールの中で
    priority重み+includeヒット長+cue加点が最大のものを採用する。ヒット0件・全除外時は
    Fallbackルールへ回し、fallback_context/fallback_no_required_context/
    fallback_excluded_contextのいずれかに分類する。

    照合はgeo_report_keyword_matcherのContractに従う（Latinは語境界、CJKは別処理、語幹展開なし）。
    2026-10-04: 従来の部分一致では`ICE`が`office`に、`govern`が`Governor`に当たっていた。さらに、
    requiredがあるルールでは、includeとcontextが同一surfaceの部分一致（例: include `govern`とcontext
    `Governor`）だけで両方成立しないよう、includeとcontextを別々に記録して独立性を確認する。

    戻り値の追加項目（監査用）: matched_context_terms / matched_exclude_terms / match_method / matched_span。"""
    prepared = matcher.prepare_text(text)
    best = None
    best_score = -1
    best_info: dict = {}
    for rule in rules:
        if rule.is_fallback:
            continue
        if _term_hits(rule.exclude, prepared):
            continue
        context_hits = _term_hits(rule.required, prepared) if rule.required else {}
        if rule.required and not context_hits:
            continue
        include_hits = _term_hits(rule.include, prepared)
        if rule.required:
            include_hits = _independent_include_hits(include_hits, context_hits)
        if not include_hits:
            continue
        hits = [term for term in rule.include if term in include_hits]
        score = get_priority_weight(rule.priority)
        for term in hits:
            score += 10 + min(len(term), 20)
        for cue in rule.cues:
            if matcher.term_spans(cue, prepared):
                score += 5
        if score > best_score:
            first = include_hits[hits[0]]
            span = f"inc:{_format_span((hits[0], first))}"
            if context_hits:
                first_ctx = next(iter(context_hits.items()))
                span += f";ctx:{_format_span(first_ctx)}"
            best, best_score = rule, score
            best_info = {
                "hits": hits,
                "context": "|".join(context_hits),
                "method": "|".join(sorted({include_hits[t][0] for t in hits})),
                "span": span,
            }

    if best is None:
        fallback_status = "fallback"
        excluded = ""
        if fallback is not None:
            fallback_exclude_hits = _term_hits(fallback.exclude, prepared)
            if fallback_exclude_hits:
                fallback_status = "fallback_excluded_context"
                excluded = "|".join(fallback_exclude_hits)
            elif fallback.required:
                if _term_hits(fallback.required, prepared):
                    fallback_status = "fallback_context"
                else:
                    fallback_status = "fallback_no_required_context"
        return {"rule": fallback, "matched_terms": "", "match_status": fallback_status,
                "matched_context_terms": "", "matched_exclude_terms": excluded, "match_method": "", "matched_span": ""}
    return {"rule": best, "matched_terms": "|".join(best_info["hits"]), "match_status": "matched",
            "matched_context_terms": best_info["context"], "matched_exclude_terms": "",
            "match_method": best_info["method"], "matched_span": best_info["span"]}


# ---------------------------------------------------------------------------
# レコード単位の決定的判定（run.ps1 v4.4.0の忠実な移植）
# ---------------------------------------------------------------------------

# official判定は「URLのホスト名」と「source_name」に分けて行う（2026-10-03、Preprocess潜在不具合監査 #3）。
# 従来はURL全体（パス・クエリ含む）に対し`gov`を部分一致していたため、US中間選挙fixtureで
# `/governor-race-...`を含むニュース記事・Reddit・`twitter.com/GovKathyHochul`等が`official`
# （=Official）に入っていた（officialバケットの93.8%が政府ドメインではなかった）。
# ホスト名は`.go.jp`/`.lg.jp`/`gov`ラベル（例: ny.gov, fmprc.gov.cn, gov.uk）に限定する。
_OFFICIAL_HOST_RE = re.compile(r"(^|\.)gov(\.[a-z]{2,3})?$|\.go\.jp$|\.lg\.jp$")
_OFFICIAL_SOURCE_NAME_RE = re.compile(r"\.go\.jp|\.lg\.jp|\bmhlw\b|\bmaff\b|\bmeti\b|\bgov\b")
# `press|release`をdocument typeへ素朴に部分一致すると`WordPress`までCorporate_PRになる。
# `press`は単語として、`release`は部分一致（PressRelease等）で判定する。
_PR_DOCUMENT_TYPE_RE = re.compile(r"(?<![a-z])press(?![a-z])|release")


def _extract_host(url: str) -> str:
    text = clean_value(url)
    if not text:
        return ""
    try:
        netloc = urlparse(text if "://" in text else "//" + text).netloc.lower()
    except ValueError:
        return ""
    return netloc.rsplit("@", 1)[-1].split(":", 1)[0]


def get_source_bucket(url, document_type, source_name) -> str:
    u = f"{clean_value(url)} {clean_value(source_name)}".lower()
    d = clean_value(document_type).lower()
    if re.search(r"prtimes|businesswire|globenewswire|prnewswire|newswire|jpubb", u) or _PR_DOCUMENT_TYPE_RE.search(d):
        return "pr_release"
    if _OFFICIAL_HOST_RE.search(_extract_host(url)) or _OFFICIAL_SOURCE_NAME_RE.search(clean_value(source_name).lower()):
        return "official"
    # 明示的なcomment/social投稿は、news/articleより先に判定する必要がある
    # （"News Comment"のようなdocument typeがMedia扱いになりPublic Voiceを抑圧してしまうため）
    if re.search(r"comment|social|tweet|post|instagram|facebook|reddit|tiktok|youtube", d):
        return "public_social"
    if re.search(r"forum|board", d):
        return "public_forum"
    if re.search(r"news|article", d):
        return "media"
    if re.search(r"blog|wordpress", d):
        return "public_blog"
    if re.search(r"x\.com|twitter\.com|facebook\.com|instagram\.com|tiktok\.com|threads\.net|bsky\.app|reddit\.com|youtube\.com", u):
        return "public_social"
    if re.search(r"5ch|2ch|girlschannel|komachi|bakusai|mamastar|forum|bbs|board", u):
        return "public_forum"
    if re.search(r"ameblo|note\.com|hatenablog|livedoor|cocolog|blog", u):
        return "public_blog"
    if re.search(r"review|reviews|amazon|rakuten|shopping\.yahoo", u):
        return "public_review_or_ec"
    return "unknown"


_UPDATED_POST_PREFIX_RE = re.compile(r"^\[This post has been updated\.\]\s*", re.IGNORECASE)
_SOCIAL_MARKERS = (
    "===== begin shared post =====", "[quoted @", "----- attachment -----", "----- details -----",
)
_RT_FULL_RE = re.compile(r"^\s*RT\s+@[^:]+:\s*", re.IGNORECASE)
_RT_MID_RE = re.compile(r"^(.*?)\s+RT\s+@[^:]+:\s*", re.IGNORECASE)


def get_review_text(title, text, source_bucket: str) -> str:
    body = clean_value(text)
    title_text = clean_value(title)
    if source_bucket == "public_social":
        own_text = _UPDATED_POST_PREFIX_RE.sub("", body)
        lower = own_text.lower()
        for marker in _SOCIAL_MARKERS:
            pos = lower.find(marker)
            if pos >= 0:
                own_text = own_text[:pos].strip()
                lower = own_text.lower()
        if _RT_FULL_RE.match(own_text):
            return ""
        m = _RT_MID_RE.match(own_text)
        if m:
            own_text = m.group(1)
        return shorten(own_text, 500)
    if source_bucket in ("media", "pr_release", "official"):
        return shorten(title_text, 400)
    if body:
        return shorten(body, 500)
    return shorten(title_text, 400)


def get_review_text_status(title, text, source_bucket: str, review_text: str) -> str:
    """text_for_reviewの状態を区別する（2026-10-03、Preprocess潜在不具合監査 #2）。
    source_class_preliminaryの`Fact_Only_Candidate`は「本文が空」というだけで付くため、
    次の別々の状態が全て混ざっていた（US fixtureの空欄代表の98.8%は純粋なRTで、rawには本文がある）。
    source_classは変えず、区別のための列として`review_text_status`を別に出す。

      has_text                   : text_for_reviewが非空
      retweet_only               : SNSの純粋なRT（rawに本文はあるが、本人の文は無い）
      attachment_or_shared_only  : 本文が添付/共有投稿マーカーで始まり、本人の文が無い
      missing_body               : title相当はあるが、rawに本文が無い
      metadata_only              : title/本文とも空（URL・投稿者等のメタデータのみ）
      title_missing              : media/official/pr_releaseでtitleが空（本文はあるがtitleのみ使う仕様）
    """
    if review_text:
        return "has_text"
    body = clean_value(text)
    title_text = clean_value(title)
    if not body and not title_text:
        return "metadata_only"
    if not body:
        return "missing_body"
    if source_bucket == "public_social":
        own_text = _UPDATED_POST_PREFIX_RE.sub("", body)
        if _RT_FULL_RE.match(own_text):
            return "retweet_only"
        lower = own_text.lower()
        if any(marker in lower for marker in _SOCIAL_MARKERS):
            return "attachment_or_shared_only"
        return "missing_body"
    return "title_missing"


def get_source_class_preliminary(source_bucket: str, review_text: str, document_type: str) -> str:
    """v4.4.0の高Recall候補ラベル（最終判定ではない、Prompt1がPrecisionを担保する）。"""
    if source_bucket == "official":
        return "Official"
    if source_bucket == "pr_release":
        return "Corporate_PR"
    if source_bucket == "media":
        return "Media"
    if source_bucket.startswith("public_") or source_bucket == "public_voice":
        return "Public_Voice_Candidate" if review_text else "Fact_Only_Candidate"
    if re.search(r"news|article", clean_value(document_type).lower()):
        return "Media"
    return "Unknown"


_QUESTION_RE = re.compile(
    r"[?？]|\b(why|how|what|when|where|who|which|really|is it|are they|do you|does it|can we|could we)\b"
    r"|なぜ|どう|何故|本当|なのか|だろうか|かな|でしょうか", re.IGNORECASE)
_EVALUATION_RE = re.compile(
    r"\b(think|believe|feel|support|oppose|agree|disagree|approve|disapprove|prefer|trust|distrust|good|bad|"
    r"better|worse|right|wrong|qualified|unqualified)\b"
    r"|思う|考える|感じる|支持|反対|賛成|評価|好ましい|良い|悪い|好き|嫌い|信頼|信用でき", re.IGNORECASE)
_EMOTION_RE = re.compile(
    r"\b(love|hate|happy|glad|angry|furious|worried|concerned|afraid|hope|fear|disappointed|excited)\b"
    r"|嬉し|悲し|怒|腹立|不安|心配|怖|期待|失望", re.IGNORECASE)
_PREDICTION_RE = re.compile(
    r"\b(likely|unlikely|might|may|could|probably|possibly|expect|predict|forecast|seems likely|looks like)\b"
    r"|見込み|可能性|予想|予測|だろう|そうだ|と思われ|になりそう", re.IGNORECASE)
_BEHAVIOR_RE = re.compile(
    r"\b(i will|i won't|i would|i wouldn't|i plan to|i intend to|i decided to|we will|we won't|we plan to|"
    r"stop using|switch to|choose|avoid)\b"
    r"|するつもり|しないつもり|決めた|やめる|切り替える|選ぶ|避ける", re.IGNORECASE)

_REACTION_TYPE_ORDER = ("Behavior", "Evaluation", "Emotion", "Prediction", "Question")
_REACTION_TYPE_PATTERNS = {
    "Question": _QUESTION_RE, "Evaluation": _EVALUATION_RE, "Emotion": _EMOTION_RE,
    "Prediction": _PREDICTION_RE, "Behavior": _BEHAVIOR_RE,
}


def get_reaction_types(text: str) -> str:
    """汎用・テーマ非依存の弱いヒントのみ（Public Voice判定のGateには使わない、
    最終判定はPrompt1が行う）。'|'区切りで複数値を返す（run.ps1と同じ形式）。"""
    if not text:
        return ""
    found = [name for name in ("Question", "Evaluation", "Emotion", "Prediction", "Behavior")
             if _REACTION_TYPE_PATTERNS[name].search(text)]
    return "|".join(found)


def get_primary_reaction_type(reaction_types: str) -> str:
    if not reaction_types:
        return "None"
    values = set(reaction_types.split("|"))
    for name in _REACTION_TYPE_ORDER:
        if name in values:
            return name
    return "None"


_POSITIVE_RE = re.compile(
    r"\b(support|approve|agree|trust|prefer|good|great|better|best|qualified|welcome|hopeful|love|like)\b"
    r"|支持|賛成|信頼|好ましい|良い|素晴らしい|歓迎|期待|好き", re.IGNORECASE)
_NEGATIVE_RE = re.compile(
    r"\b(oppose|disapprove|disagree|distrust|bad|worse|worst|unqualified|wrong|corrupt|hate|angry|concerned|"
    r"worried|reject)\b"
    r"|反対|不信|信用できない|悪い|最悪|不適格|間違|腐敗|嫌い|怒|不安|心配|拒否", re.IGNORECASE)


def get_attitude_hint(text: str, reaction_types: str) -> str:
    """汎用な極性ヒントのみ。最終センチメントラベルとして使わないこと（run.ps1コメント通り）。"""
    if not text:
        return "unknown"
    positive = bool(_POSITIVE_RE.search(text))
    negative = bool(_NEGATIVE_RE.search(text))
    if positive and negative:
        return "mixed"
    if negative:
        return "negative"
    if positive:
        return "positive"
    if reaction_types:
        return "neutral"
    return "unknown"


def get_cluster_cue(text: str, reaction_type: str, split_terms: list) -> str:
    """Narrative Masterのcluster_split_termsに含まれるトピック固有語のみを使う。
    一致が無ければreaction_typeから汎用cueへフォールバックする。"""
    prepared = matcher.prepare_text(text)
    for term in split_terms:
        if matcher.term_spans(term, prepared):
            return term
    return {
        "Behavior": "behavior", "Question": "question", "Prediction": "prediction",
        "Emotion": "emotion", "Evaluation": "evaluation",
    }.get(reaction_type, "general")


def get_sharded_cluster_id(prefix: str, base_key: str, max_records: int, counts: dict) -> str:
    """run.ps1の`Get-ShardedClusterId`と同じシャーディング（(sequence-1)//max+1）。
    prefix='AC'|'SC'。ハッシュは14文字小文字（実データ確認済みの`AC_xxxxxxxxxxxxxx`形式）。
    cluster_idはRunを跨いでグローバル一意ではない（Phase A監査結果1で確定済み——
    countsがRun実行ごとに空初期化されるため、同一内容が別Runで同じIDになり得る。
    DB保存時は(run_id, cluster_id)の組で扱うこと、cluster_id単体をグローバルキーにしない）。"""
    counts[base_key] = counts.get(base_key, 0) + 1
    sequence = counts[base_key]
    shard = (sequence - 1) // max(max_records, 1) + 1
    h = get_hash(f"{base_key}|shard={shard}")
    return f"{prefix}_{h[:14]}"


# ---------------------------------------------------------------------------
# Dedup（Signal単位スコープ、media/official/pr_releaseのみURL基準）
# ---------------------------------------------------------------------------

_URL_DEDUP_BUCKETS = ("media", "official", "pr_release")


def is_duplicate_record(signal: str, source_bucket: str, url_hash: str, text_hash: str,
                          seen_urls: set, seen_texts: set) -> tuple:
    """(is_duplicate, duplicate_group)。非重複なら呼び出し側がseen_urls/seen_textsへ登録すること。"""
    use_url_dedup = source_bucket in _URL_DEDUP_BUCKETS
    signal_url_key = f"{signal}|{url_hash}" if (use_url_dedup and url_hash) else ""
    signal_text_key = f"{signal}|{text_hash}" if text_hash else ""
    if signal_url_key and signal_url_key in seen_urls:
        return True, f"url:{url_hash[:16]}"
    if signal_text_key and signal_text_key in seen_texts:
        return True, f"text:{text_hash[:16]}"
    return False, ""


# ---------------------------------------------------------------------------
# Cluster統計（Analysis/Sentiment共通の形状、run.ps1の`New-ClusterStats`相当）
# ---------------------------------------------------------------------------

@dataclass
class ClusterStats:
    cluster_id: str
    kind: str  # 'analysis' | 'sentiment'
    rule: Rule
    week_start: str
    week_end: str
    source_class_preliminary: str
    source_bucket: str
    reaction_type_hint: str
    attitude_hint: str
    cluster_cue: str
    match_signature: str
    representative_limit: int
    record_count: int = 0
    duplicate_count: int = 0
    matched_count: int = 0
    fallback_count: int = 0
    representatives: list = field(default_factory=list)
    exception_representatives: list = field(default_factory=list)
    source_distribution: dict = field(default_factory=dict)
    reaction_distribution: dict = field(default_factory=dict)
    attitude_distribution: dict = field(default_factory=dict)
    language_distribution: dict = field(default_factory=dict)
    # 2026-09-16追加（Recursive Split, #3）: reservoir samplingされたrepresentatives
    # （既定8件）とは別に、そのcluster_idへ割り当てられた全メンバーのrepresentative
    # dictを保持する。get_sharded_cluster_idのシャーディングにより、1つのcluster_idが
    # 受け取るrecord数は常にmax_cluster_records以下に自然に有界化されるため
    # （新しいshardが発番されるだけ）、all_membersのメモリコストは既存の
    # representatives reservoir sampling相当のオーダーに収まる。cap-hit Clusterの
    # heterogeneity検知・recursive split・representative再生成の元データとして使う。
    all_members: list = field(default_factory=list)
    # Recursive Split結果メタデータ（既定値=未評価/未分割。cap-hit判定・coherence評価は
    # 全Clusterに対してではなく、record_count >= rule.max_cluster_recordsのClusterに
    # ついてのみ事後的に行う——大規模Runで無駄な計算をしない）。
    cluster_cap_hit: bool = False
    semantic_coherence_status: str = "Not Evaluated"
    representative_diversity: Optional[float] = None
    recursive_split_applied: bool = False
    recursive_split_depth: int = 0
    parent_cluster_id: str = ""
    split_reason: str = ""
    heterogeneous_cluster: bool = False


def _increment(table: dict, key: str) -> None:
    k = key if key else "Unknown"
    table[k] = table.get(k, 0) + 1


def _majority_label(distribution: dict, fallback: str) -> str:
    """分布の最頻値を返す（同数は件数降順→キー昇順で決定的に決める）。分布が空ならfallback。"""
    if not distribution:
        return fallback
    return sorted(distribution.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def finalize_cluster_labels(clusters: dict) -> None:
    """Cluster単位のsource_class_preliminary/reaction_type_hint/attitude_hintを、最初の1件ではなく
    そのClusterに入った全recordの分布の最頻値で確定する（2026-10-03、Preprocess潜在不具合監査 #3）。

    Analysis Clusterのbase_keyはsource_class/reaction/attitudeを含まない（cue・source_bucket等のみ）ため、
    ClusterStats作成時に最初のrecordの値を入れただけだと、同一Clusterの他recordと食い違う
    （US fixtureでAnalysis Clusterの6.5%のrecordがClusterのsource_classと不一致、Sentiment Clusterは
    key自体にこれらを含むため0%）。record単位の真値はsource_distribution等に常に残っており、
    ここでは「Clusterの代表ラベル」だけを分布と整合させる。Sentiment Clusterは一様なので結果は変わらない。"""
    for stats in clusters.values():
        stats.source_class_preliminary = _majority_label(stats.source_distribution, stats.source_class_preliminary)
        stats.reaction_type_hint = _majority_label(stats.reaction_distribution, stats.reaction_type_hint)
        stats.attitude_hint = _majority_label(stats.attitude_distribution, stats.attitude_hint)


def add_representative(stats: ClusterStats, representative: dict, seed_hash: str, is_exception: bool) -> None:
    """reservoir sampling（run.ps1の`Add-Representative`と同じ、record_countで割った
    決定的な採否判定によりRunを再実行しても同じ代表投稿が選ばれる）。"""
    limit = stats.representative_limit
    reps = stats.representatives
    if len(reps) < limit:
        reps.append(representative)
    else:
        pick = int(seed_hash[:8], 16) % max(stats.record_count, 1)
        if pick < limit:
            reps[pick] = representative
    if is_exception and len(stats.exception_representatives) < 4:
        stats.exception_representatives.append(representative)


# ---------------------------------------------------------------------------
# Recursive Split（2026-09-16追加、#3）: max_cluster_recordsへ到達した
# （cluster_cap_hit）Clusterを、そのまま1 Clusterとして確定しない。
# geo_report_cluster_semantic_service（決定的coherence proxy、embedding未導入）を
# 使ってheterogeneityを検知し、異質な場合のみ分割候補を生成する。
# 「200件だから必ず分割」ではなく「cap hit + heterogeneity」を条件にする
# （ユーザー要望3-2）。
# ---------------------------------------------------------------------------

RECURSIVE_SPLIT_MAX_DEPTH = 3
COHERENCE_BACKEND = "deterministic"  # 将来embedding backend追加時にここを差し替える


def _rebuild_cluster_stats_from_members(parent: ClusterStats, members: list, *, cluster_id: str,
                                          depth: int, split_reason: str) -> ClusterStats:
    """Split後のChild Cluster用にClusterStatsを再構築する。Parentのrepresentatives/
    分布をそのまま流用せず、Child自身のmembersから再生成する（ユーザー要望3-4）。"""
    child = ClusterStats(
        cluster_id=cluster_id, kind=parent.kind, rule=parent.rule,
        week_start=parent.week_start, week_end=parent.week_end,
        source_class_preliminary=Counter(m["source_class_preliminary"] for m in members).most_common(1)[0][0],
        source_bucket=Counter(m["source_bucket"] for m in members).most_common(1)[0][0],
        reaction_type_hint=Counter(
            get_primary_reaction_type(m["reaction_type_hint"]) for m in members).most_common(1)[0][0],
        attitude_hint=Counter(m["attitude_hint"] for m in members).most_common(1)[0][0],
        cluster_cue=parent.cluster_cue, match_signature=parent.match_signature,
        representative_limit=parent.representative_limit,
        parent_cluster_id=parent.cluster_id, recursive_split_depth=depth,
        recursive_split_applied=True, split_reason=split_reason, cluster_cap_hit=parent.cluster_cap_hit,
    )
    for m in members:
        child.record_count += 1
        if m["match_status"] == "matched":
            child.matched_count += 1
        else:
            child.fallback_count += 1
        _increment(child.source_distribution, m["source_class_preliminary"])
        _increment(child.reaction_distribution, get_primary_reaction_type(m["reaction_type_hint"]))
        _increment(child.attitude_distribution, m["attitude_hint"])
        _increment(child.language_distribution, m.get("language_group", ""))
        is_exception = (
            m["attitude_hint"] in ("mixed", "unknown") or "|" in m["reaction_type_hint"]
            or m["match_status"] != "matched")
        seed = get_hash(f"{m.get('record_id', '')}|{cluster_id}")
        add_representative(child, m, seed, is_exception)
        child.all_members.append(m)
    return child


def _split_cluster_recursive(parent: ClusterStats, *, depth: int = 1,
                               max_depth: int = RECURSIVE_SPLIT_MAX_DEPTH,
                               coherence_backend: str = COHERENCE_BACKEND) -> list:
    """parent.all_membersをdiscriminatorで分割し、各グループについて再帰的に
    coherenceを再評価する。分割候補が見つからない場合は[parent]のまま返し
    （heterogeneous_cluster=trueを立てる）、Prompt1側での確認に委ねる
    （ユーザー要望3-3: max_depthに達しても高分散ならheterogeneous_cluster=true）。"""
    split = semantic.choose_split_discriminator(parent.all_members, backend=coherence_backend)
    if split is None:
        parent.heterogeneous_cluster = True
        return [parent]

    discriminator, groups = split
    children = []
    for i, (value, members) in enumerate(sorted(groups.items(), key=lambda kv: kv[0])):
        child_id = f"{parent.cluster_id}-S{depth}{i}"
        reason = f"{discriminator}={value}"
        child = _rebuild_cluster_stats_from_members(parent, members, cluster_id=child_id, depth=depth,
                                                      split_reason=reason)
        if len(members) >= 2:
            coherence = semantic.evaluate_cluster_coherence(members, backend=coherence_backend)
            child.semantic_coherence_status = coherence.status
            child.representative_diversity = coherence.representative_diversity
        else:
            child.semantic_coherence_status = "High"
            child.representative_diversity = 0.0

        if (child.semantic_coherence_status == "Low" and depth < max_depth
                and len(members) >= semantic.MIN_SPLIT_GROUP_SIZE):
            children.extend(_split_cluster_recursive(
                child, depth=depth + 1, max_depth=max_depth, coherence_backend=coherence_backend))
        else:
            if child.semantic_coherence_status == "Low":
                child.heterogeneous_cluster = True
            children.append(child)
    return children


def apply_recursive_split(clusters: dict, *, coherence_backend: str = COHERENCE_BACKEND) -> dict:
    """cap-hit（record_count >= rule.max_cluster_records）のClusterについてのみ
    coherence評価を行い、Lowと判定されたものをrecursive splitする。cap-hit以外は
    評価コストをかけず既定値（Not Evaluated/False）のまま返す——大規模Runで無駄な
    計算をしないため（ユーザー要望3-1: cap hitをQuality Signalとして扱うが、
    'cap-hitでない大多数のCluster'にまで評価コストを広げない）。

    2段階評価（ユーザー要望、将来embedding backend導入時の呼び出しパターンを
    踏襲）: まずrepresentative（reservoir sample、既定8件）だけで安価に判定し、
    High/Mediumならall_membersまでは展開しない。Lowが出た場合のみall_members
    （shard cap分、最大200件程度）まで展開して再評価する。"""
    result: dict = {}
    for cluster_id, stats in clusters.items():
        if stats.record_count < stats.rule.max_cluster_records:
            result[cluster_id] = stats
            continue
        stats.cluster_cap_hit = True

        # lexical_v2は全メンバー(cap以下、既定最大200件)を直接評価する。代表8件だけの1段目は標本の偏りで
        # 異質なclusterを見逃し得る（合成で再現）ため、v2では1段目を使わない。
        if coherence_backend == semantic.BACKEND_LEXICAL_V2:
            cheap = semantic.evaluate_cluster_coherence(stats.all_members, backend=coherence_backend)
        else:
            cheap = semantic.evaluate_cluster_coherence(stats.representatives, backend=coherence_backend)
        if cheap.status != "Low":
            stats.semantic_coherence_status = cheap.status
            stats.representative_diversity = cheap.representative_diversity
            result[cluster_id] = stats
            continue

        full = semantic.evaluate_cluster_coherence(stats.all_members, backend=coherence_backend)
        stats.semantic_coherence_status = full.status
        stats.representative_diversity = full.representative_diversity
        if full.status != "Low":
            result[cluster_id] = stats
            continue

        children = _split_cluster_recursive(stats, coherence_backend=coherence_backend)
        if len(children) == 1 and children[0] is stats:
            result[cluster_id] = stats
        else:
            for child in children:
                result[child.cluster_id] = child
    return result


def _build_record_to_child_cluster_map(before: dict, after: dict) -> dict:
    """(分割前のcluster_id, record_id) -> 分割後のcluster_id のマップを構築する（分割が実際に
    発生したClusterのrecordのみ含む）。record_map_rowsのanalysis_cluster_id/
    sentiment_cluster_idを、分割後も存在する正しいIDへ付け替えるために使う
    （2026-09-16追加、#3）。

    2026-10-03修正（Preprocess潜在不具合監査）: キーを従来のrecord_idのみから
    (分割前cluster_id, record_id)へ変更した。dedupがSignal単位のため、同じ文書(record_id)が
    複数Signalに非重複として存在する（US fixtureで10,379件）。record_idだけをキーにすると、
    あるSignalのClusterが分割された際に別Signalの同一record_idの行まで分割後IDへ
    書き換えられ、record_mapが別Signalのclusterを指してしまう。
    分割後ID（'<親>-S<深さ><連番>'）の先頭部分が分割前のcluster_id。"""
    mapping: dict = {}
    for cluster_id, stats in after.items():
        if stats.recursive_split_applied and cluster_id not in before:
            original_id = cluster_id.split("-S", 1)[0]
            for m in stats.all_members:
                rid = m.get("record_id")
                if rid:
                    mapping[(original_id, rid)] = cluster_id
    return mapping


# ---------------------------------------------------------------------------
# Prompt1 Current Input契約（C章、33/23列）へのCSV変換
# ---------------------------------------------------------------------------

# run.ps1のCurrent契約は33列（Export-ClusterFiles）。2026-09-16、Recursive Split
# （#3）導入に伴い、末尾へメタデータ列を追加した（既存consumerはDictReader/列名
# ベースで読む前提のため、末尾への追加はCurrent契約を破壊しない——列順・欠落列に
# 依存しないという既存の設計方針、parse_narrative_master_csv等と同じ考え方）。
CLUSTER_LABELING_COLUMNS = [
    "cluster_id", "signal_id", "signal_source", "signal_name", "report_topic_label", "theme_id", "theme_name",
    "trend_role", "trend_start_date", "main_narrative", "narrative_id", "narrative_name",
    "provisional_sub_narrative", "provisional_sub_narrative_id", "week_start", "week_end", "cluster_records",
    "eligible_records", "duplicate_records", "source_class_preliminary", "source_bucket", "reaction_type_hint",
    "attitude_hint", "cluster_cue", "match_signature", "exception_candidate_count", "source_distribution",
    "reaction_distribution", "attitude_distribution", "language_distribution", "fallback_share",
    "preliminary_consistency", "requires_review",
    # Recursive Split metadata（2026-09-16追加、#3）
    "cluster_cap_hit", "representative_diversity", "semantic_coherence_status",
    "recursive_split_applied", "recursive_split_depth", "parent_cluster_id", "split_reason",
    "heterogeneous_cluster",
    # 2026-10-04追加: requires_reviewの理由（`|`区切り）。空ならレビュー不要。
    "review_reasons",
    # 2026-10-03追加（Preprocess潜在不具合監査 #3）: Clusterのsource_class_preliminary（最頻値）が
    # Cluster内の全recordのうち何割を占めるか。1.0未満はAnalysis Clusterに複数のsource classが混在
    # している（record単位の真値はsource_distribution）。
    "source_class_purity",
]

CLUSTER_REPRESENTATIVE_COLUMNS = [
    "cluster_id", "signal_id", "report_topic_label", "narrative_id", "narrative_name", "representative_no",
    "record_id", "title", "text_for_review", "source_name", "domain", "url", "published_at", "language_group",
    "author", "documentTypeName", "source_bucket", "source_class_preliminary", "reaction_type_hint",
    "attitude_hint", "matched_terms", "match_status", "source_file",
    # 2026-10-03追加（Preprocess潜在不具合監査 #2）: text_for_reviewが空の理由の区別
    "review_text_status",
    # 2026-10-04追加（keyword_matcher_v2）: Narrative割当の根拠。matched_termsがinclude、
    # matched_context_termsがrequired context。match_methodはlatin_token/latin_phrase/latin_acronym/
    # latin_prefix/cjk_phrase、matched_spanは照合用の正規化表面上の位置（inc:start-end;ctx:start-end）。
    "matched_context_terms", "matched_exclude_terms", "match_method", "matched_span",
]

EXCEPTION_REVIEW_COLUMNS = [
    "cluster_id", "record_id", "text_for_review", "url", "source_class_preliminary", "reaction_type_hint",
    "attitude_hint", "match_status", "review_reason",
    "review_text_status",
]


def _serialize_distribution(table: dict) -> str:
    return "|".join(f"{k}:{table[k]}" for k in sorted(table.keys()))


REVIEW_MISSING_TEXT_SHARE = 0.5      # review_text_statusがhas_text以外のrecordがこの割合以上
REVIEW_MIXED_REACTION_SHARE = 0.3    # attitude=mixed または複数reactionのrecordがこの割合以上
REVIEW_SOURCE_PURITY_MIN = 0.9       # source_class_purityがこれ未満


def get_review_reasons(stats: "ClusterStats", *, fallback_share: float, purity: float,
                       flagged_terms: Optional[set] = None) -> list:
    """requires_reviewの理由を分離して返す（従来は全clusterが一律True）。理由が空なら高品質・一貫した
    clusterとしてrequires_review=falseになり得る。preliminary_consistencyの導出条件とは独立。"""
    reasons = []
    if fallback_share > 0.25:
        reasons.append("fallback")
    members = stats.all_members
    if flagged_terms and members:
        signal, narrative = stats.rule.signal_source, stats.rule.sub_narrative_id
        for m in members:
            if any((signal, narrative, t.lower()) in flagged_terms or (signal, "", t.lower()) in flagged_terms
                   for t in (m.get("matched_terms") or "").split("|") if t):
                reasons.append("keyword_collision")
                break
    if purity < REVIEW_SOURCE_PURITY_MIN or stats.source_bucket == "unknown":
        reasons.append("source_identity_uncertain")
    if members:
        missing = sum(1 for m in members if m.get("review_text_status", "has_text") != "has_text") / len(members)
        if missing >= REVIEW_MISSING_TEXT_SHARE:
            reasons.append("missing_text")
        mixed = sum(1 for m in members if m.get("attitude_hint") == "mixed"
                    or "|" in (m.get("reaction_type_hint") or "")) / len(members)
        if mixed >= REVIEW_MIXED_REACTION_SHARE:
            reasons.append("mixed_reaction")
    if stats.cluster_cap_hit:
        reasons.append("cap_hit")
    if stats.semantic_coherence_status == "Low" or stats.heterogeneous_cluster:
        reasons.append("low_semantic_coherence")
    reps = stats.representatives
    if len(reps) >= 3:
        top_attitude = Counter(m.get("attitude_hint") for m in reps).most_common(1)[0][0]
        top_class = Counter(m.get("source_class_preliminary") for m in reps).most_common(1)[0][0]
        if top_attitude != stats.attitude_hint or top_class != stats.source_class_preliminary:
            reasons.append("representative_conflict")
    return reasons


def build_cluster_labeling_rows(clusters: dict, flagged_terms: Optional[set] = None) -> list:
    """cluster_labeling_input.csv / sentiment_cluster_labeling_input.csv 相当
    （両方とも同一33列スキーマ、run.ps1が同じExport-ClusterFilesを使うため）。"""
    rows = []
    for cluster_id in sorted(clusters.keys()):
        stats = clusters[cluster_id]
        eligible = stats.record_count - stats.duplicate_count
        fallback_share = round(stats.fallback_count / stats.record_count, 4) if stats.record_count else 0
        exception_count = len(stats.exception_representatives)
        if fallback_share > 0.25 or stats.cluster_cue == "general" or eligible > 100 or exception_count > 0:
            consistency = "Low"
        elif fallback_share > 0 or eligible > 40:
            consistency = "Medium"
        else:
            consistency = "High"
        purity = (round(stats.source_distribution.get(stats.source_class_preliminary, 0)
                        / sum(stats.source_distribution.values()), 4) if stats.source_distribution else 1.0)
        review_reasons = get_review_reasons(stats, fallback_share=fallback_share, purity=purity,
                                            flagged_terms=flagged_terms)
        rows.append({
            "cluster_id": cluster_id, "signal_id": stats.rule.signal_source,
            "signal_source": stats.rule.signal_source, "signal_name": stats.rule.signal_name,
            "report_topic_label": stats.rule.theme_name, "theme_id": stats.rule.theme_id,
            "theme_name": stats.rule.theme_name, "trend_role": stats.rule.trend_role,
            "trend_start_date": stats.rule.trend_start_date, "main_narrative": stats.rule.main_narrative,
            "narrative_id": stats.rule.sub_narrative_id, "narrative_name": stats.rule.sub_narrative,
            "provisional_sub_narrative": stats.rule.sub_narrative,
            "provisional_sub_narrative_id": stats.rule.sub_narrative_id,
            "week_start": stats.week_start, "week_end": stats.week_end,
            "cluster_records": stats.record_count, "eligible_records": eligible,
            "duplicate_records": stats.duplicate_count,
            "source_class_preliminary": stats.source_class_preliminary, "source_bucket": stats.source_bucket,
            "reaction_type_hint": stats.reaction_type_hint, "attitude_hint": stats.attitude_hint,
            "cluster_cue": stats.cluster_cue, "match_signature": stats.match_signature,
            "exception_candidate_count": exception_count,
            "source_distribution": _serialize_distribution(stats.source_distribution),
            "reaction_distribution": _serialize_distribution(stats.reaction_distribution),
            "attitude_distribution": _serialize_distribution(stats.attitude_distribution),
            "language_distribution": _serialize_distribution(stats.language_distribution),
            "fallback_share": fallback_share, "preliminary_consistency": consistency,
            "requires_review": bool(review_reasons), "review_reasons": "|".join(review_reasons),
            "cluster_cap_hit": stats.cluster_cap_hit,
            "representative_diversity": stats.representative_diversity
            if stats.representative_diversity is not None else "",
            "semantic_coherence_status": stats.semantic_coherence_status,
            "recursive_split_applied": stats.recursive_split_applied,
            "recursive_split_depth": stats.recursive_split_depth,
            "parent_cluster_id": stats.parent_cluster_id,
            "split_reason": stats.split_reason,
            "heterogeneous_cluster": stats.heterogeneous_cluster,
            "source_class_purity": purity,
        })
    return rows


def build_cluster_representative_rows(clusters: dict) -> list:
    rows = []
    for cluster_id in sorted(clusters.keys()):
        stats = clusters[cluster_id]
        for i, rep in enumerate(stats.representatives, start=1):
            rows.append({
                "cluster_id": cluster_id, "signal_id": stats.rule.signal_source,
                "report_topic_label": stats.rule.theme_name, "narrative_id": stats.rule.sub_narrative_id,
                "narrative_name": stats.rule.sub_narrative, "representative_no": i, **rep,
            })
    return rows


def get_exception_reasons(rep: dict) -> list:
    """exception候補になった理由を、is_exception判定（attitude mixed/unknown・複数reaction・
    fallback）と同じ条件で個別に返す（2026-10-03、Preprocess潜在不具合監査 #6）。
    従来は理由を1つの定数文字列`mixed_or_multiple_reaction_or_fallback`にまとめていたため、
    fallback（Masterに未マッチ）・mixed反応・attitude unknown・本文欠損の区別がつかなかった。
    US fixtureではexception 48,175件の約76%がattitude=unknown（反応語/極性語が無いだけ）だった。"""
    reasons = []
    if rep.get("match_status") != "matched":
        reasons.append("fallback_match")
    attitude = rep.get("attitude_hint")
    if attitude == "mixed":
        reasons.append("mixed_attitude")
    elif attitude == "unknown":
        reasons.append("missing_text" if not rep.get("text_for_review") else "unknown_attitude")
    if "|" in (rep.get("reaction_type_hint") or ""):
        reasons.append("multiple_reaction")
    return reasons


def build_exception_review_rows(sentiment_clusters: dict) -> list:
    rows = []
    for cluster_id in sorted(sentiment_clusters.keys()):
        stats = sentiment_clusters[cluster_id]
        for rep in stats.exception_representatives:
            rows.append({
                "cluster_id": cluster_id, "record_id": rep["record_id"],
                "text_for_review": rep["text_for_review"], "url": rep["url"],
                "source_class_preliminary": rep["source_class_preliminary"],
                "reaction_type_hint": rep["reaction_type_hint"], "attitude_hint": rep["attitude_hint"],
                "match_status": rep["match_status"],
                "review_reason": "|".join(get_exception_reasons(rep)) or "unspecified",
                "review_text_status": rep.get("review_text_status", ""),
            })
    return rows


# ---------------------------------------------------------------------------
# Discovery Mode（2026-09-16追加、#4）: discovery_mode=trueのSignal（例: S6）は
# 既知Narrativeへのkeyword matchingをPrimary routingに使わない。Preprocess側の
# 役割は「dedup / coarse clustering / noise reduction」までに限定し（ユーザー
# 確認済み）、topic_label/topic_summary/existing_signal_overlap/materiality等の
# 意味判断はPrompt1 Evidence Intelligence（evidence_intelligence_v1）に委ねる
# ——Preprocessだけで最終結論を確定しない、という既存のShell/AI責務分離
# （Plan H章）をDiscovery Modeでも維持する。
# ---------------------------------------------------------------------------

DISCOVERY_MIN_SIGNIFICANT_TOKENS = 2  # これ未満の有意トークン数の投稿はnoiseとして除外

DISCOVERY_CANDIDATE_COLUMNS = [
    "discovery_cluster_id", "signal_id", "cluster_size", "dominant_tokens", "source_types",
    "topic_label", "topic_summary", "key_actors", "existing_signal_overlap", "linked_signal_ids",
    "why_new_or_different", "potential_H1_H2_H3_effect", "business_relevance", "materiality", "confidence",
]

DISCOVERY_CANDIDATE_REPRESENTATIVE_COLUMNS = [
    "discovery_cluster_id", "representative_no", "record_id", "title", "text_for_review", "source_name",
    "domain", "url", "published_at", "language_group", "author", "documentTypeName", "source_bucket",
    "source_class_preliminary", "reaction_type_hint", "attitude_hint", "matched_terms", "match_status",
    "source_file",
]


def build_discovery_candidates(clusters: dict, discovery_signal_ids: set) -> tuple:
    """discovery_mode=trueのSignalに属するClusterのall_membersをプールし、
    (1)正規化テキストhashによる追加dedup、(2)有意トークン数が少ない投稿を
    noiseとして除外、(3)頻出トークンによる粗いsignatureでのcoarse clustering、
    を行う。'意味的に新規論点かどうか'は判定しない（topic_label/existing_signal_
    overlap/materiality等はPrompt1が埋める前提で空欄のまま返す）。

    戻り値: (discovery_candidates rows, discovery_candidate_representatives rows,
             audit dict)。discovery_signal_idsが空、またはコーパスが0件の場合は
    ([], [], {}) を返す——emerging_candidate=0も正常な結果として扱う
    （ユーザー要望4-5）。"""
    if not discovery_signal_ids:
        return [], [], {}

    pooled = []
    for stats in clusters.values():
        if stats.rule.signal_source in discovery_signal_ids:
            for m in stats.all_members:
                pooled.append((stats.rule.signal_source, m))

    corpus_size = len(pooled)
    if corpus_size == 0:
        return [], [], {
            "discovery_corpus_size": 0, "deduped_corpus_size": 0, "coarse_cluster_count": 0,
            "noise_or_irrelevant_count": 0, "representative_coverage": 0.0,
        }

    seen_text_hashes: set = set()
    deduped = []
    for signal_id, m in pooled:
        key = get_hash(normalize_text(f"{m.get('title', '')} {m.get('text_for_review', '')}"))
        if key in seen_text_hashes:
            continue
        seen_text_hashes.add(key)
        deduped.append((signal_id, m))

    noise_count = 0
    signal_members = []
    for signal_id, m in deduped:
        tokens = semantic.tokenize(f"{m.get('title', '')} {m.get('text_for_review', '')}")
        if len(tokens) < DISCOVERY_MIN_SIGNIFICANT_TOKENS:
            noise_count += 1
        else:
            signal_members.append((signal_id, m))

    groups: dict = {}
    for signal_id, m in signal_members:
        sig = semantic.salient_tokens(f"{m.get('title', '')} {m.get('text_for_review', '')}", top_k=2)
        key = "+".join(sig) if sig else "(no_signature)"
        groups.setdefault(key, []).append((signal_id, m))

    candidate_rows = []
    representative_rows = []
    total_reps = 0
    for signature, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        members = [m for _, m in items]
        signal_ids_here = sorted({sid for sid, _ in items})
        discovery_cluster_id = f"DISC_{get_hash(signature + '|' + '|'.join(signal_ids_here))[:14]}"
        source_types = _serialize_distribution(Counter(m.get("source_bucket", "") for m in members))
        rep_limit = min(len(members), DEFAULT_REPRESENTATIVE_COUNT)
        reps = members[:rep_limit]
        total_reps += len(reps)
        candidate_rows.append({
            "discovery_cluster_id": discovery_cluster_id, "signal_id": "|".join(signal_ids_here),
            "cluster_size": len(members), "dominant_tokens": signature, "source_types": source_types,
            # 以下はPrompt1 Evidence Intelligenceが埋める意味判断フィールド
            # （Preprocessでは確定しない、ユーザー確認済み）。
            "topic_label": "", "topic_summary": "", "key_actors": "", "existing_signal_overlap": "",
            "linked_signal_ids": "", "why_new_or_different": "", "potential_H1_H2_H3_effect": "",
            "business_relevance": "", "materiality": "", "confidence": "",
        })
        for j, rep in enumerate(reps, start=1):
            representative_rows.append({
                "discovery_cluster_id": discovery_cluster_id, "representative_no": j, **rep,
            })

    audit = {
        "discovery_corpus_size": corpus_size, "deduped_corpus_size": len(deduped),
        "coarse_cluster_count": len(groups), "noise_or_irrelevant_count": noise_count,
        "representative_coverage": round(total_reps / len(deduped), 4) if deduped else 0.0,
    }
    return candidate_rows, representative_rows, audit


def rows_to_csv_text(rows: list, columns: list) -> str:
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(escape_csv(row.get(col, "")) for col in columns))
    return "\r\n".join(lines) + "\r\n"


def _get_domain(url: str) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# run_preprocess本体（run.ps1の1パスストリーミング処理の忠実な移植）
# ---------------------------------------------------------------------------

# record_cluster_map_*.csv（Prompt 2 base input）の1ファイルあたり行数。cluster_manifest.csvの
# record_map_chunks（従来は常に1が書かれていた）とExport時のchunk分割・自動検証で同じ値を使う。
RECORD_MAP_CHUNK_SIZE = 50000

RECORD_MAP_COLUMNS = [
    "record_id", "timestamp", "date_key", "week_start", "week_end", "signal_source", "theme_id", "theme_name",
    "trend_role", "trend_start_date", "source_file", "source_bucket", "source_class_preliminary",
    "reaction_type_hint", "attitude_hint", "narrative_id", "narrative_name", "match_status", "matched_terms",
    "cluster_cue", "match_signature", "analysis_cluster_id", "sentiment_cluster_id", "is_duplicate",
    "duplicate_group_id", "url_hash", "text_hash",
]


@dataclass
class FileAuditRow:
    source_file: str
    detected_signal: str
    encoding: str
    status: str
    rows: int
    elapsed_seconds: float
    records_per_second: float
    error: str


@dataclass
class PreprocessResult:
    analysis_clusters: dict
    sentiment_clusters: dict
    record_map_rows: list
    file_audit: list
    total_records: int
    nonduplicate_records: int
    signal_total: dict
    signal_nonduplicate: dict
    signal_matched: dict
    signal_fallback: dict
    weekly_counts: dict
    population_narrative: dict
    population_date: dict
    population_language: dict
    source_bucket_counts: dict
    coverage: dict
    common_primary_graph_start: Optional[str]
    input_files: list
    # Discovery Mode（2026-09-16追加、#4）: discovery_mode=trueのSignal
    # （例: S6）について、keyword matchingをPrimary routingに使わない粗い
    # dedup/coarse clustering/noise reductionの結果。空リストなら
    # discovery-mode Signalが無い、またはコーパスが0件だったことを示す
    # （ユーザー要望4-5: emerging_candidate=0も正常な結果として扱う）。
    discovery_candidates: list = field(default_factory=list)
    discovery_candidate_representatives: list = field(default_factory=list)
    discovery_audit: dict = field(default_factory=dict)
    # 2026-10-04: Narrative master lint（実データの語彙・ヒット率・言語分布を使った検査）の結果と、
    # keyword_collisionとして扱う語の集合 {(signal, narrative_id|"", term小文字)}。
    master_lint: list = field(default_factory=list)
    flagged_terms: set = field(default_factory=set)
    # 2026-10-05: 分析期間による絞り込み。期間外の行は集計に入れず読み飛ばし、件数だけ数える。
    period_start: str = ""
    period_end: str = ""
    # 履歴の基準開始日（Narrative Masterのprimary Signalのtrend_start_dateのうち最も遅い日）。観測期間とは別概念で、
    # common_primary_graph_start（実際にグラフへ使える開始日）はこれと観測期間・実データの最古日で制限される。
    historical_trend_start: str = ""
    out_of_period_total: int = 0
    out_of_period_by_signal: dict = field(default_factory=dict)


# keyword_collision（要レビュー理由）の対象とするlint issue。再現率の指摘(inflection_not_covered)や
# 回避済みの衝突(substring_collision)は含めない。
KEYWORD_COLLISION_ISSUES = {
    "high_hit_rate", "generic_word", "include_inside_required", "include_equals_required",
    "self_firing_names_only", "prefix_collision", "short_keyword", "include_contains_exclude",
}
_WORD_RE = re.compile(r"[a-z0-9]+")
VOCABULARY_SAMPLE_EVERY = 7        # lint用の語彙は7件に1件を標本にする（コストを抑える）


def flagged_terms_from_findings(findings: list) -> set:
    flagged = set()
    for f in findings:
        if f["code"] in KEYWORD_COLLISION_ISSUES and f["severity"] in (master_lint.SEVERITY_WARNING,
                                                                       master_lint.SEVERITY_ERROR):
            for term in f["term"].split("|"):
                if term:
                    flagged.add((f["signal_source"], f["narrative_id"], term.lower()))
    return flagged


def build_master_lint_rows(result: "PreprocessResult") -> list:
    """`narrative_master_lint.csv`の行（signal_id, narrative_id, term, term_type, issue_type, collision_example,
    severity, recommended_action）。"""
    return master_lint.findings_to_csv_rows(result.master_lint)


def run_preprocess(file_paths: list, master_rows: list, *,
                    default_max_cluster_records: int = DEFAULT_MAX_CLUSTER_RECORDS,
                    default_representative_count: int = DEFAULT_REPRESENTATIVE_COUNT,
                    on_file_processed=None,
                    enable_recursive_split: bool = True,
                    coherence_backend: str = COHERENCE_BACKEND,
                    discovery_signal_ids: Optional[set] = None,
                    lint_master: bool = True,
                    period_start: Optional[str] = None,
                    period_end: Optional[str] = None) -> PreprocessResult:
    """run.ps1（build 2026-08-26-v4.4.0-generic-public-voice）の1パスストリーミング処理
    の忠実な移植。Analysis Cluster(AC_*)とSentiment Cluster(SC_*)の二系統を同時に構築する
    （キーの違いはget_sharded_cluster_id呼び出し側のbase_key構成、Phase A監査結果1参照）。

    on_file_processed: 指定時、ファイル1件処理完了ごとに
    on_file_processed(file_name, files_done, files_total) を呼ぶ（進捗可視化用。呼び出し元が
    DBのgeo_report_executions.progressへ書き込む——本モジュール自体はDBに依存しない）。
    UNMAPPEDなSignalは行単位でスキップせず、Unmappedな仮Ruleで処理を継続する（run.ps1と
    同じ——ファイル単位のpreflightで全体を止めない）。

    enable_recursive_split（2026-09-16追加、#3）: cap-hit Clusterのheterogeneity検知・
    recursive splitを行うかどうかのfeature flag（既定True、既存public_voice_monitor
    プロジェクトも含めた共通基盤としてよい、というユーザー確認済み。段階導入したい
    場合はFalseにして無効化できる）。coherence_backendは現状'deterministic'のみ
    （geo_report_cluster_semantic_service参照、embedding backendは未実装）。

    discovery_signal_ids（2026-09-16追加、#4）: discovery_mode=trueのSignal
    （例: S6）のsignal_idの集合。指定されたSignalはPreprocessResult.discovery_
    candidatesへ粗いdedup/coarse clustering結果を出力する（意味判断はしない）。"""
    rules_by_signal, fallback_by_signal, signal_meta = build_master_rules(
        master_rows, default_max_cluster_records=default_max_cluster_records,
        default_representative_count=default_representative_count)
    if not rules_by_signal:
        raise ValueError("Narrative Masterに有効な(active=true)Signalがありません。")

    # 分析期間（YYYY-MM-DD、両端を含む）。date_keyが期間外の行は集計対象外にする。日付が解釈できない行は
    # 期間を判定できないため従来どおり残す（date_key=unknownとして扱われ、unknown_date_recordsで警告される）。
    period_lo = clean_value(period_start)[:10] if period_start else ""
    period_hi = clean_value(period_end)[:10] if period_end else ""
    for bound in (period_lo, period_hi):
        if bound and parse_date_safe(bound) is None:
            raise ValueError(f"分析期間の日付が解釈できません: {bound}")
    if period_lo and period_hi and period_lo > period_hi:
        raise ValueError(f"分析期間の開始日({period_lo})が終了日({period_hi})より後です。")
    out_of_period_by_signal: dict = {}
    rows_seen = 0                    # 期間外を含む読み込み行数（record_id代替ハッシュ用。期間指定なしならtotal_recordsと同じ）
    seen_urls: set = set()
    seen_texts: set = set()
    analysis_base_counts: dict = {}
    sentiment_base_counts: dict = {}
    analysis_clusters: dict = {}
    sentiment_clusters: dict = {}
    weekly_counts: dict = {}
    population_narrative: dict = {}
    population_date: dict = {}
    population_language: dict = {}
    source_bucket_counts: dict = {}
    coverage: dict = {}
    signal_total: dict = {}
    signal_nonduplicate: dict = {}
    signal_matched: dict = {}
    signal_fallback: dict = {}
    total_records = 0
    nonduplicate_records = 0
    record_map_rows: list = []
    file_audit: list = []
    vocabulary: Counter = Counter()
    term_hits: dict = defaultdict(Counter)
    language_by_signal: dict = defaultdict(Counter)

    for path in file_paths:
        file_name = os.path.basename(path)
        file_signal = detect_signal_from_file(file_name)
        encoding = detect_input_encoding(path)
        started = time.monotonic()
        rows_in_file = 0
        status, error_text = "loaded", ""
        try:
            with open(path, "r", encoding=encoding, newline="") as fh:
                reader = csv.DictReader(fh)
                headers = reader.fieldnames or []
                title_h = resolve_header_name(headers, TITLE_ALIASES)
                text_h = resolve_header_name(headers, TEXT_ALIASES)
                if not title_h and not text_h:
                    raise ValueError(f"title/textらしき列が見つかりません。headers={','.join(headers)}")
                signal_h = resolve_header_name(headers, SIGNAL_ALIASES)
                url_h = resolve_header_name(headers, URL_ALIASES)
                timestamp_h = resolve_header_name(headers, TIMESTAMP_ALIASES)
                record_id_h = resolve_header_name(headers, RECORD_ID_ALIASES)
                document_type_h = resolve_header_name(headers, DOCUMENT_TYPE_ALIASES)
                source_name_h = resolve_header_name(headers, SOURCE_NAME_ALIASES)
                author_h = resolve_header_name(headers, AUTHOR_ALIASES)
                language_h = resolve_header_name(headers, LANGUAGE_ALIASES)

                for row in reader:
                    rows_seen += 1
                    signal = clean_value(row.get(signal_h, "")).upper() if signal_h else ""
                    if not signal:
                        signal = file_signal
                    if signal not in rules_by_signal:
                        signal = "UNMAPPED"
                    if period_lo or period_hi:
                        early_date = parse_date_safe(clean_value(row.get(timestamp_h, "")) if timestamp_h else "")
                        if early_date:
                            early_key = early_date.strftime("%Y-%m-%d")
                            if (period_lo and early_key < period_lo) or (period_hi and early_key > period_hi):
                                _increment(out_of_period_by_signal, signal)
                                continue
                    total_records += 1
                    rows_in_file += 1
                    _increment(signal_total, signal)

                    title = clean_value(row.get(title_h, "")) if title_h else ""
                    text = clean_value(row.get(text_h, "")) if text_h else ""
                    url = clean_value(row.get(url_h, "")) if url_h else ""
                    timestamp = clean_value(row.get(timestamp_h, "")) if timestamp_h else ""
                    record_id = clean_value(row.get(record_id_h, "")) if record_id_h else ""
                    if not record_id:
                        record_id = get_hash(f"{file_name}|{rows_seen}|{url}|{timestamp}")
                    document_type = clean_value(row.get(document_type_h, "")) if document_type_h else ""
                    source_name = clean_value(row.get(source_name_h, "")) if source_name_h else ""
                    author = clean_value(row.get(author_h, "")) if author_h else ""
                    language = clean_value(row.get(language_h, "")) if language_h else ""

                    source_bucket = get_source_bucket(url, document_type, source_name)
                    review_text = get_review_text(title, text, source_bucket)
                    reaction_types = get_reaction_types(review_text)
                    primary_reaction = get_primary_reaction_type(reaction_types)
                    attitude = get_attitude_hint(review_text, reaction_types)
                    source_class = get_source_class_preliminary(source_bucket, review_text, document_type)

                    date_obj = parse_date_safe(timestamp)
                    if date_obj:
                        date_key = date_obj.strftime("%Y-%m-%d")
                        week_start_dt = get_week_start(date_obj)
                        week_start_text = week_start_dt.strftime("%Y-%m-%d")
                        week_end_text = (week_start_dt + timedelta(days=6)).strftime("%Y-%m-%d")
                    else:
                        date_key = week_start_text = week_end_text = "unknown"

                    url_dedup_key = get_dedup_url_key(url)
                    url_hash = get_hash(url_dedup_key) if url_dedup_key else ""
                    text_hash = get_hash(normalize_text(shorten(f"{title} {text}", 3000)))
                    if lint_master and total_records % VOCABULARY_SAMPLE_EVERY == 0:
                        vocabulary.update(set(_WORD_RE.findall(shorten(f"{title} {text}", 3000).lower())))

                    is_dup, dup_group = is_duplicate_record(
                        signal, source_bucket, url_hash, text_hash, seen_urls, seen_texts)
                    if not is_dup:
                        if source_bucket in _URL_DEDUP_BUCKETS and url_hash:
                            seen_urls.add(f"{signal}|{url_hash}")
                        if text_hash:
                            seen_texts.add(f"{signal}|{text_hash}")
                        nonduplicate_records += 1
                        _increment(signal_nonduplicate, signal)

                    match_status, matched_terms = "unmapped", ""
                    match_audit = {"matched_context_terms": "", "matched_exclude_terms": "", "match_method": "",
                                   "matched_span": ""}
                    if signal in rules_by_signal:
                        fallback = fallback_by_signal.get(signal) or rules_by_signal[signal][0]
                        match = match_master(rules_by_signal[signal], fallback, shorten(f"{title} {text}", 3000))
                        rule, match_status, matched_terms = match["rule"], match["match_status"], match["matched_terms"]
                        match_audit = {k: match[k] for k in match_audit}
                    else:
                        rule = Rule(
                            signal_source=signal, signal_name="Unmapped", theme_id="UNMAPPED",
                            theme_name="Unmapped", trend_role="none", trend_start_date="",
                            main_narrative="Unmapped", sub_narrative="Unmapped", sub_narrative_id="UNMAPPED-00",
                            stance_default="Unknown", max_cluster_records=default_max_cluster_records,
                            representative_count=default_representative_count)

                    match_signature = get_match_signature(matched_terms, match_status)
                    if not is_dup:
                        _increment(signal_matched if match_status == "matched" else signal_fallback, signal)
                        if lint_master:
                            language_by_signal[signal][language or "Unknown"] += 1
                            if match_status == "matched":
                                for matched_term in matched_terms.split("|"):
                                    term_hits[signal][matched_term.lower()] += 1

                    cue = get_cluster_cue(review_text, primary_reaction, rule.split_terms)
                    analysis_cluster_id = sentiment_cluster_id = ""

                    if not is_dup:
                        max_cluster = max(rule.max_cluster_records, 20)
                        rep_limit = max(rule.representative_count, 3)
                        analysis_base = "|".join([
                            signal, rule.sub_narrative_id, week_start_text, source_bucket, cue, language,
                            match_signature])
                        analysis_cluster_id = get_sharded_cluster_id(
                            "AC", analysis_base, max_cluster, analysis_base_counts)
                        sentiment_base = "|".join([
                            signal, rule.sub_narrative_id, week_start_text, source_bucket, source_class,
                            primary_reaction, attitude, cue, language, match_signature])
                        sentiment_cluster_id = get_sharded_cluster_id(
                            "SC", sentiment_base, max_cluster, sentiment_base_counts)

                        if analysis_cluster_id not in analysis_clusters:
                            analysis_clusters[analysis_cluster_id] = ClusterStats(
                                cluster_id=analysis_cluster_id, kind="analysis", rule=rule,
                                week_start=week_start_text, week_end=week_end_text,
                                source_class_preliminary=source_class, source_bucket=source_bucket,
                                reaction_type_hint=primary_reaction, attitude_hint=attitude, cluster_cue=cue,
                                match_signature=match_signature, representative_limit=rep_limit)
                        if sentiment_cluster_id not in sentiment_clusters:
                            sentiment_clusters[sentiment_cluster_id] = ClusterStats(
                                cluster_id=sentiment_cluster_id, kind="sentiment", rule=rule,
                                week_start=week_start_text, week_end=week_end_text,
                                source_class_preliminary=source_class, source_bucket=source_bucket,
                                reaction_type_hint=primary_reaction, attitude_hint=attitude, cluster_cue=cue,
                                match_signature=match_signature, representative_limit=rep_limit)

                        representative = {
                            "record_id": record_id, "title": shorten(title, 300),
                            "text_for_review": shorten(review_text, 500), "source_name": source_name,
                            "domain": _get_domain(url), "url": url, "published_at": timestamp,
                            "language_group": language, "author": author, "documentTypeName": document_type,
                            "source_bucket": source_bucket, "source_class_preliminary": source_class,
                            "reaction_type_hint": reaction_types, "attitude_hint": attitude,
                            "matched_terms": matched_terms, "match_status": match_status, "source_file": file_name,
                            "review_text_status": get_review_text_status(title, text, source_bucket, review_text),
                            **match_audit,
                        }
                        seed = get_hash(f"{record_id}|{sentiment_cluster_id}")
                        is_exception = (
                            attitude in ("mixed", "unknown") or "|" in reaction_types or match_status != "matched")
                        for stats in (analysis_clusters[analysis_cluster_id], sentiment_clusters[sentiment_cluster_id]):
                            stats.record_count += 1
                            if match_status == "matched":
                                stats.matched_count += 1
                            else:
                                stats.fallback_count += 1
                            _increment(stats.source_distribution, source_class)
                            _increment(stats.reaction_distribution, primary_reaction)
                            _increment(stats.attitude_distribution, attitude)
                            _increment(stats.language_distribution, language)
                            add_representative(stats, representative, seed, is_exception)
                            # get_sharded_cluster_idのシャーディングにより、1つの
                            # cluster_idが受け取るrecord数はmax_cluster_records以下に
                            # 自然に有界化されるため、reservoir samplingせず全件保持する
                            # （cap-hit判定・recursive splitの元データとして使う）。
                            stats.all_members.append(representative)

                        _increment(source_bucket_counts, f"{signal}|{source_bucket}|{source_class}")

                    if not is_dup and week_start_text != "unknown":
                        _increment(weekly_counts, "|".join([
                            week_start_text, week_end_text, signal, rule.theme_id, rule.theme_name,
                            rule.trend_role, source_class, attitude]))
                        _increment(population_narrative, "|".join([signal, rule.sub_narrative_id, rule.sub_narrative]))
                        _increment(population_date, "|".join([date_key, signal, rule.sub_narrative_id]))
                        _increment(population_language, "|".join([signal, rule.sub_narrative_id, language]))
                        coverage_key = "|".join([
                            signal, rule.theme_id, rule.theme_name, rule.trend_role, rule.trend_start_date])
                        entry = coverage.setdefault(coverage_key, {"count": 0, "min": None, "max": None})
                        entry["count"] += 1
                        if date_obj:
                            if entry["min"] is None or date_obj < entry["min"]:
                                entry["min"] = date_obj
                            if entry["max"] is None or date_obj > entry["max"]:
                                entry["max"] = date_obj

                    record_map_rows.append({
                        "record_id": record_id, "timestamp": timestamp, "date_key": date_key,
                        "week_start": week_start_text, "week_end": week_end_text, "signal_source": signal,
                        "theme_id": rule.theme_id, "theme_name": rule.theme_name, "trend_role": rule.trend_role,
                        "trend_start_date": rule.trend_start_date, "source_file": file_name,
                        "source_bucket": source_bucket, "source_class_preliminary": source_class,
                        "reaction_type_hint": reaction_types, "attitude_hint": attitude,
                        "narrative_id": rule.sub_narrative_id, "narrative_name": rule.sub_narrative,
                        "match_status": match_status, "matched_terms": matched_terms, "cluster_cue": cue,
                        "match_signature": match_signature, "analysis_cluster_id": analysis_cluster_id,
                        "sentiment_cluster_id": sentiment_cluster_id, "is_duplicate": is_dup,
                        "duplicate_group_id": dup_group, "url_hash": url_hash, "text_hash": text_hash,
                    })
        except Exception as exc:
            status, error_text = "error", str(exc)
        elapsed = max(time.monotonic() - started, 1e-9)
        file_audit.append(FileAuditRow(
            source_file=file_name, detected_signal=file_signal, encoding=encoding, status=status,
            rows=rows_in_file, elapsed_seconds=round(elapsed, 3),
            records_per_second=round(rows_in_file / elapsed, 1), error=error_text))
        if on_file_processed:
            on_file_processed(file_name, len(file_audit), len(file_paths))
        if status == "error":
            raise ValueError(f"{file_name} の読み込みに失敗しました: {error_text}")

    if total_records == 0:
        raise ValueError("レコードが1件も読み込めませんでした。"
                         + (f"（期間{period_lo}〜{period_hi}の外の行が{sum(out_of_period_by_signal.values())}件）"
                            if out_of_period_by_signal else ""))
    if nonduplicate_records == 0:
        raise ValueError("全レコードが重複または無効でした。")

    primary_starts = [
        parse_date_safe(rule.trend_start_date) for rule in signal_meta.values()
        if rule.trend_role == "primary" and rule.trend_start_date
    ]
    primary_starts = [d for d in primary_starts if d is not None]
    common_start = max(primary_starts).strftime("%Y-%m-%d") if primary_starts else None
    historical_trend_start = common_start or ""
    # グラフ開始日 = max(履歴の基準開始日, 観測期間の開始日)。さらに、primary Signalの実データの最古日より前には
    # できない（存在しない履歴をグラフ期間に含めない）。観測期間もマスターの開始日も無い場合は従来どおりNone。
    if common_start or period_lo:
        chart_start = max(d for d in (common_start, period_lo) if d)
        primary_mins = [e["min"] for k, e in coverage.items() if k.split("|", 4)[3] == "primary" and e["min"]]
        any_mins = primary_mins or [e["min"] for e in coverage.values() if e["min"]]
        if any_mins:
            earliest = min(any_mins).strftime("%Y-%m-%d")
            if chart_start < earliest:
                chart_start = earliest
        common_start = chart_start

    finalize_cluster_labels(analysis_clusters)
    finalize_cluster_labels(sentiment_clusters)

    master_lint_findings: list = []
    flagged_terms: set = set()
    if lint_master:
        master_lint_findings = master_lint.lint_narrative_master(
            master_rows, language_distribution=language_by_signal, term_hit_counts=term_hits,
            signal_totals=signal_nonduplicate, vocabulary=vocabulary)
        flagged_terms = flagged_terms_from_findings(master_lint_findings)

    # Recursive Split（2026-09-16追加、#3）: cap-hit + heterogeneousなClusterのみ
    # 分割する。分割されたClusterのrecord_map_rowsは、旧cluster_idのままだと
    # 分割後のanalysis_clusters/sentiment_clustersに存在しないIDを指してしまうため、
    # record_idベースでchild cluster_idへ付け替える（分割されなかったrecordの値は
    # 変更しない）。
    if enable_recursive_split:
        analysis_clusters_before = analysis_clusters
        sentiment_clusters_before = sentiment_clusters
        analysis_clusters = apply_recursive_split(analysis_clusters, coherence_backend=coherence_backend)
        sentiment_clusters = apply_recursive_split(sentiment_clusters, coherence_backend=coherence_backend)
        analysis_remap = _build_record_to_child_cluster_map(analysis_clusters_before, analysis_clusters)
        sentiment_remap = _build_record_to_child_cluster_map(sentiment_clusters_before, sentiment_clusters)
        if analysis_remap or sentiment_remap:
            for row in record_map_rows:
                rid = row.get("record_id")
                analysis_key = (row.get("analysis_cluster_id"), rid)
                sentiment_key = (row.get("sentiment_cluster_id"), rid)
                if analysis_key in analysis_remap:
                    row["analysis_cluster_id"] = analysis_remap[analysis_key]
                if sentiment_key in sentiment_remap:
                    row["sentiment_cluster_id"] = sentiment_remap[sentiment_key]

    # Discovery Mode（2026-09-16追加、#4）: discovery_mode=trueのSignalについて、
    # Analysis Cluster側のall_membersをプールしてdedup/coarse clustering/noise
    # reductionのみ行う（意味判断はPrompt1へ委ねる）。
    discovery_candidates, discovery_candidate_representatives, discovery_audit = (
        build_discovery_candidates(analysis_clusters, discovery_signal_ids or set()))

    return PreprocessResult(
        analysis_clusters=analysis_clusters, sentiment_clusters=sentiment_clusters,
        record_map_rows=record_map_rows, file_audit=file_audit,
        total_records=total_records, nonduplicate_records=nonduplicate_records,
        signal_total=signal_total, signal_nonduplicate=signal_nonduplicate,
        signal_matched=signal_matched, signal_fallback=signal_fallback,
        weekly_counts=weekly_counts, population_narrative=population_narrative,
        population_date=population_date, population_language=population_language,
        source_bucket_counts=source_bucket_counts, coverage=coverage,
        common_primary_graph_start=common_start,
        input_files=[os.path.basename(p) for p in file_paths],
        discovery_candidates=discovery_candidates,
        discovery_candidate_representatives=discovery_candidate_representatives,
        discovery_audit=discovery_audit, master_lint=master_lint_findings, flagged_terms=flagged_terms,
        period_start=period_lo, period_end=period_hi, historical_trend_start=historical_trend_start,
        out_of_period_total=sum(out_of_period_by_signal.values()),
        out_of_period_by_signal=dict(out_of_period_by_signal),
    )


# ---------------------------------------------------------------------------
# 監査・集計ファイル群（cluster_manifest.csv / preprocess_audit_summary.csv /
# source_bucket_summary.csv / monitoring_scope.csv / weekly_trend_raw_basis.csv /
# report_config.yaml、いずれもrun.ps1の出力そのままの形状）
# ---------------------------------------------------------------------------

def build_preprocess_audit_rows(result: PreprocessResult, discovery_signal_ids: Optional[set] = None) -> list:
    """discovery_signal_ids（2026-09-16追加、#4）: 指定Signalの行に
    discovery_mode=Trueを付与する。fallback_shareは従来通り算出するが、
    discovery_mode=trueの行はQA判定（Prompt1側）でfallback_share単独の
    失敗条件にしない、という運用上の注記をPrompt1指示文側で行う
    （このCSV自体はfallback_shareの算出方法を変えない、"discovery_modeの
    Signalであること"を自己記述的に示すのみ）。"""
    discovery_signal_ids = discovery_signal_ids or set()
    rows = []
    for signal in sorted(result.signal_total.keys()):
        total = result.signal_total[signal]
        nondup = result.signal_nonduplicate.get(signal, 0)
        matched = result.signal_matched.get(signal, 0)
        fallback = result.signal_fallback.get(signal, 0)
        fallback_share = round(fallback / nondup, 4) if nondup else 0
        rows.append({
            "signal_source": signal, "total_records": total, "nonduplicate_records": nondup,
            "duplicate_records": total - nondup, "matched_records": matched, "fallback_records": fallback,
            "fallback_share": fallback_share, "common_primary_graph_start": result.common_primary_graph_start or "",
            "discovery_mode": signal in discovery_signal_ids,
        })
    return rows


def build_source_bucket_summary_rows(result: PreprocessResult) -> list:
    rows = []
    for key in sorted(result.source_bucket_counts.keys()):
        signal, bucket, source_class = key.split("|", 2)
        rows.append({
            "signal_source": signal, "source_bucket": bucket, "source_class_preliminary": source_class,
            "nonduplicate_records": result.source_bucket_counts[key],
        })
    return rows


def resolve_effective_start(configured_start: str, run_period_start: Optional[str], actual_min: str) -> tuple:
    """分析開始日の優先順位（2026-10-03、Preprocess潜在不具合監査 #9）。
    (effective_start_date, effective_start_source) を返す。

      1. master_trend_start_date : Narrative Masterのtrend_start_date（Signal別の設定値、従来の
                                   configured_start_date列。最も具体的な設定なので最優先）
      2. run_period_start        : Run作成時に指定した収集期間の開始日（geo_report_collection_runs.period_start）
      3. actual_min_date         : 実データの最古日（設定が何も無い場合の最後の手段。設定値ではないため
                                   sourceに明示して区別する）

    Evidence Intelligenceのように master の trend_start_date が全て空の案件では、従来configured_start_dateが
    全Signal空のままPrompt 2へ渡っていた（Prompt 2 EI契約は同列をanalysis_start_dateとして参照する）。
    `configured_start_date`列自体の意味（設定値のみ）は変えず、別列で解決結果を示す。"""
    period_date = parse_date_safe(run_period_start) if run_period_start else None
    if period_date is not None:
        # 観測期間(Runの期間)がある場合（2026-10-05）: 設定開始日と観測期間の開始日の遅い方。観測期間より前の
        # 履歴は今回のRunのデータに含まれないため、マスターのtrend_start_date（履歴の基準）をそのまま
        # 有効開始日にしない（従来は7/1のままPrompt 2へ渡っていた）。
        configured_date = parse_date_safe(configured_start) if configured_start else None
        if configured_date is not None and configured_date > period_date:
            return configured_start, "master_trend_start_date"
        return clean_value(run_period_start)[:10], "run_period_start"
    if configured_start and parse_date_safe(configured_start) is not None:
        return configured_start, "master_trend_start_date"
    if actual_min:
        return actual_min, "actual_min_date"
    return "", ""


def build_monitoring_scope_rows(result: PreprocessResult, *, run_period_start: Optional[str] = None,
                                run_period_end: Optional[str] = None) -> list:
    """configured_start_dateは従来どおり『マスターの設定値』（履歴の基準開始日）で、観測期間とは別。
    2026-10-05: observation_start_date/observation_end_date/historical_trend_start_dateを末尾に追加した
    （既存の列名・順序は変えない）。"""
    rows = []
    for key in sorted(result.coverage.keys()):
        signal, theme_id, theme_name, trend_role, configured_start = key.split("|", 4)
        entry = result.coverage[key]
        actual_min = entry["min"].strftime("%Y-%m-%d") if entry["min"] else ""
        effective_start, effective_source = resolve_effective_start(configured_start, run_period_start, actual_min)
        rows.append({
            "signal_source": signal, "theme_id": theme_id, "theme_name": theme_name, "trend_role": trend_role,
            "configured_start_date": configured_start,
            "actual_min_date": actual_min,
            "actual_max_date": entry["max"].strftime("%Y-%m-%d") if entry["max"] else "",
            "nonduplicate_records": entry["count"],
            "common_primary_graph_start": result.common_primary_graph_start or "",
            "effective_start_date": effective_start, "effective_start_source": effective_source,
            "observation_start_date": clean_value(run_period_start)[:10] if run_period_start else "",
            "observation_end_date": clean_value(run_period_end)[:10] if run_period_end else "",
            "historical_trend_start_date": configured_start,
        })
    return rows


def build_weekly_trend_raw_basis_rows(result: PreprocessResult) -> list:
    rows = []
    for key in sorted(result.weekly_counts.keys()):
        week_start, week_end, signal, theme_id, theme_name, trend_role, source_class, attitude = key.split("|", 7)
        rows.append({
            "week_start": week_start, "week_end": week_end, "signal_source": signal, "theme_id": theme_id,
            "theme_name": theme_name, "trend_role": trend_role, "source_class_preliminary": source_class,
            "attitude_hint": attitude, "records": result.weekly_counts[key],
            "common_trend_start": result.common_primary_graph_start or "",
            "note": "Preliminary audit basis. Final trends require Prompt 1 cluster labels and record map join.",
        })
    return rows


def build_cluster_manifest(result: PreprocessResult, topic: str, *, created_at: str, record_map_chunks: int,
                             coherence_backend: str = COHERENCE_BACKEND) -> dict:
    """coherence_backend（2026-09-16追加）: Recursive Split（#3）で実際に使われた
    coherence判定backendをRun成果物へ記録する透明性のための項目。現状は
    'deterministic'固定（Azure OpenAIにembeddings用Deploymentが存在しないため）。
    将来embedding backendを追加した際、どのRunがどちらの方式で判定されたかを
    区別できるようにしておく。cap_hit/split件数もあわせて記録する。"""
    cap_hit_count = sum(1 for c in result.analysis_clusters.values() if c.cluster_cap_hit)
    split_applied_count = sum(1 for c in result.analysis_clusters.values() if c.recursive_split_applied)
    heterogeneous_count = sum(1 for c in result.analysis_clusters.values() if c.heterogeneous_cluster)
    return {
        "topic": topic, "created_at": created_at, "total_records": result.total_records,
        "nonduplicate_records": result.nonduplicate_records,
        "analysis_cluster_count": len(result.analysis_clusters),
        "sentiment_cluster_count": len(result.sentiment_clusters),
        "record_map_chunks": record_map_chunks,
        "common_primary_graph_start": result.common_primary_graph_start or "",
        "input_files": "|".join(result.input_files),
        "coherence_backend": coherence_backend,
        "keyword_matcher_version": matcher.MATCHER_VERSION,
        "analysis_cluster_cap_hit_count": cap_hit_count,
        "analysis_cluster_recursive_split_applied_count": split_applied_count,
        "analysis_cluster_heterogeneous_count": heterogeneous_count,
        "discovery_corpus_size": result.discovery_audit.get("discovery_corpus_size", 0),
        "discovery_coarse_cluster_count": result.discovery_audit.get("coarse_cluster_count", 0),
    }


ANALYSIS_CONTRACT_BY_MODE = {
    "public_voice_monitor": "public_voice_monitor_v4_3",
    "evidence_intelligence": "evidence_intelligence_v1",
}


def resolve_analysis_contract(analysis_mode: Optional[str], prompt1_contract_version: Optional[str] = None) -> str:
    """Runのanalysis_mode（Design→Run snapshot済みの列挙値）とPrompt1 contract versionから、
    パッケージに明記する`analysis_contract`文字列を決める（2026-10-03、Preprocess潜在不具合監査 #10）。
    例: public_voice_monitor + v4.3 -> public_voice_monitor_v4_3 / evidence_intelligence -> evidence_intelligence_v1。
    analysis_modeが不明なら空文字（既存Run・旧呼び出し元との互換のため、無理に推定しない）。"""
    mode = clean_value(analysis_mode)
    if not mode:
        return ""
    version = clean_value(prompt1_contract_version)
    if version.startswith(mode):
        return version
    if mode in ANALYSIS_CONTRACT_BY_MODE and (not version or mode == "evidence_intelligence"):
        return ANALYSIS_CONTRACT_BY_MODE[mode]
    return f"{mode}_{version.replace('.', '_')}"


def build_report_config_yaml(result: PreprocessResult, *, report_id: str, topic: str,
                              primary_graph_signals: list, final_graph_rule: str,
                              created_at: str, analysis_mode: Optional[str] = None,
                              analysis_contract: Optional[str] = None, run_config: Optional[dict] = None) -> str:
    """report_config.yaml相当のテキストを組み立てる。primary_graph_signals/
    final_graph_ruleはプロジェクトごとに設定可能にする（現行run.ps1は
    'S1,S7'を1案件専用にハードコードしているが、Report Studioでは
    Design（geo_report_run_config.config_json）から渡す、Phase A監査結果1参照）。

    analysis_mode/analysis_contract（2026-10-03追加、#10）: 従来、パッケージ内にはモードを示す
    構造化された値が無く、`report_title`文字列と`final_graph_rule`文面からしか判別できなかった
    （`analysis_policy`は全モード共通の固定値）。指定された場合のみ、既存キーの後ろへ追記する
    （既存consumerはキー名で読むため追記は互換。未指定なら従来と同一の出力）。"""
    lines = [
        f"report_id: {report_id}",
        f"report_title: {topic}",
        f"created_at: {created_at}",
        "analysis_policy: narrative_master_v4_final_streaming_cluster_review",
    ]
    if analysis_mode:
        lines.append(f"analysis_mode: {analysis_mode}")
    if analysis_contract:
        lines.append(f"analysis_contract: {analysis_contract}")
    # Report Type・Prompt/Template profile・日付の意味（2026-10-05）。Runに固定された値（run_config）を書き出す。
    for key in ("report_kind", "prompt1_profile", "prompt2_profile", "template_id", "template_hash",
                "observation_start_date", "observation_end_date", "historical_trend_start_date",
                "primary_graph_signals_source"):
        if run_config and run_config.get(key):
            lines.append(f"{key}: {run_config[key]}")
    if result.period_start or result.period_end:
        lines.append(f"preprocess_period_filter: {result.period_start or ''}..{result.period_end or ''}")
    lines.append(f"keyword_matcher: {matcher.MATCHER_VERSION}")
    lines += [
        f"common_primary_graph_start: {result.common_primary_graph_start or ''}",
        f"primary_chart_start: {result.common_primary_graph_start or ''}",
        f"primary_graph_signals: {','.join(primary_graph_signals)}",
        f"final_graph_rule: {final_graph_rule}",
        "signals:",
    ]
    for signal in sorted(result.signal_total.keys()):
        lines.append(f"  - {signal}")
    return "\n".join(lines) + "\n"


def build_population_narrative_by_signal_rows(result: PreprocessResult) -> list:
    return build_population_table(result.population_narrative, ["signal_source", "narrative_id", "narrative_name"])


def build_population_narrative_by_signal_date_rows(result: PreprocessResult) -> list:
    return build_population_table(result.population_date, ["date", "signal_source", "narrative_id"])


def build_population_narrative_by_signal_language_rows(result: PreprocessResult) -> list:
    return build_population_table(result.population_language, ["signal_source", "narrative_id", "language_group"])
