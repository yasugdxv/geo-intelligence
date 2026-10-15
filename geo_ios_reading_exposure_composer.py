"""Geopolitical Intelligence OS: Reading Exposure / Diversity Layer（Step2 Reading構成）。

Past Case Reproduction / Reading Exposure Validation Test（2026-09-24）の結果を受けた
実装。Expert AI Core（geo_expert_engine.py/geo_expert_schema.py）・CQAS・S（既存のrank
付与ロジック、geo_ios_expert_ai_adapter.py/geo_ios_analysis_session_service.py）は
一切変更しない。既にrank付けされた候補（main_scenario=rank1、alternative_scenarios=
rank2以降）・hypothesis_register・既存Reading Portfolio出力を入力として受け取り、
その"後段"でユーザーに見せる最大5件のReadingを構成するだけの、純粋な追加レイヤー。
この関数を呼ばなくても既存のStep2フローは今まで通り動作する（既存呼び出し元は無変更）。

背景（Past Case Reproduction Test、4 evaluable casesの診断結果）:
- display_top_n=5は既に実装済み。問題は件数ではなく5件の"構成"だった。
- Sはmain_scenario選定においてEvidence Disciplineを保っている（GOOD寄り）——このレイヤーは
  Sのスコア・rank・promptには一切触れない。
- hypothesis_registerのH4（frame-breaking型）がExpert-likeな読みの主な担い手だったが、
  4ケース中2ケースでstatus=rejectedとなり、既存Reading Portfolio（main_scenario/
  alternative_scenariosしか見ない設計）では救済されなかった。
- Hormuz Tollケースのように、そもそもGold Readingに相当する候補がhypothesis_register
  にも生成されないGENERATION_GAPも存在した（このレイヤーでは解決できない——正直に
  Reading4/5を空のまま返す）。

5件構成:
  Reading 1-3 = S Rank #1-3（そのまま、再スコアリング・内容変更をしない）
  Reading 4   = Frontier Reading: hypothesis_registerのTop3外から、Evidence-grounded
                かつTop3とsemantic duplicateでない最有力候補を選ぶ。retained/unresolved/
                rejectedいずれも候補から除外しない。Sのstatus判定は上書きしない
                （rejectedのままexploratory=Trueで表示する——「AIが有力と判断した」ように
                見せる表示は禁止）。
  Reading 5   = Expert Exploration: 既存Reading Portfolio → 残りのhypothesis_register →
                それでも十分な多様性・Evidence groundingを持つ候補が無い場合のみ、
                汎用化されたPromptで新規に1回だけLLM生成する。

Evidence最低基準を満たす候補が存在しない場合、無理に5件へ埋めない（4件・3件で返してよい）。
"""
import geo_llm_common

_DUPLICATE_SIMILARITY_THRESHOLD = 0.5
_NGRAM_SIZE = 3
_FRAME_BREAKING_TYPES = {"H4_frame_breaking", "frame_breaking"}
_ACTOR_SHIFT_TYPES = {"H2_counterparty_audience_shift", "actor_intent_alternative"}
_CONSTRAINT_TYPES = {"H3_constraint_domestic_driver", "structural_alternative"}
_MIN_REASON_LEN = 20
_MIN_DESCRIPTION_LEN = 20
_SHORT_TITLE_MAX_LEN = 45

# 2026-09-24修正（1回目、ユーザー指摘）: Reading5選定が文字N-gramの「距離の最大化」を
# 主要Selection Signalにしていたため、Situationの中心から外れるほど有利になってしまい、
# HORMUZ_BLOCKADE_SESSION3でEvidence groundingの弱いwildcardが、より的確な
# premise_reversalより優先される問題が生じた。
#
# 2026-09-24修正（2回目）: 1回目の修正では、hypothesis_registerのhypothesis_type
# （H0〜H4）とReading Portfolioのslot（alternative_causal_structure/premise_reversal/
# wildcard_non_obvious）という**別々の目的で設計された2つのtaxonomy**を、共通の
# reasoning_familyバケットへ強引に統合していた。この結果、Top3で既にH4
# （frame_breaking、「輸出ルート多様化による自殺コスト低下」という特定の機構）が
# 使われていると、全く別の機構を持つPortfolio側premise_reversal（「体制生存優先に
# よる受忍可能な犠牲」）まで「同じfamilyは既出」としてボーナスを失い、結果的に
# evidence groundingの弱いwildcardに逆転されてしまう問題が判明した（実データで検証
# 済み——causal mechanismが異なる2つの読みを、粗いラベルの一致だけで同一視しては
# ならない）。
#
# 対応: hypothesis_registerとReading Portfolioのfamily照合を別Namespaceにする
# （taxonomyをまたいだ「既出判定」はしない——Portfolioは既存プロンプト設計上、
# そもそもCQAS本体のmain/alternativeと重複しないことを前提に生成されているため）。
# Portfolio側は、既存のslot定義文（geo_ios_reading_portfolio_service.pyのSystem
# Prompt）が既に明記している「alternative_causal_structure/premise_reversal＝
# もう一つの有力な読み」「wildcard_non_obvious＝発生確率は低いが見落とされがちな
# 読み」という区別をそのまま構造シグナルとして使う（新しいhard-coded順位表ではなく、
# 既存システムが既に持つslotの設計意図を反映するのみ）。文字N-gramはduplicate除外と
# 僅かなtie-breakにのみ残す。
_PORTFOLIO_SUBSTANTIVE_SLOTS = {"alternative_causal_structure", "premise_reversal"}
_PORTFOLIO_SUBSTANTIVE_WEIGHT = 0.5
_PORTFOLIO_WILDCARD_WEIGHT = 0.1
_HYPOTHESIS_BASE_WEIGHT = 0.3
_TOPICAL_RELEVANCE_TIEBREAK_WEIGHT = 0.1

