"""地政学インテリジェンス基盤: Signal Engine（指示書11章）。

数値計算とルール判定はコードで実装し、LLMには計算させない。3つの計算方式を提供する。
LLMを使う方式（event_count_window用の分類はgeo_event_extractor側、qualitative_llm_state）
でも、LLMには「分類」のみをさせ、件数集計・序数へのマッピング・baseline/deviation計算は
すべてこの後のコード側で行う。

- event_count_window: geo_eventsを指標コードで絞り込み、直近window_days件数を集計
  （MIL-01, MIL-02, MAR-01, CYB-01, ECO-01）
- qualitative_llm_state: 直近文書群をLLMで固定選択肢の状態に分類し、コード側で序数へ変換
  （POL-01, DIP-02）
- regex_level_from_document: 正規表現で文書から明示的なレベル表記を抽出（LLM不使用、JPN-01）
- market_data_api: 無料の公開API（台湾証券取引所OpenAPI・exchangerate-api.comの無料エンドポイント）
  から直接数値を取得する。文書/Event経由ではない「生データ直結」方式（LLM不使用、MKT-01）
- financial_disclosure_parse: 公開されている財務開示ページ（TSMC IR月次売上）のHTML表を
  決定的にパースする（LLM不使用、SCM-01）

値が不足する場合は0として扱わず insufficient_data=true を返す。実コネクタ・実文書が
無い場合や根拠がis_mockの場合は、Observation自体もis_mock=trueとし、モックデータを
実データのように見せない。
"""

import re
from datetime import datetime, timedelta, timezone
from statistics import mean

import requests
from lxml import html as lxml_html

from geo_llm_common import call_llm_structured

REQUEST_TIMEOUT = 30

# Phase2B: 閾値超過アラート（direction/operator対応）
THRESHOLD_OPERATORS = ("above", "below", "gte", "lte")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _mean_raw_value(observations: list, since: datetime) -> float | None:
    values = [
        o["raw_value"] for o in observations
        if o.get("raw_value") is not None
        and datetime.fromisoformat(o["as_of"].replace("Z", "+00:00")) >= since
    ]
    return mean(values) if values else None


def _compute_baselines(client, indicator_id: str, now: datetime) -> dict:
    past = client.select("geo_observations", {
        "select": "raw_value,as_of", "indicator_id": f"eq.{indicator_id}",
        "order": "as_of.desc", "limit": "200",
    })
    return {
        "baseline_7d": _mean_raw_value(past, now - timedelta(days=7)),
        "baseline_30d": _mean_raw_value(past, now - timedelta(days=30)),
        "baseline_90d": _mean_raw_value(past, now - timedelta(days=90)),
    }


def ensure_rule_version(client, indicator_id: str, method: str, extra_definition: dict = None) -> dict:
    """指標の最新rule_versionを返す。まだ無ければ、指定methodで案(is_draft=true)のversion=1を
    自動作成する（geo_official_crawler.pyの初回クロール時・APIの初回再計算時にルールversionの
    事前手動登録を必須にしないための自己修復）"""
    existing = client.select("geo_indicator_rule_versions", {
        "select": "*", "indicator_id": f"eq.{indicator_id}", "order": "version.desc", "limit": "1",
    })
    if existing:
        return existing[0]
    rule_definition = {"method": method, **(extra_definition or {})}
    inserted = client.insert("geo_indicator_rule_versions", [{
        "indicator_id": indicator_id, "version": 1, "rule_definition": rule_definition, "is_draft": True,
    }])
    return inserted[0]


def _insufficient_observation(indicator_id: str, rule_version_id: str, now: datetime) -> dict:
    return {
        "indicator_id": indicator_id,
        "rule_version_id": rule_version_id,
        "as_of": _iso(now),
        "raw_value": None,
        "insufficient_data": True,
        "baseline_7d": None, "baseline_30d": None, "baseline_90d": None,
        "deviation": None, "persistence": None, "novelty": None,
        "confidence": None, "data_quality": "insufficient_data",
        "qualitative_label": None, "rationale": None,
        "source_freshness_hours": None,
        "evidence_id": None,
        "is_mock": False,
    }


