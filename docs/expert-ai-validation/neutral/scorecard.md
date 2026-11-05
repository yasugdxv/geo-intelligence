# Neutral Prompt Validation — 5-Case Scorecard

## 実行条件（Freeze確認）

Neutral Benchmark実行後にコード変更は一切行っていない。実行後の再計算Fingerprintは
以下の通りで、Before Benchmark完了時点（P0修正完了直後）の記録と完全一致している
（実行中・実行後の混入なし）。

| ファイル | fingerprint |
|---|---|
| geo_expert_engine.py | a9761898de101931 |
| geo_expert_schema.py | 358640565bdd689c |
| geo_expert_quality_guard.py | b59c0e221ef5d456 |
| geo_actor_intelligence_service.py | a4728e75b6b9c3ab |
| geo_media_intelligence_service.py | aa61f22f460b32a5 |
| geo_knowledge_item_schemas.py | 5ab8d2935eebf4b7 |
| schemas/geo_expert_ai_response.schema.json | 8e39537bcce80c2f |
| geo_persistent_preference_service.py | 47e2b31ccec1b1c5 |
| geo_foundation_pack_service.py | 11dd58266c423f91 |

Model: Global-AzureOpenAI-DEV-gpt-5.1。Feature Flag: Actor Intelligence ON
（test-mode、`profile_lookup_statuses=("active","candidate")`）、Media Intelligence OFF。
mode=case_analysis、profile=deep。実行日: 2026-08-31。
Query textは依頼書20章の**Neutral版**（質問側からAlternativeを一切示唆しない表現）。
CallBudget: 全5ケースとも`calls=2`（Stage1+Stage2、修復発火なし）でCallBudget<=3を厳守。

## Token使用量

| Case | prompt | completion | total |
|---|---|---|---|
| Case1 Trump-Xi | 80,383 | 14,109 | 94,492 |
| Case2 Putin | 79,658 | 14,133 | 93,791 |
| Case3 Netanyahu | 82,418 | 15,379 | 97,797 |
| Case4 MBS | 80,648 | 16,102 | 96,750 |
| Case5 Kim | 79,341 | 15,474 | 94,815 |
| **合計** | 402,448 | 75,197 | 477,645 |

---

## 16点Rubric（8項目 × 0/1/2、既存Rubric）

項目: Key Point / Key Actor / Actor Depth / Surface Escape / Hypothesis Breadth /
Evidence Testing / Self-Challenge / Scenario

| 項目 | Case1 Trump-Xi | Case2 Putin | Case3 Netanyahu | Case4 MBS | Case5 Kim |
|---|---|---|---|---|---|
| Key Point | 2 | 2 | 2 | 2 | 1 |
| Key Actor | 2 | 2 | 2 | 2 | 2 |
| Actor Depth | 1 | 2 | 2 | 2 | 1 |
| Surface Escape | 2 | 2 | 2 | 2 | 1 |
| Hypothesis Breadth | 2 | 2 | 2 | 2 | 2 |
| Evidence Testing | 1 | 1 | 1 | 1 | 1 |
| Self-Challenge | 2 | 2 | 2 | 2 | 2 |
| Scenario | 2 | 2 | 2 | 2 | 2 |
| **合計 /16** | **14** | **15** | **15** | **15** | **12** |

**判定根拠の要旨**（詳細はper-case節）:
- **Actor Depth**がCase1/5で1点なのは、`relevant_decision_patterns`が存在するのに
  `pattern_applicability_assessment`が`null`のまま（`actor_pattern_applicability_present`
  Guardが実際にFailを検出、後述）。Case2/3/4は具体的なsimilarities/differences/
  applicability=mediumを伴う実質的な条件付きPattern適用性評価がある。
- **Surface Escape**がCase5で1点なのは、H0（Surface: 核・ミサイル=体制保証ドクトリン）
  がそのままPrimaryとしてretainされ、Neutral質問が禁じた二項対立
  （anti-US bloc vs China dependency reduction）以外のFrame自体は検討したものの
  （H1経済転換仮説、H4後継闘争仮説は共にunresolved/rejected）、最終的に採用された
  Frameは北朝鮮分析の定型的な読み筋（核=体制保証）に留まった。
