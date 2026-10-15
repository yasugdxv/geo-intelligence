import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_cluster_semantic_service as semantic  # noqa: E402


def _member(title, text="", attitude="neutral", bucket="media", matched=""):
    return {"title": title, "text_for_review": text, "attitude_hint": attitude,
            "source_bucket": bucket, "matched_terms": matched}


def test_tokenize_filters_stopwords_and_short_tokens():
    tokens = semantic.tokenize("The new tariff deal is a big deal for the US")
    assert "the" not in tokens
    assert "is" not in tokens
    assert "tariff" in tokens
    assert "deal" in tokens


def test_jaccard_similarity_identical_and_disjoint():
    a = {"tariff", "deal", "china"}
    b = {"tariff", "deal", "china"}
    assert semantic.jaccard_similarity(a, b) == 1.0
    assert semantic.jaccard_similarity(a, {"unrelated", "topic"}) == 0.0


def test_dominant_share_all_same_vs_spread():
    assert semantic.dominant_share(["a", "a", "a"]) == 1.0
    assert semantic.dominant_share(["a", "b", "c", "d"]) == 0.25


def test_evaluate_cluster_coherence_high_for_similar_representatives():
    members = [
        _member("Trump Xi summit trade deal announced", "trade deal announced by both sides", matched="trade deal"),
        _member("Trade deal between Trump and Xi announced today", "the trade deal was announced", matched="trade deal"),
        _member("Summit trade deal reached, Trump Xi confirm", "trade deal confirmed", matched="trade deal"),
    ]
    result = semantic.evaluate_cluster_coherence(members)
    assert result.status == "High"
    assert result.representative_diversity < 0.45


def test_evaluate_cluster_coherence_low_for_heterogeneous_representatives():
    members = [
        _member("Trade tariffs raised on steel imports", "tariff hike on steel", attitude="negative",
                bucket="media", matched="tariffs"),
        _member("Local bakery wins award for best croissant", "the bakery celebrates its award",
                attitude="positive", bucket="public_blog", matched=""),
        _member("Senator gives speech on unrelated healthcare bill", "healthcare bill debate continues",
                attitude="neutral", bucket="official", matched=""),
        _member("New laser welding technology unveiled by manufacturer", "manufacturer unveils laser welding",
                attitude="neutral", bucket="pr_release", matched=""),
    ]
    result = semantic.evaluate_cluster_coherence(members)
    assert result.status == "Low"
    assert result.representative_diversity >= 0.65


def test_evaluate_cluster_coherence_handles_single_or_no_members():
    assert semantic.evaluate_cluster_coherence([]).status == "High"
    assert semantic.evaluate_cluster_coherence([_member("solo")]).status == "High"


def test_evaluate_cluster_coherence_rejects_unknown_backend():
    try:
        semantic.evaluate_cluster_coherence([_member("a"), _member("b")], backend="embedding")
        assert False, "should have raised"
    except NotImplementedError:
        pass


def test_choose_split_discriminator_prefers_matched_terms_when_balanced():
    members = (
        [_member(f"trade item {i}", matched="trade deal") for i in range(6)] +
        [_member(f"taiwan item {i}", matched="taiwan strait") for i in range(6)]
    )
    result = semantic.choose_split_discriminator(members)
    assert result is not None
    discriminator, groups = result
    assert discriminator == "matched_terms"
    assert set(groups.keys()) == {"trade deal", "taiwan strait"}
    assert len(groups["trade deal"]) == 6
    assert len(groups["taiwan strait"]) == 6


def test_choose_split_discriminator_falls_back_to_salient_token_when_no_field_discriminates():
    members = (
        [_member("steel tariffs announcement today") for _ in range(6)] +
        [_member("bakery award ceremony held today") for _ in range(6)]
    )
    result = semantic.choose_split_discriminator(members)
    assert result is not None
    discriminator, groups = result
    assert discriminator == "salient_token"
    assert len(groups) >= 2


def test_choose_split_discriminator_returns_none_when_homogeneous():
    members = [_member("same topic every time", matched="same phrase") for _ in range(10)]
    result = semantic.choose_split_discriminator(members)
    assert result is None
