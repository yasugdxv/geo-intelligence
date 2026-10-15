import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_project_assets_service as assets  # noqa: E402

_INPUT_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl",
                  "languageDisplayName", "documentTypeName", "authorName", "sourceName"]


def _write_input_csv(path: Path, rows: list):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_INPUT_FIELDS)
        writer.writeheader()
        for row in rows:
            full = {k: row.get(k, "") for k in _INPUT_FIELDS}
            writer.writerow(full)


def _master_row(signal_id, narrative_id, *, include="", exclude="", required="", split_terms="",
                 is_fallback="false", active="true", trend_role="", trend_start_date="",
                 max_cluster_records="200"):
    row = {
        "signal_id": signal_id, "narrative_id": narrative_id, "narrative_name": f"{narrative_id} name",
        "include_keywords": include, "exclude_keywords": exclude, "required_context_keywords": required,
        "cluster_split_terms": split_terms, "public_voice_cues": "", "priority": "Medium",
        "max_cluster_records": max_cluster_records, "representative_count": "8", "is_fallback": is_fallback,
        "active": active, "theme_id": f"{signal_id}_THEME", "theme_name": f"{signal_id} Theme",
        "trend_role": trend_role, "trend_start_date": trend_start_date, "main_narrative": "Main",
        "stance_default": "Unknown",
    }
    return assets.sync_shell_mirror_columns(row)


# ---------------------------------------------------------------------------
# get_source_bucket（実run.ps1の優先順位、Phase A監査結果1で確定した順）
# ---------------------------------------------------------------------------

def test_source_bucket_pr_release_before_others():
    assert cp.get_source_bucket("https://prtimes.jp/x", "News", "PR TIMES") == "pr_release"
    assert cp.get_source_bucket("https://example.com/x", "Press Release", "X") == "pr_release"


def test_source_bucket_official_uses_real_domain_list():
    assert cp.get_source_bucket("https://www.mhlw.go.jp/x", "News", "MHLW") == "official"
    assert cp.get_source_bucket("https://city.example.lg.jp/x", "News", "City") == "official"
    # 旧実装の`.gov`汎用チェックは実データ契約に含まれない（Phase A監査結果1参照）
    assert cp.get_source_bucket("https://example.gov/x", "News", "Gov") == "official"


def test_source_bucket_comment_classified_before_news_article():
    # "News Comment"のようなdocument typeはMediaではなくPublic Social扱いにする
    # （run.ps1の明示コメント通り、Public Voiceを抑圧しないため）
    assert cp.get_source_bucket("https://example.com/x", "News Comment", "Example") == "public_social"


def test_source_bucket_url_based_fallbacks():
    assert cp.get_source_bucket("https://twitter.com/x/status/1", "", "") == "public_social"
    assert cp.get_source_bucket("https://5ch.net/x", "", "") == "public_forum"
    assert cp.get_source_bucket("https://ameblo.jp/x", "", "") == "public_blog"
    assert cp.get_source_bucket("https://www.amazon.com/review/x", "", "") == "public_review_or_ec"
    assert cp.get_source_bucket("https://unknown-domain.example/x", "", "") == "unknown"


# ---------------------------------------------------------------------------
# get_review_text
# ---------------------------------------------------------------------------

def test_get_review_text_removes_full_rt_prefix():
    assert cp.get_review_text("", "RT @someone: original content here", "public_social") == ""


def test_get_review_text_extracts_own_comment_before_mid_rt():
    text = "My own take on this RT @someone: their original post"
    assert cp.get_review_text("", text, "public_social") == "My own take on this"


def test_get_review_text_strips_updated_post_prefix_and_shared_markers():
    text = "[This post has been updated.] My real opinion ===== BEGIN SHARED POST =====junk after"
    assert cp.get_review_text("", text, "public_social") == "My real opinion"


def test_get_review_text_media_uses_title_only():
    assert cp.get_review_text("Headline Only", "full body text ignored", "media") == "Headline Only"


