"""Article Discovery: 「テーマ探索開始」で、直近の記事からその場でTheme Candidateを提案する（SOURCE_INTELLIGENCE_V1_1）。

Theme Candidateの入力経路を増やすもので、既存の Approved Situation Link → Theme Candidate
（geo_theme_candidate_service.sync_theme_candidates）は廃止も変更もしない。両者は同じ geo_theme_candidates に入り、
由来は candidate_origin（generation_model の接頭辞から導出。"article_discovery/<model>"）で区別する。
将来の Monitoring Alert 由来・手動登録も同じ仕組みで足せる（geo_theme_explorer_service.ORIGIN_PREFIXES）。

処理（実際の処理段階と進行表示が一致するように、段階ごとにjobの状態を更新する）:
  1. interpret : 入力を解釈（既存 interpret_query = 語・地域・期間 ＋ 既存 Query Expansion = 日本語→英語）
  2. search    : 関連記事を検索（既存 geo_external_signals。Source Set指定時はそのSource/domainに限定）
  3. cluster   : 関連記事をLLMで「同じ変化・論点・Actorの動き」ごとに束ねる → 検証 → 既存との重複判定 → 保存

方針:
- 新しい記事検索基盤は作らない（geo_external_signals / Source Registry / Query Expansion / 一致判定をそのまま再利用）。
- 1記事1テーマにしない。1テーマに最低2記事（異なる見出し）＋（2つ以上のSource または 3記事以上）の根拠を要求する。
- 「イベント名」ではなく「何が変わっているか」をテーマにする（プロンプトで指示し、根拠不足は出さない）。
- 既存Theme Candidate / Monitoring Theme / 既存Themeとほぼ同じなら新規を乱造せず「既存テーマに新しい動き」として返す。
  削除済み（DISMISSED）の候補と同じテーマは復活させない。
- AIが見つけた候補は status=NEW の提案（pending）。人間がMonitoring追加/調査/Expert AI/却下するまで確定扱いにしない。
- 十分な関連記事が無い場合はLLMを呼ばず、無理に候補を作らない。
- 根拠記事（タイトル・Source・公開日・元URL）を必ず追えるよう、source_external_signal_ids に保存する。
- 同一（入力・Source・期間）は短時間キャッシュする。LLMには関連度の高い記事だけ（最大40件）を渡す。
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import geo_llm_common
import geo_source_library_service as lib
import geo_source_query_expansion as query_expansion
import geo_source_research_service as research
import geo_source_set_service as sets
import geo_theme_explorer_service as explorer

DISCOVERY_VERSION = "article_discovery_v1"
ORIGIN = "article_discovery"
DEFAULT_PERIOD_DAYS = 30
MAX_PERIOD_DAYS = 180
MAX_ARTICLES_TO_LLM = 40          # LLMへ渡す記事の上限（関連度の高いものだけ）
MAX_PER_DOMAIN = 6                # 1つのSourceに偏らないようにする上限
MIN_MATCHED_ARTICLES = 3          # これ未満なら材料不足とみなしLLMを呼ばない
MAX_THEMES = 5
MIN_EVIDENCE_ARTICLES = 2
OVERLAP_DUPLICATE = 0.5           # 根拠記事の重なり（小さい方に対する割合）がこれ以上なら同じテーマ
TITLE_SIMILARITY_DUPLICATE = 0.6  # 見出しの類似度（2文字shingleのJaccard）
CACHE_TTL_SECONDS = 600
NO_RESULT_MESSAGE = "該当する新しいテーマ候補は見つかりませんでした"

STEPS = [("interpret", "入力を解釈"), ("search", "関連記事を検索"), ("cluster", "テーマ候補を整理")]

CANDIDATE_TYPES = ["emerging", "changing", "re_emerging", "structural", "cross_market"]
CHANGE_TYPES = ["actor_movement", "policy_shift", "relationship_change", "institutionalization",
                "signal_spike", "recurrence", "cross_region_pattern"]


class DiscoveryError(Exception):
    """探索を続けられない失敗（画面にそのまま理由を出す）。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 文字列ユーティリティ（見出しの正規化・類似度）