def compute_event_count_observation(client, indicator_id: str, indicator_code: str, rule_version_id: str,
                                      theme_id: str, window_days: int = 30) -> dict:
    """theme_id配下・indicator_code一致のgeo_eventsを直近window_days件数集計する"""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)

    events = client.select("geo_events", {
        "select": "*", "theme_id": f"eq.{theme_id}", "indicator_code": f"eq.{indicator_code}",
        "created_at": f"gte.{_iso(since)}",
    })

    if not events:
        return _insufficient_observation(indicator_id, rule_version_id, now)

    event_ids = [e["event_id"] for e in events]
    evidence = client.select("geo_evidence", {
        "select": "*", "event_id": f"in.({','.join(event_ids)})",
    })
    document_ids = sorted({ev["document_id"] for ev in evidence if ev.get("document_id")})

    is_mock = False
    if document_ids:
        docs = client.select("geo_documents", {
            "select": "document_id,is_mock", "document_id": f"in.({','.join(document_ids)})",
        })
        is_mock = any(d.get("is_mock") for d in docs)

    raw_value = float(len(events))
    confidences = [e["confidence"] for e in events if e.get("confidence") is not None]
    confidence = mean(confidences) if confidences else None

    baselines = _compute_baselines(client, indicator_id, now)
    deviation = (raw_value - baselines["baseline_30d"]) if baselines["baseline_30d"] is not None else None
    latest_evidence_id = evidence[0]["evidence_id"] if evidence else None

    return {
        "indicator_id": indicator_id,
        "rule_version_id": rule_version_id,
        "as_of": _iso(now),
        "raw_value": raw_value,
        "insufficient_data": False,
        **baselines,
        "deviation": deviation, "persistence": None, "novelty": None,
        "confidence": confidence,
        "data_quality": "mock" if is_mock else "event_count_window",
        "qualitative_label": None, "rationale": None,
        "source_freshness_hours": None,
        "evidence_id": latest_evidence_id,
        "is_mock": is_mock,
    }


def compute_qualitative_observation(client, azure_client, model: str, indicator_id: str, rule_version_id: str,
                                      theme_id: str, source_id: str, states: list[dict],
                                      document_limit: int = 3) -> dict:
    """直近document_limit件の文書をLLMで固定状態(states)へ分類する。
    states: [{"label": str, "value": int, "description": str}, ...]（valueは序数、コード側で使う）"""
    now = datetime.now(timezone.utc)
    docs = client.select("geo_documents", {
        "select": "*", "theme_id": f"eq.{theme_id}", "source_id": f"eq.{source_id}",
        "order": "fetched_at.desc", "limit": str(document_limit),
    })
    if not docs:
        return _insufficient_observation(indicator_id, rule_version_id, now)

    combined_text = "\n\n---\n\n".join((d.get("extracted_text") or "")[:4000] for d in docs)
    if not combined_text.strip():
        return _insufficient_observation(indicator_id, rule_version_id, now)

    state_labels = [s["label"] for s in states]
    schema = {
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": state_labels},
            "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "description": "判断根拠となった本文の記述の要約"},
        },
        "required": ["state", "rationale"],
    }
    options_text = "\n".join(f"- {s['label']}: {s['description']}" for s in states)
    system_prompt = f"""あなたは地政学インテリジェンス基盤の指標分類アシスタントです。
与えられた文書群の内容から、現在の状態を次の選択肢から最も近いもの1つだけ選んでください。

選択肢:
{options_text}

厳守事項:
- 本文に記載されている内容のみを根拠にする。書かれていないことを推測して結論づけない。
- リスクレベルや重大性の判定・スコア計算はしない。あくまで記述内容の分類のみを行う。
- rationaleには判断根拠となった本文の記述を簡潔に示す（創作しない）。
"""
    result = call_llm_structured(azure_client, model, system_prompt, combined_text, schema, "geo_qualitative_state")
    data = result["data"]
    state_value_map = {s["label"]: s["value"] for s in states}
    raw_value = state_value_map.get(data["state"])

    is_mock = any(d.get("is_mock") for d in docs)
    baselines = _compute_baselines(client, indicator_id, now)
    deviation = (raw_value - baselines["baseline_30d"]) if (raw_value is not None and baselines["baseline_30d"] is not None) else None

    return {
        "indicator_id": indicator_id,
        "rule_version_id": rule_version_id,
        "as_of": _iso(now),
        "raw_value": float(raw_value) if raw_value is not None else None,
        "insufficient_data": raw_value is None,
        **baselines,
        "deviation": deviation, "persistence": None, "novelty": None,
        "confidence": data.get("confidence"),
        "data_quality": "mock" if is_mock else "qualitative_llm_state",
        "qualitative_label": data.get("state"), "rationale": data.get("rationale"),
        "source_freshness_hours": None,
        "evidence_id": None,
        "is_mock": is_mock,
    }


# 外務省海外安全ホームページ「危険情報」の明示的なレベル表記をそのまま抽出する
# （LLMを使わず正規表現で決定的に判定。指示書の絶対原則3に最も忠実な方式）
_MOFA_LEVEL_PATTERNS = [
    (4, re.compile(r"レベル\s*4|退避勧告")),
    (3, re.compile(r"レベル\s*3|渡航中止勧告")),
    (2, re.compile(r"レベル\s*2|不要不急の?渡航(?:は)?中止")),
    (1, re.compile(r"レベル\s*1|十分注意")),
]
_MOFA_LEVEL_LABELS = {
    0: "危険情報なし", 1: "レベル1(十分注意)", 2: "レベル2(不要不急渡航中止)",
    3: "レベル3(渡航中止勧告)", 4: "レベル4(退避勧告)",
}