# 2026-09-24修正（3回目、ユーザー指摘の構造診断を受けて）: 「Top4で未使用のhypothesis_
# typeなら+0.2」というnovelty bonusは、hypothesis_registerが通常5件・Top3+Frontierで
# 4件消費されるこの実装の実態上、残る1件がほぼ常に「まだ使われていないtype」に該当して
# しまい、ほぼ無条件にhypothesis_register側だけを底上げする効果しか持っていなかった
# （4 evaluable casesの実データで検証: 3/4ケースでPortfolio最良候補に対し0.15pt差の
# 非拮抗な差を生んでいた——「際どい同点」ではなく設計上のバイアスだった）。このbonusは
# 廃止し、hypothesis_register側の構造重みは基礎重み＋既存_type_bonus（H4を高く扱う、
# Frontier選定と共通の既存基準）のみに戻す。
#
# その上で、ユーザー提案の「ほぼ同点の場合のみsource namespaceをweak tie-breakとして
# 使う」を実装する。Portfolioを必ず1件出すhard ruleにはしない、Goldに近い方を選ぶ
# ruleにもしない——あくまで最終スコアが僅差(_NEAR_TIE_EPSILON以内)の場合にのみ、
# Top1-4に無いsource namespaceの候補をわずかに優先する。
_NEAR_TIE_EPSILON = 0.03
_NEAR_TIE_NAMESPACE_BONUS = 0.01

# 2026-09-24修正（Phase 4、Reading Exposure V2——Past Case semantic validationで、
# groundedなPortfolio premise_reversalがalternative_causal_structureへ僅差で負けて
# Final5から消えるパターンがHORMUZ_BLOCKADE/RUSSIA_SANCTIONSの2ケースで再現した
# ことを受けた対応）:
#
# raw score（既存の構造重み＋topical_relevance tie-break）だけでなく、Top1-4に対する
# Marginal Analytical Coverage（前提・因果構造・Actor logic・目的階層のうち、Top1-4が
# 既にカバーしている「analytical move」と重複しないか）を追加のScoring Signalとして
# 加味する。
#
# 設計原則（v2バグの再発防止）: hypothesis_registerのhypothesis_typeとPortfolioの
# slotは、既存の"weight"計算では引き続き別Namespaceのまま評価する（v2で修正済みの
# 「taxonomyをまたいだ既出判定によるexclusion」は絶対に復活させない）。本シグナルは
# 除外・格下げには使わず、あくまで加点（bonus）としてのみ使う——Top1-4という固定の
# 参照集合に対して「このcandidateのanalytical moveは新しい次元を追加するか」を見る
# だけで、pool内の他candidateとの比較・排除には使わない。
#
# analytical_moveのtagは、hypothesis_typeとPortfolio slotの両方が持つ既存の
# 設計意図（H4_frame_breaking/premise_reversal＝前提を疑う、H3_constraint_domestic_
# driver/alternative_causal_structure＝別の因果ドライバー、等）をそのまま踏襲した
# 決定的なmapping。新しいGold固有の判定基準ではない。
#
# 同じmoveが既にTop1-4に存在する場合でも、内容が実質的に異なるなら
# （文字N-gram類似度が低いなら）部分点を与える——ラベルが同じだけで機械的に
# 「既出」とみなさない（これもv2バグの教訓）。ただし、この文字N-gram類似度は
# あくまで「同じmove内での内容重複チェック」という補助的な用途に限定し、
# 主要な"farther=better"のSelection Signalとしては使わない（v1で修正済みの
# lexical distance最大化バグを再発させない）。
_ANALYTICAL_MOVE_BY_HYPOTHESIS_TYPE = {
    "H0_straightforward": "literal_premise",
    "H1_instrumental_indirect": "objective_hierarchy",
    "H2_counterparty_audience_shift": "actor_audience_shift",
    "H3_constraint_domestic_driver": "alternative_causal_driver",
    "H4_frame_breaking": "premise_challenge",
}
_ANALYTICAL_MOVE_BY_PORTFOLIO_SLOT = {
    "alternative_causal_structure": "alternative_causal_driver",
    "premise_reversal": "premise_challenge",
    "wildcard_non_obvious": "low_probability_shift",
}
_MARGINAL_COVERAGE_WEIGHT = 0.15


