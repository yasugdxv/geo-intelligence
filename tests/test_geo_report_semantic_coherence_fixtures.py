"""semantic coherence / recursive split の合成fixture（A〜D）による到達性・誤分割防止のテスト（2026-10-04）。

実データ（US、cap-hit 669 cluster）ではdeterministic backendのLowが0件で、diversityの最大値は0.574（Analysis）/
0.500（Sentiment）だった。ここでは実データを強制splitせず、合成fixtureで次を保証する:
  A homogeneous           : 200件すべて同じ意味 -> splitしない
  B heterogeneous         : tariff/Taiwan/AI/Iran 各50件 -> Lowを検知し4分割
  C syndicated duplicates : 同じ内容のMedia転載200件 -> 量が多いだけではsplitしない
  D mixed source          : Government/Media/Socialでも同じ意味 -> source違いだけではsplitしない
既定のdeterministic backendは変更していないため、B/Dはlexical_v2で検証し、deterministicの限界も記録する。
"""
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_cluster_semantic_service as semantic  # noqa: E402
import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_project_assets_service as assets  # noqa: E402

V2 = semantic.BACKEND_LEXICAL_V2
TOPICS = {
    "tariff": ["tariff", "trade", "import", "duties", "customs", "levy"],
    "taiwan": ["taiwan", "strait", "island", "beijing", "invasion", "navy"],
    "ai": ["artificial", "intelligence", "chips", "model", "regulation", "compute"],
    "iran": ["iran", "hormuz", "tanker", "sanctions", "nuclear", "crude"],
}
# 反応語・極性語を避ける（全recordをcue=general・同じAnalysis Clusterに入れるため）
_INPUT_FIELDS = ["documentId", "timestamp", "documentName", "documentText", "sourceUrl",
                 "languageDisplayName", "documentTypeName", "authorName"]


def topic_text(topic: str, i: int, rng: random.Random) -> str:
    fillers = [f"{topic}filler{k}" for k in rng.sample(range(40), 3)]
    return " ".join(TOPICS[topic] + fillers + [f"doc{i}x"])


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_INPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def master():
    base = {"signal_id": "S1", "exclude_keywords": "", "required_context_keywords": "", "public_voice_cues": "",
            "cluster_split_terms": "", "priority": "Medium", "max_cluster_records": "200",
            "representative_count": "8", "active": "true", "theme_id": "S1_T", "theme_name": "S1 Theme",
            "trend_role": "", "trend_start_date": "", "main_narrative": "Main", "stance_default": "Unknown"}
    return [
        assets.sync_shell_mirror_columns({**base, "narrative_id": "S1-01", "narrative_name": "n1",
                                          "include_keywords": "zzzneverhit", "is_fallback": "false"}),
        assets.sync_shell_mirror_columns({**base, "narrative_id": "S1-99", "narrative_name": "fb",
                                          "include_keywords": "", "is_fallback": "true"}),
    ]


def social_rows(texts):
    return [{"documentId": f"r{i}", "timestamp": "2026-09-10T10:00:00Z", "documentName": f"Post {i}",
             "documentText": t, "sourceUrl": f"https://twitter.com/u{i}/status/{i}", "languageDisplayName": "English",
             "documentTypeName": "TwitterSocialMedia", "authorName": f"u{i}"} for i, t in enumerate(texts)]


def run(tmp_path, rows, backend):
    write_csv(tmp_path / "S1_fixture.csv", rows)
    return cp.run_preprocess([str(tmp_path / "S1_fixture.csv")], master(), coherence_backend=backend)


def cap_clusters(result):
    return [c for c in result.analysis_clusters.values() if c.cluster_cap_hit or c.parent_cluster_id]


# ---------------- A: homogeneous ----------------