def compute_regex_level_observation(client, indicator_id: str, rule_version_id: str,
                                      theme_id: str, source_id: str) -> dict:
    """直近1件の文書から正規表現でレベルを抽出する（JPN-01用）"""
    now = datetime.now(timezone.utc)
    docs = client.select("geo_documents", {
        "select": "*", "theme_id": f"eq.{theme_id}", "source_id": f"eq.{source_id}",
        "order": "fetched_at.desc", "limit": "1",
    })
    if not docs:
        return _insufficient_observation(indicator_id, rule_version_id, now)

    text = docs[0].get("extracted_text") or ""
    level = 0
    matched = False
    for value, pattern in _MOFA_LEVEL_PATTERNS:
        if pattern.search(text):
            level, matched = value, True
            break

    is_mock = docs[0].get("is_mock", False)
    baselines = _compute_baselines(client, indicator_id, now)
    deviation = (float(level) - baselines["baseline_30d"]) if baselines["baseline_30d"] is not None else None

    return {
        "indicator_id": indicator_id,
        "rule_version_id": rule_version_id,
        "as_of": _iso(now),
        "raw_value": float(level),
        "insufficient_data": False,
        **baselines,
        "deviation": deviation, "persistence": None, "novelty": None,
        "confidence": 1.0 if matched else None,
        "data_quality": "mock" if is_mock else "regex_level_from_document",
        "qualitative_label": _MOFA_LEVEL_LABELS[level], "rationale": None,
        "source_freshness_hours": None,
        "evidence_id": None,
        "is_mock": is_mock,
    }


def _threshold_matches(value: float, operator: str, threshold_value: float) -> bool:
    if operator == "above":
        return value > threshold_value
    if operator == "below":
        return value < threshold_value
    if operator == "gte":
        return value >= threshold_value
    if operator == "lte":
        return value <= threshold_value
    raise ValueError(f"未対応のoperatorです: {operator}")


def evaluate_threshold(observation: dict, rule_definition: dict) -> str | None:
    """rule_definition["thresholds"]（[{severity, compare_field, operator, value}, ...]）を
    criticalから順に評価し、最初に一致したseverityを返す。未定義・insufficient_dataならNone。
    thresholdsが無いIndicatorは常にNone（既存のIndicatorに影響しない、後方互換）。"""
    if observation.get("insufficient_data"):
        return None
    thresholds = rule_definition.get("thresholds") or []
    ordered = sorted(thresholds, key=lambda t: 0 if t.get("severity") == "critical" else 1)
    for threshold in ordered:
        compare_field = threshold.get("compare_field", "raw_value")
        value = observation.get(compare_field)
        operator = threshold.get("operator")
        threshold_value = threshold.get("value")
        if value is None or operator not in THRESHOLD_OPERATORS or threshold_value is None:
            continue
        if _threshold_matches(value, operator, threshold_value):
            return threshold.get("severity")
    return None


def create_alert_if_needed(client, *, observation: dict, severity: str | None) -> dict | None:
    """severityがNoneならAlertを作らない。同一observation_idで既にAlertがあれば何もしない
    （geo_indicator_alerts.observation_idのunique制約と対で、再計算を複数回実行しても
    重複作成されないことを保証する）。"""
    if severity is None:
        return None
    existing = client.select("geo_indicator_alerts", {
        "select": "*", "observation_id": f"eq.{observation['observation_id']}", "limit": "1",
    })
    if existing:
        return existing[0]
    return client.insert("geo_indicator_alerts", [{
        "indicator_id": observation["indicator_id"], "rule_version_id": observation["rule_version_id"],
        "observation_id": observation["observation_id"], "severity": severity,
        "triggered_at": observation["as_of"],
    }])[0]


# 台湾証券取引所(TWSE) OpenAPI（無料・登録不要の公式公開API）とexchangerate-api.comの
# 無料エンドポイント（open.er-api.com、無料・キー不要）から直接数値を取得する。
# 文書クロール・Event抽出を経由しない「生データ直結」方式のため、geo_documents/geo_events
# には保存しない（代わりにdata_qualityで出典を明示する）。
TWSE_INDEX_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
FX_RATE_URL = "https://open.er-api.com/v6/latest/USD"
_TAIEX_INDEX_NAME = "發行量加權股價指數"


