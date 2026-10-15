"""地政学インテリジェンス基盤: 地政学専門家AI本格版のエンジン。

指示書14.3章の15段階Reasoning Pipelineは、実装上、Source Pack作成（本ファイル、決定的・コード）→
1回の構造化LLM呼び出し（geo_llm_common.call_llm_structured）→Quality Guard（geo_expert_quality_guard.py、
決定的・コード）→保存、という流れに集約している。

Phase2A: theme_id指定のみの既存呼び出し（case_idなし）は完全に後方互換を維持する。case_id付きで
呼び出された場合のみ、geo_retrieval_serviceからFoundation/Reusable/Case/Contested Contextを取得し
Source Packへ統合する。Contextの整形はToken Budget・Top-K・Evidence必須条件に従い決定的に行い、
除外理由はgeo_retrieval_resultsへ記録する（docs/geopolitical-expert-engine-integration.md参照）。

Source Pack Retrieval P0修正（2026-09-01、docs/expert-ai-validation/m10a_case2_media_increment/
media_activation_trace.md参照）: build_source_packは以前、query_textを一切使わず
「theme_id配下（未指定時はDB全体）で直近fetchされたN件」を無条件に返していた。これにより
theme_id未指定の全ケースで、質問と無関係などの文書でもそのままSource Packへ混入していた
（実例: プーチン/ウクライナに関する質問に、無関係なサステナビリティレポート10本が混入）。
_extract_query_keywords/_relevant_rows_by_keywordsにより、query_textから抽出したKeyword
（Katakana/Latin固有名詞を優先、無ければKanji連続語）へILIKE部分一致した行のみを候補とする
決定的処理へ変更した（新規RPC・migrationは作らない、既存geo_search_service._partial_match_
candidatesと同じ設計方針）。該当が無ければ空を返す（無関係な文書を無理に埋めない）。
"""
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

import jsonschema

import geo_actor_intelligence_service
import geo_audit
import geo_entity_service
import geo_explanatory_model_library
import geo_expert_common
import geo_expert_quality_guard
import geo_expert_schema
import geo_foundation_pack_service
import geo_llm_common
import geo_media_intelligence_service
import geo_institution_intelligence_service
import geo_expert_research_service
import geo_persistent_preference_service
import geo_relationship_service
import geo_retrieval_service

PROMPT_VERSION = "geo-expert-v1.2-session2"

# Actor Intelligence Phase AI-3: Feature Flag（既定OFF、実装・テスト完了までfalseのまま運用）。
# ONで実際に変わるのは、Actor Intelligence Packがプロンプトへ注入され、LLMがkey_actor_profiles[]の
# 新規オプショナルフィールド（why_actor_matters等）に実質的な内容を埋めるようになる点のみ。
# Output Schema自体は常時存在するため、Flag OFF時もこれらはnullとしてわずかにtoken増するが許容する。
ACTOR_INTELLIGENCE_ENABLED_ENV = "GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED"


