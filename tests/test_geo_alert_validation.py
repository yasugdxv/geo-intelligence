"""Monitoring Alert Context Validation（Candidate Detection → Context Validation → Alert）。

「China invasion of Taiwan unlikely」のような否定・予測・仮定・過去・否認の見出しでは
event Alertを出さず、実際に起きた出来事の見出しでは引き続き出すことを検証する。
LLMは曖昧な記事だけ・上限つき。全記事をLLMにかけない。
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_alert_validation as av  # noqa: E402
import geo_monitoring_theme_service as monitoring  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
MIL = dict((k, p) for k, _l, p in monitoring._EVENT_PATTERNS)["military_action"]


def _validate(title, summary="", validator=None):
    v = validator or av.AlertValidator(None, None, now=NOW)
    art = {"external_signal_id": f"id-{abs(hash(title))}", "title": title, "summary": summary}
    return v.validate(art, MIL, rule_name="event_military_action", event_label="軍事行動")


# --- Alertにしない（仕様の例） ---------------------------------------------------

@pytest.mark.parametrize("title,label", [
    ("China invasion of Taiwan unlikely in 2027", "negated"),            # 否定 + 予測
    ("  China invasion of Taiwan unlikely in 2027  ", "negated"),
    ("If China invades Taiwan, the world economy would suffer", "hypothetical"),
    ("Officials denied reports of an invasion", "denial"),
    ("Chances of Chinese Attack on Taiwan in the Next Decade", "forecast"),
    ("Taiwan takes a page from Ukraine playbook in steeling society for an invasion", "forecast"),
    ("Taiwan rehearses defence against invasion scenario", "hypothetical"),
    ("Marks 30 years since the missile crisis: a historical look", "historical"),
    ("PLA says it will not stage live-fire exercise near Taiwan", "negated"),
    ("Beijing dismissed claims of a blockade as baseless", "denial"),
])
def test_non_actual_events_are_not_valid(title, label):
    r = _validate(title)
    assert r["valid"] is False and r["label"] == label, r
    assert r["reason"]                                   # 理由が必ず付く


def test_future_year_is_forecast_but_past_tense_report_is_not_blocked():
    assert _validate("PLA exercises expected near Taiwan in 2027")["label"] in ("forecast",)
    r = _validate("PLA launches missile drills around Taiwan, first since 2022")   # 「since 2022」では除外しない
    assert r["valid"] is True and r["label"] == "actual_event"


# --- Alertにする（actual event） -----------------------------------------------

@pytest.mark.parametrize("title", [
    "China invaded Taiwan",
    "PLA fires missiles into waters near Taiwan",
    "PLA launches large-scale military drills around Taiwan",
    "Chinese warships conduct missile test as Taiwan scrambles jets",
    "中国軍が台湾周辺で演習を実施した",
])
def test_actual_events_are_valid(title):
    r = _validate(title)
    assert r["valid"] is True and r["label"] == "actual_event" and r["reason"], r


def test_marker_in_summary_sentence_blocks_but_unrelated_sentence_does_not():
    # トリガー語を含む文の否定は効く
    r = _validate("Taiwan military update", "Officials said an invasion is unlikely this year.")
    assert r["valid"] is False and r["label"] == "negated"
    # 無関係な別の文の否定語では、出来事自体は否定されない（トリガー文は実際の出来事）
    r2 = _validate("Taiwan military update", "The PLA fired missiles near the island. Markets did not react.")
    assert r2["valid"] is True


# --- 曖昧な記事だけLLM（上限つき） ----------------------------------------------

def test_ambiguous_article_is_excluded_without_llm():
    r = _validate("Missile defence debate in Taipei")                 # マーカーも実行動詞も無い
    assert r["valid"] is False and r["label"] == "uncertain_excluded"


def test_llm_is_used_only_for_ambiguous_and_is_capped(monkeypatch):
    calls = []

    def fake_llm(client, model, system, user, schema, name, **kw):
        calls.append(user)
        return {"data": {"occurred": "yes", "reason": "記事は発射があったと述べている"}}

    monkeypatch.setattr(av.geo_llm_common, "call_llm_structured", fake_llm)
    v = av.AlertValidator(object(), "m", max_llm_calls=2, now=NOW)
    clear_actual = _validate("PLA fires missiles near Taiwan", validator=v)          # ruleで確定 → LLM不要
    clear_negated = _validate("China invasion of Taiwan unlikely", validator=v)      # ruleで除外 → LLM不要
    assert not calls and clear_actual["valid"] and not clear_negated["valid"]
    r1 = _validate("Missile debate one", validator=v)
    r2 = _validate("Missile debate two", validator=v)
    r3 = _validate("Missile debate three", validator=v)                              # 上限超過
    assert len(calls) == 2
    assert r1["valid"] and r1["label"] == "llm_confirmed_actual" and r2["valid"]
    assert r3["valid"] is False and "上限" in r3["reason"]
    # LLMへ渡すのは「実際に発生したか」だけを問う内容（イベント種別と見出し・要約のみ）
    assert "イベント種別" in calls[0] and "見出し" in calls[0]


def test_llm_no_or_unclear_or_failure_is_excluded(monkeypatch):
    for answer in ("no", "unclear"):
        monkeypatch.setattr(av.geo_llm_common, "call_llm_structured",
                            lambda *a, **k: {"data": {"occurred": answer, "reason": "x"}})
        r = _validate("Missile debate", validator=av.AlertValidator(object(), "m", now=NOW))
        assert r["valid"] is False

    def boom(*a, **k):
        raise av.geo_llm_common.GeoLLMError("down")

    monkeypatch.setattr(av.geo_llm_common, "call_llm_structured", boom)
    r = _validate("Missile debate", validator=av.AlertValidator(object(), "m", now=NOW))
    assert r["valid"] is False and r["label"] == "uncertain_excluded"


# --- detect_alerts への統合 -------------------------------------------------------

def _article(i, title, domain, **kw):
    a = {"external_signal_id": f"a{i}", "title": title, "summary": "", "source_id": None, "source_domain": domain,
         "source_name": domain, "published_at": "2026-10-05T08:00:00+00:00", "created_at": "2026-10-05T08:00:00+00:00"}
    a.update(kw)
    return a


def _theme():
    return {"monitoring_theme_id": "m1", "sensitivity": "low"}      # lowで量的Alertを出にくくする


def test_detect_alerts_negated_headlines_produce_no_event_alert_but_actual_still_does():
    neg = [_article(1, "China invasion of Taiwan unlikely in 2027", "manilatimes.net"),
           _article(2, "Officials denied reports of an invasion", "reuters.com")]
    log = []
    assert monitoring.detect_alerts(_theme(), neg, set(), now=NOW, validation_log=log) == []
    assert [r["label"] for r in log] == ["negated", "denial"] and all(not r["valid"] for r in log)

    actual = [_article(1, "PLA fires missiles near Taiwan", "reuters.com"),
              _article(2, "PLA launches military drills around Taiwan", "scmp.com"),
              _article(3, "China invasion of Taiwan unlikely in 2027", "manilatimes.net")]
    log = []
    alerts = monitoring.detect_alerts(_theme(), actual, set(), now=NOW, validation_log=log)
    ev = [a for a in alerts if a["alert_type"] == "event_military_action"]
    assert len(ev) == 1
    a = ev[0]
    assert a["related_signal_ids"] == ["a1", "a2"]                  # 否定記事は根拠に入らない
    assert a["validation_result"] == "actual_event" and a["validation_reason"]
    assert a["candidate_rule"].startswith("event_military_action:")
    assert "3件" in a["reason"] and "2件が" in a["reason"] and "1件を除外" in a["reason"]


def test_negated_only_second_source_does_not_satisfy_independent_source_requirement():
    arts = [_article(1, "PLA fires missiles near Taiwan", "reuters.com"),
            _article(2, "China invasion of Taiwan unlikely", "manilatimes.net")]
    assert monitoring.detect_alerts(_theme(), arts, set(), now=NOW) == []   # 有効記事は1ソースのみ


def test_assessment_records_validation_audit_and_alert_columns_fall_back_without_db8():
    from tests._fakes import FakeSupabaseClient

    class NoAuditColumnsClient(FakeSupabaseClient):
        def insert(self, table, rows, prefer="return=representation"):
            if table == "geo_monitoring_alerts" and "validation_result" in rows[0]:
                import requests
                resp = requests.Response()
                resp.status_code = 400
                raise requests.HTTPError("column does not exist", response=resp)
            return super().insert(table, rows, prefer)

    sig = lambda i, t, d: {"external_signal_id": f"s{i}", "source_id": None, "source_domain": d, "source_name": d,  # noqa: E731
                           "title": t, "summary": "", "url": f"https://{d}/{i}",
                           "published_at": "2026-10-05T08:00:00+00:00", "created_at": "2026-10-05T08:00:00+00:00",
                           "entities": None}
    c = NoAuditColumnsClient({
        "geo_external_signals": [sig(1, "PLA fires missiles near Taiwan", "reuters.com"),
                                 sig(2, "PLA launches military drills around Taiwan", "scmp.com"),
                                 sig(3, "China invasion of Taiwan unlikely", "manilatimes.net"),
                                 sig(4, "Taiwan news", "fresh.example")],
        "geo_source_registry": [], "geo_source_domains": [], "geo_source_sets": [], "geo_source_set_members": [],
        "geo_monitoring_themes": [{"monitoring_theme_id": "m1", "display_name": "t", "keywords": ["Taiwan"],
                                   "sensitivity": "medium", "status": "active", "source_set_id": None,
                                   "theme_candidate_id": None}],
        "geo_monitoring_alerts": []})
    theme = c.tables["geo_monitoring_themes"][0]
    res = monitoring.assess(c, theme, now=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc))
    v = res["assessment"]["validation"]
    assert v["candidates"] == 3 and v["valid"] == 2
    assert [x["label"] for x in v["excluded"]] == ["negated"] and v["excluded"][0]["reason"]
    saved = c.tables["geo_monitoring_alerts"]
    assert saved and all("validation_result" not in r for r in saved)         # 列が無くても保存できる


# --- 2026-10-05 実データで見つかった弱点の回帰テスト -------------------------------------

SANCTION = dict((k, p) for k, _l, p in monitoring._EVENT_PATTERNS)["sanction"]


def _validate_sanction(title, summary=""):
    v = av.AlertValidator(None, None, now=NOW)
    return v.validate({"external_signal_id": f"sid-{abs(hash(title))}", "title": title, "summary": summary},
                      SANCTION, rule_name="event_sanction", event_label="制裁")


def test_trigger_word_noun_usage_is_not_counted_as_an_actual_event_verb():
    # 名詞としての sanctions / strikes は「実際に起きた」の根拠にしない（曖昧→LLM未使用なら除外）
    r = _validate_sanction("Iran urges Iraq to lift flight ban amid US sanctions")
    assert r["valid"] is False and r["label"] == "uncertain_excluded"
    r2 = _validate("Ukrainian strikes on southern ports continue")
    assert r2["valid"] is False and r2["label"] == "uncertain_excluded"
    # 動詞としての用法（大文字主語＋sanctions＋大文字の目的語）は実際の出来事
    assert _validate_sanction("US sanctions Iran car makers and railway operators")["valid"] is True
    assert _validate_sanction("US imposes new sanctions on Iran auto sector")["valid"] is True
    assert _validate_sanction("Treasury Expands Iran Sanctions With Industrial Network")["valid"] is True


def test_historic_adjective_alone_does_not_mark_a_current_event_as_historical():
    r = _validate_sanction("US imposes sanctions on Iran, threatening historic trade ties")
    assert r["valid"] is True and r["label"] == "actual_event"
    # 明示的な回想は引き続き除外
    assert _validate_sanction("Remembers sanctions imposed 30 years ago")["label"] == "historical"


def test_sanction_relief_offer_with_would_is_hypothetical_and_denied_report_is_denial():
    assert _validate_sanction("Araghchi said Iran would allow access to inspectors for sanctions relief")["label"] == "hypothetical"
    assert _validate_sanction("Iran denies report on offer to restore inspections for sanctions relief")["label"] == "denial"


def test_same_headline_is_validated_once_so_syndicated_copies_do_not_exhaust_the_llm_cap(monkeypatch):
    calls = []

    def fake_llm(client, model, system, user, schema, name, **kw):
        calls.append(user)
        return {"data": {"occurred": "no", "reason": "協議しただけ"}}

    monkeypatch.setattr(av.geo_llm_common, "call_llm_structured", fake_llm)
    v = av.AlertValidator(object(), "m", max_llm_calls=2, now=NOW)
    arts = [{"external_signal_id": f"c{i}", "title": "Foreign Secy meets US delegation, discusses missile defence",
             "summary": ""} for i in range(5)]
    res = [v.validate(a, MIL, rule_name="event_military_action", event_label="軍事行動") for a in arts]
    assert len(calls) == 1                                    # 5件の転載でもLLMは1回
    assert all(r["valid"] is False for r in res) and [r["article_id"] for r in res] == [a["external_signal_id"] for a in arts]


def test_casualty_and_report_words_count_as_evidence_of_an_actual_event():
    assert _validate("Russian guided bomb strikes on Kharkiv apartment building, injuring 22")["valid"] is True
    assert _validate("Russia-Ukraine war: Fresh strikes reported across Kyiv")["valid"] is True