def compute_market_data_observation(client, indicator_id: str, rule_version_id: str,
                                      proxies: dict, verify: bool) -> dict:
    """TAIEX（台湾加権指数）の当日騰落率の絶対値をボラティリティの簡易指標とする
    （MKT-01）。TWDレートは参考情報としてqualitative_labelに含める"""
    now = datetime.now(timezone.utc)

    try:
        resp = requests.get(TWSE_INDEX_URL, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        taiex_row = next((r for r in rows if r.get("指數") == _TAIEX_INDEX_NAME), None)
    except Exception:
        taiex_row = None

    try:
        fx_resp = requests.get(FX_RATE_URL, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT)
        fx_resp.raise_for_status()
        twd_rate = fx_resp.json().get("rates", {}).get("TWD")
    except Exception:
        twd_rate = None

    if not taiex_row:
        return _insufficient_observation(indicator_id, rule_version_id, now)

    try:
        close_value = float(taiex_row["收盤指數"].replace(",", ""))
        change_pct = float(taiex_row["漲跌百分比"])
        sign = 1 if taiex_row.get("漲跌") == "+" else (-1 if taiex_row.get("漲跌") == "-" else 0)
        change_pct *= sign
    except (KeyError, ValueError):
        return _insufficient_observation(indicator_id, rule_version_id, now)

    raw_value = abs(change_pct)  # 騰落率の絶対値をボラティリティの簡易プロキシとする
    label = f"TAIEX {close_value:,.2f} ({change_pct:+.2f}%)"
    if twd_rate:
        label += f" / USDTWD {twd_rate:.2f}"

    baselines = _compute_baselines(client, indicator_id, now)
    deviation = (raw_value - baselines["baseline_30d"]) if baselines["baseline_30d"] is not None else None

    return {
        "indicator_id": indicator_id,
        "rule_version_id": rule_version_id,
        "as_of": _iso(now),
        "raw_value": raw_value,
        "insufficient_data": False,
        **baselines,
        "deviation": deviation, "persistence": None, "novelty": None,
        "confidence": None,
        "data_quality": "market_data_api",
        "qualitative_label": label, "rationale": None,
        "source_freshness_hours": None,
        "evidence_id": None,
        "is_mock": False,
    }


# TSMC Investor Relationsの月次売上ページ（公開の財務開示、単純なHTML表）を直接パースする。
# 月次売上の前年同月比(YoY)を操業状況の簡易プロキシとする（SCM-01）
TSMC_MONTHLY_REVENUE_URL = "https://investor.tsmc.com/english/monthly-revenue/2026"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def compute_financial_disclosure_observation(client, indicator_id: str, rule_version_id: str,
                                               proxies: dict, verify: bool,
                                               url: str = TSMC_MONTHLY_REVENUE_URL) -> dict:
    """TSMC IRの月次売上表から、直近の実績月のYoY(%)を取得する（SCM-01）"""
    now = datetime.now(timezone.utc)

    try:
        resp = requests.get(url, proxies=proxies, verify=verify, timeout=REQUEST_TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        tree = lxml_html.fromstring(resp.content)
    except Exception:
        return _insufficient_observation(indicator_id, rule_version_id, now)

    latest_month, latest_revenue, latest_yoy = None, None, None
    for row in tree.xpath("//table//tr"):
        cells = [c.text_content().strip() for c in row.xpath("./td")]
        if len(cells) < 3:
            continue
        month, revenue_text, yoy_text = cells[0], cells[1], cells[-1]
        # 表の最終行は各月の合計("Total"等)であり、個別の月次実績ではないため除外する
        # （合計行を「最新月」として拾ってしまっていた不具合の修正）
        if month.strip().lower() in ("total", "合計", "累計"):
            continue
        revenue_text = revenue_text.replace(",", "")
        yoy_text = yoy_text.replace(",", "").replace("%", "")
        if not revenue_text or not yoy_text:
            continue
        try:
            latest_month, latest_revenue, latest_yoy = month, float(revenue_text), float(yoy_text)
        except ValueError:
            continue

    if latest_yoy is None:
        return _insufficient_observation(indicator_id, rule_version_id, now)

    baselines = _compute_baselines(client, indicator_id, now)
    deviation = (latest_yoy - baselines["baseline_30d"]) if baselines["baseline_30d"] is not None else None

    return {
        "indicator_id": indicator_id,
        "rule_version_id": rule_version_id,
        "as_of": _iso(now),
        "raw_value": latest_yoy,
        "insufficient_data": False,
        **baselines,
        "deviation": deviation, "persistence": None, "novelty": None,
        "confidence": None,
        "data_quality": "financial_disclosure_parse",
        "qualitative_label": f"{latest_month} revenue {latest_revenue:,.0f}M NTD (YoY {latest_yoy:+.1f}%)",
        "rationale": None,
        "source_freshness_hours": None,
        "evidence_id": None,
        "is_mock": False,
    }