# ---------------------------------------------------------------------------

_NORM_RE = re.compile(r"[^a-z0-9぀-ヿ一-鿿]+")


def norm_text(s: str | None) -> str:
    return _NORM_RE.sub("", (s or "").lower())


def shingles(s: str, n: int = 2) -> set:
    s = norm_text(s)
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else ({s} if s else set())


def title_similarity(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------------------
# 2. 関連記事の検索（既存 geo_external_signals / Source Registry / 一致判定の再利用）
# ---------------------------------------------------------------------------

_ARTICLE_COLS = ("external_signal_id,title,summary,url,source_id,source_name,source_domain,"
                 "published_at,created_at")


# 探索の英訳に混ざる一般語（「recently」「notable」等）。検索に使うと無関係な記事を大量に拾い、取得上限を埋めてしまう。
GENERIC_TERMS = {
    "recent", "recently", "latest", "new", "newly", "notable", "noteworthy", "topic", "topics", "theme", "themes",
    "development", "developments", "change", "changes", "trend", "trends", "watch", "monitor", "monitoring",
    "attention", "important", "key", "emerging", "worth", "going", "forward", "future", "should", "need", "needs",
    "issue", "issues", "situation", "news", "update", "updates", "about", "around", "regarding",
    "emerged", "emerge", "emerges", "movement", "movements", "observed", "relevant", "various", "concerning",
    "over", "past", "month", "months", "week", "weeks", "year", "years", "day", "days", "three", "several",
    "recent", "region", "regional", "global", "international", "worldwide", "activity", "activities",
}


def build_search_terms(interp_filters: dict, expansion: dict) -> list:
    """入力の解釈（日本語の語・地域）と Query Expansion（英訳・展開語）から、出自つきの検索語を作る。
    一般語（GENERIC_TERMS）は除く。"""
    terms = [{"term": t["term"], "kind": t["kind"]} for t in expansion.get("search_terms", [])
             if t["term"].lower() not in GENERIC_TERMS]
    seen = {t["term"].lower() for t in terms}
    for k in (interp_filters.get("keywords") or []):
        if k and k.lower() not in seen:
            seen.add(k.lower())
            terms.append({"term": k, "kind": "original"})
    region = interp_filters.get("region")
    if region and region.lower() not in seen:
        terms.append({"term": region, "kind": "original"})
    return terms[:14]


def retrieve_articles(client, search_terms: list, *, source_ids: list, domains: set,
                      since_iso: str, limit: int = 240) -> list:
    """期間内の記事を、検索語のOR条件で取得する（published_atのindexで期間を絞る）。
    Sourceが指定されていればそのSource/domainに限定。未指定なら全登録Source＋既存Article全体。"""
    terms = [t["term"].replace(",", " ").replace("(", " ").replace(")", " ").strip() for t in search_terms]
    terms = [t for t in terms if t][:12]
    if not terms:
        return []
    cond = ",".join(f"title.ilike.*{t}*,summary.ilike.*{t}*" for t in terms)
    bases = []
    if source_ids:
        bases.append({"source_id": "in.(" + ",".join(source_ids) + ")"})
    if domains:
        bases.append({"source_domain": "in.(" + ",".join(sorted(domains)) + ")"})
    if not bases:
        bases = [{}]
    found: dict = {}
    for base in bases:
        try:
            rows = client.select("geo_external_signals", {
                "select": _ARTICLE_COLS, **base, "published_at": f"gte.{since_iso}", "or": f"({cond})",
                "order": "published_at.desc", "limit": str(limit)})
        except Exception as exc:  # noqa: BLE001
            if lib._is_missing_column_error(exc):   # source_id列が無い旧スキーマはdomain側だけで探す
                continue
            raise
        for r in rows:
            found.setdefault(r["external_signal_id"], r)
    return list(found.values())


def discovery_required(search_terms: list) -> dict:
    """探索向けの一致条件。Researchは「質問に直接関連する根拠」を厳しく選ぶ（英語側は最大3語一致）が、探索は
    「話題に関係しそうな記事」を広めに集める（一般語を除いた検索語が1語でも一致すれば候補）。精度は
    (1) 一致した語の数が多い順に上位だけをLLMへ渡す (2) LLMが関連しない記事を捨てる (3) 検証で
    『2記事以上＋独立した根拠』を要求する、の3段で担保する。"""
    n: dict = {}
    for t in search_terms:
        n.setdefault(t["kind"], set()).add(t["term"].lower())
    en = len(n.get("translated", set()) | n.get("expanded", set()))
    orig = len(n.get("original", set()))
    return {"english": None if en == 0 else 1, "original": None if orig == 0 else 1}


_CJK_RE = re.compile(r"[぀-ヿ一-鿿]")


def term_hits(article: dict, search_terms: list) -> list:
    """記事に含まれる検索語（出自つき）。英字の語は単語単位で一致させる（"PLA"が"planting"に一致しない）。
    複数形（-s/-es）は許容。日本語・中国語の語は部分一致。"""
    text = f"{article.get('title') or ''} {article.get('summary') or ''}".lower()
    hits = []
    for t in search_terms:
        term = t["term"].lower().strip()
        if not term:
            continue
        if _CJK_RE.search(term):
            ok = term in text
        else:
            ok = re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?:s|es)?(?![a-z0-9])", text) is not None
        if ok:
            hits.append({"term": t["term"], "kind": t["kind"]})
    return hits