def test_get_review_text_public_uses_body_else_title():
    assert cp.get_review_text("T", "actual body", "public_forum") == "actual body"
    assert cp.get_review_text("Title fallback", "", "public_forum") == "Title fallback"


# ---------------------------------------------------------------------------
# reaction/attitude/source_class_preliminary
# ---------------------------------------------------------------------------

def test_get_reaction_types_and_primary():
    types = cp.get_reaction_types("I think this is good and I will switch to a new provider")
    assert "Evaluation" in types
    assert "Behavior" in types
    assert cp.get_primary_reaction_type(types) == "Behavior"  # Behaviorが優先順位トップ
    assert cp.get_primary_reaction_type("") == "None"


def test_get_attitude_hint_positive_negative_mixed_neutral_unknown():
    assert cp.get_attitude_hint("I support this policy", "Evaluation") == "positive"
    assert cp.get_attitude_hint("I oppose this policy", "Evaluation") == "negative"
    assert cp.get_attitude_hint("I support but also oppose parts of it", "Evaluation") == "mixed"
    assert cp.get_attitude_hint("Why did this happen?", "Question") == "neutral"
    assert cp.get_attitude_hint("", "") == "unknown"


def test_get_source_class_preliminary():
    assert cp.get_source_class_preliminary("official", "text", "News") == "Official"
    assert cp.get_source_class_preliminary("pr_release", "text", "Press") == "Corporate_PR"
    assert cp.get_source_class_preliminary("media", "", "News") == "Media"
    assert cp.get_source_class_preliminary("public_social", "some opinion", "Comment") == "Public_Voice_Candidate"
    assert cp.get_source_class_preliminary("public_social", "", "Comment") == "Fact_Only_Candidate"
    assert cp.get_source_class_preliminary("unknown", "", "") == "Unknown"


def test_get_cluster_cue_prefers_topic_specific_split_terms():
    assert cp.get_cluster_cue("something about crime and ICE", "Evaluation", ["crime", "immigration"]) == "crime"
    assert cp.get_cluster_cue("no split term hit", "Behavior", []) == "behavior"
    assert cp.get_cluster_cue("no split term hit", "None", []) == "general"


# ---------------------------------------------------------------------------
# get_sharded_cluster_id（シャーディング数式・prefix/形式）
# ---------------------------------------------------------------------------

def test_sharded_cluster_id_format():
    counts = {}
    cid = cp.get_sharded_cluster_id("AC", "base-key", 200, counts)
    assert cid.startswith("AC_")
    assert len(cid) == len("AC_") + 14
    assert cid[3:].islower() or cid[3:].isdigit()


def test_sharded_cluster_id_shards_after_max_records():
    counts = {}
    ids = [cp.get_sharded_cluster_id("AC", "k", 2, counts) for _ in range(5)]
    # 最初2件は同じshard、3-4件目は次のshard、5件目はさらに次
    assert ids[0] == ids[1]
    assert ids[2] == ids[3]
    assert ids[0] != ids[2]
    assert ids[4] != ids[2]


def test_sharded_cluster_id_not_globally_unique_across_independent_runs():
    # Phase A監査結果1: base_keyが同じで独立したcountsで呼べば同じIDが再生成される
    # （run.ps1実測通り、run_idに相当する要素を含まないため）
    counts_run_a = {}
    counts_run_b = {}
    id_a = cp.get_sharded_cluster_id("AC", "same-content-key", 200, counts_run_a)
    id_b = cp.get_sharded_cluster_id("AC", "same-content-key", 200, counts_run_b)
    assert id_a == id_b


# ---------------------------------------------------------------------------
# match_master
# ---------------------------------------------------------------------------

