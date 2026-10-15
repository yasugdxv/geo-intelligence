"""Report Studio: Signal前処理エンジン（Shell Parity層、Phase1）

外部ワークフロー `BSprocessv3.0/run.ps1`（version
2026-08-31-v6.1.6-wide-master-singleton-array-fix）が担っていた決定的な前処理
（列エイリアス解決→重複排除→Narrative Master照合→クラスタリング→代表サンプリング→
population/audit集計）をPythonへ移植したもの。LLM呼び出しは一切含まない
（Evidence Classification/Prompt1相当は別サービスで扱う、本モジュールの責務外）。

Shell Parityの検証方法・既知差異は docs/report-studio/shell-parity.md を参照。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

try:
    csv.field_size_limit(2_147_483_647)
except OverflowError:
    csv.field_size_limit(2_147_483_647 // 2)


# ---------------------------------------------------------------------------
# 低レベルユーティリティ（run.ps1: Clean-Value/Shorten/Get-Hash/Normalize-Text等）
# ---------------------------------------------------------------------------

def clean_value(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def shorten(value, length: int) -> str:
    x = clean_value(value)
    return x if len(x) <= length else x[:length]


def join_short_text(title, text, max_length: int) -> str:
    if max_length <= 0:
        return ""
    t = clean_value(title)
    if len(t) >= max_length:
        return t[:max_length]
    remaining = max_length - len(t)
    if remaining <= 1:
        return t
    b = clean_value(text)
    take = min(len(b), remaining - 1)
    if take <= 0:
        return t
    if len(t) == 0:
        return b[:max_length]
    return f"{t} {b[:take]}"


def get_hash(text: str) -> str:
    if not text or not text.strip():
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_punct_symbol_or_space(ch: str) -> bool:
    if ch.isspace():
        return True
    category = unicodedata.category(ch)
    return category.startswith("P") or category.startswith("S")


def normalize_text(text) -> str:
    # run.ps1 Normalize-Text: URLを空白化 → \p{P}\p{S}\s を除去（dedup用の正規化キー）
    x = clean_value(text).lower()
    x = re.sub(r"https?://\S+", " ", x)
    return "".join(ch for ch in x if not _is_punct_symbol_or_space(ch))


def escape_csv(value) -> str:
    s = clean_value(value)
    if '"' in s:
        s = s.replace('"', '""')
    if any(c in s for c in (",", "\r", "\n", '"')):
        return f'"{s}"'
    return s


def split_phrases(value) -> list:
    text = clean_value(value)
    if not text:
        return []
    return [p.strip() for p in re.split(r"[;|]", text) if p.strip()]


def parse_bool(value, default: bool) -> bool:
    text = clean_value(value)
    if not text:
        return default
    x = text.lower()
    if x in ("true", "1", "yes", "y"):
        return True
    if x in ("false", "0", "no", "n"):
        return False
    return default


def parse_int(value, default: int) -> int:
    text = clean_value(value)
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def parse_date_safe(value):
    text = clean_value(value)
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


_SIGNAL_S_PREFIX_RE = re.compile(r"^S0*([0-9]{1,3})([A-Z]?)$")
_SIGNAL_NUMERIC_RE = re.compile(r"^0*([0-9]{1,3})([A-Z]?)$")


def normalize_signal_key(signal) -> str:
    x = clean_value(signal).upper()
    m = _SIGNAL_S_PREFIX_RE.match(x)
    if m:
        return f"S{int(m.group(1))}{m.group(2)}"
    m = _SIGNAL_NUMERIC_RE.match(x)
    if m:
        return f"S{int(m.group(1))}{m.group(2)}"
    return x


_SIGNAL_WITH_SUFFIX_RE = re.compile(r"(^|[^A-Z0-9])S0*([0-9]{1,3})([A-Z])(?=[^A-Z0-9]|$)", re.IGNORECASE)
_SIGNAL_NUMERIC_FILE_RE = re.compile(r"(^|[^A-Z0-9])S0*([0-9]{1,3})(?=[^0-9]|$)", re.IGNORECASE)
_SIGNAL_WORD_RE = re.compile(r"signal[_-]?0*([0-9]{1,3})(?=[^0-9]|$)", re.IGNORECASE)


def detect_signal_from_file(file_name: str) -> str:
    base = re.sub(r"\.[^.]+$", "", file_name)
    m = _SIGNAL_WITH_SUFFIX_RE.search(base)
    if m:
        return f"S{int(m.group(2))}{m.group(3).upper()}"
    m = _SIGNAL_NUMERIC_FILE_RE.search(base)
    if m:
        return f"S{int(m.group(2))}"
    m = _SIGNAL_WORD_RE.search(base)
    if m:
        return f"S{int(m.group(1))}"
    return "UNMAPPED"


def get_dedup_url_key(url) -> str:
    text = clean_value(url)
    if not text:
        return ""
    try:
        parsed = urlparse(text)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("not absolute")
        path = parsed.path.rstrip("/")
        query = f"?{parsed.query}" if parsed.query else ""
        if (not path or path in ("/home", "/index", "/index.html", "/search")) and not query:
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path.lower()}{query}"
    except Exception:
        x = text.strip().lower()
        if re.match(r"^https?://[^/]+/?$", x):
            return ""
        return re.sub(r"#.*$", "", x)


def get_source_bucket(url, document_type, source_name) -> str:
    u = f"{clean_value(url)} {clean_value(source_name)}".lower()
    d = clean_value(document_type).lower()

    if re.search(r"comment|social|tweet|post|instagram|facebook|reddit|tiktok|youtube", d):
        return "public_social"
    if re.search(r"forum|board", d):
        return "public_forum"
    if re.search(r"prtimes|businesswire|globenewswire|prnewswire|newswire", u) or re.search(r"press|release", d):
        return "pr_release"
    if re.search(r"\.go\.|\.gov\b|government|commission|parliament|senate|congress", u) or re.search(r"government|official", d):
        return "official"
    if re.search(r"news|article", d):
        return "media"
    if re.search(r"blog|wordpress", d):
        return "public_blog"
    if re.search(r"x\.com|twitter\.com|facebook\.com|instagram\.com|tiktok\.com|threads\.net|bsky\.app|reddit\.com|youtube\.com", u):
        return "public_social"
    if re.search(r"forum|bbs|board", u):
        return "public_forum"
    return "unknown"


_SOCIAL_MARKERS = (
    "===== begin shared post =====",
    "[quoted @",
    "----- attachment -----",
    "----- details -----",
)
_RT_PREFIX_RE = re.compile(r"^\s*RT\s+@[^:]+:\s*", re.IGNORECASE)


def get_review_text(title, text, source_bucket: str) -> str:
    body = clean_value(text)
    title_text = clean_value(title)

    if source_bucket == "public_social":
        own_text = body
        lower_own = own_text.lower()
        for marker in _SOCIAL_MARKERS:
            pos = lower_own.find(marker)
            if pos >= 0:
                own_text = own_text[:pos].strip()
                lower_own = own_text.lower()
        if _RT_PREFIX_RE.match(own_text):
            return ""
        return shorten(own_text, 1200)

    if len(body) > 0:
        return join_short_text(title_text, body, 1600)
    return shorten(title_text, 800)


def get_week_start(date: datetime) -> datetime:
    d = date.replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())


def get_time_bucket(date, granularity: str) -> str:
    if date is None:
        return "unknown"
    g = clean_value(granularity).lower()
    if g == "none":
        return "all"
    if g == "day":
        return date.strftime("%Y-%m-%d")
    if g == "month":
        return date.strftime("%Y-%m")
    return get_week_start(date).strftime("%Y-%m-%d")


def get_split_label(text: str, rules_raw: str) -> str:
    if not rules_raw or not rules_raw.strip():
        return "general"
    lower_text = text.lower()
    for rule_text in rules_raw.split(";"):
        x = rule_text.strip()
        if not x:
            continue
        parts = x.split("=", 1)
        if len(parts) != 2:
            continue
        label = parts[0].strip()
        for term_text in parts[1].split("~"):
            term = term_text.strip()
            if term and term.lower() in lower_text:
                return label
    return "general"


def get_match_signature(matched_terms: str, match_status: str) -> str:
    if match_status != "matched":
        return match_status if match_status and match_status.strip() else "fallback"
    terms = sorted({t for t in matched_terms.split("|") if t.strip()})[:3]
    if not terms:
        return "matched_general"
    return "+".join(terms)


def get_priority_weight(priority: str) -> int:
    p = clean_value(priority).lower()
    if p == "high":
        return 30
    if p == "medium":
        return 20
    return 10


def get_sharded_cluster_id(base_key: str, max_records: int, counts: dict) -> str:
    seen = counts.get(base_key, 0)
    shard = seen // max(max_records, 1) + 1
    counts[base_key] = seen + 1
    h = get_hash(f"{base_key}|shard={shard}")
    return f"CL-{h[:16].upper()}"


def increment_count(table: dict, key: str, amount: int = 1) -> None:
    table[key] = table.get(key, 0) + amount


def increment_nested(table: dict, key: str) -> None:
    k = key if key and key.strip() else "Unknown"
    table[k] = table.get(k, 0) + 1


def serialize_distribution(table: dict) -> str:
    return "|".join(f"{k}:{table[k]}" for k in sorted(table.keys()))


def detect_input_encoding(path: str) -> str:
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    if head[:2] == b"\xff\xfe":
        return "utf-16-le"
    if head[:2] == b"\xfe\xff":
        return "utf-16-be"
    with open(path, "rb") as fh:
        sample = fh.read(1_048_576)
    try:
        sample.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        # run.ps1のGet-InputEncodingは失敗時Windows既定ANSI("Default")を返す。
        # 本環境（日本語Windows）ではcp932(Shift_JIS)相当として扱う。
        return "cp932"


# ---------------------------------------------------------------------------
# Narrative Master（実データはWIDE v6.0形式、1行=1 Signal×Narrative）
# ---------------------------------------------------------------------------

TITLE_ALIASES = ["documentName", "title", "Title", "headline", "subject", "name"]
TEXT_ALIASES = ["documentText", "full_text", "fullText", "text", "Text", "content", "body", "snippet", "description", "message", "post"]
SIGNAL_ALIASES = ["signal_id", "signal_source", "signal", "Signal", "signalId", "signalSource"]
URL_ALIASES = ["sourceUrl", "url", "URL", "link", "documentUrl", "article_url", "permalink"]
TIMESTAMP_ALIASES = ["timestamp", "publishedAt", "published_at", "publishedDate", "publishDate", "date", "createdAt", "created_at", "postDate", "datetime"]
RECORD_ID_ALIASES = ["documentId", "document_id", "record_id", "recordId", "itemId", "postId", "articleId", "id", "ID", "guid"]
DOCUMENT_TYPE_ALIASES = ["documentTypeName", "document_type", "documentType", "sourceType", "platform", "type"]
SOURCE_NAME_ALIASES = ["sourceName", "publicationName", "publication", "source", "publisher", "media_name", "site", "channel"]
AUTHOR_ALIASES = ["authorName", "author", "userName", "username", "account_name", "account", "handle", "screenName"]
LANGUAGE_ALIASES = ["languageDisplayName", "languageId", "language", "lang", "contentLanguage"]
SOURCE_COUNTRY_ALIASES = ["sourceCountry", "locationCountry", "country", "countryName", "country_name", "authorPlaceName", "location", "sourceLocation"]
ENGAGEMENT_ALIASES = ["impact", "reach", "engagement", "impressions", "score", "interactions", "totalEngagement"]


def resolve_header_name(headers, aliases):
    lower_map = {h.lower(): h for h in headers if h}
    for alias in aliases:
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


@dataclass
class Rule:
    signal_id: str
    signal_name: str
    signal_order: int
    signal_role: str
    report_topic_label: str
    narrative_id: str
    narrative_name: str
    narrative_description: str
    include: list
    required: list
    exclude: list
    split_rules: str
    cluster_time_granularity: str
    report_priority: str
    report_section: str
    report_order: int
    continuity_role: str
    max_cluster_records: int
    representative_count: int
    is_fallback: bool
    active: bool


def load_narrative_master_csv(path: str) -> list:
    encoding = detect_input_encoding(path)
    with open(path, "r", encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


def build_rule_from_wide_row(row: dict, default_max_cluster_records: int, default_representative_count: int) -> Rule:
    signal = normalize_signal_key(row.get("signal_id", ""))
    narrative_id = clean_value(row.get("narrative_id", ""))
    signal_name = clean_value(row.get("signal_name_internal")) or signal
    return Rule(
        signal_id=signal,
        signal_name=signal_name,
        signal_order=parse_int(row.get("signal_order"), 999),
        signal_role=clean_value(row.get("signal_role")) or "Context",
        report_topic_label=clean_value(row.get("report_topic_label")) or signal_name,
        narrative_id=narrative_id,
        narrative_name=clean_value(row.get("narrative_name")) or narrative_id,
        narrative_description=clean_value(row.get("narrative_description")),
        include=split_phrases(row.get("include_keywords")),
        required=split_phrases(row.get("required_context")),
        exclude=split_phrases(row.get("exclude_keywords")),
        split_rules=clean_value(row.get("cluster_split_rules")),
        cluster_time_granularity=clean_value(row.get("cluster_time_granularity")) or "week",
        report_priority=clean_value(row.get("report_priority")) or "Medium",
        report_section=clean_value(row.get("report_section")) or "Key Findings",
        report_order=parse_int(row.get("report_order"), 999),
        continuity_role=clean_value(row.get("continuity_role")) or "Watch",
        max_cluster_records=parse_int(row.get("max_cluster_records"), default_max_cluster_records),
        representative_count=parse_int(row.get("representative_count"), default_representative_count),
        is_fallback=parse_bool(row.get("is_fallback"), False),
        active=parse_bool(row.get("active"), True),
    )


def _dimension_config_preflight_error(row: dict) -> str:
    """Report Studio Human Check指摘（Iran/Hormuz Calibration実データで発見）:
    dimension_config_json（Narrative固有の分類軸定義。geo_report_classification_
    service.get_dimension_configが読む列だが、Narrative Master「WIDE v6.0」の
    21列リストには含まれない暗黙の列）が不正なJSONの場合、以前はClassify実行時
    までエラーが顕在化せず（Classification Schemaが空になり、LLMが自発的に生成した
    分類軸がadditionalProperties違反になるという分かりにくいSchema検証エラーとして
    のみ発覚した）。Narrative Master投入時点（Classify実行より大幅に前）で、
    欠落しているNarrative IDを名指しして即座に失敗させる。

    欠落・空自体はもうエラーにしない——geo_report_classification_service.
    get_dimension_configがDEFAULT_DIMENSION_CONFIG（汎用の1分類軸）へ自動的に
    フォールバックするため、Babel Street等の外部ツール由来でこの列を持たない
    Narrative Masterもそのまま投入できる。壊れたJSON（列はあるが構文エラー）の
    場合のみ、投入者の入力ミスとして早期に検出する。"""
    raw = row.get("dimension_config_json")
    if not clean_value(raw):
        return ""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        return f"dimension_config_jsonが不正なJSONです: {exc}"
    if not isinstance(parsed, dict):
        return "dimension_config_jsonはオブジェクト（辞書）である必要があります"
    return ""


def build_master_rules(master_rows, *, default_max_cluster_records: int = 200, default_representative_count: int = 8):
    rules_by_signal: dict = {}
    fallback_by_signal: dict = {}
    narrative_ids_seen: set = set()
    dimension_config_errors: list = []

    for row in master_rows:
        if not parse_bool(row.get("active"), True):
            continue
        rule = build_rule_from_wide_row(row, default_max_cluster_records, default_representative_count)
        if rule.narrative_id in narrative_ids_seen:
            raise ValueError(f"Duplicate narrative_id: {rule.narrative_id}")
        narrative_ids_seen.add(rule.narrative_id)

        if not rule.is_fallback:
            error = _dimension_config_preflight_error(row)
            if error:
                dimension_config_errors.append(f"{rule.narrative_id}: {error}")

        rules_by_signal.setdefault(rule.signal_id, []).append(rule)
        if rule.is_fallback:
            if rule.signal_id in fallback_by_signal:
                raise ValueError(f"Multiple enabled fallbacks for {rule.signal_id}")
            fallback_by_signal[rule.signal_id] = rule

    if not rules_by_signal:
        raise ValueError("Narrative Master has no enabled Narrative rows")

    if dimension_config_errors:
        raise ValueError(
            "Narrative Masterのdimension_config_jsonが不足しているNarrativeがあります"
            "（Classification Schemaが空になりClassify実行時に分かりにくいエラーになるため、"
            "投入時点で停止します）: " + "; ".join(dimension_config_errors)
        )

    signal_alias: dict = {}
    for signal in rules_by_signal:
        if signal not in fallback_by_signal:
            raise ValueError(f"No enabled fallback for {signal}. Add one fallback Narrative.")
        signal_alias[signal] = signal
        signal_alias[normalize_signal_key(signal)] = signal

    return rules_by_signal, fallback_by_signal, signal_alias


def resolve_signal(raw: str, signal_alias: dict) -> str:
    x = clean_value(raw).upper()
    if x in signal_alias:
        return signal_alias[x]
    normalized = normalize_signal_key(x)
    return signal_alias.get(normalized, normalized)


def match_master(rules, fallback, text: str) -> dict:
    best = None
    best_score = -1
    best_hits = []
    tl = text.lower()

    for rule in rules:
        if (not rule.active) or rule.is_fallback:
            continue
        if any(term.lower() in tl for term in rule.exclude):
            continue
        if rule.required and not any(term.lower() in tl for term in rule.required):
            continue

        hits = []
        score = get_priority_weight(rule.report_priority)
        for term in rule.include:
            if term.lower() in tl:
                hits.append(term)
                score += 10 + min(len(term), 20)
        if not hits:
            continue
        if score > best_score:
            best, best_score, best_hits = rule, score, hits

    if best is None:
        return {"rule": fallback, "matched_terms": "", "match_status": "fallback"}
    return {"rule": best, "matched_terms": "|".join(best_hits), "match_status": "matched"}


# ---------------------------------------------------------------------------
# Cluster集計
# ---------------------------------------------------------------------------

@dataclass
class ClusterStats:
    cluster_id: str
    base_key: str
    signal_id: str
    signal_name: str
    signal_order: int
    signal_role: str
    report_topic_label: str
    narrative_id: str
    narrative_name: str
    narrative_description: str
    time_bucket: str
    cluster_time_granularity: str
    source_bucket: str
    split_label: str
    match_signature: str
    report_priority: str
    report_section: str
    report_order: int
    continuity_role: str
    representative_limit: int
    record_count: int = 0
    matched_count: int = 0
    fallback_count: int = 0
    record_ids: set = field(default_factory=set)
    representatives: list = field(default_factory=list)
    source_distribution: dict = field(default_factory=dict)
    language_distribution: dict = field(default_factory=dict)
    country_distribution: dict = field(default_factory=dict)
    document_type_distribution: dict = field(default_factory=dict)
    match_distribution: dict = field(default_factory=dict)


def add_representative(stats: ClusterStats, representative: dict, seed: str) -> None:
    lst = stats.representatives
    limit = stats.representative_limit
    if len(lst) < limit:
        lst.append(representative)
        return
    pick = int(seed[:8], 16) % max(stats.record_count, 1)
    if pick < limit:
        lst[pick] = representative


_UNMAPPED_RULE_TEMPLATE = dict(
    signal_name="Unmapped", signal_order=999, signal_role="Context", report_topic_label="Unmapped",
    narrative_name="Unmapped", narrative_description="", include=[], required=[], exclude=[],
    split_rules="", cluster_time_granularity="week", report_priority="Low", report_section="Unmapped",
    report_order=999, continuity_role="Watch", is_fallback=False, active=True,
)


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
    clusters: dict
    file_audit: list
    record_rows: list
    total_records: int
    in_scope_total: int
    nonduplicate_total: int
    signal_total: dict
    signal_scope: dict
    signal_nonduplicate: dict
    signal_matched: dict
    signal_fallback: dict
    population_signal: dict
    population_narrative: dict
    population_date: dict
    population_language: dict
    population_country: dict
    population_bucket: dict


def _basename(path: str) -> str:
    return os.path.basename(path)


def run_preprocess(
    file_paths,
    master_rows,
    *,
    start_date=None,
    end_date=None,
    default_max_cluster_records: int = 200,
    default_representative_count: int = 8,
) -> PreprocessResult:
    rules_by_signal, fallback_by_signal, signal_alias = build_master_rules(
        master_rows,
        default_max_cluster_records=default_max_cluster_records,
        default_representative_count=default_representative_count,
    )

    preflight_errors = []
    for path in file_paths:
        detected = resolve_signal(detect_signal_from_file(_basename(path)), signal_alias)
        if detected == "UNMAPPED" or detected not in rules_by_signal:
            preflight_errors.append(f"{_basename(path)} -> {detected}")
    if preflight_errors:
        raise ValueError(
            "Signal preflight failed. Fix filenames or Narrative Master Signal IDs before processing:\n - "
            + "\n - ".join(preflight_errors)
        )

    period_start = parse_date_safe(f"{start_date}T00:00:00Z") if start_date else None
    period_end = parse_date_safe(f"{end_date}T23:59:59Z") if end_date else None

    seen_urls: set = set()
    seen_texts: set = set()
    base_counts: dict = {}
    clusters: dict = {}
    file_audit = []
    record_rows = []

    total_records = 0
    in_scope_total = 0
    nonduplicate_total = 0
    signal_total: dict = {}
    signal_scope: dict = {}
    signal_nonduplicate: dict = {}
    signal_matched: dict = {}
    signal_fallback: dict = {}
    population_signal: dict = {}
    population_narrative: dict = {}
    population_date: dict = {}
    population_language: dict = {}
    population_country: dict = {}
    population_bucket: dict = {}

    for path in file_paths:
        file_name = _basename(path)
        file_signal = resolve_signal(detect_signal_from_file(file_name), signal_alias)
        encoding = detect_input_encoding(path)
        started = time.monotonic()
        rows_in_file = 0
        status = "loaded"
        error_text = ""
        try:
            with open(path, "r", encoding=encoding, newline="") as fh:
                reader = csv.DictReader(fh)
                headers = reader.fieldnames or []
                title_h = resolve_header_name(headers, TITLE_ALIASES)
                text_h = resolve_header_name(headers, TEXT_ALIASES)
                if not title_h and not text_h:
                    raise ValueError(f"No recognized title/text column. Headers={','.join(headers)}")
                signal_h = resolve_header_name(headers, SIGNAL_ALIASES)
                url_h = resolve_header_name(headers, URL_ALIASES)
                timestamp_h = resolve_header_name(headers, TIMESTAMP_ALIASES)
                record_id_h = resolve_header_name(headers, RECORD_ID_ALIASES)
                document_type_h = resolve_header_name(headers, DOCUMENT_TYPE_ALIASES)
                source_name_h = resolve_header_name(headers, SOURCE_NAME_ALIASES)
                author_h = resolve_header_name(headers, AUTHOR_ALIASES)
                language_h = resolve_header_name(headers, LANGUAGE_ALIASES)
                source_country_h = resolve_header_name(headers, SOURCE_COUNTRY_ALIASES)
                engagement_h = resolve_header_name(headers, ENGAGEMENT_ALIASES)

                for row in reader:
                    rows_in_file += 1
                    total_records += 1

                    raw_signal = clean_value(row.get(signal_h)) if signal_h else ""
                    signal = resolve_signal(raw_signal, signal_alias) if raw_signal else file_signal
                    increment_count(signal_total, signal)

                    title = clean_value(row.get(title_h)) if title_h else ""
                    text = clean_value(row.get(text_h)) if text_h else ""
                    url = clean_value(row.get(url_h)) if url_h else ""
                    timestamp = clean_value(row.get(timestamp_h)) if timestamp_h else ""
                    record_id = clean_value(row.get(record_id_h)) if record_id_h else ""
                    document_type = clean_value(row.get(document_type_h)) if document_type_h else ""
                    source_name = clean_value(row.get(source_name_h)) if source_name_h else ""
                    author = clean_value(row.get(author_h)) if author_h else ""
                    language = clean_value(row.get(language_h)) if language_h else ""
                    source_country = clean_value(row.get(source_country_h)) if source_country_h else ""
                    engagement_raw = clean_value(row.get(engagement_h)) if engagement_h else ""

                    if not language:
                        language = "Unknown"
                    if not source_country:
                        source_country = "Unknown"

                    date = parse_date_safe(timestamp)
                    date_key = date.strftime("%Y-%m-%d") if date else "unknown"

                    in_scope = True
                    if period_start is not None and (date is None or date < period_start):
                        in_scope = False
                    if period_end is not None and (date is None or date > period_end):
                        in_scope = False
                    if in_scope:
                        in_scope_total += 1
                        increment_count(signal_scope, signal)

                    if not record_id:
                        record_id = get_hash(f"{file_name}|{rows_in_file}|{url}|{timestamp}")[:24]

                    source_bucket = get_source_bucket(url, document_type, source_name)
                    review_text = get_review_text(title, text, source_bucket)
                    url_key = get_dedup_url_key(url)
                    url_hash = get_hash(url_key) if url_key else ""
                    analysis_text = join_short_text(title, text, 7000)
                    text_hash_input = analysis_text[:5000] if len(analysis_text) > 5000 else analysis_text
                    text_hash = get_hash(normalize_text(text_hash_input))

                    use_url_dedup = source_bucket in ("media", "official", "pr_release")
                    signal_url_key = f"{signal}|{url_hash}" if (use_url_dedup and url_hash) else ""
                    signal_text_key = f"{signal}|{text_hash}" if text_hash else ""

                    is_duplicate = False
                    duplicate_group = ""
                    if in_scope:
                        if signal_url_key and signal_url_key in seen_urls:
                            is_duplicate = True
                            duplicate_group = f"url:{url_hash[:16]}"
                        elif signal_text_key and signal_text_key in seen_texts:
                            is_duplicate = True
                            duplicate_group = f"text:{text_hash[:16]}"
                        if not is_duplicate:
                            if signal_url_key:
                                seen_urls.add(signal_url_key)
                            if signal_text_key:
                                seen_texts.add(signal_text_key)
                            nonduplicate_total += 1
                            increment_count(signal_nonduplicate, signal)

                    match_status = "unmapped"
                    matched_terms = ""
                    if signal in rules_by_signal:
                        result = match_master(rules_by_signal[signal], fallback_by_signal[signal], analysis_text)
                        rule = result["rule"]
                        match_status = result["match_status"]
                        matched_terms = result["matched_terms"]
                    else:
                        rule = Rule(
                            signal_id=signal,
                            narrative_id=f"{signal}-UNMAPPED",
                            max_cluster_records=default_max_cluster_records,
                            representative_count=default_representative_count,
                            **_UNMAPPED_RULE_TEMPLATE,
                        )

                    if in_scope and not is_duplicate:
                        if match_status == "matched":
                            increment_count(signal_matched, signal)
                        else:
                            increment_count(signal_fallback, signal)

                    split_label = get_split_label(review_text, rule.split_rules)
                    time_bucket = get_time_bucket(date, rule.cluster_time_granularity) if date else "unknown"
                    match_signature = get_match_signature(matched_terms, match_status)
                    cluster_id = ""

                    if in_scope and not is_duplicate:
                        base_key = "|".join([
                            signal, rule.narrative_id, time_bucket, source_bucket, split_label, language, match_signature,
                        ])
                        max_cluster = max(rule.max_cluster_records, 20)
                        cluster_id = get_sharded_cluster_id(base_key, max_cluster, base_counts)

                        if cluster_id not in clusters:
                            representative_limit = max(rule.representative_count, 3)
                            clusters[cluster_id] = ClusterStats(
                                cluster_id=cluster_id, base_key=base_key,
                                signal_id=rule.signal_id, signal_name=rule.signal_name,
                                signal_order=rule.signal_order, signal_role=rule.signal_role,
                                report_topic_label=rule.report_topic_label, narrative_id=rule.narrative_id,
                                narrative_name=rule.narrative_name, narrative_description=rule.narrative_description,
                                time_bucket=time_bucket, cluster_time_granularity=rule.cluster_time_granularity,
                                source_bucket=source_bucket, split_label=split_label, match_signature=match_signature,
                                report_priority=rule.report_priority, report_section=rule.report_section,
                                report_order=rule.report_order, continuity_role=rule.continuity_role,
                                representative_limit=representative_limit,
                            )

                        cluster = clusters[cluster_id]
                        cluster.record_count += 1
                        cluster.record_ids.add(record_id)
                        if match_status == "matched":
                            cluster.matched_count += 1
                        else:
                            cluster.fallback_count += 1

                        increment_nested(cluster.source_distribution, source_bucket)
                        increment_nested(cluster.language_distribution, language)
                        increment_nested(cluster.country_distribution, source_country)
                        increment_nested(cluster.document_type_distribution, document_type)
                        increment_nested(cluster.match_distribution, match_status)

                        representative = {
                            "record_id": record_id, "title": shorten(title, 400),
                            "text_for_review": shorten(review_text, 1400), "source_name": source_name,
                            "url": url, "published_at": timestamp, "language": language,
                            "source_country": source_country, "author": author, "document_type": document_type,
                            "source_bucket": source_bucket, "matched_terms": matched_terms,
                            "match_status": match_status, "split_label": split_label, "source_file": file_name,
                        }
                        add_representative(cluster, representative, get_hash(f"{record_id}|{cluster_id}"))

                        increment_count(population_signal, signal)
                        increment_count(population_narrative, f"{signal}|{rule.narrative_id}|{rule.narrative_name}")
                        increment_count(population_date, f"{date_key}|{signal}|{rule.narrative_id}")
                        increment_count(population_language, f"{signal}|{rule.narrative_id}|{language}")
                        increment_count(population_country, f"{signal}|{rule.narrative_id}|{source_country}")
                        increment_count(population_bucket, f"{signal}|{rule.narrative_id}|{source_bucket}")

                    record_rows.append({
                        "record_id": record_id, "timestamp": timestamp, "date_key": date_key,
                        "signal_id": signal, "signal_name_internal": rule.signal_name,
                        "report_topic_label": rule.report_topic_label, "narrative_id": rule.narrative_id,
                        "narrative_name": rule.narrative_name, "match_status": match_status,
                        "matched_terms": matched_terms, "split_label": split_label,
                        "cluster_time_granularity": rule.cluster_time_granularity, "time_bucket": time_bucket,
                        "cluster_id": cluster_id, "is_duplicate": is_duplicate,
                        "duplicate_group_id": duplicate_group, "period_in_scope": in_scope,
                        "source_file": file_name, "source_bucket": source_bucket, "source_name": source_name,
                        "author": author, "document_type": document_type, "language": language,
                        "source_country": source_country, "url": url, "engagement_raw": engagement_raw,
                        "url_hash": url_hash, "text_hash": text_hash,
                    })
        except Exception as exc:
            status = "failed"
            error_text = str(exc)

        elapsed = max(time.monotonic() - started, 0.001)
        file_audit.append(FileAuditRow(
            source_file=file_name, detected_signal=file_signal, encoding=encoding, status=status,
            rows=rows_in_file, elapsed_seconds=round(elapsed, 3),
            records_per_second=round(rows_in_file / elapsed, 1) if elapsed else 0.0, error=error_text,
        ))

    failed = [f for f in file_audit if f.status == "failed"]
    if failed:
        raise ValueError(
            "One or more input files failed. Partial output not published. "
            + " | ".join(f"{f.source_file}: {f.error}" for f in failed)
        )
    if total_records == 0:
        raise ValueError("No records read")
    if in_scope_total == 0:
        raise ValueError("No records in requested period")
    if nonduplicate_total == 0:
        raise ValueError("No nonduplicate records in period")

    return PreprocessResult(
        clusters=clusters, file_audit=file_audit, record_rows=record_rows,
        total_records=total_records, in_scope_total=in_scope_total, nonduplicate_total=nonduplicate_total,
        signal_total=signal_total, signal_scope=signal_scope, signal_nonduplicate=signal_nonduplicate,
        signal_matched=signal_matched, signal_fallback=signal_fallback,
        population_signal=population_signal, population_narrative=population_narrative,
        population_date=population_date, population_language=population_language,
        population_country=population_country, population_bucket=population_bucket,
    )


# ---------------------------------------------------------------------------
# 出力テーブル組み立て（cluster_labeling_input.csv / cluster_representatives.csv /
# preprocess_audit_summary.csv / population_by_signal_*.csv 相当）
# ---------------------------------------------------------------------------

def build_cluster_rows(clusters: dict) -> list:
    rows = []
    for cluster_id in sorted(clusters.keys()):
        c = clusters[cluster_id]
        matched_share = round(c.matched_count / c.record_count, 4) if c.record_count else 0
        fallback_share = round(c.fallback_count / c.record_count, 4) if c.record_count else 0
        if fallback_share > 0.25 or c.split_label == "general" or c.record_count > 100:
            consistency = "Low"
        elif fallback_share > 0 or c.record_count > 40:
            consistency = "Medium"
        else:
            consistency = "High"
        rows.append({
            "cluster_id": cluster_id, "base_key": c.base_key,
            "record_ids": sorted(c.record_ids),
            "signal_id": c.signal_id, "signal_name_internal": c.signal_name,
            "signal_order": c.signal_order, "signal_role": c.signal_role,
            "report_topic_label": c.report_topic_label, "narrative_id": c.narrative_id,
            "narrative_name": c.narrative_name, "narrative_description": c.narrative_description,
            "time_bucket": c.time_bucket, "cluster_time_granularity": c.cluster_time_granularity,
            "cluster_records": c.record_count, "source_bucket": c.source_bucket, "split_label": c.split_label,
            "match_signature": c.match_signature, "matched_share": matched_share, "fallback_share": fallback_share,
            "source_distribution": serialize_distribution(c.source_distribution),
            "language_distribution": serialize_distribution(c.language_distribution),
            "country_distribution": serialize_distribution(c.country_distribution),
            "document_type_distribution": serialize_distribution(c.document_type_distribution),
            "match_distribution": serialize_distribution(c.match_distribution),
            "report_priority": c.report_priority, "report_section": c.report_section,
            "report_order": c.report_order, "continuity_role": c.continuity_role,
            "preliminary_consistency": consistency, "requires_review": True,
        })
    return rows


def build_representative_rows(clusters: dict) -> list:
    rows = []
    for cluster_id in sorted(clusters.keys()):
        c = clusters[cluster_id]
        for i, rep in enumerate(c.representatives, start=1):
            rows.append({
                "cluster_id": cluster_id, "signal_id": c.signal_id, "narrative_id": c.narrative_id,
                "report_topic_label": c.report_topic_label, "representative_no": i,
                "record_id": rep["record_id"], "title": rep["title"], "text_for_review": rep["text_for_review"],
                "source_name": rep["source_name"], "source_url": rep["url"], "published_at": rep["published_at"],
                "language": rep["language"], "source_country": rep["source_country"],
                "author_or_account": rep["author"], "document_type": rep["document_type"],
                "source_bucket": rep["source_bucket"], "matched_terms": rep["matched_terms"],
                "match_status": rep["match_status"], "split_label": rep["split_label"],
                "source_file": rep["source_file"],
            })
    return rows


def build_preprocess_audit_rows(result: PreprocessResult, rules_by_signal: dict) -> list:
    signals = sorted(set(result.signal_total) | set(rules_by_signal))
    rows = []
    for signal in signals:
        total = result.signal_total.get(signal, 0)
        in_scope = result.signal_scope.get(signal, 0)
        nondup = result.signal_nonduplicate.get(signal, 0)
        matched = result.signal_matched.get(signal, 0)
        fallback = result.signal_fallback.get(signal, 0)
        if nondup > 0:
            fallback_share = round(fallback / nondup, 4)
            qa_status = "PASS"
        else:
            fallback_share = 0
            qa_status = "REVIEW REQUIRED"
        rows.append({
            "signal_id": signal, "total_records": total, "period_in_scope_records": in_scope,
            "nonduplicate_records": nondup, "duplicate_records": in_scope - nondup,
            "matched_records": matched, "fallback_records": fallback,
            "fallback_share": fallback_share, "qa_status": qa_status,
        })
    return rows


def build_population_table(table: dict, names: list) -> list:
    rows = []
    for key, count in table.items():
        parts = key.split("|", len(names) - 1)
        record = {}
        for i, name in enumerate(names):
            record[name] = parts[i] if i < len(parts) else ""
        record["records"] = count
        rows.append(record)
    return rows