def select_relevant(articles: list, search_terms: list) -> tuple:
    """既存のResearch一致判定（異なる検索語の一致数）で関連記事に絞り、転載の重複を除いて、
    関連度→新しさの順に、1つのSourceへ偏らない範囲で上位を返す。戻り値: (LLMへ渡す記事, 関連記事の数)"""
    required = discovery_required(search_terms)
    matched = []
    for a in articles:
        hits = term_hits(a, search_terms)
        if research._is_direct_match(hits, required):
            a["_score"] = len({h["term"].lower() for h in hits})
            matched.append(a)
    uniq: dict = {}
    for a in sorted(matched, key=lambda x: (x["_score"], x.get("published_at") or ""), reverse=True):
        key = norm_text(a.get("title")) or a["external_signal_id"]
        uniq.setdefault(key, a)               # 同じ見出しの転載記事は1件に
    per_domain: dict = {}
    picked = []
    for a in uniq.values():
        d = a.get("source_domain") or a.get("source_name") or ""
        if per_domain.get(d, 0) >= MAX_PER_DOMAIN:
            continue
        per_domain[d] = per_domain.get(d, 0) + 1
        picked.append(a)
        if len(picked) >= MAX_ARTICLES_TO_LLM:
            break
    return picked, len(uniq)


# ---------------------------------------------------------------------------
# 3. LLMによるクラスタリング（複数記事→「何が変わっているか」のテーマ）と検証
# ---------------------------------------------------------------------------