def test_fixture_a_homogeneous_200_posts_is_high_and_not_split(tmp_path):
    rng = random.Random(1)
    result = run(tmp_path, social_rows([topic_text("tariff", i, rng) for i in range(200)]), V2)
    assert len(result.analysis_clusters) == 1
    stats = next(iter(result.analysis_clusters.values()))
    assert stats.record_count == 200 and stats.cluster_cap_hit
    assert stats.semantic_coherence_status == "High"
    assert stats.representative_diversity <= semantic._V2_HIGH_DIVERSITY
    assert not stats.recursive_split_applied and not stats.heterogeneous_cluster and stats.split_reason == ""


# ---------------- B: heterogeneous ----------------

def test_fixture_b_four_topics_is_low_and_split_by_topic(tmp_path):
    rng = random.Random(2)
    texts = [topic_text(t, i, rng) for i in range(50) for t in TOPICS]          # 4話題 x 50件
    result = run(tmp_path, social_rows(texts), V2)
    children = [c for c in result.analysis_clusters.values() if c.recursive_split_applied]
    assert len(children) == 4
    assert all(c.recursive_split_depth == 1 and c.split_reason.startswith("lexical_anchor=") for c in children)
    assert sorted(c.record_count for c in children) == [50, 50, 50, 50]
    assert all(c.semantic_coherence_status == "High" for c in children)         # 分割後は各話題が均質で再分割しない
    anchors = {c.split_reason.split("=", 1)[1] for c in children}
    assert len(anchors) == 4
    # record mapは分割後のcluster IDを指し、全recordが存在するclusterに属する
    ids = set(result.analysis_clusters)
    assert all(r["analysis_cluster_id"] in ids for r in result.record_map_rows if r["analysis_cluster_id"])
    # 各子clusterの中身は1つの話題だけ
    for child in children:
        topics_seen = {t for m in child.all_members for t in TOPICS if TOPICS[t][0] in m["text_for_review"].split()}
        assert len(topics_seen) == 1


def test_fixture_b_low_branch_is_reachable_and_parent_records_low(tmp_path):
    rng = random.Random(3)
    texts = [topic_text(t, i, rng) for i in range(50) for t in TOPICS]
    members = [{"record_id": f"r{i}", "title": "", "text_for_review": t, "attitude_hint": "unknown",
                "source_bucket": "public_social", "matched_terms": "", "source_class_preliminary": "Public_Voice_Candidate",
                "reaction_type_hint": "", "match_status": "fallback", "language_group": "English"}
               for i, t in enumerate(texts)]
    result = semantic.evaluate_cluster_coherence(members, backend=V2)
    assert result.status == "Low" and result.representative_diversity >= semantic._V2_LOW_DIVERSITY
    split = semantic.choose_split_discriminator(members, backend=V2)
    assert split is not None and split[0] == "lexical_anchor" and len(split[1]) == 4


def test_fixture_b_is_not_detected_by_the_default_backend_known_limitation(tmp_path):
    """既定のdeterministic backendは、cluster key由来で一定の次元が占有率1.0になるため、4話題混在でも
    Lowに届かない（実データでLowが0件の理由）。lexical_v2を既定へ昇格したらこのテストは意図的に更新する。"""
    rng = random.Random(2)
    texts = [topic_text(t, i, rng) for i in range(50) for t in TOPICS]
    result = run(tmp_path, social_rows(texts), semantic.BACKEND_DETERMINISTIC)
    stats = next(iter(result.analysis_clusters.values()))
    assert stats.semantic_coherence_status != "Low" and not stats.recursive_split_applied


# ---------------- C: syndicated duplicates ----------------

