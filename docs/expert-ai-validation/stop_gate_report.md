# Expert AI Depth & Grounding Upgrade — Stop Gate Report

依頼書24章の22項目に沿って報告する。ここで一度停止し、追加チューニング・Media
Intelligence再統合には進まない（依頼書の指示通り）。

## 1. 実装変更一覧

| ファイル | 変更内容 |
|---|---|
| `geo_expert_engine.py` | `attach_actor_intelligence_provenance`/`normalize_control_type`/`cap_confidence_by_evidence_class`呼び出しをStage merge・修復パス双方に追加。`quality_evaluation`へ`confidence_adjustments`/`control_type_adjustments`/`actor_provenance_touched`を追加。 |
| `geo_expert_schema.py` | `hypothesis_register`をStage2 `situation_actor_assessment`モジュールへ追加。5種の新規Prompt Guidance定数（Deep Reasoning Sequence/Competing Hypotheses/Actor Pattern Applicability/Evidence Substitution/Weak Analogy）を全Mode Guidanceへ追記。 |
| `geo_expert_quality_guard.py` | 決定的post-processing関数3種（`attach_actor_intelligence_provenance`/`normalize_control_type`/`cap_confidence_by_evidence_class`）+ 新規Quality Guard 7種を追加。 |
| `geo_actor_intelligence_service.py` | `build_actor_intelligence_pack`へ`profile_source`/`pattern_ids`/`exception_episode_ids`/`historical_episode_ids`を追加。`_relevant_precedents_for_entity`のhardcoded `status="active"`をtest-mode override可能に修正（既存バグの修正）。 |
| `schemas/geo_expert_ai_response.schema.json` | `key_actor_profiles[]`へ`actor_intelligence_provenance`/`pattern_applicability_assessment`/`actor_model_challenge`、`key_point_assessment`へ`control_type_adjustment_reason`、`situation_actor_assessment[]`/media hypothesesへ`evidence_class`、`main_scenario`へ`historical_analogies_used`、トップレベルへ`hypothesis_register`を追加。 |
| `tests/test_geo_expert_depth_grounding_upgrade.py`（新規） | 11件のUnit Test（要求最低8件を超過）。 |
| 既存テスト4ファイル | `_strictify()`による新規必須フィールド追加に伴うFixture修正のみ（挙動変更なし）。 |

## 2. DB migration有無

**なし**。全変更は既存`response_json`/`structured_content`（jsonbカラム）の範囲内。

## 3. Actor grounding fix

`attach_actor_intelligence_provenance`がActor Intelligence Pack実データから
`profile_source`/`historical_episode_ids`/`pattern_ids`/`exception_episode_ids`を
**決定的に**（LLM自己申告に依存せず）転記する。Neutral実データで7件全ての
Key Actor Profileに欠落なく付与されたことを確認済み（Technical Acceptance Criteria
参照）。

## 4. control structure fix

`normalize_control_type`が`actor_controlled`+空`controlling_entity_ids`を
`unknown`へ決定的に降格する契約を実装。Neutral実データでは5ケース全てで
`actor_controlled`判定時に`controlling_entity_ids`が最初からpopulatedされており、
実際の降格発火は観測されなかった（Before Benchmark時点でも同様、Case4/5で
再現できなかったことは`before/scorecard.md`に既述）。Unit Testで契約自体は
決定的に検証済み。

## 5. conditional decision pattern implementation

Actor Intelligence Retrieval/Formattingに変更は加えていない（既存の
`_format_actor_intelligence_block`をそのまま使用）。Neutral実データでは、
Case2/3/4で命名されたPattern（例: `netanyahu_pattern_reject_illegitimate_process`）に
`current_similarities`/`current_differences`/`applicability`を伴う条件付き適用性評価が
実際に生成されており、依頼書6章が求める「条件付きDecision Pattern」の形は
達成されている。ただしこれはPrompt Guidance（`_ACTOR_PATTERN_APPLICABILITY_NOTE`等）
による誘導の効果であり、Retrieval側のデータ構造自体（`pattern`/`applies_when`/
`does_not_apply_when`等の依頼書5章の厳密なJSON形状）は未実装（Schema変更を
`pattern_applicability_assessment`側のみに絞ったため）。