_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["themes", "no_theme_reason"],
    "properties": {
        "no_theme_reason": {"type": "string"},
        "themes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            # 必須は最小限（モデルのばらつきで探索全体が失敗しないように）。他の項目は欠けても既定値で補う
            "required": ["title", "short_description", "evidence_ids"],
            "properties": {
                "title": {"type": "string"}, "short_description": {"type": "string"},
                "observed_change": {"type": "string"}, "why_it_matters": {"type": "string"},
                "question": {"type": "string"},
                "actors": {"type": "array", "items": {"type": "string"}},
                "regions": {"type": "array", "items": {"type": "string"}},
                "topics": {"type": "array", "items": {"type": "string"}},
                "candidate_type": {"type": "string", "enum": CANDIDATE_TYPES},
                "change_type": {"type": "string", "enum": CHANGE_TYPES},
                "evidence_ids": {"type": "array", "items": {"type": "integer"}}}}}},
}
_PROMPT = (
    "あなたは地政学インテリジェンス部門のアナリストです。ユーザーの関心（調査プロンプト）と、直近の記事一覧"
    "（[番号]つき）が与えられます。記事群から『継続して見る価値のある変化』をテーマ候補としてまとめてください。\n"
    "【ルール】\n"
    "- 1記事=1テーマにしない。同じ変化・論点・Actorの動きを示す複数の記事（最低2件、できれば異なるSource）を"
    "束ねて1テーマにする。\n"
    "- テーマは『イベント名』ではなく『何が変わっているか』で書く。\n"
    "  悪い例: 「中国外相が会見」「BISが発表」。\n"
    "  良い例: 「中国が台湾周辺の軍事活動を常態化させる兆候」「米国の対中AI半導体規制が一律禁止から運用管理型へ移る可能性」。\n"
    "- 単発の出来事、ニュース速報、無関係な話題（スポーツ・芸能・市況の一言など）はテーマにしない。"
    "変化として読めないものは出力しない。\n"
    "- 記事に書かれていない事実・Actor・地域を足さない。断定せず『兆候』『可能性』の表現を使う。\n"
    "- evidence_idsには、そのテーマの根拠にした記事の番号だけを入れる（存在しない番号を書かない）。\n"
    "- 十分な材料が無ければthemesを空にし、no_theme_reasonに理由を書く。最大5テーマ。\n"
    "【項目】title: 40字程度の『変化』の文 / short_description: 何が起きているか1〜2文 / observed_change: 何が変わっている"
    "可能性があるか1文 / why_it_matters: なぜ今見るべきか（日本企業・地政学担当部門にとっての意味）1〜2文 / "
    "question: 分析すべき問い1文 / actors・regions: 記事に登場するもののみ / topics: タグ（最大4）")


def _article_block(articles: list) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        date = (a.get("published_at") or a.get("created_at") or "")[:10] or "日付不明"
        src = a.get("source_name") or a.get("source_domain") or "不明"
        lines.append(f"[{i}] {date} | {src} | {a.get('title') or '(無題)'}\n    {(a.get('summary') or '')[:220]}")
    return "\n".join(lines)


def validate_themes(raw_themes: list, articles: list) -> list:
    """LLMの出力を検証する。根拠記事の番号が実在し、1記事1テーマでなく、独立した根拠があるものだけ通す。"""
    out, used_sets = [], []
    for t in raw_themes[:MAX_THEMES * 2]:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        idx = []
        for n in t.get("evidence_ids") or []:
            if isinstance(n, int) and 1 <= n <= len(articles) and n not in idx:
                idx.append(n)
        ev = [articles[n - 1] for n in idx]
        titles = {norm_text(a.get("title")) for a in ev}
        domains = {a.get("source_domain") or a.get("source_name") for a in ev} - {None, ""}
        if len(titles) < MIN_EVIDENCE_ARTICLES:
            continue                                   # 1記事1テーマは不可
        if len(domains) < 2 and len(titles) < 3:
            continue                                   # 独立した根拠が足りない（1つのSourceの2記事だけ等）
        key = frozenset(a["external_signal_id"] for a in ev)
        if any(len(key & u) / min(len(key), len(u)) >= OVERLAP_DUPLICATE for u in used_sets):
            continue                                   # 同じ実行内で根拠がほぼ同じ別テーマは捨てる
        used_sets.append(key)
        ev.sort(key=lambda a: a.get("published_at") or a.get("created_at") or "", reverse=True)
        dates = [a.get("published_at") or a.get("created_at") for a in ev if a.get("published_at") or a.get("created_at")]
        out.append({
            "title": title[:120], "short_description": (t.get("short_description") or "").strip()[:500],
            "observed_change": (t.get("observed_change") or "").strip()[:400],
            "why_it_matters": (t.get("why_it_matters") or "").strip()[:500],
            "question": (t.get("question") or "").strip()[:300],
            "actors": [x.strip() for x in (t.get("actors") or []) if x.strip()][:8],
            "regions": [x.strip() for x in (t.get("regions") or []) if x.strip()][:6],
            "topics": [x.strip() for x in (t.get("topics") or []) if x.strip()][:4],
            "candidate_type": t.get("candidate_type") if t.get("candidate_type") in CANDIDATE_TYPES else "emerging",
            "change_type": t.get("change_type") if t.get("change_type") in CHANGE_TYPES else "signal_spike",
            "evidence": ev, "evidence_ids": [a["external_signal_id"] for a in ev],
            "first_seen_at": min(dates) if dates else _now().isoformat(),
            "latest_signal_at": max(dates) if dates else _now().isoformat()})
        if len(out) >= MAX_THEMES:
            break
    return out