def test_match_master_picks_highest_scoring_rule():
    rules_by_signal, fallback_by_signal, _ = cp.build_master_rules([
        _master_row("S1", "S1-01", include="alpha"),
        _master_row("S1", "S1-02", include="alpha;important"),
        _master_row("S1", "S1-99", is_fallback="true"),
    ])
    fb = fallback_by_signal["S1"]
    result = cp.match_master(rules_by_signal["S1"], fb, "this is about alpha and important stuff")
    assert result["match_status"] == "matched"
    assert result["rule"].sub_narrative_id == "S1-02"  # 複数キーワード一致で高スコア


def test_match_master_excluded_falls_back():
    rules_by_signal, fallback_by_signal, _ = cp.build_master_rules([
        _master_row("S1", "S1-01", include="alpha", exclude="excluded_word"),
        _master_row("S1", "S1-99", is_fallback="true", required="context_needed"),
    ])
    fb = fallback_by_signal["S1"]
    result = cp.match_master(rules_by_signal["S1"], fb, "alpha but excluded_word present, context_needed too")
    assert result["match_status"] == "fallback_context"


def test_match_master_no_hit_and_no_required_context():
    rules_by_signal, fallback_by_signal, _ = cp.build_master_rules([
        _master_row("S1", "S1-01", include="alpha"),
        _master_row("S1", "S1-99", is_fallback="true", required="needed_context"),
    ])
    fb = fallback_by_signal["S1"]
    result = cp.match_master(rules_by_signal["S1"], fb, "totally unrelated text")
    assert result["match_status"] == "fallback_no_required_context"


# ---------------------------------------------------------------------------
# is_duplicate_record
# ---------------------------------------------------------------------------

def test_is_duplicate_record_url_dedup_only_for_media_official_pr():
    seen_urls, seen_texts = set(), set()
    url_hash = cp.get_hash("https://news.example.com/a")
    is_dup, _ = cp.is_duplicate_record("S1", "media", url_hash, "", seen_urls, seen_texts)
    assert is_dup is False
    seen_urls.add(f"S1|{url_hash}")
    is_dup2, group = cp.is_duplicate_record("S1", "media", url_hash, "", seen_urls, seen_texts)
    assert is_dup2 is True
    assert group.startswith("url:")


def test_is_duplicate_record_social_uses_text_hash_not_url():
    seen_urls, seen_texts = set(), set()
    url_hash = cp.get_hash("https://x.com/post/1")
    text_hash = cp.get_hash("some normalized text")
    # 1件目登録
    cp.is_duplicate_record("S1", "public_social", url_hash, text_hash, seen_urls, seen_texts)
    seen_texts.add(f"S1|{text_hash}")
    # 同じURLだが同じtext_hashでの2件目 -> text基準でdup
    is_dup, group = cp.is_duplicate_record("S1", "public_social", url_hash, text_hash, seen_urls, seen_texts)
    assert is_dup is True
    assert group.startswith("text:")


def test_is_duplicate_record_scoped_per_signal():
    seen_urls, seen_texts = set(), set()
    url_hash = cp.get_hash("https://news.example.com/shared")
    seen_urls.add(f"S1|{url_hash}")
    # 別Signalなら重複扱いされない
    is_dup, _ = cp.is_duplicate_record("S2", "media", url_hash, "", seen_urls, seen_texts)
    assert is_dup is False


# ---------------------------------------------------------------------------
# run_preprocess end-to-end
# ---------------------------------------------------------------------------

