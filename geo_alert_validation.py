"""Monitoring Alertの Context Validation（Candidate Detection → Context Validation → Alert）。

背景: 旧Ruleは語のHit（invasion等）だけでAlert候補を出していたため、
「China invasion of Taiwan unlikely」のような否定・予測・仮定の見出しでも
military_action Alertを生成していた（2026-10-05 E2Eで発覚）。

方針:
- Candidate Detection（geo_monitoring_theme_service._EVENT_PATTERNS）が候補を出す。
- 本モジュールが各候補記事の文脈を検証し、「イベントが実際に発生したと記事が述べているか」
  だけを判定する。判定ラベル:
    actual_event            : 実際に起きた出来事（rule-based確認）
    llm_confirmed_actual    : 曖昧だったためLLMで確認し、実際に起きたと判定
    negated / hypothetical / forecast / historical / denial : Alertにしない（理由つき）
    uncertain_excluded      : 曖昧でLLM未使用/失敗/unclear → Alertにしない（精度優先）
- Rule-based guard優先。LLMは「曖昧」な記事だけに、評価1回あたり上限つきで使う
  （全記事をLLMにかけない）。LLMには「実際に発生したと述べているか」だけを判定させる。
- 全ての判定を理由つきで返す（監査可能）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import geo_llm_common

# SOURCE_INTELLIGENCE_V1 Freezeで固定したバージョン（Ruleやマーカーを変えたら必ず上げる）
ALERT_VALIDATION_VERSION = "alert_validation_v1"

MAX_LLM_CALLS_PER_EVALUATION = 6

# ---- 文脈マーカー（英語は大文字小文字無視、日本語はそのまま） ----
_DENIAL = re.compile(
    r"\b(?:denied|denies|deny|denying|dismiss(?:ed|es)\s+(?:the\s+)?(?:claims?|reports?|rumou?rs?)|"
    r"rejected\s+(?:the\s+)?(?:claims?|reports?)|baseless|unfounded|refuted|debunk(?:ed|s)?|"
    r"false\s+(?:claims?|reports?)|rumou?rs?\s+of)\b|否認|否定した|デマ|事実無根",
    re.I)
_NEGATION = re.compile(
    r"\b(?:unlikely|not\s+likely|improbable|no\s+sign\s+of|no\s+plans?|no\s+evidence|will\s+not|won't|"
    r"did\s+not|didn't|does\s+not|doesn't|has\s+not|hasn't|have\s+not|haven't|rules?\s+out|ruled\s+out|"
    r"never|cannot|can't|not\s+(?:going|expected|planning|about)\s+to|averted|avoided)\b|"
    r"起こりそうにない|可能性は低|見込みはない|考えにくい|ない見通し|否定的",
    re.I)
_HYPOTHETICAL = re.compile(
    r"\b(?:if|would|could|might|what\s+if|hypothetical(?:ly)?|scenario|suppose|in\s+case\s+of)\b|"
    r"もし|仮に|想定|シナリオ|場合に",
    re.I)
_HYPOTHETICAL_CASE_SENSITIVE = re.compile(r"\bmay\b")  # 大文字始まりの「May（5月）」を除くため小文字のみ
_FORECAST = re.compile(
    r"\b(?:expected\s+to|forecast(?:s|ed)?|predict(?:s|ed|ion)?|projected|likelihood|chances?\s+of|"
    r"probabilit(?:y|ies)|risks?\s+of|possibilit(?:y|ies)|prospects?\s+of|outlook|warns?\s+of|"
    r"fears?\s+of|prepar(?:e|es|ed|ing)\s+for|braces?\s+for|steel(?:s|ing)?\s+.{0,40}\bfor|"
    r"ready\s+for|readies\s+for|for\s+(?:an?|the)\s+(?:invasion|attack|strike|blockade|war))\b|"
    r"予測|見通し|可能性|懸念|リスク|備え|警戒",
    re.I)
_HISTORICAL = re.compile(
    # 「historic」「historical」という形容詞だけでは過去の回想と判定しない（現在の出来事を修飾する用法が多い）
    r"\b(?:anniversary|years?\s+ago|decades?\s+ago|history\s+of|retrospective|"
    r"remembers?|marks?\s+\d+\s+years?|in\s+(?:19\d\d|20[01]\d))\b|周年|年前",
    re.I)
_ACTUAL_VERB = re.compile(
    # 名詞と同形の語（ban/block/order/hold/sign/start/begin）は過去形だけを動詞とみなす
    # （現在形は下の_ACTUAL_VERB_AMBIGUOUS で「大文字の主語＋動詞＋目的語」の形のときだけ拾う）
    r"\b(?:adds?|added|imposes?|imposed|announces?|announced|launch(?:es|ed)?|fires?|fired|invad(?:es|ed)|"
    r"blocked|blockad(?:es|ed)|banned|tighten(?:s|ed)?|expand(?:s|ed)?|began|"
    r"started|held|conduct(?:s|ed)?|deploy(?:s|ed)?|stag(?:es|ed)|sends?|sent|seiz(?:es|ed)|"
    r"struck|sanctioned|suspend(?:s|ed)?|halt(?:s|ed)?|signed|"
    r"agrees?|agreed|resum(?:es|ed)|walk(?:s|ed)\s+out|break(?:s)?\s+off|broke\s+off|unveil(?:s|ed)|"
    r"ordered|restrict(?:s|ed)|enter(?:s|ed)|cross(?:es|ed)|intrud(?:es|ed)|scrambl(?:es|ed)|"
    r"kill(?:s|ed)|bombed|shelled|attacked|injur(?:es|ed|ing)|wound(?:s|ed|ing)|casualties|dead|destroy(?:s|ed)|"
    r"damag(?:es|ed|ing)|reported|confirmed)\b|"
    r"実施した|発表した|開始した|発射した|科した|発動した|導入した|決定した|合意した|停止した|再開した|封鎖した|侵攻した|攻撃した|派遣した",
    re.I)
# 候補語（sanctions / attack / strike）は名詞と動詞が同形のため、動詞として使われている形だけを拾う:
#   「US sanctions Iran ...」（大文字始まりの主語＋sanctions＋大文字始まりの目的語）、「X attacks the ...」等。
# 名詞用法（"amid US sanctions" "Ukrainian strikes on ..."）は実際の出来事の根拠にしない（→曖昧としてLLM確認）。
_ACTUAL_VERB_AMBIGUOUS = re.compile(
    r"\b[A-Z][A-Za-z.]*\s+(?:sanctions|attacks|strikes|bombs|shells|bans|blocks|orders|holds|signs|begins|starts)"
    r"\s+(?:[A-Z]|the\s|a\s|an\s|all\s|new\s)")

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n")


def _future_year_forecast(text: str, now: datetime) -> str | None:
    """『unlikely in 2027』のような将来年の言及（現在年より後）はForecast。"""
    for m in re.finditer(r"\b(20\d\d)\b", text):
        if int(m.group(1)) > now.year:
            return m.group(0)
    return None


def _context_sentence(text: str, span: tuple) -> str:
    """トリガー語を含む文（見出しなら全体）を返す。"""
    pos = 0
    for part in _SENT_SPLIT.split(text):
        start = text.find(part, pos)
        if start == -1:
            continue
        end = start + len(part)
        pos = end
        if start <= span[0] < max(end, start + 1):
            return part
    return text


def _find_marker(text: str, now: datetime) -> tuple | None:
    """(label, marker, reason) を返す。優先順: denial → negation → hypothetical → forecast → historical。"""
    for label, pat in (("denial", _DENIAL), ("negated", _NEGATION), ("hypothetical", _HYPOTHETICAL),
                       ("forecast", _FORECAST), ("historical", _HISTORICAL)):
        m = pat.search(text)
        if m:
            return label, m.group(0), _REASONS[label].format(marker=m.group(0))
    m = _HYPOTHETICAL_CASE_SENSITIVE.search(text)
    if m:
        return "hypothetical", m.group(0), _REASONS["hypothetical"].format(marker=m.group(0))
    yr = _future_year_forecast(text, now)
    if yr:
        return "forecast", yr, _REASONS["forecast"].format(marker=yr)
    return None


_REASONS = {
    "denial": "「{marker}」を含み、報道・主張の否認として書かれているため（実際の出来事ではない）",
    "negated": "「{marker}」を含み、出来事を否定する内容のため",
    "hypothetical": "「{marker}」を含み、仮定・シナリオの話のため",
    "forecast": "「{marker}」を含み、予測・可能性・備えの話で、実際に起きたとは述べていないため",
    "historical": "「{marker}」を含み、過去の回想・歴史的言及のため",
}

_LLM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["occurred", "reason"],
    "properties": {"occurred": {"type": "string", "enum": ["yes", "no", "unclear"]},
                   "reason": {"type": "string"}},
}
_LLM_PROMPT = (
    "あなたはニュース記事の事実確認アシスタントです。与えられた記事（見出しと要約）が、指定された"
    "イベント種別の出来事が『実際に発生した』と述べているかだけを判定してください。"
    "予測・可能性・仮定・否定・否認・過去の回想・準備の話は no。実際に起きたと明確に述べていれば yes。"
    "判断できなければ unclear。記事に書かれていない事実を推測で補わないこと。reasonは1文で。")


class AlertValidator:
    """Event系Alert候補の文脈検証。LLMは曖昧な記事だけ、上限つきで使う。"""

    def __init__(self, azure_client=None, model: str | None = None, *,
                 max_llm_calls: int = MAX_LLM_CALLS_PER_EVALUATION, now: datetime | None = None):
        self.azure_client = azure_client
        self.model = model
        self.max_llm_calls = max_llm_calls
        self.llm_calls = 0
        self.now = now or datetime.now(timezone.utc)
        self._cache: dict = {}

    def validate(self, article: dict, pattern: re.Pattern, *, rule_name: str, event_label: str) -> dict:
        """戻り値: {valid, label, reason, rule, matched_term, article_id, title}"""
        # 同じ見出し（転載・シンジケート記事）は1回だけ判定して結果を使い回す（LLM上限の浪費を防ぐ）
        norm = re.sub(r"[^a-z0-9぀-ヿ一-鿿]+", "", (article.get("title") or "").lower())
        key = (norm or article.get("external_signal_id"), rule_name)
        if key in self._cache:
            return {**self._cache[key], "article_id": article.get("external_signal_id")}
        res = self._validate(article, pattern, rule_name=rule_name, event_label=event_label)
        self._cache[key] = res
        return res

    def _validate(self, article, pattern, *, rule_name, event_label) -> dict:
        title = article.get("title") or ""
        summary = article.get("summary") or ""
        base = {"article_id": article.get("external_signal_id"), "title": title[:140], "rule": rule_name}
        m = pattern.search(title)
        text_in = "title"
        if not m:
            m = pattern.search(summary)
            text_in = "summary"
        if not m:
            return {**base, "valid": False, "label": "uncertain_excluded", "matched_term": None,
                    "reason": "候補語が見つからず検証できないため"}
        base["matched_term"] = m.group(0)
        context = title if text_in == "title" else _context_sentence(summary, m.span())
        # 見出しの文脈（否定・予測などの枠付け）は本文Hitでも優先して見る
        for scope in dict.fromkeys([title, context]):
            marker = _find_marker(scope, self.now)
            if marker:
                label, term, reason = marker
                return {**base, "valid": False, "label": label, "reason": reason}
        if _ACTUAL_VERB.search(context) or _ACTUAL_VERB_AMBIGUOUS.search(context):
            return {**base, "valid": True, "label": "actual_event",
                    "reason": "否定・仮定・予測・過去の語が無く、実際の出来事を示す動詞を含むため"}
        return self._validate_with_llm(article, base, event_label)

    def _validate_with_llm(self, article, base, event_label) -> dict:
        if self.azure_client is None or self.llm_calls >= self.max_llm_calls:
            why = "AI未設定" if self.azure_client is None else f"LLM検証の上限({self.max_llm_calls}件)超過"
            return {**base, "valid": False, "label": "uncertain_excluded",
                    "reason": f"実際に起きたか文面から確認できず、{why}のためAlertにしない（精度優先）"}
        self.llm_calls += 1
        user = (f"イベント種別: {event_label}\n見出し: {article.get('title') or ''}\n"
                f"要約: {(article.get('summary') or '')[:600]}")
        try:
            result = geo_llm_common.call_llm_structured(
                self.azure_client, self.model, _LLM_PROMPT, user, _LLM_SCHEMA,
                "alert_event_occurrence", max_attempts=2)
        except geo_llm_common.GeoLLMError as exc:
            return {**base, "valid": False, "label": "uncertain_excluded",
                    "reason": f"LLM検証に失敗したためAlertにしない: {str(exc)[:80]}"}
        data = result["data"]
        if data["occurred"] == "yes":
            return {**base, "valid": True, "label": "llm_confirmed_actual",
                    "reason": f"LLM確認: 実際に発生したと記事が述べている（{data['reason']}）"}
        label = "llm_rejected" if data["occurred"] == "no" else "uncertain_excluded"
        return {**base, "valid": False, "label": label,
                "reason": f"LLM確認: 実際の発生とは判断できない（{data['occurred']}: {data['reason']}）"}