## 6. actor pattern applicability implementation

`pattern_applicability_assessment[]`をSchemaへ追加、Guard
`_actor_pattern_applicability_present`で「Patternを参照しながら適用性評価が
無い」ケースを検出。Neutral実データでCase1/Case5の各1名（Trump, Xi, Kim）で
実際にこのGuardがFailを検出——**Guardが機能していることの実証であり、かつ
P0-3がまだ完全に遵守されていない実態を正しく捕捉した**。

## 7. actor pattern failure test

`actor_model_challenge`をSchemaへ追加、Guard`_actor_model_challenged`で
「実質的なActor Profileを持つのにChallengeが無い」ケースを検出。Neutral
5ケース・7 Actor全てで`actor_model_challenge`が生成され、内容も
`outcome=inconclusive`＋具体的な条件（例: Case2「ワグネル反乱後に集団指導制へ
移行した場合」）を伴う実質的なものだった。

## 8. Evidence Substitution Guard

`evidence_class`Enumを`situation_actor_assessment[]`/media hypothesesへ追加、
`cap_confidence_by_evidence_class`が`historical_actor_pattern`/
`historical_media_pattern`/`model_general_knowledge`/`unsupported_assumption`の
場合にConfidenceを強制的に「低」へ引き下げる。Unit Testで契約は検証済み。
**ただし`key_point_assessment`にはこのフィールドを追加しておらず**、実データで
`evidence_attribution_not_conflated`（既存M9 Guard）がCase2/3/4/5で新規Fail
——Historical Episode IDが`key_point_assessment.supporting_evidence_ids`へ
Current Evidenceであるかのように引用される実例が発生した。これは**P0-4の
適用範囲漏れ**として後述22項目で報告する。

## 9. Weak Analogy Guard

`historical_analogies_used[]`をSchemaへ追加、Guard
`_weak_analogy_not_used_as_strong_evidence`で`analogy_strength=weak`＋
`main_scenario.confidence=高`の組合せを検出。Neutral実データではCase2/3/4で
Historical Analogyが実際に使用され（例: Case2「ソ連アフガニスタン戦争との類推」を
`analogy_strength=weak`と明示）、いずれも`main_scenario.confidence`は「中」以下
——Guard発火は無かったが、LLMが自発的にWeak Analogyを弱いConfidenceとして
扱う挙動が観測された（Prompt Guidanceの効果と考えられる）。

## 10. Hypothesis Pruning

`hypothesis_register[]`をStage2へ追加、Guard`_hypothesis_pruning_present`で
「2件未満」または「全件retained」を検出。Neutral 5ケース全てでH0-H4の5件が
生成され、いずれもretained/rejected/unresolvedが混在（全件retainedのケースは
0件）。Guard発火なし——依頼書14章の要求を実データで達成。

## 11. Media integration changes

**なし（依頼書0章・15章の通り、P0完了後まで意図的に据え置き）**。全Neutral
ケースでMedia IntelligenceはOFF。

## 12. Quality Guard追加

7件追加（`actor_intelligence_provenance_present`/`actor_pattern_applicability_present`/
`unsupported_actor_assumption_not_promoted`/`weak_analogy_not_used_as_strong_evidence`/
`evidence_substitution_detected`/`hypothesis_pruning_present`/`actor_model_challenged`）。
全て非repair対象（既存方針通り、Human Reviewの判断材料）。

## 13. Regression結果

