"""Preprocess潜在不具合監査（2026-10-03）で確認した確定バグ・予防的ハードニングの回帰テスト。

各テストの冒頭に、対応する監査項目（#番号）と、US中間選挙/Evidence Intelligence fixtureでの
実測根拠を書く。`known_limitation`を名前に含むテストは「現状の挙動の記録」で、将来その挙動を
意図的に直したときにこのテストも更新する（テストが落ちたら“壊れた”のではなく“仕様が変わった”合図）。
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_cluster_semantic_service as semantic  # noqa: E402
import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_project_assets_service as assets  # noqa: E402

_INPUT_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl",
                  "languageDisplayName", "documentTypeName", "authorName", "sourceName"]


def _write_input_csv(path: Path, rows: list):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_INPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in _INPUT_FIELDS})


def _master_row(signal_id, narrative_id, *, include="", split_terms="", is_fallback="false",
                 trend_start_date="", max_cluster_records="200"):
    row = {
        "signal_id": signal_id, "narrative_id": narrative_id, "narrative_name": f"{narrative_id} name",
        "include_keywords": include, "exclude_keywords": "", "required_context_keywords": "",
        "cluster_split_terms": split_terms, "public_voice_cues": "", "priority": "Medium",
        "max_cluster_records": max_cluster_records, "representative_count": "8", "is_fallback": is_fallback,
        "active": "true", "theme_id": f"{signal_id}_THEME", "theme_name": f"{signal_id} Theme",
        "trend_role": "", "trend_start_date": trend_start_date, "main_narrative": "Main",
        "stance_default": "Unknown",
    }
    return assets.sync_shell_mirror_columns(row)


def _tweet(doc_id, text, *, day="2026-09-10", author=None, title="Twitter Post"):
    author = author or f"user{doc_id}"
    return {"documentId": doc_id, "timestamp": f"{day}T10:00:00Z", "documentName": title, "documentText": text,
            "sourceUrl": f"https://twitter.com/{author}/status/{doc_id}", "languageDisplayName": "English",
            "documentTypeName": "TwitterSocialMedia", "authorName": author}


# ---------------------------------------------------------------------------
# #3 source bucket（確定バグ）: official判定がURL全体への`gov`部分一致だった
# 実測: US fixtureのofficialバケット(非重複4,128件)のうち、政府ドメインは255件(6.2%)のみ。
#       残りは`/governor-race-...`を含むニュース3,050件、Reddit306件、twitter.com/GovKathyHochul等。
# ---------------------------------------------------------------------------

def test_official_bucket_requires_government_host_not_url_path_substring():
    assert cp.get_source_bucket(
        "https://www.azcentral.com/story/news/politics/governor-race-hobbs-biggs/1/", "News", "") == "media"
    assert cp.get_source_bucket(
        "https://www.reddit.com/r/Georgia/comments/1/georgia_gov_brian_kemp_signs_order/", "RedditSocialMedia",
        "") == "public_social"
    assert cp.get_source_bucket("https://twitter.com/GovKathyHochul/status/1", "TwitterSocialMedia", "") \
        == "public_social"
    assert cp.get_source_bucket("https://www.governorsample.com/news/x", "News", "") == "media"


def test_official_bucket_still_matches_real_government_hosts():
    for url in ("https://www.governor.ny.gov/news/x", "https://www.fmprc.gov.cn/x", "https://english.www.gov.cn/x",
                "https://www.gov.uk/x", "https://www.mhlw.go.jp/x", "https://city.example.lg.jp/x",
                "https://example.gov/x"):
        assert cp.get_source_bucket(url, "News", "") == "official", url


def test_pr_release_document_type_does_not_match_wordpress():
    """実測: US fixtureのWordPress記録46件(非重複14件)が全てpr_release(Corporate_PR)だった。"""
    assert cp.get_source_bucket("https://blog.example.com/p/1", "WordPress", "") == "public_blog"
    assert cp.get_source_bucket("https://example.com/x", "PressRelease", "") == "pr_release"
    assert cp.get_source_bucket("https://example.com/x", "Press Release", "X") == "pr_release"
    assert cp.get_source_bucket("https://example.com/x", "Press", "X") == "pr_release"


# ---------------------------------------------------------------------------
# #3 Analysis Clusterのラベル（確定バグ）: 先頭recordの値がClusterのラベルになっていた
# 実測: US fixtureでAnalysis Clusterのsource_classが不一致のrecordは6.5%(11,407/174,524)、
#       Evidenceは6.1%。Sentiment Clusterはkeyに含むため0%。
# ---------------------------------------------------------------------------

def _run_mixed_class_cluster(tmp_path):
    # 全recordを同じcue(general)・同じAnalysis Clusterに入れるため、反応語(support等)は使わず、
    # 反応語に当たらない肯定語(welcome)でattitude=positiveだけを作る。
    texts = [
        "RT @orig: base message",                # 先頭: 純粋RT -> review_text空 -> Fact_Only/unknown
        "welcome news alpha1x", "welcome news beta2x", "welcome news gamma3x",
    ]
    rows = [_tweet(f"d{i}", t) for i, t in enumerate(texts)]
    input_path = tmp_path / "S1_input.csv"
    _write_input_csv(input_path, rows)
    master = [_master_row("S1", "S1-01", include="zzzneverhit"), _master_row("S1", "S1-99", is_fallback="true")]
    return cp.run_preprocess([str(input_path)], master)


def test_analysis_cluster_labels_use_majority_of_records_not_first_record(tmp_path):
    result = _run_mixed_class_cluster(tmp_path)
    assert len(result.analysis_clusters) == 1
    stats = next(iter(result.analysis_clusters.values()))
    assert stats.source_distribution == {"Fact_Only_Candidate": 1, "Public_Voice_Candidate": 3}
    assert stats.attitude_distribution == {"unknown": 1, "positive": 3}
    assert stats.source_class_preliminary == "Public_Voice_Candidate"   # 先頭(Fact_Only)ではなく最頻値
    assert stats.attitude_hint == "positive"                            # 先頭(unknown)ではなく最頻値


def test_finalize_cluster_labels_also_fixes_reaction_type_hint():
    members = [_member(i) for i in range(4)]
    stats = _stats_for(members, max_records=200)
    stats.reaction_type_hint = "None"                                   # 先頭recordの値
    stats.reaction_distribution = {"None": 1, "Evaluation": 3}
    stats.source_distribution = {"Public_Voice_Candidate": 4}
    stats.attitude_distribution = {"positive": 4}
    cp.finalize_cluster_labels({"AC_synth": stats})
    assert stats.reaction_type_hint == "Evaluation"


def test_sentiment_cluster_labels_are_unchanged_and_uniform(tmp_path):
    result = _run_mixed_class_cluster(tmp_path)
    for stats in result.sentiment_clusters.values():
        assert stats.source_distribution == {stats.source_class_preliminary: stats.record_count}
        assert stats.attitude_distribution == {stats.attitude_hint: stats.record_count}


def test_finalize_cluster_labels_tie_breaks_deterministically():
    assert cp._majority_label({"b": 2, "a": 2, "c": 1}, "x") == "a"
    assert cp._majority_label({}, "fallback") == "fallback"


# ---------------------------------------------------------------------------
# #4 recursive split（潜在バグ）: record_idだけをキーに付け替えると別Signalの行を巻き込む
# 実測: US fixtureで同じdocumentIdが複数Signalに非重複として存在するrecordが10,379件
#       （実データでは分割自体が起きていないため未顕在）。
# ---------------------------------------------------------------------------

def test_recursive_split_remap_does_not_rewrite_same_record_id_in_another_signal(tmp_path, monkeypatch):
    attitudes = ["support", "oppose", "support oppose", ""]
    s1_rows = []
    for i in range(20):
        doc_id = "shared-1" if i == 0 else f"s1-{i}"
        s1_rows.append(_tweet(doc_id, f"zq item{i}x {attitudes[i % 4]}"))
    s2_rows = [_tweet("shared-1", "zq other0x support"), _tweet("s2-2", "zq other1x"), _tweet("s2-3", "zq other2x")]
    _write_input_csv(tmp_path / "S1.csv", s1_rows)
    _write_input_csv(tmp_path / "S2.csv", s2_rows)
    master = [
        _master_row("S1", "S1-01", include="zzzneverhit", split_terms="zq", max_cluster_records="20"),
        _master_row("S1", "S1-99", split_terms="zq", is_fallback="true", max_cluster_records="20"),
        _master_row("S2", "S2-01", include="zzzneverhit", split_terms="zq", max_cluster_records="20"),
        _master_row("S2", "S2-99", split_terms="zq", is_fallback="true", max_cluster_records="20"),
    ]

    def force_low(members, backend="deterministic"):   # 8件以上=Low、分割後の小さい子=High
        status = "Low" if len(members) >= 8 else "High"
        return semantic.CoherenceResult(status=status, representative_diversity=0.9, backend=backend,
                                         avg_text_similarity=0.0, avg_dominant_share=0.1, sample_size=len(members))

    monkeypatch.setattr(semantic, "evaluate_cluster_coherence", force_low)
    result = cp.run_preprocess([str(tmp_path / "S1.csv"), str(tmp_path / "S2.csv")], master)

    assert any(c.recursive_split_applied for c in result.analysis_clusters.values())   # S1側は実際に分割された
    s2_row = next(r for r in result.record_map_rows if r["record_id"] == "shared-1" and r["signal_source"] == "S2")
    assert s2_row["analysis_cluster_id"] in result.analysis_clusters
    assert "-S" not in s2_row["analysis_cluster_id"]          # S2のrecordはS1の子Clusterへ書き換えられない
    s1_row = next(r for r in result.record_map_rows if r["record_id"] == "shared-1" and r["signal_source"] == "S1")
    assert "-S" in s1_row["analysis_cluster_id"]              # S1のrecordは分割後のClusterを指す
    ac_ids = set(result.analysis_clusters)
    assert all(r["analysis_cluster_id"] in ac_ids for r in result.record_map_rows if r["analysis_cluster_id"])


# ---------------------------------------------------------------------------
# #2 text_for_review欠損 / #6 exception理由
# 実測: US fixtureの空欄代表(ユニーク12,073件)のうち、純粋RT 11,931件(98.8%)・FB添付のみ105件・
#       共有投稿マーカー14件・rawも本当に空23件が、全て同じFact_Only_Candidateに入っていた。
#       exception 48,175件はreview_reasonが100%同一の定数だった。
# ---------------------------------------------------------------------------

def test_review_text_status_distinguishes_why_text_is_empty():
    assert cp.get_review_text_status("t", "body", "public_social", "body") == "has_text"
    assert cp.get_review_text_status("Twitter Post", "RT @a: hello", "public_social", "") == "retweet_only"
    assert cp.get_review_text_status("FB", "----- ATTACHMENT ----- Title: x", "public_social", "") \
        == "attachment_or_shared_only"
    assert cp.get_review_text_status("FB", "===== BEGIN SHARED POST ===== x", "public_social", "") \
        == "attachment_or_shared_only"
    assert cp.get_review_text_status("FB", "", "public_social", "") == "missing_body"
    assert cp.get_review_text_status("", "", "public_social", "") == "metadata_only"
    assert cp.get_review_text_status("", "body text", "media", "") == "title_missing"


def test_pure_retweet_representative_keeps_fact_only_class_but_is_labeled_retweet_only(tmp_path):
    result = _run_mixed_class_cluster(tmp_path)
    reps = [r for st in result.analysis_clusters.values() for r in st.representatives]
    rt = next(r for r in reps if r["record_id"] == "d0")
    assert rt["text_for_review"] == ""
    assert rt["source_class_preliminary"] == "Fact_Only_Candidate"      # source classの仕様は変えない
    assert rt["review_text_status"] == "retweet_only"
    assert "review_text_status" in cp.CLUSTER_REPRESENTATIVE_COLUMNS
    rows = cp.build_cluster_representative_rows(result.analysis_clusters)
    assert set(rows[0].keys()) == set(cp.CLUSTER_REPRESENTATIVE_COLUMNS)


def test_exception_reasons_are_specific_not_one_catch_all():
    rep = {"match_status": "fallback_context", "attitude_hint": "unknown", "text_for_review": "",
           "reaction_type_hint": "Question|Emotion"}
    assert cp.get_exception_reasons(rep) == ["fallback_match", "missing_text", "multiple_reaction"]
    assert cp.get_exception_reasons({"match_status": "matched", "attitude_hint": "mixed",
                                      "text_for_review": "x", "reaction_type_hint": "Evaluation"}) \
        == ["mixed_attitude"]
    assert cp.get_exception_reasons({"match_status": "matched", "attitude_hint": "unknown",
                                      "text_for_review": "x", "reaction_type_hint": ""}) == ["unknown_attitude"]


def test_exception_review_rows_carry_specific_reason_and_text_status(tmp_path):
    result = _run_mixed_class_cluster(tmp_path)
    rows = cp.build_exception_review_rows(result.sentiment_clusters)
    assert rows
    assert all(r["review_reason"] != "mixed_or_multiple_reaction_or_fallback" for r in rows)
    rt_row = next(r for r in rows if r["record_id"] == "d0")
    assert "missing_text" in rt_row["review_reason"].split("|")
    assert rt_row["review_text_status"] == "retweet_only"


# ---------------------------------------------------------------------------
# #9 configured_start_date / #10 analysis_contract
# 実測: Evidence fixtureはmonitoring_scope.configured_start_dateが全6 Signalで空（masterの
#       trend_start_dateが空）。Prompt 2 EI契約はこの列をSignal別analysis_start_dateとして参照する。
# ---------------------------------------------------------------------------

def test_resolve_effective_start_priority_master_then_run_period_then_actual():
    # 観測期間(Runの期間)が無い場合は従来どおり、マスターの設定値が最優先
    assert cp.resolve_effective_start("2026-07-01", None, "2026-09-01") == ("2026-07-01", "master_trend_start_date")
    # 観測期間がある場合は、設定開始日と観測期間開始日の遅い方（観測期間より前の履歴は今回のRunに含まれない）
    assert cp.resolve_effective_start("2026-07-01", "2026-08-01", "2026-09-01") == ("2026-08-01", "run_period_start")
    assert cp.resolve_effective_start("2026-08-15", "2026-08-01", "2026-09-01") == ("2026-08-15", "master_trend_start_date")
    assert cp.resolve_effective_start("", "2026-08-01T00:00:00Z", "2026-09-01") == ("2026-08-01", "run_period_start")
    assert cp.resolve_effective_start("", None, "2026-09-01") == ("2026-09-01", "actual_min_date")
    assert cp.resolve_effective_start("not-a-date", "2026-08-01", "2026-09-01")[1] == "run_period_start"
    assert cp.resolve_effective_start("", None, "") == ("", "")


def test_monitoring_scope_keeps_configured_start_empty_and_reports_effective_start(tmp_path):
    result = _run_mixed_class_cluster(tmp_path)
    row = cp.build_monitoring_scope_rows(result)[0]
    assert row["configured_start_date"] == ""                       # 「設定値」の意味は変えない
    assert row["effective_start_date"] == "2026-09-10"
    assert row["effective_start_source"] == "actual_min_date"
    row = cp.build_monitoring_scope_rows(result, run_period_start="2026-09-01")[0]
    assert (row["effective_start_date"], row["effective_start_source"]) == ("2026-09-01", "run_period_start")


def test_resolve_analysis_contract_names():
    assert cp.resolve_analysis_contract("public_voice_monitor", "v4.3") == "public_voice_monitor_v4_3"
    assert cp.resolve_analysis_contract("public_voice_monitor", None) == "public_voice_monitor_v4_3"
    assert cp.resolve_analysis_contract("evidence_intelligence", "evidence_intelligence_v1") == "evidence_intelligence_v1"
    assert cp.resolve_analysis_contract("evidence_intelligence", None) == "evidence_intelligence_v1"
    assert cp.resolve_analysis_contract(None, "v4.3") == ""


def test_report_config_yaml_adds_mode_and_contract_only_when_given(tmp_path):
    result = _run_mixed_class_cluster(tmp_path)
    common = dict(report_id="r1", topic="evidence_intelligence_report", primary_graph_signals=[],
                  final_graph_rule="not_applicable", created_at="2026-10-03T00:00:00")
    legacy = cp.build_report_config_yaml(result, **common)
    assert "analysis_contract" not in legacy and "analysis_mode" not in legacy
    new = cp.build_report_config_yaml(result, **common, analysis_mode="evidence_intelligence",
                                       analysis_contract="evidence_intelligence_v1")
    assert "analysis_mode: evidence_intelligence\n" in new
    assert "analysis_contract: evidence_intelligence_v1\n" in new
    assert "analysis_policy: narrative_master_v4_final_streaming_cluster_review" in new  # 既存キーは不変


# ---------------------------------------------------------------------------
# #7 semantic_coherence_status
# 実測: US fixtureのcap-hit cluster 669(Analysis)/608(Sentiment)は、representatives評価でも全件評価でも
#       Lowが0件。representative_diversityの最大値はAnalysis 0.574、Sentiment 0.500。
#       （SentimentはLow閾値0.65に構造的に届かない）
# ---------------------------------------------------------------------------

def _member(i, *, attitude="positive", bucket="public_social", terms="gov", words=6):
    text = " ".join(f"w{i}x{j}y" for j in range(words))
    return {"record_id": f"r{i}", "title": "", "text_for_review": text, "attitude_hint": attitude,
            "source_bucket": bucket, "matched_terms": terms, "source_class_preliminary": "Public_Voice_Candidate",
            "reaction_type_hint": "Evaluation", "match_status": "matched" if terms else "fallback",
            "language_group": "English"}


def _stats_for(members, *, max_records):
    rule = cp.build_rule_from_row(_master_row("S1", "S1-01", include="x", max_cluster_records=str(max_records)))
    stats = cp.ClusterStats(
        cluster_id="AC_synth", kind="analysis", rule=rule, week_start="2026-09-07", week_end="2026-09-13",
        source_class_preliminary="Public_Voice_Candidate", source_bucket="public_social",
        reaction_type_hint="Evaluation", attitude_hint="positive", cluster_cue="general", match_signature="sig",
        representative_limit=8)
    stats.record_count = len(members)
    stats.representatives = list(members[:8])
    stats.all_members = list(members)
    return stats


def test_low_and_recursive_split_are_reachable_for_heterogeneous_cap_hit_cluster():
    attitudes = ["positive", "negative", "mixed", "unknown"]
    buckets = ["media", "public_social", "official", "unknown"]
    members = [_member(i, attitude=attitudes[i % 4], bucket=buckets[(i // 4) % 4], terms=f"term{i % 4}")
               for i in range(20)]
    assert semantic.evaluate_cluster_coherence(members).status == "Low"
    stats = _stats_for(members, max_records=20)
    result = cp.apply_recursive_split({"AC_synth": stats})
    assert stats.cluster_cap_hit and stats.semantic_coherence_status == "Low"
    children = [c for c in result.values() if c.recursive_split_applied]
    assert len(children) >= 2
    assert all(c.parent_cluster_id == "AC_synth" for c in children if c.recursive_split_depth == 1)
    assert sum(c.record_count for c in children) == 20


def test_heterogeneous_flag_is_reachable_when_low_but_no_discriminator_has_enough_members():
    attitudes = ["positive", "negative", "mixed", "unknown"]
    buckets = [f"bucket{i}" for i in range(6)]
    members = [_member(i, attitude=attitudes[i % 4], bucket=buckets[i % 6], terms=f"term{i}") for i in range(12)]
    stats = _stats_for(members, max_records=12)
    result = cp.apply_recursive_split({"AC_synth": stats})
    assert stats.semantic_coherence_status == "Low"
    assert stats.heterogeneous_cluster is True        # 分割候補が無いため分割せず、異質フラグだけ立つ
    assert not stats.recursive_split_applied
    assert list(result) == ["AC_synth"]


def test_known_limitation_key_constant_dimensions_cap_diversity_below_low_threshold():
    """Sentiment/Analysis Clusterではattitude/source_bucket/matched_termsがcluster keyに含まれ（または
    key由来で）一定のため、本文が全く重ならなくてもdiversityが0.5付近で頭打ちになりLowに届かない。
    実データでLowが出ない主因。閾値/入力次元を直したらこのテストは意図的に更新する。"""
    members = [_member(i) for i in range(8)]       # attitude/bucket/terms一定、本文は互いに素
    result = semantic.evaluate_cluster_coherence(members)
    assert result.avg_dominant_share == 1.0
    assert result.representative_diversity <= 0.5
    assert result.status == "Medium"


def test_known_limitation_cjk_similarity_is_underestimated_compared_to_english():
    """CJKは2文字以上の連続文字列全体が1トークンになる（句読点・ASCIIでのみ区切られる）ため、ほぼ同じ文でも
    類似度が低く出て、diversityが過大になる（英語は0.75、日本語は0.33）。"""
    ja_a = semantic.tokenize("習近平主席は米国訪問で首脳会談を行い、関税問題について協議した")
    ja_b = semantic.tokenize("習近平主席は米国訪問で首脳会談を行い、関税問題について議論した")
    en_a = semantic.tokenize("Xi Jinping held a summit in the US and discussed tariff issues with leaders")
    en_b = semantic.tokenize("Xi Jinping held a summit in the US and talked about tariff issues with leaders")
    assert semantic.jaccard_similarity(ja_a, ja_b) < semantic.jaccard_similarity(en_a, en_b)


def test_cheap_pass_can_miss_a_cluster_that_full_evaluation_would_call_low():
    """representatives(8件)だけの1段目がLow以外だと全件評価へ進まない設計のため、サンプルの偏りで
    Lowを見逃し得る（合成データで再現。US実データではどちらでもLow 0件のため実害は未確認）。"""
    attitudes = ["positive"] * 5 + ["negative", "mixed", "unknown"]       # 偏ったサンプル(8件)
    reps = [_member(i, attitude=attitudes[i], terms="") for i in range(8)]
    # 残り12件を足すと全体では4種類が5件ずつの完全に異質な構成になる
    rest = [_member(8 + i, attitude=["negative", "mixed", "unknown"][i % 3], terms="") for i in range(12)]
    cheap = semantic.evaluate_cluster_coherence(reps)
    full = semantic.evaluate_cluster_coherence(reps + rest)
    assert cheap.status != "Low"
    assert full.representative_diversity > cheap.representative_diversity


def test_cluster_labeling_rows_expose_source_class_purity_for_mixed_analysis_clusters(tmp_path):
    """#3: Analysis Clusterのラベルは最頻値になったが、混在自体は消えない。混在度を列で出す
    （US fixtureではAnalysis Clusterの1,491/31,227件が複数source classの混在、純度<1.0）。"""
    result = _run_mixed_class_cluster(tmp_path)
    ac_row = cp.build_cluster_labeling_rows(result.analysis_clusters)[0]
    assert ac_row["source_distribution"] == "Fact_Only_Candidate:1|Public_Voice_Candidate:3"
    assert ac_row["source_class_purity"] == 0.75
    assert "source_class_purity" in cp.CLUSTER_LABELING_COLUMNS
    for row in cp.build_cluster_labeling_rows(result.sentiment_clusters):
        assert row["source_class_purity"] == 1.0                     # Sentiment Clusterは常に純粋