def cluster_with_llm(azure_client, model, prompt: str, articles: list) -> tuple:
    """戻り値: (検証済みテーマ, LLMのno_theme_reason)。LLMが使えない/失敗した場合は DiscoveryError。"""
    if azure_client is None:
        raise DiscoveryError("AI分析エンジンが設定されていないため、テーマ候補を整理できません。")
    user = f"# 調査プロンプト\n{prompt}\n\n# 直近の記事\n{_article_block(articles)}"
    try:
        res = geo_llm_common.call_llm_structured(azure_client, model, _PROMPT, user, _SCHEMA,
                                                 "article_theme_discovery", max_attempts=3)
    except geo_llm_common.GeoLLMError as exc:
        raise DiscoveryError(f"テーマ候補の整理に失敗しました（AIの応答エラー）: {str(exc)[:120]}") from exc
    data = res["data"]
    return validate_themes(data.get("themes") or [], articles), (data.get("no_theme_reason") or "").strip()


# ---------------------------------------------------------------------------
# 既存との重複判定（Theme Candidate / Monitoring Theme / 既存Theme）
# ---------------------------------------------------------------------------

def load_existing(client) -> dict:
    cands = client.select("geo_theme_candidates", {
        "select": "candidate_id,title,status,source_external_signal_ids,generation_model", "limit": "2000"})
    try:
        mons = client.select("geo_monitoring_themes", {
            "select": "monitoring_theme_id,display_name,status,theme_candidate_id",
            "status": "neq.archived", "limit": "500"})
    except Exception as exc:  # noqa: BLE001
        if not lib._is_missing_column_error(exc):
            raise
        mons = []
    themes = client.select("geo_themes", {"select": "theme_id,name_ja", "limit": "300"})
    return {"candidates": cands, "monitoring": mons, "themes": themes}


def find_duplicate(title: str, evidence_ids: list, existing: dict) -> dict | None:
    """既存とほぼ同じテーマか判定する。戻り値: {"type": candidate|deleted_candidate|monitoring|theme, ...} / None。
    判定は (1) 根拠記事の重なり (2) 見出しの類似。優先順: Theme Candidate → Monitoring Theme → 既存Theme。"""
    ev = set(evidence_ids)
    best = None
    for c in existing["candidates"]:
        s = set(c.get("source_external_signal_ids") or [])
        overlap = (len(ev & s) / min(len(ev), len(s))) if ev and s else 0.0
        sim = title_similarity(title, c.get("title") or "")
        if overlap >= OVERLAP_DUPLICATE or sim >= TITLE_SIMILARITY_DUPLICATE:
            kind = "deleted_candidate" if c.get("status") == "DISMISSED" else "candidate"
            cand = {"type": kind, "id": c["candidate_id"], "title": c.get("title"), "status": c.get("status"),
                    "existing_signal_ids": s, "score": max(overlap, sim)}
            # 生きている候補を、削除済みより優先する
            if best is None or (best["type"] == "deleted_candidate" and kind == "candidate") or \
                    (best["type"] == kind and cand["score"] > best["score"]):
                best = cand
    if best is not None:
        return best
    for m in existing["monitoring"]:
        if title_similarity(title, m.get("display_name") or "") >= TITLE_SIMILARITY_DUPLICATE:
            return {"type": "monitoring", "id": m["monitoring_theme_id"], "title": m.get("display_name"),
                    "existing_signal_ids": set(), "score": 1.0}
    for t in existing["themes"]:
        if title_similarity(title, t.get("name_ja") or "") >= TITLE_SIMILARITY_DUPLICATE:
            return {"type": "theme", "id": t["theme_id"], "title": t.get("name_ja"),
                    "existing_signal_ids": set(), "score": 1.0}
    return None