**Expert AI / Actor Intelligence関連スコープ**（`geo_expert*`/`geo_actor_intelligence*`/
`geo_media_intelligence*`/`geo_intelligence_service*`/`geo_persistent_preference*`/
`geo_foundation_pack*`/`geo_depth_grounding`）: **170 passed, 0 failed**
（既存Regression + 新規11件を含む）。

プロジェクト`tests/`全体（`world_monitor_dashboard`の共有conftest経由で混入する
無関係プロジェクトのテストを含む）: 402 passed, 6 failed。**この6件
（`test_sustainability_article_selector.py`×5、`test_geo_official_crawler.py`×1）は
Expert AI Depth & Grounding Upgradeのスコープ外のモジュール**（sustainability記事
選定・crawler観測、日付ベースの`since_days`フィルタ関連）であり、本タスクで一切
変更していないファイルに対するテストである。今回のP0修正が原因である可能性は
極めて低い（日付依存のFixtureが実行日の経過で陳腐化した可能性が高いが、本タスクの
スコープ外のため原因調査は行っていない）。

## 14. Original 5-case Before/After score

**未実施（依頼書23章の通り、優先度は明示的にB=Neutralであり、これを優先した）**。
Before（`docs/expert-ai-validation/before/`）はCase1のみ確実にクリーンなPre-Fix
データであり、Case2-5は手順ミスによりPre-Fix状態が保証されない
（詳細は`before/scorecard.md`）。Original PromptでのAfter再実行は、時間・Token
予算の制約から今回は実施しなかった——**Residual Gapとして22項目で報告する**。

## 15. Neutral 5-case score

`docs/expert-ai-validation/neutral/scorecard.md`参照。16点Rubric合計:
Case1=14, Case2=15, Case3=15, Case4=15, Case5=12（平均14.2/16）。

## 16. Independent Frame Discovery結果

Case1=2, Case2=2, Case3=2, Case4=2, Case5=1。
Acceptance Criteria（>=1が4/5、=2が3/5）は**共に達成**（5/5が>=1、4/5が=2）。

## 17. 各Caseで自力生成したFrame-breaking hypothesis

| Case | Neutral質問が禁じた二項対立 | AIが自力発見したFrame |
|---|---|---|
| Trump-Xi | 恒久封じ込め vs 交渉カード | 半導体・AIが軍事・安全保障上の中核技術であるという構造要因（政権交代に左右されない持続性の説明） |
| Putin | 停戦か継戦か | Putin政権の存続とエリート・治安機構の統制維持（ワグネル反乱を根拠とする体制生存フレーム） |
| Netanyahu | 安全保障 vs 個人政治 | Netanyahu個人の政権延命と法的責任回避（ICC不承認・独立調査委回避という具体的Evidence） |
| MBS | China shift vs strategic autonomy | MBSの王位継承・国内権力集中（外交はその手段として機能） |
| Kim | anti-US bloc vs China dependency reduction | （限定的）核・ミサイル=体制保証という定型Frameのままretain。H1（経済転換）・H4（後継闘争）は検討されたが不採用。 |

## 18. Actor Generalization Before/After

Before: 全Actor共通で無条件の人物解説（`Trump=dealmaker`等）。
After: 5ケース中3ケース（Putin/Netanyahu/MBS）で"deep"（命名Pattern＋適用性評価
＋Actor Model Challengeが揃う）、2ケース（Trump/Xi、Kim）で"partial"
（適用性評価のみ欠落）。全5ケースで"generic"を脱している
（Acceptance Criteria: 4/5達成基準に対し5/5達成）。

## 19. General Knowledge使用量/箇所

全7 Actor Profile全てで`general_knowledge_used=true`。うちConfidence内訳:
Case1（Trump/Xi）=低×2（Guard問題なし）、Case2-5（Putin/Netanyahu/MBS/Kim）=中×4
（**Guard `unsupported_actor_assumption_not_promoted`がFail検出、後述22項目**）。

## 20. Unresolved failures