def _short_title(description: str) -> str:
    if not description:
        return ""
    for sep in ("。", "、"):
        idx = description.find(sep)
        if 0 < idx <= _SHORT_TITLE_MAX_LEN:
            return description[:idx]
    if len(description) <= _SHORT_TITLE_MAX_LEN:
        return description
    return description[:_SHORT_TITLE_MAX_LEN] + "…"


def _hypothesis_text(h: dict) -> str:
    return " ".join(filter(None, [h.get("description"), h.get("reason")]))


def _has_evidence_anchor(h: dict) -> bool:
    """新しいFactを創作せず、既存フィールドだけでgroundingの有無を判定する。決定的な
    近似方針（geo_knowledge_gap_service.text_similarityと同じ考え方）を踏襲し、厳密な
    NLP的検証はしない。supporting_evidence_ids等の明示的なEvidence参照が無い場合でも、
    reason/descriptionが十分に具体的（長さで近似）であれば、Structured Outputs自体が
    既に持つEvidence Fit判定（hypothesis_registerのreason欄はEvidence Fit等7要素を
    参照するようPromptで指示済み——geo_expert_schema.py）を信頼してgroundedとみなす。"""
    if h.get("supporting_evidence_ids") or h.get("contradicting_evidence_ids") or h.get("weakening_evidence_relevance"):
        return True
    reason = (h.get("reason") or "").strip()
    description = (h.get("description") or "").strip()
    return len(reason) >= _MIN_REASON_LEN and len(description) >= _MIN_DESCRIPTION_LEN


def _type_bonus(hypothesis_type: str | None) -> float:
    if hypothesis_type in _FRAME_BREAKING_TYPES:
        return 0.3
    if hypothesis_type in _ACTOR_SHIFT_TYPES or hypothesis_type in _CONSTRAINT_TYPES:
        return 0.15
    return 0.0


def _char_ngram_similarity(a: str, b: str, n: int = _NGRAM_SIZE) -> float:
    """文字N-gram Jaccard類似度。既存geo_knowledge_gap_service.text_similarityは空白
    区切りトークン前提（英語向け）で、日本語の非同一文ではほぼ常に0.0になってしまう
    ことを確認したため（分かち書きが無い言語では文全体が1トークン扱いになる）、この
    レイヤー専用に文字N-gramベースの決定的な近似を用いる（Embedding/pgvectorは
    導入しない、という既存方針は踏襲する）。"""
    def _ngrams(text: str) -> set:
        t = (text or "").strip()
        if len(t) < n:
            return {t} if t else set()
        return {t[i:i + n] for i in range(len(t) - n + 1)}
    na, nb = _ngrams(a), _ngrams(b)
    if not na or not nb:
        return 0.0
    return len(na & nb) / len(na | nb)


def _max_similarity(text: str, others: list) -> float:
    sims = [_char_ngram_similarity(text, t) for t in others if t]
    return max(sims) if sims else 0.0


def _portfolio_slot_weight(slot: str) -> float:
    """Reading Portfolio既存プロンプト（geo_ios_reading_portfolio_service.py）が
    既に明記しているslotの設計意図をそのまま構造シグナルにする——新しいhard-coded
    順位表ではない。alternative_causal_structure/premise_reversalは同プロンプトで
    「もう一つの有力な読み」と明記され、wildcard_non_obviousは「発生確率は低い」と
    明記されている。"""
    return _PORTFOLIO_SUBSTANTIVE_WEIGHT if slot in _PORTFOLIO_SUBSTANTIVE_SLOTS else _PORTFOLIO_WILDCARD_WEIGHT


def _portfolio_has_evidence_anchor(raw: dict) -> bool:
    """Reading Portfolio側のgrounding判定。既存の構造化フィールド
    （supporting_reasons/weakening_information/key_cue）だけを見る。"""
    if raw.get("supporting_reasons") or raw.get("weakening_information"):
        return True
    return bool((raw.get("key_cue") or "").strip()) and bool((raw.get("reading") or "").strip())


def _topical_relevance(text: str, situation_and_main_texts: list) -> float:
    """候補のcore_claimが、Situation本文・main_scenario本文とどれだけ話題的に重なって
    いるかを文字N-gram Jaccardで近似する。値が高いほど「Situationの中心論点に留まって
    いる」ことを意味する——Top4との距離（重複除外にのみ使う）とは別の目的の指標。"""
    sims = [_char_ngram_similarity(text, t) for t in situation_and_main_texts if t]
    return max(sims) if sims else 0.0


# ---- Reading 4: Frontier -------------------------------------------------------

