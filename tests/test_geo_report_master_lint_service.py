"""Narrative Master lint（geo_report_master_lint_service）のテスト。

match_masterは小文字化したテキストへの単純な部分一致（単語境界なし・正規化なし）で判定するため、
Masterの書き方次第で「発火しない/発火しすぎる」Narrativeが静かに生まれる。実Master（US中間選挙204行、
Evidence 30行・34行）で見つかった典型パターンを最小の行で再現して検出を確認する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_master_lint_service as lint  # noqa: E402
import geo_report_run_provisioning_service as provisioning  # noqa: E402
from tests._fakes import FakeSupabaseClient  # noqa: E402


def row(signal="S1", narrative="S1-01", *, include="", required="", exclude="", cues="", split_terms="",
        is_fallback="false", theme_name="Arizona Governor", **extra):
    base = {
        "signal_source": signal, "signal_name": theme_name, "theme_id": signal, "theme_name": theme_name,
        "sub_narrative_id": narrative, "include_keywords": include, "required_context_keywords": required,
        "exclude_keywords": exclude, "public_voice_cues": cues, "cluster_split_terms": split_terms,
        "priority": "High", "max_cluster_records": "200", "representative_count": "8",
        "is_fallback": is_fallback, "active": "true", "trend_role": "", "trend_start_date": "",
    }
    base.update(extra)
    return base


def fallback(signal="S1"):
    return row(signal, f"{signal}-00", is_fallback="true")


def found(rows, code, **kw):
    return [f for f in lint.lint_narrative_master(rows, **kw) if f["code"] == code]


def test_clean_master_has_no_errors_or_warnings():
    rows = [row(include="competent leader; experienced governor", required="Katie Hobbs"), fallback()]
    findings = lint.lint_narrative_master(rows)
    assert not [f for f in findings if f["severity"] in ("error", "warning")], findings


def test_include_inside_required_means_required_context_alone_fires_the_narrative():
    """実Master(US S1-10): include 'govern' が required 'Governor' の部分文字列 -> 'Governor'と書くだけで発火。"""
    rows = [row("S1", "S1-10", include="govern; governing", required="Katie Hobbs; Governor"), fallback()]
    hits = found(rows, "include_inside_required")
    assert hits and hits[0]["term"] == "govern" and hits[0]["severity"] == "warning"


def test_required_inside_include_is_info_only_harmless_redundancy():
    rows = [row(include="support Katie Hobbs", required="Katie Hobbs"), fallback()]
    hits = found(rows, "required_inside_include")
    assert hits and all(h["severity"] == "info" for h in hits)


def test_include_equal_to_required_is_flagged():
    rows = [row(include="Taiwan", required="Taiwan"), fallback()]
    assert found(rows, "include_equals_required")


def test_include_containing_an_exclude_term_can_never_fire():
    rows = [row(include="2024 election results", exclude="2024 election"), fallback()]
    hits = found(rows, "include_contains_exclude")
    assert hits and hits[0]["severity"] == "error"


def test_short_keywords_acronym_is_info_and_common_word_is_warning():
    """matcher v2: 大文字2〜3文字のアクロニムはcase-sensitive exact tokenなのでINFO、普通の短い語はWARNING。"""
    rows = [row(include="ICE; tax; gov; Hobbs"), fallback()]
    by_term = {f["term"]: f["severity"] for f in found(rows, "short_keyword")}
    assert by_term == {"ICE": "info", "tax": "warning", "gov": "warning"}
    assert not found(rows, "word_boundary_risk")         # 旧matcher(部分一致)前提の指摘は廃止


VOCAB = {"office": 4260, "police": 1093, "justice": 900, "ice": 700, "voters": 5960, "voted": 1958, "vote": 100,
         "parent": 300, "parents": 485, "current": 2119, "rentacop": 300, "rental": 600, "rents": 120}


def test_vocabulary_finds_substring_collisions_and_uncovered_inflections():
    rows = [row(include="ICE; vote; parent; rent*"), fallback()]
    findings = lint.lint_narrative_master(rows, vocabulary=VOCAB)
    by = {(f["term"], f["code"]): f for f in findings}
    ice = by[("ICE", "substring_collision")]
    assert ice["severity"] == "info" and "office(4260)" in ice["collision_example"]       # v2では回避済み
    inflect = by[("vote", "inflection_not_covered")]
    assert inflect["severity"] == "warning" and "voters(5960)" in inflect["collision_example"]
    assert by[("rent*", "prefix_collision")]["severity"] == "warning"                       # rentacopに当たる
    assert ("rent*", "inflection_not_covered") not in by                                     # `*`は語形を既に許可している


def test_lint_csv_rows_have_required_columns_and_uppercase_severity():
    rows = [row(include="ICE; vote"), fallback()]
    findings = lint.lint_narrative_master(rows, vocabulary=VOCAB)
    csv_rows = lint.findings_to_csv_rows(findings)
    assert list(csv_rows[0].keys()) == lint.LINT_CSV_COLUMNS
    assert {r["severity"] for r in csv_rows} <= {"ERROR", "WARNING", "INFO"}
    row_ice = next(r for r in csv_rows if r["term"] == "ICE" and r["issue_type"] == "substring_collision")
    assert row_ice["term_type"] == "include" and row_ice["recommended_action"]
    assert csv_rows == sorted(csv_rows, key=lambda r: ["ERROR", "WARNING", "INFO"].index(r["severity"]))


def test_generic_keywords_win_lose_back_beat_race_state_are_flagged():
    rows = [row(include="win; lose; back; beat; race; state"), fallback()]
    assert {f["term"] for f in found(rows, "generic_word")} == {"win", "lose", "back", "beat", "race", "state"}


def test_single_cjk_character_is_flagged_but_two_are_not():
    rows = [row(include="米; 関税"), fallback()]
    assert [f["term"] for f in found(rows, "short_keyword")] == ["米"]


def test_generic_single_word_and_names_only_self_firing():
    rows = [row(include="economy; taxes"), fallback()]
    assert {f["term"] for f in found(rows, "generic_word")} == {"economy", "taxes"}
    rows = [row(include="Katie Hobbs; Hobbs; Arizona", required="Katie Hobbs"), fallback()]
    assert found(rows, "self_firing_names_only")


def test_hyphenated_keyword_without_space_variant_is_flagged_and_variant_present_is_ok():
    """matcherは正規化しないため 'US-China summit' は 'US China summit' にマッチしない
    （Evidence master S1の required 'US-China summit' が fallback_share 0.95 の一因）。"""
    assert found([row(required="US-China summit"), fallback()], "punctuation_variant")
    assert not found([row(required="US-China summit; US China summit"), fallback()], "punctuation_variant")
    assert found([row(include="don’t vote"), fallback()], "punctuation_variant")      # カーリー引用符


def test_cues_that_are_descriptions_and_vs_split_terms_never_match_text():
    rows = [row(cues="会談を『関係安定化』として位置づける発言や社説。具体例: 「重要な一歩」",
                split_terms="stabilization vs escalation; guardrails"), fallback()]
    assert found(rows, "cues_not_phrases")
    assert [f["term"] for f in found(rows, "split_term_vs_phrase")] == ["stabilization vs escalation"]


def test_include_in_one_narrative_exclude_in_another_and_alias_collision():
    rows = [row("S1", "S1-01", include="guardrails", exclude="managed competition"),
            row("S1", "S1-02", include="managed competition; guardrails"), fallback()]
    assert found(rows, "alias_collision")                       # 'guardrails'が2つのNarrativeにある
    assert found(rows, "include_exclude_cross_conflict")


def test_fallback_rule_structure():
    assert found([row()], "no_fallback_rule")[0]["severity"] == "error"
    assert found([row(), fallback(), row("S1", "S1-98", is_fallback="true")], "multiple_fallback_rules")
    assert found([row(), row(is_fallback="true", narrative="S1-00", include="x")], "fallback_has_include")


def test_duplicate_narrative_id_and_missing_include():
    rows = [row("S1", "S1-01", include="a"), row("S1", "S1-01", include="b"), row("S1", "S1-02"), fallback()]
    assert found(rows, "duplicate_narrative_id")[0]["severity"] == "error"
    assert found(rows, "no_include")[0]["narrative_id"] == "S1-02"


def test_config_value_floors_and_primary_without_start():
    rows = [row(include="a", max_cluster_records="10", representative_count="2", priority="urgent",
                trend_role="primary", trend_start_date=""), fallback()]
    assert found(rows, "max_cluster_records_below_floor")
    assert found(rows, "representative_count_below_floor")
    assert found(rows, "invalid_priority")
    assert found(rows, "primary_without_valid_start")


def test_inactive_rows_are_not_linted():
    rows = [row(include="ICE", active="false"), fallback()]
    assert not found(rows, "short_keyword")


def test_signal_id_is_used_when_signal_source_is_missing():
    r = row(include="ICE")
    del r["signal_source"]
    r["signal_id"] = "S9"
    assert found([r, fallback("S9")], "short_keyword")[0]["signal_source"] == "S9"


def test_data_driven_checks_multilingual_and_high_hit_rate():
    rows = [row(include="vote for Hobbs"), fallback()]
    findings = lint.lint_narrative_master(
        rows, language_distribution={"S1": {"English": 70, "Chinese": 30}},
        term_hit_counts={"S1": {"vote for hobbs": 600}}, signal_totals={"S1": 1000})
    codes = {f["code"] for f in findings}
    assert {"multilingual_keywords_missing", "high_hit_rate"} <= codes
    # 低いヒット率・非ラテン文字が少ない場合は出さない
    quiet = lint.lint_narrative_master(
        rows, language_distribution={"S1": {"English": 99, "Chinese": 1}},
        term_hit_counts={"S1": {"vote for hobbs": 10}}, signal_totals={"S1": 1000})
    assert not {"multilingual_keywords_missing", "high_hit_rate"} & {f["code"] for f in quiet}


def test_summary_and_warning_formatting():
    rows = [row(include="2024 election results", exclude="2024 election")]
    findings = lint.lint_narrative_master(rows)
    summary = lint.summarize_findings(findings)
    assert summary["by_severity"]["error"] >= 2                  # include_contains_exclude + no_fallback_rule
    lines = lint.format_findings_for_warning(findings, max_items=1)
    assert lines[0].startswith("[error]") and "...他" in lines[-1]


def test_lint_runs_on_master_rows_built_for_the_pipeline_without_crashing():
    """cp.build_master_rules が読む行と同じ形（Design生成のMasterを想定）でも例外なく動く。"""
    rows = [row(include="alpha; beta", required="gamma"), fallback()]
    assert cp.build_master_rules(rows)[0]
    assert isinstance(lint.lint_narrative_master(rows), list)


def test_run_creation_helper_returns_warnings_only_when_there_are_findings():
    assert provisioning.build_master_lint_warnings([row(include="competent leader", required="Katie Hobbs"),
                                                     fallback()]) == {}
    result = provisioning.build_master_lint_warnings([row(include="ICE")])
    assert result["warnings"] and result["summary"]["by_severity"]["error"] >= 1


def test_pin_run_snapshot_surfaces_master_lint_warnings_without_blocking(monkeypatch):
    """pin_run_snapshotの結果にmaster_lint_warningsが付くが、Run作成(RPC)自体は止まらない。"""
    client = FakeSupabaseClient()
    client.insert("geo_report_project_assets", [{
        "id": "nm-1", "project_id": "p1", "asset_type": "narrative_master", "status": "published",
        "content": {"rows": [dict(row(include="ICE"), signal_id="S1")]}}])
    design_version = {"id": "dv-1", "project_id": "p1", "version": 1, "narrative_master_asset_id": "nm-1",
                      "report_design": {"analysis_mode": "public_voice_monitor", "report_kind": "consumption_tax_food_price",
                                        "primary_graph_signals": []},
                      "signals": []}
    monkeypatch.setattr(provisioning, "get_latest_published_design_version", lambda c, p: design_version)
    monkeypatch.setattr(provisioning, "resolve_baseline_continuity", lambda c, b: (None, None))
    monkeypatch.setattr(provisioning, "load_prompt_instruction",
                        lambda kind, version=None: {"contract_version": "v4.3", "fingerprint": "f",
                                                "text": ("targeted_stance_classified" if kind == "prompt1"
                                                         else "消費税 食品値上げ")})
    monkeypatch.setattr(client, "rpc", lambda name, params: {"run_id": "run-1"}, raising=False)
    result = provisioning.pin_run_snapshot(client, "run-1", "p1")
    assert result["run_id"] == "run-1"
    assert result["master_lint_warnings"]["warnings"]


def test_inflection_listed_in_the_same_row_is_not_reported_as_uncovered():
    """`vote`に対して`voters`/`voted`を同じ行で明示済み、`vote*`で語頭一致を指定済みなら、取りこぼしとして指摘しない。"""
    covered = lint.lint_narrative_master([row(include="vote; voters; voted"), fallback()], vocabulary=VOCAB)
    assert not [f for f in covered if f["code"] == "inflection_not_covered"]
    wildcard = lint.lint_narrative_master([row(include="vote*"), fallback()], vocabulary=VOCAB)
    assert not [f for f in wildcard if f["code"] == "inflection_not_covered"]
    uncovered = lint.lint_narrative_master([row(include="vote; voters"), fallback()], vocabulary=VOCAB)
    assert [f["collision_example"] for f in uncovered if f["code"] == "inflection_not_covered"] == ["voted(1958)"]


def test_trailing_wildcard_is_not_reported_as_punctuation():
    findings = lint.lint_narrative_master([row(include="vote*; Democrat*"), fallback()])
    assert not [f for f in findings if f["code"] == "punctuation_variant"]
    assert [f["term"] for f in lint.lint_narrative_master([row(include="vote!"), fallback()])
            if f["code"] == "punctuation_variant"] == ["vote!"]