- `unsupported_actor_assumption_not_promoted`: 4/5ケースでFail（自動補正未実装、
  21項目参照）。
- `evidence_attribution_not_conflated`: 4/5ケース（Case2-5）でFail（`key_point_
  assessment`へのEvidence Class未適用、21項目参照）。
- `actor_pattern_applicability_present`: 2/5ケース（Case1, Case5）でFail
  （Pattern参照時にApplicability Assessmentが未生成）。
- `fact_claims_have_source_ref`/`stakeholders_grounded_in_source_pack`: 既存の
  既知の制約（本タスク開始前からの既存Gap、P0スコープ外）。

## 21. Azure Call count / tokens

全5ケース`calls=2`（CallBudget<=3を厳守）。合計token: prompt 402,448 +
completion 75,197 = 477,645 tokens（5ケース平均 95,529 tokens/ケース）。

## 22. P0/P1 residual gaps

1. **P0-1残存**: `general_knowledge_used=true`かつ`confidence=中/高`の組合せに対する
   決定的Confidence補正関数が未実装（Guard検出のみ）。`cap_confidence_by_
   evidence_class`と同型の補正（例:`cap_confidence_by_general_knowledge_flag`）を
   追加すれば技術的Acceptance Criteria「unsupported actor assumptionの
   Medium/High昇格=0件」を満たせる可能性が高い。
2. **P0-4残存**: `evidence_class`Enumが`key_point_assessment.supporting_evidence_
   ids`へ拡張されておらず、Historical Episode IDがCurrent Evidenceとして
   誤引用される実例（Case2-5共通）を防げていない。
3. **P0-3残存**: `pattern_applicability_assessment`の生成がLLMの裁量に委ねられており
   （Guardは検出のみで強制生成はしない）、Case1/Case5で欠落が実際に発生した。
4. Case4/MBSの`rejected_candidates[1].why_not_primary`にJSON構文の断片
   （`},{"`）がテキスト内へ混入する軽微な出力Glitchを確認（Case1でも同様の
   軽微な混入を確認）。JSON自体は有効でParsing失敗はしていないが、LLM出力側の
   品質問題として記録する。
5. **P1-1（Media Intelligence統合）は今回完全に未着手**（依頼書の指示通り、
   意図的措置）。
6. Original Prompt（Non-Neutral）でのAfter再実行は未実施（14項目参照）。

---

## Acceptance Criteria 総合判定

| 分類 | 基準 | 判定 |
|---|---|---|
| Technical | 既存Regression pass | ○ Expert AIスコープ内170/170。スコープ外6件Failは無関係かつ調査対象外。 |
| Technical | CallBudget<=3 | ○ 全ケース2 |
| Technical | actor_controlled+空entity_ids=0件 | ○ 達成 |
| Technical | Key Actor provenance欠落=0件 | ○ 達成 |
| Technical | unsupported assumptionのMedium/High昇格=0件 | **✗ 未達（4/5件）** |
| Reasoning | Independent Frame Discovery>=1が4/5以上 | ○ 5/5達成 |
| Reasoning | Independent Frame Discovery=2が3/5以上 | ○ 4/5達成 |
| Reasoning | Actor Generalization改善が4/5以上 | ○ 5/5達成 |
| Evidence | Main ScenarioがGeneral Knowledge/Weak Analogy/Historical Patternのみ=0件 | ○ 達成 |

**総合**: Reasoning・Evidence区分の基準は全て達成。Technical区分のうち1項目
（unsupported actor assumptionのMedium/High昇格）が未達——上記「22. P0/P1
residual gaps」1.が原因であり、対応には`cap_confidence_by_evidence_class`と
同型の追加補正関数1つの実装が必要と見込まれる。

依頼書24章の指示通り、ここで一度停止する。追加チューニング・Media Intelligence
再統合・Original Prompt After再実行は、ユーザーの明示的な指示があるまで着手しない。