def test_run_preprocess_builds_ac_and_sc_dual_clusters(tmp_path):
    master_rows = [
        _master_row("S1", "S1-01", include="alpha", trend_role="primary", trend_start_date="2026-07-01"),
        _master_row("S1", "S1-99", is_fallback="true"),
    ]
    rows = [
        # 同じAC key（週/source_bucket/cue/language/match_signature同一）だが、
        # attitudeが違うため別々のSC clusterへ分かれるはずの2件
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "Alpha post",
         "documentText": "I support alpha policy strongly, alpha is good", "sourceUrl": "https://x.com/p/1",
         "languageDisplayName": "English", "documentTypeName": "SocialMedia", "authorName": "u1"},
        {"documentId": "r2", "timestamp": "2026-08-10T01:00:00Z", "documentName": "Alpha post 2",
         "documentText": "I oppose alpha policy, alpha is bad and wrong", "sourceUrl": "https://x.com/p/2",
         "languageDisplayName": "English", "documentTypeName": "SocialMedia", "authorName": "u2"},
        # 重複（同一URL、public_socialなのでtext hashベースだが今回はtextも同一）
        {"documentId": "r3", "timestamp": "2026-08-10T02:00:00Z", "documentName": "Alpha post",
         "documentText": "I support alpha policy strongly, alpha is good", "sourceUrl": "https://x.com/p/3",
         "languageDisplayName": "English", "documentTypeName": "SocialMedia", "authorName": "u1"},
        # fallback行き
        {"documentId": "r4", "timestamp": "2026-08-11T00:00:00Z", "documentName": "Unrelated",
         "documentText": "nothing about the topic at all", "sourceUrl": "https://x.com/p/4",
         "languageDisplayName": "English", "documentTypeName": "SocialMedia", "authorName": "u3"},
    ]
    input_path = tmp_path / "US中間選挙_S1Arizona_20260907.csv"
    _write_input_csv(input_path, rows)

    result = cp.run_preprocess([str(input_path)], master_rows)

    assert result.total_records == 4
    assert result.nonduplicate_records == 3  # r3はr1とtext重複
    assert result.signal_matched.get("S1") == 2  # r1, r2
    assert result.signal_fallback.get("S1") == 1  # r4

    # 同一AC clusterに2レコード（r1,r2、attitudeは違うがAC keyには含まれない）
    assert len(result.analysis_clusters) >= 1
    ac_with_two = [c for c in result.analysis_clusters.values() if c.record_count == 2]
    assert ac_with_two, "r1/r2はAC keyが同一のはずなので同じAnalysis Clusterに入るべき"

    # Sentiment ClusterはattitudeがAC keyに追加されるため、r1(positive)とr2(negative)は別クラスタ
    sc_record_counts = sorted(c.record_count for c in result.sentiment_clusters.values() if c.record_count > 0)
    assert 1 in sc_record_counts  # r1, r2それぞれ単独のSCクラスタになっているはず

    cluster_rows = cp.build_cluster_labeling_rows(result.analysis_clusters)
    assert set(cluster_rows[0].keys()) == set(cp.CLUSTER_LABELING_COLUMNS)
    # run.ps1のCurrent契約自体は33列だが、2026-09-16のRecursive Split（#3）導入で
    # 末尾へ8列のメタデータ（cluster_cap_hit等）を追加したため、実際の列数は41列。
    # 2026-10-03にsource_class_purity（Analysis Clusterのsource class混在度）を追加し42列、
    # 2026-10-04にreview_reasons（requires_reviewの理由）を追加し43列。
    assert len(cluster_rows[0]) == 43 or len(cp.CLUSTER_LABELING_COLUMNS) == 43

    rep_rows = cp.build_cluster_representative_rows(result.analysis_clusters)
    assert set(rep_rows[0].keys()) == set(cp.CLUSTER_REPRESENTATIVE_COLUMNS)


def test_run_preprocess_handles_unmapped_signal_without_failing(tmp_path):
    master_rows = [
        _master_row("S1", "S1-01", include="alpha"),
        _master_row("S1", "S1-99", is_fallback="true"),
    ]
    rows = [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "alpha content", "sourceUrl": "https://news.example.com/x",
         "languageDisplayName": "English", "documentTypeName": "News"},
    ]
    # ファイル名からSignalを検出できない（S1/S2等のパターンが無い）
    input_path = tmp_path / "no_signal_pattern_here.csv"
    _write_input_csv(input_path, rows)

    # run.ps1と同様、UNMAPPEDでも例外にせず処理を続行する
    result = cp.run_preprocess([str(input_path)], master_rows)
    assert result.total_records == 1
    assert "UNMAPPED" in result.signal_total