def select_frontier_reading(hypothesis_register: list, *, top3_texts: list,
                             used_hypothesis_ids: set) -> dict | None:
    """S Top3外のhypothesis_registerから、semantic novelty/causal difference/premise
    challenge value/evidence anchorを考慮してFrontier Readingを1件選ぶ。Sのstatus判定
    （retained/unresolved/rejected）はそのまま保持し、上書きしない——このレイヤーは
    S rerankではない。"""
    best, best_score = None, -1.0
    for h in hypothesis_register or []:
        if h.get("hypothesis_id") in used_hypothesis_ids:
            continue
        if not _has_evidence_anchor(h):
            continue
        text = _hypothesis_text(h)
        if not text.strip():
            continue
        max_sim = _max_similarity(text, top3_texts)
        if max_sim > _DUPLICATE_SIMILARITY_THRESHOLD:
            continue  # Top3とsemantic duplicate
        score = (1.0 - max_sim) + _type_bonus(h.get("hypothesis_type"))
        if score > best_score:
            best, best_score = h, score
    return best


def expand_hypothesis_to_reading_card(h: dict) -> dict:
    """hypothesis_registerの1エントリを、Presentation用の完全なReading Cardへ展開する。
    既存のdescription/reason/supporting_evidence_ids/contradicting_evidence_ids/
    weakening_evidence_relevanceだけを使い、新しいFactは創作しない。Sのrerankでは
    ないためstatusは変更しない。Main Scenarioへの昇格も行わない（あくまで表示用の展開）。"""
    description = h.get("description") or ""
    reason = h.get("reason") or ""
    weakening = h.get("weakening_evidence_relevance") or []
    weakening_texts = [
        (w.get("rationale") or w.get("evidence_id") or "") for w in weakening if isinstance(w, dict)
    ]
    weakening_texts = [t for t in weakening_texts if t]
    contradicting = h.get("contradicting_evidence_ids") or []
    supporting = h.get("supporting_evidence_ids") or []

    missing_evidence = None
    if not supporting and not weakening_texts and not contradicting:
        missing_evidence = ("supporting_evidence_ids/weakening_evidence_relevance/"
                             "contradicting_evidence_idsの明示的な参照は無く、reasonの記述の"
                             "みに基づく")

    return {
        "title": _short_title(description),
        "reasoning_story": (description + ("\n" + reason if reason else "")).strip(),
        "supporting_evidence": supporting,
        "weakening_evidence": weakening_texts or contradicting,
        # reasonはPrompt設計上「何が不足しているか」を既に含むことが多いため、新規に創作
        # せず既存フィールドをそのまま転用する（geo_expert_schema.pyのhypothesis_register
        # Prompt指示を参照）。
        "what_would_make_it_stronger": reason or None,
        "what_would_make_it_weaker": (weakening_texts[0] if weakening_texts else
                                       (contradicting[0] if contradicting else None)),
        "original_hypothesis_status": h.get("status"),
        "why_not_selected_by_s": reason or None,
        "supporting_basis": supporting or None,
        "weakening_basis": weakening_texts or contradicting or None,
        "missing_evidence": missing_evidence,
    }


# ---- Reading 5: Expert Exploration ---------------------------------------------

_EXPLORATION_SCHEMA_NAME = "geo_ios_reading_exploration"
_EXPLORATION_SCHEMA = {
    "type": "object",
    "properties": {
        "insufficient_material": {"type": "boolean"},
        "insufficient_material_reason": {"type": "string"},
        "title": {"type": "string"},
        "reading": {"type": "string"},
        "exploration_angle": {"type": "string"},
        "key_cue": {"type": "string"},
        "key_actors_and_structure": {"type": "string"},
        "supporting_reasons": {"type": "array", "items": {"type": "string"}},
        "weakening_information": {"type": "array", "items": {"type": "string"}},
        "switch_condition": {"type": "string"},
    },
    "required": ["insufficient_material"],
    "additionalProperties": False,
}

_EXPLORATION_SYSTEM_PROMPT = """あなたは地政学インテリジェンス分析の専門家AIです。
この局面については、既に4つのReading（S Rank上位3件＋前提を疑う追加の読み1件）が
生成済みです。あなたの役割は、それら4件のいずれとも異なる、追加でもう1つの読み方を
提示することです。

問いは次の通りです：「現在の4つのReadingでは捉えていない、しかし現在のSituation・
Actor・Relationship・Evidenceから成立し得る、因果構造または上位目的が異なる読みは
あるか」。

観点の例（いずれかを必ず選ぶ必要はない）: Objective Hierarchy（目的の階層構造）、
Hidden Objective（隠れた上位目的）、Premise Challenge（前提への疑問）、
Premise Reversal（前提の反転）、Alternative Causal Structure（別の因果構造）、
Role Choreography（役割分担）、Audience/Signaling（発信対象の分析）、
Structural Constraint（構造的制約）、Implementation Constraint（実施上の制約）。

**厳守事項**:
- 既存4件のいずれかを言い換えただけの読みは禁止です（それは新しい読みではありません）。
- 新しいReadingは、与えられた資料・Evidence・Actor・Structureのうち少なくとも1つに
  明確に根拠づけられている必要があります（source-grounded anchor）。
- 与えられた資料に無い具体的事実・数値・Evidenceを創作しないでください。
- 十分な材料が無い場合は、無理に読みを作らずinsufficient_material=trueとし、
  insufficient_material_reasonに何が不足しているかを一言書いてください。
- readingとtitleは、出来事（法案・政策・声明）そのものを主語にしないでください
  （2026-09-25追加）。key_actors_and_structureに登場する**具体的なActorを主語**にし、
  そのActorが何を望み・何に縛られ・その出来事をどう使おうとしているかを中心に組み立てて
  ください。出来事は、Actorの目的・力学が表面化した結果・道具として位置づけること。

insufficient_material=falseの場合、以下を含めてください:
- title: 読み方タイトル
- reading: この読みが何を意味するかの1〜2文の要約
- exploration_angle: 上記の観点例のうち最も近いもの（自由記述でよい）
- key_cue: この読みの起点となった、資料中の具体的な着眼点
- key_actors_and_structure: この読みで重要になるActor・力学構造
- supporting_reasons: この読みを支持する理由（配列、資料の記述に基づくこと）
- weakening_information: この読みを弱める・反証し得る情報（配列）
- switch_condition: 何が観測されたらこの読みへ確度が寄るか"""