- **Evidence Testing**が全Case共通で1点なのは、既知の限定的Guard未達
  （`fact_claims_have_source_ref`/`evidence_attribution_not_conflated`/
  `unsupported_actor_assumption_not_promoted`、詳細は「Technical Acceptance
  Criteria」節）による。

---

## Independent Frame Discovery（新規Diagnostic、0/1/2）

| Case | Score | 根拠 |
|---|---|---|
| Case1 Trump-Xi | **2** | 禁止された二項対立（恒久封じ込め vs 交渉カード）ではなく、「半導体・AIが軍事・安全保障上の中核技術であること」という構造要因へPrimary Key Pointを再定義。H4（第三国・企業主導）という追加Frame-breaking仮説も検討の上rejected。 |
| Case2 Putin | **2** | 禁止された二項対立（停戦か継戦か）ではなく、「Putin政権の存続とエリート統制」という体制生存フレームへ再定義（ワグネル反乱を根拠に）。さらにH3で「反乱後の意思決定が集団指導制へ移行した可能性」というActor-model-breaking仮説をunresolvedとして正直に保持。 |
| Case3 Netanyahu | **2** | 禁止された二項対立（安全保障 vs 個人政治）のうちH0（安全保障最優先）を明示的にrejectし、H1（政権延命・法的責任回避が上位目的）をretain。ICC不承認・独立調査委回避という具体的Evidenceで裏付け。H4（軍・官僚主導、Netanyahuは顔役に過ぎない）も検討の上reject。 |
| Case4 MBS | **2** | 禁止された二項対立（China shift vs strategic autonomy）ではなく、「MBSの王位継承・権力集中」という上位目的フレームへ再定義。外交の多極化バランスをこの上位目的の手段として説明。H4（宗教・イデオロギー主導）も検討の上reject。 |
| Case5 Kim | **1** | H0（核・ミサイル=体制存続の最終保証）がそのままPrimaryとしてretain。これは北朝鮮分析における定型的読み筋であり、禁止された二項対立からは外れているものの、真に意味のあるFrame-break（H1経済転換、H4後継闘争）はいずれもunresolved/rejectedのまま採用されず。 |

**Acceptance Criteria照合**:
- Independent Frame Discovery >= 1: **5/5ケース**（基準: 最低4/5） → 達成
- Independent Frame Discovery = 2: **4/5ケース**（Case1,2,3,4）（基準: 最低3/5） → 達成

---

## Actor Generalization: Before(generic) → After(partial/deep)

Before（依頼書のP0-3節が示す実例、5-Case Frozen Benchmark時点）:
`Trump=dealmaker` / `Xi=long-term/regime stability` 等、条件なしの一般的人物解説のみ。

| Case | Actor | After評価 | 根拠 |
|---|---|---|---|
| Case1 | trump, xi | **partial** | `persistent_preference`+`relevant_decision_patterns`+`actor_model_challenge`は具体的だが、`pattern_applicability_assessment`が`null`のためPattern適用性の条件（similarities/differences/applicability）が欠落。 |
| Case2 | putin | **deep** | Decision Pattern 2件それぞれにcurrent_similarities/current_differences/applicability=mediumを伴う適用性評価があり、`actor_model_challenge`もH3（集団指導制移行の可能性）と直結した具体的reasoningを持つ。 |
| Case3 | netanyahu | **deep** | 命名されたPattern2件（`netanyahu_pattern_reject_illegitimate_process`等）にapplicability=mediumの適用性評価が付随。ICC/独立調査委という具体的Historical Episodeで裏付け。 |
| Case4 | MBS | **deep** | 命名されたPattern2件（`mbs_pattern_official_framing_vs_rival_removal`等）にapplicability=mediumの適用性評価が付随。 |
| Case5 | kim_jong_un | **partial** | `persistent_preference`+`relevant_decision_patterns`は具体的だが、`pattern_applicability_assessment`が`null`（Case1と同型のGap）。 |

