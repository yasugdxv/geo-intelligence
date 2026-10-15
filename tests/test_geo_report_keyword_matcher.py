"""keyword_matcher_v2（geo_report_keyword_matcher と match_master の統合）のテスト。

実データの根拠（US中間選挙、標本48,309件）: 旧matcherは部分一致のため、`ICE`が21,868件にヒットし誤ヒット
21,164件（office/prices/vice/service/police）、`govern`は17,858件のうち17,675件が`Governor`/`government`、
split termの`ad`は36,667件のうち31,799件が`had`/`head`/`already`だった。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_current_pipeline_service as cp  # noqa: E402
import geo_report_keyword_matcher as K  # noqa: E402


def hit(term: str, text: str) -> bool:
    return bool(K.term_spans(term, K.prepare_text(text)))


@pytest.mark.parametrize("term,text,expected", [
    # --- Latin token（exact token） ---
    ("ICE", "ICE raids in Phoenix", True), ("ICE", "they protest against ICE.", True),
    ("ICE", "office", False), ("ICE", "justice and police service", False), ("ICE", "gas prices vice", False),
    ("ICE", "an ice storm hit", False),                       # 大文字アクロニムはcase-sensitive
    ("AI", "AI規制が進む", True), ("AI", "he said", False), ("US", "tell us about it", False),
    # --- 語幹展開しない ---
    ("govern", "govern the state", True), ("govern", "Governor Hobbs", False), ("govern", "governance", False),
    ("govern", "governing", False), ("govern", "government", False),
    ("rent", "rent is rising", True), ("rent", "parent apparent different current", False), ("rent", "rents", False),
    ("lose", "they lose", True), ("lose", "close closed closely", False), ("lose", "losing", False),
    ("beat", "beat them", True), ("beat", "Beatty heartbeat", False),
    # --- 明示した語形（* = 語頭一致） ---
    ("rent*", "rents renter rental", True), ("rent*", "parent current", False),
    ("vote*", "voters voted votes", True), ("vote*", "devoted", False),
    # --- Latin phrase ---
    ("cost of living", "Cost-of-Living crisis", True), ("cost of living", "cost  of　living", True),
    ("cost of living", "the cost, of living", True), ("cost of living", "living cost of", False),
    ("health care", "health-care costs", True), ("border security", "Border   Security!", True),
    ("public safety", "public-safety", True), ("data center", "data centers", False),
    ("election integrity", "election integrity", True), ("US-China summit", "US China summit", True),
    ("don't", "DON’T do it", True), ("Katie Hobbs", "Hobbs's rival Katie Hobbs", True), ("Hobbs", "Hobbs's", True),
    # --- CJK（Latin用の語境界を使わない） ---
    ("关税", "美国对华关税政策", True), ("関税", "米国の関税 引き上げ", True), ("AI規制", "AI規制が進む", True),
    ("관세", "미국 관세 정책", True), ("关税", "关 税", True), ("台湾", "台灣", False),
])
def test_keyword_matcher_contract(term, text, expected):
    assert hit(term, text) is expected


def test_term_kinds():
    assert K.term_kind("ICE") == K.KIND_ACRONYM and K.term_kind("gop") == K.KIND_TOKEN
    assert K.term_kind("rent*") == K.KIND_PREFIX and K.term_kind("cost of living") == K.KIND_PHRASE
    assert K.term_kind("关税") == K.KIND_CJK and K.term_kind("AI規制") == K.KIND_CJK


def test_matcher_does_not_reorder_phrase_words():
    assert not hit("cost of living", "living of cost")


def test_original_surface_is_kept_in_the_compiled_term():
    compiled = K.compile_term("Cost-of-Living")
    assert compiled.surface == "Cost-of-Living" and compiled.needle == " cost of living "


# ---------------------------------------------------------------------------
# match_master の統合
# ---------------------------------------------------------------------------

def rule(narrative, *, include="", required="", exclude="", cues="", priority="Medium", fallback=False, split=""):
    return cp.build_rule_from_row({
        "signal_source": "S1", "signal_name": "t", "theme_id": "T", "theme_name": "T", "sub_narrative": narrative,
        "sub_narrative_id": narrative, "include_keywords": include, "required_context_keywords": required,
        "exclude_keywords": exclude, "public_voice_cues": cues, "cluster_split_terms": split,
        "priority": priority, "is_fallback": "true" if fallback else "false", "active": "true"})


FALLBACK = rule("S1-00", fallback=True)


def match(rules, text):
    return cp.match_master(rules, FALLBACK, text)


def test_ice_in_office_does_not_fire_but_acronym_does():
    r = rule("S1-08", include="ICE; immigration enforcement")
    assert match([r], "She works at the office of justice")["match_status"] == "fallback"
    m = match([r], "protest against ICE agents")
    assert m["match_status"] == "matched" and m["matched_terms"] == "ICE" and m["match_method"] == "latin_acronym"


def test_governor_alone_does_not_trigger_the_governance_narrative():
    """US S*-10: include `govern`、required context `Governor`。同一surfaceの部分一致だけで成立させない。"""
    r = rule("S1-10", include="govern; governing", required="Katie Hobbs; Governor")
    assert match([r], "Governor Katie Hobbs spoke today")["match_status"] == "fallback"
    m = match([r], "Katie Hobbs is governing badly")
    assert m["match_status"] == "matched" and m["matched_terms"] == "governing"


def test_include_that_is_part_of_the_context_term_never_self_fires_even_if_mentioned_twice():
    r = rule("S1-10", include="governor", required="Governor")
    assert match([r], "Governor Hobbs said the governor will sign")["match_status"] == "fallback"


def test_context_inside_a_longer_include_phrase_is_allowed():
    """US S*-01: include `support Katie Hobbs`、required `Katie Hobbs`（includeが文脈を含むだけ）。"""
    r = rule("S1-01", include="support Katie Hobbs", required="Katie Hobbs; Hobbs")
    m = match([r], "I support Katie Hobbs for governor")
    assert m["match_status"] == "matched"
    assert m["matched_terms"] == "support Katie Hobbs" and m["matched_context_terms"] == "Katie Hobbs|Hobbs"


def test_include_and_context_evidence_are_recorded_separately_with_spans():
    r = rule("S1-07", include="rent*; cost of living", required="Arizona")
    m = match([r], "Arizona cost-of-living and rents are up")
    assert m["match_status"] == "matched"
    assert m["matched_terms"] == "rent*|cost of living"
    assert m["matched_context_terms"] == "Arizona"
    assert m["match_method"] == "latin_phrase|latin_prefix"
    assert m["matched_span"].startswith("inc:") and ";ctx:" in m["matched_span"]
    assert m["matched_exclude_terms"] == ""


def test_exclude_blocks_the_rule_and_exact_exclude_does_not_block_on_substring():
    r = rule("S1-01", include="support Hobbs", exclude="sports")
    assert match([r], "support Hobbs at the sports bar")["match_status"] == "fallback"
    assert match([r], "support Hobbs, show your passports")["match_status"] == "matched"


def test_fallback_statuses_and_exclude_terms_use_the_new_matcher():
    fb = rule("S1-00", fallback=True, required="Arizona", exclude="sports")
    r = rule("S1-01", include="zzz")
    assert cp.match_master([r], fb, "Arizona news")["match_status"] == "fallback_context"
    assert cp.match_master([r], fb, "nothing relevant")["match_status"] == "fallback_no_required_context"
    m = cp.match_master([r], fb, "sports in Arizona")
    assert m["match_status"] == "fallback_excluded_context" and m["matched_exclude_terms"] == "sports"
    assert cp.match_master([r], fb, "passports in Arizona")["match_status"] == "fallback_context"


def test_priority_and_cue_scoring_still_choose_the_highest_score_rule():
    high = rule("S1-A", include="inflation", priority="High")
    low = rule("S1-B", include="inflation", priority="Low")
    assert match([low, high], "inflation hurts")["rule"].sub_narrative_id == "S1-A"
    plain = rule("S1-C", include="tariffs", priority="Medium")
    cued = rule("S1-D", include="tariffs", priority="Medium", cues="too expensive")
    assert match([plain, cued], "tariffs are too expensive")["rule"].sub_narrative_id == "S1-D"


def test_cjk_include_matches_without_latin_word_boundary():
    r = rule("S1-N1", include="关税; 関税")
    assert match([r], "美国对华关税政策升级")["match_status"] == "matched"


def test_cluster_cue_uses_exact_token_for_split_terms():
    """split term `ad`が`had`/`head`に当たっていた（標本で31,799件の誤ヒット）。"""
    assert cp.get_cluster_cue("she had a headache already", "", ["ad"]) == "general"
    assert cp.get_cluster_cue("the new attack ad is running", "", ["ad"]) == "ad"
    assert cp.get_cluster_cue("he said ICE agents came", "", ["ICE"]) == "ICE"
    assert cp.get_cluster_cue("the office closed", "", ["ICE"]) == "general"


@pytest.mark.parametrize("term,text,expected", [
    ("corruption", "#StopTrumpCorruption now", True),         # CamelCaseのハッシュタグを語に分割
    ("Trump", "#StopTrumpCorruption", True),
    ("Katie Hobbs", "@KatieHobbs spoke today", True),
    ("Hobbs", "#KatieHobbs", True),
    ("Barr", "@BarrForSenate", True),
    ("MAGA", "#MAGA", True), ("MAGA", "#MAGAHats", True),      # 全大文字+語は`MAGA Hats`に分割（acronym+Word境界）
    ("corruption", "#stoptrumpcorruption", False),             # 全て小文字は分割できない（既知の制限）
    ("ICE", "#ICEraids", False),
    ("ice", "office", False),
])
def test_hashtag_and_handle_camel_case_segmentation(term, text, expected):
    assert hit(term, text) is expected