def _format_existing_readings_for_prompt(readings: list) -> str:
    lines = ["# 既に生成済みの4つのReading（これらと重複しないこと）"]
    for i, r in enumerate(readings, start=1):
        desc = r.get("content", {}).get("why_this_matters") or r.get("title") or ""
        lines.append(f"- Reading{i} ({r.get('reading_role')}): {desc}")
    return "\n".join(lines)


def generate_expert_exploration_reading(azure_client, model, *, situation_text: str,
                                         existing_readings: list) -> dict | None:
    """既存Portfolio/hypothesis_registerで埋まらない場合のみ呼ばれる、1回限りの生成。
    Gold Case・江口さん固有の言い回しは一切Promptへ入れない（一般化された問いのみ）。
    insufficient_material=trueならNoneを返す（無理に埋めない）。"""
    user_prompt = (
        f"# Situation\n{situation_text}\n\n"
        f"{_format_existing_readings_for_prompt(existing_readings)}"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _EXPLORATION_SYSTEM_PROMPT, user_prompt,
        _EXPLORATION_SCHEMA, _EXPLORATION_SCHEMA_NAME)
    data = result["data"]
    if data.get("insufficient_material"):
        return None
    return data


# ---- Generate More Readings（§13、2026-09-25追加） ----------------------------
#
# 既存generate_expert_exploration_reading（Pool枯渇時のfallback生成、Freeze済み・無変更）
# とは別の、ユーザーが方向性を明示的に指定するための入口。プロンプトの厳守事項（source-
# grounded anchor必須・既存Readingとの重複禁止・insufficient_materialなら無理に作らない）
# は同じ設計方針を踏襲する——既存の枠組みを壊さないよう、既存定数・既存関数はそのまま残し、
# 完全に別の関数として追加する。

_DIRECTION_INSTRUCTIONS = {
    "auto": "特に制約はありません。既存の読みでは十分にカバーされていない、成立し得る読みを自由に探してください。",
    "more_orthodox": "既存の読みよりもさらに『王道』——与えられた証拠に最も忠実で、最も自然な読み——になり得る観点を優先してください。",
    "invert_premise": "既存の読みが当然の前提としていることのうち1つを反転させた場合に成立する読みを探してください。",
    "different_actor_lead": "既存の読みでは主役になっていない別のActorを主役に据えた読みを探してください。",
    "domestic_politics": "国内政治・制度上の力学から見た読みを探してください。",
    "international_relations": "国際関係・同盟関係の観点から見た読みを探してください。",
    "economic_structure": "経済・産業構造の観点から見た読みを探してください。",
    "negotiation_relationship": "交渉・当事者間の関係性(Relationship)の観点から見た読みを探してください。",
    "bold_hypothesis": "証拠は限定的でも構いません。より大胆な仮説を探してください（ただし与えられた資料に無い具体的事実・数値は創作しないでください）。",
}


def _directed_exploration_system_prompt(existing_count: int) -> str:
    return f"""あなたは地政学インテリジェンス分析の専門家AIです。
この局面については、既に{existing_count}つのReadingが生成済みです。あなたの役割は、
ユーザーが指定した観点から、それら{existing_count}件のいずれとも異なる、追加の読み方を
1つ提示することです。

**厳守事項**:
- 既存{existing_count}件のいずれかを言い換えただけの読みは禁止です（それは新しい読みでは
  ありません）。
- 新しいReadingは、与えられたSituation・既存Readingの記述のうち少なくとも1つに明確に
  根拠づけられている必要があります（source-grounded anchor）。
- 与えられた資料に無い具体的事実・数値・Evidenceを創作しないでください。
- ユーザーが指定した観点に沿えない、または十分な材料が無い場合は、無理に読みを作らず
  insufficient_material=trueとし、insufficient_material_reasonに理由を書いてください。
- readingとtitleは、出来事（法案・政策・声明）そのものを主語にしないでください
  （2026-09-25追加）。key_actors_and_structureに登場する**具体的なActorを主語**にし、
  そのActorが何を望み・何に縛られ・その出来事をどう使おうとしているかを中心に組み立てて
  ください。出来事は、Actorの目的・力学が表面化した結果・道具として位置づけること。

insufficient_material=falseの場合、以下を含めてください:
- title: 読み方タイトル
- reading: この読みが何を意味するかの1〜2文の要約
- exploration_angle: 指定された観点、またはそれに最も近いもの
- key_cue: この読みの起点となった、資料中の具体的な着眼点
- key_actors_and_structure: この読みで重要になるActor・力学構造
- supporting_reasons: この読みを支持する理由（配列、資料の記述に基づくこと）
- weakening_information: この読みを弱める・反証し得る情報（配列）
- switch_condition: 何が観測されたらこの読みへ確度が寄るか"""