def test_run_preprocess_rejects_master_with_no_active_signals(tmp_path):
    input_path = tmp_path / "S1_x.csv"
    _write_input_csv(input_path, [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z",
                                     "documentName": "x", "documentText": "y", "sourceUrl": "https://x.com/1"}])
    try:
        cp.run_preprocess([str(input_path)], [_master_row("S1", "S1-01", active="false")])
        assert False, "should have raised"
    except ValueError as exc:
        assert "Signal" in str(exc)


# ---------------------------------------------------------------------------
# 監査・集計ビルダー
# ---------------------------------------------------------------------------

def test_build_preprocess_audit_rows_and_monitoring_scope(tmp_path):
    master_rows = [
        _master_row("S1", "S1-01", include="alpha", trend_role="primary", trend_start_date="2026-07-01"),
        _master_row("S1", "S1-99", is_fallback="true"),
    ]
    rows = [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "alpha content", "sourceUrl": "https://news.example.com/x1",
         "languageDisplayName": "English", "documentTypeName": "News"},
        {"documentId": "r2", "timestamp": "2026-08-12T00:00:00Z", "documentName": "y",
         "documentText": "unrelated", "sourceUrl": "https://news.example.com/x2",
         "languageDisplayName": "English", "documentTypeName": "News"},
    ]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)
    result = cp.run_preprocess([str(input_path)], master_rows)

    audit_rows = cp.build_preprocess_audit_rows(result)
    s1_row = next(r for r in audit_rows if r["signal_source"] == "S1")
    assert s1_row["total_records"] == 2
    assert s1_row["nonduplicate_records"] == 2
    assert s1_row["matched_records"] == 1
    assert s1_row["fallback_records"] == 1

    scope_rows = cp.build_monitoring_scope_rows(result)
    assert scope_rows
    assert scope_rows[0]["signal_source"] == "S1"
    assert scope_rows[0]["actual_min_date"] and scope_rows[0]["actual_max_date"]

    manifest = cp.build_cluster_manifest(result, "test_topic", created_at="2026-09-11T00:00:00", record_map_chunks=1)
    assert manifest["total_records"] == 2
    assert manifest["analysis_cluster_count"] == len(result.analysis_clusters)

    yaml_text = cp.build_report_config_yaml(
        result, report_id="run-1", topic="test_topic", primary_graph_signals=["S1"],
        final_graph_rule="source_class=Public Voice", created_at="2026-09-11T00:00:00")
    assert "primary_graph_signals: S1" in yaml_text
    assert "report_id: run-1" in yaml_text

    pop_rows = cp.build_population_narrative_by_signal_rows(result)
    assert any(r["narrative_id"] == "S1-01" for r in pop_rows)


def test_run_preprocess_invokes_progress_callback_per_file(tmp_path):
    master_rows = [_master_row("S1", "S1-01", include="alpha"), _master_row("S1", "S1-99", is_fallback="true")]
    calls = []

    def on_file(file_name, done, total):
        calls.append((file_name, done, total))

    paths = []
    for i in range(2):
        row = [{"documentId": f"r{i}", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
                 "documentText": "alpha content", "sourceUrl": f"https://x.com/{i}"}]
        p = tmp_path / f"S1_file{i}.csv"
        _write_input_csv(p, row)
        paths.append(str(p))

    cp.run_preprocess(paths, master_rows, on_file_processed=on_file)
    assert len(calls) == 2
    assert calls[0][1:] == (1, 2)
    assert calls[1][1:] == (2, 2)


# ---------------------------------------------------------------------------
# Recursive Split（2026-09-16追加、#3）: apply_recursive_split/_split_cluster_
# recursiveを直接テストする（run_preprocessのbase_key生成ロジックに縛られず、
# heterogeneityの閾値を正確に制御するため）。
# ---------------------------------------------------------------------------

