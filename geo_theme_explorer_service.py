"""Theme Explorer（テーマ探索）用の閲覧・絞り込みレイヤー。

geo_theme_candidates（Freeze済みTheme Discovery V1）は読み取りのみ。既存列・fingerprint・
Candidate生成ロジックには一切触れない（追加のscoreは別レイヤーとしてここで算出するだけで、
DBへ永続化もしない）。

★ 「重要度/Priority」は正式なModelが存在しないため作らない。代わりに既存の
  business_relevance / signal_count / change_type / recency / first_seen から、説明可能な
  暫定評価「注目度」を算出する（将来の正式Priority Modelとは分離）。
  配点（合計100）: 業務関連性25 + 根拠記事数25 + 変化タイプ15 + 直近性25 + 新規性10
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import geo_llm_common
import geo_source_library_service as lib
import geo_source_query_expansion as query_expansion

_CHANGE_TYPE_POINTS = {
    "policy_shift": 15, "relationship_change": 15, "actor_movement": 14, "signal_spike": 12,
    "cross_region_pattern": 12, "institutionalization": 10, "recurrence": 8}
BUCKETS = ("notable", "new", "ongoing", "past")
BUCKET_LABELS = {"notable": "注目テーマ", "new": "新たに登場したテーマ",
                 "ongoing": "継続的に見るべきテーマ", "past": "過去の注目テーマ"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compute_attention(c: dict, *, now: datetime | None = None) -> dict:
    """説明可能な暫定「注目度」。戻り値: {score, level, breakdown[{label, points, max, note}]}"""
    now = now or _now()
    rel = c.get("business_relevance") or []
    rel_pts = round(min(len(rel), 3) / 3 * 25)
    n = c.get("signal_count") or 0
    vol_pts = round(min(n, 20) / 20 * 25)
    ct = c.get("change_type")
    ct_pts = _CHANGE_TYPE_POINTS.get(ct, 0)
    latest = lib._parse_ts(c.get("latest_signal_at"))
    age = (now - latest).days if latest else None
    rec_pts = 0 if age is None else 25 if age <= 3 else 18 if age <= 7 else 10 if age <= 14 else 5 if age <= 30 else 0
    first = lib._parse_ts(c.get("first_seen_at"))
    new_pts = 10 if first and (now - first).days <= 7 else 0
    breakdown = [
        {"label": "業務関連性", "points": rel_pts, "max": 25, "note": f"関連タグ{len(rel)}件"},
        {"label": "根拠記事数", "points": vol_pts, "max": 25, "note": f"{n}件"},
        {"label": "変化タイプ", "points": ct_pts, "max": 15, "note": ct or "不明"},
        {"label": "直近性", "points": rec_pts, "max": 25,
         "note": "最新Signalなし" if age is None else f"最新Signalは{age}日前"},
        {"label": "新規性", "points": new_pts, "max": 10, "note": "7日以内に初出" if new_pts else "既出"},
    ]
    score = sum(b["points"] for b in breakdown)
    return {"score": score, "level": "high" if score >= 60 else "medium" if score >= 35 else "low",
            "breakdown": breakdown}


def classify_buckets(c: dict, attention: dict, *, now: datetime | None = None) -> list:
    now = now or _now()
    status = c.get("status")
    latest = lib._parse_ts(c.get("latest_signal_at"))
    first = lib._parse_ts(c.get("first_seen_at"))
    stale = latest is None or (now - latest).days > 30
    buckets = []
    if status in ("DISMISSED", "HANDED_OFF") or stale:
        buckets.append("past")
        return buckets
    if first and (now - first).days <= 7:
        buckets.append("new")
    if attention["score"] >= 60:
        buckets.append("notable")
    if status in ("MONITORING", "SHORTLISTED") or (first and (now - first).days > 14):
        buckets.append("ongoing")
    if not buckets:
        buckets.append("ongoing")
    return buckets


_INTERPRET_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["keywords", "region", "new_only", "period_days"],
    "properties": {"keywords": {"type": "array", "items": {"type": "string"}},
                   "region": {"type": "string"}, "new_only": {"type": "boolean"},
                   "period_days": {"type": "integer"}}}
_INTERPRET_PROMPT = (
    "地政学テーマ探索の自然言語検索を、絞り込み条件へ変換してください。keywords: 主要語(最大6、"
    "日本語は原文、英語表記もあれば追加可)。region: 明確な地域/国のみ。new_only: 『まだ注目されて"
    "いない』『新しい』等を求める場合のみtrue。period_days: 期間指定があれば日数、無ければ0。"
    "推測で条件を足さない。")


# 調査プロンプト（長い自然言語）から語を取り出すときに除く、意味の薄い語（日本語の依頼表現・一般語）
_PROMPT_STOPWORDS = {"テーマ", "探したい", "知りたい", "見たい", "調べたい", "ついて", "最近", "動き", "もの", "こと",
                     "ような", "について", "関する", "関連", "ため", "など", "まだ", "大きく", "うち", "重要", "重要な",
                     "新しい", "新しく", "一般", "注目", "ある", "いる", "いない", "ない", "する", "した", "よう", "さらに"}
# 日本語の語を「助詞」だけでなく、動詞・依頼表現・接続の語尾でも区切る（「めぐる軍事」のような塊を避ける）
_LEADING_ADVERB = re.compile(r"^(?:最近|まだ|大きく|新しく|新しい|特に|とくに)")
_PROMPT_SPLIT = re.compile(
    r"(?:をめぐる|をめぐって|に関する|に関して|について|に対する|における|による|のうち|のなか|という|ような|"
    r"を探したい|を知りたい|を見たい|を調べたい|探したい|知りたい|見たい|調べたい|が|を|は|の|に|で|と|や|か|も|へ|から|まで|"
    r"ない|ある|いる|なった|した|する|ており|ている|され|される|でき|できる|・|、|。|，|,|\s)+")


def _fallback_keywords(text: str) -> list:
    """LLMが使えない時のキーワード抽出。日本語の長文は空白区切りでは1語になり何にも一致しないため、
    助詞・動詞・依頼表現で区切った語（2文字以上）とASCII語を使う。"""
    text = (text or "").replace("　", " ")
    if query_expansion.has_cjk(text):
        raw = query_expansion._ASCII_WORD.findall(text) + _PROMPT_SPLIT.split(text)
        words = []
        for w in raw:
            w = _LEADING_ADVERB.sub("", w.strip())
            # ひらがなのみの語（「てい」等）は何にでも一致してしまうので使わない
            if len(w) >= 2 and w not in _PROMPT_STOPWORDS and not re.fullmatch(r"[぀-ゟ]+", w):
                words.append(w)
        words = query_expansion._dedupe(words)
    else:
        words = [w for w in text.split() if w]
    return words[:6]


def interpret_query(azure_client, model: str, text: str) -> dict:
    fallback = {"keywords": _fallback_keywords(text),
                "region": None, "new_only": False, "period_days": 0}
    if not (text or "").strip():
        return {"filters": fallback, "used_llm": False, "error": None}
    if azure_client is None:
        return {"filters": fallback, "used_llm": False, "error": "AI未設定のためキーワード検索にしました"}
    try:
        res = geo_llm_common.call_llm_structured(
            azure_client, model, _INTERPRET_PROMPT, text, _INTERPRET_SCHEMA, "theme_search_interpretation",
            max_attempts=2)
    except geo_llm_common.GeoLLMError as exc:
        return {"filters": fallback, "used_llm": False,
                "error": f"検索意図の解釈に失敗したためキーワード検索にしました: {exc}"}
    d = res["data"]
    return {"filters": {"keywords": [k for k in d["keywords"] if k.strip()][:6], "region": d["region"] or None,
                        "new_only": bool(d["new_only"]), "period_days": int(d["period_days"] or 0)},
            "used_llm": True, "error": None}


def _text_blob(c: dict) -> str:
    parts = [c.get("title"), c.get("question"), c.get("why_now"), c.get("observed_change"), c.get("signal_summary")]
    for key in ("actors", "regions", "themes", "business_relevance"):
        parts.extend(c.get(key) or [])
    return " ".join(str(p) for p in parts if p).lower()


def _region_matches(c: dict, region: str) -> bool:
    """地域の一致は緩やかに判定する。AIが文章から取り出す地域（例: 「台湾海峡」）と候補の地域タグ（例: 「台湾」）は
    粒度が違うことが多く、完全一致だと取りこぼす。部分一致（どちらかがもう一方を含む）か、候補の文面に地域名があれば一致。"""
    rl = region.strip().lower()
    if not rl:
        return True
    regs = [str(r).strip().lower() for r in (c.get("regions") or []) if str(r).strip()]
    if any(rl in r or r in rl for r in regs):
        return True
    return rl in _text_blob(c)


# ---------------------------------------------------------------------------
# V1_1: Candidateの由来（candidate_origin）・提案状態・根拠記事
# ---------------------------------------------------------------------------

# 由来は専用列を増やさず、既存の generation_model（telemetry用の自由記述text）の接頭辞で持つ。
#   "article_discovery/<model>" → 記事から発見 / "monitoring/..." → Monitoring由来 / "manual/..." → 手動
#   上記以外（LLM model名・NULL）→ "situation"（従来のApproved Situation Link由来。V1のsyncが入れる）
ORIGIN_PREFIXES = {"article_discovery": "article_discovery", "web_discovery": "web_discovery",
                   "monitoring": "monitoring", "manual": "manual"}
ORIGIN_LABELS = {"article_discovery": "記事から発見", "web_discovery": "Webから発見", "situation": "Situationから発見",
                 "monitoring": "モニタリングから発見", "manual": "手動で登録"}


def candidate_origin(row: dict) -> str:
    gm = (row.get("generation_model") or "").strip()
    head = gm.split("/", 1)[0] if "/" in gm else ""
    return ORIGIN_PREFIXES.get(head, "situation")


def proposal_state(row: dict) -> str:
    """AIが見つけた候補は人間が判断するまで「提案（pending）」。Situation由来は従来どおり"NEW"のまま扱う。
    status が NEW 以外（SHORTLISTED/MONITORING/HANDED_OFF/DISMISSED）になれば、人間が何か判断した後なので確定扱い。"""
    if candidate_origin(row) == "situation":
        return "n/a"
    return "pending" if row.get("status") == "NEW" else "decided"


def attach_evidence_articles(client, rows: list, *, per_candidate: int = 5) -> None:
    """各候補の根拠記事（タイトル・Source・公開日・元URL）を付ける。根拠が追えない候補を作らないための表示用。
    geo_external_signals から ID 指定でまとめて1回取得する（候補ごとに問い合わせない）。"""
    want: dict = {}
    for r in rows:
        for sid in (r.get("source_external_signal_ids") or [])[:60]:
            want.setdefault(sid, None)
    found: dict = {}
    ids = list(want)
    for i in range(0, len(ids), 80):
        chunk = ids[i:i + 80]
        try:
            for a in client.select("geo_external_signals", {
                    "select": "external_signal_id,title,url,source_name,source_domain,published_at,created_at,timestamp_source",
                    "external_signal_id": "in.(" + ",".join(chunk) + ")", "limit": str(len(chunk))}):
                found[a["external_signal_id"]] = a
        except Exception as exc:  # noqa: BLE001 — 根拠記事の取得失敗で一覧全体を止めない
            if not lib._is_missing_column_error(exc):
                raise
    for r in rows:
        arts = [found[i] for i in (r.get("source_external_signal_ids") or []) if i in found]
        arts.sort(key=lambda a: a.get("published_at") or a.get("created_at") or "", reverse=True)
        r["evidence_articles"] = [{
            "external_signal_id": a["external_signal_id"], "title": a.get("title"), "url": a.get("url"),
            "source": a.get("source_name") or a.get("source_domain"),
            "published_at": a.get("published_at") or a.get("created_at"),
            # 公開日が分からない情報源（Web検索の引用など。取得時刻しか無い）は画面で「日付不明」にする
            "date_known": a.get("timestamp_source") != "unknown"} for a in arts[:per_candidate]]
        r["evidence_total"] = len(r.get("source_external_signal_ids") or [])


def decorate_candidate(row: dict) -> dict:
    row["candidate_origin"] = candidate_origin(row)
    row["candidate_origin_label"] = ORIGIN_LABELS[row["candidate_origin"]]
    row["proposal_state"] = proposal_state(row)
    return row


def explore(client, azure_client=None, model: str | None = None, *, q: str | None = None,
            region: str | None = None, category: str | None = None, level: str | None = None,
            bucket: str | None = None, new_only: bool = False, period_days: int | None = None,
            status: str | None = None, is_demo: bool = False, page: int = 1, page_size: int = 20,
            now: datetime | None = None, keyword: str | None = None, sort: str = "newest",
            include_deleted: bool = False) -> dict:
    """V1_1: 画面は「調査プロンプト(q)」「キーワード絞り込み(keyword)」「並び替え(sort)」「削除」だけにした。
    - q: 自然言語の調査プロンプト（LLMで語・地域・新規性・期間を解釈）
    - keyword: 候補の文面への単純な絞り込み（空白区切りは全語を含む=AND、LLMなし）
    - sort: newest（新しい順=最新Signal日時の降順、既定）/ oldest（古い順）/ attention（注目度順）
    - 削除=DISMISSED（論理削除）。既定で一覧から除外する（include_deleted=trueで含める）
    - available_total: 削除済みを除く候補の総数（絞り込み前）。0件なら「候補自体がまだ無い」と画面で説明できる"""
    now = now or _now()
    params = {"select": "*", "is_demo": f"eq.{'true' if is_demo else 'false'}",
              "order": "latest_signal_at.desc", "limit": "500"}
    if status:
        params["status"] = f"eq.{status}"
    candidates = client.select("geo_theme_candidates", params)
    if not include_deleted and status != "DISMISSED":
        candidates = [c for c in candidates if c.get("status") != "DISMISSED"]
    available_total = len(candidates)

    interp = None
    keywords: list = []
    if q and q.strip():
        interp = interpret_query(azure_client, model, q)
        f = interp["filters"]
        keywords = [k.lower() for k in f["keywords"]]
        region = region or f.get("region")
        new_only = new_only or f.get("new_only", False)
        period_days = period_days or (f.get("period_days") or None)

    enriched = []
    for c in candidates:
        att = compute_attention(c, now=now)
        c["attention"] = att
        c["buckets"] = classify_buckets(c, att, now=now)
        c["related_article_count"] = c.get("signal_count") or 0
        decorate_candidate(c)
        enriched.append(c)

    keyword_tokens = [t.lower() for t in (keyword or "").replace("　", " ").split() if t.strip()]

    def keep(c) -> bool:
        if keyword_tokens and not all(t in _text_blob(c) for t in keyword_tokens):
            return False
        if keywords and not any(k in _text_blob(c) for k in keywords):
            return False
        if region and not _region_matches(c, region):
            return False
        if category and category not in ((c.get("candidate_type"), c.get("change_type")) +
                                         tuple(c.get("business_relevance") or [])):
            return False
        if level and c["attention"]["level"] != level:
            return False
        if new_only and "new" not in c["buckets"]:
            return False
        if period_days:
            latest = lib._parse_ts(c.get("latest_signal_at"))
            if not latest or latest < now - timedelta(days=period_days):
                return False
        return True

    filtered = [c for c in enriched if keep(c)]
    counts = {b: sum(1 for c in filtered if b in c["buckets"]) for b in BUCKETS}
    shown = [c for c in filtered if (not bucket or bucket in c["buckets"])]
    if sort == "attention":
        shown.sort(key=lambda c: (c["attention"]["score"], c.get("latest_signal_at") or ""), reverse=True)
    else:   # newest（既定）/ oldest: 最新Signal日時で並べる
        shown.sort(key=lambda c: (c.get("latest_signal_at") or "", c.get("title") or ""), reverse=(sort != "oldest"))

    facets = {"regions": sorted({r for c in enriched for r in (c.get("regions") or [])}),
              "categories": sorted({v for c in enriched for v in
                                    [c.get("candidate_type"), c.get("change_type"), *(c.get("business_relevance") or [])] if v})}
    page = max(1, int(page))
    start = (page - 1) * page_size
    page_rows = shown[start:start + page_size]
    attach_evidence_articles(client, page_rows)
    return {"themes": page_rows, "total": len(shown), "page": page,
            "available_total": available_total, "sort": sort,
            "page_size": page_size, "bucket_counts": counts, "bucket_labels": BUCKET_LABELS,
            "facets": facets, "interpretation": interp,
            "data_freshness": lib.get_global_freshness(client)}


def candidate_for_recommendation(client, candidate_id: str) -> dict | None:
    rows = client.select("geo_theme_candidates", {"select": "*", "candidate_id": f"eq.{candidate_id}"})
    if not rows:
        return None
    c = rows[0]
    return {"title": c.get("title"), "actors": c.get("actors") or [], "regions": c.get("regions") or [],
            "themes": c.get("themes") or [], "external_signal_ids": c.get("source_external_signal_ids") or [],
            "candidate": c}