def generate_directed_exploration_reading(azure_client, model, *, situation_text: str,
                                           existing_readings: list, direction: str,
                                           custom_text: str | None = None) -> dict | None:
    """direction（_DIRECTION_INSTRUCTIONSのキー、または"custom"）に沿った追加のReadingを
    1件生成する。existing_readingsは現在露出済みの全件（5件superえても可）——既存4件固定の
    generate_expert_exploration_readingと違い、件数を動的にPromptへ反映する。"""
    if direction == "custom":
        instruction = (custom_text or "").strip()
        if not instruction:
            return None
    else:
        instruction = _DIRECTION_INSTRUCTIONS.get(direction, _DIRECTION_INSTRUCTIONS["auto"])

    user_prompt = (
        f"# Situation\n{situation_text}\n\n"
        f"# 追加でこの観点から探すこと\n{instruction}\n\n"
        f"{_format_existing_readings_for_prompt(existing_readings)}"
    )
    result = geo_llm_common.call_llm_structured(
        azure_client, model, _directed_exploration_system_prompt(len(existing_readings)),
        user_prompt, _EXPLORATION_SCHEMA, _EXPLORATION_SCHEMA_NAME)
    data = result["data"]
    if data.get("insufficient_material"):
        return None
    return data


def _build_top1_4_analytical_moves(top1_4_hypothesis_types_and_texts: list) -> dict:
    """Top1-4（S Rank#1-3＋Frontier）が既にカバーしているanalytical moveを、
    move_tag -> [その内容を表すtext] の辞書として集計する。Marginal Analytical
    Coverageの加点計算専用の参照集合であり、pool内candidate同士の比較・除外には
    使わない（Top1-4という固定集合に対する一方向の参照のみ）。"""
    moves: dict = {}
    for hypothesis_type, text in top1_4_hypothesis_types_and_texts:
        move = _ANALYTICAL_MOVE_BY_HYPOTHESIS_TYPE.get(hypothesis_type)
        if move and text:
            moves.setdefault(move, []).append(text)
    return moves


def _marginal_coverage_bonus(move_tag: str | None, text: str, top1_4_moves: dict) -> float:
    """Top1-4に対するMarginal Analytical Coverage加点。move_tagがTop1-4に全く
    存在しない場合は満額（1.0）、存在する場合でも内容が実質的に異なるなら
    （文字N-gram類似度が低いなら）部分点を与える——ラベルが同じだけで機械的に
    「既出」とみなさない。move_tagが無い（未知のtype/slot）場合は0。"""
    if not move_tag:
        return 0.0
    same_move_texts = top1_4_moves.get(move_tag)
    if not same_move_texts:
        return 1.0
    max_sim = _max_similarity(text, same_move_texts)
    return max(0.0, 1.0 - max_sim)