**Acceptance Criteria照合**: 5ケース中最低4ケースでgeneric→partial/deepへ改善 →
**5/5ケースで達成**（Case1・Case5もBefore時点の無条件人物解説からは明確に前進しており、
"generic"には該当しない）。

---

## Technical Acceptance Criteria（実データでの実証結果）

| 基準 | 結果 | 詳細 |
|---|---|---|
| `actor_controlled`かつ`controlling_entity_ids=[]`の件数 = 0 | **達成（0件）** | 全5ケースの`key_point_assessment`本体・`candidate_key_points[]`を全数確認。`actor_controlled`と判定された全項目でIDが1件以上populatedされていた（`normalize_control_type`による決定的補正が機能する前に、そもそもLLMが空で出力するケース自体が今回は発生しなかった）。 |
| Key Actor Provenance欠落件数 = 0 | **達成（0件）** | 全7つの`key_actor_profiles`エントリ（Case1×2, Case2×1, Case3×1, Case4×1, Case5×1）全てに`actor_intelligence_provenance`が`attach_actor_intelligence_provenance`により決定的に付与されていた。 |
| unsupported actor assumptionのMedium/High昇格 = 0 | **未達（4/5件で発生）** | Case2/3/4/5で`general_knowledge_used=true`かつ`confidence=中`の組合せが発生し、`unsupported_actor_assumption_not_promoted`Guardが実際にFailを検出した。**Guardは検出したが、自動でConfidenceを引き下げる決定的補正関数が存在しない**（`cap_confidence_by_evidence_class`は`evidence_class`起点のみを補正し、`general_knowledge_used`起点の補正が未実装）。これは実装のGapであり、Stop Gate「22. P0/P1 residual gaps」で報告する。 |
| Main ScenarioがGeneral Knowledge/Weak Analogy/Historical Patternのみに依拠する件数 = 0 | **達成（0件）** | 全5ケースの`main_scenario.supporting_evidence`はActor Intelligence起点のEvidence ID（`actor_intel:...`形式を含む）を伴っており、`historical_analogies_used`はCase2/3/4で使用されたが全て`analogy_strength=weak/medium`と明示され、`main_scenario.confidence`は全Case「中」または「低」（「高」なし）——`weak_analogy_not_used_as_strong_evidence`Guードが要求する「weak analogy + confidence=高」の組合せは発生していない。 |
| CallBudget <= 3 | **達成** | 全5ケース`calls=2`。 |
| 既存Regression全Pass | **達成**（Neutral実行前に確認済み、実行中コード変更なし） | Fingerprintが実行前後で完全一致（上記参照）。 |

---

## 新規発見された残存Gap（正直な報告、詳細はStop Gate report参照）

1. **`unsupported_actor_assumption_not_promoted`のGuard検出のみで自動補正なし** —
   上記Technical Criteria参照。P0-1の対応漏れ。
2. **`evidence_attribution_not_conflated`がCase2/3/4/5で新規Fail** — Historical
   Episode ID（Actor Intelligence由来のDerived Intelligence）が
   `key_point_assessment.supporting_evidence_ids`へ、あたかも今回のCase固有の
   Raw Evidenceであるかのように引用されるケースが実際に発生した（例:
   Case2 Putinの`d0ef206d-...`が`historical_episode_ids`と
   `key_point_assessment.supporting_evidence_ids`の両方に出現）。これは既存
   （M9世代）のGuardであり新規実装ではないが、**`key_point_assessment`には
   今回新設した`evidence_class`フィールドが無いため、`cap_confidence_by_
   evidence_class`によるConfidence補正の対象外になっている**——P0-4 Evidence
   Substitution Guardの適用範囲がsituation_actor_assessment/media hypothesesに
   限定され、Key Point Assessmentまでは及んでいないことを示す実データ上の証拠。
3. **`actor_pattern_applicability_present`がCase1/Case5で新規Fail** —
   `relevant_decision_patterns`を参照しながら`pattern_applicability_assessment`を
   空のまま出力するケースが実際に発生（P0-3のGuardが実際に機能し、まだ完全には
   遵守されていない実態を正しく検出）。