def _article_view(a: dict) -> dict:
    return {"external_signal_id": a["external_signal_id"], "title": a.get("title"), "url": a.get("url"),
            "source": a.get("source_name") or a.get("source_domain"),
            "published_at": a.get("published_at") or a.get("created_at")}


def _fingerprint(evidence_ids: list) -> str:
    return hashlib.sha256((f"{ORIGIN}:v1:" + ",".join(sorted(evidence_ids))).encode()).hexdigest()


def _row_for(theme: dict, model: str | None) -> dict:
    return {
        "candidate_fingerprint": _fingerprint(theme["evidence_ids"]),
        "title": theme["title"],
        "signal_summary": theme["short_description"] or theme["observed_change"] or theme["title"],
        "observed_change": theme["observed_change"] or theme["short_description"] or theme["title"],
        "why_now": theme["why_it_matters"] or "（記事群から抽出）",
        "question": theme["question"] or f"{theme['title']}は継続するか",
        "actors": theme["actors"], "regions": theme["regions"], "themes": theme["topics"], "business_relevance": [],
        "candidate_type": theme["candidate_type"], "change_type": theme["change_type"],
        "source_external_signal_ids": theme["evidence_ids"],
        "first_seen_at": theme["first_seen_at"], "latest_signal_at": theme["latest_signal_at"],
        "signal_count": len(theme["evidence_ids"]), "status": "NEW", "is_demo": False,
        # 由来: 既存のgeneration_model（自由記述text）の接頭辞に持つ。DB変更なし
        "generation_model": f"{ORIGIN}/{model or 'unknown'}"}


# ---------------------------------------------------------------------------
# 探索の本体
# ---------------------------------------------------------------------------

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(prompt: str, source_ids: list, period_days: int | None) -> str:
    raw = "|".join([norm_text(prompt), ",".join(sorted(source_ids)), str(period_days or "")])
    return hashlib.sha256(raw.encode()).hexdigest()


def resolve_scope(client, source_ids: list | None, source_set_id: str | None) -> dict:
    """探索対象のSource。未指定なら全登録Source＋既存Article全体。archived Sourceは除く。"""
    ids = list(dict.fromkeys(source_ids or []))
    if source_set_id:
        ids +=[s["source_id"] for s in sets.resolve_set_sources(client, source_set_id) if not s.get("archived_at")]
        ids = list(dict.fromkeys(ids))
    if not ids:
        return {"mode": "all", "source_ids": [], "domains": set(), "source_count": 0}
    sources, domains = research.resolve_sources_and_domains(client, ids)
    live = [s["source_id"] for s in sources if not s.get("archived_at")]
    return {"mode": "selected", "source_ids": live, "domains": domains, "source_count": len(live)}


def _prepare_for_display(client, rows: list) -> list:
    now = _now()
    for r in rows:
        att = explorer.compute_attention(r, now=now)
        r["attention"] = att
        r["buckets"] = explorer.classify_buckets(r, att, now=now)
        r["related_article_count"] = r.get("signal_count") or 0
        explorer.decorate_candidate(r)
    explorer.attach_evidence_articles(client, rows, per_candidate=8)
    return rows