def _select_exploration_from_pool(portfolio_candidates: list, hypothesis_register: list,
                                   used_hypothesis_ids: set, top4_texts: list,
                                   situation_and_main_texts: list,
                                   top1_4_moves: dict | None = None) -> dict | None:
    """Reading Portfolio（3スロット）とTop3外のhypothesis_registerを1つの候補プールとして
    まとめて評価し、最良の1件を選ぶ（ソース種別による優先順位は付けない）。

    Selection Signal（2026-09-24、3回目修正——構造的バイアス診断を受けて）:
      1. Top4とのsemantic duplicate除外（文字N-gram類似度が閾値超なら除外。これは
         「近い」ものを弾くためだけに使い、「遠い」ことを積極的に評価するsignalとしては
         使わない）
      2. Evidence grounding（既存の構造化フィールドで判定、gate）
      3. 構造重み: Reading PortfolioとhypothesisRegisterは別Namespaceで評価する
         （taxonomyをまたいだ「既出判定」はしない）。
         - Portfolio側: 既存slot定義（alternative_causal_structure/premise_reversal
           ＝有力な読み、wildcard_non_obvious＝低確率）をそのまま重みにする。
         - hypothesis_register側: 基礎重み＋既存_type_bonus（H4を高く扱う、Frontier
           選定と共通の既存基準）のみ。「Top4で未使用のhypothesis_typeなら+0.2」という
           novelty bonusは3回目修正で廃止した——hypothesis_registerが通常5件・
           Top3+Frontierで4件消費される実態上、残る1件がほぼ常に「未使用type」に該当
           してしまい、ほぼ無条件にhypothesis_register側だけを底上げする系統的バイアス
           になっていたことを実データで確認したため（4ケース中3ケースでPortfolio
           最良候補に対し0.15pt差の非拮抗な差を生んでいた）。
      4. topical_relevance（Situation・main_scenario本文との話題的重なり）は僅かな
         tie-break（重み0.1）としてのみ使う。
      5. 最終スコアがほぼ同点（_NEAR_TIE_EPSILON以内）で、上位2件が異なるNamespace
         （hypothesis_register vs reading_portfolio）の場合のみ、Top1-4に無い方の
         Namespaceへ僅かなbonus（_NEAR_TIE_NAMESPACE_BONUS）を与える——Portfolioを
         必ず1件出すhard ruleではなく、Goldに近い方を選ぶruleでもない、あくまで
         際どい同点の最終判断材料。
      6.（2026-09-24追加、Phase 4 Reading Exposure V2）Marginal Analytical Coverage:
         Top1-4が既にカバーしているanalytical move（前提・因果構造・Actor logic・
         目的階層のいずれか、hypothesis_type/Portfolio slotの既存定義から決定的に
         導出）と重ならないcandidateへ、僅かな加点（_MARGINAL_COVERAGE_WEIGHT）を
         与える。同じmoveでも内容が実質的に異なれば部分点を与える（ラベル一致だけで
         機械的に「既出」としない）。この加点はTop1-4という固定集合への一方向の参照
         のみで、pool内candidate同士の除外・格下げには使わない（v2バグの再発防止）。
         premise_reversalを必ず勝たせるhard ruleではない——同じmoveが既にTop1-4に
         含まれる場合は他のcandidateと同様に部分点のみ。"""
    top1_4_moves = top1_4_moves or {}
    pool = []
    for p in portfolio_candidates or []:
        raw = (p.get("content") or {}).get("raw") or {}
        text = " ".join(filter(None, [raw.get("reading"), raw.get("key_cue")]))
        if not text.strip() or not _portfolio_has_evidence_anchor(raw):
            continue
        origin = (p.get("content") or {}).get("origin") or ""
        slot = origin.replace("reading_portfolio_", "")
        weight = _portfolio_slot_weight(slot)
        move_tag = _ANALYTICAL_MOVE_BY_PORTFOLIO_SLOT.get(slot)
        pool.append(("reading_portfolio", text, weight, move_tag, {
            "reading_role": "EXPLORATORY", "exploratory": True, "source": "reading_portfolio",
            "title": p.get("title"), "content": p.get("content"),
            "supporting_basis": raw.get("supporting_reasons"),
            "weakening_basis": raw.get("weakening_information"),
            "original_hypothesis_status": None, "why_not_selected_by_s": None,
            "missing_evidence": None,
        }))

    for h in hypothesis_register or []:
        if h.get("hypothesis_id") in used_hypothesis_ids:
            continue
        if not _has_evidence_anchor(h):
            continue
        text = _hypothesis_text(h)
        if not text.strip():
            continue
        weight = _HYPOTHESIS_BASE_WEIGHT + _type_bonus(h.get("hypothesis_type"))
        move_tag = _ANALYTICAL_MOVE_BY_HYPOTHESIS_TYPE.get(h.get("hypothesis_type"))
        card = expand_hypothesis_to_reading_card(h)
        pool.append(("hypothesis_register", text, weight, move_tag, {
            "reading_role": "EXPLORATORY", "exploratory": True, "source": "hypothesis_register",
            "title": card["title"], "content": {"why_this_matters": card["reasoning_story"], "raw": h},
            **card,
        }))

    scored = []
    for namespace, text, weight, move_tag, reading in pool:
        if _max_similarity(text, top4_texts) > _DUPLICATE_SIMILARITY_THRESHOLD:
            continue  # Top4とsemantic duplicate
        coverage_bonus = _marginal_coverage_bonus(move_tag, text, top1_4_moves)
        score = (weight + _TOPICAL_RELEVANCE_TIEBREAK_WEIGHT * _topical_relevance(text, situation_and_main_texts)
                  + _MARGINAL_COVERAGE_WEIGHT * coverage_bonus)
        scored.append((namespace, score, reading))
    if not scored:
        return None
    scored.sort(key=lambda x: x[1], reverse=True)

    # 際どい同点の場合のみ、Top1-4に無いNamespace（=reading_portfolio。main/alternative/
    # FrontierはいずれもhypothesisRegister由来のため、Top1-4に無い方は必ずPortfolio側）
    # へ僅かなbonusを与えて最終決定する。
    if len(scored) >= 2 and (scored[0][1] - scored[1][1]) <= _NEAR_TIE_EPSILON and scored[0][0] != scored[1][0]:
        adjusted = []
        for namespace, score, reading in scored[:2]:
            bonus = _NEAR_TIE_NAMESPACE_BONUS if namespace == "reading_portfolio" else 0.0
            adjusted.append((namespace, score + bonus, reading))
        adjusted.sort(key=lambda x: x[1], reverse=True)
        return adjusted[0][2]
    return scored[0][2]


