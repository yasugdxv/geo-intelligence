"""Geopolitical Intelligence OS: Reading Exposure / Diversity Layerのテスト。

Sのrank/main_scenario/alternative_scenariosの内容は一切変更されないこと、Frontier
Readingがrejectedなhypothesisも候補から除外しないがstatusを上書きしないこと、
Top3とのsemantic duplicateが除外されること、Expert Exploration生成が「本当に必要な
場合のみ1回だけ」呼ばれることを中心に検証する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_ios_reading_exposure_composer as composer  # noqa: E402


def _candidate(origin, description, rank, derived_from=None):
    return {
        "rank": rank, "displayed": rank <= 5, "title": description[:20],
        "content": {
            "origin": origin, "why_this_matters": description,
            "raw": {"description": description, "derived_from_hypothesis_id": derived_from},
        },
    }


def _hypothesis(hid, description, status="retained", hypothesis_type="H1_instrumental_indirect",
                 reason="十分に具体的な理由テキストがここに入っており評価材料として機能する。"):
    return {
        "hypothesis_id": hid, "hypothesis_type": hypothesis_type, "description": description,
        "status": status, "reason": reason, "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [], "weakening_evidence_relevance": [],
    }


# ---- select_frontier_reading -----------------------------------------------------

def test_frontier_excludes_hypotheses_already_used_by_top3():
    hyps = [_hypothesis("H0", "メインシナリオの説明文そのもの")]
    result = composer.select_frontier_reading(hyps, top3_texts=[], used_hypothesis_ids={"H0"})
    assert result is None


def test_frontier_excludes_semantic_duplicate_of_top3():
    text = "トランプ大統領は中国への圧力を維持しつつ企業の競争力も守ろうとしている"
    # reasonを空にしてtop3_textsとの比較対象をdescriptionのみに揃えつつ、
    # supporting_evidence_idsでevidence anchor要件は満たしておく（重複判定だけを見る）
    h = _hypothesis("H1", text, reason="")
    h["supporting_evidence_ids"] = ["E1"]
    result = composer.select_frontier_reading([h], top3_texts=[text], used_hypothesis_ids=set())
    assert result is None


def test_frontier_excludes_unsupported_hypothesis_without_evidence_anchor():
    hyps = [_hypothesis("H2", "短い", reason="短い")]
    result = composer.select_frontier_reading(hyps, top3_texts=[], used_hypothesis_ids=set())
    assert result is None


def test_frontier_does_not_exclude_rejected_status():
    hyps = [_hypothesis("H4", "従来の前提を覆す新しい読み方がここにある", status="rejected",
                          hypothesis_type="H4_frame_breaking")]
    result = composer.select_frontier_reading(hyps, top3_texts=["全く別の内容"], used_hypothesis_ids=set())
    assert result is not None
    assert result["status"] == "rejected"  # Sの判定を上書きしない


def test_frontier_prefers_frame_breaking_type_when_novelty_tied():
    common_top3 = ["トランプは強い圧力をかけつつ交渉を進める"]
    ordinary = _hypothesis("H2", "議会は超党派で法案を支持している", hypothesis_type="H2_counterparty_audience_shift")
    frame_breaking = _hypothesis("H4", "そもそも圧力をかけたいという前提自体が誤っている可能性がある",
                                   hypothesis_type="H4_frame_breaking")
    result = composer.select_frontier_reading([ordinary, frame_breaking], top3_texts=common_top3,
                                                used_hypothesis_ids=set())
    assert result["hypothesis_id"] == "H4"


# ---- expand_hypothesis_to_reading_card --------------------------------------------

def test_expand_does_not_fabricate_missing_fields():
    h = _hypothesis("H3", "国内制約が主因であるという読み", reason="議会構成の圧力が背景にある")
    card = composer.expand_hypothesis_to_reading_card(h)
    assert card["original_hypothesis_status"] == "retained"
    assert card["supporting_evidence"] == []
    assert card["missing_evidence"] is not None  # 明示的なEvidence参照が無いことを正直に記録
    assert "国内制約が主因であるという読み" in card["reasoning_story"]


def test_expand_uses_weakening_evidence_relevance_when_present():
    h = _hypothesis("H2", "国内向けシグナルという読み")
    h["weakening_evidence_relevance"] = [{"evidence_id": "E1", "rationale": "反証となる証言がある"}]
    card = composer.expand_hypothesis_to_reading_card(h)
    assert card["weakening_evidence"] == ["反証となる証言がある"]
    assert card["missing_evidence"] is None


# ---- compose_reading_exposure ------------------------------------------------------

def test_top3_readings_are_passed_through_unchanged():
    candidates = [
        _candidate("main_scenario", "本線の説明", 1, derived_from="H0"),
        _candidate("alternative_scenario", "対抗シナリオの説明", 2, derived_from="H1"),
        _candidate("alternative_scenario", "別解シナリオの説明", 3, derived_from="H2"),
        _candidate("reading_portfolio_wildcard_non_obvious", "ワイルドカードの説明", 4),
    ]
    hyps = [_hypothesis("H0", "本線の説明"), _hypothesis("H1", "対抗シナリオの説明"),
            _hypothesis("H2", "別解シナリオの説明")]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=[],
        situation_text="dummy")
    assert readings[0]["reading_role"] == "MAIN"
    assert readings[0]["content"]["why_this_matters"] == "本線の説明"
    assert readings[1]["reading_role"] == "ALTERNATIVE"
    assert readings[2]["reading_role"] == "ALTERNATIVE"
    # Sのcontentが一切変更されていないこと
    assert readings[0]["rank"] == 1
    assert readings[1]["rank"] == 2


def test_frontier_and_exploration_slots_can_be_empty_without_forcing_five():
    candidates = [_candidate("main_scenario", "本線のみ", 1, derived_from="H0")]
    hyps = [_hypothesis("H0", "本線のみ")]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=[],
        situation_text="dummy")
    assert len(readings) == 1  # Frontier/Explorationとも候補が無いため無理に埋めない
    assert readings[0]["reading_role"] == "MAIN"


def test_exploration_prefers_portfolio_over_new_generation(monkeypatch):
    candidates = [_candidate("main_scenario", "本線の説明A", 1, derived_from="H0")]
    hyps = [_hypothesis("H0", "本線の説明A")]
    portfolio = [{
        "title": "ワイルドカード案", "content": {
            "origin": "reading_portfolio_wildcard_non_obvious",
            "raw": {"reading": "全く別の観点からの読み方がここにある", "key_cue": "別の着眼点"},
        },
    }]
    called = {"n": 0}

    def fail_if_called(*a, **kw):
        called["n"] += 1
        raise AssertionError("should not call LLM generation when portfolio already satisfies Reading5")

    monkeypatch.setattr(composer, "generate_expert_exploration_reading", fail_if_called)
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=portfolio,
        situation_text="dummy", azure_client=object(), model="gpt-test")
    assert called["n"] == 0
    exploratory = [r for r in readings if r["reading_role"] == "EXPLORATORY"]
    assert len(exploratory) == 1
    assert exploratory[0]["source"] == "reading_portfolio"


def test_exploration_generation_called_exactly_once_when_pool_insufficient(monkeypatch):
    candidates = [_candidate("main_scenario", "本線の説明B", 1, derived_from="H0")]
    hyps = [_hypothesis("H0", "本線の説明B")]
    called = {"n": 0}

    def fake_generate(azure_client, model, *, situation_text, existing_readings):
        called["n"] += 1
        return {"title": "新規探索案", "reading": "新しい観点の読み", "supporting_reasons": ["根拠1"],
                "weakening_information": []}

    monkeypatch.setattr(composer, "generate_expert_exploration_reading", fake_generate)
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=[],
        situation_text="dummy", azure_client=object(), model="gpt-test")
    assert called["n"] == 1
    exploratory = [r for r in readings if r["reading_role"] == "EXPLORATORY"]
    assert len(exploratory) == 1
    assert exploratory[0]["source"] == "expert_exploration_generated"


def test_exploration_generation_not_called_without_azure_client():
    candidates = [_candidate("main_scenario", "本線の説明C", 1, derived_from="H0")]
    hyps = [_hypothesis("H0", "本線の説明C")]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=[],
        situation_text="dummy")  # azure_client/model省略
    exploratory = [r for r in readings if r["reading_role"] == "EXPLORATORY"]
    assert exploratory == []


def test_readings_never_exceed_five():
    candidates = [
        _candidate("main_scenario", "本線", 1, derived_from="H0"),
        _candidate("alternative_scenario", "対抗1", 2, derived_from="H1"),
        _candidate("alternative_scenario", "対抗2", 3, derived_from="H2"),
        _candidate("alternative_scenario", "対抗3(Top3外)", 4, derived_from="H3"),
    ]
    hyps = [_hypothesis("H0", "本線"), _hypothesis("H1", "対抗1"), _hypothesis("H2", "対抗2"),
            _hypothesis("H3", "対抗3(Top3外)"),
            _hypothesis("H4", "全く別の前提を疑う読み方", hypothesis_type="H4_frame_breaking")]
    portfolio = [{"title": "wildcard", "content": {
        "origin": "reading_portfolio_wildcard_non_obvious",
        "raw": {"reading": "もう一つ別のワイルドカード読み", "key_cue": "着眼点"}}}]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=portfolio,
        situation_text="dummy")
    assert len(readings) <= 5


def test_exploration_prefers_on_topic_grounded_candidate_over_lexically_distant_wildcard():
    """2026-09-24修正の回帰テスト: HORMUZ_BLOCKADE_SESSION3で、Situationの中心論点
    （ホルムズ海峡・原油輸出）から外れた話題的に遠いwildcard（地域協力枠組み）が、
    文字N-gram距離が大きいというだけの理由でSituationの中心を再解釈する
    premise_reversal相当の候補より優先されてしまう問題が無いことを確認する。"""
    situation_text = "イランがホルムズ海峡を封鎖するかどうかは、自国の原油輸出への依存度に左右される。"
    candidates = [_candidate("main_scenario",
                              "イランはホルムズ海峡封鎖を最大圧力カードとして保持しつつ、原油輸出への依存から全面封鎖は避ける",
                              1, derived_from="H0")]
    hyps = [_hypothesis("H0",
                         "イランはホルムズ海峡封鎖を最大圧力カードとして保持しつつ、原油輸出への依存から全面封鎖は避ける")]
    portfolio = [
        {
            "title": "受忍可能な犠牲という読み",
            "content": {
                "origin": "reading_portfolio_premise_reversal",
                "raw": {
                    "reading": "ホルムズ海峡封鎖による原油輸出の喪失は、イランにとって自殺的コストではなく、体制生存を最優先する局面では受忍可能な犠牲になりうる",
                    "key_cue": "原油輸出の喪失を自殺的とみなす前提そのもの",
                    "supporting_reasons": ["体制生存が経済的損失より優先される局面がある"],
                    "weakening_information": [],
                },
            },
        },
        {
            "title": "地域協力枠組みへの転化という読み",
            "content": {
                "origin": "reading_portfolio_wildcard_non_obvious",
                "raw": {
                    "reading": "オマーンやサウジアラビアとの地域協力の枠組みが進展すれば、遠く離れた別の外交チャンネルが局面を左右する可能性がある",
                    "key_cue": "地域協力枠組みの存在",
                    "supporting_reasons": ["複雑な折衷案が地域の安全保障構造を変えうる"],
                    "weakening_information": [],
                },
            },
        },
    ]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=portfolio,
        situation_text=situation_text)
    exploratory = [r for r in readings if r["reading_role"] == "EXPLORATORY"]
    assert len(exploratory) == 1
    # 話題的に遠いだけのwildcardではなく、Situationの中心論点(原油輸出依存の前提)を
    # 別構造で読み直すpremise_reversal相当が選ばれること
    assert "受忍可能な犠牲" in exploratory[0]["title"]


def test_no_systematic_hypothesis_register_bias_when_portfolio_is_equally_grounded():
    """2026-09-24修正（3回目）の回帰テスト: novelty bonus廃止前は、Top3+Frontierで
    5件中4件のhypothesis_registerが消費された後の『残り1件』が、ほぼ無条件に
    Portfolioの有力スロット(weight=0.5)を上回ってしまう系統的バイアスがあった
    （実データで確認: 4ケース中3ケースで0.15pt差）。この回帰テストでは、H2型
    （type_bonus=0.15）の残り1件と、Portfolioのalternative_causal_structure
    （weight=0.5）を比較し、Portfolio側が僅かでも上回れることを確認する
    （0.3+0.15=0.45 < 0.5）。"""
    situation_text = "局面Xについての分析"
    candidates = [
        _candidate("main_scenario", "本線H0の説明", 1, derived_from="H0"),
        _candidate("alternative_scenario", "対抗H1の説明", 2, derived_from="H1"),
        _candidate("alternative_scenario", "対抗H3の説明", 3, derived_from="H3"),
    ]
    hyps = [
        _hypothesis("H0", "本線H0の説明"), _hypothesis("H1", "対抗H1の説明"),
        _hypothesis("H3", "対抗H3の説明"),
        _hypothesis("H4", "前提を疑うH4の説明はここにある", status="rejected",
                     hypothesis_type="H4_frame_breaking"),
        _hypothesis("H2", "残り1件のH2説明はここにある", hypothesis_type="H2_counterparty_audience_shift"),
    ]
    portfolio = [{
        "title": "別の因果構造という読み",
        "content": {
            "origin": "reading_portfolio_alternative_causal_structure",
            "raw": {"reading": "全く異なる因果構造で局面Xを読み直す", "key_cue": "別の着眼点",
                     "supporting_reasons": ["根拠A"], "weakening_information": []},
        },
    }]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=portfolio,
        situation_text=situation_text)
    exploratory = [r for r in readings if r["reading_role"] == "EXPLORATORY"]
    assert len(exploratory) == 1
    # H2(残り1件、weight=0.3+0.15=0.45)がPortfolio(weight=0.5)に対しnovelty bonusで
    # 自動的に逆転することはない
    assert exploratory[0]["source"] == "reading_portfolio"


def test_frontier_and_exploratory_flagged_as_exploratory_not_high_confidence():
    candidates = [_candidate("main_scenario", "本線の説明D", 1, derived_from="H0")]
    hyps = [_hypothesis("H0", "本線の説明D"),
            _hypothesis("H4", "前提を疑う独立した読み方がここに存在する", status="rejected",
                         hypothesis_type="H4_frame_breaking")]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=[],
        situation_text="dummy")
    frontier = [r for r in readings if r["reading_role"] == "FRONTIER"][0]
    assert frontier["exploratory"] is True
    assert frontier["original_hypothesis_status"] == "rejected"
    assert "confidence" not in frontier or frontier.get("confidence") is None


# ---- Phase 4 Reading Exposure V2: Marginal Analytical Coverage (2026-09-24) ------
# Past Case semantic validationで、groundedなPortfolio premise_reversalが
# alternative_causal_structureへ僅差で負けてFinal5から消えるパターンが
# HORMUZ_BLOCKADE/RUSSIA_SANCTIONSの2ケースで再現したことを受けた追加。
# 「premise_reversalを必ず勝たせるhard rule」ではないことと、v2バグ
# （taxonomyをまたいだ既出判定によるexclusion）を再発させていないことを中心に検証する。

def test_marginal_coverage_full_bonus_when_move_entirely_absent_from_top1_4():
    """Top1-4のanalytical moveに全く含まれないmoveを持つcandidateは、
    coverage_bonus=1.0（満額）を受け取ること。"""
    bonus = composer._marginal_coverage_bonus("actor_audience_shift", "何らかの内容テキスト", {})
    assert bonus == 1.0


def test_marginal_coverage_partial_not_zero_when_same_move_but_different_content():
    """同じmove tagがTop1-4に既に存在していても、内容が実質的に異なれば
    （文字N-gram類似度が低ければ）ゼロではなく部分点を与える——ラベルが同じ
    というだけで機械的に「既出」とみなさない（v2バグの教訓）。"""
    top1_4_moves = {"premise_challenge": ["イランの原油輸出構造が変化し、封鎖の蓋然性が高まる"]}
    bonus = composer._marginal_coverage_bonus(
        "premise_challenge", "体制生存を最優先する局面では、経済的損失は受忍可能な犠牲になりうる",
        top1_4_moves)
    assert bonus > 0.5  # 内容が大きく異なるため、ゼロへ近づかない


def test_marginal_coverage_near_zero_when_same_move_and_near_identical_content():
    """同じmove tagかつ内容もほぼ同一なら、bonusはゼロに近づくこと。"""
    text = "イランの原油輸出構造が変化し、封鎖の蓋然性が高まるという説明である"
    top1_4_moves = {"premise_challenge": [text]}
    bonus = composer._marginal_coverage_bonus("premise_challenge", text, top1_4_moves)
    assert bonus < 0.05


def test_marginal_coverage_unknown_move_tag_gives_zero_not_full_bonus():
    """move_tagがNone（未知のhypothesis_type/slot）の場合はbonusを与えない
    （不明なものを『新規性がある』と誤認しない）。"""
    assert composer._marginal_coverage_bonus(None, "何らかのテキスト", {}) == 0.0


def test_marginal_coverage_does_not_force_premise_reversal_to_always_win():
    """premise_reversal必須化は禁止——Top1-4に同じmoveが既に存在し、かつ内容も
    近い場合は、他の十分にgroundedなcandidate（例: 明確に話題的関連性が高い
    alternative_causal_structure）に競り負けてよいことを確認する（hard ruleに
    なっていないことの回帰）。"""
    situation_text = "局面Yについての分析、中心論点はAとBの関係"
    candidates = [_candidate("main_scenario", "本線H0の説明", 1, derived_from="H0")]
    hyps = [
        _hypothesis("H0", "本線H0の説明"),
        _hypothesis("H4", "前提Xが局面変化Yによって無効化されるという読みがここにある",
                     status="rejected", hypothesis_type="H4_frame_breaking"),
    ]
    # premise_reversal: H4とmove tagが同じ(premise_challenge)かつ内容もほぼ同一
    # （coverage_bonusがゼロに近づく）。一方alternative_causal_structureは
    # 局面Yの中心論点(A・Bの関係)に直接言及し話題的関連性が高い。
    portfolio = [
        {
            "title": "前提反転という読み",
            "content": {
                "origin": "reading_portfolio_premise_reversal",
                "raw": {
                    "reading": "前提Xが局面変化Yによって無効化されるという読みがここにある",
                    "key_cue": "前提Xの妥当性",
                    "supporting_reasons": ["根拠P"], "weakening_information": [],
                },
            },
        },
        {
            "title": "別の因果構造という読み",
            "content": {
                "origin": "reading_portfolio_alternative_causal_structure",
                "raw": {
                    "reading": "局面YについてAとBの関係を軸に、全く異なる因果構造で読み直す",
                    "key_cue": "AとBの関係",
                    "supporting_reasons": ["根拠Q"], "weakening_information": [],
                },
            },
        },
    ]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=portfolio,
        situation_text=situation_text)
    exploratory = [r for r in readings if r["reading_role"] == "EXPLORATORY"]
    assert len(exploratory) == 1
    assert "別の因果構造" in exploratory[0]["title"]


def test_marginal_coverage_does_not_exclude_candidates_only_adds_bonus():
    """Marginal Analytical Coverageは加点のみで、候補を除外・格下げ（v2バグの
    taxonomyをまたいだexclusion）には使わないこと——moveが完全に既出でも、
    Evidence groundingとduplicate除外さえ満たせば候補として残ることを確認する。"""
    situation_text = "局面Zについての分析"
    candidates = [_candidate("main_scenario", "本線H0の説明", 1, derived_from="H0")]
    hyps = [
        _hypothesis("H0", "本線H0の説明"),
        _hypothesis("H4", "前提を疑う読み方Aがここにある", status="rejected",
                     hypothesis_type="H4_frame_breaking"),
    ]
    # premise_reversalのmove(premise_challenge)はH4と同じだが、内容は明確に別物
    # （duplicate閾値は超えない）——除外されず、部分点付きで候補に残ること。
    portfolio = [{
        "title": "前提反転という読み",
        "content": {
            "origin": "reading_portfolio_premise_reversal",
            "raw": {
                "reading": "全く別の観点から前提を疑う読み方Bであり、内容は前提を疑う読み方Aとは異なる",
                "key_cue": "別の着眼点C",
                "supporting_reasons": ["根拠R"], "weakening_information": [],
            },
        },
    }]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=portfolio,
        situation_text=situation_text)
    exploratory = [r for r in readings if r["reading_role"] == "EXPLORATORY"]
    assert len(exploratory) == 1
    assert exploratory[0]["source"] == "reading_portfolio"


def test_top1_3_and_frontier_unchanged_by_marginal_coverage_addition():
    """Phase 4追加後も、Top1-3(main/alternative)とFrontierの内容・順序は一切
    変更されないこと（既存Freeze条件の回帰）。"""
    candidates = [
        _candidate("main_scenario", "本線H0の説明", 1, derived_from="H0"),
        _candidate("alternative_scenario", "対抗H1の説明", 2, derived_from="H1"),
    ]
    hyps = [
        _hypothesis("H0", "本線H0の説明"), _hypothesis("H1", "対抗H1の説明"),
        _hypothesis("H4", "前提を疑う独立した読み方Wがここに十分な長さの説明として存在する",
                     status="rejected", hypothesis_type="H4_frame_breaking"),
    ]
    readings = composer.compose_reading_exposure(
        ranked_candidates=candidates, hypothesis_register=hyps, portfolio_candidates=[],
        situation_text="局面Wについての分析")
    assert readings[0]["reading_role"] == "MAIN"
    assert readings[0]["content"]["raw"]["description"] == "本線H0の説明"
    assert readings[1]["reading_role"] == "ALTERNATIVE"
    assert readings[1]["content"]["raw"]["description"] == "対抗H1の説明"
    assert readings[2]["reading_role"] == "FRONTIER"