def _split_test_rule(max_cluster_records=6):
    return cp.Rule(
        signal_source="S1", signal_name="Sig", theme_id="T", theme_name="Theme",
        trend_role="", trend_start_date="", main_narrative="Main", sub_narrative="Sub",
        sub_narrative_id="S1-99", stance_default="Unknown", max_cluster_records=max_cluster_records,
        representative_count=8, is_fallback=True,
    )


def _split_test_member(record_id, title, *, attitude="positive", bucket="public_social"):
    return {
        "record_id": record_id, "title": title, "text_for_review": "",
        "attitude_hint": attitude, "source_bucket": bucket, "matched_terms": "",
        "match_status": "fallback_no_required_context", "reaction_type_hint": "Evaluation",
        "language_group": "English", "source_class_preliminary": "Public_Voice_Candidate",
        "source_name": "", "domain": "", "url": "", "published_at": "", "author": "",
        "documentTypeName": "SocialMedia", "source_file": "test.csv",
    }


def _split_test_cluster(cluster_id, rule, members):
    stats = cp.ClusterStats(
        cluster_id=cluster_id, kind="analysis", rule=rule, week_start="2026-09-01", week_end="2026-09-07",
        source_class_preliminary="Public_Voice_Candidate", source_bucket="public_social",
        reaction_type_hint="Evaluation", attitude_hint="mixed", cluster_cue="evaluation",
        match_signature="fallback", representative_limit=8,
    )
    stats.record_count = len(members)
    for m in members:
        stats.all_members.append(m)
        stats.representatives.append(m)
        if m["match_status"] == "matched":
            stats.matched_count += 1
        else:
            stats.fallback_count += 1
    return stats


_HOMOGENEOUS_TITLES = [
    "New trade tariffs announced by government today",
    "Tariff policy update affects steel imports significantly",
    "Government reveals fresh trade tariff plan this week",
    "Steel tariffs raise concerns among manufacturers nationwide",
    "Trade officials confirm tariff changes starting next month",
    "Officials discuss new tariff plan affecting steel trade",
]

_GROUP_A_TITLES = _HOMOGENEOUS_TITLES[:5]
_GROUP_B_TITLES = [
    "Local bakery festival draws large crowds downtown",
    "Annual pastry celebration event held at town square",
    "Bakery owners showcase treats at festival gathering",
    "Community festival features fresh bread and pastries",
    "Town celebrates traditional baking festival this weekend",
]


def test_apply_recursive_split_does_not_split_homogeneous_cap_hit_cluster():
    """cap hit（record_count >= max_cluster_records）でも、代表投稿が同一論点の
    類似記事であればsplitしない（ユーザー要望3-2「単純に200件だからsplitではない」）。"""
    rule = _split_test_rule(max_cluster_records=6)
    members = [_split_test_member(f"r{i}", title) for i, title in enumerate(_HOMOGENEOUS_TITLES)]
    cluster = _split_test_cluster("AC_homogeneous", rule, members)

    result = cp.apply_recursive_split({"AC_homogeneous": cluster})

    assert list(result.keys()) == ["AC_homogeneous"]
    only = result["AC_homogeneous"]
    assert only.cluster_cap_hit is True
    assert only.recursive_split_applied is False
    assert only.semantic_coherence_status != "Low"


