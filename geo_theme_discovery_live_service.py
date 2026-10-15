"""Theme Discovery「本当に探索する」拡張（ユーザー指示、2026-10-07）。

既存のgeo_theme_discovery_service.discover()は「直近の記事DB（geo_external_signals）を
検索してTheme Candidateを提案する」オンデマンド機能として既に実装済み（V1_1）。本モジュールは
その手前に2つの段階を足すだけで、discover()自体・geo_theme_explorer_service・
geo_external_signal_serviceはどれも変更しない（Freeze対象のSOURCE_INTELLIGENCE_V1_1に触れない）。

  1. チャットで探索要件をすり合わせる（自然言語の対話→構造化Brief。DB永続化はしない
     ステートレス設計——フロントエンドが会話履歴を保持し、毎ターン全履歴を送る）。
  2. Briefから情報ソースDBの中のふさわしいSourceを自動提案する。新しいAPIは作らず、
     既存の `POST /api/geo/source-library/recommend`（geo_source_recommendation_service.
     recommend_sources、LLM不要）をフロントエンドが直接呼ぶ（brief.summary/keywords/regionを
     title/themes・keywords/regionsへそのまま渡すだけ）。
  3. 選ばれたSourceについて、必要ならWeb検索（geo_expert_research_service、
     Source Researchと同じdomain限定パターン）でDB未収録の最新情報を取得し、
     既存のgeo_external_signal_service.normalize_url/compute_dedup_key/bulk_upsert_signals
     でgeo_external_signalsへ取り込む（provider="source_discovery_web"、既存のGDELT等とは
     別providerのため、Collection Health/Freshness判定やPhase D/D.1のScheduler分類には
     一切影響しない）。取り込み後、既存discover()を（新しく取り込んだ記事を拾えるよう
     短いperiod_daysで）そのまま呼ぶ——discover()自体は無改修。
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

import geo_expert_research_service as expert_research
import geo_external_signal_service as signal_svc
import geo_llm_common
import geo_source_research_service as source_research
import geo_theme_discovery_service as discovery

WEB_PROVIDER = "source_discovery_web"

BRIEF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["assistant_reply", "brief", "ready"],
    "properties": {
        "assistant_reply": {"type": "string", "description": "ユーザーへの返信（日本語）。"
                            "要件が曖昧なら聞き返す質問を含める。十分なら要約して確認を促す。"},
        "brief": {
            "type": "object", "additionalProperties": False,
            "required": ["keywords", "exclude_mainstream", "region", "period_days", "summary", "actors", "angle"],
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                             "description": "探索したい語・概念（英語/日本語どちらでも可、重複なし）"},
                "exclude_mainstream": {"type": "boolean", "description": "『ニッチなものを』"
                                       "『一般メディアで既に話題のものは除いて』という趣旨の発言があればtrue"},
                "region": {"type": ["string", "null"], "description": "対象地域（無指定ならnull）"},
                "period_days": {"type": "integer", "minimum": 1, "maximum": 180,
                                "description": "探索対象の期間（日数）。指定が無ければ30"},
                "summary": {"type": "string", "description": "探索条件の短い要約（1-2文、日本語）。"
                            "discover()へそのまま渡すプロンプト文になる。アクターと見たい角度も含める"},
                "actors": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                           "description": "主要なアクター（政府・政党・企業・団体・人物）。無ければ空"},
                "angle": {"type": ["string", "null"], "description": "最も知りたい角度（アクターの意図/制度上の制約/"
                          "過去の類似事例/先行指標/反対の見方/日本企業への影響のどれか。無指定ならnull）"},
            },
        },
        "ready": {"type": "boolean", "description": "この条件でソース提案・探索に進んでよいならtrue。"
                  "まだ要件が曖昧で聞き返した場合はfalse"},
    },
}

_SYSTEM_PROMPT = (
    "あなたは地政学インテリジェンス部門の専門アナリストで、「テーマ探索」の要件をユーザーとすり合わせる"
    "ガイド役です。単に聞き返すのではなく、専門家として、探索の質が上がる論点を先回りして示してください。\n"
    "【確認する項目（1ターンに1つだけ、足りないものから順に聞く）】\n"
    "1. 対象: 何の話題か、どの地域・国・分野か。\n"
    "2. 主要アクター: 誰（どの政府・政党・企業・団体・人物）の動きが鍵か。\n"
    "3. 見たい角度: 次のうち何を最も知りたいか —— アクターの意図と能力／制度上の制約（法律・日程・予算・司法）／"
    "過去の類似事例との比較／先行指標（観測可能な数値・日程）／反対の見方／日本企業（消費財・食品・飲料）への影響。\n"
    "4. 期間と新規性: いつからの動きか、一般メディアで既に話題のものは除くか。\n"
    "【聞き方】質問には、専門家としての具体的な選択肢を2〜3個添える（例:『この局面では、(a)郡レベルの選挙管理の"
    "変更、(b)資金の流れ、(c)制度上の期限、のどれが鍵になりそうですか』）。ユーザーが選びやすいようにする。\n"
    "【ready】対象・主要アクター（または見たい角度）・期間の3つが分かればtrue。まだ不足していれば、assistant_replyで"
    "不足している1項目だけを聞き、readyはfalse。十分ならassistant_replyで探索条件を短く要約し、readyをtrueにする。\n"
    "【summary】そのままテーマ探索エンジンへ渡す1-2文の日本語。対象・アクター・見たい角度を含める"
    "（例:『ジョージア州の中間選挙で、郡の選挙管理委員会の人事・手続き変更と、その先行指標を、一般メディアが"
    "まだ報じていないニッチな動きとして探す』）。ユーザーが言っていない事実・アクターを足さない。"
)


def _render_history(history: list[dict], message: str) -> str:
    lines = []
    for turn in history or []:
        role = "ユーザー" if turn.get("role") == "user" else "アシスタント"
        lines.append(f"{role}: {turn.get('content', '')}")
    lines.append(f"ユーザー: {message}")
    return "\n".join(lines)


def refine_brief(azure_client, model: str, *, history: list[dict], message: str) -> dict:
    """チャット1ターン分。DBには何も保存しない——呼び出し元（フロントエンド）が
    historyを保持し、次回呼び出し時に全履歴を渡す。"""
    message = (message or "").strip()
    if not message:
        raise discovery.DiscoveryError("メッセージを入力してください")
    if azure_client is None:
        raise discovery.DiscoveryError("AI分析エンジンが設定されていないため、チャットでの要件すり合わせはできません")
    transcript = _render_history(history, message)
    try:
        # max_attempts=3: 形式が合わない応答（briefを文章で返す等）のとき、エラー内容を添えて1回だけ訂正を依頼する
        result = geo_llm_common.call_llm_structured(
            azure_client, model, _SYSTEM_PROMPT, transcript, BRIEF_SCHEMA,
            "theme_discovery_live_brief", max_attempts=3)
    except geo_llm_common.GeoLLMError as exc:
        # 500にせず、画面に理由が出る形で返す（利用者はもう一度送信すればよい）
        raise discovery.DiscoveryError(
            "AIの応答を、探索条件の形に整理できませんでした。もう一度、同じ内容を送信してください。"
            f"（{str(exc)[:80]}）") from exc
    return result["data"]


# ---------------------------------------------------------------------------
# Web検索結果 → geo_external_signals への取り込み（既存の正規化・dedup・upsertを再利用）
# ---------------------------------------------------------------------------

def _citation_to_raw(citation: dict, *, now: datetime) -> dict | None:
    url = (citation.get("url") or "").strip()
    if not url:
        return None
    try:
        domain = urlparse(url).netloc.lower()
    except ValueError:
        domain = ""
    return {"url": url, "title": citation.get("title") or url, "domain": domain,
            "_fetched_at": now, "published_at": citation.get("published_at") or None}


def _normalize_web_citation(raw: dict) -> dict:
    """normalize_article（GDELT専用、seendateが必須）は使わず、Web検索citation専用の
    正規化をここに書く。真の公開日時は分からないため、取得時刻をpublished_atとして使う
    （timestamp_source='unknown'——既存のgeo_external_signalsのCHECK制約が許可する3値
    publisher/gdelt_seen/unknownのうち、GDELT由来ではなく真の公開日時でもない以上、
    'unknown'が最も正直な表現。provider='source_discovery_web'自体で由来は判別できる）。"""
    url = raw["url"]
    title = raw.get("title")
    domain = raw.get("domain")
    normalized_url = signal_svc.normalize_url(url)
    normalized_title = signal_svc._normalize_title(title) if title else ""
    dedup_key = signal_svc.compute_dedup_key(
        canonical_url=url or None, normalized_url=normalized_url or None,
        source_domain=domain, normalized_title=normalized_title)
    now_iso = raw["_fetched_at"].isoformat()
    return {
        "provider": WEB_PROVIDER, "provider_record_id": None, "signal_type": "article",
        "title": title, "summary": None, "url": url, "canonical_url": url,
        "normalized_url": normalized_url or None, "source_domain": domain, "source_name": domain,
        "source_country": None, "source_language": None,
        # 検索結果のメタデータに公開日があればそれを使う（無ければ取得時刻・timestamp_source=unknownで日付不明）
        "published_at": raw.get("published_at") or now_iso,
        "timestamp_source": "publisher" if raw.get("published_at") else "unknown", "entities": [], "countries": [], "tone": None,
        "coverage_value": None, "source_role": "discovery", "evidence_status": "unverified",
        "raw_metadata": {"url": url, "title": title, "domain": domain, "fetched_at": now_iso},
        "validation_status": "unreviewed", "knowledge_promotion_status": "not_promoted",
        "dedup_key": dedup_key,
    }


def gather_web_signals_for_brief(client, azure_client, model: str, *, brief: dict,
                                  source_ids: list, is_demo: bool = False,
                                  now: datetime | None = None) -> dict:
    """選択Sourceのdomainに限定したWeb検索（既存geo_source_research_serviceと同じ
    site:限定＋後段domainフィルタのパターン）を行い、新規に見つかった記事を
    geo_external_signalsへ取り込む。戻り値: {fetched, inserted, updated, errors}。"""
    now = now or datetime.now(timezone.utc)
    if not source_ids:
        return {"fetched": 0, "inserted": 0, "updated": 0, "errors": ["Web検索の対象Sourceが選択されていません"]}
    if azure_client is None:
        return {"fetched": 0, "inserted": 0, "updated": 0, "errors": ["AI分析エンジンが未設定のためWeb検索は行っていません"]}

    _, domains = source_research.resolve_sources_and_domains(client, source_ids)
    if not domains:
        return {"fetched": 0, "inserted": 0, "updated": 0, "errors": ["選択Sourceのdomainを解決できませんでした"]}

    keywords = [k for k in (brief.get("keywords") or []) if k]
    question = " ".join(keywords) or (brief.get("summary") or "")
    site_clause = " OR ".join(f"site:{d}" for d in sorted(domains)[:8])
    errors: list = []
    citations: list = []
    try:
        res = expert_research.run_external_research(
            azure_client, model, question=f"{question} ({site_clause})",
            context=f"探索条件: {brief.get('summary') or question}。"
                    f"検索対象は次のdomainに限定: {', '.join(sorted(domains)[:8])}",
            search_lens="選択された情報ソースのdomain内の最新記事")
        for c in res.get("citations", []):
            d = (urlparse(c.get("url") or "").netloc or "").lower()
            if not source_research._domain_allowed(d, domains):
                continue
            citations.append(c)
    except Exception as exc:  # noqa: BLE001 — Web検索の失敗で全体を止めない
        errors.append(f"Web検索に失敗しました: {exc}")

    normalized = []
    for c in citations:
        raw = _citation_to_raw(c, now=now)
        if raw is None:
            continue
        try:
            normalized.append(_normalize_web_citation(raw))
        except ValueError:
            continue
    upsert_result = signal_svc.bulk_upsert_signals(client, normalized, provider=WEB_PROVIDER, is_demo=is_demo)
    return {"fetched": len(citations), "inserted": upsert_result["inserted"],
            "updated": upsert_result["updated"], "errors": errors}


def run_live_discovery(client, azure_client, model: str, *, brief: dict, source_ids: list,
                       include_web: bool, is_demo: bool = False, now: datetime | None = None) -> dict:
    """探索を開始する。戻り値は {job_id, web}（webは常にNone。Web検索の件数は完了した探索結果
    result["web"] に入る）。

    - include_web=true（既定）: Web検索（選んだSourceに限定しない）＋保存済み記事から、記事数の
      足切りをせずにテーマを探す（geo_theme_discovery_web_service）。選んだSourceがあれば、そのdomain内の
      検索も追加し、保存済み記事もそのSourceのものに絞る。
    - include_web=false: 従来の保存済み記事だけを使うArticle Discovery（discover()、無改修）。"""
    import geo_theme_discovery_web_service as web_service   # 循環importを避けるため関数内で読み込む
    summary = (brief.get("summary") or "").strip()
    if not summary:
        raise discovery.DiscoveryError("探索条件が未確定です。チャットで要件をすり合わせてください")

    if include_web:
        job_id = web_service.start_web_job(
            client, azure_client, model, brief=brief, source_ids=source_ids, is_demo=is_demo)
        return {"job_id": job_id, "web": None}

    period_days = int(brief.get("period_days") or discovery.DEFAULT_PERIOD_DAYS)
    job_id = discovery.start_job(
        client, azure_client, model, prompt=summary, source_ids=source_ids,
        source_set_id=None, period_days=period_days)
    return {"job_id": job_id, "web": None}