def test_fixture_c_200_syndicated_media_copies_are_not_split_by_volume(tmp_path):
    headline = "tariff trade import duties customs levy announced"
    body = "tariff trade import duties customs levy details beijing washington negotiators"
    rows = [{"documentId": f"m{i}", "timestamp": "2026-09-10T10:00:00Z", "documentName": headline,
             "documentText": f"{body} outlet{i}x wire", "sourceUrl": f"https://news{i}.example.com/a",
             "languageDisplayName": "English", "documentTypeName": "News", "authorName": ""} for i in range(200)]
    result = run(tmp_path, rows, V2)
    assert result.nonduplicate_records == 200                                  # 本文がoutlet毎に違うのでdedupされない
    stats = next(iter(result.analysis_clusters.values()))
    assert stats.record_count == 200 and stats.cluster_cap_hit
    assert stats.semantic_coherence_status == "High" and stats.representative_diversity == 0.0
    assert not stats.recursive_split_applied and not stats.heterogeneous_cluster


# ---------------- D: mixed source, same meaning ----------------

def mixed_source_members(rng):
    buckets = ["official", "media", "public_social"]
    members = []
    for i in range(90):
        members.append({
            "record_id": f"r{i}", "title": "", "text_for_review": topic_text("tariff", i, rng),
            "attitude_hint": "unknown", "source_bucket": buckets[i % 3], "matched_terms": "",
            "source_class_preliminary": ["Official", "Media", "Public_Voice_Candidate"][i % 3],
            "reaction_type_hint": "", "match_status": "fallback", "language_group": "English"})
    return members


def test_fixture_d_same_meaning_across_sources_is_high_and_not_split_by_lexical_v2():
    members = mixed_source_members(random.Random(4))
    assert semantic.evaluate_cluster_coherence(members, backend=V2).status == "High"
    rule = cp.build_rule_from_row(dict(master()[1], max_cluster_records="90"))
    stats = cp.ClusterStats(
        cluster_id="AC_d", kind="analysis", rule=rule, week_start="2026-09-07", week_end="2026-09-13",
        source_class_preliminary="Media", source_bucket="media", reaction_type_hint="None", attitude_hint="unknown",
        cluster_cue="general", match_signature="fallback", representative_limit=8)
    stats.record_count, stats.representatives, stats.all_members = 90, members[:8], members
    result = cp.apply_recursive_split({"AC_d": stats}, coherence_backend=V2)
    assert list(result) == ["AC_d"]
    assert stats.cluster_cap_hit and stats.semantic_coherence_status == "High"
    assert not stats.recursive_split_applied and not stats.heterogeneous_cluster


def test_fixture_d_default_backend_would_treat_source_differences_as_diversity():
    """deterministic backendはsource_bucketの占有率をdiversityに入れる。同じ意味でもsourceが混ざると
    diversityが上がる（v2はsourceを入力に使わない）。"""
    members = mixed_source_members(random.Random(4))
    legacy = semantic.evaluate_cluster_coherence(members)
    v2 = semantic.evaluate_cluster_coherence(members, backend=V2)
    assert legacy.avg_dominant_share < 1.0
    assert legacy.representative_diversity > v2.representative_diversity


# ---------------- 追加の性質 ----------------

def test_cjk_homogeneous_posts_are_high_under_v2_even_though_deterministic_underestimates_similarity():
    members = [{"record_id": f"r{i}", "title": "", "text_for_review": f"習近平主席は米国訪問で関税問題について{w}した",
                "attitude_hint": "unknown", "source_bucket": "public_social", "matched_terms": ""}
               for i, w in enumerate(["協議", "議論", "交渉", "確認"] * 5)]
    assert semantic.evaluate_cluster_coherence(members, backend=V2).status == "High"
    assert semantic.tokenize_v2("関税問題") == {"関税", "税問", "問題"}


def test_metadata_only_members_do_not_make_a_cluster_look_heterogeneous():
    members = [{"record_id": f"r{i}", "title": "", "text_for_review": ""} for i in range(10)]
    assert semantic.evaluate_cluster_coherence(members, backend=V2).status == "High"


def test_unsupported_backend_still_raises():
    import pytest
    with pytest.raises(NotImplementedError):
        semantic.evaluate_cluster_coherence([{"title": "a b c"}] * 3, backend="embedding")