def actor_intelligence_enabled() -> bool:
    return os.environ.get(ACTOR_INTELLIGENCE_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


def _resolve_actor_intelligence_enabled(override: bool | None) -> bool:
    """operation-scoped override解決ヘルパー（2026-09-25追加、ユーザー指示Option 3）。
    override=None（既定）の場合は既存のglobal env flag（actor_intelligence_enabled()）へ
    そのまま委ねる——Consultation/Draft/RefineDesign等、overrideを渡さない既存呼び出し元は
    一切影響を受けない。override=True/Falseを明示した呼び出し元（Reading生成のみ）だけが、
    global flagの値に関わらずその値を使う。"""
    if override is None:
        return actor_intelligence_enabled()
    return override


# Media Intelligence Build（M8.5）: Feature Flag（既定OFF）。ONで変わるのはMedia
# Intelligence Pack（Media Pattern Candidates/Media Profiles）がプロンプトへ注入される点のみ。
# Actor Intelligence Flagとは独立に制御する（今回のOFF/ON比較実験でActorを変数に含めないため）。
MEDIA_INTELLIGENCE_ENABLED_ENV = "GEO_EXPERT_MEDIA_INTELLIGENCE_ENABLED"


def media_intelligence_enabled() -> bool:
    return os.environ.get(MEDIA_INTELLIGENCE_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Expert AI Phase R1 Chunk3: Institution Intelligence Feature Flag（既定OFF）。ONで
# 変わるのは、Foundation Packで解決済みの制度的Entity（government/ministry/military/
# security_service/international_organization/state_owned_enterprise）についてInstitution
# Intelligence Packがプロンプトへ注入される点のみ。Actor/Media Intelligence Flagとは
# 独立に制御する。
INSTITUTION_INTELLIGENCE_ENABLED_ENV = "GEO_EXPERT_INSTITUTION_INTELLIGENCE_ENABLED"


def institution_intelligence_enabled() -> bool:
    return os.environ.get(INSTITUTION_INTELLIGENCE_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Expert AI Phase R1 Chunk4: External Research Feature Flag（既定OFF）。ONで変わるのは、
# Research Sufficiency GateがCurrent Evidence不足と判定した場合のみ、geo_expert_
# research_service.run_research_cycleが起動する点のみ（既存CallBudgetとは別立ての
# search_budgetを消費、既存Stage1/Stage2/Repairの呼び出し回数には影響しない）。
EXTERNAL_RESEARCH_ENABLED_ENV = "GEO_EXPERT_EXTERNAL_RESEARCH_ENABLED"


def external_research_enabled() -> bool:
    return os.environ.get(EXTERNAL_RESEARCH_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Expert Reasoning Core v0.1（ユーザー指示）: Feature Flag（既定OFF）。ONで変わるのは、
# hypothesis_register/situation_actor_assessmentが発火するprofile×mode組合せ（現状
# deep×theme_design/case_analysisのみ）でのみ、geo_expert_schema.REASONING_CORE_V1_NOTE
# がsystem promptへ追加される点のみ。旧Q1/Q2/Q3実験機能（reasoning_lens_application/
# expert_reasoning_questions/expert_reasoning_checks、いずれもデフォルトOFFのまま）とは
# 独立・無関係。Actor/Media/Institution Intelligence Flagとも独立に制御する。
REASONING_CORE_ENABLED_ENV = "GEO_EXPERT_REASONING_CORE_ENABLED"


def reasoning_core_enabled() -> bool:
    return os.environ.get(REASONING_CORE_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Expert Reasoning Pipeline v0.2（ユーザー指示）: Feature Flag（既定OFF）。ONの場合、
# situation_actor_assessmentが発火するprofile×mode組合せに限り、Reasoning Core v0.1の
# REASONING_CORE_V1_NOTE（Call1/Call2共通の単一Note）の代わりに、Call1(Explore/Activate)
# 用REASONING_PIPELINE_V2_EXPLORE_NOTEとCall2(Reassess/Prune/Compose/Decide)用
# REASONING_PIPELINE_V2_DECIDE_NOTEを、それぞれの呼び出しへ別々に追加する
# （_build_system_promptのstage引数で切り替える）。GEO_EXPERT_REASONING_CORE_ENABLEDと
# 同時にONでも、_build_system_prompt側でv0.2を優先し両方が同時適用されることはない。
REASONING_PIPELINE_V2_ENABLED_ENV = "GEO_EXPERT_REASONING_PIPELINE_V2_ENABLED"


def reasoning_pipeline_v2_enabled() -> bool:
    return os.environ.get(REASONING_PIPELINE_V2_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Decision Calculus（ユーザー指示、Production統合）: Feature Flag（既定ON）。v0.2 Pipeline
# (GEO_EXPERT_REASONING_PIPELINE_V2_ENABLED)がONの場合にのみ意味を持ち、Call1(Explore)の
# REASONING_PIPELINE_V2_EXPLORE_NOTE末尾へ小さなAddendumを追加する（Call2へは追加しない、
# Schema変更なし、Call数増加なし）。E01(Hormuz)でtest-only Lensとして検証済み・E06/E20で
# Regression PASS確認済み。他のFlagと異なり既定ONとする——Decision CalculusはE01のGold
# 固有の答えではなく汎用的なAction-choice trade-off統合Operatorであり、v0.2 Pipeline自体が
# ONの環境では常時有効にする設計。無効化したい場合のみ明示的にOFFへ設定する。
DECISION_CALCULUS_ENABLED_ENV = "GEO_EXPERT_DECISION_CALCULUS_ENABLED"


def decision_calculus_enabled() -> bool:
    return os.environ.get(DECISION_CALCULUS_ENABLED_ENV, "1").strip().lower() in ("1", "true", "yes")


# Trajectory Reasoning Ablation v0.2（ユーザー指示）: Feature Flag（既定OFF）。v0.2 Pipeline
# ONの場合にのみ意味を持ち、Call1(Explore)側にtrajectory_assessment Schemaフィールドを
# 追加し、Call2(Decide)側にTrajectory Reasoning Addendumを追加する。OFF時はProduction
# Baselineと完全に同一動作（Schemaにフィールドが現れない）。test-only、検証完了まで
# Productionへ自動統合しない。
# v0.1からの訂正: 「時系列推論が必要か(Reasoning)」と「追加のEvidence取得が必要か
# (Retrieval)」は別問題であるため、2つ目のFlagで独立に制御する——
# GEO_EXPERT_TRAJECTORY_ENABLEDのみONならCondition B相当（Reasoning Addendumのみ、
# 既存Evidence内での時系列推論）、両方ONならCondition C相当（trajectory_assessmentが
# 具体的なevidence_gapを示した場合のみDeterministic Trajectory Retrievalも実行）。
TRAJECTORY_ENABLED_ENV = "GEO_EXPERT_TRAJECTORY_ENABLED"
TRAJECTORY_RETRIEVAL_ENABLED_ENV = "GEO_EXPERT_TRAJECTORY_RETRIEVAL_ENABLED"


def trajectory_enabled() -> bool:
    return os.environ.get(TRAJECTORY_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


def trajectory_retrieval_enabled() -> bool:
    return os.environ.get(TRAJECTORY_RETRIEVAL_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# CQAS Temporal Integration v0.3（ユーザー指示）: Feature Flag（既定OFF）。v0.1/v0.2の
# trajectory_assessment Gate（Call1が中間判定を出し、それがtrueの場合のみReasoning
# Addendumを足す設計）をやめ、"何が変わったか"という観点をCue(initial_anomaly)/
# Question(investigation_questions)/Selection(main_scenario/alternative_scenarios)
# という既存のCQAS出力へ、trueだった場合のみ足す中間ゲート無しで直接組み込む。新規Schema
# fieldは追加しない（trajectory_assessmentは本Flagでは一切使わない）。v0.1/v0.2の
# 知見（trajectory_assessmentという単一のCall1判定はサンプリング分散で不安定、価値の
# 大半はRetrievalではなくReasoning自体にあった）を踏まえた設計。test-only、検証完了まで
# Productionへ自動統合しない。
CQAS_TEMPORAL_ENABLED_ENV = "GEO_EXPERT_CQAS_TEMPORAL_ENABLED"


def cqas_temporal_enabled() -> bool:
    return os.environ.get(CQAS_TEMPORAL_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Situation Trajectory Intelligence v1（ユーザー指示）: Feature Flag（既定OFF、2本）。
# CQAS v1 Candidate（v0.2 Pipeline + Decision Calculus既定ON + CQAS Temporal）を一切
# 変更せず、Actor/Media Intelligenceと並ぶ新しいFoundation Context Layerとして追加する。
# TRAJECTORY_V1_ENABLEDのみON = Condition B相当（Issue Scope判定＋Trajectory Reasoning
# Frameworkを既存Evidenceの上で適用、新規Retrieval無し）。両方ON = Condition C相当
# （trajectory_information_needが具体的なevidence_gapを示した場合のみ、Issue-scoped/
# Evidence-gap-driven Retrievalも実行）。v0.1/v0.2のtrajectory_assessment・
# GEO_EXPERT_TRAJECTORY_ENABLED・GEO_EXPERT_TRAJECTORY_RETRIEVAL_ENABLEDとは完全に独立
# （同時使用時は両方のSchema/Addendumが並存し得るが、本実験では併用しない）。
TRAJECTORY_V1_ENABLED_ENV = "GEO_EXPERT_TRAJECTORY_V1_ENABLED"
TRAJECTORY_V1_RETRIEVAL_ENABLED_ENV = "GEO_EXPERT_TRAJECTORY_V1_RETRIEVAL_ENABLED"


def trajectory_v1_enabled() -> bool:
    return os.environ.get(TRAJECTORY_V1_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


def trajectory_v1_retrieval_enabled() -> bool:
    return os.environ.get(TRAJECTORY_V1_RETRIEVAL_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Situation Trajectory Intelligence v1.2（ユーザー指示）: Feature Flag（既定OFF、2本）。
# v1のtrajectory_information_need（Boolean Gate: "information_needed" true/false）を
# 廃止し、CQAS自身のReasoning DependencyからEvidence Requestを直接出させる方式へ置き換
# える（Evidence Ablation v1.1で、E01のようにAblationが実際にmain hypothesisを変える
# ケースでもBoolean Gateが6/6でfalseのままだったことを受けた再設計）。v1のFlag/コードは
# 変更せず共存させる（ユーザー指示Section3「既存schema compatibilityのため一時的に残し
# てもよい」）。TRAJECTORY_V1_2_ENABLEDのみON = Evidence Request生成のみ（Retrieval無し、
# v1.2 Condition C相当）。両方ON = Evidence Request + Existing Evidence Satisfaction
# Check + Issue-scoped Retrieval（v1.2 Condition D相当）。
TRAJECTORY_V1_2_ENABLED_ENV = "GEO_EXPERT_TRAJECTORY_V1_2_ENABLED"
TRAJECTORY_V1_2_RETRIEVAL_ENABLED_ENV = "GEO_EXPERT_TRAJECTORY_V1_2_RETRIEVAL_ENABLED"


def trajectory_v1_2_enabled() -> bool:
    return os.environ.get(TRAJECTORY_V1_2_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


def trajectory_v1_2_retrieval_enabled() -> bool:
    return os.environ.get(TRAJECTORY_V1_2_RETRIEVAL_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Hypothesis Generation v1.4（ユーザー指示）: Feature Flag（既定OFF）。v1.3のFrozen-Call1
# 実験で、Full Evidenceでも「継続すれば」というContinuity Hypothesisは6/6で安定生成
# される一方、「相手側の行動・Thresholdが変化したら抑制が外れる」というState-transition
# Hypothesisは0/6だった（E01）。各Hypothesisに両方を必須構造として持たせる、Call1のみ
# で完結するFlag（Retrieval判定には使わない、v1.3とは独立）。
HYPOTHESIS_GENERATION_V1_4_ENABLED_ENV = "GEO_EXPERT_HYPOTHESIS_GENERATION_V1_4_ENABLED"


def hypothesis_generation_v1_4_enabled() -> bool:
    return os.environ.get(HYPOTHESIS_GENERATION_V1_4_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Phase 7-2K-A/B(Deterministic Candidate Set → LLM Materiality Selection、ユーザー指示):
# Feature Flag（既定OFF）。Phase 7-1D/7-2I/7-2Jの3種の異なるPrompt介入がすべて0/3で
# 失敗した後の、責務分離への再設計（詳細はactor_candidate_selection module定義の
# コメント、geo_expert_schema.py参照）。
ACTOR_CANDIDATE_SELECTION_ENABLED_ENV = "GEO_EXPERT_ACTOR_CANDIDATE_SELECTION_ENABLED"


def actor_candidate_selection_enabled() -> bool:
    return os.environ.get(ACTOR_CANDIDATE_SELECTION_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Foundation Context Builder v1（ユーザー指示）: Feature Flag（既定OFF）。Situation
# Trajectory Intelligence（v0.1〜v1.4系）の検証で確定した結論——必要なHistorical
# Context（Foundation Context）がCall1の入力に無いと、そもそも評価対象のHypothesis
# 自体が生成されない——を受け、Actor/Media Intelligenceと同じ位置づけで、Call1より前に
# 過去の経緯（prior_state/precedent/operating_pattern/material_turning_point/
# structural_constraint）を決定的に（追加LLM呼び出し無し）収集し、user_promptへ
# 差し込む。「Foundation Context Retrieval Validation v1」で実証済みの範囲
# （Hormuz/Iran、実文書の厚いCorpus内）を製品化する最小実装。Actor/Relationship/Media
# Layerとの統合は行わない（Section「v1では余計なことをしない」、Situation Trajectoryのみ）。
FOUNDATION_CONTEXT_V1_ENABLED_ENV = "GEO_EXPERT_FOUNDATION_CONTEXT_V1_ENABLED"


def foundation_context_v1_enabled() -> bool:
    return os.environ.get(FOUNDATION_CONTEXT_V1_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Expert Context Architecture v1.3（ユーザー指示）: Feature Flag（既定OFF、2本）。v1/v1.2の
# Optional Self-Report Gate（trajectory_information_need/trajectory_evidence_requests、
# DEPRECATED_FOR_RETRIEVAL_DECISION——コードは残すがRetrieval判定には使わない）を置き換え、
# 各Hypothesisが成立するために必要なEvidence Requirementsを必須構造として列挙させ、既存
# Source Packとの決定的なCoverage Checkで判定する。EXPERT_CONTEXT_V1_3_ENABLEDのみON=
# Requirements生成+Coverage Checkのみ（Retrieval無し）。両方ON=CRITICALかつCoverage∈
# {PARTIALLY_SATISFIED,MISSING}のRequirement（またはDiscriminator）についてのみ、
# TRAJECTORYへ経路づけられたものをIssue-scoped Retrievalする。
EXPERT_CONTEXT_V1_3_ENABLED_ENV = "GEO_EXPERT_EXPERT_CONTEXT_V1_3_ENABLED"
EXPERT_CONTEXT_V1_3_RETRIEVAL_ENABLED_ENV = "GEO_EXPERT_EXPERT_CONTEXT_V1_3_RETRIEVAL_ENABLED"


def expert_context_v1_3_enabled() -> bool:
    return os.environ.get(EXPERT_CONTEXT_V1_3_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


def expert_context_v1_3_retrieval_enabled() -> bool:
    return os.environ.get(EXPERT_CONTEXT_V1_3_RETRIEVAL_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Explanatory Model Activation v0.3（ユーザー指示）: Feature Flag（既定OFF）。v0.2を
# 壊さず追加するFlagであり、v0.2 Pipeline(GEO_EXPERT_REASONING_PIPELINE_V2_ENABLED)が
# ONの場合にのみ意味を持つ（v0.2のExplore/Decide Noteの末尾へAddendumを追加し、Call1
# schemaへmodel_activation moduleを追加する）。v0.2がOFFのままこのFlagだけONにしても
# 何も変化しない（v0.1のNoteへ混入することもない）。比較対象は Baseline / Pipeline v0.2
# / Pipeline v0.2 + Model Activation v0.3 の3状態。
EXPLANATORY_MODEL_ACTIVATION_ENABLED_ENV = "GEO_EXPERT_EXPLANATORY_MODEL_ACTIVATION_ENABLED"


def explanatory_model_activation_enabled() -> bool:
    return os.environ.get(EXPLANATORY_MODEL_ACTIVATION_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# Selector-Free Model Sweep実験（ユーザー指示、v0.3のAblation、既定OFF）: v0.3の
# model_activation_requests Selectorを使わず、ACTIVE+EXPERIMENTAL計8件のFull Model
# CardをすべてCall2へ渡す1回限りの実験用Flag。v0.2 Pipeline ONの上でのみ意味を持つ
# （v0.3 Selectorと同時にONの場合はSweepを優先する——v0.3の既存結果は再実行せず
# Sweep側だけを新規実行する、という今回の比較設計に合わせる）。
EXPLANATORY_MODEL_SWEEP_ENABLED_ENV = "GEO_EXPERT_EXPLANATORY_MODEL_SWEEP_ENABLED"


def explanatory_model_sweep_enabled() -> bool:
    return os.environ.get(EXPLANATORY_MODEL_SWEEP_ENABLED_ENV, "").strip().lower() in ("1", "true", "yes")


# セクション別Top-K（Context投入ルール、承認済みPlan参照）
SECTION_TOP_K = {
    "foundation": 5, "precedent": 3, "mechanism": 3, "reasoning_rule": 5,
    "indicator": 5, "live": 10, "gap": 10,
    # Phase2B-2: External Reference / Internal Expert Knowledge区分の追加セクション
    "observed_fact": 10, "actor_claim": 10, "external_assessment": 10,
    "internal_expert_assessment": 5, "internal_expert_reasoning": 10,
}
# Token Budget（文字数ベースの簡易概算。正確なtokenizerは導入しない）
DEFAULT_CHAR_BUDGET = 6000 * 2  # 1トークン≒2文字相当の粗い概算


# Source Pack Retrieval: query_textから固有名詞らしいKeywordを抽出する軽量な正規表現ベース抽出
# （形態素解析ライブラリは導入しない）。Katakana/Latin（人名・地名・組織名・略語等の固有名詞である
# 可能性が高い）を優先Keywordとして扱い、これらが1件も無い場合のみKanji連続語（依頼文の定型語
# を除く）へフォールバックする。定型語（現在/今後/分析等）は複数Caseで共通して出現し、かつ
# 「規制」のようにドメインを跨いで頻出する語は誤ヒットの原因になるため、Kanji側にのみ
# ストップワードを適用する。
# Latinは3文字以上のみ抽出する（2文字だと"AI"が"Strait"のように無関係な単語の部分
# 文字列として誤ヒットする実例を確認したため——docs/expert-ai-validation/
# m10a2_case2_media_increment/report.md参照。ILIKEは単語境界を認識しないため、
# 抽出側で最低文字数を上げて緩和する）。
_KATAKANA_LATIN_KEYWORD_PATTERN = re.compile(r"[A-Za-z]{3,}|[ァ-ヶー]{2,}")
_KANJI_KEYWORD_PATTERN = re.compile(r"[一-龥々]{2,}")
_GENERIC_KANJI_KEYWORD_STOPWORDS = {
    "現在", "今後", "方向性", "分析", "意思決定", "局面", "目的", "主体", "主要",
    "規制", "政策", "関係", "対応", "力学", "制約", "地域", "国内", "国際", "状況",
    "対象", "今後取り得る",
    # Phase 10-R(Source Pack Retrieval Precision Hotfix、ユーザー指示): 「米国」
    # 「欧州」は固有名詞ではあるが、地政学クエリ全般に広く出現しすぎる（実データで
    # 確認済み——NATO/TSMCクエリでこの2語の共起により、無関係なEUサステナ規制記事が
    # 候補採用されていた）ため、単独では判別力の無いGeneric扱いとする。他のGeneric語
    # との組合せでの採用（2件以上一致ルール）自体は妨げない。
    "米国", "欧州",
}
# Phase 10-R(Source Pack Retrieval Precision Hotfix、ユーザー指示): Gold Caseの
# original_questionが「Key Actors」「Leverage」「Primary Key Point」等の分析framing
# 用語を含む定型的な構成で書かれることがあり（実データで確認済み——Saudi-Israel
# クエリ）、これらのASCII英単語は単にLatin文字列という理由だけでStrong Anchor
# （固有名詞らしいKeyword）に誤分類されていた。_GENERIC_KANJI_KEYWORD_STOPWORDSと
# 同じ思想で、英語側にも汎用framing語のstopwordを設ける。
_GENERIC_LATIN_KEYWORD_STOPWORDS = {
    "key", "actor", "actors", "leverage", "point", "points", "gap", "gaps",
    "card", "cards", "primary", "evidence",
}
# Phase 10-R(Source Pack Retrieval Precision Hotfix、ユーザー指示): 上と同じ分析
# framing語のKatakana外来語表記版（実データで確認済み——Saudi-Israelクエリの
# 「カード」「ポジション」「シナリオ」「プロセス」「レッドライン」が、真の固有名詞
# （サウジアラビア/イスラエル等）と同じStrong Anchor扱いされ、max_keywords上限を
# 専有していたためKanji側の具体的なcontent語（安全保障・防衛協力等）が一件も
# 抽出されない事態を招いていた）。
_GENERIC_KATAKANA_KEYWORD_STOPWORDS = {
    "カード", "ポジション", "シナリオ", "プロセス", "レッドライン",
}


def _extract_query_keywords(query_text: str, max_keywords: int = 10) -> list:
    if not query_text:
        return []
    keywords = []
    for candidate in _KATAKANA_LATIN_KEYWORD_PATTERN.findall(query_text):
        if candidate in keywords:
            continue
        if candidate.lower() in _GENERIC_LATIN_KEYWORD_STOPWORDS:
            continue
        if candidate in _GENERIC_KATAKANA_KEYWORD_STOPWORDS:
            continue
        keywords.append(candidate)
    for candidate in _KANJI_KEYWORD_PATTERN.findall(query_text):
        if candidate in keywords:
            continue
        # 部分一致で除外する（例:「今後取」は「今後」を含むため除外——正規表現が
        # ストップワードに後続の漢字を連結して抽出してしまうケースに対応するため、
        # 完全一致だけでなく包含関係も見る）
        if any(stopword in candidate for stopword in _GENERIC_KANJI_KEYWORD_STOPWORDS):
            continue
        keywords.append(candidate)
    return keywords[:max_keywords]


_ASCII_LATIN_KEYWORD_PATTERN = re.compile(r"[A-Za-z]+")


def _classify_keywords(keywords: list) -> tuple:
    """Phase 10-R(Source Pack Retrieval Precision Hotfix、ユーザー指示): keywordsを
    Strong Anchor（Katakana/Latin語、固有名詞である可能性が高い）とGeneric（それ以外、
    主に汎用的なKanji語）に分離する。個別Theme・個別Case専用のlistは作らない——
    _extract_query_keywordsが既にKatakana/Latinを優先Keywordとして扱っている判定基準
    （_KATAKANA_LATIN_KEYWORD_PATTERN）をそのまま流用するため、Case横断で一般化できる。"""
    strong = [k for k in keywords if _KATAKANA_LATIN_KEYWORD_PATTERN.fullmatch(k)]
    generic = [k for k in keywords if k not in strong]
    return strong, generic


_GENERIC_KEYWORD_ADMISSION_MIN_MATCHES = 2
_WORD_BOUNDARY_PATTERN_CACHE: dict = {}


def _ascii_word_boundary_match(keyword: str, text: str) -> bool:
    """Phase 10-R(Source Pack Retrieval Precision Hotfix、ユーザー指示): "MoU"のような
    短いASCII Latin語（略語・固有名詞）は、ILIKEの単純substring一致だと"mounted"
    "mountain"「mouth」等、無関係な英単語の内部に偶然出現するだけで誤ヒットする
    （実データで確認済み——ホルムズ海峡クエリの"MoU"が、無関係な台湾海峡記事の
    英文中の"mounted"/"mountain"/"mouth"にsubstring一致し、Source Pack上位に
    混入していた）。ASCII Latin Keywordについてのみ、前後がASCII英数字で無いこと
    （(?<![A-Za-z0-9])keyword(?![A-Za-z0-9])）を再検証し、他の英単語へ食い込んで
    いる誤ヒットのみを除く。標準の正規表現\\b（Unicode word boundary）は使わない
    ——日本語では"MoU後"「PLA機」のようにLatin略語へKanjiが直接（空白無しで）
    後続するのが通常の書き方であり、\\bはKanjiも\\w扱いするため、この正常な用例まで
    誤って除外してしまう（実際に既存テストで確認済みの回帰）。Kanji/Katakanaへの
    隣接は境界とみなし許容し、他のASCII英数字への食い込みのみを境界違反として扱う。"""
    pattern = _WORD_BOUNDARY_PATTERN_CACHE.get(keyword)
    if pattern is None:
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(keyword) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        _WORD_BOUNDARY_PATTERN_CACHE[keyword] = pattern
    return bool(pattern.search(text or ""))


_KATAKANA_CHAR_CLASS = "ァ-ヶー"
_KATAKANA_ONLY_PATTERN = re.compile(r"[" + _KATAKANA_CHAR_CLASS + r"]+")


def _katakana_word_boundary_match(keyword: str, text: str) -> bool:
    """Post-Freeze Integrity Audit Finding D-3(2026-09-22発見): 「南シナ海」から抽出
    される2文字Katakana語「シナ」が、無関係語「シナリオ」の内部へsubstring一致して
    しまうケースが見つかった（"シナリオ"はKeyword抽出側では_GENERIC_KATAKANA_
    KEYWORD_STOPWORDSで既に除外済みだが、"シナ"という短い派生Keyword自体は
    stopword対象外で、他語への食い込みまでは防げていなかった）。_ascii_word_
    boundary_matchと同じ思想で、前後がKatakana文字で連続していないことを確認し、
    真に独立したKatakana語としての出現のみを一致とみなす。"""
    cache_key = ("katakana", keyword)
    pattern = _WORD_BOUNDARY_PATTERN_CACHE.get(cache_key)
    if pattern is None:
        pattern = re.compile(
            r"(?<![" + _KATAKANA_CHAR_CLASS + r"])" + re.escape(keyword) + r"(?![" + _KATAKANA_CHAR_CLASS + r"])")
        _WORD_BOUNDARY_PATTERN_CACHE[cache_key] = pattern
    return bool(pattern.search(text or ""))


# Corpus Integrity P0(ユーザー指示、2026-09-22発見): 全コーパス監査で、is_mock=false
# のまま本文中に自己申告のTEST CORPUS markerを持つ合成文書が10件見つかった
# （AI間連携テスト用に作られた公開情報ベース分析ブリーフ、document idはGEO-SUS-接頭辞）。
# is_mockフラグでは検出できないため、明示的・確定的なmarkerのみで個別にRetrieval対象から
# 除外する。曖昧な一般語（"test"等）は誤除外のリスクがあるため対象にしない。
_SYNTHETIC_TEST_CORPUS_MARKERS = ("TEST CORPUS", "AI-to-AI Test Queries", "AI 間連携テスト", "GEO-SUS-")


def _is_synthetic_test_corpus_row(row: dict) -> bool:
    for value in row.values():
        if isinstance(value, str) and any(marker in value for marker in _SYNTHETIC_TEST_CORPUS_MARKERS):
            return True
    return False


# Post-Freeze Integrity Audit Finding C-1(2026-09-22発見): 全Theme監査で、theme自体が
# 捏造コンテンツの器になっていながら、配下のdocument/eventが通常のkeyword一致
# Retrievalから除外されていない例が見つかった（実例: theme_id=f6e3631f-f4a7-4802-
# 8af8-aeec5c371a65「Zelenskyy Actor Intelligence Test」、捏造Zelenskyy声明を含む
# documentと3件のevent）。「テストである」という信号が行自体ではなくgeo_themes側に
# のみあるため、_is_synthetic_test_corpus_rowでは検知できない。
#
# 【設計変更の経緯、2026-09-22 R2 Exit Gateで発見】: 当初`geo_themes.status != '有効'`
# という広い基準（全10 Theme中7件が「無効」）で除外する実装を試みたが、R2 Exit Gate
# 検証で、NATO Gold Caseの実Evidence文書2件（"NATO 3.0: Burden-Sharing"・"Defence
# investment and NATO's 5% commitment"）が、theme_id=05064de8-48f8-484a-8243-
# f8c837c5a735「Expert AI検証用ベンチマーク」という**disabledだが実データを保持する**
# Themeに属しており、この広い基準がNATO Source Packを0件へ静かに破壊することが判明
# した——「Theme無効=中身も偽物」という前提は誤りだった（無効化の理由は多様で、
# 内容の真正性とは独立）。そのため、`_SYNTHETIC_TEST_CORPUS_MARKERS`と同じ設計思想
# （汎用的な推測ではなく、実監査で捏造と確認済みのものだけを明示登録する）へ変更する。
_KNOWN_SYNTHETIC_THEME_IDS = {"f6e3631f-f4a7-4802-8af8-aeec5c371a65"}


def _excluded_theme_ids(client) -> set:
    return set(_KNOWN_SYNTHETIC_THEME_IDS)


def _relevant_rows_by_keywords(client, table: str, id_field: str, columns: list, keywords: list, *,
                                theme_id: str = None, limit: int) -> list:
    """keywordsのいずれかがcolumnsのいずれかへILIKE部分一致する行を、id_fieldで重複排除しつつ
    集める（geo_search_service._partial_match_candidatesと同じ既存パターン、新規RPC・
    migrationは作らない）。theme_id指定時はさらにtheme_idで絞り込む。

    Phase 10-R(Source Pack Retrieval Precision Hotfix、ユーザー指示): 実データで
    確認された複数の汚染・過剰厳格化経路にそれぞれ対応する:
    (1) ASCII Latin語（"MoU"等の短い略語）は、_ascii_word_boundary_matchで単語境界
        一致を再確認し、無関係な英単語内部への偶然のsubstring一致は候補から除く
        （Genericかどうかに関わらず全Latin語に適用）。
    (2) Generic（Kanji）語は1件だけの一致では候補採用しない——Strong Anchor
        （Katakana/Latin語で、かつ(1)を通過したもの）に1件でも一致する行、または
        Genericに2件以上一致する行のみを採用する。queryから抽出できたGeneric語の
        総数が少ない場合（2件要求すると、そのquery自身の判別力の低さとは無関係に
        Retrievalが常にゼロ件近くまで痩せてしまう——実データで確認済み、US-China
        クエリでGeneric語が「米中間」「半導体」の2件しか無く、両方一致を要求した
        結果Source Packが1文書まで減少していた）は、1件一致へfallbackする
        （recallを過度に落とさない）。
    (3) is_mock=trueの行（検証用Fixtureデータ）は、Genericの語数に関わらず候補から
        除外する（実データで確認済み——NATOクエリのSource Packに【検証用Fixture】
        文書が混入していた）。
    (4) Corpus Integrity P0（2026-09-22発見）: is_mock=falseのまま本文中に自己申告の
        TEST CORPUS markerを持つ合成文書（_is_synthetic_test_corpus_row）も同様に
        除外する。
    (5) Post-Freeze Integrity Audit Finding C-1（2026-09-22発見）: 行自体ではなく
        親Theme（geo_themes.status）がテスト・無効化済みの場合も除外する
        （_excluded_theme_ids）。呼び出し元が明示的にそのtheme_idをスコープ指定した
        場合は除外しない。
    (6) Post-Freeze Integrity Audit Finding D-2（2026-09-22発見、Saudi-Israel Source
        Pack実質0%オントピック事案）: Strong AnchorのうちKatakana語（国名・組織名等、
        コーパス内で話題を跨いで頻出しやすい）については、Generic語と同じ「2件以上
        一致」ルールを適用する。以前はStrong Anchorが1件でも一致すれば無条件採用して
        いたため、query内に複数のKatakana Strong Anchorが抽出された場合
        （例:「サウジアラビア」「イスラエル」「イラン」）、本来の主題と無関係でも
        「イラン」のみ一致する文書（Hormuz/Iran記事）が無差別に採用されていた。
        ASCII/Latin Strong Anchor（"MoU"や"TSMC"のような固有の略語・型番）は対象外
        のまま単独一致を許容する——(1)のword boundary検証を経た上でのASCII単独一致は、
        Katakana国名と異なりコーパス内で話題を跨いで頻出するリスクが低く、実際に
        既存のcross-language単独一致ケース（例: 日本語クエリの"MoU"に対する英語文書）
        を壊さないために区別が必要（Katakana語彙のみに絞ったfallback-to-1条件は(2)と
        同じ理由）。
    (7) Post-Freeze Integrity Audit Finding D-3（2026-09-22発見）: 短いKatakana語も
        _katakana_word_boundary_matchで単語境界一致を再検証し、より長い無関係語
        （例:「シナ」→「シナリオ」）への偶然のsubstring食い込みを除く（(1)のKatakana版）。
    個別Theme・個別Case専用のhardcodeはしない。戻り値の各行には、後続のranking
    （build_source_pack側）で使う_strong_anchor_match(bool)を付与する（Source Pack
    本文へは出さない内部フラグ）。"""
    strong_keywords, generic_keywords = _classify_keywords(keywords)
    strong_katakana_keywords = [k for k in strong_keywords if _KATAKANA_ONLY_PATTERN.fullmatch(k)]
    min_generic_matches = (_GENERIC_KEYWORD_ADMISSION_MIN_MATCHES
                            if len(generic_keywords) > _GENERIC_KEYWORD_ADMISSION_MIN_MATCHES else 1)
    min_strong_katakana_matches = (_GENERIC_KEYWORD_ADMISSION_MIN_MATCHES
                                    if len(strong_katakana_keywords) > _GENERIC_KEYWORD_ADMISSION_MIN_MATCHES
                                    else 1)
    excluded_theme_ids = _excluded_theme_ids(client)
    rows_by_id = {}
    matched_strong_ascii = {}
    matched_strong_katakana = {}
    matched_generic = {}
    for keyword in keywords:
        is_strong = keyword in strong_keywords
        is_ascii_latin = bool(_ASCII_LATIN_KEYWORD_PATTERN.fullmatch(keyword))
        is_katakana_only = bool(_KATAKANA_ONLY_PATTERN.fullmatch(keyword))
        for column in columns:
            params = {"select": "*", column: f"ilike.*{keyword}*", "limit": str(limit)}
            if theme_id:
                params["theme_id"] = f"eq.{theme_id}"
            try:
                rows = client.select(table, params)
            except Exception:  # noqa: BLE001 — ILIKE非対応列等、既存_partial_match_candidatesと同じ扱い
                rows = []
            for row in rows:
                if row.get("is_mock"):
                    continue
                if _is_synthetic_test_corpus_row(row):
                    continue
                row_theme_id = row.get("theme_id")
                if row_theme_id and row_theme_id in excluded_theme_ids and row_theme_id != theme_id:
                    continue
                if is_ascii_latin and not _ascii_word_boundary_match(keyword, row.get(column) or ""):
                    continue
                if is_katakana_only and not _katakana_word_boundary_match(keyword, row.get(column) or ""):
                    continue
                row_id = row[id_field]
                rows_by_id[row_id] = row
                if is_strong and is_katakana_only:
                    matched_strong_katakana.setdefault(row_id, set()).add(keyword)
                elif is_strong:
                    matched_strong_ascii.setdefault(row_id, set()).add(keyword)
                else:
                    matched_generic.setdefault(row_id, set()).add(keyword)

    admitted = []
    for row_id, row in rows_by_id.items():
        strong_ascii_match_count = len(matched_strong_ascii.get(row_id, ()))
        strong_katakana_match_count = len(matched_strong_katakana.get(row_id, ()))
        generic_match_count = len(matched_generic.get(row_id, ()))
        has_strong = strong_ascii_match_count >= 1 or strong_katakana_match_count >= min_strong_katakana_matches
        if has_strong or generic_match_count >= min_generic_matches:
            row["_strong_anchor_match"] = has_strong
            admitted.append(row)
    return admitted


# Source Pack Retrieval: 文書一覧の表現力強化（2026-09-01、docs/expert-ai-validation/
# m10a2_case2_media_increment/report.md参照）。以前はtitleのみを表示しdocument_id・
# extracted_textを一切渡していなかったため、LLMが「引用可能なID」を持てず、Media
# Reasoning等がdocumentをタイトル文字列で引用しようとしてEvidence Fabrication
# Scrubで除去される事態が発生していた。document_id・出典・公開日時・関連抜粋を
# 明示することで、Current Evidenceとして構造的に引用できるようにする。
DOCUMENT_EXCERPT_CHAR_BUDGET = 1600


def _excerpt_around_keywords(text: str, keywords: list, budget: int = DOCUMENT_EXCERPT_CHAR_BUDGET) -> str:
    """textがbudgetを超える場合、keywordsのいずれかが最初に出現する位置を中心とした
    抜粋を返す（全文をそのまま渡すと1文書でtoken予算を圧迫するため）。一致箇所が
    見つからない場合（例: titleのみでマッチしtitle自体には出ていない場合）は先頭から
    budget分を返す。"""
    if not text:
        return ""
    if len(text) <= budget:
        return text
    lower_text = text.lower()
    match_pos = None
    for keyword in keywords:
        idx = lower_text.find(keyword.lower())
        if idx != -1:
            match_pos = idx
            break
    if match_pos is None:
        return text[:budget] + "…"
    half = budget // 2
    start = max(0, match_pos - half)
    end = min(len(text), start + budget)
    start = max(0, end - budget)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _source_names_by_id(client, source_ids: list) -> dict:
    unique_ids = sorted({sid for sid in source_ids if sid})
    if not unique_ids:
        return {}
    rows = client.select("geo_source_registry", {
        "select": "source_id,name", "source_id": f"in.({','.join(unique_ids)})",
    })
    return {r["source_id"]: r["name"] for r in rows}


# Phase R1 Chunk2: geo_source_registry.source_type（既存、official_site/news/sns/
# commercial_data/other）から、evidence_relevance.source_type（primary_official等）へ
# 決定論的にマッピングする。LLMの自己申告には依存しない——解決できないevidence_idは
# _evidence_source_typesの戻り値に含めず、呼び出し側でunknownとして扱う。
_SOURCE_REGISTRY_TYPE_MAP = {
    "official_site": "primary_official",
    "news": "current_news",
    "sns": "osint_physical",
    "commercial_data": "specialist_analysis",
    "other": "unknown",
}


def _evidence_source_types(client, evidence: list, documents: list = None) -> dict:
    """evidence_id/document_id → source_type（決定論的）。geo_evidence.document_id →
    geo_documents.source_id → geo_source_registry.source_typeの経路で解決できた
    evidenceのみ値を持つ。この経路を辿れないevidence（document_id無し、または
    source_idが未登録）は戻り値に含めない。

    Phase R1 Chunk4: documentsを渡した場合、source_pack自体が保持するdocument（
    geo_evidenceに包まれずdocument_idで直接引用され得るもの、External Research由来の
    文書を含む）についてもdocument_id → source_typeを同じ経路で解決し、戻り値の同じ
    辞書へ含める（evidence_idキーと衝突しないUUID空間のため混在させてよい）。"""
    documents = documents or []
    document_ids = sorted(
        {e.get("document_id") for e in evidence if e.get("document_id")}
        | {d.get("document_id") for d in documents if d.get("document_id")}
    )
    if not document_ids:
        return {}
    docs = client.select("geo_documents", {
        "select": "document_id,source_id", "document_id": f"in.({','.join(document_ids)})",
    })
    doc_source_id = {d["document_id"]: d.get("source_id") for d in docs}
    source_ids = sorted({sid for sid in doc_source_id.values() if sid})
    if not source_ids:
        return {}
    registry_rows = client.select("geo_source_registry", {
        "select": "source_id,source_type", "source_id": f"in.({','.join(source_ids)})",
    })
    registry_type_by_source_id = {r["source_id"]: r.get("source_type") for r in registry_rows}
    result = {}
    for e in evidence:
        source_id = doc_source_id.get(e.get("document_id")) if e.get("document_id") else None
        registry_type = registry_type_by_source_id.get(source_id) if source_id else None
        mapped = _SOURCE_REGISTRY_TYPE_MAP.get(registry_type)
        if mapped:
            result[e["evidence_id"]] = mapped
    for d in documents:
        doc_id = d.get("document_id")
        if not doc_id:
            continue
        source_id = doc_source_id.get(doc_id)
        registry_type = registry_type_by_source_id.get(source_id) if source_id else None
        mapped = _SOURCE_REGISTRY_TYPE_MAP.get(registry_type)
        if mapped:
            result[doc_id] = mapped
    return result


# ---------------------------------------------------------------------------
# Expert Reasoning Pipeline v0.2（ユーザー指示）: Deterministic Supplemental Retrieval。
# Call1(Explore/Activate)がintelligence_needsとして名指ししたNeedのみ、追加LLM呼び出し
# 無しでDB/既存Service関数から解決し、見つかった内容だけをCall2(Decide)側のPromptへ渡す。
# Regression Test等の比較実験はcase_id=Noneで実行される（case_idスコープのActor
# Intelligence Retrievalは発火しない）ため、case_id非依存のentity_id単位API
# （get_actor_profile_item_by_entity_id/list_relationships_for_entity）と、
# build_source_packが使うのと同じKeyword一致検索（_extract_query_keywords/
# _relevant_rows_by_keywords、geo_events/geo_documents）を再利用する。存在しない
# Entity・事例を無理に埋めず、見つからない場合は「未検出」と正直に返す（fabricationしない）。
# ---------------------------------------------------------------------------
_SUPPLEMENTAL_RETRIEVAL_MAX_NEEDS = 8
_SUPPLEMENTAL_RETRIEVAL_ROWS_PER_NEED = 3


def _find_entity_by_query(query: str, entities_cache: list) -> dict | None:
    """queryとcanonical_nameの部分文字列一致（相互どちらかを含む、case-insensitive）で
    1件だけ解決する。新規Entity検索APIは追加せず、既存resolve_entity_id_for_actor_
    intelligence_idと同じ「候補一覧をPython側でスキャンする」方式に倣う。"""
    if not query:
        return None
    query_lower = query.lower()
    for entity in entities_cache:
        name = entity.get("canonical_name") or ""
        name_lower = name.lower()
        if name_lower and (name_lower in query_lower or query_lower in name_lower):
            return entity
    return None


def _format_entity_profile_note(entity: dict, profile_item: dict | None) -> str:
    name = entity.get("canonical_name") or entity["entity_id"]
    if not profile_item:
        return f"- {name}: 登録されたActor Profileなし（Evidenceとして扱わないこと）"
    content = profile_item.get("structured_content") or {}
    parts = []
    if content.get("objectives"):
        parts.append("objectives=" + "; ".join(content["objectives"][:3]))
    if content.get("core_interests"):
        parts.append("core_interests=" + "; ".join(content["core_interests"][:3]))
    if content.get("constraints"):
        parts.append("constraints=" + "; ".join(content["constraints"][:3]))
    current_posture = content.get("current_posture") or {}
    if current_posture.get("current_direction"):
        parts.append(f"current_direction={current_posture['current_direction']}")
    if not parts:
        return f"- {name}: Actor Profileはあるが該当する内容なし"
    return f"- {name}: " + " / ".join(parts)


def _format_relationship_note(entity: dict, relationships: list, entities_by_id: dict) -> str:
    name = entity.get("canonical_name") or entity["entity_id"]
    if not relationships:
        return f"- {name}: 登録されたRelationshipなし"
    lines = []
    for rel in relationships[:_SUPPLEMENTAL_RETRIEVAL_ROWS_PER_NEED]:
        other_id = rel.get("to_entity_id") if rel.get("from_entity_id") == entity["entity_id"] else rel.get("from_entity_id")
        other_name = (entities_by_id.get(other_id) or {}).get("canonical_name") or other_id
        lines.append(f"{name}—{other_name}: {rel.get('relationship_type')}({rel.get('relationship_domain')})")
    return "- " + " / ".join(lines)


def _resolve_entity_need(client, need: dict, entities_cache: list, entities_by_id: dict, *,
                          actor_profile_lookup_statuses: tuple, actor_profile_retrieval_status) -> str | None:
    entity = _find_entity_by_query(need.get("query") or "", entities_cache)
    if not entity:
        return f"- Need「{need.get('query')}」に一致するEntityは見つからなかった"
    profile_item = geo_actor_intelligence_service.get_actor_profile_item_by_entity_id(
        client, entity["entity_id"], retrieval_status=actor_profile_retrieval_status,
        statuses=actor_profile_lookup_statuses)
    return _format_entity_profile_note(entity, profile_item)


def _resolve_relationship_need(client, need: dict, entities_cache: list, entities_by_id: dict) -> str | None:
    entity = _find_entity_by_query(need.get("query") or "", entities_cache)
    if not entity:
        return f"- Need「{need.get('query')}」に一致するEntityは見つからなかった"
    relationships = geo_relationship_service.list_relationships_for_entity(client, entity["entity_id"])
    return _format_relationship_note(entity, relationships, entities_by_id)


def _resolve_precedent_or_structural_fact_need(client, need: dict, *, theme_id: str | None) -> str | None:
    keywords = _extract_query_keywords(need.get("query") or "")
    if not keywords:
        return f"- Need「{need.get('query')}」からは検索可能なKeywordを抽出できなかった"
    events = _relevant_rows_by_keywords(
        client, "geo_events", "event_id", ["actor", "action", "target", "event_type"], keywords,
        theme_id=theme_id, limit=_SUPPLEMENTAL_RETRIEVAL_ROWS_PER_NEED)
    documents = _relevant_rows_by_keywords(
        client, "geo_documents", "document_id", ["title", "extracted_text"], keywords,
        theme_id=theme_id, limit=_SUPPLEMENTAL_RETRIEVAL_ROWS_PER_NEED)
    if not events and not documents:
        return f"- Need「{need.get('query')}」に一致する既存Event/Documentは見つからなかった"
    lines = []
    for e in events[:_SUPPLEMENTAL_RETRIEVAL_ROWS_PER_NEED]:
        lines.append(f"event: {e.get('actor')} {e.get('action')} {e.get('target')} ({e.get('event_type')})")
    for d in documents[:_SUPPLEMENTAL_RETRIEVAL_ROWS_PER_NEED]:
        lines.append(f"document: {d.get('title')}")
    return "- " + " / ".join(lines)


def _run_supplemental_retrieval(client, intelligence_needs: list | None, *, theme_id: str | None = None,
                                 actor_profile_lookup_statuses: tuple = ("active",),
                                 actor_profile_retrieval_status: str | None = "available") -> str:
    """intelligence_needsを実際にDB/Service照会で解決し、Call2側Promptへ差し込むテキストを
    返す（新規Azure呼び出しはしない）。Need自体が無い場合は空文字列を返す（無理に埋めない）。"""
    if not intelligence_needs:
        return ""
    needs = intelligence_needs[:_SUPPLEMENTAL_RETRIEVAL_MAX_NEEDS]
    entities_cache = geo_entity_service.list_entities(client, include_demo=True, limit=1000)
    entities_by_id = {e["entity_id"]: e for e in entities_cache}
    lines = []
    for need in needs:
        need_type = need.get("need_type")
        if need_type == "entity":
            note = _resolve_entity_need(
                client, need, entities_cache, entities_by_id,
                actor_profile_lookup_statuses=actor_profile_lookup_statuses,
                actor_profile_retrieval_status=actor_profile_retrieval_status)
        elif need_type == "relationship":
            note = _resolve_relationship_need(client, need, entities_cache, entities_by_id)
        elif need_type in ("precedent", "structural_fact"):
            note = _resolve_precedent_or_structural_fact_need(client, need, theme_id=theme_id)
        else:
            note = None
        if note:
            lines.append(note)
    if not lines:
        return ""
    return (
        "\n\n【Deterministic Supplemental Retrieval（Call1のintelligence_needsをDB/Service照会"
        "のみで解決した結果。LLM呼び出しは追加していない）】\n"
        "見つからなかったNeedは無理に埋めず、その旨をそのままCandidate Reassessmentの材料に"
        "すること（Evidence不足の場合はunresolved/低confidenceのまま扱う）。\n" + "\n".join(lines)
    )


_TRAJECTORY_MAX_RETRIEVAL_QUESTIONS = 6
_TRAJECTORY_MAX_CANDIDATES = 15


def trajectory_retrieval_should_run(trajectory_assessment: dict | None) -> bool:
    """Trajectory Reasoning Ablation v0.2（ユーザー指示）: Reasoning(Addendum)の要否と
    Retrievalの要否を独立に判定する。Retrievalは「具体的なevidence_gapがあり、Call1自身が
    supplemental_retrieval_needed=trueと申告した場合」のみ実行し、trajectory_reasoning_
    needed=trueというだけでは実行しない（既存Evidenceだけで時系列推論が可能なケースを
    区別するため）。"""
    if not trajectory_assessment or not trajectory_assessment.get("trajectory_reasoning_needed"):
        return False
    if trajectory_assessment.get("existing_evidence_sufficient"):
        return False
    if not trajectory_assessment.get("supplemental_retrieval_needed"):
        return False
    return bool(trajectory_assessment.get("evidence_gaps"))


def _run_trajectory_retrieval(client, trajectory_assessment: dict | None, *,
                               theme_id: str | None = None) -> str:
    """Trajectory Reasoning Ablation v0.2（ユーザー指示、test-only）: 呼び出し元
    （run_expert_request_staged）がtrajectory_retrieval_should_run()でgateした後にのみ
    呼ばれる。既存の_relevant_rows_by_keywords（geo_events/geo_documentsへのILIKE
    キーワード検索、_resolve_precedent_or_structural_fact_needと同じ既存機構）をそのまま
    再利用し、新規Retrieval Infra・新規DBは追加しない。v0.1からの訂正: 汎用的な背景History
    ではなく、Call1が挙げたevidence_gaps/retrieval_questionsを中心にKeywordを組み立てる
    （actors/topicは補助的な絞り込みにのみ使う）。ここではCandidateを機械的に集めて返す
    だけで、どれがMaterial Turning Pointか（what_changed）の判断はCall2のLLM Reasoningへ
    委ねる（新規LLM要約Callは行わない）。"""
    if not trajectory_assessment:
        return ""
    actors = trajectory_assessment.get("actors") or []
    topic = trajectory_assessment.get("topic") or ""
    gaps = trajectory_assessment.get("evidence_gaps") or []
    questions = (trajectory_assessment.get("retrieval_questions") or [])[:_TRAJECTORY_MAX_RETRIEVAL_QUESTIONS]
    # evidence_gaps/retrieval_questionsを主とし、actors/topicは補助（各queryが具体的な
    # Gapに対応することを優先し、一般的な背景History取得にしない）。
    keywords = []
    for src in (*gaps, *questions, *actors, topic):
        for kw in _extract_query_keywords(src or ""):
            if kw not in keywords:
                keywords.append(kw)
    primary_mode = trajectory_assessment.get("primary_mode") or "unspecified"
    modes = trajectory_assessment.get("trajectory_modes") or []
    reason = trajectory_assessment.get("reason") or ""
    lookback = trajectory_assessment.get("lookback_window") or "unspecified"
    header = (
        "\n\n【TRAJECTORY CONTEXT（Deterministic Retrieval、未加工のCandidate。LLM要約は"
        "行っていない——どのCandidateがMaterial Turning Pointかの判断はこのCall2自身が"
        "行うこと）】\n"
        f"Primary mode: {primary_mode} / All modes: {', '.join(modes) if modes else '(none)'}\n"
        f"Why trajectory reasoning was judged necessary (Call1): {reason}\n"
        f"Evidence gaps Call1 identified: {'; '.join(gaps) if gaps else '(none)'}\n"
        f"Lookback window requested by Call1: {lookback}\n"
    )
    if not keywords:
        return (header + "検索可能なKeywordを抽出できなかったため、Candidateを取得できなかった。"
                "無理にTrajectoryを構成せず、現在のSnapshotの範囲で判断すること。")
    events = _relevant_rows_by_keywords(
        client, "geo_events", "event_id", ["actor", "action", "target", "event_type"], keywords,
        theme_id=theme_id, limit=_TRAJECTORY_MAX_CANDIDATES)
    documents = _relevant_rows_by_keywords(
        client, "geo_documents", "document_id", ["title", "extracted_text"], keywords,
        theme_id=theme_id, limit=_TRAJECTORY_MAX_CANDIDATES)
    if not events and not documents:
        return (header + "一致する既存Event/Documentは見つからなかった。無理にTrajectoryを"
                "構成せず、現在のSnapshotの範囲で判断すること。")
    lines = []
    for e in events[:_TRAJECTORY_MAX_CANDIDATES]:
        date = e.get("event_date") or e.get("occurred_at") or e.get("created_at") or "date unknown"
        lines.append(f"- [event:{e.get('event_id')}] {date}: {e.get('actor')} {e.get('action')} {e.get('target')} ({e.get('event_type')})")
    for d in documents[:_TRAJECTORY_MAX_CANDIDATES]:
        date = d.get("published_at") or d.get("created_at") or "date unknown"
        lines.append(f"- [document:{d.get('document_id')}] {date}: {d.get('title')}")
    return (header + "Retrieved candidates (most may not be material — select only those "
            "that meet the Trajectory Reasoning criteria):\n" + "\n".join(lines))


_TRAJECTORY_V1_MAX_CANDIDATES = 20
# Section9のgap_type enumのうち、Actor自身の過去（Layer2: Actor Historyのみでの一致）を
# 保持してよいと判断する種別。それ以外のgap_typeしか無い場合、Layer2はActor Keywordのみの
# 一致だけでは不十分として除外する（Scope Protection、ユーザー指示Section11）。
_TRAJECTORY_V1_LAYER2_GAP_TYPES = frozenset({"prior_state", "precedent"})


def _trajectory_v1_relevance_layer(row: dict, columns: list, actor_keywords: list, issue_keywords: list) -> str | None:
    """Situation Trajectory Intelligence v1（ユーザー指示）: Scope Protection。
    Actor Keyword（issue_scope.primary_actors/secondary_actors由来）とIssue Keyword
    （core_issue/relevant_dimensions/evidence_gaps[].question由来）の両方に一致すれば
    'layer1'（Same Issue）、Actor Keywordのみ一致なら'layer2'（Same Actor History）、
    どちらにも一致しなければNone（Reject）を返す。v0.1/v0.2の_run_trajectory_retrievalが
    単純なKeyword OR一致で無関係なCase（例: Hormuz関連文書がTrump-Netanyahuクエリへ混入）
    を拾っていた問題を修正する。"""
    text = " ".join(str(row.get(c) or "") for c in columns).lower()
    actor_hit = any(kw.lower() in text for kw in actor_keywords if kw)
    issue_hit = any(kw.lower() in text for kw in issue_keywords if kw)
    if actor_hit and issue_hit:
        return "layer1"
    if actor_hit:
        return "layer2"
    return None


def _run_trajectory_v1_retrieval(client, trajectory_information_need: dict | None, *,
                                  theme_id: str | None = None) -> str:
    """Situation Trajectory Intelligence v1（ユーザー指示、test-only）: Call1の
    trajectory_information_needがinformation_needed=trueかつevidence_gapsを伴う場合のみ
    呼ばれる（呼び出し元でtrajectory_v1_retrieval_should_run相当のgateを行うこと）。
    既存の_relevant_rows_by_keywords（geo_events/geo_documentsへのILIKE検索）は再利用
    するが、v0.1/v0.2と異なりActor KeywordとIssue Keywordを分離して保持し、Layer1/Layer2
    のScope Protectionでフィルタしてから返す（新規Retrieval Infra・新規DBは追加しない）。
    どれがMaterial Turning Pointかの最終判断はCall2のLLM Reasoningへ委ねる。"""
    if not trajectory_information_need or not trajectory_information_need.get("information_needed"):
        return ""
    issue_scope = trajectory_information_need.get("issue_scope") or {}
    gaps = trajectory_information_need.get("evidence_gaps") or []
    primary_actors = issue_scope.get("primary_actors") or []
    secondary_actors = issue_scope.get("secondary_actors") or []
    actor_keywords = []
    for src in (*primary_actors, *secondary_actors):
        for kw in _extract_query_keywords(src or ""):
            if kw not in actor_keywords:
                actor_keywords.append(kw)
    core_issue = issue_scope.get("core_issue") or ""
    dimensions = issue_scope.get("relevant_dimensions") or []
    issue_keywords = []
    for src in (core_issue, *dimensions, *[g.get("question") or "" for g in gaps]):
        for kw in _extract_query_keywords(src or ""):
            if kw not in issue_keywords:
                issue_keywords.append(kw)
    all_keywords = list(dict.fromkeys(actor_keywords + issue_keywords))
    time_horizon = issue_scope.get("time_horizon") or "unspecified"
    gap_summary = "; ".join(f"[{g.get('gap_type')}] {g.get('question')}" for g in gaps) or "(none)"
    header = (
        "\n\n【TRAJECTORY V1 CONTEXT（Issue-scoped, Evidence-gap-driven Deterministic "
        "Retrieval。Layer1=Actor+Issue両方一致、Layer2=Actor Historyのみ一致（該当"
        "evidence_gapがある場合のみ保持）。どのCandidateがMaterial Turning Pointかの"
        "判断はこのCall2自身が行うこと）】\n"
        f"Issue: {core_issue or '(unspecified)'} / Time horizon: {time_horizon}\n"
        f"Evidence gaps: {gap_summary}\n"
    )
    if not all_keywords:
        return header + "検索可能なKeywordを抽出できなかった。無理にTrajectoryを構成せず、現在のSnapshotの範囲で判断すること。"
    events = _relevant_rows_by_keywords(
        client, "geo_events", "event_id", ["actor", "action", "target", "event_type"], all_keywords,
        theme_id=theme_id, limit=_TRAJECTORY_V1_MAX_CANDIDATES)
    documents = _relevant_rows_by_keywords(
        client, "geo_documents", "document_id", ["title", "extracted_text"], all_keywords,
        theme_id=theme_id, limit=_TRAJECTORY_V1_MAX_CANDIDATES)
    allow_layer2 = any(g.get("gap_type") in _TRAJECTORY_V1_LAYER2_GAP_TYPES for g in gaps)

    kept = []
    rejected = 0
    for e in events:
        layer = _trajectory_v1_relevance_layer(e, ["actor", "action", "target", "event_type"],
                                                actor_keywords, issue_keywords)
        if layer == "layer1" or (layer == "layer2" and allow_layer2):
            kept.append(("event", layer, e))
        else:
            rejected += 1
    for d in documents:
        layer = _trajectory_v1_relevance_layer(d, ["title", "extracted_text"], actor_keywords, issue_keywords)
        if layer == "layer1" or (layer == "layer2" and allow_layer2):
            kept.append(("document", layer, d))
        else:
            rejected += 1
    kept = kept[:_TRAJECTORY_V1_MAX_CANDIDATES]
    if not kept:
        return (header + f"検索した{len(events) + len(documents)}件のCandidateのうち、"
                f"Scope Protection基準（Actor+Issue一致、または該当Gapを伴うActor History"
                f"一致）を満たしたものは無かった（除外{rejected}件）。無理にTrajectoryを"
                "構成せず、現在のSnapshotの範囲で判断すること。")
    lines = []
    for kind, layer, row in kept:
        if kind == "event":
            date = row.get("event_date") or row.get("occurred_at") or row.get("created_at") or "date unknown"
            lines.append(f"- [{layer}][event:{row.get('event_id')}] {date}: "
                         f"{row.get('actor')} {row.get('action')} {row.get('target')} ({row.get('event_type')})")
        else:
            date = row.get("published_at") or row.get("created_at") or "date unknown"
            lines.append(f"- [{layer}][document:{row.get('document_id')}] {date}: {row.get('title')}")
    return (header + "Relevance-filtered candidates (still may not all be material turning "
            "points — apply the Turning Point criteria):\n" + "\n".join(lines))


# ---- Situation Trajectory Intelligence v1.2（ユーザー指示、test-only）: Evidence Request
# 方式。v1のBoolean Gate（trajectory_information_need.information_needed）を廃止し、
# CQASのReasoning Dependencyから直接出たEvidence Requestを、既存Source Packと突き合わせる
# 決定的（LLM呼び出し無し）なEvidence Satisfaction Checkで判定してからRetrievalの要否を
# 決める。「必要か？」を再度LLMへ聞かない（ユーザー指示Section11-13）。----
_TRAJECTORY_V1_2_MAX_REQUESTS = 3
_TRAJECTORY_V1_2_MAX_CANDIDATES = 20
_TRAJECTORY_V1_2_MAX_CONTEXT_ITEMS = 7
_TRAJECTORY_V1_2_RETRIEVAL_PRIORITIES = frozenset({"P1", "P2"})
# Layer2（Actor Keywordのみ一致）を許容するneed_type（v1の_TRAJECTORY_V1_LAYER2_GAP_TYPES
# と同じ考え方——Actor自身の過去の行動パターンを問うRequestに限り、Issue Keyword一致無しの
# Actor Historyヒットを許可する）。
_TRAJECTORY_V1_2_LAYER2_NEED_TYPES = frozenset({"actor_history", "precedent"})
# Existing Evidence Satisfaction Checkの閾値（Section12-13、決定的・Keyword Overlapベース）。
_TRAJECTORY_V1_2_SATISFIED_RATIO = 0.6
_TRAJECTORY_V1_2_PARTIAL_RATIO = 0.25


def _trajectory_v1_2_evidence_satisfaction(request: dict, existing_text: str) -> str:
    """既存Source Pack（Call1へ渡したuser_prompt全文）に対し、request.question +
    expected_evidenceから抽出したKeywordがどれだけ既に含まれているかを見る、決定的な
    Existing Evidence Satisfaction Check（ユーザー指示Section12-13、追加LLM呼び出し無し）。
    「必要か？」というLLMの自己申告には依存しない。"""
    expected = request.get("expected_evidence") or []
    probe_text = " ".join([request.get("question") or "", *expected])
    keywords = _extract_query_keywords(probe_text, max_keywords=12)
    if not keywords:
        return "UNSATISFIED"
    existing_lower = (existing_text or "").lower()
    hits = sum(1 for kw in keywords if kw.lower() in existing_lower)
    ratio = hits / len(keywords)
    if ratio >= _TRAJECTORY_V1_2_SATISFIED_RATIO:
        return "SATISFIED_BY_EXISTING"
    if ratio >= _TRAJECTORY_V1_2_PARTIAL_RATIO:
        return "PARTIALLY_SATISFIED"
    return "UNSATISFIED"


def _trajectory_v1_2_relevance_layer(row: dict, columns: list, actor_keywords: list,
                                      issue_keywords: list, need_type: str) -> str | None:
    """v1の_trajectory_v1_relevance_layerと同じScope Protection（Layer1=Actor+Issue両方
    一致、Layer2=Actor Keywordのみ一致、それ以外はReject）を、Request単位のneed_typeで
    Layer2許容を判定する形へ拡張したもの（ユーザー指示Section10、v0.1/v0.2のUnscoped
    OR-keyword検索によるCross-case Leakage対策を維持する）。"""
    text = " ".join(str(row.get(c) or "") for c in columns).lower()
    actor_hit = any(kw.lower() in text for kw in actor_keywords if kw)
    issue_hit = any(kw.lower() in text for kw in issue_keywords if kw)
    if actor_hit and issue_hit:
        return "layer1"
    if actor_hit and need_type in _TRAJECTORY_V1_2_LAYER2_NEED_TYPES:
        return "layer2"
    return None


def _run_trajectory_v1_2_retrieval(client, evidence_requests: list, existing_text: str, *,
                                    theme_id: str | None = None) -> tuple:
    """trajectory_evidence_requestsを優先度（P1→P2、P3は原則Retrievalしない）とExisting
    Evidence Satisfaction Check（SATISFIED_BY_EXISTINGは検索しない）でフィルタしたうえで、
    Request単位でIssue-scoped Deterministic Retrieval（既存_relevant_rows_by_keywords・
    新規DB/RPC無し）を行う。返り値は(context_text, diagnostics)——diagnosticsはPublic
    Response Schemaへは出さず、execution_metadataへの格納専用（ユーザー指示Section4）。
    どのCandidateがMaterial（NEW_RELEVANT/WITHHELD_RECOVERED等）かの最終判断はCall2の
    LLM Reasoningへ委ねる。"""
    diagnostics = {"requests": [], "retrieval_fired": False, "kept_total": 0}
    if not evidence_requests:
        return "", diagnostics
    requests = evidence_requests[:_TRAJECTORY_V1_2_MAX_REQUESTS]
    ranked = sorted(requests, key=lambda r: {"P1": 0, "P2": 1, "P3": 2}.get(r.get("priority"), 3))

    sections = []
    all_kept = []
    for req in ranked:
        satisfaction = _trajectory_v1_2_evidence_satisfaction(req, existing_text)
        priority = req.get("priority")
        should_retrieve = (priority in _TRAJECTORY_V1_2_RETRIEVAL_PRIORITIES
                            and satisfaction != "SATISFIED_BY_EXISTING")
        req_diag = {
            "request_id": req.get("request_id"), "need_type": req.get("need_type"),
            "priority": priority, "satisfaction": satisfaction,
            "retrieval_attempted": should_retrieve, "kept_count": 0,
            # 診断専用（読み取り専用の記録、判定ロジックには一切使わない）: Request品質の
            # 事後評価（reasoning_dependency/question等の具体性）に必要な原文をそのまま
            # 保持する。Retrieval要否の判定はすでに上のsatisfaction/priorityで確定済み。
            "reasoning_dependency": req.get("reasoning_dependency"),
            "question": req.get("question"),
            "why_it_matters": req.get("why_it_matters"),
            "core_issue": req.get("core_issue"),
            "primary_actors": req.get("primary_actors"),
            "secondary_actors": req.get("secondary_actors"),
            "relevant_dimensions": req.get("relevant_dimensions"),
            "expected_evidence": req.get("expected_evidence"),
        }
        if not should_retrieve:
            diagnostics["requests"].append(req_diag)
            continue

        primary_actors = req.get("primary_actors") or []
        secondary_actors = req.get("secondary_actors") or []
        actor_keywords = []
        for src in (*primary_actors, *secondary_actors):
            for kw in _extract_query_keywords(src or ""):
                if kw not in actor_keywords:
                    actor_keywords.append(kw)
        core_issue = req.get("core_issue") or ""
        dimensions = req.get("relevant_dimensions") or []
        issue_keywords = []
        for src in (core_issue, req.get("question") or "", *dimensions):
            for kw in _extract_query_keywords(src or ""):
                if kw not in issue_keywords:
                    issue_keywords.append(kw)
        all_keywords = list(dict.fromkeys(actor_keywords + issue_keywords))
        if not all_keywords:
            req_diag["kept_count"] = 0
            diagnostics["requests"].append(req_diag)
            continue

        need_type = req.get("need_type")
        events = _relevant_rows_by_keywords(
            client, "geo_events", "event_id", ["actor", "action", "target", "event_type"],
            all_keywords, theme_id=theme_id, limit=_TRAJECTORY_V1_2_MAX_CANDIDATES)
        documents = _relevant_rows_by_keywords(
            client, "geo_documents", "document_id", ["title", "extracted_text"],
            all_keywords, theme_id=theme_id, limit=_TRAJECTORY_V1_2_MAX_CANDIDATES)
        req_kept = []
        for e in events:
            layer = _trajectory_v1_2_relevance_layer(
                e, ["actor", "action", "target", "event_type"], actor_keywords, issue_keywords, need_type)
            if layer:
                req_kept.append(("event", layer, e, req.get("request_id")))
        for d in documents:
            layer = _trajectory_v1_2_relevance_layer(
                d, ["title", "extracted_text"], actor_keywords, issue_keywords, need_type)
            if layer:
                req_kept.append(("document", layer, d, req.get("request_id")))
        req_diag["kept_count"] = len(req_kept)
        diagnostics["requests"].append(req_diag)
        all_kept.extend(req_kept)

    # Section15: Call2へ渡すのは最大_TRAJECTORY_V1_2_MAX_CONTEXT_ITEMS件まで（Layer1優先）。
    all_kept.sort(key=lambda item: 0 if item[1] == "layer1" else 1)
    all_kept = all_kept[:_TRAJECTORY_V1_2_MAX_CONTEXT_ITEMS]
    diagnostics["kept_total"] = len(all_kept)
    diagnostics["retrieval_fired"] = len(all_kept) > 0
    if not all_kept:
        return "", diagnostics

    lines = []
    for kind, layer, row, request_id in all_kept:
        if kind == "event":
            date = row.get("event_date") or row.get("occurred_at") or row.get("created_at") or "date unknown"
            lines.append(f"- [{request_id}][{layer}][event:{row.get('event_id')}] {date}: "
                         f"{row.get('actor')} {row.get('action')} {row.get('target')} ({row.get('event_type')})")
        else:
            date = row.get("published_at") or row.get("created_at") or "date unknown"
            lines.append(f"- [{request_id}][{layer}][document:{row.get('document_id')}] {date}: {row.get('title')}")
    header = ("\n\n【TRAJECTORY V1.2 EVIDENCE（Evidence Request方式、Issue-scoped "
               "Deterministic Retrieval。各行の先頭[request_id]がどのEvidence Requestに"
               "応じた候補かを示す。どれがMaterial Turning Pointかの判断はこのCall2自身が"
               "行うこと）】\n")
    return header + "\n".join(lines), diagnostics


# ---- Expert Context Architecture v1.3（ユーザー指示、test-only）: Hypothesis Evidence
# Requirements + Coverage Check。v1/v1.2の「追加情報が必要か？」という自己申告Gateを
# 廃止し、各Hypothesisが成立するために必要なEvidence Requirementsを必須構造として先に
# 列挙させ、既存Source Packとの充足度を決定的（LLM呼び出し無し）なCoverage Checkで
# 判定してからRetrieval要否を決める。----
_EXPERT_CONTEXT_V1_3_MAX_REQUIREMENTS = 5
_EXPERT_CONTEXT_V1_3_MAX_CANDIDATES = 20
_EXPERT_CONTEXT_V1_3_MAX_CONTEXT_ITEMS = 7
_EXPERT_CONTEXT_V1_3_SATISFIED_RATIO = 0.6
_EXPERT_CONTEXT_V1_3_PARTIAL_RATIO = 0.25
# Section15（ユーザー指示）: dimension→Context Layerの決定的な経路づけ。v1.2の
# actor_historyはLayer2（Trajectory側のActor Historyヒット）として扱っていたが、v1.3
# ではACTOR Contextへ明示的に分離する（Trajectory Retrievalの対象外）。
_EXPERT_CONTEXT_V1_3_DIMENSION_ROUTING = {
    "actor_objective": "ACTOR", "actor_history": "ACTOR", "actor_response_pattern": "ACTOR",
    "relationship_state": "RELATIONSHIP", "relationship_change": "RELATIONSHIP",
    "narrative_state": "MEDIA_PERCEPTION", "audience_perception": "MEDIA_PERCEPTION",
    "source_intent": "MEDIA_PERCEPTION",
    "prior_state": "TRAJECTORY", "turning_point": "TRAJECTORY", "precedent": "TRAJECTORY",
    "constraint_change": "TRAJECTORY", "constraint": "TRAJECTORY",
    "implementation_feasibility": "CURRENT_FOUNDATION", "current_state": "CURRENT_FOUNDATION",
    "current_incentive": "CURRENT_FOUNDATION",
}
# TRAJECTORYへ経路づけられたdimensionのうち、Layer2（Actor Keywordのみ一致）を許容する
# もの（precedent=Actorの過去パターンが主眼のため）。それ以外はLayer1（Actor+Issue両方
# 一致）必須（v1/v1.2と同じScope Protection、Section17）。
_EXPERT_CONTEXT_V1_3_TRAJECTORY_LAYER2_DIMENSIONS = frozenset({"precedent"})


def _expert_context_v1_3_source_lines(existing_text: str) -> list:
    """既存Source Pack（「- [FACT] ...」「- [CONTEXT] ...」形式、または任意の改行区切り
    テキスト）を行単位に分解し、Coverage Checkのevidence_refsとして参照できるようにする
    （決定的、LLM呼び出し無し）。"""
    lines = []
    for i, line in enumerate((existing_text or "").splitlines()):
        line = line.strip()
        if line:
            lines.append((f"L{i}", line))
    return lines


def _expert_context_v1_3_coverage_check(item: dict, source_lines: list, existing_lower: str) -> tuple:
    """RequirementまたはDiscriminatorを既存Source Packと突き合わせる、決定的な
    Coverage Check（ユーザー指示Section11-13、追加LLM呼び出し無し）。「必要か？」という
    LLMの自己申告には依存しない。返り値は(status, evidence_refs)。
    CONTRADICTEDはKeyword Overlapのみでは意味的な矛盾を検出できないため、この決定的
    実装では出さない（Report側で明記するスコープ上の制約）。"""
    probe_parts = [item.get("question") or ""]
    probe_parts.append(item.get("supporting_evidence") or "")
    probe_parts.append(item.get("falsifying_evidence") or "")
    probe_parts.append(item.get("required_evidence") or "")
    keywords = _extract_query_keywords(" ".join(probe_parts), max_keywords=12)
    if not keywords:
        return "MISSING", []
    hits_overall = sum(1 for kw in keywords if kw.lower() in existing_lower)
    ratio = hits_overall / len(keywords)
    evidence_refs = [ref for ref, line in source_lines
                     if any(kw.lower() in line.lower() for kw in keywords)][:5]
    if ratio >= _EXPERT_CONTEXT_V1_3_SATISFIED_RATIO:
        status = "SATISFIED"
    elif ratio >= _EXPERT_CONTEXT_V1_3_PARTIAL_RATIO:
        status = "PARTIALLY_SATISFIED"
    else:
        status = "MISSING"
    # Section12（ユーザー指示）: 該当するevidence_refsが無いのにSATISFIED/PARTIALLY_
    # SATISFIEDとするのは無効——「モデルが十分だと感じた」だけの充足判定を許さない。
    if status != "MISSING" and not evidence_refs:
        status = "MISSING"
    return status, evidence_refs


def _expert_context_v1_3_relevance_layer(row: dict, columns: list, actor_keywords: list,
                                          issue_keywords: list, dimension: str) -> str | None:
    """v1/v1.2と同じScope Protection（Layer1=Actor+Issue両方一致、Layer2=Actor Keyword
    のみ一致、それ以外はReject）。Layer2許容はdimension単位で判定する。"""
    text = " ".join(str(row.get(c) or "") for c in columns).lower()
    actor_hit = any(kw.lower() in text for kw in actor_keywords if kw)
    issue_hit = any(kw.lower() in text for kw in issue_keywords if kw)
    if actor_hit and issue_hit:
        return "layer1"
    if actor_hit and dimension in _EXPERT_CONTEXT_V1_3_TRAJECTORY_LAYER2_DIMENSIONS:
        return "layer2"
    return None


def _expert_context_v1_3_row_date(kind: str, row: dict) -> str | None:
    """Retrieval候補の日付をISO文字列（先頭10文字がYYYY-MM-DD）として取り出す。
    day-levelの語彙的比較で十分なため、datetimeパースはしない。"""
    if kind == "event":
        date = row.get("event_date") or row.get("occurred_at") or row.get("created_at")
    else:
        date = row.get("published_at") or row.get("created_at")
    if not date:
        return None
    return str(date)[:10]


def _run_expert_context_v1_3_retrieval(client, eligible_items: list, *,
                                        theme_id: str | None = None,
                                        as_of_date: str | None = None) -> tuple:
    """TRAJECTORYへ経路づけられ、かつCRITICAL(または discriminator)でCoverageが
    PARTIALLY_SATISFIED/MISSINGだったRequirement/Discriminatorについてのみ、v1/v1.2と
    同じ_relevant_rows_by_keywords（新規DB/RPC無し）でIssue-scoped Retrievalする。
    as_of_date（"YYYY-MM-DD"、省略可・既定None=フィルタなし）を渡すと、その日付より後の
    候補を除外する（Temporal Integrity Guard、ユーザー指示: Look-ahead leakage対策。
    Frozen Run1実験で、E01のas_of=2026-02-18より後の2026年夏の実データが無条件で
    取得され、Case当時には存在しなかった情報でSelectionが動いていたことが判明した
    ——これは「Retrievalの成功」ではなく「未来情報の混入」であり、区別する必要がある）。
    日付が取れない候補は、以前と同じくWithout-filterとして許容する（既存データの多くは
    日付が付与されているため、この既定挙動を厳しくしすぎない）。
    返り値は(context_text, kept_total)。どれがMaterialかの最終判断はCall2のLLM
    Reasoningへ委ねる。"""
    if not eligible_items:
        return "", 0
    items = eligible_items[:_EXPERT_CONTEXT_V1_3_MAX_REQUIREMENTS]
    all_kept = []
    rejected_future = 0
    for item in items:
        req_id = item.get("requirement_id") or ("DISC-" + "+".join(item.get("discriminator_for") or []))
        primary_actors = item.get("primary_actors") or []
        secondary_actors = item.get("secondary_actors") or []
        actor_keywords = []
        for src in (*primary_actors, *secondary_actors):
            for kw in _extract_query_keywords(src or ""):
                if kw not in actor_keywords:
                    actor_keywords.append(kw)
        core_issue = item.get("core_issue") or ""
        issue_keywords = []
        for src in (core_issue, item.get("question") or ""):
            for kw in _extract_query_keywords(src or ""):
                if kw not in issue_keywords:
                    issue_keywords.append(kw)
        all_keywords = list(dict.fromkeys(actor_keywords + issue_keywords))
        if not all_keywords:
            continue
        dimension = item.get("dimension")
        events = _relevant_rows_by_keywords(
            client, "geo_events", "event_id", ["actor", "action", "target", "event_type"],
            all_keywords, theme_id=theme_id, limit=_EXPERT_CONTEXT_V1_3_MAX_CANDIDATES)
        documents = _relevant_rows_by_keywords(
            client, "geo_documents", "document_id", ["title", "extracted_text"],
            all_keywords, theme_id=theme_id, limit=_EXPERT_CONTEXT_V1_3_MAX_CANDIDATES)
        for e in events:
            layer = _expert_context_v1_3_relevance_layer(
                e, ["actor", "action", "target", "event_type"], actor_keywords, issue_keywords, dimension)
            if not layer:
                continue
            if as_of_date and (date_str := _expert_context_v1_3_row_date("event", e)) and date_str > as_of_date:
                rejected_future += 1
                continue
            all_kept.append(("event", layer, e, req_id))
        for d in documents:
            layer = _expert_context_v1_3_relevance_layer(
                d, ["title", "extracted_text"], actor_keywords, issue_keywords, dimension)
            if not layer:
                continue
            if as_of_date and (date_str := _expert_context_v1_3_row_date("document", d)) and date_str > as_of_date:
                rejected_future += 1
                continue
            all_kept.append(("document", layer, d, req_id))

    all_kept.sort(key=lambda x: 0 if x[1] == "layer1" else 1)
    all_kept = all_kept[:_EXPERT_CONTEXT_V1_3_MAX_CONTEXT_ITEMS]
    if not all_kept:
        return "", 0
    lines = []
    for kind, layer, row, req_id in all_kept:
        if kind == "event":
            date = row.get("event_date") or row.get("occurred_at") or row.get("created_at") or "date unknown"
            lines.append(f"- [{req_id}][{layer}][event:{row.get('event_id')}] {date}: "
                         f"{row.get('actor')} {row.get('action')} {row.get('target')} ({row.get('event_type')})")
        else:
            date = row.get("published_at") or row.get("created_at") or "date unknown"
            lines.append(f"- [{req_id}][{layer}][document:{row.get('document_id')}] {date}: {row.get('title')}")
    as_of_note = f"As-of cutoff: {as_of_date}（これより後の日付の候補は除外済み、除外{rejected_future}件）\n" if as_of_date else ""
    header = ("\n\n【EXPERT CONTEXT V1.3 EVIDENCE（Hypothesis Evidence Requirement/"
               "Discriminator方式、Issue-scoped Deterministic Retrieval。各行先頭の"
               "[requirement_id]がどのRequirement/Discriminatorに応じた候補かを示す。"
               "どれがMaterial Turning Pointかの判断はこのCall2自身が行うこと）】\n" + as_of_note)
    return header + "\n".join(lines), len(all_kept)


def _expert_context_v1_3_process(stage1_data: dict, existing_text: str, client, *,
                                  retrieval_enabled: bool, theme_id: str | None = None,
                                  as_of_date: str | None = None) -> tuple:
    """hypothesis_evidence_requirements/hypothesis_discriminatorsに対しCoverage Check
    （決定的）を行い、Context Layer（ACTOR/RELATIONSHIP/MEDIA_PERCEPTION/TRAJECTORY/
    CURRENT_FOUNDATION）へ経路づけたうえで、TRAJECTORYのみ実際にRetrievalする（Section
    15・46: Actor/Media/Relationshipはdetect/classify/reportに留め、Retrievalは実行
    しない——generalized Orchestratorを先取り実装しない）。返り値は(context_text,
    diagnostics)。diagnosticsはPublic Response Schemaには出さず、execution_metadata
    へのみ格納する。"""
    groups = stage1_data.get("hypothesis_evidence_requirements") or []
    discriminators = stage1_data.get("hypothesis_discriminators") or []
    source_lines = _expert_context_v1_3_source_lines(existing_text)
    existing_lower = (existing_text or "").lower()

    diag_requirements = []
    diag_discriminators = []
    trajectory_eligible = []

    for group in groups:
        hyp_id = group.get("hypothesis_id")
        for req in (group.get("evidence_requirements") or []):
            status, refs = _expert_context_v1_3_coverage_check(req, source_lines, existing_lower)
            layer_route = _EXPERT_CONTEXT_V1_3_DIMENSION_ROUTING.get(req.get("dimension"), "CURRENT_FOUNDATION")
            importance = req.get("importance")
            eligible = importance == "CRITICAL" and status in ("PARTIALLY_SATISFIED", "MISSING")
            diag_requirements.append({
                "hypothesis_id": hyp_id, "requirement_id": req.get("requirement_id"),
                "dimension": req.get("dimension"), "importance": importance,
                "coverage_status": status, "evidence_refs": refs,
                "context_layer": layer_route, "retrieval_eligible": eligible,
                "question": req.get("question"), "supporting_evidence": req.get("supporting_evidence"),
                "falsifying_evidence": req.get("falsifying_evidence"),
            })
            if eligible and layer_route == "TRAJECTORY":
                trajectory_eligible.append(req)

    for disc in discriminators:
        status, refs = _expert_context_v1_3_coverage_check(disc, source_lines, existing_lower)
        layer_route = _EXPERT_CONTEXT_V1_3_DIMENSION_ROUTING.get(disc.get("dimension"), "CURRENT_FOUNDATION")
        # Discriminatorはimportance欄を持たず、Section10により常に最優先（CRITICAL相当）
        # として扱う。
        eligible = status in ("PARTIALLY_SATISFIED", "MISSING")
        diag_discriminators.append({
            "discriminator_for": disc.get("discriminator_for"), "dimension": disc.get("dimension"),
            "coverage_status": status, "evidence_refs": refs, "context_layer": layer_route,
            "retrieval_eligible": eligible, "question": disc.get("question"),
            "required_evidence": disc.get("required_evidence"),
        })
        if eligible and layer_route == "TRAJECTORY":
            trajectory_eligible.append(disc)

    context_routing_counts = {"ACTOR": 0, "RELATIONSHIP": 0, "MEDIA_PERCEPTION": 0,
                               "TRAJECTORY": 0, "CURRENT_FOUNDATION": 0}
    for entry in diag_requirements + diag_discriminators:
        if entry["retrieval_eligible"]:
            context_routing_counts[entry["context_layer"]] += 1

    context_text = ""
    kept_total = 0
    if retrieval_enabled and trajectory_eligible:
        context_text, kept_total = _run_expert_context_v1_3_retrieval(
            client, trajectory_eligible, theme_id=theme_id, as_of_date=as_of_date)

    diagnostics = {
        "requirements": diag_requirements,
        "discriminators": diag_discriminators,
        "context_routing_counts": context_routing_counts,
        "trajectory_retrieval_eligible_count": len(trajectory_eligible),
        "retrieval_fired": kept_total > 0,
        "kept_total": kept_total,
    }
    return context_text, diagnostics


_MODEL_ACTIVATION_MAX_REQUESTS = 4


def _resolve_activated_model_cards(model_activation_requests: list | None) -> str:
    """Explanatory Model Activation v0.3（ユーザー指示）: Call1が挙げたmodel_activation_
    requestsについて、Local Library Lookupのみ(追加LLM呼び出し無し)でFull Model Cardを
    解決し、Call2側Promptへ差し込むテキストを返す。Requestが無ければ空文字列（無理に
    埋めない）。Library未登録のmodel_id(LLMの誤生成)は静かにスキップする(fabricationしない)。
    Runtime Cardにはprovenance/supporting_episode_idsを含めない(Gold Leakage防止、
    geo_explanatory_model_library.get_full_card_text参照)。"""
    if not model_activation_requests:
        return ""
    requests = model_activation_requests[:_MODEL_ACTIVATION_MAX_REQUESTS]
    lines = []
    for req in requests:
        model_id = req.get("model_id")
        card_text = geo_explanatory_model_library.get_full_card_text(model_id)
        if card_text is None:
            continue
        cue_ref = req.get("cue_ref") or ""
        lines.append(f"(Activated for cue: {cue_ref})\n{card_text}")
    if not lines:
        return ""
    return (
        "\n\n【Activated Explanatory Model Cards（Call1が要求したModelをLocal Libraryから"
        "取得した結果。LLM呼び出しは追加していない）】\n"
        "各Cardを今回の局面へ具体化(Instantiate)し、Actor/Mechanism/Causal Pathまで"
        "踏み込んだCandidateへ展開すること。抽象的な言及だけで終わらせないこと。"
        "Cardが今回のDisqualifiersに該当する場合は使わないこと。\n\n" + "\n\n".join(lines)
    )


# ---- Foundation Context Builder v1（ユーザー指示）: Situation Trajectoryを、Call1より
# 前のFoundation Context Layerとして実装する。「Foundation Context Retrieval
# Validation v1」で実証済み（E01 Fact A: Ablate 0/6→Oracle 5/6、E01-Late: Ablate 0/6→
# Real Retrieval 5/6+1 PARTIAL）の設計をそのまま反映する。新規LLM呼び出しは追加しない
# （既存の2-call構造=Stage1+Stage2を維持）。Retrieval自体は既存の_relevant_rows_by_
# keywords（新規DB/RPC無し）を再利用する。----
_FOUNDATION_CONTEXT_MAX_ITEMS = 7
_FOUNDATION_CONTEXT_MAX_CHARS = 3000
_FOUNDATION_CONTEXT_MAX_ITEM_CHARS = 500
_FOUNDATION_CONTEXT_MAX_CANDIDATES = 30
# 合成テストコーパス（GEO-SUS-*等のSustainability Test Fixture）を検出するマーカー。
# 「Foundation Context Retrieval Validation v1」でeba1d367（紅海・ホルムズ海峡リスク
# ブリーフ）がこの文言を含むことを確認済み。Corpus Integrity P0（2026-09-22）で、
# このmarker集合が_relevant_rows_by_keywords側（_SYNTHETIC_TEST_CORPUS_MARKERS）と
# 統合された——マーカー定義の重複・乖離を避けるため、ここでは共通定数を参照する。
_FOUNDATION_CONTEXT_SYNTHETIC_MARKERS = _SYNTHETIC_TEST_CORPUS_MARKERS


def _foundation_context_is_stub(title: str, text: str) -> bool:
    """本文が無い/タイトルの繰り返しに過ぎないスタブ文書を検出する（決定的）。
    Validation v1のE02/E03監査で、published_at=None・extracted_textがtitleと同一の
    スタブ（Netanyahu関連文書等）を確認済み——これらはFoundation Contextとして使えない。"""
    text = (text or "").strip()
    title = (title or "").strip()
    if not text:
        return True
    if text == title:
        return True
    if len(text) < 30:
        return True
    return False


# Post-Freeze Integrity Audit R2 Hotfix(ユーザー指示、2026-09-22): Evidence Integrity
# Guard（geo_expert_quality_guard.cap_evidence_from_stub_documents/enforce_temporal_
# grounding_in_claims）が必要とする、Source Pack内document_idのstub判定・実本文
# テキストを1回のDB問い合わせで決定的に算出する。_foundation_context_is_stubと
# 同じ判定基準を、Foundation Context固有ではなくSource Pack全体（通常Retrieval分＋
# extra_document_ids分）へ適用する。
def _stub_document_ids_and_texts(client, document_ids: list) -> tuple:
    if not document_ids:
        return set(), {}
    rows = client.select("geo_documents", {
        "select": "document_id,title,extracted_text", "document_id": f"in.({','.join(document_ids)})"})
    stub_ids = set()
    texts = {}
    for r in rows:
        did = r.get("document_id")
        title = r.get("title") or ""
        text = r.get("extracted_text") or ""
        texts[did] = text
        if _foundation_context_is_stub(title, text):
            stub_ids.add(did)
    return stub_ids, texts


def _foundation_context_is_synthetic(text: str) -> bool:
    upper = (text or "").upper()
    return any(marker.upper() in upper for marker in _FOUNDATION_CONTEXT_SYNTHETIC_MARKERS)


_FOUNDATION_CONTEXT_REDUNDANT_RATIO = 0.6


def _foundation_context_is_redundant(candidate_text: str, existing_lower: str) -> bool:
    """候補文書の内容が、既にquery_text（Call1へ渡す予定の依頼内容本体）にほぼ含まれて
    いるかを決定的に判定する（Acceptance Test発見: 単純な日付昇順+件数上限だけでは、
    query_textに既にある内容と重複する候補が上限を専有し、本当に不足している後続の
    Evidenceがcapの外に押し出されてしまう——「既知の情報を繰り返すのではなく、不足して
    いるものだけを補う」という設計原則に反するため、事前に除外する）。"""
    keywords = _extract_query_keywords(candidate_text or "", max_keywords=12)
    if not keywords:
        return False
    hits = sum(1 for kw in keywords if kw.lower() in existing_lower)
    return (hits / len(keywords)) >= _FOUNDATION_CONTEXT_REDUNDANT_RATIO


def _foundation_context_relevance_score(text: str, issue_keywords: list) -> int:
    """候補文書がissue_keywordsのうち何種類を含むかを数える。Layer1判定自体は
    いずれか1件の一致で通過する（v1ではactor/issueを分離していないため）ため、
    "China"のような広範な単発語だけで一致した無関係文書と、複数のKeywordに
    一致する本来の主題の文書を、この件数で区別する（build_foundation_context
    でのみ使用、共有のLayer判定関数自体は変更しない）。"""
    lower = (text or "").lower()
    return sum(1 for kw in issue_keywords if kw and kw.lower() in lower)


def build_foundation_context(client, query_text: str, *, theme_id: str | None = None,
                              as_of_date: str | None = None,
                              already_known_text: str | None = None) -> tuple:
    """Situation Trajectoryを、Actor/Media Intelligenceと同じ位置づけでCall1より前に
    収集する（Foundation Need: prior_state/precedent/operating_pattern/
    material_turning_point/structural_constraint——v1ではこれらをLayer1一致という
    単一のScope Protectionで一括して扱う。dimension別の細分化は将来の拡張候補）。
    as_of_date（"YYYY-MM-DD"、省略時はUTC本日日付）より後の候補は除外する
    （Temporal Contamination対策、GEO_EXPERT_DEBUG_STAGE1_DUMP_PATHとは無関係の本番
    経路）。合成テストコーパス・スタブ文書（本文が無い/titleと同一）も除外する。
    Layer2（Actor Keywordのみ一致、Issue不一致）はv1では採用しない——Validation v1の
    手動監査で、Layer2がHormuz局面にIran国内政治のような無関係文書を混入させる例を
    確認したため、v1は保守的にLayer1のみとする（Section「余計なことをしない」）。
    返り値は(context_text, diagnostics)。diagnosticsはPublic Response Schemaには出さず、
    execution_metadataへのみ格納する。
    already_known_text: 冗長判定（rejected_redundant）の比較対象。呼び出し時点で既に
    Call1へ渡す予定のsource_pack本文（BASE_FACTS等）を渡す——省略時はquery_textのみとの
    比較になり、query_textに含まれないBASE_FACTSとの重複を見逃す（Acceptance Test発見）。"""
    if as_of_date is None:
        import datetime
        as_of_date = datetime.datetime.utcnow().date().isoformat()

    actor_keywords = []
    issue_keywords = []
    for kw in _extract_query_keywords(query_text or ""):
        # Foundation Need抽出はCall1が無い時点での決定的処理のため、Actor/Issueを
        # 分離せず同一のKeyword集合をLayer1判定（本文内でのAND一致）に使う。
        if kw not in issue_keywords:
            issue_keywords.append(kw)
    actor_keywords = issue_keywords  # v1では分離しない（将来、Actor Intelligence連携時に分離）

    diagnostics = {"as_of_date": as_of_date, "keywords": issue_keywords,
                   "kept_total": 0, "rejected_future": 0, "rejected_stub_or_synthetic": 0,
                   "rejected_no_layer1": 0, "rejected_redundant": 0}
    if not issue_keywords:
        return "", diagnostics

    existing_lower = (already_known_text if already_known_text is not None else query_text or "").lower()
    events = _relevant_rows_by_keywords(
        client, "geo_events", "event_id", ["actor", "action", "target", "event_type"],
        issue_keywords, theme_id=theme_id, limit=_FOUNDATION_CONTEXT_MAX_CANDIDATES)
    documents = _relevant_rows_by_keywords(
        client, "geo_documents", "document_id", ["title", "extracted_text"],
        issue_keywords, theme_id=theme_id, limit=_FOUNDATION_CONTEXT_MAX_CANDIDATES)

    kept = []
    for e in events:
        layer = _expert_context_v1_3_relevance_layer(
            e, ["actor", "action", "target", "event_type"], actor_keywords, issue_keywords,
            "prior_state")
        if layer != "layer1":
            diagnostics["rejected_no_layer1"] += 1
            continue
        date_str = _expert_context_v1_3_row_date("event", e)
        if date_str and date_str > as_of_date:
            diagnostics["rejected_future"] += 1
            continue
        event_text = f"{e.get('actor')} {e.get('action')} {e.get('target')}"
        if _foundation_context_is_redundant(event_text, existing_lower):
            diagnostics["rejected_redundant"] += 1
            continue
        score = _foundation_context_relevance_score(event_text, issue_keywords)
        kept.append(("event", e, date_str, score))
    for d in documents:
        title = d.get("title") or ""
        text = d.get("extracted_text") or ""
        if _foundation_context_is_synthetic(text) or _foundation_context_is_stub(title, text):
            diagnostics["rejected_stub_or_synthetic"] += 1
            continue
        layer = _expert_context_v1_3_relevance_layer(
            d, ["title", "extracted_text"], actor_keywords, issue_keywords, "prior_state")
        if layer != "layer1":
            diagnostics["rejected_no_layer1"] += 1
            continue
        date_str = _expert_context_v1_3_row_date("document", d)
        if date_str and date_str > as_of_date:
            diagnostics["rejected_future"] += 1
            continue
        if _foundation_context_is_redundant(text, existing_lower):
            diagnostics["rejected_redundant"] += 1
            continue
        score = _foundation_context_relevance_score(f"{title} {text}", issue_keywords)
        kept.append(("document", d, date_str, score))

    # query_textに既に実質的に含まれている候補（rejected_redundant）を先に除いてから、
    # 残った「本当に不足しているもの」を「Keyword一致数（relevance）優先→date昇順」で
    # capする。Layer1判定はissue_keywordsのいずれか1件でも一致すれば通過するため
    # （actor/issueをv1では分離していない）、"China"のような単発の広範な語だけで一致した
    # 無関係文書（例: 半導体輸出管理の記事）が、date昇順だけの並べ替えでは、より多くの
    # Keywordに一致する本来の主題（例: 海上活動・PLA・vessels）の文書より先に採用されて
    # しまう問題をTaiwan Blockade Case実Retrievalで発見したため、relevanceを主キーにする。
    kept.sort(key=lambda x: (-x[3], x[2] or ""))
    kept = kept[:_FOUNDATION_CONTEXT_MAX_ITEMS]
    diagnostics["kept_total"] = len(kept)
    if not kept:
        return "", diagnostics

    lines = []
    total_chars = 0
    for kind, row, date_str, _score in kept:
        if kind == "event":
            ref = f"event:{row.get('event_id')}"
            body = f"{row.get('actor')} {row.get('action')} {row.get('target')} ({row.get('event_type')})"
            source = None
        else:
            ref = f"document:{row.get('document_id')}"
            body = row.get("extracted_text") or row.get("title") or ""
            # Evidence Cardは要約であり全文転記ではない（ユーザー指示: Evidence Card
            # のToken量上限もGate）。実Corpusのextracted_textは数千〜数万字に及ぶことが
            # あり（Hormuz実験の手書き短文要約とは異なる）、1件で_FOUNDATION_CONTEXT_
            # MAX_CHARSを専有してしまい、本来7件まで許容すべき他のEvidenceを cap外へ
            # 押し出してしまう不具合を確認した（Taiwan Blockade Case、実Retrieval時に
            # 発見）。1件あたりの上限を設け、超える場合は先頭のみを残す。
            if len(body) > _FOUNDATION_CONTEXT_MAX_ITEM_CHARS:
                body = body[:_FOUNDATION_CONTEXT_MAX_ITEM_CHARS].rstrip() + "…"
            source = row.get("source_url") or row.get("url") or row.get("source")
        line = f"- [{ref}] {date_str or 'date unknown'}" + (f" ({source})" if source else "") + f": {body}"
        if total_chars + len(line) > _FOUNDATION_CONTEXT_MAX_CHARS:
            break
        lines.append(line)
        total_chars += len(line)
    diagnostics["kept_total"] = len(lines)
    if not lines:
        return "", diagnostics
    header = ("\n\n【FOUNDATION CONTEXT（Situation Trajectory、Call1前・決定的Retrieval。"
              f"as_of={as_of_date}より後の情報・合成テストデータ・本文の無いスタブ文書は"
              "除外済み。この局面を理解する上での経緯・先例・行動パターン・構造的制約として"
              "扱い、鵜呑みにせず今回の分析へ関連する範囲でのみ用いること）】\n")
    return header + "\n".join(lines), diagnostics


def build_source_pack(client, theme_id: str, query_text: str,
                       event_limit: int = 15, document_limit: int = 10,
                       extra_document_ids: list = None,
                       include_external_signals: bool = False,
                       include_external_events: bool = False) -> dict:
    """テーマ配下の検証済み/未検証Event・Evidence・直近文書・指標観測値を収集し、
    LLMへ渡すための本文(text)と、DB保存用のid一覧を組み立てる。

    query_textから抽出したKeywordに部分一致する行のみを候補とする（該当が無ければ空を返す）。
    theme_id指定時はさらにtheme_idで絞り込む。Keywordが1件も抽出できない場合も空を返す
    （「関連文書が無ければ無理に埋めない」というEvidence Discipline思想を、Source Pack
    自体の構築段階から一貫させる）。

    extra_document_ids指定時は、そのdocument_idを常にkeyword一致判定と無関係に含める
    （Phase R1 Chunk4: External Researchが直後に永続化したdocumentをid指定で確実に
    拾うため。日本語クエリから抽出したKeywordは、English中心の検索結果title/
    extracted_textとILIKEで一致しないことがある——keyword抽出ロジック自体は既存の
    国内crawler文書向けのまま変更せず、Research Cycleが返した既知のdocument_idだけを
    素通しで追加する）。

    include_external_signals=True時のみ、Geopolitical News DB（geo_external_signal_
    retrieval_service.search_external_signals）から直近のExternal Signal（GDELT等、
    source_role=discovery/evidence_status=unverified）を取得し独立セクションとして加える。
    document_ids/evidence_ids（Human Review済みEvidence）とは混在させず、常にDiscovery/
    未検証であることが分かる形でSource Packへ含める。呼び出し判定（本当に取得すべきか）は
    呼び出し元がgeo_external_signal_retrieval_service.needs_external_signal_lookupで
    行い、本関数はTrueを渡されたら常に取得する（Gate自体はここに実装しない）。"""
    theme = None
    if theme_id:
        themes = client.select("geo_themes", {"select": "*", "theme_id": f"eq.{theme_id}", "limit": "1"})
        theme = themes[0] if themes else None

    keywords = _extract_query_keywords(query_text)

    events = []
    if keywords:
        events = _relevant_rows_by_keywords(
            client, "geo_events", "event_id", ["actor", "action", "target", "event_type"], keywords,
            theme_id=theme_id, limit=event_limit)
    # Phase 10-R: Strong Anchor一致を優先し、同順位内は既存通り新しい順にする。
    # _strong_anchor_matchはranking専用の内部フラグなので、キー計算後に取り除く
    # （Source Pack本文・DB保存へは出さない）。
    for e in events:
        e["_sort_strong_anchor"] = e.pop("_strong_anchor_match", False)
    events.sort(key=lambda e: (e["_sort_strong_anchor"], e.get("created_at") or ""), reverse=True)
    for e in events:
        e.pop("_sort_strong_anchor", None)
    events = events[:event_limit]

    event_ids = [e["event_id"] for e in events]
    evidence = []
    if event_ids:
        evidence = client.select("geo_evidence", {
            "select": "*", "event_id": f"in.({','.join(event_ids)})",
        })
    evidence_by_event = {}
    for ev in evidence:
        evidence_by_event.setdefault(ev["event_id"], []).append(ev)

    documents = []
    if keywords:
        documents = _relevant_rows_by_keywords(
            client, "geo_documents", "document_id", ["title", "extracted_text"], keywords,
            theme_id=theme_id, limit=document_limit)
    # Phase 10-R: Strong Anchor一致を優先し、同順位内は既存通り新しい順にする。
    for d in documents:
        d["_sort_strong_anchor"] = d.pop("_strong_anchor_match", False)
    documents.sort(key=lambda d: (d["_sort_strong_anchor"], d.get("fetched_at") or ""), reverse=True)
    for d in documents:
        d.pop("_sort_strong_anchor", None)
    documents = documents[:document_limit]
    if extra_document_ids:
        known_ids = {d["document_id"] for d in documents}
        missing_ids = [d_id for d_id in extra_document_ids if d_id not in known_ids]
        if missing_ids:
            forced = client.select("geo_documents", {
                "select": "*", "document_id": f"in.({','.join(missing_ids)})",
            })
            documents.extend(forced)

    external_signals = []
    if include_external_signals:
        import geo_external_signal_retrieval_service
        external_signals = geo_external_signal_retrieval_service.search_external_signals(
            client, query_text, theme_id=theme_id, limit=5)

    external_events = []
    if include_external_events:
        import geo_external_event_service
        external_events = geo_external_event_service.search_recent_events_for_theme(
            client, theme_id=theme_id, limit=10)

    observation_snapshot = []
    if theme_id:
        indicators = client.select("geo_indicator_definitions", {
            "select": "*", "theme_id": f"eq.{theme_id}", "mvp_enabled": "eq.true",
        })
        for indicator in indicators:
            obs = client.select("geo_observations", {
                "select": "*", "indicator_id": f"eq.{indicator['indicator_id']}",
                "order": "as_of.desc", "limit": "1",
            })
            if obs:
                observation_snapshot.append({
                    "indicator_code": indicator["indicator_code"], "name": indicator["name"], **obs[0],
                })

    lines = []
    if theme:
        lines.append(f"監視テーマ: {theme['name_ja']}")

    if observation_snapshot:
        lines.append("\n直近の指標観測値（既に計算済みの値。再計算せずそのまま引用すること）:")
        for o in observation_snapshot:
            if o.get("insufficient_data"):
                lines.append(f"- {o['indicator_code']}({o['name']}): データ不足")
            else:
                value = o.get("qualitative_label") or o.get("raw_value")
                mock_note = "（モックデータ）" if o.get("is_mock") else ""
                lines.append(f"- {o['indicator_code']}({o['name']}): {value}{mock_note}")

    if events:
        lines.append("\n直近のEvent（[verified]=検証済み/[unverified]=未検証、未検証は慎重に扱うこと）:")
        for e in events:
            status = "verified" if e.get("verified") else "unverified"
            quotes = [ev.get("quote") for ev in evidence_by_event.get(e["event_id"], []) if ev.get("quote")]
            line = f"- [{status}] {e.get('actor') or '?'} が {e.get('action') or e.get('event_type') or '?'}"
            if e.get("start_at"):
                line += f"（{e['start_at']}）"
            if quotes:
                line += f" 根拠引用:「{quotes[0]}」"
            lines.append(line)

    if documents:
        source_names = _source_names_by_id(client, [d.get("source_id") for d in documents])
        lines.append(
            "\n参照文書一覧（[document_id: ...]をCurrent Evidenceとして引用する際のIDとして"
            "使うこと。タイトル文字列を代わりに引用しない。document_idはEntity/Actorの"
            "entity_idとは別のIDであり、entity_idとして流用しないこと）:")
        for d in documents:
            mock_note = "（モック）" if d.get("is_mock") else ""
            source_name = source_names.get(d.get("source_id"))
            lines.append(f"- [document_id: {d['document_id']}] {d.get('title') or d['document_id']}{mock_note}")
            meta_parts = []
            if source_name:
                meta_parts.append(f"出典: {source_name}")
            if d.get("published_at"):
                meta_parts.append(f"公開日時: {d['published_at']}")
            if meta_parts:
                lines.append(f"  {' / '.join(meta_parts)}")
            excerpt = _excerpt_around_keywords(d.get("extracted_text") or "", keywords)
            if excerpt:
                lines.append(f"  抜粋: {' '.join(excerpt.split())}")

    if external_signals:
        lines.append(
            "\n【External Signals（Geopolitical News DB、discovery/unverified）】"
            "以下は外部ニュース収集からの未検証シグナルであり、Human Reviewを経たEvidence/"
            "Knowledgeとは異なる。事実確定には使わず、あくまで最新の報道動向の参考情報として"
            "扱うこと（Foundation/Actor Core Profile/Precedent等を自動更新する根拠にしない）:")
        for s in external_signals:
            source = s.get("source_name") or s.get("source_domain") or "不明な情報源"
            country = f"（{s['source_country']}）" if s.get("source_country") else ""
            published = s.get("published_at") or "日時不明"
            lines.append(
                f"- [external_signal_id: {s['external_signal_id']}] {s.get('title') or '(無題)'} "
                f"— {source}{country}, {published}")

    if external_events:
        lines.append(
            "\n【External Events（GDELT構造化Event、automated event extraction/unverified）】"
            "以下はGDELTが自動抽出したEvent（誰が誰に対して何をしたか）であり、人間のReviewを"
            "経ていない未検証のObservationである。事実確定には使わず参考情報として扱うこと:")
        for ev in external_events:
            actor1 = ev.get("actor1_name") or "不明"
            actor2 = ev.get("actor2_name") or "不明"
            date = ev.get("event_date") or "日時不明"
            lines.append(
                f"- [external_event_id: {ev['external_event_id']}] {actor1} → {actor2} "
                f"（event_code: {ev.get('event_code')}, {date}）")

    return {
        "theme": theme,
        "text": "\n".join(lines) if lines else "（参照できる情報がありません）",
        "document_ids": [d["document_id"] for d in documents],
        "event_ids": event_ids,
        "evidence_ids": [e["evidence_id"] for e in evidence],
        "observation_snapshot": observation_snapshot,
        "evidence_source_types": _evidence_source_types(client, evidence, documents),
        "external_signals": external_signals,
        "external_events": external_events,
    }


def _build_system_prompt(mode: str, knowledge_block: str, profile: str = "standard",
                          stage: str = "explore") -> str:
    guidance = geo_expert_schema.MODE_GUIDANCE.get(mode, "")
    # Expert Reasoning Core v0.1（ユーザー指示）: Baseline OFF/ON比較の為、MODE_GUIDANCEの
    # 静的連結ではなくここで動的に追加する（Feature Flag確認は呼び出しの都度、Actor
    # Intelligence等と同じ作法）。hypothesis_register/situation_actor_assessmentが発火
    # しないprofile×mode組合せ（quick/standard、またはdeepでもresearch_design等）では
    # resolve_stage2_modulesにsituation_actor_assessmentが含まれない為、Noteは追加されない
    # （新規Azure呼び出しは増えない、既存Call内のsystem prompt文字列が伸びるのみ）。
    #
    # Expert Reasoning Pipeline v0.2（ユーザー指示）: GEO_EXPERT_REASONING_PIPELINE_V2_ENABLED
    # がONの場合、v0.1の単一Noteの代わりに、stage引数（呼び出し元がCall1/Explore=explore、
    # Call2/Decide=decideを明示）に応じて別々の短いNoteを追加する。v0.1/v0.2が同時にONでも
    # 常にv0.2を優先し（elif）、2つのNoteが同時に本文へ入ることはない。
    modules = geo_expert_schema.resolve_stage2_modules(mode, profile)
    if "situation_actor_assessment" in modules:
        if reasoning_pipeline_v2_enabled():
            guidance = guidance + (
                geo_expert_schema.REASONING_PIPELINE_V2_DECIDE_NOTE if stage == "decide"
                else geo_expert_schema.REASONING_PIPELINE_V2_EXPLORE_NOTE)
            # Decision Calculus（ユーザー指示、Production統合）: Call1(Explore)側にのみ
            # 小さなAddendumを追加する（Call2/decideには追加しない）。
            if stage != "decide" and decision_calculus_enabled():
                guidance = guidance + geo_expert_schema.DECISION_CALCULUS_EXPLORE_ADDENDUM
            # Trajectory Context Experiment v0.1（ユーザー指示、test-only）: Call1(Explore)
            # 側にのみ、trajectory_assessmentを埋めるためのNeed Detection Addendumを追加
            # する（Call2側のTrajectory Reasoning Addendumは、実際にtrajectory_needed=true
            # だった場合のみrun_expert_request_staged側で動的に追加するため、ここでは
            # 追加しない）。
            if stage != "decide" and trajectory_enabled():
                guidance = guidance + geo_expert_schema.TRAJECTORY_NEED_DETECTION_EXPLORE_ADDENDUM
            # CQAS Temporal Integration v0.3（ユーザー指示、test-only）: v0.1/v0.2の
            # trajectory_assessment Gateとは異なり、Call1/Call2どちらの呼び出しにも常時
            # （trajectory_needed等の中間判定を経ずに）小さなAddendumを追加する。新規Schema
            # fieldは一切追加しない——Cue(initial_anomaly)/Question(investigation_
            # questions)/Selection(main_scenario/alternative_scenarios)という既存の
            # CQAS出力へ、Change Anomalyという観点を織り込むだけ。強制しない旨は
            # Addendum自身のNegative Controlに委ねる（Decision Calculusと同じ設計）。
            if cqas_temporal_enabled():
                guidance = guidance + (
                    geo_expert_schema.CQAS_TEMPORAL_DECIDE_ADDENDUM if stage == "decide"
                    else geo_expert_schema.CQAS_TEMPORAL_EXPLORE_ADDENDUM)
            # Situation Trajectory Intelligence v1（ユーザー指示、test-only）: Call1に
            # Issue Scope Addendumを、Call2にTrajectory Reasoning Addendumを追加する
            # （Retrieval結果の有無に関わらずCall2側は常時追加——Condition Bはこれのみ、
            # Condition Cはこれに加えて実際のRetrievalが発火する）。
            if trajectory_v1_enabled():
                guidance = guidance + (
                    geo_expert_schema.TRAJECTORY_V1_REASONING_DECIDE_ADDENDUM if stage == "decide"
                    else geo_expert_schema.TRAJECTORY_V1_ISSUE_SCOPE_EXPLORE_ADDENDUM)
            # Situation Trajectory Intelligence v1.2（ユーザー指示、test-only）: v1の
            # Boolean Gateを置き換えるEvidence Request方式。Call1にEvidence Request
            # Addendumを、Call2にReasoning Addendumを追加する（Retrieval結果の有無に
            # 関わらずCall2側は常時追加——v1と同じ構造）。
            if trajectory_v1_2_enabled():
                guidance = guidance + (
                    geo_expert_schema.TRAJECTORY_V1_2_REASONING_DECIDE_ADDENDUM if stage == "decide"
                    else geo_expert_schema.TRAJECTORY_V1_2_EVIDENCE_REQUEST_EXPLORE_ADDENDUM)
            # Expert Context Architecture v1.3（ユーザー指示、test-only）: v1/v1.2の
            # Optional Self-Report Gateを置き換える、Hypothesis Evidence Requirements +
            # Coverage Check方式。Call1にRequirements Addendumを、Call2にReasoning
            # Addendumを追加する（Retrieval結果の有無に関わらずCall2側は常時追加）。
            if expert_context_v1_3_enabled():
                guidance = guidance + (
                    geo_expert_schema.EXPERT_CONTEXT_V1_3_REASONING_DECIDE_ADDENDUM if stage == "decide"
                    else geo_expert_schema.EXPERT_CONTEXT_V1_3_REQUIREMENTS_EXPLORE_ADDENDUM)
            # Hypothesis Generation v1.4（ユーザー指示、test-only）: 各Hypothesisに
            # Continuity/State-transition構造を追加する。Call2側は常時追加
            # （Retrievalの成否とは無関係、v1.3と同じ設計）。
            if hypothesis_generation_v1_4_enabled():
                guidance = guidance + (
                    geo_expert_schema.HYPOTHESIS_GENERATION_V1_4_DECIDE_ADDENDUM if stage == "decide"
                    else geo_expert_schema.HYPOTHESIS_GENERATION_V1_4_STATE_TRANSITION_EXPLORE_ADDENDUM)
            # Selector-Free Model Sweep実験（ユーザー指示、v0.3のAblation）: Sweep ONの
            # 場合、v0.3 Selectorとは異なりCall1側にAddendumを付けない（model_activation_
            # requestsを生成させない、Call1はSurface Reading/Cue/Initial Hypotheses/
            # Intelligence Needsのみに留める）。Call2側にだけ全8件のFull Model Cardを
            # 付けたAddendumを追加する。v0.3 Selectorと同時ONの場合はSweepを優先する。
            if explanatory_model_sweep_enabled():
                if stage == "decide":
                    guidance = guidance + geo_expert_schema.EXPLANATORY_MODEL_SWEEP_DECIDE_ADDENDUM
            # Explanatory Model Activation v0.3（ユーザー指示）: v0.2 Noteを壊さず末尾へ
            # Addendumのみ追加する。v0.2 OFFの場合はこのFlagが単独でONでも何も起きない
            # （上のif分岐に入らないため）。
            elif explanatory_model_activation_enabled():
                guidance = guidance + (
                    geo_expert_schema.EXPLANATORY_MODEL_ACTIVATION_DECIDE_ADDENDUM
                    if stage == "decide"
                    else geo_expert_schema.EXPLANATORY_MODEL_ACTIVATION_EXPLORE_ADDENDUM)
        elif reasoning_core_enabled():
            guidance = guidance + geo_expert_schema.REASONING_CORE_V1_NOTE
    return f"""あなたは地政学インテリジェンス基盤の地政学専門家AIです。以下のKnowledgeに
従って分析してください。専門家レビュー前の一次フィルターであり、最終判断は行いません。

{knowledge_block}

【今回のモード】{mode}
{guidance}

厳守事項:
- Source Pack（ユーザーメッセージ内）に記載の無い事実・主体・引用を創作しない。不足して
  いればunknown_or_uncertainに明記する。
- claimsは必ずFact/Official Claim/Observation/Inference/Hypothesis/Illustrationへ分類する。
- 主シナリオには支持根拠・最も強い反論・撤回条件を必ず添える。
- 数値の判定・リスクスコアの計算はしない。Source Pack中の指標観測値は既に計算済みの値
  としてそのまま引用する。
- 【Knowledge DBから参照した確定情報】セクションはactive化された既承認の知識であり、
  【対立する見方（Contested）】セクションとは明確に区別する。後者は対立する評価として
  扱い、確定事実であるかのように断定しない。
- 外部機関のAssessmentや政府のClaimは、その主体の見方・主張として扱う。AIは複数の見方を
  比較し、自らMain Hypothesisを構築する。Sourceの見解を確定事実へ変換してはならない。
- 【Internal Expert Assessments】は当該案件・当該時点での専門家の判断であり、他案件に
  無条件に適用できるとは限らない。【Internal Expert Reasoning Rules】は案件を越えて
  再利用可能な思考方法として蓄積されたものであり、両者を混同しない。
- 【Internal Expert Assessments】に記載の各判断は、当該Case・当該時点のものであり、
  現在も有効とは限らない。現在のEvidence（観測・記録された事実情報/Actor Claims/
  External Assessments/直近のClaim）と必ず照合したうえで利用すること。
"""


def _format_structural_context(client, pack: dict) -> str:
    """Phase3-0 Gap1 Integration Fix（最小限接続）: build_foundation_pack（既存関数、変更しない）の
    うち、build_case_contextでは扱っていないEntity/Relationship/Asset/Dependency/既存Leverage Card
    のみを最小限の文字列化でCase Contextへ追加する。Foundation/Reusable/Hypothesis/Gap/Claimは
    build_case_context側と重複するためここには含めない（新しいKnowledge Model/Retrieval方式は
    作らない、既存Foundation PackをExpert Engineへ繋ぐだけの変更）。"""
    entities = pack.get("2_relevant_entities") or []
    entity_names = {e["entity_id"]: e.get("canonical_name") or e["entity_id"] for e in entities}

    # build_foundation_packはIssueに未タグのEntityどうしのRelationshipも補完するため
    # （geo_foundation_pack_service._relationship_ids_for_issues参照）、relationshipの
    # from/to側entityがpack["2_relevant_entities"]に含まれず、無変換だとEntity名の代わりに
    # UUIDがそのままプロンプトへ出てしまう。不足分だけ追加で解決する。
    rel_dep_raw = pack.get("6_relationships_and_dependencies") or {}
    missing_ids = {
        eid for r in (rel_dep_raw.get("relationships") or [])
        for eid in (r.get("from_entity_id"), r.get("to_entity_id"))
        if eid and eid not in entity_names
    }
    if missing_ids:
        extra = client.select("geo_entities", {
            "select": "entity_id,canonical_name", "entity_id": f"in.({','.join(sorted(missing_ids))})",
        })
        for e in extra:
            entity_names[e["entity_id"]] = e.get("canonical_name") or e["entity_id"]

    lines = []
    if entities:
        lines.append("\n【関連Entity（Foundation Pack）】")
        # Session2 Reasoning Upgrade: key_point_assessment.controlling_entity_idsがここに
        # 実在するentity_idのみを引用できるよう、_format_actor_intelligence_blockと同じ
        # 形式でentity_idを併記する（従来はcanonical_name/entity_typeのみでLLMへentity_idを
        # 一切見せていなかった）。
        lines.append("\n".join(
            f"- {e.get('canonical_name')}（{e.get('entity_type')}、entity_id: {e.get('entity_id')}）"
            for e in entities
        ))

    rel_dep = pack.get("6_relationships_and_dependencies") or {}
    relationships = rel_dep.get("relationships") or []
    if relationships:
        rel_lines = []
        for r in relationships:
            from_name = entity_names.get(r.get("from_entity_id"), r.get("from_entity_id"))
            to_name = entity_names.get(r.get("to_entity_id"), r.get("to_entity_id"))
            trend = f", trend: {r['trend']}" if r.get("trend") else ""
            desc = f": {r['description']}" if r.get("description") else ""
            rel_lines.append(f"- {from_name}→{to_name}: {r.get('relationship_type')}"
                              f"（domain: {r.get('relationship_domain')}{trend}）{desc}")
        lines.append("\n【Relationship（Foundation Pack）】")
        lines.append("\n".join(rel_lines))

    dependencies = rel_dep.get("dependencies") or []
    if dependencies:
        dep_lines = []
        for d in dependencies:
            name = entity_names.get(d.get("entity_id"), d.get("entity_id"))
            desc = f": {d['description']}" if d.get("description") else ""
            dep_lines.append(f"- {name}: {d.get('dependency_type')}（severity: {d.get('severity')}）{desc}")
        lines.append("\n【Dependency（Foundation Pack）】")
        lines.append("\n".join(dep_lines))

    assets = pack.get("7_assets") or []
    if assets:
        asset_lines = []
        for a in assets:
            name = entity_names.get(a.get("entity_id"), a.get("entity_id"))
            desc = f": {a['description']}" if a.get("description") else ""
            asset_lines.append(f"- {name}: {a.get('asset_type')}{desc}")
        lines.append("\n【Asset（Foundation Pack）】")
        lines.append("\n".join(asset_lines))

    leverage_cards = pack.get("8_existing_leverage_cards") or []
    if leverage_cards:
        lines.append("\n【既存Leverage Card（Foundation Pack、active化済みReusable Knowledgeのみ）】")
        lines.append("\n".join(f"- {lc.get('title')}: {lc.get('summary') or ''}" for lc in leverage_cards))

    return "\n".join(lines)


def _format_actor_intelligence_block(pack: dict) -> str:
    """Actor Intelligence Phase AI-3: geo_actor_intelligence_service.build_actor_intelligence_pack
    の結果を、Stage0でuser_promptへ追記するプロンプト文字列へ整形する（決定的処理、LLMを呼ばない）。
    Key Actorが1件も無い場合は空文字を返す（呼び出し元がuser_promptへ追記しない）。"""
    key_actors = pack.get("key_actors") or []
    if not key_actors:
        return ""

    lines = [f"\n【Actor Intelligence Pack（reference_date: {pack.get('reference_date')}、"
             f"case全体のactor_intelligence_status: {pack.get('actor_intelligence_status')}）】",
             "注意: ここの内容（core_profile/persistent_preference/decision_pattern等）を"
             "key_point_assessment等の根拠として引用したい場合、supporting_evidence_idsへ"
             "架空のIDを作らないこと——supporting_knowledge_refs（type=actor_intelligence, "
             "ref=actor_ref, field=persistent_preference等）を使うこと。ここはEvidence"
             "（今回のClaimを検証する証拠）ではなくKnowledge（Actorを理解するための"
             "既存知識）である。"]
    for ka in key_actors:
        lines.append(f"\n- {ka.get('canonical_name')}（entity_id: {ka.get('entity_id')}）")
        lines.append(f"  selection_reasons: {', '.join(ka.get('selection_reasons') or [])}"
                      f" / relevance_score: {ka.get('relevance_score')}"
                      f" / actor_intelligence_status: {ka.get('actor_intelligence_status')}")
        core_profile = ka.get("core_profile") or {}
        # Actor Intelligence Hierarchy Validation v0.5.1（ユーザー指示）: core_objectivesは
        # Schema上tier(primary/secondary、_ACTOR_OBJECTIVE_STATEMENT_ITEM)を持つが、従来の
        # 整形では出力されていなかった（Higher-order/Instrumental Objectiveの区別がLLMへ
        # 渡っていなかった構造的Gap）。tierが実際に設定されている場合のみ表示し、無い場合は
        # 何も推測せず従来通りの出力のまま（既存Actor Profileへの後方互換）。
        for item in core_profile.get("core_objectives") or []:
            tier = item.get("tier")
            tier_label = f"[{tier.upper()}] " if tier else ""
            lines.append(f"  core_profile.core_objectives: {tier_label}{item.get('statement')}"
                          f"（{item.get('information_class')}, confidence: {item.get('confidence')}）")
        for key in ("worldview", "decision_preferences", "red_lines",
                    "preferred_instruments", "structural_constraints"):
            for item in core_profile.get(key) or []:
                lines.append(f"  core_profile.{key}: {item.get('statement')}"
                              f"（{item.get('information_class')}, confidence: {item.get('confidence')}）")
        # Session2 Reasoning Upgrade（Phase2A）: 持続的Preference/Communication Patternを
        # Expert AIが実際に読める状態にする（自動候補生成・昇格はPhase6）。
        persistent_preference = ka.get("persistent_preference")
        if persistent_preference:
            lines.append(f"  persistent_preference（as_of: {persistent_preference.get('as_of')}, "
                         f"confidence: {persistent_preference.get('confidence')}）: "
                         f"{persistent_preference.get('statement')}")
        comm_pattern = ka.get("strategic_communication_pattern")
        if comm_pattern:
            lines.append(f"  strategic_communication_pattern（confidence: {comm_pattern.get('confidence')}）: "
                         f"typical_channels={comm_pattern.get('typical_channels')}, "
                         f"signaling_tendencies={comm_pattern.get('signaling_tendencies')}, "
                         f"ambiguity_or_deniability_tendency={comm_pattern.get('ambiguity_or_deniability_tendency')}")
        for dp in ka.get("decision_patterns") or []:
            role_note = f"、role: {dp.get('pattern_role')}" if dp.get("pattern_role") else ""
            lines.append(f"  decision_pattern: {dp.get('pattern')}（confidence: {dp.get('confidence')}、"
                          f"根拠先例数: {len(dp.get('supporting_precedent_ids') or [])}{role_note}）")
        current_posture = ka.get("current_posture")
        if current_posture:
            freshness = current_posture.get("freshness") or {}
            lines.append(f"  current_posture（as_of: {current_posture.get('as_of')}, "
                         f"freshness: {freshness.get('status')}/{freshness.get('age_days')}日）: "
                         f"{', '.join(current_posture.get('current_priorities') or [])}")
        issue_position = ka.get("issue_position")
        if issue_position:
            lines.append(f"  issue_position: objectives={issue_position.get('objectives')}, "
                         f"red_lines={issue_position.get('red_lines')}, "
                         f"available_leverage={issue_position.get('available_leverage')}, "
                         f"unavailable_or_costly_cards={issue_position.get('unavailable_or_costly_cards')}, "
                         f"vulnerabilities={issue_position.get('vulnerabilities')}")
        # Tier1(Eguchi Expert AI Final v1.0対応): 相手（counterpart）と方向（direction）を
        # 明示する。directional_perceptions[]がこの主体からみた相手認識を生成する際の、
        # 既存Relationship Dataに基づく根拠材料となる（このブロック自体はLLMを呼ばない
        # 決定的整形）。
        for rel in ka.get("relevant_relationships") or []:
            counterpart = rel.get("counterpart_name") or rel.get("counterpart_entity_id") or "不明"
            direction_label = "この主体→相手" if rel.get("direction") == "from" else "相手→この主体"
            lines.append(f"  relationship: 相手={counterpart}（entity_id: "
                         f"{rel.get('counterpart_entity_id')}）, {direction_label}, "
                         f"type={rel.get('relationship_type')}, domain={rel.get('relationship_domain')}, "
                         f"dependency_direction={rel.get('dependency_direction')}, "
                         f"leverage_direction={rel.get('leverage_direction')}, confidence={rel.get('confidence')}")
        for prec in ka.get("relevant_precedents") or []:
            lines.append(f"  relevant_precedent: {prec.get('title')}（knowledge_item_id: "
                         f"{prec.get('knowledge_item_id')}）")
        # Phase G(Directional Relationship Intelligence、ユーザー指示): これはobserver(この
        # 主体)の主観的認識であり、targetの客観的な実態・確定した本人の認識ではない
        # ——特にclaim_type=inferred_from_source/analyst_hypothesisやinformation_class=
        # external_assessmentの場合、targetが実際にそう考えている確定事実として扱わないこと。
        # directional_perceptions[]を生成する際、根拠のある組についてはこれを優先材料とし、
        # claim_type/confidenceをそのまま引き継ぐこと（格上げしない）。
        for drp in ka.get("directional_relationship_perceptions") or []:
            lines.append(
                f"  directional_relationship_perception: 相手={drp.get('target_canonical_name')}"
                f"（entity_id: {drp.get('target_entity_id')}）, "
                f"perception=\"{drp.get('perception')}\", claim_type={drp.get('claim_type')}, "
                f"confidence={drp.get('confidence')}, information_class={drp.get('information_class')}"
                + (f", issue_scope={drp.get('issue_scope')}" if drp.get("issue_scope") else "")
            )
    return "\n".join(lines)


def _format_institution_intelligence_block(pack: dict) -> str:
    """Expert AI Phase R1 Chunk3: geo_institution_intelligence_service.
    build_institution_intelligence_packの結果を、Stage0でuser_promptへ追記する
    プロンプト文字列へ整形する（決定的処理、LLMを呼ばない）。Person Actor（Actor
    Intelligence Pack）とは常に別セクションとして提示し、混在させない——制度的Actorを
    「志向」「性格」を持つ主体であるかのように扱わないため、mandate/decision_authority/
    constraints等の手続き的・構造的な情報のみを提示する。"""
    institutions = pack.get("institutions") or []
    if not institutions:
        return ""

    lines = ["\n【Institution Intelligence Pack】",
             "注意: ここに列挙する主体は制度・組織であり、Person Actorのような「志向」"
             "「性格」「本音」を持つ主体として扱わないこと。mandate（権限）・"
             "decision_authority（決定構造）・constraints（制約）・internal_"
             "stakeholders（内部の主体）・external_dependencies（外部依存）・"
             "historical_decision_patterns（手続き的な反復傾向）のみを根拠に判断する。"
             "ここの内容をkey_point_assessment等の根拠として引用したい場合、"
             "supporting_evidence_idsへ架空のIDを作らないこと——"
             "supporting_knowledge_refs（type=institution_intelligence, "
             "ref=entity_id, field=mandate等）を使うこと。ここはEvidence"
             "（今回のClaimを検証する証拠）ではなくKnowledge（Actorを理解するための"
             "既存知識）である。"]
    for inst in institutions:
        lines.append(f"\n- {inst.get('canonical_name')}（entity_id: {inst.get('entity_id')}、"
                      f"entity_type: {inst.get('entity_type')}、"
                      f"profile_source: {inst.get('profile_source')}）")
        if inst.get("mandate"):
            lines.append(f"  mandate: {inst['mandate']}")
        decision_authority = inst.get("decision_authority")
        if decision_authority:
            lines.append(f"  decision_authority: {decision_authority.get('description')}"
                          f"（veto_players: {decision_authority.get('veto_players')}、"
                          f"decision_process: {decision_authority.get('decision_process')}）")
        if inst.get("constraints"):
            lines.append(f"  constraints: {inst['constraints']}")
        for s in inst.get("internal_stakeholders") or []:
            lines.append(f"  internal_stakeholder: {s.get('name_or_role')}"
                          f"（position_or_influence: {s.get('position_or_influence')}）")
        for d in inst.get("external_dependencies") or []:
            lines.append(f"  external_dependency: {d.get('dependency')}（{d.get('description')}）")
        for dp in inst.get("historical_decision_patterns") or []:
            role_note = f"、role: {dp.get('pattern_role')}" if dp.get("pattern_role") else ""
            lines.append(f"  historical_decision_pattern: {dp.get('pattern')}"
                          f"（confidence: {dp.get('confidence')}{role_note}）")
        for exc in inst.get("known_exceptions") or []:
            lines.append(f"  known_exception: {exc.get('description')}（confidence: {exc.get('confidence')}）")
    return "\n".join(lines)


# Phase 7-1A(Institutionally-Constrained Controller Packaging、ユーザー指示): Phase
# 7-0で確認した通り、control_type=institutionally_constrained局面では制度的Controller
# （例: 議会）がcanonical entity自体を持たないことが多く、Source Pack本文・Actor
# Intelligence Pack本文（Trump等のstructural_constraints）に言及があっても、独立した
# Key Actorへ一度も昇格しない。DBへの新規Entity作成・Actor Intelligenceの捏造をせず、
# 「Canonical Entityとして未解決だが、既に提示済みの本文中に制度名として言及がある」
# 主体をentity_id無しのCandidateとしてまとめて提示する（Runtime-only、DB永続化なし）。
# geo_expert_quality_guard._CONTROLLER_INSTITUTION_SUFFIXESと意図的に同じ語彙を保持する
# （Reasoning前のPackaging用途とReasoning後のGuard評価用途で、同じ「制度種別語」の
# 定義を使い一貫性を保つ。モジュール間でprivateシンボルをimportするのは避け、値をここに
# 複製する）。
_CONTROLLER_CANDIDATE_INSTITUTION_SUFFIXES = (
    "議会", "委員会", "裁判所", "保険会社", "銀行", "財務省", "国防総省", "商務省",
    "規制機関", "規制当局", "当局", "大統領府", "中央銀行", "艦隊", "海警局",
)
_CONTROLLER_CANDIDATE_PATTERN = re.compile(
    "[一-龥々A-Za-z]{0,6}(?:" + "|".join(_CONTROLLER_CANDIDATE_INSTITUTION_SUFFIXES) + ")"
)


def _build_institutional_controller_context(
        *, source_pack_text: str, actor_intelligence_text: str,
        foundation_pack: dict | None, actor_intelligence_pack: dict | None,
        institution_intelligence_pack: dict | None) -> list:
    """Phase 7-1A: 既にCall1へ提示されるSource Pack本文・Actor Intelligence Pack本文
    から、制度種別語（議会/委員会/当局等）を機械的に検出し、Foundation Pack/Actor
    Intelligence Pack/Institution Intelligence Packのいずれにも同名でcanonical
    entityとして解決されていないもののみをCandidateとして返す（決定的処理、LLMを
    呼ばない）。既にentity_idで解決済みの主体（BIS等）は、Institution/Actor
    Intelligence Pack自体が既に専用セクションで詳細提示しているため対象外とする
    （本関数は「取りこぼし候補」のみを補う、重複掲載しない）。

    戻り値の各itemはRuntime-only（DB永続化しない）: {controller_label,
    canonical_entity_id（常にNone、このCandidateの定義上）, actor_intelligence_status
    （常に"unavailable_no_canonical_entity"）, sources（言及が見つかった箇所一覧）}。"""
    resolved_names_lower = set()
    for e in (foundation_pack or {}).get("2_relevant_entities") or []:
        if e.get("canonical_name"):
            resolved_names_lower.add(e["canonical_name"].lower())
    for ka in (actor_intelligence_pack or {}).get("key_actors") or []:
        if ka.get("canonical_name"):
            resolved_names_lower.add(ka["canonical_name"].lower())
    for inst in (institution_intelligence_pack or {}).get("institutions") or []:
        if inst.get("canonical_name"):
            resolved_names_lower.add(inst["canonical_name"].lower())

    candidates: dict = {}

    def scan(text, source_label):
        if not text:
            return
        for m in _CONTROLLER_CANDIDATE_PATTERN.finditer(text):
            term = m.group(0)
            term_lower = term.lower()
            if any(rn and (term_lower in rn or rn in term_lower) for rn in resolved_names_lower):
                continue
            entry = candidates.setdefault(term_lower, {"controller_label": term, "sources": set()})
            entry["sources"].add(source_label)

    scan(source_pack_text, "source_pack")
    scan(actor_intelligence_text, "actor_intelligence_pack")

    return [
        {
            "controller_label": v["controller_label"], "canonical_entity_id": None,
            "actor_intelligence_status": "unavailable_no_canonical_entity",
            "sources": sorted(v["sources"]),
        }
        for v in sorted(candidates.values(), key=lambda v: v["controller_label"])
    ]


def _format_institutional_controller_context_block(candidates: list) -> str:
    """Phase 7-1A: _build_institutional_controller_contextの結果をuser_promptへ
    追記するプロンプト文字列へ整形する（決定的処理、LLMを呼ばない）。あくまで参考情報
    として提示し、使用を強制する文言にはしない（Phase 7-1指示: rich actor intelligence
    を必ず使わせない、controller数を増やすこと自体を目的にしない）。"""
    if not candidates:
        return ""
    lines = ["\n【Institutional Controller Candidates（参考、機械的抽出、Canonical Entity未解決）】",
             "注意: 以下はSource Pack本文・Actor Intelligence Pack本文中に制度名として"
             "言及されているが、Canonical Entityとして未解決の主体である（entity_idなし、"
             "Actor Intelligenceの裏付けなし、DBに存在しないためRich Profileは提供できない）。"
             "この局面でmaterialな役割（decide/block/enable/institutionalize/implement等）"
             "を持つかどうかの判断はここでは行っていない——列挙は機械的な言及検出のみであり、"
             "使用を強制しない。materialと判断する場合はkey_actor_profilesへ自己命名した"
             "actor_ref（entity_idはnullのまま）で追加してよい（架空のentity_idを作らない）。"]
    for c in candidates:
        lines.append(f"- {c['controller_label']}（出典: {', '.join(c['sources'])}）")
    return "\n".join(lines)


# Phase 7-2K-A(Deterministic Candidate Set、ユーザー指示): Phase 7-1D/7-2I/7-2Jの3種の
# 異なるPrompt介入がすべて0/3で失敗し、LLM自由生成によるKey Actor Selectionそのものに
# 構造的な限界があると判断されたことを受けた、責務分離の再設計。System側は既存の
# Entity/Relationship/Actor Intelligence/Institution Intelligence/Issue linkageのみ
# から候補を機械的に構築する（新しいLLM呼び出しなし、新規Entity作成なし、DB書き込み
# なし）。LLMはこのCandidate Set内からのみ選択する（Phase 7-2K-B、Schema側で強制、
# geo_expert_schema.pyのactor_candidate_selection module参照）。
# Phase 7-2K-R(Role-aware Candidate Context、ユーザー指示): Candidateのsituation_role
# を、既存のcurated dataだけから機械的に導出するための語彙。model world knowledge・
# hardcoded person-specific role（例: "Donald Trump"→"米国大統領"）・current-office
# assumptionの無根拠な付与は一切行わない——entity_type分類とselect_key_actorsの
# 決定的scoring reason（issue_linked/veto_power/high_leverage_relationship/
# situation_relevant）、Institution Intelligenceのmandateという、既に実在する
# curated/evidence-backed信号のみを人間可読な語句へ翻訳する。
_ENTITY_TYPE_ROLE_LABEL = {
    "person": "個人主体",
    "country": "国家レベル主体",
    "government": "政府機関主体",
    "ministry": "省庁レベル主体",
    "military": "軍事主体",
    "security_service": "治安機関主体",
    "international_organization": "国際機関主体",
    "state_owned_enterprise": "国有企業主体",
    "political_party": "政党主体",
    "institution": "制度主体",
}


def _derive_situation_role(*, entity_type: str | None, relationship_to_issue: list,
                            institution_mandate: str | None) -> str:
    """既存data（entity_type分類・決定的scoring reason・Institution Intelligenceの
    mandate）だけからsituation_roleを導出する。安全に導出できる要素が無ければ
    "unknown"を返す（ユーザー指示「roleを安全に解決できない場合はunknownでよい」）。"""
    parts = []
    type_label = _ENTITY_TYPE_ROLE_LABEL.get(entity_type)
    if type_label:
        parts.append(type_label)
    if institution_mandate:
        parts.append(f"mandate: {institution_mandate}")
    if "issue_linked" in relationship_to_issue:
        parts.append("このCaseのIssueに人手でCuration済みのlinkあり")
    if "veto_power" in relationship_to_issue:
        parts.append("entity_type分類上、この局面で拒否権を持ちうる主体")
    if "high_leverage_relationship" in relationship_to_issue:
        parts.append("この局面と高いレバレッジ関係にあると判定された主体")
    if "situation_relevant" in relationship_to_issue:
        parts.append("Runtime解決によりこの局面に関連すると判定された主体")
    if not parts:
        return "unknown"
    return "、".join(parts)


def _build_deterministic_actor_candidate_set(
        client, *, foundation_pack: dict | None, actor_intelligence_pack: dict | None,
        institution_intelligence_pack: dict | None) -> list:
    """Foundation Pack `2_relevant_entities`（Issue-linked + 1-hop Relationship展開）・
    Actor Intelligence Pack `key_actors`・Institution Intelligence Pack `institutions`
    の和集合を、entity_idごとに正規化されたCandidateへdedupe・統合する。粒度
    （国家/制度/個人）による優先順位付けはこの関数では一切行わない——単に候補を
    列挙するだけで、どれがmaterialかの判断はLLM側（Phase 7-2K-B）に委ねる。

    2026-09-21実データ検証で発見・修正: Actor Intelligence Pack `key_actors`は
    entity_typeを自身のフィールドとして持たない（Person中心設計の名残）ため、
    以前はここで一律entity_type="person"と決め打ちしていた。しかし1-hop Relationship
    展開（Phase 7-2X参照）経由でcountry/institution種別のEntityもkey_actorsへ混入する
    （例: "United States"はentity_type=countryなのに"person"と誤表示していた）。
    候補のabstraction level（国家/制度/個人）を正しくLLMへ伝えることが本Phaseの核心
    であるため、最後に`geo_entities`への実DB問い合わせでentity_typeを正規化する
    （決定的、追加LLM呼び出しなし）。"""
    candidates: dict = {}

    def _get_or_create(entity_id: str, *, entity_type: str | None, name: str | None) -> dict:
        return candidates.setdefault(entity_id, {
            "entity_id": entity_id, "entity_type": entity_type, "name": name,
            "relationship_to_issue": [], "intelligence_available": False,
            "intelligence_richness": "none", "source_reason": [],
        })

    for e in (foundation_pack or {}).get("2_relevant_entities") or []:
        eid = e.get("entity_id")
        if not eid:
            continue
        c = _get_or_create(eid, entity_type=e.get("entity_type"), name=e.get("canonical_name"))
        if "foundation_pack_relevant_entity" not in c["source_reason"]:
            c["source_reason"].append("foundation_pack_relevant_entity")

    for ka in (actor_intelligence_pack or {}).get("key_actors") or []:
        eid = ka.get("entity_id")
        if not eid:
            continue
        c = _get_or_create(eid, entity_type=None, name=ka.get("canonical_name"))
        for reason in (ka.get("selection_reasons") or []):
            if reason not in c["relationship_to_issue"]:
                c["relationship_to_issue"].append(reason)
            if reason not in c["source_reason"]:
                c["source_reason"].append(reason)
        status = ka.get("actor_intelligence_status")
        if status in ("partial", "sufficient"):
            c["intelligence_available"] = True
            c["intelligence_richness"] = status

    institution_mandates: dict = {}
    for inst in (institution_intelligence_pack or {}).get("institutions") or []:
        eid = inst.get("entity_id")
        if not eid:
            continue
        c = _get_or_create(eid, entity_type=inst.get("entity_type") or "institution",
                            name=inst.get("canonical_name"))
        if inst.get("profile_source") == "institution_intelligence_active":
            c["intelligence_available"] = True
            c["intelligence_richness"] = "active"
        if "institution_intelligence_pack" not in c["source_reason"]:
            c["source_reason"].append("institution_intelligence_pack")
        if inst.get("mandate"):
            institution_mandates[eid] = inst["mandate"]

    # entity_typeが未確定（Actor Intelligence Pack経由でFoundation Packに無かった候補）
    # のものだけ、実DBへ問い合わせて正規化する（不要な問い合わせを増やさない）。
    missing_type_ids = [eid for eid, c in candidates.items() if not c["entity_type"]]
    if missing_type_ids and client is not None:
        for eid in missing_type_ids:
            row = geo_entity_service.get_entity(client, eid)
            if row:
                candidates[eid]["entity_type"] = row.get("entity_type")

    # Phase 7-2K-R(ユーザー指示): 全sourceのmergeが終わった後（entity_type正規化・
    # relationship_to_issue集約後）にsituation_roleを導出する。
    for eid, c in candidates.items():
        c["situation_role"] = _derive_situation_role(
            entity_type=c["entity_type"], relationship_to_issue=c["relationship_to_issue"],
            institution_mandate=institution_mandates.get(eid))

    return sorted(candidates.values(), key=lambda c: c["name"] or "")


def _format_actor_candidate_set_block(candidates: list) -> str:
    """Phase 7-2K-A: _build_deterministic_actor_candidate_setの結果をuser_promptへ
    追記するプロンプト文字列へ整形する（決定的処理、LLMを呼ばない）。"""
    if not candidates:
        return ""
    lines = ["\n【Actor Candidate Set（Phase 7-2K、決定的生成、Canonical Entity限定）】",
             "注意: actor_candidate_selection.candidate_entity_idには、以下に実在する"
             "entity_idのみを使うこと。ここに無い主体を新たにentity_idとして作らない"
             "こと（架空のIDを作らない）。必要な主体がここに無い場合は、無理に"
             "entity_idを割り当てず、actor_candidate_gapsへその旨を記述すること。"
             "各候補について、この局面で実際にdecision authority／implementation "
             "authority／blocking power／agenda-setting power／negotiating power／"
             "material business influenceのいずれかを持つかを評価し、持つと判断した"
             "ものだけをactor_candidate_selectionへ含めること。単なる言及・関連性"
             "だけでは不十分。粒度（国家/制度/個人等）そのものでの優先順位付けは"
             "しないこと——情報の豊富さ・具体性ではなく、この局面への実際のagencyで"
             "判断すること。"]
    for c in candidates:
        lines.append(f"- {c['name']}（entity_id: {c['entity_id']}、entity_type: "
                      f"{c['entity_type']}、situation_role: {c.get('situation_role', 'unknown')}、"
                      f"intelligence_available: {c['intelligence_available']}、"
                      f"intelligence_richness: {c['intelligence_richness']}、source_reason: "
                      f"{', '.join(c['source_reason'])}）")
    return "\n".join(lines)


# Phase 7-2K-C(Referential Integrity、ユーザー指示「候補集合外のentity_idは禁止」):
# actor_candidate_selectionはRESPONSE_SCHEMA非登録のtest-only fieldであり
# response_jsonには一切含まれない（v1.2/v1.3と同じ設計）。したがって本関数の結果は
# 診断用telemetry専用——response_jsonの補正は行わない（そもそも公開Schemaに存在
# しないため補正対象が無い）。
def _validate_actor_candidate_selection(stage1_data: dict, candidates: list) -> dict:
    """actor_candidate_selection[].candidate_entity_idが、実際に提示したCandidate Set
    に含まれるかを決定的に検証する。含まれない場合はhallucinated_idsとして記録する
    （nullへの書き換えは行わない——response_jsonに出ないfieldのため、記録のみで足りる）。"""
    valid_ids = {c["entity_id"] for c in candidates}
    selections = stage1_data.get("actor_candidate_selection") or []
    matched = []
    hallucinated = []
    for sel in selections:
        cid = sel.get("candidate_entity_id")
        if cid in valid_ids:
            matched.append(cid)
        else:
            hallucinated.append(cid)
    names_by_id = {c["entity_id"]: c.get("name") for c in candidates}
    return {
        "candidate_set_size": len(candidates),
        "selected_count": len(selections),
        "matched_count": len(matched),
        "hallucinated_candidate_ids": hallucinated,
        "gaps": stage1_data.get("actor_candidate_gaps") or [],
        # 2026-09-21追加(ユーザー指示への対応強化): 「なぜTrumpが選ばれなかったか」を
        # 事後分析できるよう、選択・非選択それぞれの理由を診断telemetryとして残す
        # （response_jsonには出ない、v1.2/v1.3と同じtest-only diagnostics）。
        "selected": [{"entity_id": s.get("candidate_entity_id"),
                       "name": names_by_id.get(s.get("candidate_entity_id")),
                       "materiality_reason": s.get("materiality_reason"),
                       "role_in_situation": s.get("role_in_situation"),
                       "confidence": s.get("confidence")} for s in selections],
        "not_selected": [{"entity_id": c["entity_id"], "name": c.get("name")}
                          for c in candidates if c["entity_id"] not in matched],
    }


# Phase 7-2L(Selection-to-Profile Handoff Integrity、ユーザー指示): Phase 7-2K-R
# Run3で、actor_candidate_selectionでは選択・理由付けされた主体（Donald Trump）が
# 最終key_actor_profilesには一切現れないという内部矛盾が観測された。コード監査
# （_merge_staged_responseはstage1_data由来のkey_actor_profilesを一切フィルタしない、
# repair_attempted=False、entity_reference_resolutions=[]、entity_identity_
# mismatches=[]）により、この矛盾はPython側の後処理では一切発生し得ず、単一の
# raw Stage1 LLM completion自体に既に存在していたことを確定した
# （PHASE_7_2L_SELECTION_PROFILE_HANDOFF_DIAGNOSIS_V1.md参照、
# 分類=LLM_INTERNAL_CONSISTENCY）。6-runのCross-run確認では1/6のみで発生し、
# systemicではない。本関数はこの種の内部矛盾を検知するだけの決定的Observability
# 専用の追加であり、entity_idを書き換えたり、不足したkey_actor_profilesを
# 自動生成・補完したりは一切しない（ユーザー指示「自動で人物profileを生成・
# 補完しない。必要ならunresolved/incompleteとして残す」）。
def _validate_selection_profile_handoff(response: dict, selection_diagnostics: dict | None) -> dict | None:
    """actor_candidate_selectionで選択されたentity_idが、最終response_json
    （repair/rebind等すべての後処理を経た後）のkey_actor_profilesに実在するかを
    決定的に検証する。selection_diagnosticsがNone（module未発火）の場合はNoneを返す。"""
    if selection_diagnostics is None:
        return None
    selected_ids = {s["entity_id"] for s in selection_diagnostics.get("selected") or [] if s.get("entity_id")}
    if not selected_ids:
        return {"selected_but_no_profile": [], "profile_but_not_selected": []}
    profile_ids = {kap.get("entity_id") for kap in response.get("key_actor_profiles") or [] if kap.get("entity_id")}
    names_by_id = {s["entity_id"]: s.get("name") for s in selection_diagnostics.get("selected") or []}
    selected_but_no_profile = [
        {"entity_id": eid, "name": names_by_id.get(eid)} for eid in selected_ids if eid not in profile_ids]
    profile_but_not_selected = [
        {"entity_id": kap.get("entity_id"), "actor": kap.get("actor")}
        for kap in response.get("key_actor_profiles") or []
        if kap.get("entity_id") and kap.get("entity_id") not in selected_ids]
    return {
        "selected_but_no_profile": selected_but_no_profile,
        "profile_but_not_selected": profile_but_not_selected,
    }


def _format_media_intelligence_block(pack: dict) -> str:
    """Media Intelligence Build（M8.5）: geo_media_intelligence_service.build_media_intelligence_pack
    の結果を、Stage0でuser_promptへ追記するプロンプト文字列へ整形する（決定的処理、LLMを呼ばない）。
    Pattern/Profileが1件も無い場合は空文字を返す（_format_actor_intelligence_blockと同じ設計）。"""
    patterns = pack.get("media_patterns") or []
    profiles = pack.get("media_profiles") or []
    if not patterns and not profiles:
        return ""

    lines = [f"\n【Media Intelligence Pack（statuses_included: {pack.get('statuses_included')}）】"]
    if patterns:
        lines.append("\n■ Media Pattern Candidates（複数の過去局面から導出された媒体の傾向。"
                      "単発記事の解釈ではなく、Sourceの一般的な振る舞いパターンとして参照する。"
                      "この情報を根拠として引用する場合は、必ずここに示すknowledge_item_id"
                      "（実在するID）を使うこと。パターン名やラベルを新たなIDとして自作しない）:")
        for p in patterns:
            role_note = f"、role: {p.get('pattern_role')}" if p.get("pattern_role") else ""
            lines.append(f"  - {p.get('pattern')}（knowledge_item_id: {p.get('knowledge_item_id')}、"
                          f"confidence: {p.get('confidence')}、"
                          f"根拠Episode数: {p.get('evidence_episode_count')}{role_note}）")
            if p.get("when_it_applies"):
                lines.append(f"    when_it_applies: {p.get('when_it_applies')}")
            if p.get("exception_conditions"):
                lines.append(f"    exception_conditions: {p.get('exception_conditions')}")
    if profiles:
        lines.append("\n■ Media Profiles（媒体単位の比較的安定したIntelligence。"
                      "biasという単純属性ではなく『何を見るために有効か』として扱う。"
                      "根拠として引用する場合は、必ずここに示すknowledge_item_id"
                      "（実在するID）を使うこと。媒体名を新たなIDとして自作しない）:")
        for pr in profiles:
            lines.append(f"  - {pr.get('source_name')}（knowledge_item_id: {pr.get('knowledge_item_id')}、"
                         f"confidence: {pr.get('confidence')}）: "
                         f"{pr.get('institutional_perspective')}")
            if pr.get("use_for"):
                lines.append(f"    use_for: {pr.get('use_for')} / do_not_use_for: {pr.get('do_not_use_for')}")
            if pr.get("known_limitations"):
                lines.append(f"    known_limitations: {pr.get('known_limitations')}")
    return "\n".join(lines)


def _has_supporting_evidence_chain(client, knowledge_item_id: str) -> bool:
    """Active Knowledge Itemに、実際にEvidenceまで遡れるSupporting Claimが1件以上あるか検証する
    （structured_content内の自己申告フィールドではなく、geo_knowledge_item_claims経由で検証する）。"""
    links = client.select("geo_knowledge_item_claims", {
        "select": "claim_id", "knowledge_item_id": f"eq.{knowledge_item_id}", "relationship": "eq.supporting",
    })
    for link in links:
        evidence = client.select("geo_claim_evidence", {"select": "claim_evidence_id", "claim_id": f"eq.{link['claim_id']}"})
        if evidence:
            return True
    return False


def _char_len(*texts) -> int:
    return sum(len(str(t)) for t in texts if t)


def _resolve_source_label(client, source_id: str | None) -> str:
    """Phase2B-2: External Assessment表示用にSource名・perspectiveを解決する。
    解決できない場合は空文字を返す（表示自体は諦めない）。"""
    if not source_id:
        return ""
    rows = client.select("geo_source_registry", {
        "select": "name,perspective", "source_id": f"eq.{source_id}", "limit": "1",
    })
    if not rows:
        return ""
    name = rows[0].get("name") or ""
    perspective = rows[0].get("perspective")
    return f"{name}（{perspective}）" if perspective else name


def _format_reference_claim_line(client, claim: dict, *, with_source: bool = False) -> str:
    line = f"- {claim.get('claim_text', '')}"
    if with_source:
        source_label = _resolve_source_label(client, claim.get("source_id"))
        if source_label:
            line += f"（Source: {source_label}）"
    return line


def _format_case_finding_line(item: dict) -> str:
    """Phase2B-2: Internal Expert Assessment（case_finding）の表示行。元Case・判断時点・Reviewerを
    必ず併記する（いずれか解決できなくても、取得できた範囲でそのまま表示する）。"""
    line = f"- [case_finding] {item.get('title', '')}: {item.get('summary') or ''}"
    provenance = (f"原Case: {item.get('original_case_title') or '不明'} / "
                  f"判断時点: {item.get('as_of_date') or '不明'} / "
                  f"Reviewer: {item.get('reviewed_by') or '不明'}")
    return f"{line}\n  （{provenance}）"


def build_case_context(client, case_id: str) -> dict:
    """Case Contextを組み立てる。Retrieval Layer経由でFoundation/Reusable/Live/Case/Contested
    Contextを取得し、Top-K・Evidence必須条件・重複除外・Token Budgetを適用した上で、プロンプトへ
    統合するためのtext本文と、Retrieval Trace記録用のinclusion/exclusion情報を返す。"""
    case_rows = client.select("geo_cases", {"select": "*", "case_id": f"eq.{case_id}", "limit": "1"})
    if not case_rows:
        raise ValueError(f"case_idが見つかりません: {case_id}")

    issue_links = client.select("geo_case_issues", {"select": "issue_id", "case_id": f"eq.{case_id}"})
    issue_ids = sorted({link["issue_id"] for link in issue_links})

    foundation = geo_retrieval_service.get_foundation_knowledge(client, issue_ids)[:SECTION_TOP_K["foundation"]]
    precedents = geo_retrieval_service.get_relevant_precedents(client, issue_ids)[:SECTION_TOP_K["precedent"]]
    mechanisms = geo_retrieval_service.get_relevant_mechanisms(client, issue_ids)[:SECTION_TOP_K["mechanism"]]
    reasoning_rules = geo_retrieval_service.get_relevant_reasoning_rules(client, issue_ids)[:SECTION_TOP_K["reasoning_rule"]]
    indicators = geo_retrieval_service.get_reusable_knowledge(
        client, issue_ids, knowledge_types=("indicator",))[:SECTION_TOP_K["indicator"]]
    contested = geo_retrieval_service.get_contested_knowledge(client, issue_ids)
    case_context = geo_retrieval_service.get_case_context(client, case_id)
    gaps = case_context["gaps"][:SECTION_TOP_K["gap"]]

    # Phase2B-2: Internal Expert Assessment（case_finding、Case/時点依存の判断）は
    # Internal Expert Reasoning Rule（reasoning_rule/mechanism/precedent、再利用可能な思考方法）
    # とは別クエリ・別セクションで扱う
    internal_expert_assessments_raw = geo_retrieval_service.get_internal_expert_assessments(
        client, issue_ids)[:SECTION_TOP_K["internal_expert_assessment"]]

    # Phase2B-2: External Reference（geo_claim_issues経由、Human Reviewなしでretrieval_status=
    # availableのもの）。Caseスコープのclaims（既存case_context["claims"]）とIssueスコープの
    # Reference Claimをclaim_idで統合し、information_classで振り分ける
    reference_claims = geo_retrieval_service.get_reference_claims_for_issue(client, issue_ids)
    claims_by_id = {c["claim_id"]: c for c in reference_claims}
    for c in case_context["claims"]:
        claims_by_id.setdefault(c["claim_id"], c)
    all_claims = list(claims_by_id.values())
    observed_facts = [c for c in all_claims
                       if c.get("information_class") == "observed_fact"][:SECTION_TOP_K["observed_fact"]]
    actor_claims = [c for c in all_claims
                     if c.get("information_class") == "actor_claim"][:SECTION_TOP_K["actor_claim"]]
    external_assessments = [c for c in all_claims
                             if c.get("information_class") == "external_assessment"][:SECTION_TOP_K["external_assessment"]]
    other_claims = [c for c in all_claims if c.get("information_class") not in
                     ("observed_fact", "actor_claim", "external_assessment")][:SECTION_TOP_K["live"]]

    # Evidence必須条件: Supporting Claim→Evidenceまで遡れないactive itemを除外する
    included, excluded = [], []
    seen_ids = set()
    for section_name, items, top_k in (
        ("foundation", foundation, SECTION_TOP_K["foundation"]),
        ("precedent", precedents, SECTION_TOP_K["precedent"]),
        ("mechanism", mechanisms, SECTION_TOP_K["mechanism"]),
        ("reasoning_rule", reasoning_rules, SECTION_TOP_K["reasoning_rule"]),
        ("indicator", indicators, SECTION_TOP_K["indicator"]),
    ):
        for item in items:
            if item["knowledge_item_id"] in seen_ids:
                continue
            seen_ids.add(item["knowledge_item_id"])
            if not _has_supporting_evidence_chain(client, item["knowledge_item_id"]):
                excluded.append({"result_type": "knowledge_item", "result_id": item["knowledge_item_id"],
                                  "exclusion_reason": "no_supporting_evidence_chain"})
                continue
            included.append({"section": section_name, "knowledge_item_id": item["knowledge_item_id"],
                              "title": item["title"], "summary": item.get("summary") or "",
                              "knowledge_type": item["knowledge_type"],
                              "information_class": item.get("information_class")})

    # Internal Expert Assessment（case_finding）は同じEvidence必須条件・重複除外を適用するが、
    # Token Budgetの対象外（簡易化。実利用上case_findingは既にIssue単位でTop-K制限済みのため）
    internal_expert_assessments_included = []
    for item in internal_expert_assessments_raw:
        if item["knowledge_item_id"] in seen_ids:
            continue
        seen_ids.add(item["knowledge_item_id"])
        if not _has_supporting_evidence_chain(client, item["knowledge_item_id"]):
            excluded.append({"result_type": "knowledge_item", "result_id": item["knowledge_item_id"],
                              "exclusion_reason": "no_supporting_evidence_chain"})
            continue
        internal_expert_assessments_included.append(item)
    internal_expert_assessments_included = geo_retrieval_service.attach_case_finding_provenance(
        client, internal_expert_assessments_included)

    # Token Budget: 超過したら下位（末尾）から間引く。優先順位はReusable→Live→Foundation、
    # Case固有情報（Hypothesis/Gap）は間引かない。
    def _section_text(entries):
        return "\n".join(f"- [{e['knowledge_type']}] {e['title']}: {e['summary']}" for e in entries)

    foundation_included = [e for e in included if e["section"] == "foundation"]
    reusable_included_all = [e for e in included
                              if e["section"] in ("precedent", "mechanism", "reasoning_rule", "indicator")]
    # Phase2B-2: Internal Expert Reasoning Rule（information_class=internal_expert_assessment）は
    # 「確定情報」セクションから分離し、別セクションとして扱う（両者を混同しないため）
    internal_expert_reasoning_included = [e for e in reusable_included_all
                                           if e.get("information_class") == "internal_expert_assessment"
                                           ][:SECTION_TOP_K["internal_expert_reasoning"]]
    reusable_included = [e for e in reusable_included_all
                          if e.get("information_class") != "internal_expert_assessment"]

    budget = DEFAULT_CHAR_BUDGET
    core_text = _char_len(
        [h["hypothesis_statement"] for h in case_context["hypotheses"]],
        [g["gap_description"] for g in gaps],
    )
    budget -= core_text

    # Token Budget: 超過したら下位（末尾）から間引く。優先順位はReusable→Foundation。
    # Phase2B-2で追加したExternal Reference/Internal Expert Assessmentの各セクションは、
    # 既にIssue単位のTop-Kで上限を設けているため、この文字数Budgetの対象外とする（簡易化）。
    trimmed_out = []
    while _char_len(_section_text(reusable_included), _section_text(foundation_included)) > budget:
        if reusable_included:
            trimmed_out.append(reusable_included.pop())
        elif foundation_included:
            trimmed_out.append(foundation_included.pop())
        else:
            break

    for item in trimmed_out:
        if item.get("knowledge_item_id"):
            excluded.append({"result_type": "knowledge_item", "result_id": item["knowledge_item_id"],
                              "exclusion_reason": "token_budget_exceeded"})

    lines = []
    if foundation_included:
        lines.append("\n【Knowledge DBから参照した確定情報（Foundation）】")
        lines.append(_section_text(foundation_included))
    if reusable_included:
        lines.append("\n【Knowledge DBから参照した確定情報（Precedent/Mechanism/Reasoning Rule/Indicator）】")
        lines.append(_section_text(reusable_included))
    if contested:
        lines.append("\n【対立する見方（Contested、確定事実として扱わないこと）】")
        lines.append("\n".join(f"- [{c['knowledge_type']}] {c['title']}: {c.get('summary') or ''}" for c in contested))
    if observed_facts:
        lines.append("\n【観測・記録された事実情報】")
        lines.append("\n".join(_format_reference_claim_line(client, c) for c in observed_facts))
    if actor_claims:
        lines.append("\n【Actor Claims（主体の発言、事実性は保証しない）】")
        lines.append("\n".join(_format_reference_claim_line(client, c) for c in actor_claims))
    if external_assessments:
        lines.append("\n【External Assessments（外部専門機関の見解、Source/Perspectiveを併記）】")
        lines.append("\n".join(_format_reference_claim_line(client, c, with_source=True) for c in external_assessments))
    if other_claims:
        lines.append("\n【直近のClaim（その他）】")
        lines.append("\n".join(_format_reference_claim_line(client, c) for c in other_claims))
    if internal_expert_assessments_included:
        lines.append("\n【Internal Expert Assessments（本件・時点に紐づく専門家の判断）】")
        lines.append("\n".join(_format_case_finding_line(i) for i in internal_expert_assessments_included))
    if internal_expert_reasoning_included:
        lines.append("\n【Internal Expert Reasoning Rules（案件を越えて再利用可能な思考方法）】")
        lines.append(_section_text(internal_expert_reasoning_included))
    if case_context["hypotheses"]:
        lines.append("\n【既存の仮説】")
        for h in case_context["hypotheses"]:
            lines.append(f"- [{h['hypothesis_type']}] {h['hypothesis_statement']}")
    if gaps:
        lines.append("\n【既知のKnowledge Gap】")
        lines.append("\n".join(f"- [{g['gap_category']}] {g['gap_description']}" for g in gaps))

    included_final = (foundation_included + reusable_included + internal_expert_reasoning_included
                       + internal_expert_assessments_included)
    # v1.2: Evidence ID捏造防止チェック（geo_expert_quality_guard.evidence_ids_not_fabricated）用に、
    # プロンプトへ実際に投入したClaim IDも返す（既存のincluded/excludedはknowledge_item_idのみ
    # 追跡しており、claims系4セクションはこれまでTraceの対象外だったため新たに集約する）。
    claim_ids = sorted({
        c["claim_id"] for c in (observed_facts + actor_claims + external_assessments + other_claims)
        if c.get("claim_id")
    })
    return {
        "text": "\n".join(lines), "issue_ids": issue_ids,
        "included": included_final, "excluded": excluded,
        "gaps": gaps, "hypotheses": case_context["hypotheses"], "contested": contested,
        "claim_ids": claim_ids,
    }


def _record_case_retrieval_trace(client, *, case_id: str, expert_request_id: str, context: dict) -> None:
    run = client.insert("geo_retrieval_runs", [{
        "case_id": case_id, "expert_request_id": expert_request_id, "query_text": None,
        "filters": {"issue_ids": context["issue_ids"]}, "retrieval_mode": "expert_context",
        "completed_at": datetime.now(timezone.utc).isoformat(), "retrieved_count": len(context["included"]),
    }])[0]
    for rank, item in enumerate(context["included"], start=1):
        client.insert("geo_retrieval_results", [{
            "retrieval_run_id": run["retrieval_run_id"], "result_type": "knowledge_item",
            "result_id": item["knowledge_item_id"], "rank": rank,
            "inclusion_reason": "within_budget_and_top_k", "used_in_final_context": True,
        }], prefer="return=minimal")
        # Phase2B-2: usage_count/last_used_atはAnalytics専用（Retrieval Scoreには使わない、
        # geo_search_service.py参照）。既存のRetrieval Trace記録ポイントに相乗りするのみで、
        # 新しい記録経路は作らない
        current = client.select("geo_knowledge_items", {
            "select": "usage_count", "knowledge_item_id": f"eq.{item['knowledge_item_id']}", "limit": "1",
        })
        if current:
            client.update("geo_knowledge_items", {"knowledge_item_id": f"eq.{item['knowledge_item_id']}"}, {
                "usage_count": (current[0].get("usage_count") or 0) + 1,
                "last_used_at": datetime.now(timezone.utc).isoformat(),
            })
    for item in context["excluded"]:
        client.insert("geo_retrieval_results", [{
            "retrieval_run_id": run["retrieval_run_id"], "result_type": item["result_type"],
            "result_id": item["result_id"], "exclusion_reason": item["exclusion_reason"],
            "used_in_final_context": False,
        }], prefer="return=minimal")


def _accumulate_usage(total: dict, usage: dict | None) -> None:
    if not usage:
        return
    total["calls"] += 1
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if usage.get(key) is not None:
            total[key] = (total[key] or 0) + usage[key]


def run_expert_request(client, azure_client, model: str, mode: str, theme_id: str | None,
                        query_text: str, draft_text: str | None = None, *,
                        case_id: str | None = None,
                        actor_intelligence_enabled: bool | None = None) -> dict:
    """Source Pack作成→構造化LLM呼び出し→Quality Guard→DB保存までを一括で行う。

    actor_intelligence_enabled: 既定None（operation-scoped override、2026-09-25追加）。
    Noneの場合は既存のglobal env flag（モジュール関数actor_intelligence_enabled()）へ
    そのまま委ねる。True/False明示時はglobal flagに関わらずその値を使う
    （Reading生成専用、Consultation/Draft/RefineDesign等は引き続きNoneのまま呼ぶ）。
    case_id指定時のみ、Knowledge DBのFoundation/Reusable/Case/Contested Contextを統合する
    （theme_idのみの既存呼び出しは完全に後方互換を維持する）。

    v1.2(Session2): モード別のKey Stakeholder-first必須項目（geo_expert_quality_guard.
    MODE_V12_CHECK_NAMES）が欠落した場合、元の応答を維持したまま不足箇所のみを補う修復生成を
    最大1回行う。1リクエストあたりの実Azure OpenAI API呼び出しは、通常生成（構造化出力/手動JSON
    fallback/Schema訂正リトライを含め最大2回）＋修復生成（内部リトライ無効化、最大1回）の
    合計最大3回に制御する（geo_llm_common.call_llm_structuredのmax_attempts引数参照）。"""
    source_pack = build_source_pack(client, theme_id, query_text)
    knowledge = geo_expert_common.load_geo_expert_knowledge()
    knowledge_block = geo_expert_common.build_knowledge_block()

    system_prompt = _build_system_prompt(mode, knowledge_block)
    user_prompt = f"{source_pack['text']}\n\n【依頼内容】\n{query_text}"
    if draft_text:
        user_prompt += f"\n\n【レビュー対象のドラフト】\n{draft_text}"

    case_context = None
    case_context_text = ""
    if case_id:
        case_context = build_case_context(client, case_id)
        case_context_text = case_context["text"]
        user_prompt += f"\n{case_context_text}"
        # Phase3-0 Gap1 Integration Fix: 既存build_foundation_packが持つEntity/Relationship/
        # Asset/Dependency/既存Leverage Cardを最小限追加する（新しいRetrieval方式は作らない）
        foundation_pack = geo_foundation_pack_service.build_foundation_pack(client, case_id)
        structural_text = _format_structural_context(client, foundation_pack)
        if structural_text:
            user_prompt += f"\n{structural_text}"

    # Actor Intelligence Phase AI-3: Feature Flag有効時のみ、case_id指定時に限りPackを注入する。
    effective_actor_intelligence_enabled = _resolve_actor_intelligence_enabled(actor_intelligence_enabled)
    actor_intelligence_pack = None
    if case_id and effective_actor_intelligence_enabled:
        actor_intelligence_pack = geo_actor_intelligence_service.build_actor_intelligence_pack(client, case_id)
        actor_intelligence_text = _format_actor_intelligence_block(actor_intelligence_pack)
        if actor_intelligence_text:
            user_prompt += f"\n{actor_intelligence_text}"

    # v1.2: Evidence ID捏造防止チェック（evidence_ids_not_fabricated）用の既知ID集合。
    # Source Pack由来のID＋（case_id指定時）Case Context由来のknowledge_item_id/claim_id/
    # hypothesis_id/gap_idを集約する。
    known_ids = set(source_pack["document_ids"]) | set(source_pack["event_ids"]) | set(source_pack["evidence_ids"])
    if case_context is not None:
        known_ids |= {i.get("knowledge_item_id") for i in case_context["included"] if i.get("knowledge_item_id")}
        known_ids |= {i.get("knowledge_item_id") for i in case_context["contested"] if i.get("knowledge_item_id")}
        known_ids |= set(case_context["claim_ids"])
        known_ids |= {h.get("hypothesis_id") for h in case_context["hypotheses"] if h.get("hypothesis_id")}
        known_ids |= {g.get("gap_id") for g in case_context["gaps"] if g.get("gap_id")}
    if actor_intelligence_pack is not None:
        known_ids |= actor_intelligence_pack.get("known_ids") or set()
    known_ids.discard(None)

    usage_total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    result = geo_llm_common.call_llm_structured(
        azure_client, model, system_prompt, user_prompt, geo_expert_schema.RESPONSE_SCHEMA,
        "geo_expert_response", max_attempts=2,
    )
    response_json = result["data"]
    if actor_intelligence_pack is not None:
        # Retrieval側が決定的に算出した充足度をそのまま設定する（LLMには生成させない）。
        response_json["actor_intelligence_status"] = actor_intelligence_pack.get("actor_intelligence_status")
    _accumulate_usage(usage_total, result.get("usage"))

    request_row = client.insert("geo_expert_requests", [{
        "theme_id": theme_id, "mode": mode, "query_text": query_text,
    }])[0]

    source_pack_row = client.insert("geo_source_packs", [{
        "request_id": request_row["request_id"],
        "document_ids": source_pack["document_ids"], "event_ids": source_pack["event_ids"],
        "evidence_ids": source_pack["evidence_ids"], "observation_snapshot": source_pack["observation_snapshot"],
    }])[0]

    versions = {
        "prompt_version": PROMPT_VERSION,
        "knowledge_version": knowledge["version"],
        "model": model,
        "source_pack_id": source_pack_row["source_pack_id"],
    }
    guard_result = geo_expert_quality_guard.run_quality_guard(
        response_json, source_pack["text"], versions,
        case_context_text=case_context_text, known_ids=known_ids, mode=mode,
    )

    initial_failed_checks = [c["name"] for c in guard_result["checks"] if not c["passed"]]
    repair_attempted = False
    remaining_failed_checks = initial_failed_checks
    needs_repair = any(name in geo_expert_quality_guard.MODE_V12_CHECK_NAMES for name in initial_failed_checks)

    if needs_repair:
        repair_attempted = True
        failed_detail = "\n".join(
            f"- {c['name']}: {c['detail']}" for c in guard_result["checks"] if not c["passed"]
        )
        repair_user_prompt = (
            f"{user_prompt}\n\n【修復依頼】\n以下は今回の元の応答JSONです。下記のQuality Guard"
            f"チェックに失敗しています。既存の内容は維持したまま、不足している箇所のみを補い、"
            f"Schemaに準拠したJSON全体を再度出力してください。ゼロから作り直さないこと。"
            f"根拠が無い場合は空配列/nullのままでよく、Evidence IDを創作しないこと（Source Pack/"
            f"Case Context/Foundation Packに実在するIDのみ使用すること）。\n"
            f"【失敗したチェック】\n{failed_detail}\n\n"
            f"【元の応答JSON】\n{json.dumps(response_json, ensure_ascii=False)}"
        )
        try:
            repair_result = geo_llm_common.call_llm_structured(
                azure_client, model, system_prompt, repair_user_prompt, geo_expert_schema.RESPONSE_SCHEMA,
                "geo_expert_response_repair", max_attempts=1,
            )
            response_json = repair_result["data"]
            if actor_intelligence_pack is not None:
                response_json["actor_intelligence_status"] = actor_intelligence_pack.get("actor_intelligence_status")
            _accumulate_usage(usage_total, repair_result.get("usage"))
            guard_result = geo_expert_quality_guard.run_quality_guard(
                response_json, source_pack["text"], versions,
                case_context_text=case_context_text, known_ids=known_ids, mode=mode,
            )
            remaining_failed_checks = [c["name"] for c in guard_result["checks"] if not c["passed"]]
        except geo_llm_common.GeoLLMError:
            remaining_failed_checks = initial_failed_checks

    completeness_status = "complete" if not remaining_failed_checks else "incomplete_after_repair"

    response_row = client.insert("geo_expert_responses", [{
        "request_id": request_row["request_id"], "source_pack_id": source_pack_row["source_pack_id"],
        "mode": mode, "prompt_version": PROMPT_VERSION, "knowledge_version": knowledge["version"],
        "model": model, "response_json": response_json,
    }])[0]

    evaluation_row = client.insert("geo_quality_evaluations", [{
        "response_id": response_row["response_id"], "checks": guard_result["checks"],
        "passed_count": guard_result["passed_count"], "failed_count": guard_result["failed_count"],
    }])[0]

    if case_id and case_context is not None:
        _record_case_retrieval_trace(client, case_id=case_id, expert_request_id=request_row["request_id"],
                                      context=case_context)
        client.update("geo_cases", {"case_id": f"eq.{case_id}"}, {"final_output": response_json})

    # completenessStatus/initialFailedChecks/repairAttempted/remainingFailedChecksは
    # geo_quality_evaluationsテーブルに対応カラムが無いため（DBマイグレーションなしの方針）、
    # 生成直後のこの戻り値・APIレスポンスにのみ含める（response_idで後から再取得した場合、
    # これらの生成時メタ情報は再現できない。既知の制約として完了報告に明記する）。
    quality_evaluation_ui = {
        **evaluation_row,
        "completeness_status": completeness_status,
        "initial_failed_checks": initial_failed_checks,
        "repair_attempted": repair_attempted,
        "remaining_failed_checks": remaining_failed_checks,
    }

    return {
        "request": request_row,
        "source_pack": source_pack_row,
        "response": response_row,
        "quality_evaluation": quality_evaluation_ui,
        "usage": usage_total,
        "execution_metadata": {
            "execution_mode": "monolithic", "profile": None, "mode": mode,
            "executed": {"stage1": None, "stage2_modules": None, "stage3_repair": repair_attempted},
            "routing_reason": "一括方式（段階実行なし）",
            "stage_metrics": None,
        },
        "persistent_preference_candidates": [],
    }


# =====================================================================================
# v1.2 段階実行(Staged Execution)
# =====================================================================================
# 既存run_expert_request（上記、一括生成方式）とは別関数として並存させる（削除しない）。
# Phase AではDB保存なしのプロトタイプとして比較実験（Hormuz 3ケース、docs/
# geopolitical-staged-execution-pilot-results.md）に使用し、Phase Bでrun_expert_request
# と同じテーブル（geo_expert_requests/geo_source_packs/geo_expert_responses/
# geo_quality_evaluations）への保存に対応した。どちらの方式を使うかは
# run_expert_request_with_mode()のexecution_mode（既定は環境変数GEO_EXPERT_EXECUTION_MODE、
# 未設定時"staged"）で切り替える。問題発生時は環境変数を"monolithic"に変更するだけで
# 即座にロールバックできる。

STAGED_PROMPT_VERSION = "geo-expert-v1.2-staged-phaseA"

# call_llm_structuredの戻り値mode別に、実際に消費した生Azure HTTP呼び出し回数
_CALLS_BY_MODE = {"structured_output": 1, "manual_json": 2, "manual_json_retry": 3}

# ai_client.pyのclient既定timeout（180秒）は全システム共通のため一律には変更しない
# （ユーザー指示、call-level override優先）。Stage1/Stage2/Repairは実データ投入後の
# 実測で単発143秒が確認されており、180秒では余裕が小さい。300秒を初期値として
# call-level overrideで渡し、Production-equivalent実行での実測を見て調整する
# （2026-09-18、geo_llm_common._call_llm_structured_oneのmanual JSON fallback例外正規化
# 修正と合わせて導入）。
_EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDS = 300.0

# v1.2 Knowledge投入量の削減（Core／モード別／必要時のみの分割）: 実データ計測で、Quickプロファイル
# でも常に全9ファイル（約29,700文字）が投入され、入力トークンの大半を占めていることが判明した
# （closeout時のE2E実測でprompt_tokens=26,340のほぼ全量）。06(Review OS)はdraft_review/
# outline_review（reviewモジュール）、10(Analyst Development)はanalyst_coaching
# （analyst_learningモジュール）でのみ必要なため、それ以外では投入しない。
# 一括方式(run_expert_request)は変更しない（既存動作を完全に維持する）。
_KNOWLEDGE_CORE_FILES = [
    "01_SYSTEM_POLICY.md", "03_KNOWLEDGE_THINKING_OS.md", "04_KNOWLEDGE_STAKEHOLDER_FORCE_MODEL.md",
    "05_KNOWLEDGE_HYPOTHESIS_EVIDENCE_PROTOCOL.md", "07_KNOWLEDGE_OUTPUT_TEMPLATES.md",
    "08_KNOWLEDGE_CASE_LIBRARY.md", "09_KNOWLEDGE_BUSINESS_IMPACT_MODEL.md",
]
_REVIEW_OS_FILE = "06_KNOWLEDGE_REVIEW_OS.md"
_ANALYST_DEVELOPMENT_FILE = "10_KNOWLEDGE_ANALYST_DEVELOPMENT.md"


def _knowledge_files_for_stage1(mode: str) -> list:
    files = list(_KNOWLEDGE_CORE_FILES)
    if mode in ("draft_review", "outline_review"):
        files.append(_REVIEW_OS_FILE)
    if mode == "analyst_coaching":
        files.append(_ANALYST_DEVELOPMENT_FILE)
    return files


def _knowledge_files_for_stage2(modules: list) -> list:
    files = list(_KNOWLEDGE_CORE_FILES)
    if "review" in modules:
        files.append(_REVIEW_OS_FILE)
    if "analyst_learning" in modules:
        files.append(_ANALYST_DEVELOPMENT_FILE)
    return files


class CallBudget:
    """Stage1生成・Stage2生成・Stage2 Recovery・部分修復を通じて共有する実Azure呼び出し
    回数の予算。geo_llm_common.call_llm_structuredのmax_attempts（1回の生成が内部で
    使ってよい生HTTPコール数）とは別の上位ゲート。呼び出し前にcan_afford()で確認し、
    呼び出し後に実際に使われた回数だけspend()で減らす。

    Phase 7-1C-R2(ユーザー指示): total=3→4。Stage2 scenario生成にStage1と同等の
    Recovery Retry（1回だけ）を追加したため、Stage1+Stage2+Stage2 Recovery+Repairの
    理論上限4回を許容する（Stage1 Recoveryまで同時に必要になる5コール目の稀な
    edge caseへは、実データで衝突が確認されるまで予防的に拡張しない——ユーザー指示）。"""

    def __init__(self, total: int = 4):
        self.total = total
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.total - self.used

    def can_afford(self, attempts: int) -> bool:
        return self.remaining >= attempts

    def spend(self, actual_calls: int) -> None:
        self.used += actual_calls


# Phase 7-1C-R2(Stage2 Recovery、ユーザー指示): Stage2 scenario生成の初回失敗のうち、
# 再試行に意味がある種類の失敗（JSON parse失敗・Schema検証失敗・transient transport/
# timeout系）だけを対象とする明示的allowlist。content policy拒否等、同一入力を再送しても
# 結果が変わらない種類の失敗（未知のexception_typeも含む、保守的に）は対象外——
# 無条件リトライしない（ユーザー指示#3「Retry対象」）。
_STAGE2_RECOVERABLE_EXCEPTION_TYPES = frozenset({
    "JSONDecodeError", "ValidationError",  # 構造化出力/手動JSON抽出のparse失敗、Schema検証失敗
    "APITimeoutError", "APIConnectionError", "InternalServerError", "ReadTimeout", "APIError",
})


def _is_recoverable_stage2_failure(exc: "geo_llm_common.GeoLLMError") -> bool:
    """Phase 7-1C-R2(ユーザー指示): Stage2 scenario生成の初回失敗がRecovery Retryの対象か
    判定する。exc.exception_typeが_STAGE2_RECOVERABLE_EXCEPTION_TYPESに含まれる場合のみTrue。
    exception_type不明（None）や未知の種類は、無条件リトライを避けるため対象外とする。"""
    return exc.exception_type in _STAGE2_RECOVERABLE_EXCEPTION_TYPES


def _derive_stakeholders_from_key_actors(stage1_data: dict) -> list:
    """既存互換の`stakeholders[]`をLLMに二重で書かせず、Stage1の`key_actor_profiles[]`から
    プログラム側で導出する（レビュー反映: 既存互換フィールドは可能な限りプログラム側で導出）。"""
    stakeholders = []
    for kap in stage1_data.get("key_actor_profiles") or []:
        forces = kap.get("driving_forces") or []
        supporting = "、".join(f["force"] for f in forces if f.get("direction") == "supporting" and f.get("force"))
        constraining = "、".join(f["force"] for f in forces if f.get("direction") == "constraining" and f.get("force"))
        stakeholders.append({
            "actor": kap.get("actor"),
            "purpose": kap.get("ultimate_objective"),
            "constraints_fears": constraining or None,
            "red_lines": kap.get("red_line"),
            "confidence": kap.get("confidence"),
            "position": kap.get("issue_objective"),
            "leverage": supporting or None,
            "ability_to_move_or_block": kap.get("ability_to_move") or kap.get("ability_to_block"),
        })
    return stakeholders


def _derive_sources_and_citations(source_pack: dict, case_context: dict | None) -> list:
    out = [{"result_type": "document", "result_id": d, "citation_label": None}
           for d in source_pack.get("document_ids") or []]
    out += [{"result_type": "evidence", "result_id": e, "citation_label": None}
            for e in source_pack.get("evidence_ids") or []]
    if case_context:
        out += [{"result_type": "knowledge_item", "result_id": i["knowledge_item_id"], "citation_label": i.get("title")}
                for i in case_context.get("included") or [] if i.get("knowledge_item_id")]
    return out


def _derive_knowledge_gaps_referenced(case_context: dict | None) -> list:
    if not case_context:
        return []
    return [{"gap_id": g["gap_id"], "gap_description": g.get("gap_description", "")}
            for g in case_context.get("gaps") or [] if g.get("gap_id")]


def _derive_relevant_items(case_context: dict | None, section: str) -> list:
    if not case_context:
        return []
    return [{"knowledge_item_id": i["knowledge_item_id"], "title": i.get("title", ""), "relevance": None}
            for i in case_context.get("included") or []
            if i.get("section") == section and i.get("knowledge_item_id")]


def _derive_assets_dependencies_leverage(foundation_pack: dict | None) -> list:
    if not foundation_pack:
        return []
    out = []
    for a in foundation_pack.get("7_assets") or []:
        out.append({"kind": "asset", "summary": a.get("description") or a.get("asset_type") or ""})
    for d in foundation_pack.get("9_vulnerabilities_and_constraints") or []:
        out.append({"kind": "dependency", "summary": d.get("description") or d.get("dependency_type") or ""})
    for lc in foundation_pack.get("8_existing_leverage_cards") or []:
        out.append({"kind": "leverage_card", "summary": lc.get("summary") or lc.get("title") or ""})
    return out


# Stage1・Stage2のSchemaに含まれず、マージ後に埋まらなければコード側でデフォルト補完する
# Optionalフィールド（LLMを再度呼ばず、nullまたは空配列で確定させる）
# Phase D-3(Holistic Reasoning Architecture Review): defer_scenario=Trueの場合、
# Call2(Scenario)自体が呼び出せない（CallBudget枯渇）、または呼び出しに失敗した場合の
# 決定的フォールバック。main_scenarioはmaster schema側で必須のため、欠落したまま
# 最終検証(jsonschema.validate)に到達させない（Human Strategic Interaction/Competing
# Hypothesisの後にのみ生成する、という設計変更に伴う既知の稀な失敗経路）。
_SCENARIO_FALLBACK_STUB = {
    "main_scenario": {
        "description": "Main Scenarioを生成できませんでした（Call予算不足または生成失敗）。",
        "confidence": "判断不能", "supporting_evidence": [],
    },
    "alternative_scenarios": [],
    "strongest_counterargument": "Main Scenario生成不可のため評価できません。",
    "additional_stakeholders": [],
}

_STAGE_OPTIONAL_DEFAULTS = {
    "initial_anomaly": None, "dynamics": None, "decisive_power_and_leverage": None,
    "weakening_information": None, "contradicting_evidence": None, "unknown_or_uncertain": None,
    "preference_action_gap_summary": None, "identified_knowledge_gaps": [],
    "audience_analysis": None, "media_source_context": None, "root_premise_check": None,
    "situation_actor_assessment": None, "investigation_questions": None,
    "review_findings": None, "analyst_coaching_notes": None,
    "additional_research": [], "executive_summary": None, "monitoring_indicators": [],
}


def _merge_staged_response(stage1_data: dict, stage2_data: dict | None, *, source_pack: dict,
                            case_context: dict | None, foundation_pack: dict | None) -> dict:
    """Stage1辞書をベースに、既存互換フィールドをプログラム的に導出して注入し、Stage2辞書
    （あれば）をマージする。stakeholders/action_substanceのみ特別扱い（追記/パッチ）、
    残りは非重複のキーのため単純な上書きで正しい。"""
    response = dict(stage1_data)
    response["stakeholders"] = _derive_stakeholders_from_key_actors(stage1_data)
    response["sources_and_citations"] = _derive_sources_and_citations(source_pack, case_context)
    response["knowledge_gaps_referenced"] = _derive_knowledge_gaps_referenced(case_context)
    response["relevant_precedents"] = _derive_relevant_items(case_context, "precedent")
    response["relevant_mechanisms"] = _derive_relevant_items(case_context, "mechanism")
    response["assets_dependencies_leverage"] = _derive_assets_dependencies_leverage(foundation_pack)

    if stage2_data:
        stage2_data = dict(stage2_data)
        additional_stakeholders = stage2_data.pop("additional_stakeholders", None)
        if additional_stakeholders:
            response["stakeholders"] = response["stakeholders"] + additional_stakeholders
        action_substance_patches = stage2_data.pop("key_actor_action_substance", None)
        if action_substance_patches:
            by_ref = {p["actor_ref"]: p["action_substance"] for p in action_substance_patches if p.get("actor_ref")}
            for kap in response.get("key_actor_profiles") or []:
                if kap.get("actor_ref") in by_ref:
                    kap["action_substance"] = by_ref[kap["actor_ref"]]
        # 残りのキー(audience_analysis/media_source_context/root_premise_check/
        # review_findings/analyst_coaching_notes/alternative_scenarios)は非重複のため単純上書きでよい
        response.update(stage2_data)

    _merge_investigation_questions_into_additional_research(response)

    return response


def _normalize_research_question_fingerprint(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


def _merge_investigation_questions_into_additional_research(response: dict) -> None:
    """Session2 Reasoning Upgrade（Phase4）: investigation_questions[]のうち
    can_current_evidence_answer=falseの項目（真に外部情報が不足しているknowledge_gap相当）
    のみを、決定的Python処理でadditional_research[]へ反映する（LLMに二重生成させない、新規
    Azure呼び出しも増やさない）。重複判定は文字列完全一致ではなく、trim・lower化・空白正規化
    した決定的Fingerprintで行う（意味的なLLM dedupeは行わない）。"""
    questions = response.get("investigation_questions") or []
    if not questions:
        return
    existing = list(response.get("additional_research") or [])
    existing_fingerprints = {_normalize_research_question_fingerprint(r.get("question")) for r in existing}
    for q in questions:
        if q.get("can_current_evidence_answer"):
            continue
        fingerprint = _normalize_research_question_fingerprint(q.get("question"))
        if not fingerprint or fingerprint in existing_fingerprints:
            continue
        existing.append({
            "question": q.get("question"), "related_hypothesis": None, "recommended_sources": None,
            "priority": None, "rationale": q.get("rationale"),
        })
        existing_fingerprints.add(fingerprint)
    response["additional_research"] = existing


def _rebind_or_unresolve_invalid_entity_references(
        client, response: dict, *, stage1_visible_entity_ids: set) -> list:
    """Phase 6A-4(Repair/Rebinding、ユーザー指示): key_actor_profiles[].entity_idが
    Stage1へ実際に提示したEntity集合（stage1_visible_entity_ids）に含まれない場合
    （geo_expert_quality_guard._stakeholder_entity_resolved_check「捏造の可能性」相当）、
    以下の決定的手順のみで対処する。LLMを再度呼ばない。DBへの新規Entity作成は行わない。
    Fuzzy matchのみでの書き換えは行わない（名前の完全な部分一致のみを根拠にする）。

    2026-09-19実データ監査（TSMC Case）で根本原因を確認済み: Call1 user_promptは
    Source Pack内の各文書を`[document_id: <uuid>]`、Actor Intelligence Pack内の各主体を
    `entity_id: <uuid>`という視覚的に類似したラベル形式で提示しており、LLMが近傍の
    document_idをentity_idとして誤って転記する実例を確認した（trade.gov.tw文書の
    document_idが、その文書内容から自ら命名した"台湾貿易当局"actorのentity_idとして
    誤用された）。本関数はこの種の誤参照を、LLM Hallucinationとして黙って握り潰すのでも
    自動でDBへ新規登録するのでもなく、決定的な検証手順で仕分ける:

    1. entity_idがgeo_entitiesに実在するかを確認する。
       - 実在しない（document_id等、別テーブルのIDを混同したケースを含む）
         → unresolved（entity_id=Noneへ書き換え、actor_ref/actor名の自由記述はそのまま
         保持——Actor自体の存在を否定するのではなく、DB上のIdentityへの結び付けだけを
         取り消す）。
       - 実在する（DB全体には存在するが、今回Stage1へは提示しなかった別のEntity）→ 2へ。
    2. 実在した場合、そのEntityのcanonical_name/name_en/name_ja/local_nameいずれかが、
       当該key_actor_profiles.actor（Actor名の自由記述）と大文字小文字を無視した部分一致を
       するかを確認する。
       - 一致する（名前が一意に対応することをEvidenceとして扱える）→ rebind（entity_idは
         そのまま維持）。
       - 一致しない（別のEntityである可能性を否定できない、fuzzy matchのみでの断定を
         避ける）→ unresolved。

    戻り値: 各対応の記録一覧（telemetry用、execution_metadataへ格納する）。"""
    touched = []
    for kap in response.get("key_actor_profiles") or []:
        entity_id = kap.get("entity_id")
        if not entity_id or entity_id in stage1_visible_entity_ids:
            continue
        actor_label = (kap.get("actor") or kap.get("actor_ref") or "").lower()
        entity = geo_entity_service.get_entity(client, entity_id)
        if entity is None:
            kap["entity_id"] = None
            touched.append({
                "actor_ref": kap.get("actor_ref"), "original_invalid_reference": entity_id,
                "resolved_entity_id": None, "resolution_method": "not_found_in_geo_entities",
                "confidence": None,
            })
            continue
        candidate_names = [entity.get(f) for f in
                            ("canonical_name", "name_en", "name_ja", "local_name")]
        name_matched = bool(actor_label) and any(
            name and (name.lower() in actor_label or actor_label in name.lower())
            for name in candidate_names if name)
        if name_matched:
            touched.append({
                "actor_ref": kap.get("actor_ref"), "original_invalid_reference": entity_id,
                "resolved_entity_id": entity_id, "resolution_method": "name_matched_existing_entity",
                "confidence": "high",
            })
        else:
            kap["entity_id"] = None
            touched.append({
                "actor_ref": kap.get("actor_ref"), "original_invalid_reference": entity_id,
                "resolved_entity_id": None, "resolution_method": "name_mismatch_unresolved",
                "confidence": None,
            })
    return touched


def _resolve_entity_identity_consistency(response: dict, *, stage1_visible_entity_names: dict) -> list:
    """Phase 6E-5(Entity Identity Integrity、ユーザー指示): _rebind_or_unresolve_invalid_
    entity_referencesは、entity_idがstage1_visible_entity_idsに含まれない場合のみ検証し、
    含まれる場合は無条件でスキップする。これは「entity_idそのものは今回提示範囲内で正当
    だが、LLMがそのIDに別のActor名を紐付けて出力した」というIdentity Mismatch（例:
    entity_id=Xi Jinpingの正規ID、actor名="Taiwan trade authority"）を検出できない
    構造的な穴だった（Phase 6E-1/6E-2の実データ監査では実例は確認されなかったが、
    Root Causeに関わらず低リスクで追加可能なInvariantとしてユーザーが指定）。

    手順: entity_idがstage1_visible_entity_names（Stage1へ提示した各entity_idの
    canonical_name）に記録されている場合のみ判定する（名前が分からないentity_idは
    判定不能としてスキップ、無理な推測はしない）。canonical_nameと、LLMが書いた
    actor（自由記述）が大文字小文字を無視した部分一致をするかを確認する。
    - 一致する → retain（何もしない、telemetryにも記録しない）。
    - 一致しない → unresolved（entity_id=Noneへ書き換え。fuzzy推測での別ID差し替えは
      行わない、常にnullへ倒す）。

    戻り値: Identity Mismatchが検出されたitemのみのtelemetry一覧（entity_id/
    model_supplied_name/canonical_name/identity_match_status/resolution_methodを含む、
    異常のみ記録——既存のentity_reference_resolutions等と同じ方針）。"""
    mismatches = []
    for kap in response.get("key_actor_profiles") or []:
        entity_id = kap.get("entity_id")
        if not entity_id or entity_id not in stage1_visible_entity_names:
            continue
        canonical_name = stage1_visible_entity_names[entity_id]
        actor_label = (kap.get("actor") or kap.get("actor_ref") or "")
        name_matched = bool(actor_label) and (
            canonical_name.lower() in actor_label.lower() or actor_label.lower() in canonical_name.lower())
        if not name_matched:
            kap["entity_id"] = None
            mismatches.append({
                "entity_id": entity_id, "model_supplied_name": actor_label,
                "canonical_name": canonical_name, "identity_match_status": "mismatch",
                "resolution_method": "identity_mismatch_unresolved",
            })
    return mismatches


def _controller_candidate_has_causal_role(profile: dict) -> bool:
    """Phase 7-1A: geo_expert_quality_guard._controller_actor_has_causal_roleと
    意図的に同じ判定基準を保持する（モジュール間でprivateシンボルをimportするのは
    避け、値をここに複製する）。"""
    return bool(
        profile.get("event_role") or profile.get("driving_forces")
        or profile.get("ability_to_move") or profile.get("ability_to_block")
        or profile.get("available_leverage") or profile.get("red_line")
    )


def _controller_label_matches_profile(label: str, profile: dict) -> bool:
    """Phase 7-1A: geo_expert_quality_guard._controller_term_matches_identityと同じ
    alias/粒度差許容ロジックを保持する（例: "台湾当局"⊂"台湾の貿易・産業当局（経済部
    国際貿易署など）"を一致とみなす。モジュール間でprivateシンボルをimportするのは
    避け、同じ判定を複製する）。単純なsubstring方式だけに戻すと、6C-0で確認したTSMC
    Caseのような粒度差のある自己命名actor_refをUSEDと判定できず、DATA_READINESS_
    LIMITEDへ誤分類してしまう。"""
    own_identity = " ".join(filter(None, [profile.get("actor"), profile.get("formal_role")]))
    if not own_identity or not label:
        return False
    own_identity_lower = own_identity.lower()
    label_lower = label.lower()
    if label_lower in own_identity_lower or own_identity_lower in label_lower:
        return True
    prefix, suffix = "", ""
    for s in sorted(_CONTROLLER_CANDIDATE_INSTITUTION_SUFFIXES, key=len, reverse=True):
        if label.endswith(s):
            prefix, suffix = label[: -len(s)], s
            break
    if not suffix or suffix not in own_identity:
        return False
    return not prefix or prefix in own_identity


def classify_controller_intelligence_utilization(
        *, entity_backed_candidates: list, label_only_candidates: list, response: dict) -> list:
    """Phase 7-1A Telemetry(ユーザー指示): Controller/Rich Actor Candidateごとに、
    最終response_jsonから決定的にAVAILABLE/USED/CONSIDERED_NOT_MATERIAL/UNAVAILABLE/
    DATA_READINESS_LIMITEDを判定する。新しいLLM出力fieldは追加しない（既存の
    key_actor_profiles/key_point_assessment等から後段で判定するのみ、無理にLLMへ
    新fieldを出させない）。

    entity_backed_candidates: [{"entity_id":..., "canonical_name":...}, ...]
    （Foundation Pack/Actor Intelligence Pack/Institution Intelligence Packで実際に
    Call1へ提示した主体）。
    label_only_candidates: _build_institutional_controller_contextの戻り値
    （canonical_entity_id無しのCandidate、Runtime-onlyで提示のみ）。

    判定順序（保守的: 明示的根拠が無い限りCONSIDERED_NOT_MATERIALは付与しない）:
    1. USED: key_actor_profilesの中に、そのCandidateの名前・entity_idに対応する
       Entryが存在し、かつ因果的役割（driving_forces等）を持つ。entity_id無しの
       Candidateでも、自己命名actor_ref＋因果的役割込みで存在すればUSEDとする
       （TSMCの台湾貿易当局のような既存の正しいパターンを許容する）。
    2. CONSIDERED_NOT_MATERIAL: USEDでなく、key_point_assessment.rejected_
       candidates[].why_not_primaryにCandidate名への言及がある（明示的な理由づけが
       存在する場合のみ、推測では付与しない）。
    3. DATA_READINESS_LIMITED: USED/CONSIDERED_NOT_MATERIALでなく、かつentity_id
       を持たない（そもそもcanonical entityがDBに存在しない）。
    4. AVAILABLE: USED/CONSIDERED_NOT_MATERIAL/DATA_READINESS_LIMITEDのいずれでも
       ないが、entity_idを持ち、response中のどこか（control_structure_summary/
       hinge_structure等のnarrative field）に言及の痕跡がある。
    5. UNAVAILABLE: entity_idを持つのに、response中のどこにも痕跡が一切無い
       （提示したが完全に無視された）。
    """
    kpa = response.get("key_point_assessment") or {}
    narrative_text = " ".join(filter(None, [
        kpa.get("control_structure_summary"),
        (kpa.get("hinge_structure") or {}).get("primary_hinge"),
        " ".join((kpa.get("hinge_structure") or {}).get("secondary_hinges") or []),
    ]))
    rejected_reasons = " ".join(
        (rc.get("why_not_primary") or "") for rc in kpa.get("rejected_candidates") or [])
    profiles = response.get("key_actor_profiles") or []

    results = []

    def classify_one(label: str, entity_id: str | None):
        used = any(
            (entity_id and p.get("entity_id") == entity_id and _controller_candidate_has_causal_role(p))
            or (not entity_id and _controller_label_matches_profile(label, p)
                and _controller_candidate_has_causal_role(p))
            for p in profiles
        )
        if used:
            return "USED"
        if label and label.lower() in rejected_reasons.lower():
            return "CONSIDERED_NOT_MATERIAL"
        if not entity_id:
            return "DATA_READINESS_LIMITED"
        if label and label.lower() in narrative_text.lower():
            return "AVAILABLE"
        return "UNAVAILABLE"

    for c in entity_backed_candidates:
        status = classify_one(c.get("canonical_name"), c.get("entity_id"))
        results.append({
            "controller_label": c.get("canonical_name"), "canonical_entity_id": c.get("entity_id"),
            "status": status,
        })
    for c in label_only_candidates:
        status = classify_one(c.get("controller_label"), None)
        results.append({
            "controller_label": c.get("controller_label"), "canonical_entity_id": None,
            "status": status,
        })
    return results


def _detect_unused_rich_actor_intelligence(actor_intelligence_pack: dict | None, response: dict) -> list:
    """Phase 6A-1(Saudi-Israel Actor Routing、ユーザー指示)の調査結果を踏まえた
    Observability専用の追加。2026-09-19実データ調査: Saudi-Israel Caseで、Trump
    （entity_id=c93580fd...、actor_intelligence_status=partial、core_profile付きで
    Call1へ明示的に提示済み）が最終key_actor_profilesのどのentity_idとしても使われず、
    代わりにUnited States（entity_id=671eeb94...、actor_intelligence_status=
    insufficient、汎用制度Entity）が使われていた事象を確認した。Actor Intelligence
    Pack自体は正しくRich Dataを提示しており、Retrieval/Wiring側の欠陥ではない
    ——「複数のEntityが概念上同じActor枠を表しうる場合にどちらを選ぶか」はCall1自身の
    生成時判断であり、6A（Wiring修正のみ、Reasoning Prompt変更禁止）のScopeでは内容を
    書き換えない。本関数は「Curated/partial以上のActor Intelligenceが利用可能だった
    Entityのentity_idが、最終key_actor_profilesのどのentity_idとしても使われなかった」
    ケースを検出しtelemetryへ記録するのみ（内容は一切変更しない、Phase 6C Reasoning
    Weakness Reclassificationの判断材料として残す）。"""
    if not actor_intelligence_pack:
        return []
    used_entity_ids = {kap.get("entity_id") for kap in (response.get("key_actor_profiles") or [])
                        if kap.get("entity_id")}
    unused = []
    for ka in actor_intelligence_pack.get("key_actors") or []:
        if (ka.get("actor_intelligence_status") in ("partial", "sufficient")
                and ka.get("entity_id") not in used_entity_ids):
            unused.append({
                "entity_id": ka.get("entity_id"), "canonical_name": ka.get("canonical_name"),
                "actor_intelligence_status": ka.get("actor_intelligence_status"),
            })
    return unused


def _apply_stage_optional_defaults(response: dict) -> None:
    for key, default in _STAGE_OPTIONAL_DEFAULTS.items():
        response.setdefault(key, default)


def _validate_final_response(response: dict) -> list:
    """最終応答をマスターSchema(RESPONSE_SCHEMA)で検証する。

    「最低1件」(minItems)の配列が空なだけの不適合（例: レポートを読み比べるdraft_reviewで
    AIがretraction_conditionsを空で返す）は、呼び出し全体を失敗にせず、空のままの項目の
    パス一覧を返して続行する——意味的な要件はquality guard（has_retraction_conditions等）が
    既に「失敗」として記録しているため。AIが書いていない内容は補わない。
    それ以外の不適合（型違い・必須項目の欠落など）は従来どおり例外にする。"""
    schema = geo_expert_schema.RESPONSE_SCHEMA
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = list(validator.iter_errors(response))
    hard = [e for e in errors if not (e.validator == "minItems" and e.instance == [])]
    if hard:
        raise jsonschema.exceptions.best_match(hard)
    return [".".join(str(p) for p in e.absolute_path) for e in errors]


def _strip_nulls_disallowed_by_master_schema(response: dict) -> None:
    """Stage1では意味上任意のためnull許容にした項目（例: executive_summary）でも、
    マスターSchema(RESPONSE_SCHEMA、後方互換の正本)側がnullを許容しない型で定義している
    場合がある。該当プロパティはマスターSchemaのrequiredに含まれないため、
    Noneのままキーごと省略しても後方互換上問題ない。"""
    props = geo_expert_schema.RESPONSE_SCHEMA["properties"]
    for key in list(response.keys()):
        if response[key] is not None:
            continue
        prop_schema = props.get(key)
        if not prop_schema:
            continue
        t = prop_schema.get("type")
        allows_null = t == "null" or (isinstance(t, list) and "null" in t)
        if not allows_null:
            del response[key]


# ---- Foundation Context Resolver: Actor Runtime Activation（ユーザー指示Phase D） ----
# geo_case_issues/geo_entity_issuesという手動Curated Linkが無いCaseでも、Actor
# Intelligenceが機能するようにする。DBへのauto-link永続化は一切行わない
# （このRequest処理の間だけ有効なRuntime Candidateであり、次のRequestでは毎回
# 再解決する）。新しいSemantic検索・Embeddingは導入しない。
#
# 実装上の発見（Phase D初回実装で判明、テストで確認済み）: query_text/keyword→
# geo_entities ILIKE検索という方向（_extract_query_keywords + _relevant_rows_by_
# keywords、Foundation Context Builder v1と同じ既存パターン）は、日本語クエリの
# Kanji連続語抽出が貪欲マッチのため、「台湾海峡周辺」のような複合語1トークンに
# なってしまい、"Taiwan"/"China"のような短い固有名詞が独立したKeywordとして
# 抜けてしまう（実際にTaiwan Strait Caseで0件になることを確認）。また、geo_entities
# は現状20件のみ（他のテーブルと比べ極端に小規模）でJapanese name（name_ja）が
# 殆ど未設定。そのため、Keyword抽出方向ではなく、Entity側（件数が少ない）から
# 「その名前がテキスト中に含まれるか」を直接判定する逆方向マッチングを使う
# （件数が少ないため全件取得しても軽い。将来Entity数が大きく増えた場合は
# 再検討が必要）。
_RUNTIME_ACTOR_ENTITY_NAME_FIELDS = ["canonical_name", "name_en", "name_ja", "local_name"]
_RUNTIME_ACTOR_MIN_NAME_LENGTH = 2


def resolve_runtime_actor_candidates(client, *, case_id: str | None, query_text: str,
                                      source_pack: dict | None) -> dict:
    """Situation-driven Actor Activation。優先順位（ユーザー指示Phase B/D）:
    1. Curated: case_id→geo_case_issues→geo_entity_issues（既存経路、最優先。見つかった
       entityは"curated"として常に含み、以下のRuntime解決で上書きしない）。
    2. Situation-relevant: query_text＋Source Pack本文（結合テキスト）の中に、既存の
       geo_entitiesの名前（canonical_name/name_en/name_ja/local_name）が部分文字列として
       現れるEntityを"situation_relevant"として追加する（query-explicitとsource-pack-
       derivedを同一の決定的処理へ統合。Source Pack本文には既にKeyword-matched文書・
       Eventのactor名が含まれているため、別途geo_eventsへ再クエリしなくてもカバーできる）。
    3. 何も見つからない場合はStructure-first（Actorを無理に作らない、空のまま返す）。
    戻り値: {"entity_ids": set, "activation_method": {entity_id: "curated"|"situation_relevant"}}
    ——DBへは一切書き込まない。"""
    activation_method: dict[str, str] = {}

    if case_id:
        issue_ids = _relevant_case_issue_ids(client, case_id)
        curated_rows = client.select("geo_entity_issues", {"select": "entity_id",
                                                             "issue_id": f"in.({','.join(issue_ids)})"}) \
            if issue_ids else []
        for row in curated_rows:
            entity_id = row.get("entity_id")
            if entity_id:
                activation_method[entity_id] = "curated"

    combined_lower = f"{query_text or ''}\n{(source_pack or {}).get('text') or ''}".lower()
    if combined_lower.strip():
        all_entities = client.select("geo_entities", {"select": "*", "limit": "2000"})
        for entity in all_entities:
            entity_id = entity.get("entity_id")
            if not entity_id or entity_id in activation_method:
                continue
            if entity.get("entity_type") not in geo_actor_intelligence_service.ACTOR_ENTITY_TYPES:
                continue
            names = [entity.get(f) for f in _RUNTIME_ACTOR_ENTITY_NAME_FIELDS]
            if any(name and len(name) >= _RUNTIME_ACTOR_MIN_NAME_LENGTH and name.lower() in combined_lower
                   for name in names):
                activation_method[entity_id] = "situation_relevant"

    return {"entity_ids": set(activation_method.keys()), "activation_method": activation_method}


def _relevant_case_issue_ids(client, case_id: str) -> list:
    """geo_case_issuesのみを見る決定的ヘルパー（Runtime Resolverから使う。既存
    geo_retrieval_service.get_issue_ids_for_caseと同じ問い合わせだが、geo_expert_engine側
    からgeo_actor_intelligence_service経由の間接importを増やさないための直接実装）。"""
    rows = client.select("geo_case_issues", {"select": "issue_id", "case_id": f"eq.{case_id}"})
    return sorted({row["issue_id"] for row in rows if row.get("issue_id")})


# ---- Context Availability / Activation Telemetry（ユーザー指示Phase C） ----
# Call1直前で、6種のContextそれぞれについてstatus/item_count/token estimate/
# activation_method/evidence_ids/call1_includedを決定的に記録する（追加LLM呼び出し
# なし、Reasoning Behaviorは変更しない）。既存execution_metadataと同じ格納パターン
# （geo_audit_logs.detail、新規テーブル・新規カラムなし）を再利用する。「0件なのに
# 何も残さない」というSilent Context Failureを無くすことが目的——status自体は
# 判定結果であり、EMPTY/NOT_APPLICABLEも正常な記録として残す。
_FC_STATUS_READY = "READY"
_FC_STATUS_PARTIAL = "PARTIAL"
_FC_STATUS_EMPTY = "EMPTY"
_FC_STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
_FC_STATUS_NOT_CONFIGURED = "NOT_CONFIGURED"

_PROVENANCE_ID_PATTERN = re.compile(r"\[(document|event):([0-9a-fA-F-]{8,})\]")


def _estimate_tokens(text: str) -> int:
    """Telemetry用の粗い決定的推定（chars//3）。課金・contextウィンドウ計算用の
    正確な値ではなく、Phase F-0比較実験でCondition間の相対的な大小を見るための目安。"""
    return len(text or "") // 3


def _extract_provenance_ids_from_text(text: str) -> list:
    """Situation Trajectory等、文字列注入のみでdocument_id/event_idを個別に保持していない
    Contextから、レンダリング済みテキストを決定的に逆解析してProvenance IDを復元する
    （build_foundation_context自体は変更しない、Telemetry専用の後処理）。"""
    return sorted({m.group(2) for m in _PROVENANCE_ID_PATTERN.finditer(text or "")})


def build_foundation_context_status(
        *, mode: str, profile: str, source_pack: dict, foundation_context_text: str,
        foundation_context_diagnostics: dict | None, case_id: str | None, foundation_pack: dict | None,
        structural_text: str, actor_intelligence_pack: dict | None, actor_intelligence_text: str,
        runtime_actor_candidates: dict | None, media_intelligence_pack: dict | None,
        media_intelligence_text: str,
        actor_intelligence_effective_enabled: bool | None = None) -> dict:
    """6種のFoundation Context Statusを組み立てる（決定的処理、追加LLM呼び出しなし）。

    actor_intelligence_effective_enabled: 呼び出し元が実際にActor Intelligence Packを
    構築する際に使った、operation-scoped override解決後の実効値（2026-09-25追加）。
    省略時はglobal env flag（actor_intelligence_enabled()）を直接参照する既存挙動を
    維持する——呼び出し元を全て更新済みのため、実運用では省略されない想定。"""
    status: dict = {}
    if actor_intelligence_effective_enabled is None:
        actor_intelligence_effective_enabled = actor_intelligence_enabled()

    # 1. Source（常時実行、Flag無し）
    source_text = source_pack.get("text") or ""
    source_ready = source_text and source_text != "（参照できる情報がありません）"
    status["source"] = {
        "status": _FC_STATUS_READY if source_ready else _FC_STATUS_EMPTY,
        "item_count": len(source_pack.get("document_ids") or []) + len(source_pack.get("event_ids") or []),
        "chars": len(source_text), "estimated_tokens": _estimate_tokens(source_text),
        "activation_method": "keyword_match",
        "evidence_ids": sorted((source_pack.get("document_ids") or []) + (source_pack.get("event_ids") or [])),
        "call1_included": True,
    }

    # 2. Situation Trajectory（Flag: foundation_context_v1_enabled、既定OFF）
    # Trajectory Production Integration Phase T-1（ユーザー指示、2026-09-22）: telemetryに
    # as_ofを明示し、READY/EMPTYの2値だけでなく「候補はあったが一部除外されて薄くなった」
    # PARTIALも区別する（rejected_future/rejected_stub_or_syntheticのいずれかが発生した
    # 上でkept_total>0の場合。除外自体は正しい安全動作であり、PARTIALはfailureではなく
    # 「Evidenceが部分的」という状態を示すのみ）。
    if not foundation_context_v1_enabled():
        status["situation_trajectory"] = {"status": _FC_STATUS_NOT_CONFIGURED, "item_count": 0, "chars": 0,
                                            "estimated_tokens": 0, "activation_method": None,
                                            "evidence_ids": [], "call1_included": False, "as_of": None}
    else:
        diag = foundation_context_diagnostics or {}
        kept_total = diag.get("kept_total", 0)
        traj_ready = bool(foundation_context_text) and kept_total > 0
        had_exclusions = bool(diag.get("rejected_future") or diag.get("rejected_stub_or_synthetic"))
        if not traj_ready:
            traj_status = _FC_STATUS_EMPTY
        elif had_exclusions:
            traj_status = _FC_STATUS_PARTIAL
        else:
            traj_status = _FC_STATUS_READY
        status["situation_trajectory"] = {
            "status": traj_status,
            "item_count": kept_total,
            "chars": len(foundation_context_text or ""),
            "estimated_tokens": _estimate_tokens(foundation_context_text),
            "activation_method": "as_of_retrieval" if traj_ready else None,
            "evidence_ids": _extract_provenance_ids_from_text(foundation_context_text),
            "call1_included": traj_ready,
            "as_of": diag.get("as_of_date"),
        }

    # 3. Actor Intelligence（Flag: actor_intelligence_enabled、既定OFF。Phase D以降は
    # case_id不要——Runtime Activationで補う）
    if not actor_intelligence_effective_enabled:
        status["actor_intelligence"] = {"status": _FC_STATUS_NOT_CONFIGURED, "item_count": 0, "chars": 0,
                                          "estimated_tokens": 0, "activation_method": [],
                                          "evidence_ids": [], "call1_included": False}
    else:
        key_actors = (actor_intelligence_pack or {}).get("key_actors") or []
        methods_by_entity = (runtime_actor_candidates or {}).get("activation_method") or {}
        methods_used = sorted({methods_by_entity.get(ka.get("entity_id"), "curated_or_relationship_expansion")
                                for ka in key_actors}) if key_actors else []
        actor_ready = bool(actor_intelligence_text)
        status["actor_intelligence"] = {
            "status": _FC_STATUS_READY if actor_ready else _FC_STATUS_EMPTY,
            "item_count": len(key_actors), "chars": len(actor_intelligence_text),
            "estimated_tokens": _estimate_tokens(actor_intelligence_text),
            "activation_method": methods_used,
            "evidence_ids": sorted((actor_intelligence_pack or {}).get("known_ids") or []),
            "call1_included": actor_ready,
        }

    # 4. Relationship Intelligence（Phase E、ユーザー指示: geo_relationshipsだけで完了扱い
    # しない）。Actor Intelligence Packのkey_actors[].relevant_relationships（geo_relationships
    # 由来）とdirectional_relationship_perceptions（Phase G、actor_profile由来）の両方を
    # 合算する。Foundation Pack（Issue Link経路）の6_relationships_and_dependencies /
    # 8_existing_leverage_cardsは別の取得経路のため、item_countへ別途加算するが
    # activation_methodは"foundation_pack_issue_linked"と明示して区別する。
    if not actor_intelligence_effective_enabled:
        status["relationship_intelligence"] = {"status": _FC_STATUS_NOT_CONFIGURED, "item_count": 0,
                                                  "estimated_tokens": 0, "activation_method": [],
                                                  "evidence_ids": [], "call1_included": False,
                                                  "sources": []}
    else:
        key_actors = (actor_intelligence_pack or {}).get("key_actors") or []
        rel_count = 0
        rel_ids = set()
        rel_methods = set()
        for ka in key_actors:
            rels = ka.get("relevant_relationships") or []
            drps = ka.get("directional_relationship_perceptions") or []
            if rels or drps:
                rel_methods.add(methods_by_entity.get(ka.get("entity_id"), "curated_or_relationship_expansion"))
            rel_count += len(rels) + len(drps)
            rel_ids.update(r.get("relationship_id") for r in rels if r.get("relationship_id"))
        fp_relationships = (foundation_pack or {}).get("6_relationships_and_dependencies") or {}
        fp_rel_list = fp_relationships.get("relationships") or []
        fp_leverage = (foundation_pack or {}).get("8_existing_leverage_cards") or []
        if fp_rel_list or fp_leverage:
            rel_methods.add("foundation_pack_issue_linked")
        rel_count += len(fp_rel_list) + len(fp_leverage)
        sources = []
        if any(ka.get("relevant_relationships") for ka in key_actors):
            sources.append("geo_relationships(actor_intelligence)")
        if any(ka.get("directional_relationship_perceptions") for ka in key_actors):
            sources.append("directional_relationship_perceptions(phase_g)")
        if fp_leverage:
            sources.append("leverage_card(foundation_pack)")
        if fp_rel_list:
            sources.append("geo_relationships(foundation_pack)")
        status["relationship_intelligence"] = {
            "status": _FC_STATUS_READY if rel_count > 0 else _FC_STATUS_EMPTY,
            "item_count": rel_count, "estimated_tokens": _estimate_tokens(json.dumps(
                [ka.get("relevant_relationships") for ka in key_actors], ensure_ascii=False, default=str)),
            "activation_method": sorted(rel_methods), "evidence_ids": sorted(rel_ids),
            "call1_included": rel_count > 0, "sources": sources,
        }

    # 5. Media / Perception（2機構、ユーザー指示Phase A/F確認済み）。Mechanism A
    # （build_media_intelligence_pack、Flag制御、Call1前に確定）とMechanism B
    # （media_context Stage2モジュール、Call1のOutput Schema自体なので中身はCall1後
    # にしか分からない——ここでは「このRequestでmedia_context moduleが要求されるか」
    # のみtelemetry化する）を分けて記録する。
    mechanism_b_will_fire = "media_context" in geo_expert_schema.resolve_stage2_modules(mode, profile)
    if not media_intelligence_enabled():
        mechanism_a_status = {"status": _FC_STATUS_NOT_CONFIGURED, "item_count": 0, "chars": 0,
                                "estimated_tokens": 0, "call1_included": False}
    else:
        patterns = (media_intelligence_pack or {}).get("patterns") or []
        profiles_ = (media_intelligence_pack or {}).get("profiles") or []
        mechanism_a_status = {
            "status": _FC_STATUS_READY if media_intelligence_text else _FC_STATUS_EMPTY,
            "item_count": len(patterns) + len(profiles_), "chars": len(media_intelligence_text),
            "estimated_tokens": _estimate_tokens(media_intelligence_text),
            "call1_included": bool(media_intelligence_text),
        }
    status["media_perception"] = {
        "mechanism_a_pattern_profile": mechanism_a_status,
        "mechanism_b_media_context_module": {
            "status": _FC_STATUS_NOT_APPLICABLE if not mechanism_b_will_fire else _FC_STATUS_READY,
            "will_fire_this_request": mechanism_b_will_fire,
            "note": "Observation/Assessment/Narrative件数はCall1のOutputでしか確定しないため、"
                    "Call1直前時点ではモジュールが要求されるか否かのみを記録する。",
        },
    }

    # 6. Structural Context（Foundation Pack、Issue Link依存。case_id無しはNOT_APPLICABLE）
    if not case_id:
        status["structural_context"] = {"status": _FC_STATUS_NOT_APPLICABLE, "item_count": 0, "chars": 0,
                                           "estimated_tokens": 0, "activation_method": None,
                                           "evidence_ids": [], "call1_included": False}
    else:
        fp = foundation_pack or {}
        item_count = (len(fp.get("2_relevant_entities") or []) + len(fp.get("3_stakeholder_map") or [])
                      + len(fp.get("14_existing_claims_and_evidence") or [])
                      + len((fp.get("6_relationships_and_dependencies") or {}).get("relationships") or [])
                      + len(fp.get("8_existing_leverage_cards") or []))
        status["structural_context"] = {
            "status": _FC_STATUS_READY if item_count > 0 else _FC_STATUS_EMPTY,
            "item_count": item_count, "chars": len(structural_text),
            "estimated_tokens": _estimate_tokens(structural_text),
            "activation_method": "curated_issue_link",
            "evidence_ids": sorted({c.get("knowledge_item_id") for c in
                                     (fp.get("14_existing_claims_and_evidence") or [])
                                     if c.get("knowledge_item_id")}),
            "call1_included": bool(structural_text),
        }

    return status


def run_expert_request_staged(client, azure_client, model: str, mode: str, theme_id: str | None,
                               query_text: str, draft_text: str | None = None, *,
                               case_id: str | None = None, profile: str = "standard",
                               media_intelligence_statuses: tuple = ("active",),
                               actor_profile_lookup_statuses: tuple = ("active",),
                               actor_profile_retrieval_status: str | None = "available",
                               extra_document_ids: list | None = None,
                               as_of_date: str | None = None,
                               source_pack_document_limit: int | None = None,
                               persist: bool = True, persist_case_side_effects: bool = True,
                               actor_intelligence_enabled: bool | None = None) -> dict:
    """v1.2 段階実行(Staged Execution) Phase Aプロトタイプ。Stage0(決定的、既存関数を再利用)
    →Stage1(常に1回)→Stage2(条件付き、モジュール合成で最大1回)→Evidence IDスクラブ
    （決定的、LLMを呼ばない）→部分修復(該当Stageのみ、最大1回)という流れで、実Azure呼び出し
    合計を必ず3回(CallBudget)以内に収める。

    persist: 既定True（既存呼び出し元と同じ本番互換の挙動、DBへ書き込む）。
    Falseの場合はevaluation/dry-run用途で、Case Context・Foundation Pack・Actor/Institution/
    Media Intelligence PackのRetrieval（select）は通常通り実行するが、以下のDB書き込みを
    一切行わない: geo_expert_requests/geo_source_packs/geo_expert_responses/
    geo_quality_evaluationsへのinsert、geo_cases.final_outputのupdate、
    geo_retrieval_runs/geo_retrieval_results・geo_knowledge_items.usage_count/last_used_at
    （_record_case_retrieval_trace経由）、geo_audit_logsへのaudit insert、
    persistent_preference_candidateの生成・書き込み。戻り値の形（request/source_pack/
    response/quality_evaluation等のキー）はpersist=Trueと同一に保つため、実DBの代わりに
    メモリ上でのみ発行したUUIDを使う（2026-09-06修正: 以前のdocstringは「DBへの保存は
    行わない」と誤って記載していたが、実装は常にDBへ書き込んでいた。比較実験・評価用途では
    必ずpersist=Falseを明示指定すること——docs/expert-ai-validation/
    eguchi_db_write_incident_2026-09-06/incident_report.md参照、この誤りにより実際に
    2Case（M9/Case1 Trump-Xi）のgeo_cases.final_outputが上書きされ、手動復元が必要になった
    実インシデントがある）。

    source_pack_document_limit: 既定None（build_source_pack自身の既定10件に委ねる）。
    Trajectory Controlled Truncation-Recovery Experiment（ユーザー指示、2026-09-22）
    専用のパラメータ——通常Source Packを意図的に絞り、Foundation Context Builder v1が
    truncationで失われた候補を回収できるかを制御実験するためのもの。通常のProduction
    実行では指定しない。

    as_of_date: 既定None（"YYYY-MM-DD"）。Situation Trajectory Production Integration
    Phase T-1（ユーザー指示、2026-09-22）: build_foundation_context（Foundation Context
    Builder v1）へ素通しする。省略時はbuild_foundation_context自身の既定（本日UTC日付）
    に委ねる——通常のCurrent Case実行はこれで問題ないが、Gold/Calibration Release Test
    のような過去日付のケースを再実行する際は、未来情報混入を防ぐため必ず明示指定する。

    extra_document_ids: 既定None。Session Calibration Blind Case（ユーザー指示、2026-09-22）:
    日本語クエリKeywordと英語中心の文書本文のILIKE不一致等、既知のRetrieval Coverage不足を
    補うために、keyword一致に関わらず含めるdocument_idを明示指定する（build_source_packへ
    素通しするだけ、既存のExternal Research Cycleと同じ仕組み）。Data Readiness補完の
    ためであり、特定のReading/Hypothesisを支持させる目的でのEvidence選別には使わない。

    persist_case_side_effects: 既定True（persist=True時の既存挙動を完全維持）。Reading
    Presentation Layer v1(ユーザー指示、Gold Case UI検証専用): persist=True かつ
    persist_case_side_effects=Falseの場合、geo_expert_requests/geo_source_packs/
    geo_expert_responses/geo_quality_evaluations/geo_audit_logsへのinsertは通常通り行う
    （response_idを実際にDBへ発行し、Presentation APIから参照できるようにするため）が、
    case_idに紐づく副作用——geo_cases.final_outputのupdate、_record_case_retrieval_trace
    （geo_retrieval_runs/geo_retrieval_results insert、geo_knowledge_items.usage_count/
    last_used_at update）、persistent_preference_candidateの生成——は行わない。Gold Case
    評価用のUI確認responseが、実際のCase記録・Retrieval分析統計・Persistent Preference
    学習パイプラインへ混入すること（上記インシデントと同種の事故）を防ぐためのフラグ。

    actor_intelligence_enabled: 既定None（operation-scoped override、2026-09-25追加）。
    Noneの場合は既存のglobal env flag（モジュール関数actor_intelligence_enabled()）へ
    そのまま委ねる。True/False明示時はglobal flagに関わらずその値を使う
    （Reading生成専用、Consultation/Draft/RefineDesign等は引き続きNoneのまま呼ぶ）。"""
    _source_pack_kwargs = {"extra_document_ids": extra_document_ids}
    if source_pack_document_limit is not None:
        _source_pack_kwargs["document_limit"] = source_pack_document_limit
    source_pack = build_source_pack(client, theme_id, query_text, **_source_pack_kwargs)

    # Phase R1 Chunk4: Research Sufficiency Gate（決定的、Stage1呼び出し前、追加Azure
    # Callなし）→ 不足している場合のみExternal Research（既存CallBudgetとは別立ての
    # search_budgetを消費）。Web Searchを先に行わず、既存DB Retrievalの結果を見てから
    # 「何が足りないか」を判断する、という順序を守る。
    research_metadata = {"sufficiency_gate_enabled": external_research_enabled()}
    if theme_id and external_research_enabled():
        initial_sufficiency = geo_expert_research_service.assess_research_sufficiency(
            source_pack, query_text=query_text)
        research_metadata["initial_sufficiency"] = initial_sufficiency
        research_cycle_result = geo_expert_research_service.run_research_cycle(
            client, azure_client, model, theme_id=theme_id, query_text=query_text,
            sufficiency=initial_sufficiency)
        research_metadata["research_cycle"] = {
            k: v for k, v in research_cycle_result.items() if k != "documents_added"
        }
        documents_added = research_cycle_result.get("documents_added") or []
        research_metadata["documents_added_count"] = len(documents_added)
        if documents_added:
            # 新規永続化したdocumentをsource_packへ再構築する（DB専用、追加Azure
            # Callなし）。既存keyword-matched検索（日本語クエリ由来のKeywordを想定）は
            # English中心のExternal Research結果のtitle/extracted_textとILIKEで
            # 一致しないことがあるため、Research Cycleが返した既知のdocument_idを
            # extra_document_idsとして明示的に渡し、keyword一致に関わらず必ず含める
            # （呼び出し元が指定したextra_document_idsがあればマージする）。
            merged_extra_ids = list(extra_document_ids or []) + [d["document_id"] for d in documents_added]
            source_pack = build_source_pack(
                client, theme_id, query_text, extra_document_ids=merged_extra_ids)
        research_metadata["final_sufficiency"] = geo_expert_research_service.assess_research_sufficiency(
            source_pack, query_text=query_text)

    # Post-Freeze Integrity Audit R2 Hotfix(ユーザー指示、2026-09-22): Evidence Integrity
    # Guard用に、この時点で確定したSource Pack内document_idのstub判定・実本文テキストを
    # 1回だけ決定的に算出する（Foundation Context経由で追加されるdocumentはここに含まれ
    # ない——既知の限界、現時点で確認された不具合はSource Pack経由のみのため）。
    stub_document_ids, source_pack_document_texts = _stub_document_ids_and_texts(
        client, source_pack.get("document_ids") or [])

    knowledge = geo_expert_common.load_geo_expert_knowledge()
    stage1_knowledge_block = geo_expert_common.build_knowledge_block(_knowledge_files_for_stage1(mode))
    stage1_system_prompt = _build_system_prompt(mode, stage1_knowledge_block, profile=profile)

    user_prompt = f"{source_pack['text']}\n\n【依頼内容】\n{query_text}"
    if draft_text:
        user_prompt += f"\n\n【レビュー対象のドラフト】\n{draft_text}"

    # Foundation Context Builder v1（ユーザー指示、test-only、既定OFF）: Situation
    # Trajectoryを、Actor/Media Intelligenceと同じ位置づけでCall1より前に注入する。
    # 「Foundation Context Retrieval Validation v1」で実証済みの設計をそのまま反映する
    # （E01 Fact A: Ablate 0/6→Oracle 5/6、E01-Late: Ablate 0/6→Real Retrieval
    # 5/6+1 PARTIAL）。診断情報はexecution_metadataへのみ格納する（response_jsonには
    # 含まれない、popも不要）。
    foundation_context_diagnostics = None
    if foundation_context_v1_enabled():
        foundation_context_text, foundation_context_diagnostics = build_foundation_context(
            client, query_text, theme_id=theme_id, as_of_date=as_of_date, already_known_text=user_prompt)
        if foundation_context_text:
            user_prompt += foundation_context_text

    case_context = None
    case_context_text = ""
    foundation_pack = None
    structural_text = ""
    if case_id:
        case_context = build_case_context(client, case_id)
        case_context_text = case_context["text"]
        user_prompt += f"\n{case_context_text}"
        foundation_pack = geo_foundation_pack_service.build_foundation_pack(client, case_id)
        structural_text = _format_structural_context(client, foundation_pack)
        if structural_text:
            user_prompt += f"\n{structural_text}"
        # v1.2 Persistent Preference再利用（Stage0、決定的処理、常時有効）:
        # 既存active actor_profileのpersistent_preferenceをStage1へ提示し、毎回ゼロから
        # 生成させない（Candidate自動生成は別途Feature Flagで制御、post-processing側）。
        entity_names = {e["entity_id"]: e.get("canonical_name") or e["entity_id"]
                        for e in (foundation_pack.get("2_relevant_entities") or [])}
        pp_block = geo_persistent_preference_service.format_persistent_preference_block(
            foundation_pack, entity_names)
        if pp_block:
            user_prompt += pp_block

    # Actor Intelligence Phase AI-3 → Phase D(ユーザー指示、Actor Runtime Activation):
    # Feature Flag有効時、case_idの有無にかかわらずActor Intelligenceを試みる。
    # case_id指定時はcurated Issue Link経路（既存）を最優先し、Runtime Resolverは
    # それに無い候補だけを補う（DBへの永続化は一切行わない）。
    actor_intelligence_pack = None
    actor_intelligence_text = ""
    runtime_actor_candidates = None
    effective_actor_intelligence_enabled = _resolve_actor_intelligence_enabled(actor_intelligence_enabled)
    if effective_actor_intelligence_enabled:
        runtime_actor_candidates = resolve_runtime_actor_candidates(
            client, case_id=case_id, query_text=query_text, source_pack=source_pack)
        # Phase 6A-2(Relationship Relevance Precision、ユーザー指示): resolve_runtime_actor_
        # candidatesが実際にActor Activation判定に使うのと同じ結合テキストを、1-hop
        # Relationship展開の関連性フィルタへも渡す（2つの判定基準を分岐させない）。
        situation_text_for_relationships = f"{query_text or ''}\n{(source_pack or {}).get('text') or ''}".lower()
        actor_intelligence_pack = geo_actor_intelligence_service.build_actor_intelligence_pack(
            client, case_id, profile_lookup_statuses=actor_profile_lookup_statuses,
            profile_retrieval_status=actor_profile_retrieval_status,
            foundation_pack=foundation_pack,
            extra_candidate_entity_ids=runtime_actor_candidates["entity_ids"],
            situation_text=situation_text_for_relationships)
        actor_intelligence_text = _format_actor_intelligence_block(actor_intelligence_pack)
        if actor_intelligence_text:
            user_prompt += f"\n{actor_intelligence_text}"

    # Media Intelligence Build（M8.5）: Feature Flag有効時のみPackを注入する。Actorと異なり
    # case_idを必須にしない（Case-scoped Retrievalテーブルがまだ無いため、M9までは全件走査＋
    # char budget trimという簡易実装。media_intelligence_statusesは既定("active",)のみで
    # 本番相当の安全側、比較実験時のみ呼び出し元が("active","candidate")等へ明示的に上書きする）。
    media_intelligence_pack = None
    if media_intelligence_enabled():
        media_intelligence_pack = geo_media_intelligence_service.build_media_intelligence_pack(
            client, statuses=media_intelligence_statuses)
        media_intelligence_text = _format_media_intelligence_block(media_intelligence_pack)
        if media_intelligence_text:
            user_prompt += f"\n{media_intelligence_text}"

    # Expert AI Phase R1 Chunk3: Feature Flag有効時のみ、case_id指定時に限りPackを注入する
    # （Actor Intelligenceと同じゲート方式）。候補となるInstitution EntityはFoundation Pack
    # で既に解決済みのEntity一覧から抽出する（Person Actorのような専用selectionアルゴリズムは
    # 新設しない、MVPの意図的な単純化）。
    institution_intelligence_pack = None
    institution_intelligence_text = ""
    if case_id and institution_intelligence_enabled():
        institutional_entities = geo_institution_intelligence_service.institutional_entities_from_foundation_pack(
            foundation_pack)
        if institutional_entities:
            institution_intelligence_pack = geo_institution_intelligence_service.build_institution_intelligence_pack(
                client, institutional_entities,
                profile_lookup_statuses=actor_profile_lookup_statuses,
                profile_retrieval_status=actor_profile_retrieval_status)
            institution_intelligence_text = _format_institution_intelligence_block(institution_intelligence_pack)
            if institution_intelligence_text:
                user_prompt += f"\n{institution_intelligence_text}"

    # Phase 7-1A(Controller Packaging、ユーザー指示): 上記institution_intelligence_pack
    # (entity_idで解決済みの制度)とは別に、Source Pack/Actor Intelligence Pack本文中に
    # 言及があるがcanonical entityとして未解決の制度的Controller（例: 議会）を、
    # entity_id無しのCandidateとして補う（DBへの新規Entity作成・Actor Intelligenceの
    # 捏造なし、Runtime-onlyでDB永続化しない）。actor_intelligence_enabled()に依らず、
    # Source Pack本文だけからも検出できるようにする。
    institutional_controller_candidates = _build_institutional_controller_context(
        source_pack_text=source_pack.get("text") or "", actor_intelligence_text=actor_intelligence_text,
        foundation_pack=foundation_pack, actor_intelligence_pack=actor_intelligence_pack,
        institution_intelligence_pack=institution_intelligence_pack)
    institutional_controller_context_text = _format_institutional_controller_context_block(
        institutional_controller_candidates)
    if institutional_controller_context_text:
        user_prompt += f"\n{institutional_controller_context_text}"

    # Context Availability / Activation Telemetry（ユーザー指示Phase C、Call1直前）
    foundation_context_status = build_foundation_context_status(
        mode=mode, profile=profile, source_pack=source_pack,
        foundation_context_text=foundation_context_text if foundation_context_v1_enabled() else "",
        foundation_context_diagnostics=foundation_context_diagnostics, case_id=case_id,
        foundation_pack=foundation_pack, structural_text=structural_text,
        actor_intelligence_pack=actor_intelligence_pack, actor_intelligence_text=actor_intelligence_text,
        runtime_actor_candidates=runtime_actor_candidates, media_intelligence_pack=media_intelligence_pack,
        media_intelligence_text=media_intelligence_text if media_intelligence_enabled() else "",
        actor_intelligence_effective_enabled=effective_actor_intelligence_enabled)

    # Session2 Reasoning Upgrade: key_point_assessment.controlling_entity_idsの捏造検出用に、
    # 実際にStage1 Promptへ提示したentity_id集合を保持する（known_ids=Evidence/Claim等の既知ID
    # 集合とは別物。DBには存在するが今回LLMには見せていないentity_idを偶然・捏造で出力した
    # 場合を検出するため、known_entity_ids=DB全体ではなくこの厳密な集合で判定する）。
    # Phase 6E-5(Entity Identity Integrity、ユーザー指示): stage1_visible_entity_idsは
    # IDの集合のみで、対応する正しいcanonical_nameを保持していなかった。そのため既存の
    # _rebind_or_unresolve_invalid_entity_references（下記）は「entity_idがStage1へ
    # 提示した集合に含まれるか」しか検証せず、含まれてさえいれば「そのentity_idと、
    # LLMが実際に書いたactor名が一致するか」は一切検証していなかった——entity_id自体は
    # 正しく提示範囲内でも、LLMがそれを別のActor名と取り違えて出力する（identity
    # mismatch）事態を検出できない構造的な穴だった。stage1_visible_entity_names
    # （entity_id→canonical_name）を同じ収集元から追加で保持し、_resolve_entity_
    # identity_consistencyで使う。
    stage1_visible_entity_ids = set()
    stage1_visible_entity_names: dict = {}
    if foundation_pack is not None:
        for e in (foundation_pack.get("2_relevant_entities") or []):
            if e.get("entity_id"):
                stage1_visible_entity_ids.add(e["entity_id"])
                if e.get("canonical_name"):
                    stage1_visible_entity_names[e["entity_id"]] = e["canonical_name"]
    if actor_intelligence_pack is not None:
        for ka in (actor_intelligence_pack.get("key_actors") or []):
            if ka.get("entity_id"):
                stage1_visible_entity_ids.add(ka["entity_id"])
                if ka.get("canonical_name"):
                    stage1_visible_entity_names[ka["entity_id"]] = ka["canonical_name"]

    known_ids = set(source_pack["document_ids"]) | set(source_pack["event_ids"]) | set(source_pack["evidence_ids"])
    if case_context is not None:
        known_ids |= {i.get("knowledge_item_id") for i in case_context["included"] if i.get("knowledge_item_id")}
        known_ids |= {i.get("knowledge_item_id") for i in case_context["contested"] if i.get("knowledge_item_id")}
        known_ids |= set(case_context["claim_ids"])
        known_ids |= {h.get("hypothesis_id") for h in case_context["hypotheses"] if h.get("hypothesis_id")}
        known_ids |= {g.get("gap_id") for g in case_context["gaps"] if g.get("gap_id")}
    if actor_intelligence_pack is not None:
        known_ids |= actor_intelligence_pack.get("known_ids") or set()
    if media_intelligence_pack is not None:
        known_ids |= media_intelligence_pack.get("known_ids") or set()
    if institution_intelligence_pack is not None:
        known_ids |= institution_intelligence_pack.get("known_ids") or set()
    known_ids.discard(None)

    # M9 Actor×Media Integration Pilot: Actor/Media Pattern・Episode・ProfileのIDは
    # Derived Intelligence（複数Caseから導出された参照材料）であり、今回のCase固有の
    # Raw Evidenceではない。known_idsへ混ぜて捏造防止チェックの対象にはするが、
    # _evidence_attribution_not_conflated用に「Rawではない」ことを示す集合として別途保持する。
    # Phase R1 Chunk3: Institution Profileのevidence_idsも同じ扱い（Derived Intelligence）。
    derived_intelligence_ids = set()
    if actor_intelligence_pack is not None:
        derived_intelligence_ids |= actor_intelligence_pack.get("known_ids") or set()
    if media_intelligence_pack is not None:
        derived_intelligence_ids |= media_intelligence_pack.get("known_ids") or set()
    if institution_intelligence_pack is not None:
        derived_intelligence_ids |= institution_intelligence_pack.get("known_ids") or set()
    derived_intelligence_ids.discard(None)

    budget = CallBudget(total=4)
    usage_total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    stage_metrics = []

    def _record(stage_name: str, started: float, *, result: dict | None, attempts_allotted: int,
                error: "geo_llm_common.GeoLLMError | None" = None, extra: dict | None = None) -> None:
        """error: 2026-09-18追加（ユーザー指示#9、telemetry）。result=None（失敗）の場合に
        呼び出し元がexcept geo_llm_common.GeoLLMError as excで捕まえた例外そのものを渡すと、
        exc.attempts（試行ごとのAzure呼び出し記録: attempt_label/outcome/exception_type/
        elapsed_ms/timeout_value、失敗時はfinish_reason/raw_content_head・tail/parse_error等の
        診断情報も含む——geo_llm_common._mark_last_attempt_validation_failed参照）と
        exc.exception_typeをstage_metricsへ残す。渡さない場合は従来通りattempts=[]・
        exception_type=Noneのまま（既存呼び出し元は無改変で動作する）。

        extra: 2026-09-19追加（Phase 7-1C-R2、ユーザー指示）。stage_metricsのこのentryへ
        そのままmergeする追加フィールド（例: recovery_reason）。既存呼び出し元はNoneのままで
        無改変。"""
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if result is None:
            budget.spend(attempts_allotted)
            entry = {
                "stage": stage_name, "attempts": attempts_allotted, "success": False,
                "elapsed_ms": elapsed_ms,
                "exception_type": getattr(error, "exception_type", None),
                "azure_call_attempts": getattr(error, "attempts", None) or [],
            }
            if extra:
                entry.update(extra)
            stage_metrics.append(entry)
            return
        calls = _CALLS_BY_MODE[result["mode"]]
        budget.spend(calls)
        usage = result.get("usage") or {}
        _accumulate_usage(usage_total, usage)
        entry = {
            "stage": stage_name, "attempts": calls, "success": True, "elapsed_ms": elapsed_ms,
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "rate_limit_retries": result.get("rate_limit_retries"),
            "azure_call_attempts": result.get("attempts") or [],
        }
        if extra:
            entry.update(extra)
        stage_metrics.append(entry)

    # ---- Stage境界の決定(Phase D-3・Holistic Reasoning Architecture Review) ----
    # situation_actor_assessmentモジュール（hypothesis_register/directional_perceptions等、
    # Human Strategic Interaction/Competing Hypothesisを含む）が発火するprofile×mode組合せ
    # （現状deep×theme_design/case_analysisのみ）に限り、Main Scenario生成をそれらの後段
    # （Call2）へ遅延させる。quick/standard、およびdeepでもsituation_actor_assessmentが
    # 発火しないmode（research_design/outline_review/draft_review/executive_brief/
    # analyst_coaching）は、CallBudget消費・呼び出し順序とも一切変更しない
    # （defer_scenario=Falseのまま、既存の1コール構成を完全に維持する）。
    modules = geo_expert_schema.resolve_stage2_modules(mode, profile)
    defer_scenario = "situation_actor_assessment" in modules
    call1_extra_modules = tuple(m for m in modules if m != "deep_scenario") if defer_scenario else ()
    # Expert Reasoning Pipeline v0.2（ユーザー指示）: intelligence_needsは静的トリガー表
    # （_PROFILE_ADDITIONAL_MODULES）には入れず、Feature Flag ONの時だけこの1回の
    # Stage1呼び出しへ動的に追加する（OFF時はSchemaに一切現れない、Baseline/v0.1との
    # 比較を可能にするため）。
    if defer_scenario and reasoning_pipeline_v2_enabled():
        call1_extra_modules = (*call1_extra_modules, "intelligence_needs")
        # Explanatory Model Activation v0.3（ユーザー指示）: v0.2 Pipeline ONの上にのみ
        # 積み重なる（v0.3単独では意味を持たない、Section18の3状態比較要件どおり）。
        # Selector-Free Model Sweep実験がONの場合はmodel_activation_requests自体を
        # Call1に生成させない（Call1は単純化する、ユーザー指示）ため、この場合は追加しない。
        if explanatory_model_activation_enabled() and not explanatory_model_sweep_enabled():
            call1_extra_modules = (*call1_extra_modules, "model_activation")
    # Trajectory Context Experiment v0.1（ユーザー指示、test-only）: v0.2 Pipelineとは
    # 独立したFlagとして持つ（intelligence_needsの上にのみ積み重なる設計にはしない——
    # Trajectory Need DetectionはCall1(Explore)側のSchema/Prompt拡張のみで完結し、
    # Supplemental Retrieval自体は_run_supplemental_retrievalとは別の専用関数で行う）。
    if defer_scenario and trajectory_enabled():
        call1_extra_modules = (*call1_extra_modules, "trajectory_assessment")
    # Situation Trajectory Intelligence v1（ユーザー指示、test-only）: v0.1/v0.2の
    # trajectory_assessmentとは独立したFlag・独立したSchema field。本実験ではv0.1/v0.2と
    # 併用しない想定だが、コード上は両方ONでも共存できる（互いに独立したproperty）。
    if defer_scenario and trajectory_v1_enabled():
        call1_extra_modules = (*call1_extra_modules, "trajectory_information_need")
    # Situation Trajectory Intelligence v1.2（ユーザー指示、test-only）: v1のBoolean Gate
    # を置き換える、Evidence Request方式。v1と同時にSchemaへ混在させない設計。
    if defer_scenario and trajectory_v1_2_enabled():
        call1_extra_modules = (*call1_extra_modules, "trajectory_evidence_requests")
    # Expert Context Architecture v1.3（ユーザー指示、test-only）: v1/v1.2のOptional
    # Self-Report Gateを置き換える、Hypothesis Evidence Requirements方式。
    if defer_scenario and expert_context_v1_3_enabled():
        call1_extra_modules = (*call1_extra_modules, "hypothesis_evidence_requirements")
    # Hypothesis Generation v1.4（ユーザー指示、test-only）: v1.3とは独立したFlag・
    # 独立したSchema field。両方ONでも共存できる。
    if defer_scenario and hypothesis_generation_v1_4_enabled():
        call1_extra_modules = (*call1_extra_modules, "hypothesis_state_transitions")
    # Phase 7-2K-A/B(ユーザー指示、test-only): deep+defer_scenario限定（今回のvalidation
    # caseと同じスコープ、いきなり全mode/profileへ拡張しない）。Candidate Setの構築・
    # user_promptへの注入もここでdefer_scenario確定後に行う（module未発火時に無駄な
    # promptを増やさない）。
    actor_candidate_set: list = []
    if defer_scenario and actor_candidate_selection_enabled():
        call1_extra_modules = (*call1_extra_modules, "actor_candidate_selection")
        actor_candidate_set = _build_deterministic_actor_candidate_set(
            client, foundation_pack=foundation_pack, actor_intelligence_pack=actor_intelligence_pack,
            institution_intelligence_pack=institution_intelligence_pack)
        actor_candidate_set_text = _format_actor_candidate_set_block(actor_candidate_set)
        if actor_candidate_set_text:
            user_prompt += f"\n{actor_candidate_set_text}"

    # ---- Stage 1（常に1回、失敗時のみ残り予算全部で1回だけ再試行）----
    # defer_scenario=Trueの場合、この1回の呼び出しへcall1_extra_modules（audience_signal/
    # media_context/situation_actor_assessment/directional_perception）を合成し、
    # main_scenario/alternative_scenarios/strongest_counterargumentはここでは生成しない
    # （旧来のStage1簡易版生成経路はdefer_scenario=Trueの間、完全に無効——二重source of
    # truthにはならない。build_stage1_schemaのdefer_scenario引数参照）。
    stage1_schema = geo_expert_schema.build_stage1_schema(
        extra_modules=call1_extra_modules, defer_scenario=defer_scenario)
    started = time.monotonic()
    try:
        result = geo_llm_common.call_llm_structured(
            azure_client, model, stage1_system_prompt, user_prompt, stage1_schema,
            "geo_expert_stage1", max_attempts=1, strict=True,
            timeout=_EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDS)
        _record("stage1", started, result=result, attempts_allotted=1)
        stage1_data = result["data"]
    except geo_llm_common.GeoLLMError as stage1_exc:
        _record("stage1_initial_failed", started, result=None, attempts_allotted=1, error=stage1_exc)
        retry_attempts = min(2, budget.remaining)
        if retry_attempts < 1:
            raise
        started = time.monotonic()
        try:
            result = geo_llm_common.call_llm_structured(
                azure_client, model, stage1_system_prompt, user_prompt, stage1_schema,
                "geo_expert_stage1_recovery", max_attempts=retry_attempts, strict=True,
                timeout=_EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDS)
            _record("stage1_recovery", started, result=result, attempts_allotted=retry_attempts)
            stage1_data = result["data"]
        except geo_llm_common.GeoLLMError as stage1_recovery_exc:
            _record("stage1_recovery_failed", started, result=None, attempts_allotted=retry_attempts,
                    error=stage1_recovery_exc)
            raise

    # 診断専用・読み取り専用フック（Expert Context Architecture v1.3、Frozen-Call1実験
    # ユーザー指示）: 環境変数GEO_EXPERT_DEBUG_STAGE1_DUMP_PATHが設定されている場合のみ、
    # Call2へ渡す直前（マージ・popより前）のstage1_dataをそのままファイルへ書き出す。
    # 判定ロジック・Prompt・Retrieval挙動には一切影響しない（既定は未設定＝無効）。
    _debug_dump_path = os.environ.get("GEO_EXPERT_DEBUG_STAGE1_DUMP_PATH")
    if _debug_dump_path:
        with open(_debug_dump_path, "w", encoding="utf-8") as _f:
            json.dump(stage1_data, _f, ensure_ascii=False, indent=2)

    # ---- Stage 2 ----
    stage2_data = None
    stage2_schema = None
    stage2_system_prompt = None
    scenario_fallback_used = False
    # Phase 7-1C-R2(ユーザー指示、Telemetry): SUCCESS_FIRST_ATTEMPT/RECOVERED_SECOND_ATTEMPT/
    # FAILED_AFTER_RECOVERY/STUB_FALLBACK/RECOVERY_SKIPPED_BUDGETのいずれか
    # （defer_scenario=Falseの経路、またはbudget不足でStage2自体を呼ばない経路ではNoneのまま）。
    stage2_recovery_status = None
    trajectory_v1_2_diagnostics = None
    expert_context_v1_3_diagnostics = None
    if defer_scenario:
        # Phase D-3(絶対条件2・3・4): Human Strategic Interaction/Competing Hypothesisが
        # 確定した後にのみMain Scenarioを生成する。deep_scenarioモジュールが担っていた
        # alternative_scenarios生成もこの呼び出しへ一本化する（Stage1側では生成しないため
        # 二重source of truthにならない）。main_scenarioはmaster schema側で必須のため、
        # Call2自体は（budgetが許す限り）必ず試みる——通常の生成失敗時の"Stage1のみで継続"
        # というswallowパターンは使えない（決定的フォールバックで補う、下記参照）。
        stage2_schema = geo_expert_schema.build_stage3_scenario_schema()
        stage2_knowledge_block = geo_expert_common.build_knowledge_block(_knowledge_files_for_stage1(mode))
        stage2_system_prompt = _build_system_prompt(mode, stage2_knowledge_block, profile=profile, stage="decide")
        # Expert Reasoning Pipeline v0.2（ユーザー指示）: Call1がintelligence_needsを
        # 名指しした場合のみ、追加LLM呼び出し無しでDB/Service照会（Deterministic Supplemental
        # Retrieval）を行い、見つかった内容だけをCall2側のPromptへ渡す。
        supplemental_retrieval_text = ""
        activated_model_cards_text = ""
        if reasoning_pipeline_v2_enabled():
            supplemental_retrieval_text = _run_supplemental_retrieval(
                client, stage1_data.get("intelligence_needs"), theme_id=theme_id)
            # Selector-Free Model Sweep実験（ユーザー指示）: Call1の選抜結果に依存せず、
            # ACTIVE+EXPERIMENTAL計8件のFull Cardを常にCall2へ渡す（Local Lookupのみ、
            # 追加LLM呼び出しなし）。v0.3 Selectorと同時ONの場合はSweepを優先する。
            if explanatory_model_sweep_enabled():
                activated_model_cards_text = ""  # SWEEP_DECIDE_ADDENDUM側に全Card済み同梱
            elif explanatory_model_activation_enabled():
                activated_model_cards_text = _resolve_activated_model_cards(
                    stage1_data.get("model_activation_requests"))
        # Trajectory Reasoning Ablation v0.2（ユーザー指示、test-only）: Reasoning
        # AddendumとSupplemental Retrievalを独立に判定する。
        # - trajectory_enabled(): trajectory_reasoning_needed=trueの場合、Call2 system
        #   promptへTrajectory Reasoning Addendum（該当する全trajectory_modesのAddendum、
        #   primary_modeだけでなく）を追加する。既存Evidenceのみでの時系列推論を許すため、
        #   Retrievalの成否とは無関係に追加する。
        # - trajectory_retrieval_enabled(): 上記に加えて、Call1がevidence_gapを伴う
        #   supplemental_retrieval_needed=trueを申告した場合のみ、追加LLM呼び出し無しで
        #   Deterministic Trajectory Retrievalを行い、TRAJECTORY CONTEXT（raw candidate）
        #   をuser prompt側へ追加する。
        trajectory_context_text = ""
        if trajectory_enabled():
            trajectory_assessment = stage1_data.get("trajectory_assessment")
            if trajectory_assessment and trajectory_assessment.get("trajectory_reasoning_needed"):
                if trajectory_retrieval_enabled() and trajectory_retrieval_should_run(trajectory_assessment):
                    trajectory_context_text = _run_trajectory_retrieval(
                        client, trajectory_assessment, theme_id=theme_id)
                trajectory_modes = trajectory_assessment.get("trajectory_modes") or []
                trajectory_reasoning_addendum = geo_expert_schema.TRAJECTORY_REASONING_DECIDE_ADDENDUM
                if "escalation" in trajectory_modes:
                    trajectory_reasoning_addendum += geo_expert_schema.TRAJECTORY_ESCALATION_MODE_ADDENDUM
                if "narrative" in trajectory_modes:
                    trajectory_reasoning_addendum += geo_expert_schema.TRAJECTORY_NARRATIVE_MODE_ADDENDUM
                if "actor_behavior" in trajectory_modes:
                    trajectory_reasoning_addendum += geo_expert_schema.TRAJECTORY_ACTOR_BEHAVIOR_MODE_ADDENDUM
                stage2_system_prompt = stage2_system_prompt + trajectory_reasoning_addendum
        # Situation Trajectory Intelligence v1（ユーザー指示、test-only）: Reasoning
        # Addendum自体はtrajectory_v1_enabled()のみでstage2_system_promptへ既に追加済み
        # （_build_system_prompt内、Retrievalの成否と無関係、Condition B相当）。ここでは
        # 実際のRetrieval（Condition C相当、evidence_gapがある場合のみ）だけを追加で行う。
        trajectory_v1_context_text = ""
        if trajectory_v1_enabled() and trajectory_v1_retrieval_enabled():
            trajectory_information_need = stage1_data.get("trajectory_information_need")
            if (trajectory_information_need and trajectory_information_need.get("information_needed")
                    and trajectory_information_need.get("evidence_gaps")):
                trajectory_v1_context_text = _run_trajectory_v1_retrieval(
                    client, trajectory_information_need, theme_id=theme_id)
        # Situation Trajectory Intelligence v1.2（ユーザー指示、test-only）: v1のBoolean
        # Gateを置き換えるEvidence Request方式。trajectory_v1_2_enabled()単独＝Evidence
        # Request生成＋Reasoning Addendumのみ（Retrieval無し）。両Flag ON＝さらに
        # Existing Evidence Satisfaction Checkを経たIssue-scoped Retrievalも行う。
        # diagnosticsはPublic Response Schemaへは出さず、execution_metadataへのみ格納する。
        trajectory_v1_2_context_text = ""
        trajectory_v1_2_diagnostics = None
        if trajectory_v1_2_enabled():
            evidence_requests = stage1_data.get("trajectory_evidence_requests") or []
            if trajectory_v1_2_retrieval_enabled() and evidence_requests:
                trajectory_v1_2_context_text, trajectory_v1_2_diagnostics = _run_trajectory_v1_2_retrieval(
                    client, evidence_requests, user_prompt, theme_id=theme_id)
            else:
                trajectory_v1_2_diagnostics = {
                    "requests": [{"request_id": r.get("request_id"), "need_type": r.get("need_type"),
                                   "priority": r.get("priority"), "satisfaction": None,
                                   "retrieval_attempted": False, "kept_count": 0,
                                   "reasoning_dependency": r.get("reasoning_dependency"),
                                   "question": r.get("question"), "why_it_matters": r.get("why_it_matters"),
                                   "core_issue": r.get("core_issue"),
                                   "primary_actors": r.get("primary_actors"),
                                   "secondary_actors": r.get("secondary_actors"),
                                   "relevant_dimensions": r.get("relevant_dimensions"),
                                   "expected_evidence": r.get("expected_evidence")}
                                  for r in evidence_requests[:_TRAJECTORY_V1_2_MAX_REQUESTS]],
                    "retrieval_fired": False, "kept_total": 0,
                }
        # Expert Context Architecture v1.3（ユーザー指示、test-only）: v1/v1.2のOptional
        # Self-Report Gateを置き換える、Hypothesis Evidence Requirements + Coverage
        # Check方式。Requirements生成自体はexpert_context_v1_3_enabled()のみで発火
        # （Retrieval無し）、両Flag ONでTRAJECTORYへ経路づけられた項目のみRetrievalする。
        expert_context_v1_3_context_text = ""
        if expert_context_v1_3_enabled():
            expert_context_v1_3_context_text, expert_context_v1_3_diagnostics = _expert_context_v1_3_process(
                stage1_data, user_prompt, client,
                retrieval_enabled=expert_context_v1_3_retrieval_enabled(), theme_id=theme_id)
        stage2_prompt = (
            f"{user_prompt}\n\n【Call1で確定した内容（Situation/Control Resolution・"
            "Human Strategic Interaction・Competing Hypothesesを含む。前提として維持し、"
            "重複記述しないこと）】\n" + json.dumps(stage1_data, ensure_ascii=False)
            + "\n\n【Scenario生成指示】hypothesis_registerでretainedとなった仮説を1つ特定し、"
              "その仮説が依存するActor Interaction（directional_perceptions/event_role/"
              "actor_expected_response等）を踏まえてmain_scenarioを導出すること。"
              "main_scenario.derived_from_hypothesis_idへその仮説のhypothesis_idを"
              "記録すること。拮抗する仮説があれば、採用した場合に何が変わるかをalternative_"
              "scenarios（該当するderived_from_hypothesis_idも記録）へ反映すること。"
            + supplemental_retrieval_text
            + activated_model_cards_text
            + trajectory_context_text
            + trajectory_v1_context_text
            + trajectory_v1_2_context_text
            + expert_context_v1_3_context_text
        )
        if budget.can_afford(1):
            started = time.monotonic()
            try:
                result = geo_llm_common.call_llm_structured(
                    azure_client, model, stage2_system_prompt, stage2_prompt, stage2_schema,
                    "geo_expert_stage2_scenario", max_attempts=1, strict=True,
                    timeout=_EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDS)
                _record("stage2_scenario", started, result=result, attempts_allotted=1)
                stage2_data = result["data"]
                stage2_recovery_status = "SUCCESS_FIRST_ATTEMPT"
            except geo_llm_common.GeoLLMError as stage2_scenario_exc:
                _record("stage2_scenario_failed", started, result=None, attempts_allotted=1,
                        error=stage2_scenario_exc)
                # Phase 7-1C-R2(Stage2 Recovery、ユーザー指示): Stage1が既に持つのと同じ
                # パターンで、Stage2 scenario生成の初回失敗のうち再試行に意味がある種類
                # （_is_recoverable_stage2_failure）だけ、budgetが許せば1回だけ追加リトライ
                # する。Reasoning Prompt/system prompt/user prompt/Schema/model/Actor数/
                # Controller Packaging/Hypothesis数/rich intelligence/deep profile/
                # Situation Contextは一切変更しない——同じ入力をもう一度送るだけ。
                if _is_recoverable_stage2_failure(stage2_scenario_exc) and budget.can_afford(1):
                    started = time.monotonic()
                    try:
                        result = geo_llm_common.call_llm_structured(
                            azure_client, model, stage2_system_prompt, stage2_prompt, stage2_schema,
                            "geo_expert_stage2_scenario_recovery", max_attempts=1, strict=True,
                            timeout=_EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDS)
                        _record("stage2_scenario_recovery", started, result=result, attempts_allotted=1,
                                extra={"recovery_reason": stage2_scenario_exc.exception_type})
                        stage2_data = result["data"]
                        scenario_fallback_used = False
                        stage2_recovery_status = "RECOVERED_SECOND_ATTEMPT"
                    except geo_llm_common.GeoLLMError as stage2_recovery_exc:
                        _record("stage2_scenario_recovery_failed", started, result=None, attempts_allotted=1,
                                error=stage2_recovery_exc,
                                extra={"recovery_reason": stage2_scenario_exc.exception_type})
                        stage2_data = dict(_SCENARIO_FALLBACK_STUB)
                        scenario_fallback_used = True
                        stage2_recovery_status = "FAILED_AFTER_RECOVERY"
                elif _is_recoverable_stage2_failure(stage2_scenario_exc):
                    # 再試行に意味がある失敗だったが、budgetが残っていなかった
                    stage2_data = dict(_SCENARIO_FALLBACK_STUB)
                    scenario_fallback_used = True
                    stage2_recovery_status = "RECOVERY_SKIPPED_BUDGET"
                else:
                    # content policy等、同一入力の再送に意味がない失敗種別——無条件リトライしない
                    stage2_data = dict(_SCENARIO_FALLBACK_STUB)
                    scenario_fallback_used = True
                    stage2_recovery_status = "STUB_FALLBACK"
        else:
            stage2_data = dict(_SCENARIO_FALLBACK_STUB)
            scenario_fallback_used = True
            stage2_recovery_status = "STUB_FALLBACK"
    elif modules and budget.can_afford(1):
        # プロファイル×モードで選ばれたモジュールを1回の呼び出しへ合成（既存動作、無変更）。
        stage2_schema = geo_expert_schema.build_stage2_schema(modules)
        # Stage2はStage1と別のKnowledgeファイル集合が要る場合がある（例: reviewモジュールは
        # 06_KNOWLEDGE_REVIEW_OSが必要）ため、モジュール発火に応じて別途組み立てる。
        stage2_knowledge_block = geo_expert_common.build_knowledge_block(_knowledge_files_for_stage2(modules))
        stage2_system_prompt = _build_system_prompt(mode, stage2_knowledge_block, profile=profile)
        stage2_prompt = (
            f"{user_prompt}\n\n【Stage1で確定した内容（前提として維持し、重複記述しないこと）】\n"
            + json.dumps(stage1_data, ensure_ascii=False)
        )
        started = time.monotonic()
        try:
            result = geo_llm_common.call_llm_structured(
                azure_client, model, stage2_system_prompt, stage2_prompt, stage2_schema,
                "geo_expert_stage2", max_attempts=1, strict=True,
                timeout=_EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDS)
            _record(f"stage2:{'+'.join(modules)}", started, result=result, attempts_allotted=1)
            stage2_data = result["data"]
        except geo_llm_common.GeoLLMError as stage2_exc:
            _record(f"stage2:{'+'.join(modules)}_failed", started, result=None, attempts_allotted=1,
                    error=stage2_exc)
            stage2_data = None  # Stage1のみで継続（既存run_expert_requestのswallowパターンと同様）

    # Situation Trajectory Intelligence v1.2（ユーザー指示、test-only）: ユーザー指示
    # Section4「Public response schemaには出さない。Internal / intermediate schemaのみ」
    # に従い、Call2への受け渡し（stage2_promptのjson.dumps(stage1_data)）が終わった後、
    # マージ前にstage1_dataから取り除く——これ以降のresponse_json（_merge_staged_response
    # はdict(stage1_data)から始める）には一切含まれない。
    stage1_data.pop("trajectory_evidence_requests", None)
    # Expert Context Architecture v1.3（ユーザー指示、test-only）: 同じ理由で、Call2への
    # 受け渡しが終わった後、マージ前にstage1_dataから取り除く。
    stage1_data.pop("hypothesis_evidence_requirements", None)
    stage1_data.pop("hypothesis_discriminators", None)
    # Hypothesis Generation v1.4（ユーザー指示、test-only）: 同じ理由で、Call2への
    # 受け渡しが終わった後、マージ前にstage1_dataから取り除く。
    stage1_data.pop("hypothesis_state_transitions", None)
    # Phase 7-2K-A/B/C(ユーザー指示、test-only): Referential Integrity検証（Candidate Set
    # 外のentity_idが選ばれていないか）をpop前に行う。同じ理由でPublic Response Schema
    # には一切含めない（v1.2/v1.3と同じ設計）。
    actor_candidate_selection_diagnostics = None
    if defer_scenario and actor_candidate_selection_enabled():
        actor_candidate_selection_diagnostics = _validate_actor_candidate_selection(
            stage1_data, actor_candidate_set)
    stage1_data.pop("actor_candidate_selection", None)
    stage1_data.pop("actor_candidate_gaps", None)

    # ---- マージ・導出・Evidence IDスクラブ（すべて決定的、LLMを呼ばない） ----
    response_json = _merge_staged_response(
        stage1_data, stage2_data, source_pack=source_pack, case_context=case_context,
        foundation_pack=foundation_pack)
    removed_ids = geo_expert_quality_guard.scrub_fabricated_evidence_ids(response_json, known_ids)
    # Phase 6D-2(Claim source_ref Referential Integrity、ユーザー指示): claims[].source_ref
    # を実際のEvidence Registry(known_ids)に照合し、無効な参照をProduction Output側でも
    # null化する（評価側=Quality Guardだけで検出するのではなく、最終出力に捏造refを残さない）。
    claim_source_ref_resolutions = geo_expert_quality_guard.resolve_claim_source_ref_referential_integrity(
        response_json, known_ids, stage1_visible_entity_ids=stage1_visible_entity_ids)
    # Post-Freeze Integrity Audit R2 Hotfix(ユーザー指示、2026-09-22): Evidence Integrity
    # Guard。US-China再生成で実際に確認された「title-onlyスタブ文書からの具体的主張捏造」
    # 「本文の実際の日付と矛盾する年の主張」を、生成後に決定的に封じ込める
    # （Reasoning Prompt自体は変更しない）。
    stub_evidence_adjustments = geo_expert_quality_guard.cap_evidence_from_stub_documents(
        response_json, stub_document_ids)
    temporal_grounding_adjustments = geo_expert_quality_guard.enforce_temporal_grounding_in_claims(
        response_json, source_pack_document_texts)
    # M9.1 Evidence Weighting Fix: Actor/Media Historical Patternの一致はcurrent
    # independent corroborationではないため、それのみを根拠にした高confidenceを
    # 決定的に是正する（追加Azure Callなし）。
    confidence_adjustments = geo_expert_quality_guard.apply_confidence_weighting_fix(response_json)
    confidence_adjustments += geo_expert_quality_guard.cap_confidence_by_evidence_class(
        response_json, derived_intelligence_ids)
    # Phase R1 Chunk2: evidence_class（Evidenceの性質）とは別軸のclaim_relevance
    # （このClaimへの直接性）を決定的に評価する（追加Azure Callなし）。
    confidence_adjustments += geo_expert_quality_guard.cap_confidence_by_claim_relevance(response_json)
    # Phase R1 Chunk2: source_typeをLLMの自己申告に依存させず、geo_source_registry起点の
    # 決定論的な値へ強制する（追加Azure Callなし）。
    geo_expert_quality_guard.apply_known_source_types(response_json, source_pack.get("evidence_source_types"))
    # Expert AI Depth & Grounding Upgrade（P0-1/P0-2）: Actor Groundingとcontrol_typeの
    # 不整合を決定的に是正する（追加Azure Callなし）。
    actor_provenance_touched = geo_expert_quality_guard.attach_actor_intelligence_provenance(
        response_json, actor_intelligence_pack, institution_intelligence_pack)
    control_type_adjustments = geo_expert_quality_guard.normalize_control_type(response_json)
    # Expert AI Depth & Grounding Upgrade（3点修正: P0-1/P0-2/P1）: General Knowledge起点の
    # Confidence昇格・Historical/Current Evidenceの混同・Pattern Applicability欠落を
    # 決定的に是正する（追加Azure Callなし）。
    # Actor/Situation層のevidence_idsからDerived Intelligence IDを先に除去してから
    # General Knowledge Confidence Capを評価する（除去前に評価すると、Historical
    # Episode IDの混入をCurrent Evidenceありと誤認してしまうため、この順序が重要）。
    actor_evidence_separations = geo_expert_quality_guard.separate_historical_evidence_in_actor_and_situation_assessment(
        response_json, derived_intelligence_ids)
    confidence_adjustments += geo_expert_quality_guard.cap_confidence_by_general_knowledge_flag(response_json)
    historical_evidence_separations = geo_expert_quality_guard.separate_historical_evidence_in_key_point(
        response_json, derived_intelligence_ids)
    pattern_applicability_fallbacks = geo_expert_quality_guard.ensure_pattern_applicability_assessment(response_json)
    # Phase 6A-4(Repair/Rebinding、ユーザー指示): Stage1へ提示していないentity_id参照
    # （捏造・document_id誤用等）を、削除して正常扱いするのではなく、決定的な検証手順
    # （DB実在確認→名前一致確認）でrebind/unresolvedへ仕分ける。最初のQuality Guard実行
    # （後段）より前に行うことで、_stakeholder_entity_resolved_checkが補正済みの状態を見る。
    entity_reference_resolutions = _rebind_or_unresolve_invalid_entity_references(
        client, response_json, stage1_visible_entity_ids=stage1_visible_entity_ids)
    # Phase 6E-5(Entity Identity Integrity、ユーザー指示): 上記rebindが対象にしない
    # 「entity_idはstage1_visible_entity_ids内で正当だが、LLMが付けたactor名が
    # その実際のcanonical_nameと矛盾する」ケースを追加検証する（rebindの後に実行し、
    # rebindで既にnull化されたentity_idは自然に対象外になる）。
    entity_identity_mismatches = _resolve_entity_identity_consistency(
        response_json, stage1_visible_entity_names=stage1_visible_entity_names)
    unused_rich_actor_intelligence = _detect_unused_rich_actor_intelligence(
        actor_intelligence_pack, response_json)
    # Phase 7-1A Telemetry(ユーザー指示): Controller/Rich Actor CandidateごとのAVAILABLE/
    # USED/CONSIDERED_NOT_MATERIAL/UNAVAILABLE/DATA_READINESS_LIMITEDを決定的に判定する
    # （新しいLLM出力fieldは追加しない）。entity_backed_candidatesはFoundation Pack/Actor
    # Intelligence Pack/Institution Intelligence Packで実際にCall1へ提示した主体の和集合。
    _entity_backed_candidates_seen: dict = {}
    for e in (foundation_pack or {}).get("2_relevant_entities") or []:
        if e.get("entity_id"):
            _entity_backed_candidates_seen[e["entity_id"]] = e.get("canonical_name")
    for ka in (actor_intelligence_pack or {}).get("key_actors") or []:
        if ka.get("entity_id"):
            _entity_backed_candidates_seen[ka["entity_id"]] = ka.get("canonical_name")
    for inst in (institution_intelligence_pack or {}).get("institutions") or []:
        if inst.get("entity_id"):
            _entity_backed_candidates_seen[inst["entity_id"]] = inst.get("canonical_name")
    controller_intelligence_utilization = classify_controller_intelligence_utilization(
        entity_backed_candidates=[
            {"entity_id": eid, "canonical_name": name} for eid, name in _entity_backed_candidates_seen.items()],
        label_only_candidates=institutional_controller_candidates, response=response_json)
    _apply_stage_optional_defaults(response_json)
    if actor_intelligence_pack is not None:
        # Retrieval側が決定的に算出した充足度をそのまま設定する（LLMには生成させない）。
        response_json["actor_intelligence_status"] = actor_intelligence_pack.get("actor_intelligence_status")

    if persist:
        request_row = client.insert("geo_expert_requests", [{
            "theme_id": theme_id, "mode": mode, "query_text": query_text,
        }])[0]
        source_pack_row = client.insert("geo_source_packs", [{
            "request_id": request_row["request_id"],
            "document_ids": source_pack["document_ids"], "event_ids": source_pack["event_ids"],
            "evidence_ids": source_pack["evidence_ids"], "observation_snapshot": source_pack["observation_snapshot"],
        }])[0]
    else:
        # persist=False: DBへ書かず、メモリ上でのみIDを発行する（戻り値の形はpersist=Trueと同一）。
        request_row = {"request_id": str(uuid.uuid4()), "theme_id": theme_id, "mode": mode, "query_text": query_text}
        source_pack_row = {
            "source_pack_id": str(uuid.uuid4()), "request_id": request_row["request_id"],
            "document_ids": source_pack["document_ids"], "event_ids": source_pack["event_ids"],
            "evidence_ids": source_pack["evidence_ids"], "observation_snapshot": source_pack["observation_snapshot"],
        }

    versions = {
        "prompt_version": STAGED_PROMPT_VERSION, "knowledge_version": knowledge["version"],
        "model": model, "source_pack_id": source_pack_row["source_pack_id"],
    }
    guard_result = geo_expert_quality_guard.run_quality_guard(
        response_json, source_pack["text"], versions, case_context_text=case_context_text,
        known_ids=known_ids, mode=mode, stage1_visible_entity_ids=stage1_visible_entity_ids,
        derived_intelligence_ids=derived_intelligence_ids, defer_scenario=defer_scenario,
        scenario_fallback_used=scenario_fallback_used)
    initial_failed_checks = [c["name"] for c in guard_result["checks"] if not c["passed"]]
    # 運用時の原因分析用に、失敗チェックの詳細（name+detail）も実行メタデータへ残す
    # （geo_quality_evaluations.checksは最終版のみ保存されるため、初回の失敗理由は
    # ここに記録しないと後から追えなくなる）。
    initial_failed_checks_detail = [
        {"name": c["name"], "detail": c["detail"]} for c in guard_result["checks"] if not c["passed"]
    ]
    repair_attempted = False
    completeness_status = "complete"
    remaining_failed_checks = initial_failed_checks
    # Phase 7-1C-R2(ユーザー指示、Telemetry): Repairが必要（failing_v12あり）だったのに
    # budget不足で実施できなかったかどうか。Stage2 Recoveryがbudgetを消費した場合に
    # Repairへしわ寄せが行っていないかを実データで確認するため。
    repair_skipped_due_to_budget = False

    failing_v12 = [n for n in initial_failed_checks if n in geo_expert_quality_guard.MODE_V12_CHECK_NAMES]
    if failing_v12:
        target_stage = geo_expert_quality_guard.MODE_V12_CHECK_STAGE.get(failing_v12[0], "stage1")
        if defer_scenario and failing_v12[0] in (
                "scenario_derived_from_hypothesis_grounded", "scenario_derived_from_retained_hypothesis"):
            # Phase D-3/D-4b: main_scenario/alternative_scenariosはdefer_scenario=Trueの間
            # Call2(Scenario)所有になるため、この2チェックのみ動的にstage2側を修復対象と
            # する（MODE_V12_CHECK_STAGEの静的マッピングはdefer_scenario=False、すなわち
            # main_scenarioがStage1所有のままの経路向けにstage1を維持する）。
            target_stage = "stage2"
        if budget.can_afford(1):
            repair_attempted = True
            if target_stage == "stage1":
                repair_schema, repair_base_data = stage1_schema, stage1_data
                repair_system_prompt = stage1_system_prompt
            else:
                repair_schema = stage2_schema or (
                    geo_expert_schema.build_stage3_scenario_schema() if defer_scenario
                    else geo_expert_schema.build_stage2_schema(modules))
                repair_base_data = stage2_data or {}
                repair_system_prompt = stage2_system_prompt or _build_system_prompt(
                    mode, geo_expert_common.build_knowledge_block(
                        _knowledge_files_for_stage1(mode) if defer_scenario
                        else _knowledge_files_for_stage2(modules)),
                    profile=profile, stage="decide" if defer_scenario else "explore")
            failed_detail = "\n".join(f"- {c['name']}: {c['detail']}" for c in guard_result["checks"] if not c["passed"])
            repair_prompt = (
                f"{user_prompt}\n\n【修復依頼】\n以下は元の出力（該当スコープのみ）です。下記のチェックに"
                f"失敗しています。既存の内容は維持したまま不足箇所のみ補ってください。ゼロから作り直さない"
                f"こと。Evidence IDを創作しないこと。\n【失敗したチェック】\n{failed_detail}\n\n"
                f"【元の出力】\n{json.dumps(repair_base_data, ensure_ascii=False)}"
            )
            started = time.monotonic()
            try:
                result = geo_llm_common.call_llm_structured(
                    azure_client, model, repair_system_prompt, repair_prompt, repair_schema,
                    "geo_expert_stage_repair", max_attempts=1, strict=True,
                    timeout=_EXPERT_LARGE_SCHEMA_TIMEOUT_SECONDS)
                _record(f"repair:{target_stage}", started, result=result, attempts_allotted=1)
                if target_stage == "stage1":
                    stage1_data = result["data"]
                else:
                    stage2_data = result["data"]
                    # Phase D-4b: Repairで実際のScenario生成に置き換わったため、
                    # budget fallbackの明示例外はもう適用しない。
                    scenario_fallback_used = False
                response_json = _merge_staged_response(
                    stage1_data, stage2_data, source_pack=source_pack, case_context=case_context,
                    foundation_pack=foundation_pack)
                removed_ids += geo_expert_quality_guard.scrub_fabricated_evidence_ids(response_json, known_ids)
                claim_source_ref_resolutions += geo_expert_quality_guard.resolve_claim_source_ref_referential_integrity(
                    response_json, known_ids, stage1_visible_entity_ids=stage1_visible_entity_ids)
                stub_evidence_adjustments += geo_expert_quality_guard.cap_evidence_from_stub_documents(
                    response_json, stub_document_ids)
                temporal_grounding_adjustments += geo_expert_quality_guard.enforce_temporal_grounding_in_claims(
                    response_json, source_pack_document_texts)
                confidence_adjustments += geo_expert_quality_guard.apply_confidence_weighting_fix(response_json)
                confidence_adjustments += geo_expert_quality_guard.cap_confidence_by_evidence_class(
                    response_json, derived_intelligence_ids)
                confidence_adjustments += geo_expert_quality_guard.cap_confidence_by_claim_relevance(response_json)
                geo_expert_quality_guard.apply_known_source_types(response_json, source_pack.get("evidence_source_types"))
                actor_provenance_touched = geo_expert_quality_guard.attach_actor_intelligence_provenance(
                    response_json, actor_intelligence_pack, institution_intelligence_pack)
                control_type_adjustments += geo_expert_quality_guard.normalize_control_type(response_json)
                actor_evidence_separations += geo_expert_quality_guard.separate_historical_evidence_in_actor_and_situation_assessment(
                    response_json, derived_intelligence_ids)
                confidence_adjustments += geo_expert_quality_guard.cap_confidence_by_general_knowledge_flag(response_json)
                historical_evidence_separations += geo_expert_quality_guard.separate_historical_evidence_in_key_point(
                    response_json, derived_intelligence_ids)
                pattern_applicability_fallbacks += geo_expert_quality_guard.ensure_pattern_applicability_assessment(response_json)
                _apply_stage_optional_defaults(response_json)
                if actor_intelligence_pack is not None:
                    response_json["actor_intelligence_status"] = actor_intelligence_pack.get("actor_intelligence_status")
                guard_result = geo_expert_quality_guard.run_quality_guard(
                    response_json, source_pack["text"], versions, case_context_text=case_context_text,
                    known_ids=known_ids, mode=mode, stage1_visible_entity_ids=stage1_visible_entity_ids,
                    derived_intelligence_ids=derived_intelligence_ids, defer_scenario=defer_scenario,
                    scenario_fallback_used=scenario_fallback_used)
                remaining_failed_checks = [c["name"] for c in guard_result["checks"] if not c["passed"]]
            except geo_llm_common.GeoLLMError as repair_exc:
                _record(f"repair:{target_stage}_failed", started, result=None, attempts_allotted=1,
                        error=repair_exc)
                remaining_failed_checks = initial_failed_checks
        else:
            completeness_status = "incomplete_budget_exhausted"
            repair_skipped_due_to_budget = True

    if remaining_failed_checks and completeness_status == "complete":
        completeness_status = "incomplete_after_repair"

    # コード側のマージ・導出処理自体のバグ検知用（リトライはしない。失敗時は例外にする）。
    # ただし「配列が空（minItems違反）」だけは、段階実行の設計方針（「最低1件」等の意味的要件は
    # Schemaではなくquality guard側で検査する）に合わせて失敗にせず、不完全として続行する。
    _strip_nulls_disallowed_by_master_schema(response_json)
    empty_required_arrays = _validate_final_response(response_json)
    if empty_required_arrays and completeness_status == "complete":
        completeness_status = "incomplete_after_repair"

    if persist:
        response_row = client.insert("geo_expert_responses", [{
            "request_id": request_row["request_id"], "source_pack_id": source_pack_row["source_pack_id"],
            "mode": mode, "prompt_version": STAGED_PROMPT_VERSION, "knowledge_version": knowledge["version"],
            "model": model, "response_json": response_json,
        }])[0]

        evaluation_row = client.insert("geo_quality_evaluations", [{
            "response_id": response_row["response_id"], "checks": guard_result["checks"],
            "passed_count": guard_result["passed_count"], "failed_count": guard_result["failed_count"],
        }])[0]
    else:
        response_row = {
            "response_id": str(uuid.uuid4()), "request_id": request_row["request_id"],
            "source_pack_id": source_pack_row["source_pack_id"], "mode": mode,
            "prompt_version": STAGED_PROMPT_VERSION, "knowledge_version": knowledge["version"],
            "model": model, "response_json": response_json,
        }
        evaluation_row = {
            "evaluation_id": str(uuid.uuid4()), "response_id": response_row["response_id"],
            "checks": guard_result["checks"], "passed_count": guard_result["passed_count"],
            "failed_count": guard_result["failed_count"],
        }

    if persist and persist_case_side_effects and case_id and case_context is not None:
        _record_case_retrieval_trace(client, case_id=case_id, expert_request_id=request_row["request_id"],
                                      context=case_context)
        client.update("geo_cases", {"case_id": f"eq.{case_id}"}, {"final_output": response_json})

    # Phase 7-2L(Selection-to-Profile Handoff Integrity、ユーザー指示): 最終response_json
    # （すべての後処理を経た後）を対象に、Selection段階の決定がProfile生成へ実際に
    # 反映されたかを検知するだけの決定的チェック（自動補完・entity_id書き換えなし）。
    selection_profile_handoff = _validate_selection_profile_handoff(
        response_json, actor_candidate_selection_diagnostics)

    execution_metadata = {
        "execution_mode": "staged", "profile": profile, "mode": mode,
        "executed": {"stage1": True, "stage2_modules": modules, "stage3_repair": repair_attempted},
        "routing_reason": f"mode={mode}, profile={profile} -> stage2_modules={modules}",
        "stage_metrics": stage_metrics,
        # 修復前の失敗理由（geo_quality_evaluations.checksは最終版のみ保存されるため、
        # 初回失敗の原因分析にはここが唯一の記録になる）
        "initial_failed_checks_detail": initial_failed_checks_detail,
        # Phase R1 Chunk4: 既存CallBudget（Stage1/Stage2/Repair、合計3）とは別立ての
        # Research/SearchBudget消費状況をここに独立記録する（既存total=3には影響しない）。
        "research": research_metadata,
        # Situation Trajectory Intelligence v1.2（ユーザー指示、test-only）: Evidence
        # Request/Existing Evidence Satisfaction Check/Retrievalの診断情報。Public
        # Response Schemaには一切含まれない（trajectory_evidence_requests自体はマージ前に
        # stage1_dataから除去済み）。trajectory_v1_2_enabled()がOFFならNone。
        "trajectory_v1_2": trajectory_v1_2_diagnostics,
        # Expert Context Architecture v1.3（ユーザー指示、test-only）: Requirement/
        # Discriminator/Coverage Check/Retrievalの診断情報。Public Response Schemaには
        # 一切含まれない（Fieldはマージ前にstage1_dataから除去済み）。
        # expert_context_v1_3_enabled()がOFFならNone。
        "expert_context_v1_3": expert_context_v1_3_diagnostics,
        # Foundation Context Builder v1（ユーザー指示）: Retrieval診断情報（keywords/
        # as_of_date/kept_total/rejected_*）。Public Response Schemaには一切含まれない
        # （そもそもresponse_jsonのFieldではない、user_promptへの文字列注入のみ）。
        # foundation_context_v1_enabled()がOFFならNone。
        "foundation_context_v1": foundation_context_diagnostics,
        # Context Availability / Activation Telemetry（ユーザー指示Phase C）: Source/
        # Situation Trajectory/Actor/Relationship/Media-Perception/Structuralの6種について
        # status/item_count/token estimate/activation_method/evidence_ids/call1_includedを
        # 決定的に記録する（Silent Context Failureを無くす）。Public Response Schemaには
        # 一切含まれない。
        "foundation_context_status": foundation_context_status,
        # Phase 6A-4(Repair/Rebinding、ユーザー指示): Stage1未提示entity_id参照の
        # rebind/unresolved仕分け結果（各itemにactor_ref/original_invalid_reference/
        # resolved_entity_id/resolution_method/confidenceを含む）。
        "entity_reference_resolutions": entity_reference_resolutions,
        # Phase 6A-1(Saudi-Israel Actor Routing、ユーザー指示): 内容は変更しない
        # Observability専用の記録（詳細は_detect_unused_rich_actor_intelligence参照）。
        "unused_rich_actor_intelligence": unused_rich_actor_intelligence,
        # Phase 6D-2(Claim source_ref Referential Integrity、ユーザー指示): 無効だった
        # claims[].source_refのnull化結果（各itemにclaim_text/original_ref/validity/
        # resolution_method/resolved_ref/unresolved_reasonを含む、異常のみ記録）。
        "claim_source_ref_resolutions": claim_source_ref_resolutions,
        # Post-Freeze Integrity Audit R2 Hotfix(ユーザー指示、2026-09-22): R4検証時、
        # quality_evaluation側にしかこれらのGuard発火結果が記録されておらず、
        # geo_audit_logs.detail（execution_metadata）からは追跡できないという
        # Observability Gapが発見された（検証Agentが実際に該当監査ログを調べて発覚）。
        # quality_evaluationは戻り値のみでDB永続化されないため、以後の事後監査で
        # 同じ手戻りが起きないよう、ここにも記録する。
        "removed_fabricated_ids": removed_ids,
        "stub_evidence_adjustments": stub_evidence_adjustments,
        "temporal_grounding_adjustments": temporal_grounding_adjustments,
        # Phase 6E-5(Entity Identity Integrity、ユーザー指示): entity_idはStage1提示範囲
        # 内で正当だがactor名が矛盾していたケースのnull化結果（各itemにentity_id/
        # model_supplied_name/canonical_name/identity_match_status/resolution_methodを
        # 含む、異常のみ記録）。
        "entity_identity_mismatches": entity_identity_mismatches,
        # Phase 7-1A(Controller Packaging、ユーザー指示): Source Pack/Actor Intelligence
        # Pack本文中に言及はあるがcanonical entity未解決の制度的Controller候補
        # （Runtime-only、DB永続化なし）。
        "institutional_controller_candidates": institutional_controller_candidates,
        # Phase 7-1A Telemetry(ユーザー指示): Controller/Rich Actor Candidateごとの
        # AVAILABLE/USED/CONSIDERED_NOT_MATERIAL/UNAVAILABLE/DATA_READINESS_LIMITED。
        "controller_intelligence_utilization": controller_intelligence_utilization,
        # Phase 7-1C-R2(Stage2 Recovery、ユーザー指示、Telemetry): Stage2 scenario生成が
        # SUCCESS_FIRST_ATTEMPT/RECOVERED_SECOND_ATTEMPT/FAILED_AFTER_RECOVERY/
        # STUB_FALLBACK/RECOVERY_SKIPPED_BUDGETのいずれだったかを、最終出力だけからは
        # わからないRecovery発生を隠さないために明示する（defer_scenario=Falseの経路では
        # None）。calls_used/call_budget_remainingはCallBudget(total=4)の実消費・残量、
        # recovery_skipped_due_to_budget/repair_skipped_due_to_budgetは、budget不足で
        # Stage2 Recovery・Repairそれぞれが実施できなかったかを個別に区別する
        # （「Important edge case」: Stage1 Recovery+Stage2 Recovery+Repairが全て必要になる
        # 5コール相当のrunが実データで衝突するかをこれで観測する）。
        "stage2_recovery_status": stage2_recovery_status,
        "calls_used": budget.used,
        "call_budget_remaining": budget.remaining,
        "recovery_skipped_due_to_budget": stage2_recovery_status == "RECOVERY_SKIPPED_BUDGET",
        "repair_skipped_due_to_budget": repair_skipped_due_to_budget,
        # Phase 7-2K-A/B/C(ユーザー指示、test-only、Telemetry): Deterministic Candidate
        # Setの内容とLLM Selectionの整合性検証結果（module未発火時はNone）。
        "actor_candidate_set": actor_candidate_set,
        "actor_candidate_selection_diagnostics": actor_candidate_selection_diagnostics,
        # Phase 7-2L(ユーザー指示、Observability専用): selected entity ∉ key_actor_profiles
        # のIntegrity violationを検知するだけ（自動補完・entity_id書き換えは一切しない）。
        "selection_profile_handoff": selection_profile_handoff,
    }
    # v1.2 実行メタデータの保存先: geo_quality_evaluations.checksへは混在させず（Quality Guard
    # の判定結果と生成プロセスのメタ情報を同じJSONBに混ぜない）、既存のgeo_audit_logs.detail
    # （スキーマレスjsonb、target_table/target_idで後から検索可能）を再利用する。
    # 新規カラム・新規テーブルは追加しない（DBマイグレーションなし）。
    if persist:
        geo_audit.log_audit(
            client, actor="geo_expert_engine", action="expert_request_execution_metadata",
            target_table="geo_expert_responses", target_id=response_row["response_id"],
            detail=execution_metadata,
        )

    # v1.2 Persistent Preference Candidate生成（post-processing、Feature Flag既定無効）。
    # 生成に失敗しても回答の返却はブロックしない。persist=False時は評価用dry-runのため
    # 呼び出し自体をスキップする（DB書き込みをゼロに保証する）。
    pp_candidates = []
    if persist and persist_case_side_effects and case_id and foundation_pack is not None:
        try:
            pp_candidates = geo_persistent_preference_service.propose_persistent_preference_candidates(
                client, response_json=response_json, foundation_pack=foundation_pack,
                actor_id="geo_expert_engine",
            )
        except Exception as e:
            geo_audit.log_audit(
                client, actor="geo_expert_engine", action="persistent_preference_candidate_error",
                target_table="geo_expert_responses", target_id=response_row["response_id"],
                detail={"error": f"{type(e).__name__}: {e}"},
            )

    return {
        "request": request_row,
        "source_pack": source_pack_row,
        "response": response_row,
        "quality_evaluation": {
            **evaluation_row,
            "completeness_status": completeness_status,
            "initial_failed_checks": initial_failed_checks,
            "repair_attempted": repair_attempted,
            "remaining_failed_checks": remaining_failed_checks,
            "removed_fabricated_ids": removed_ids,
            "stub_evidence_adjustments": stub_evidence_adjustments,
            "temporal_grounding_adjustments": temporal_grounding_adjustments,
            "confidence_adjustments": confidence_adjustments,
            "control_type_adjustments": control_type_adjustments,
            "actor_provenance_touched": actor_provenance_touched,
            "historical_evidence_separations": historical_evidence_separations,
            "pattern_applicability_fallbacks": pattern_applicability_fallbacks,
            "actor_evidence_separations": actor_evidence_separations,
        },
        "usage": usage_total,
        "execution_metadata": execution_metadata,
        "persistent_preference_candidates": pp_candidates,
    }


# ---- Feature Flag / 実行方式パラメータによる一括方式↔段階実行方式の切り替え ----

EXECUTION_MODE_ENV = "GEO_EXPERT_EXECUTION_MODE"
EXECUTION_MODES = ("staged", "monolithic")
DEFAULT_EXECUTION_MODE = "staged"


def resolve_execution_mode(explicit: str | None = None) -> str:
    """呼び出し元が明示的に指定すればそれを使い（例: API層でのテスト用オーバーライド）、
    未指定の場合は環境変数GEO_EXPERT_EXECUTION_MODEを見る（既定"staged"）。問題発生時は
    環境変数を"monolithic"に変更し再起動するだけで既存の一括方式へ即座にロールバックできる
    （コード変更・デプロイ不要）。"""
    mode = explicit or os.environ.get(EXECUTION_MODE_ENV) or DEFAULT_EXECUTION_MODE
    if mode not in EXECUTION_MODES:
        raise ValueError(f"未対応のexecution_modeです: {mode}（{EXECUTION_MODES}のいずれか）")
    return mode


def run_expert_request_with_mode(client, azure_client, model: str, mode: str, theme_id: str | None,
                                  query_text: str, draft_text: str | None = None, *,
                                  case_id: str | None = None, profile: str = "standard",
                                  execution_mode: str | None = None,
                                  actor_intelligence_enabled: bool | None = None) -> dict:
    """API層から呼ぶ統一エントリポイント。execution_modeで一括方式(monolithic)／
    段階実行方式(staged)を切り替える（未指定時はresolve_execution_mode参照）。
    戻り値の形は両方式で統一されている（request/source_pack/response/quality_evaluation/
    usage/execution_metadata/persistent_preference_candidates）。

    actor_intelligence_enabled: 既定None（operation-scoped override、2026-09-25追加）。
    Noneの場合は既存のglobal env flag（モジュール関数actor_intelligence_enabled()）へ
    そのまま委ねる。True/False明示時はglobal flagに関わらずその値を使う
    （Reading生成専用、Consultation/Draft/RefineDesign等は引き続きNoneのまま呼ぶ）。"""
    resolved = resolve_execution_mode(execution_mode)
    if resolved == "monolithic":
        return run_expert_request(client, azure_client, model, mode, theme_id, query_text, draft_text,
                                   case_id=case_id, actor_intelligence_enabled=actor_intelligence_enabled)
    return run_expert_request_staged(client, azure_client, model, mode, theme_id, query_text, draft_text,
                                      case_id=case_id, profile=profile,
                                      actor_intelligence_enabled=actor_intelligence_enabled)