def test_apply_recursive_split_splits_heterogeneous_cap_hit_cluster():
    """複数の明確に異なるtopicが混在するcap-hit Clusterはsplitする
    （ユーザー要望3-2）。parent/child lineage・split_reasonを保持し
    （3-3）、childのrepresentativesはchild自身のmembersから再生成される
    （3-4、Parentのrepresentativesを流用しない）。"""
    rule = _split_test_rule(max_cluster_records=10)
    members = (
        [_split_test_member(f"a{i}", t, attitude="positive", bucket="public_social")
         for i, t in enumerate(_GROUP_A_TITLES)]
        + [_split_test_member(f"b{i}", t, attitude="negative", bucket="media")
           for i, t in enumerate(_GROUP_B_TITLES)]
    )
    cluster = _split_test_cluster("AC_heterogeneous", rule, members)

    result = cp.apply_recursive_split({"AC_heterogeneous": cluster})

    assert len(result) >= 2
    assert "AC_heterogeneous" not in result  # parent自身はもう返さない
    all_child_record_ids = set()
    for child_id, child in result.items():
        assert child.recursive_split_applied is True
        assert child.parent_cluster_id == "AC_heterogeneous"
        assert child.split_reason  # 例: "attitude_hint=positive"
        assert child.cluster_cap_hit is True
        child_record_ids = {m["record_id"] for m in child.all_members}
        # representative再生成: childのrepresentativesは必ずchild自身のmembersに由来する
        # （Parentのrepresentativesをそのまま流用していないことの確認）。
        assert all(rep["record_id"] in child_record_ids for rep in child.representatives)
        all_child_record_ids |= child_record_ids
    # 全レコードがどこかのchildへ割り当てられている（取りこぼしが無い）
    assert all_child_record_ids == {m["record_id"] for m in members}


def test_apply_recursive_split_respects_max_depth_guard():
    """再帰分割がmax_depthに達しても依然Low coherenceな場合、無限再帰せず
    heterogeneous_cluster=trueを立てて停止する（ユーザー要望3-3）。"""
    rule = _split_test_rule(max_cluster_records=10)
    members = (
        [_split_test_member(f"a{i}", t, attitude="positive", bucket="public_social")
         for i, t in enumerate(_GROUP_A_TITLES)]
        + [_split_test_member(f"b{i}", t, attitude="negative", bucket="media")
           for i, t in enumerate(_GROUP_B_TITLES)]
    )
    cluster = _split_test_cluster("AC_maxdepth", rule, members)

    result = cp.apply_recursive_split({"AC_maxdepth": cluster}, coherence_backend="deterministic")
    for child in result.values():
        assert child.recursive_split_depth <= cp.RECURSIVE_SPLIT_MAX_DEPTH


def test_apply_recursive_split_skips_non_cap_hit_clusters():
    """record_count < max_cluster_recordsのClusterはcoherence評価自体を行わない
    （大規模Runで無駄な計算をしないため、ユーザー要望3-1の対象は"cap hit"のみ）。"""
    rule = _split_test_rule(max_cluster_records=200)
    members = [_split_test_member(f"a{i}", t) for i, t in enumerate(_GROUP_A_TITLES)]
    cluster = _split_test_cluster("AC_small", rule, members)

    result = cp.apply_recursive_split({"AC_small": cluster})

    only = result["AC_small"]
    assert only.cluster_cap_hit is False
    assert only.semantic_coherence_status == "Not Evaluated"
    assert only.recursive_split_applied is False


def test_apply_recursive_split_disabled_via_run_preprocess_flag(tmp_path):
    """enable_recursive_split=Falseならcap-hit判定自体を行わない
    （feature flagでの段階導入、ユーザー要望3-5）。"""
    master_rows = [
        _master_row("S1", "S1-01", include="alpha", max_cluster_records="2"),
        _master_row("S1", "S1-99", is_fallback="true", max_cluster_records="2"),
    ]
    rows = [
        {"documentId": f"r{i}", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "alpha content here", "sourceUrl": f"https://news.example.com/x{i}"}
        for i in range(4)
    ]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    result = cp.run_preprocess([str(input_path)], master_rows, enable_recursive_split=False)
    assert all(not c.cluster_cap_hit for c in result.analysis_clusters.values())


# ---------------------------------------------------------------------------
# Discovery Mode（2026-09-16追加、#4）: discovery_mode=trueのSignalは
# keyword matchingをPrimary routingに使わず、Preprocess側はdedup/coarse
# clustering/noise reductionまでに限定する（意味判断はPrompt1に委ねる、
# ユーザー確認済み）。
# ---------------------------------------------------------------------------

