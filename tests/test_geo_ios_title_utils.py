"""読み方タイトルの短縮（共通関数）と、仮説IDの表示整形のテスト。実データ（2026-10-21のデモ準備で見つけた題名）を使う。"""
import geo_ios_expert_ai_adapter as adapter
import geo_ios_expert_review_view as view
import geo_ios_reading_exposure_composer as composer
import geo_ios_title_utils as tu


def test_leading_adverbial_clause_is_not_used_as_title():
    d = "現時点では、レザー・パフラヴィーは亡命王族として象徴性と国際的認知を持つものの、イラン国内の反君主制感情、反体制派諸勢力の分裂という構造要因から評価しにくい。"
    title = tu.short_title(d)
    assert title != "現時点では" and len(title) > 12 and not title.startswith("現時点では")


def test_title_never_ends_on_a_dangling_particle_clause():
    d = "レザー・パフラヴィーへの注目は、実務的な体制移行の中核を担うというより、反体制派や一部西側勢力にとって国際世論向けの象徴として機能している。"
    title = tu.short_title(d)
    assert title != "レザー・パフラヴィーへの注目は"
    assert not title.endswith(("は", "より"))


def test_internal_hypothesis_label_is_stripped_from_title():
    d = "H1_instrumental_indirectに基づく読み筋：欧州側の最上位目的はウクライナ支援そのものではなく、欧州の安全保障環境維持とEU結束維持であり、国内政治コストとのバランスの結果として現れる。"
    title = tu.short_title(d)
    assert "H1" not in title and "instrumental" not in title and not title.startswith("に基づく")
    d2 = "H2（H3_constraint_domestic_driver）に基づく読み筋：対ウクライナ支援の変化は国内政治による制約が主因となる。"
    assert tu.short_title(d2) == "対ウクライナ支援の変化は国内政治による制約が主因となる"


def test_old_behaviours_are_kept():
    assert tu.short_title("短期的には合意なき膠着が続く。このシナリオでは双方が全面戦争を避ける。") == "短期的には合意なき膠着が続く"
    assert tu.short_title("あ" * 60) == "あ" * 45 + "…"
    assert tu.short_title("短い説明") == "短い説明" and tu.short_title("") == ""


def test_both_modules_use_the_shared_function():
    d = "現時点では、あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよ。"
    assert adapter._short_title(d) == composer._short_title(d) == tu.short_title(d)


def test_hygiene_removes_hypothesis_key_after_id():
    assert view.hygiene_text("H1_instrumental_indirectに基づく読み筋") == "この見立てに基づく読み筋"
    assert view.hygiene_text("H2（H3_constraint_domestic_driver）に基づく読み筋") == "この見立てに基づく読み筋"
    assert view.hygiene_text("うちH0_straightforward仮説は、質問文が明示する") == "うちこの見立ては、質問文が明示する"
    assert view.hygiene_text("H0（Surface解釈）として扱う") == "表面的な解釈として扱う"        # 既存の挙動
    assert "H200" in view.hygiene_text("製品H200の話")                                       # 2桁以上は消さない


def test_fallback_cuts_at_last_clause_boundary_with_ellipsis_not_mid_word():
    d = "レザー・パフラヴィーへの注目は、実務的な体制移行の中核を担うというより、反体制派や一部西側勢力にとって国際世論向けの象徴として機能している。"
    title = tu.short_title(d)
    assert title.endswith("…") and "、" not in title[-3:] and title.startswith("レザー・パフラヴィーへの注目は、")


def test_looks_broken_detects_only_bad_titles():
    assert tu.looks_broken("現時点では") and tu.looks_broken("パフラヴィーへの注目は")
    assert tu.looks_broken("H1_instrumental_indirectに基づく読み筋：欧州側の最上位目的はウクラ…")
    assert not tu.looks_broken("治安機構と旧王制ネットワークの再接続を狙うディアスポラ保守派のプランB")