def discover(client, azure_client, model, *, prompt: str, source_ids: list | None = None,
             source_set_id: str | None = None, period_days: int | None = None,
             progress=None, now: datetime | None = None, use_cache: bool = True) -> dict:
    """戻り値は本モジュール冒頭の説明参照。progress(step_key) は段階が始まるたびに呼ばれる。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise DiscoveryError("探索したい内容を入力してください。")
    if azure_client is None:   # 入力の解釈・展開・テーマ整理は全てAIを使う。「該当なし」と誤解させないよう最初に止める
        raise DiscoveryError("AI分析エンジンが設定されていないため、テーマ探索を実行できません。")
    now = now or _now()
    step = progress or (lambda key: None)
    scope = resolve_scope(client, source_ids, source_set_id)
    if scope["mode"] == "selected" and not scope["source_ids"]:
        raise DiscoveryError("選択された情報ソースが利用できません（アーカイブ済みの可能性があります）。")

    key = _cache_key(prompt, scope["source_ids"], period_days)
    if use_cache:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit and time.time() - hit["at"] < CACHE_TTL_SECONDS:
            ids = hit["candidate_ids"]
            rows = client.select("geo_theme_candidates", {"select": "*", "candidate_id": "in.(" + ",".join(ids) + ")"}) if ids else []
            live = [r for r in rows if r.get("status") != "DISMISSED"]
            if len(live) == len(ids):                       # 削除されていなければ再計算しない
                result = dict(hit["result"])
                result["candidates"] = _prepare_for_display(client, live)
                result["from_cache"] = True
                for k, _ in STEPS:
                    step(k)
                return result

    # 1. 入力を解釈（既存のinterpret_query ＋ Query Expansion を並列実行）
    step("interpret")
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(explorer.interpret_query, azure_client, model, prompt)
        f2 = ex.submit(query_expansion.expand_query, azure_client, model, prompt)
        interp, expansion = f1.result(), f2.result()
    filters = interp["filters"]
    period = int(period_days or filters.get("period_days") or DEFAULT_PERIOD_DAYS)
    period = max(1, min(period, MAX_PERIOD_DAYS))                    # 入力に期間指定があればそれを優先
    search_terms = build_search_terms(filters, expansion)

    # 2. 関連記事を検索
    step("search")
    since = (now - timedelta(days=period)).isoformat()
    raw = retrieve_articles(client, search_terms, source_ids=scope["source_ids"], domains=scope["domains"], since_iso=since)
    picked, matched_n = select_relevant(raw, search_terms)
    stats = {"articles_considered": len(raw), "articles_relevant": matched_n, "articles_used": len(picked)}
    base = {"version": DISCOVERY_VERSION, "prompt": prompt, "period_days": period,
            "source_scope": {"mode": scope["mode"], "source_count": scope["source_count"]},
            "interpretation": {"keywords": filters.get("keywords"), "region": filters.get("region"),
                               "used_llm": interp.get("used_llm"), "error": interp.get("error")},
            "query_expansion": {k: expansion.get(k) for k in ("version", "translated", "expanded_terms", "used_llm", "error")},
            "stats": stats, "candidates": [], "existing_updates": [], "message": None, "from_cache": False}

    # 3. テーマ候補を整理
    step("cluster")
    if matched_n < MIN_MATCHED_ARTICLES:
        base["message"] = NO_RESULT_MESSAGE
        base["reason"] = f"期間（直近{period}日）内の関連記事が{matched_n}件で、テーマにまとめるには不足しています。"
        return base                                                  # 材料不足: LLMを呼ばず、候補も作らない
    themes, llm_reason = cluster_with_llm(azure_client, model, prompt, picked)
    existing = load_existing(client)
    created, updates, skipped_deleted = [], [], 0
    for th in themes:
        dup = find_duplicate(th["title"], th["evidence_ids"], existing)
        if dup is None:
            row_in = _row_for(th, model)
            try:
                created.extend(client.insert("geo_theme_candidates", [row_in]))
            except Exception as exc:  # noqa: BLE001 — fingerprint重複(同時実行)は既存扱いにして続ける
                msg = str(exc)
                if "409" in msg or "duplicate" in msg.lower():
                    continue
                raise
            existing["candidates"].append({**row_in, "candidate_id": created[-1]["candidate_id"]})   # 同一実行内の重複防止
        elif dup["type"] == "deleted_candidate":
            skipped_deleted += 1                                     # 削除済みと同じテーマは復活させない
        else:
            new_articles = [_article_view(a) for a in th["evidence"] if a["external_signal_id"] not in dup["existing_signal_ids"]]
            updates.append({"kind": "existing_update", "existing": {"type": dup["type"], "id": dup["id"], "title": dup["title"],
                                                                     "status": dup.get("status")},
                            "proposed_title": th["title"], "summary": th["short_description"],
                            "new_articles": new_articles, "has_new_articles": bool(new_articles)})
    stats.update({"themes_proposed": len(themes), "themes_created": len(created),
                  "existing_updates": len(updates), "skipped_deleted": skipped_deleted})
    base["candidates"] = _prepare_for_display(client, created)
    base["existing_updates"] = updates
    if not created and not updates:
        base["message"] = NO_RESULT_MESSAGE
        base["reason"] = llm_reason or ("関連記事から『継続して見る価値のある変化』としてまとまるものがありませんでした。"
                                        if not skipped_deleted else "削除済みのテーマと同じものだけでした。")
    result_for_cache = {k: v for k, v in base.items() if k != "candidates"}
    with _CACHE_LOCK:
        _CACHE[key] = {"at": time.time(), "candidate_ids": [r["candidate_id"] for r in created], "result": result_for_cache}
        if len(_CACHE) > 100:                                        # 古いものから捨てる
            for k in sorted(_CACHE, key=lambda x: _CACHE[x]["at"])[:20]:
                _CACHE.pop(k, None)
    return base


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# 実際の処理段階と一致する進行状況を返すジョブ（疑似進捗は作らない）
# ---------------------------------------------------------------------------

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="theme-discovery")
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _new_steps() -> list:
    return [{"key": k, "label": label, "state": "pending", "started_at": None, "ended_at": None} for k, label in STEPS]


def _set_step(job: dict, key: str) -> None:
    """指定段階をrunningにし、それより前の段階をdoneにする（実処理が段階に入ったときだけ呼ばれる）。"""
    now = time.time()
    reached = False
    for s in job["steps"]:
        if s["key"] == key:
            s["state"], s["started_at"], reached = "running", now, True
        elif not reached and s["state"] != "done":
            s["state"], s["ended_at"] = "done", now


def start_job(client, azure_client, model, **params) -> str:
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "status": "running", "steps": _new_steps(), "result": None, "error": None,
           "created_at": time.time(), "prompt": params.get("prompt")}
    with _JOBS_LOCK:
        for jid in [j for j, v in _JOBS.items() if time.time() - v["created_at"] > 3600]:
            _JOBS.pop(jid, None)
        _JOBS[job_id] = job

    def run():
        try:
            job["result"] = discover(client, azure_client, model, progress=lambda k: _set_step(job, k), **params)
            now = time.time()
            for s in job["steps"]:
                if s["state"] != "done":
                    s["state"], s["ended_at"] = "done", now
            job["status"] = "done"
        except DiscoveryError as exc:
            job["status"], job["error"] = "error", str(exc)
        except Exception as exc:  # noqa: BLE001 — 想定外の失敗も画面に理由を返す
            job["status"], job["error"] = "error", f"テーマ探索に失敗しました: {str(exc)[:160]}"

    _EXECUTOR.submit(run)
    return job_id


def get_job(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return None
    return {k: job[k] for k in ("job_id", "status", "steps", "result", "error")}
