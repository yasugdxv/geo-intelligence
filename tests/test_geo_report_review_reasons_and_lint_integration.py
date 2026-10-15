"""requires_reviewの理由分離（従来は全clusterが一律True）と、Preprocessへのmaster lint組み込みのテスト（2026-10-04）。"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_project_assets_service as assets  # noqa: E402

_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl", "languageDisplayName",
           "documentTypeName", "authorName"]


def master_rows(include="alpha topic", required="", max_records="200"):
    base = {"signal_id": "S1", "exclude_keywords": "", "public_voice_cues": "", "cluster_split_terms": "",
            "priority": "Medium", "max_cluster_records": max_records, "representative_count": "8", "active": "true",
            "theme_id": "S1_T", "theme_name": "S1 Theme", "trend_role": "", "trend_start_date": "",
            "main_narrative": "Main", "stance_default": "Unknown", "required_context_keywords": required}
    return [assets.sync_shell_mirror_columns({**base, "narrative_id": "S1-01", "narrative_name": "n1",
                                              "include_keywords": include, "is_fallback": "false"}),
            assets.sync_shell_mirror_columns({**base, "narrative_id": "S1-99", "narrative_name": "fb",
                                              "include_keywords": "", "is_fallback": "true"})]


def write(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def news(i, text, title=None):
    return {"documentId": f"n{i}", "timestamp": "2026-09-10T10:00:00Z", "documentName": title or f"alpha topic report {i}",
            "documentText": text, "sourceUrl": f"https://news{i}.example.com/a{i}", "languageDisplayName": "English",
            "documentTypeName": "News", "authorName": ""}


def run(tmp_path, rows, master=None, **kw):
    write(tmp_path / "S1_x.csv", rows)
    return cp.run_preprocess([str(tmp_path / "S1_x.csv")], master or master_rows(), **kw)


def label_rows(result, clusters=None):
    return cp.build_cluster_labeling_rows(clusters or result.analysis_clusters, result.flagged_terms)


def test_clean_consistent_cluster_does_not_require_review(tmp_path):
    """高品質・一貫したcluster（Narrativeに一致、本文あり、source純粋、cap-hitなし）はrequires_review=false。"""
    result = run(tmp_path, [news(i, f"alpha topic details number{i}x") for i in range(6)])
    row = label_rows(result)[0]
    assert row["match_signature"] == "alpha topic"
    assert row["review_reasons"] == "" and row["requires_review"] is False


def test_fallback_cluster_requires_review_with_fallback_reason(tmp_path):
    result = run(tmp_path, [news(i, f"unrelated content number{i}x", title=f"misc {i}") for i in range(6)])
    row = label_rows(result)[0]
    assert "fallback" in row["review_reasons"].split("|") and row["requires_review"] is True


def test_missing_text_and_unknown_source_reasons():
    stats = _stats(members=[{"review_text_status": "retweet_only", "attitude_hint": "unknown", "reaction_type_hint": "",
                             "matched_terms": ""} for _ in range(5)], bucket="unknown")
    reasons = cp.get_review_reasons(stats, fallback_share=0.0, purity=1.0)
    assert "missing_text" in reasons and "source_identity_uncertain" in reasons


def test_low_purity_marks_source_identity_uncertain():
    stats = _stats(members=[_member()] * 4)
    assert "source_identity_uncertain" in cp.get_review_reasons(stats, fallback_share=0.0, purity=0.75)
    assert "source_identity_uncertain" not in cp.get_review_reasons(stats, fallback_share=0.0, purity=1.0)


def test_mixed_reaction_cap_hit_low_coherence_and_representative_conflict_reasons():
    mixed = [dict(_member(), attitude_hint="mixed") for _ in range(4)] + [_member() for _ in range(4)]
    stats = _stats(members=mixed)
    assert "mixed_reaction" in cp.get_review_reasons(stats, fallback_share=0.0, purity=1.0)
    stats = _stats(members=[_member()] * 4)
    stats.cluster_cap_hit = True
    stats.semantic_coherence_status = "Low"
    reasons = cp.get_review_reasons(stats, fallback_share=0.0, purity=1.0)
    assert {"cap_hit", "low_semantic_coherence"} <= set(reasons)
    stats = _stats(members=[_member()] * 4)
    stats.heterogeneous_cluster = True
    assert "low_semantic_coherence" in cp.get_review_reasons(stats, fallback_share=0.0, purity=1.0)
    conflict = _stats(members=[dict(_member(), attitude_hint="negative")] * 4)
    conflict.attitude_hint = "positive"                                   # clusterのラベルと代表の多数派が食い違う
    assert "representative_conflict" in cp.get_review_reasons(conflict, fallback_share=0.0, purity=1.0)


def test_keyword_collision_reason_uses_flagged_terms_of_the_matched_narrative():
    members = [dict(_member(), matched_terms="ICE|border") for _ in range(4)]
    stats = _stats(members=members)
    flagged = {("S1", "S1-01", "ice")}
    assert "keyword_collision" in cp.get_review_reasons(stats, fallback_share=0.0, purity=1.0, flagged_terms=flagged)
    assert "keyword_collision" not in cp.get_review_reasons(stats, fallback_share=0.0, purity=1.0,
                                                              flagged_terms={("S1", "S1-02", "ice")})
    assert "keyword_collision" in cp.get_review_reasons(
        stats, fallback_share=0.0, purity=1.0, flagged_terms={("S1", "", "border")})     # signal単位のflag
    assert "keyword_collision" not in cp.get_review_reasons(stats, fallback_share=0.0, purity=1.0)


def test_requires_review_equals_nonempty_reasons_for_every_cluster(tmp_path):
    rows = [news(i, f"alpha topic details number{i}x") for i in range(6)] + \
           [news(10 + i, f"unrelated content number{i}x", title=f"misc {i}") for i in range(6)]
    result = run(tmp_path, rows)
    for row in label_rows(result):
        assert row["requires_review"] == bool(row["review_reasons"])
    assert {row["requires_review"] for row in label_rows(result)} == {True, False}


def test_preprocess_runs_master_lint_with_corpus_vocabulary_and_flags_terms(tmp_path):
    rows = [news(i, f"ICE agents and the office of justice number{i}x", title=f"alpha topic {i}") for i in range(30)]
    master = master_rows(include="ICE; office*; alpha topic")
    result = run(tmp_path, rows, master=master)
    codes = {(f["term"], f["code"]) for f in result.master_lint}
    assert ("ICE", "short_keyword") in codes
    assert result.master_lint and cp.build_master_lint_rows(result)
    csv_rows = cp.build_master_lint_rows(result)
    assert set(csv_rows[0]) == {"signal_id", "narrative_id", "term", "term_type", "issue_type", "collision_example",
                                "severity", "recommended_action"}


def test_preprocess_can_skip_master_lint(tmp_path):
    result = run(tmp_path, [news(0, "alpha topic")], lint_master=False)
    assert result.master_lint == [] and result.flagged_terms == set()


def test_manifest_and_config_record_the_matcher_version(tmp_path):
    result = run(tmp_path, [news(0, "alpha topic")])
    manifest = cp.build_cluster_manifest(result, "t", created_at="2026-10-04", record_map_chunks=1)
    assert manifest["keyword_matcher_version"] == "keyword_matcher_v2"
    yaml_text = cp.build_report_config_yaml(result, report_id="r", topic="t", primary_graph_signals=[],
                                            final_graph_rule="x", created_at="2026-10-04")
    assert "keyword_matcher: keyword_matcher_v2" in yaml_text


def test_representative_rows_carry_match_audit_columns(tmp_path):
    result = run(tmp_path, [news(i, f"alpha topic details number{i}x") for i in range(4)])
    row = cp.build_cluster_representative_rows(result.analysis_clusters)[0]
    assert row["matched_terms"] == "alpha topic" and row["match_method"] == "latin_phrase"
    assert row["matched_span"].startswith("inc:") and row["matched_exclude_terms"] == ""
    assert set(row.keys()) == set(cp.CLUSTER_REPRESENTATIVE_COLUMNS)


# ---- helpers ----

def _member():
    return {"review_text_status": "has_text", "attitude_hint": "positive", "reaction_type_hint": "Evaluation",
            "matched_terms": "alpha", "source_class_preliminary": "Media"}


def _stats(members, bucket="media"):
    rule = cp.build_rule_from_row(master_rows()[0])
    stats = cp.ClusterStats(
        cluster_id="AC_x", kind="analysis", rule=rule, week_start="2026-09-07", week_end="2026-09-13",
        source_class_preliminary="Media", source_bucket=bucket, reaction_type_hint="Evaluation",
        attitude_hint="positive", cluster_cue="general", match_signature="alpha", representative_limit=8)
    stats.record_count = len(members)
    stats.all_members = list(members)
    stats.representatives = list(members[:8])
    return stats