# ---- Composer -------------------------------------------------------------------

def compose_reading_exposure(*, ranked_candidates: list, hypothesis_register: list,
                              portfolio_candidates: list, situation_text: str,
                              azure_client=None, model=None) -> list[dict]:
    """S Rank#1-3 + Frontier + Expert Explorationの最大5件を構成する。Sのrank順・
    main_scenario/alternative_scenariosの内容は一切変更しない（そのままReading1-3として
    渡すだけ）。azure_client/modelを渡さない場合、Reading5は既存Portfolio/
    hypothesis_registerのみから選ばれ、新規LLM生成は行われない（呼び出し元が明示的に
    生成を許可した場合のみ実行される）。"""
    sorted_candidates = sorted(ranked_candidates or [], key=lambda c: c.get("rank") or 999)
    top3 = [c for c in sorted_candidates
            if (c.get("content") or {}).get("origin") in ("main_scenario", "alternative_scenario")][:3]

    readings = []
    for c in top3:
        role = "MAIN" if (c.get("content") or {}).get("origin") == "main_scenario" else "ALTERNATIVE"
        readings.append({**c, "reading_role": role, "exploratory": False, "source": "s_ranked"})

    def _candidate_text(c: dict) -> str:
        content = c.get("content") or {}
        raw = content.get("raw") or {}
        return " ".join(filter(None, [raw.get("description"), content.get("why_this_matters")]))

    top3_texts = [_candidate_text(c) for c in top3]
    used_hypothesis_ids = set()
    for c in top3:
        raw = (c.get("content") or {}).get("raw") or {}
        hid = raw.get("derived_from_hypothesis_id")
        if hid:
            used_hypothesis_ids.add(hid)

    # Reading 4: Frontier
    frontier_hypothesis = select_frontier_reading(
        hypothesis_register, top3_texts=top3_texts, used_hypothesis_ids=used_hypothesis_ids)
    frontier_card = None
    if frontier_hypothesis:
        frontier_card = expand_hypothesis_to_reading_card(frontier_hypothesis)
        frontier_reading = {
            "reading_role": "FRONTIER", "exploratory": True, "source": "hypothesis_register",
            "title": frontier_card["title"],
            "content": {"why_this_matters": frontier_card["reasoning_story"], "raw": frontier_hypothesis},
            **frontier_card,
        }
        readings.append(frontier_reading)
        used_hypothesis_ids.add(frontier_hypothesis.get("hypothesis_id"))

    # Reading 5: Expert Exploration
    top4_texts = top3_texts + ([frontier_card["reasoning_story"]] if frontier_card else [])
    main_text = top3_texts[0] if top3_texts else ""
    situation_and_main_texts = [situation_text, main_text]

    # Phase 4 Reading Exposure V2（2026-09-24）: Top1-4自身のanalytical move
    # （hypothesis_typeから決定的に導出）を集計し、Marginal Analytical Coverage
    # 加点の参照集合として渡す。Top3はderived_from_hypothesis_id経由でhypothesis_
    # registerのhypothesis_typeを引く（無ければmoveは付かない＝新しいtypeを増やさず
    # 既存フィールドのみ使う）。
    top1_4_hypothesis_types_and_texts = []
    for c, text in zip(top3, top3_texts):
        raw = (c.get("content") or {}).get("raw") or {}
        hid = raw.get("derived_from_hypothesis_id")
        matched = next((h for h in (hypothesis_register or []) if h.get("hypothesis_id") == hid), None)
        if matched:
            top1_4_hypothesis_types_and_texts.append((matched.get("hypothesis_type"), text))
    if frontier_hypothesis and frontier_card:
        top1_4_hypothesis_types_and_texts.append(
            (frontier_hypothesis.get("hypothesis_type"), frontier_card["reasoning_story"]))
    top1_4_moves = _build_top1_4_analytical_moves(top1_4_hypothesis_types_and_texts)

    exploration_reading = _select_exploration_from_pool(
        portfolio_candidates, hypothesis_register, used_hypothesis_ids, top4_texts,
        situation_and_main_texts, top1_4_moves)
    if not exploration_reading and azure_client and model:
        generated = generate_expert_exploration_reading(
            azure_client, model, situation_text=situation_text, existing_readings=readings)
        if generated:
            exploration_reading = {
                "reading_role": "EXPLORATORY", "exploratory": True,
                "source": "expert_exploration_generated",
                "title": generated.get("title"),
                "content": {"why_this_matters": generated.get("reading"), "raw": generated},
                "supporting_basis": generated.get("supporting_reasons"),
                "weakening_basis": generated.get("weakening_information"),
                "original_hypothesis_status": None,
                "why_not_selected_by_s": None,
                "missing_evidence": None,
            }
    if exploration_reading:
        readings.append(exploration_reading)

    for i, r in enumerate(readings, start=1):
        r["exposure_rank"] = i
    return readings
