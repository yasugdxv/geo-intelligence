"""ユーザー向けMonitoring（/geo/monitoring-themes）: 選んだThemeを継続監視し、意味のある変化に気づく。

既存の開発者向けMonitoring（geo_themes + indicator）とは別物。Alertは単なるKeyword Hitではなく
「通常状態からの意味のある変化」を、Rule（決定的）で判定し、必ず reason（なぜAlertか）を持つ。

★ Data Freshness Guard（最重要）:
  「記事が来ていない」を「安定」「変化なし」と表示しない。状態は次の5つ:
    no_fresh_data   : データ取得が一定期間止まっている → 「データ更新なし / 状況判定不能」
    no_new_articles : データは更新されているが、このテーマに該当する新着が無い（=安定ではない）
    stable          : 新着記事があり、Alert対象の変化は検出されなかった
    watch           : 軽微な変化（low severity Alert）
    changed         : 重要な変化（medium/high Alert）
  no_fresh_dataのとき、Alert判定・Stable判定は行わない。

★ Coverage-aware Theme Freshness（GEO_DIRECT_COLLECTION_V1、geo_theme_coverage_service）:
  Theme Freshness（fresh / partial_freshness / no_fresh_data / unknown）はTheme Stateと別に持つ。
  広域メディア（GDELT）が止まっていても、Source SetにDirect Source（防衛省・Federal Register等）が
  あり最新なら partial_freshness とし、その範囲でAlert判定する。partialでは決して stable と断定せず、
  State=partial_coverage（確認範囲では新しい動きなし／テーマ全体の変化なしを意味しない）にする。

記事の取得元（Source Setのmember）:
  source_id一致（migration DB-3後）＋ 登録済みdomain一致 ＋ Theme Candidateの根拠signal ＋ keyword
  一致（keywords/actors）をunionする。重複はexternal_signal_idで除く。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

import geo_alert_validation as alert_validation
import geo_source_library_service as lib
import geo_source_set_service as set_service
import geo_theme_coverage_service as coverage_service

WINDOW_HOURS = 24 * 7          # 「新着」として扱う期間
SENSITIVITY_FACTOR = {"low": 3.0, "medium": 2.0, "high": 1.5}
MIN_SURGE_ARTICLES = {"low": 8, "medium": 5, "high": 3}

# 意味のある変化を示唆する語（Rule）。単独Hitではなく「複数Source or 一次情報」を要件にする。
_EVENT_PATTERNS = [
    ("sanction", "制裁・輸出規制", re.compile(r"sanction|export control|entity list|embargo|制裁|輸出規制|禁輸|エンティティ", re.I)),
    ("policy_change", "政策変更", re.compile(r"new rule|policy shift|announce[sd]? (?:a )?(?:new )?(?:policy|rule|tariff)|tariff|関税|新規制|政策転換|方針転換", re.I)),
    ("military_action", "軍事行動", re.compile(r"military drill|exercise|missile|blockade|strike|attack(?:s|ed)?|invasion|invad(?:e|es|ed|ing)|演習|ミサイル|封鎖|攻撃|侵攻", re.I)),
    ("negotiation", "交渉の開始/停止", re.compile(r"talks|negotiat|ceasefire|summit|walk(?:ed)? out|suspend(?:ed)? talks|協議|交渉|停戦|首脳会談|決裂", re.I)),
]
_SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _in_list(values) -> str:
    return "in.(" + ",".join(str(v) for v in values) + ")"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

_EDITABLE = ("display_name", "theme_candidate_id", "theme_id", "source_set_id", "regions", "actors",
             "tags", "keywords", "monitoring_criteria", "sensitivity", "frequency", "status")


def _clean(data: dict) -> dict:
    out = {k: data[k] for k in _EDITABLE if k in data}
    if "display_name" in out:
        out["display_name"] = (out["display_name"] or "").strip()
        if not out["display_name"]:
            raise lib.SourceValidationError("display_nameは必須です")
    if out.get("sensitivity") and out["sensitivity"] not in SENSITIVITY_FACTOR:
        raise lib.SourceValidationError("sensitivityはlow/medium/highのいずれかにしてください")
    if out.get("frequency") and out["frequency"] not in ("hourly", "daily", "weekly"):
        raise lib.SourceValidationError("frequencyはhourly/daily/weeklyのいずれかにしてください")
    if out.get("status") and out["status"] not in ("active", "paused", "archived"):
        raise lib.SourceValidationError("statusが不正です")
    for key in ("regions", "actors", "tags", "keywords"):
        if key in out:
            out[key] = [str(v).strip() for v in (out[key] or []) if str(v).strip()]
    return out


def create_monitoring_theme(client, data: dict, *, created_by: str | None) -> dict:
    payload = _clean(data)
    if not payload.get("display_name"):
        raise lib.SourceValidationError("display_nameは必須です")
    payload["created_by"] = created_by
    set_service.ensure_usable(client, payload.get("source_set_id"))
    # DB既定値と同じ値を明示する（既定値に依存せず、返却行がそのままUIで使える）
    payload.setdefault("sensitivity", "medium")
    payload.setdefault("frequency", "daily")
    payload.setdefault("status", "active")
    return client.insert("geo_monitoring_themes", [payload])[0]


def create_from_candidate(client, candidate: dict, *, source_ids: list | None = None,
                          source_set_id: str | None = None, created_by: str | None = None,
                          overrides: dict | None = None) -> dict:
    """Theme Explorerの候補から登録する。Theme + Source Set + Actors + Tags をそのまま引き継ぐ。
    source_idsが指定され、source_set_idが無い場合は新規Source Setを作る（Theme専用ではない共通Set）。"""
    if not source_set_id and source_ids:
        s = set_service.create_source_set(
            client, name=f"{candidate.get('title', 'Theme')} 用", source_ids=source_ids, created_by=created_by,
            description="Theme Explorerから作成")
        source_set_id = s["source_set_id"]
    data = {
        "display_name": candidate.get("title") or "Untitled Theme",
        "theme_candidate_id": candidate.get("candidate_id"),
        "source_set_id": source_set_id,
        "regions": candidate.get("regions") or [], "actors": candidate.get("actors") or [],
        "tags": candidate.get("themes") or [], "keywords": [],
        "monitoring_criteria": candidate.get("question"),
    }
    data.update(overrides or {})
    return create_monitoring_theme(client, data, created_by=created_by)


def update_monitoring_theme(client, monitoring_theme_id: str, data: dict) -> dict | None:
    if not get_monitoring_theme(client, monitoring_theme_id):
        return None
    payload = _clean(data)
    if payload.get("source_set_id"):
        current = get_monitoring_theme(client, monitoring_theme_id)
        if payload["source_set_id"] != (current or {}).get("source_set_id"):   # 変更時だけ検証（既存の紐付けは維持）
            set_service.ensure_usable(client, payload["source_set_id"])
    if payload:
        client.update("geo_monitoring_themes", {"monitoring_theme_id": f"eq.{monitoring_theme_id}"}, payload)
    return get_monitoring_theme(client, monitoring_theme_id)


def get_monitoring_theme(client, monitoring_theme_id: str) -> dict | None:
    rows = client.select("geo_monitoring_themes", {"select": "*", "monitoring_theme_id": f"eq.{monitoring_theme_id}"})
    return rows[0] if rows else None


def archive_monitoring_theme(client, monitoring_theme_id: str) -> dict | None:
    return update_monitoring_theme(client, monitoring_theme_id, {"status": "archived"})


def list_monitoring_themes(client, *, include_archived: bool = False) -> dict:
    params = {"select": "*", "order": "updated_at.desc", "limit": "200"}
    if not include_archived:
        params["status"] = "neq.archived"
    themes = client.select("geo_monitoring_themes", params)
    ids = [t["monitoring_theme_id"] for t in themes]
    unacked: dict = {}
    if ids:
        for a in client.select("geo_monitoring_alerts", {
                "select": "monitoring_theme_id", "monitoring_theme_id": _in_list(ids),
                "acknowledged_at": "is.null", "limit": "5000"}):
            unacked[a["monitoring_theme_id"]] = unacked.get(a["monitoring_theme_id"], 0) + 1
    freshness = lib.get_global_freshness(client)
    new_today = changed = 0
    for t in themes:
        a = t.get("latest_assessment") or {}
        t["unacknowledged_alerts"] = unacked.get(t["monitoring_theme_id"], 0)
        t["assessment_state"] = a.get("state")           # 未評価ならNone（UIは「未評価」）
        t["freshness_state"] = a.get("freshness_state")  # Coverage-aware（fresh/partial_freshness/no_fresh_data/unknown）
        t["coverage_note"] = a.get("coverage_note")
        new_today += a.get("new_articles_24h", 0) or 0
        changed += 1 if a.get("state") == "changed" else 0
    return {
        "themes": themes,
        "summary": {"monitoring_count": len([t for t in themes if t["status"] == "active"]),
                    "unacknowledged_alerts": sum(unacked.values()), "new_articles_today": new_today,
                    "important_changes": changed},
        "data_freshness": freshness,
    }


# ---------------------------------------------------------------------------
# 記事の収集（Source Set / domain / 根拠signal / keyword の union）
# ---------------------------------------------------------------------------

# agencies: Federal Register等の構造化メタデータ（機関名のlist）。keywordの一致判定に使う（本文の部分一致より優先）
_SIGNAL_COLS = ("external_signal_id,title,summary,url,source_id,source_name,source_domain,"
                "published_at,created_at,entities,provider,agencies:raw_metadata->agencies,baseline:raw_metadata->baseline")
DIRECT_PROVIDERS = ("rss", "official")

# ---- Keyword判定（部分一致の誤検知を防ぐ）----
# 種類: text=通常の語（部分一致）／acronym=英大文字2〜4字の略語（BIS, IRGC, ADIZ等）→**単語境界**で一致／
#       agency:<正式名>=Federal Register等の構造化された機関名に一致（本文の部分一致はしない）。
# 例: 'BIS'は "business" "Arabistan" "obisk" に一致させない。'PLA'は "Plans" "Plant" に一致させない。
_ACRONYM_RE = re.compile(r"^[A-Z]{2,4}$")
_AGENCY_PREFIX = "agency:"
# all:<語>;<候補1>|<候補2>|... ＝ 複合条件（AND）。例: `all:tanker;Hormuz|Persian Gulf|Iran` は「tanker」かつ地理語のいずれか。
# 「tanker」のような一般語を単独でThemeの関連にしないための書式（V1_2）。各グループはすべて満たす必要があり、グループ内はOR。
# 英字の語は単語境界（大文字小文字は区別しない）、日本語等は部分一致で判定する
_ALL_PREFIX = "all:"


def _kw_kind(kw: str) -> str:
    if kw.lower().startswith(_AGENCY_PREFIX):
        return "agency"
    if kw.lower().startswith(_ALL_PREFIX):
        return "all"
    return "acronym" if _ACRONYM_RE.fullmatch(kw) else "text"


def _alt_in(text: str, alt: str) -> bool:
    alt = alt.strip()
    if not alt:
        return False
    if alt.isascii():
        # 単語境界。英語の複数形（tanker→tankers）は同じ語として扱う（"Oman"が"woman"に一致しないことは維持）
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(alt)}(?:s|es)?(?![A-Za-z0-9])", text, re.I) is not None
    return alt.lower() in text.lower()


def _all_groups(kw: str) -> list:
    return [[a.strip() for a in g.split("|") if a.strip()] for g in kw[len(_ALL_PREFIX):].split(";") if g.strip()]


def _acronym_in(text: str, acronym: str) -> bool:
    """大文字の略語が、英数字に隣接しない独立した語として含まれるか（大文字小文字は区別する）。"""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(acronym)}(?![A-Za-z0-9])", text) is not None


def _agency_names(article: dict) -> list:
    a = article.get("agencies")
    return [str(x).strip().lower() for x in a] if isinstance(a, list) else []


def keyword_match_reason(keyword: str, article: dict) -> str | None:
    """1つのkeywordがarticleに一致する理由（一致しなければNone）。"""
    kw = (keyword or "").strip()
    if not kw:
        return None
    kind = _kw_kind(kw)
    agencies = _agency_names(article)
    if kind == "agency":
        return f"agency:{kw[len(_AGENCY_PREFIX):].strip()}" if kw[len(_AGENCY_PREFIX):].strip().lower() in agencies else None
    text = f"{article.get('title') or ''} {article.get('summary') or ''}"
    if kind == "all":
        groups = _all_groups(kw)
        if groups and all(any(_alt_in(text, a) for a in g) for g in groups):
            return "all:" + "+".join(next(a for a in g if _alt_in(text, a)) for g in groups)
        return None
    if kind == "acronym":
        if agencies:       # 構造化された機関メタデータがある記事（Federal Register等）は、略語の本文一致ではなくagency:で判定する
            return None
        return f"word:{kw}" if _acronym_in(text, kw) else None
    return f"text:{kw}" if kw.lower() in text.lower() else None


# ---- 海域の分類（Hormuz / Red Sea / Gulf of Oman / Persian Gulf / Wider Middle East）。DB変更なし。ruleはconfig/geo_direct_sources.yaml ----
def _maritime_rules() -> list:
    try:
        import geo_direct_collection_common as dc
        return dc.load_direct_config().get("maritime_regions") or []
    except Exception:  # noqa: BLE001
        return []


def classify_maritime_region(article: dict, rules: list | None = None) -> str | None:
    """記事（タイトル優先、次に要約）を海域に分類する。優先順位はruleの並び順。該当なしはNone。"""
    rules = rules if rules is not None else _maritime_rules()
    for text in (article.get("title") or "", article.get("summary") or ""):
        for r in rules:
            if any(_alt_in(text, a) for a in r["terms"]):
                return r["key"]
    return None


def _keyword_hit(theme: dict, article: dict) -> bool:
    return any(keyword_match_reason(k, article) for k in (theme.get("keywords") or []))


def _article_time(row: dict) -> datetime | None:
    return lib._parse_ts(row.get("published_at")) or lib._parse_ts(row.get("created_at"))


def collect_articles(client, theme: dict, *, since: datetime, limit: int = 300) -> dict:
    """テーマに関係する記事を重複なしで集める。戻り値: {"articles": [...], "sources": [...]}"""
    sources = set_service.resolve_set_sources(client, theme["source_set_id"]) if theme.get("source_set_id") else []
    live_sources = [s for s in sources if not s.get("archived_at")]
    since_iso = since.isoformat()
    found: dict = {}

    def add(rows, via):
        for r in rows:
            found.setdefault(r["external_signal_id"], {**r, "matched_via": via})

    if live_sources:
        ids = [s["source_id"] for s in live_sources]
        rows = lib._safe_select(client, "geo_external_signals", {
            "select": _SIGNAL_COLS, "source_id": _in_list(ids), "published_at": f"gte.{since_iso}",
            "order": "published_at.desc", "limit": str(limit)})
        add(rows, "source_id")
        domains = sorted({r["domain"] for r in lib._safe_select(client, "geo_source_domains", {
            "select": "domain", "source_id": _in_list(ids), "match_enabled": "eq.true", "limit": "1000"})})
        if domains:
            add(client.select("geo_external_signals", {
                "select": _SIGNAL_COLS, "source_domain": _in_list(domains), "published_at": f"gte.{since_iso}",
                "order": "published_at.desc", "limit": str(limit)}), "domain")
    if theme.get("theme_candidate_id"):
        cand = client.select("geo_theme_candidates", {
            "select": "source_external_signal_ids", "candidate_id": f"eq.{theme['theme_candidate_id']}"})
        sig_ids = [s for s in ((cand[0].get("source_external_signal_ids") if cand else None) or []) if s][:200]
        if sig_ids:
            add(client.select("geo_external_signals", {
                "select": _SIGNAL_COLS, "external_signal_id": _in_list(sig_ids),
                "published_at": f"gte.{since_iso}", "limit": "200"}), "candidate_evidence")
    for kw in (theme.get("keywords") or [])[:12]:
        kind = _kw_kind(kw)
        if kind == "agency":
            continue               # 機関名はSource Set経由で届いた記事に対して判定する（本文検索しない）
        term = (_all_groups(kw)[0][0] if kind == "all" and _all_groups(kw) else kw).replace(",", " ")     # 複合条件は最初の語でDBを絞り、Python側で確定
        rows = client.select("geo_external_signals", {
            "select": _SIGNAL_COLS, "or": f"(title.ilike.*{term}*,summary.ilike.*{term}*)",
            "published_at": f"gte.{since_iso}", "order": "published_at.desc", "limit": "300" if kind in ("acronym", "all") else "100"})
        if kind in ("acronym", "all"):      # DBのilikeは部分一致の上位集合。単語境界/複合条件でPython側が確定する
            rows = [r for r in rows if keyword_match_reason(kw, r)]
        add(rows, "keyword")

    # Direct Source（政府・国際機関のRSS/API）は、Source Setに入れただけで「全記事」がテーマの記事になると
    # 無関係な発表でAlertが出る。Source/domain経由で拾ったDirect記事は、テーマのkeywordに合うものだけを数える
    # （keyword未設定のテーマは従来どおり全件）。Direct Sourceの鮮度（取得が最新か）はこれとは独立に判定する。
    if theme.get("keywords"):
        found = {k: v for k, v in found.items()
                 if not (v.get("provider") in DIRECT_PROVIDERS and v.get("matched_via") in ("source_id", "domain")
                         and not _keyword_hit(theme, v))}
    articles = sorted(found.values(), key=lambda r: _article_time(r) or datetime.min.replace(tzinfo=timezone.utc),
                      reverse=True)
    return {"articles": articles[:limit], "sources": sources}


# ---------------------------------------------------------------------------
# Alert判定（Rule）と状態評価
# ---------------------------------------------------------------------------

def _fingerprint(theme_id: str, alert_type: str, key: str) -> str:
    return hashlib.sha256(f"{theme_id}:{alert_type}:{key}".encode()).hexdigest()[:24]


def detect_alerts(theme: dict, articles: list, primary_source_ids: set, *, now: datetime | None = None,
                  baseline_daily: float = 0.0, validator=None, validation_log: list | None = None) -> list:
    """Rule判定。各Alertは reason（なぜAlertか）と related_signal_ids を持つ。
    - volume_surge: 直近24h記事数が、過去平均×係数かつ最小件数を超えた
    - primary_announcement: 一次情報Sourceからの新着
    - event_*: 制裁/政策変更/軍事行動/交渉の語を含む記事が「2つ以上の独立Source」または一次情報Source由来。
      ★ 2段階: Candidate Detection（語Hit）→ Context Validation（geo_alert_validation）。否定・仮定・
      予測・過去・否認の記事は候補から除外し、「実際に起きた」と確認できた記事だけでAlertを判定する。
      validatorが無い場合はRule-onlyの検証（LLMなし）を使う。validation_logへ全判定を追記する。"""
    now = now or _now()
    validator = validator or alert_validation.AlertValidator(None, None, now=now)
    sens = theme.get("sensitivity") or "medium"
    day_ago = now - timedelta(hours=24)
    recent = [a for a in articles if (_article_time(a) or now - timedelta(days=99)) >= day_ago]
    alerts: list = []
    tid = theme["monitoring_theme_id"]
    day_key = now.strftime("%Y-%m-%d")

    threshold = max(MIN_SURGE_ARTICLES[sens], baseline_daily * SENSITIVITY_FACTOR[sens])
    if len(recent) >= threshold:
        alerts.append({
            "alert_type": "volume_surge", "severity": "medium" if len(recent) < threshold * 2 else "high",
            "title": f"記事量が急増（24時間で{len(recent)}件）",
            "reason": (f"直近24時間の関連記事が{len(recent)}件。通常（過去の1日平均{baseline_daily:.1f}件）に対し、"
                       f"感度「{sens}」の基準（{threshold:.1f}件）を超えたため。"),
            "related_signal_ids": [a["external_signal_id"] for a in recent[:20]],
            "fingerprint": _fingerprint(tid, "volume_surge", day_key)})

    primary_new = [a for a in recent if a.get("source_id") in primary_source_ids]
    if primary_new:
        names = sorted({a.get("source_name") or a.get("source_domain") or "" for a in primary_new} - {""})
        alerts.append({
            "alert_type": "primary_announcement", "severity": "medium",
            "title": f"一次情報Sourceの新着（{len(primary_new)}件）",
            "reason": f"一次情報として登録されたSource（{', '.join(names) or '不明'}）から、直近24時間に"
                      f"{len(primary_new)}件の新着があったため。",
            "related_signal_ids": [a["external_signal_id"] for a in primary_new[:20]],
            "fingerprint": _fingerprint(tid, "primary_announcement",
                                        day_key + ":" + ",".join(sorted(a["external_signal_id"] for a in primary_new)))})

    for key, label, pattern in _EVENT_PATTERNS:
        candidates = [a for a in recent if pattern.search(f"{a.get('title') or ''} {a.get('summary') or ''}")]
        if not candidates:
            continue
        # Context Validation: 「イベントが実際に発生したと記事が述べているか」だけを判定する
        results = [(a, validator.validate(a, pattern, rule_name=f"event_{key}", event_label=label)) for a in candidates]
        if validation_log is not None:
            validation_log.extend(r for _, r in results)
        hit = [a for a, r in results if r["valid"]]
        if not hit:
            continue
        excluded = len(candidates) - len(hit)
        indep = {a.get("source_id") or a.get("source_domain") or a.get("source_name") for a in hit} - {None}
        primary_hit = [a for a in hit if a.get("source_id") in primary_source_ids]
        # 単一Sourceの1記事だけのKeyword Hitではアラートにしない（誤検知抑制）
        if len(indep) >= 2 or primary_hit:
            valid_results = [r for a, r in results if r["valid"]]
            labels = sorted({r["label"] for r in valid_results})
            terms = sorted({r["matched_term"] for r in valid_results if r.get("matched_term")})
            alerts.append({
                "alert_type": f"event_{key}", "severity": "high" if (primary_hit and len(indep) >= 2) else "medium",
                "title": f"{label}に関する動き（{len(hit)}件・{len(indep)}ソース）",
                "reason": (f"「{label}」を示す語を含む記事が直近24時間に{len(candidates)}件あり、文脈検証で{len(hit)}件が"
                           f"『実際に起きた出来事』と確認され（否定・仮定・予測・過去・否認として{excluded}件を除外）、"
                           f"{len(indep)}の独立したSourceで確認され{'、うち一次情報Sourceを含む' if primary_hit else ''}たため"
                           f"（単一Sourceの語Hitのみでは出さない）。"),
                "related_signal_ids": [a["external_signal_id"] for a in hit[:20]],
                "candidate_rule": f"event_{key}:" + ",".join(terms[:6]),
                "validation_result": "llm_confirmed_actual" if labels == ["llm_confirmed_actual"] else "actual_event",
                "validation_reason": "; ".join(dict.fromkeys(r["reason"] for r in valid_results))[:500],
                "fingerprint": _fingerprint(tid, f"event_{key}", day_key)})
    return alerts


def _baseline_daily(articles: list, now: datetime) -> float:
    """直近24hを除く過去期間の1日平均記事数。"""
    older = [a for a in articles if (_article_time(a) or now) < now - timedelta(hours=24)]
    days = max(1.0, (WINDOW_HOURS - 24) / 24)
    return len(older) / days


def assess(client, theme: dict, *, now: datetime | None = None, persist: bool = True,
           azure_client=None, model: str | None = None) -> dict:
    """テーマの状態評価（Freshness Guard → 記事収集 → Alert判定）。persist=Trueで
    Alert・latest_assessment・last_checked_atを保存する。"""
    now = now or _now()
    freshness = lib.get_global_freshness(client)         # 広域メディア（GDELT等。Direct Sourceを含まない）
    since = now - timedelta(hours=WINDOW_HOURS)
    collected = collect_articles(client, theme, since=since)
    articles, sources = collected["articles"], collected["sources"]
    coverage = coverage_service.compute_theme_freshness_for(client, sources, broad=freshness, now=now)
    fstate = coverage["freshness_state"]
    day_ago = now - timedelta(hours=24)
    new_24h = [a for a in articles if (_article_time(a) or now - timedelta(days=99)) >= day_ago]
    primary_ids = {s["source_id"] for s in sources if s.get("is_primary") or s.get("source_tier") == "一次"}

    alerts: list = []
    validation_log: list = []
    if fstate in ("no_fresh_data", "unknown"):
        state = "no_fresh_data"
        state_label = "データ更新なし（状況判定不能）"
        summary = (f"データ取得が止まっているため状況を判定できません"
                   f"（最終データ取得: {(freshness['last_data_at'] or '記録なし')[:10]}）。"
                   "「変化なし」を意味するものではありません。")
    else:
        # partial_freshnessで広域メディアが止まっている場合は、最新の一次Direct Sourceの記事だけで判定する
        # （止まった広域メディアの古い記事を混ぜて、記事量の比較や「新着なし」を誤判定しない）
        if fstate == "partial_freshness" and freshness["status"] != "fresh":
            fresh_ids = set(coverage["fresh_primary_source_ids"])
            scope = [a for a in articles if a.get("source_id") in fresh_ids]
        else:
            scope = articles
        validator = alert_validation.AlertValidator(azure_client, model, now=now)
        # Baseline（Collectorの初回一括取り込み。UKMTOの既存事案など）は関連記事・Coverageには含めるが、Alert判定の対象にしない。
        # 新規・更新された事案だけが既存のAlert policyへ渡る
        alert_scope = [a for a in scope if a.get("baseline") is not True]
        alerts = detect_alerts(theme, alert_scope, primary_ids, now=now, baseline_daily=_baseline_daily(scope, now),
                               validator=validator, validation_log=validation_log)
        top = max((_SEVERITY_ORDER[a["severity"]] for a in alerts), default=0)
        if top >= 2:
            state, state_label = "changed", "変化あり"
            summary = alerts[0]["title"] if alerts else ""
        elif top == 1:
            state, state_label = "watch", "注視"
            summary = alerts[0]["title"]
        elif fstate == "partial_freshness":
            # 一部の情報源だけが最新。「stable」とは断定しない（テーマ全体の変化なしを意味しない）
            state, state_label = "partial_coverage", "確認範囲では新しい動きなし"
            summary = coverage_service.coverage_note(fstate, coverage, alerts=0)
        elif not articles:
            state, state_label = "no_new_articles", "新着なし"
            summary = "データ取得は動いていますが、このテーマに該当する記事が期間内にありません（安定を意味しません）。"
        else:
            state, state_label = "stable", "Stable"
            summary = f"期間内の関連記事{len(articles)}件。Alert対象の変化は検出されませんでした。"

    latest = articles[0] if articles else None
    assessment = {
        "state": state, "state_label": state_label, "summary": summary,
        "latest_change": alerts[0]["title"] if alerts else None,
        "new_articles_24h": len(new_24h), "articles_in_window": len(articles),
        "latest_article_at": (_article_time(latest).isoformat() if latest and _article_time(latest) else None),
        "freshness": freshness, "evaluated_at": now.isoformat(),
        "freshness_state": fstate, "freshness_label": coverage["freshness_label"], "coverage": coverage,
        "region_breakdown": _region_breakdown(theme, articles),
        "coverage_note": coverage_service.coverage_note(fstate, coverage, alerts=len(alerts)),
        "source_count": len(sources),
        "unlinked_note": ("Sourceが未設定です。Source Setを設定すると、より正確に監視できます。"
                          if not sources else None),
        # Alert Context Validationの監査情報（なぜ候補が採用/除外されたか）
        "validation": {
            "version": alert_validation.ALERT_VALIDATION_VERSION,
            "candidates": len(validation_log), "valid": sum(1 for r in validation_log if r["valid"]),
            "excluded": [{"title": r["title"], "rule": r["rule"], "label": r["label"], "reason": r["reason"]}
                         for r in validation_log if not r["valid"]][:15],
        },
    }
    saved_alerts = []
    if persist:
        for a in alerts:
            existing = client.select("geo_monitoring_alerts", {
                "select": "alert_id", "monitoring_theme_id": f"eq.{theme['monitoring_theme_id']}",
                "fingerprint": f"eq.{a['fingerprint']}"})
            if existing:
                continue
            row = {**a, "monitoring_theme_id": theme["monitoring_theme_id"], "detected_at": now.isoformat()}
            try:
                saved_alerts.extend(client.insert("geo_monitoring_alerts", [row]))
            except Exception as exc:  # noqa: BLE001
                if not lib._is_missing_column_error(exc):
                    raise
                # DB-8（監査列）未適用: 監査情報はlatest_assessment.validationに残るので列だけ外して保存する
                for k in ("candidate_rule", "validation_result", "validation_reason"):
                    row.pop(k, None)
                saved_alerts.extend(client.insert("geo_monitoring_alerts", [row]))
        client.update("geo_monitoring_themes", {"monitoring_theme_id": f"eq.{theme['monitoring_theme_id']}"},
                      {"latest_assessment": assessment, "last_checked_at": now.isoformat()})
    return {"assessment": assessment, "alerts_detected": alerts, "alerts_saved": saved_alerts,
            "articles": articles, "sources": sources}


def _region_breakdown(theme: dict, articles: list) -> dict | None:
    """Middle East Maritime系のテーマ（tagsに'ホルムズ'）の関連記事を海域別に数える（Hormuz / Gulf of Oman / Persian Gulf / Red Sea / Wider）。"""
    if "ホルムズ" not in (theme.get("tags") or []):
        return None
    out: dict = {}
    for a in articles:
        out[classify_maritime_region(a) or "unclassified"] = out.get(classify_maritime_region(a) or "unclassified", 0) + 1
    return out


def check_all(client, *, now: datetime | None = None, azure_client=None, model: str | None = None) -> dict:
    themes = client.select("geo_monitoring_themes", {"select": "*", "status": "eq.active", "limit": "200"})
    results = []
    for t in themes:
        try:
            r = assess(client, t, now=now, azure_client=azure_client, model=model)
            results.append({"monitoring_theme_id": t["monitoring_theme_id"], "state": r["assessment"]["state"]})
        except Exception as exc:  # noqa: BLE001 — 1テーマの失敗で全体を止めない
            results.append({"monitoring_theme_id": t["monitoring_theme_id"], "error": str(exc)})
    return {"checked": len(results), "results": results}


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

def get_detail(client, monitoring_theme_id: str, *, refresh: bool = False, azure_client=None,
               model: str | None = None) -> dict | None:
    theme = get_monitoring_theme(client, monitoring_theme_id)
    if not theme:
        return None
    result = (assess(client, theme, persist=refresh, azure_client=azure_client, model=model)
              if (refresh or not theme.get("latest_assessment")) else None)
    if result is None:
        # キャッシュ済みassessmentを使いつつ、記事/Source一覧は毎回最新を取り直す
        collected = collect_articles(client, theme, since=_now() - timedelta(hours=WINDOW_HOURS))
        assessment = dict(theme["latest_assessment"])
        articles, sources = collected["articles"], collected["sources"]
        live_cov = coverage_service.compute_theme_freshness_for(client, sources, broad=lib.get_global_freshness(client))
        assessment.update(freshness_state=live_cov["freshness_state"], freshness_label=live_cov["freshness_label"],
                          coverage=live_cov, coverage_note=coverage_service.coverage_note(
                              live_cov["freshness_state"], live_cov, alerts=0))
    else:
        assessment, articles, sources = result["assessment"], result["articles"], result["sources"]
        theme = get_monitoring_theme(client, monitoring_theme_id) or theme

    alerts = client.select("geo_monitoring_alerts", {
        "select": "*", "monitoring_theme_id": f"eq.{monitoring_theme_id}",
        "order": "detected_at.desc", "limit": "50"})
    # Key Actors: 設定Actors + 記事のentitiesに出た人物/組織
    actor_counts: dict = {}
    for a in articles:
        ents = a.get("entities")
        if isinstance(ents, dict):
            ents = [e for v in ents.values() if isinstance(v, list) for e in v]
        for e in ents or []:
            name = e.get("name") if isinstance(e, dict) else str(e)
            if name:
                actor_counts[name] = actor_counts.get(name, 0) + 1
    key_actors = [{"name": n, "article_count": c} for n, c in sorted(actor_counts.items(), key=lambda x: -x[1])[:10]]
    timeline = sorted(
        [{"at": a["detected_at"], "kind": "alert", "title": a["title"], "severity": a["severity"]} for a in alerts] +
        [{"at": (_article_time(x) or _now()).isoformat(), "kind": "article", "title": x.get("title"),
          "url": x.get("url"), "source": x.get("source_name") or x.get("source_domain")} for x in articles[:30]],
        key=lambda e: e["at"], reverse=True)[:40]
    references = [{"article_title": x.get("title"), "source": x.get("source_name") or x.get("source_domain"),
                   "published_at": x.get("published_at") or x.get("created_at"), "url": x.get("url"),
                   "external_signal_id": x["external_signal_id"], "matched_via": x.get("matched_via")}
                  for x in articles[:50]]
    source_set = None
    if theme.get("source_set_id"):
        try:
            rows = client.select("geo_source_sets", {"select": "source_set_id,name,archived_at",
                                                     "source_set_id": f"eq.{theme['source_set_id']}"})
            source_set = rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001
            if not lib._is_missing_column_error(exc):
                raise
    return {
        "source_set": source_set,   # archived_atがあれば「アーカイブ済みだが監視は継続中」とUIで示せる
        "monitoring_theme": theme, "assessment": assessment, "alerts": alerts, "timeline": timeline,
        "articles": references, "key_actors": key_actors, "sources": sources,
        "watchpoints": {"keywords": theme.get("keywords") or [], "actors": theme.get("actors") or [],
                        "criteria": theme.get("monitoring_criteria"), "sensitivity": theme.get("sensitivity"),
                        "frequency": theme.get("frequency")},
    }


def acknowledge_alert(client, alert_id: str, *, by: str | None) -> bool:
    if not client.select("geo_monitoring_alerts", {"select": "alert_id", "alert_id": f"eq.{alert_id}"}):
        return False
    client.update("geo_monitoring_alerts", {"alert_id": f"eq.{alert_id}"},
                  {"acknowledged_at": _now().isoformat(), "acknowledged_by": by})
    return True