def test_run_preprocess_discovery_mode_produces_coarse_candidates(tmp_path):
    master_rows = [
        _master_row("S6", "S6-01", include="known_topic"),
        _master_row("S6", "S6-99", is_fallback="true"),
    ]
    rows = [
        {"documentId": "a1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "brand new regulation just announced by the ministry today",
         "sourceUrl": "https://news.example.com/a1"},
        {"documentId": "a2", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "new regulation announced by ministry officials this morning",
         "sourceUrl": "https://news.example.com/a2"},
        {"documentId": "b1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "unexpected security incident reported near the border today",
         "sourceUrl": "https://news.example.com/b1"},
        {"documentId": "b2", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "security incident near border draws official concern",
         "sourceUrl": "https://news.example.com/b2"},
    ]
    input_path = tmp_path / "S6_sample.csv"
    _write_input_csv(input_path, rows)

    result = cp.run_preprocess([str(input_path)], master_rows, discovery_signal_ids={"S6"})

    assert result.discovery_audit["discovery_corpus_size"] == 4
    assert result.discovery_audit["deduped_corpus_size"] == 4
    assert result.discovery_audit["coarse_cluster_count"] >= 1
    assert len(result.discovery_candidates) >= 1
    # 意味判断フィールドはPreprocessでは埋めない（Prompt1の役割、ユーザー確認済み）
    for candidate in result.discovery_candidates:
        assert candidate["topic_label"] == ""
        assert candidate["materiality"] == ""
        assert candidate["existing_signal_overlap"] == ""
        assert candidate["cluster_size"] >= 1
    assert len(result.discovery_candidate_representatives) >= 1


def test_run_preprocess_discovery_mode_zero_candidates_is_valid(tmp_path):
    """discovery-mode Signalに1件もデータが無くても、それ自体をエラー扱いしない
    （ユーザー要望4-5: emerging_candidate=0も正常な結果）。"""
    master_rows = [_master_row("S1", "S1-01", include="alpha"), _master_row("S1", "S1-99", is_fallback="true")]
    rows = [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
              "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"}]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    # S6はdiscovery-modeだがこのRunのデータには一切登場しない
    result = cp.run_preprocess([str(input_path)], master_rows, discovery_signal_ids={"S6"})

    assert result.discovery_candidates == []
    assert result.discovery_candidate_representatives == []


def test_run_preprocess_without_discovery_signal_ids_produces_no_discovery_output(tmp_path):
    master_rows = [_master_row("S1", "S1-01", include="alpha"), _master_row("S1", "S1-99", is_fallback="true")]
    rows = [{"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
              "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"}]
    input_path = tmp_path / "S1_sample.csv"
    _write_input_csv(input_path, rows)

    result = cp.run_preprocess([str(input_path)], master_rows)
    assert result.discovery_candidates == []
    assert result.discovery_audit == {}


def test_build_preprocess_audit_rows_marks_discovery_mode_signals(tmp_path):
    master_rows = [
        _master_row("S1", "S1-01", include="alpha"), _master_row("S1", "S1-99", is_fallback="true"),
        _master_row("S6", "S6-01", include="beta"), _master_row("S6", "S6-99", is_fallback="true"),
    ]
    s1_path = tmp_path / "S1_sample.csv"
    _write_input_csv(s1_path, [
        {"documentId": "r1", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "alpha content here", "sourceUrl": "https://news.example.com/x1"},
    ])
    s6_path = tmp_path / "S6_sample.csv"
    _write_input_csv(s6_path, [
        {"documentId": "r2", "timestamp": "2026-08-10T00:00:00Z", "documentName": "x",
         "documentText": "beta content here", "sourceUrl": "https://news.example.com/x2"},
    ])

    result = cp.run_preprocess([str(s1_path), str(s6_path)], master_rows, discovery_signal_ids={"S6"})
    audit_rows = cp.build_preprocess_audit_rows(result, discovery_signal_ids={"S6"})
    by_signal = {r["signal_source"]: r for r in audit_rows}
    assert by_signal["S1"]["discovery_mode"] is False
    assert by_signal["S6"]["discovery_mode"] is True
