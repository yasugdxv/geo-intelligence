# Original 5-case (Non-Neutral) After — Scorecard

**STATUS: `INVALID_FOR_REASONING_COMPARISON / RUNNER_CONFIGURATION_ERROR`**
（2026-09-01、原因確定・`docs/expert-ai-validation/actor-retrieval-audit/report.md`
参照）。本番コード・DBの問題ではなく、**本Runスクリプトが`actor_profile_
retrieval_status`を明示せず既定値`"available"`のまま実行したため**、Neutral v3が
使っていた候補（candidate/restricted）データがActor Intelligenceから一切
取得できなかった（全Actor共通でhistorical_episode_ids=0、Netanyahu/MBSは
profile自体none_available）。**正式なOriginal Before/After比較からは除外**する。
Neutral v3と同一の検証用Retrieval条件に固定したRunnerで再実行した結果を
`docs/expert-ai-validation/original_after_v2/`に別途保存する（正式比較はそちらを
参照）。

**この初回Runでも有効な参考結果として残せるもの**（Actor Intelligenceの充足度に
依存しない観測のため）: Evidence Discipline系の汎化（Confidence capping/Weak
Analogy/Historical-Current分離/Control Structure正規化）、unsupported confidence=
0/5、CallBudget遵守（全ケース2/2）。Actor Depth/Frame Breaking/Actor-model
challenge/Scenario qualityは本Runの数値を採用しない。

依頼書のOriginal 5質問（Alternativeを暗示する元の表現、Neutral化前）を、Frozen条件
（Thinking OS/Prompt/Schema/Actor Intelligence/Media Intelligence/Quality Guard/
Retrieval/Model/Feature Flag/CallBudgetを一切変更しない）のまま再実行した結果。

**実行日**: 2026-09-01（Neutral v3実行日=2026-08-31の翌日）。

## 0. 最重要の注記（正直な報告、結論を読む前に必ず確認）

**コードはFreeze条件通り完全に不変だったが、Actor Intelligence retrievalが返す
「データの中身」がNeutral v3実行時と比べて大きく痩せていた。** これは今回の
Original/Neutral比較の解釈に影響するため、最初に報告する。

| Case | Neutral v3（2026-08-31） | Original After（2026-09-01・今回） |
|---|---|---|
| Case1 Trump/Xi | 両者 `historical_episode_ids`=2件 | 両者 `historical_episode_ids`=**0件** |
| Case2 Putin | `historical_episode_ids`=2件、`situation_actor_assessment`=4件 | `historical_episode_ids`=**0件**、`situation_actor_assessment`=**0件（null）** |
| Case3 Netanyahu | `profile_source=actor_intelligence_candidate`、named pattern 2件、episode 2件、`situation_actor_assessment`=2件 | `profile_source=none_available`、`actor_intelligence_status=insufficient`、**Netanyahuに関するActor Intelligenceが一切取得できず** |
| Case4 MBS | `profile_source=actor_intelligence_candidate`、named pattern 2件、episode 2件、`situation_actor_assessment`=3件 | `profile_source=none_available`、`actor_intelligence_status=insufficient`、`actor_ref`もentity_idの生UUIDのまま（**MBSという名前すら解決できていない**） |
| Case5 Kim | `historical_episode_ids`=2件、`situation_actor_assessment`=5件 | `historical_episode_ids`=**0件**、`situation_actor_assessment`=**0件（null）** |

Freeze確認用Fingerprint（実行前に再確認、コード自体は不変）:

| ファイル | Neutral v1時点 | 今回(Original After実行時) | 判定 |
|---|---|---|---|
| geo_expert_schema.py | 358640565bdd689c | 358640565bdd689c | 一致(不変) |
| geo_actor_intelligence_service.py | a4728e75b6b9c3ab | a4728e75b6b9c3ab | 一致(不変) |
| geo_media_intelligence_service.py | aa61f22f460b32a5 | aa61f22f460b32a5 | 一致(不変) |
| geo_knowledge_item_schemas.py | 5ab8d2935eebf4b7 | 5ab8d2935eebf4b7 | 一致(不変) |
| geo_persistent_preference_service.py | 47e2b31ccec1b1c5 | 47e2b31ccec1b1c5 | 一致(不変) |
| geo_foundation_pack_service.py | 11dd58266c423f91 | 11dd58266c423f91 | 一致(不変) |
| geo_expert_engine.py / geo_expert_quality_guard.py / schemas/*.json | v2.5/v2.6でのEvidence Weighting修正を反映した値 | 同左（v3完了後、今朝09:35-09:37の修正以降は不変） | 一致(v3完了時点から不変) |

**結論**: コードは完全にFrozen。しかし`geo_actor_intelligence_service.py`が参照する
**DB側のActor Intelligenceデータ（`geo_knowledge_items`、knowledge_type=actor_profile
等）または Foundation Pack のEntity解決結果が、昨日と今日で実際に変わっている**
（Case4でPrimary Actorの`actor_ref`が名前解決すらされずUUIDのまま出力された事実が
その物的証拠）。したがって、以下のOriginal/Neutral比較のうち**Actor Depth・
Situation Actor Assessment関連の劣化は「Original Promptのせい」と「Retrieval側の
データ劣化のせい」を今回のデータだけでは完全に切り分けられない**。Key Point
Reasoning・Hypothesis生成・Evidence Discipline（confidence capping等）については
Actor Intelligenceデータの有無に関わらず観測可能なため、これらは比較的信頼できる
シグナルとして扱う。**この確認（なぜ今日Retrievalが痩せたのか）自体を、次の
診断候補として本報告の末尾で推奨する。**

---

## 1. Token / Call数

| Case | calls | prompt | completion | total | wall clock |
|---|---|---|---|---|---|
| Case1 Trump-Xi | 2 | 82,740 | 12,636 | 95,376 | 238.7s |
| Case2 Putin | 2 | 80,420 | 10,174 | 90,594 | 190.5s |
| Case3 Netanyahu | 2 | 83,141 | 12,595 | 95,736 | 272.2s |
| Case4 MBS | 2 | 76,212 | 5,797 | 82,009 | 114.9s |
| Case5 Kim | 2 | 80,107 | 11,098 | 91,205 | 502.6s |
| **合計** | 10 | 402,620 | 52,300 | 454,920 | 1319.0s |

CallBudget<=3を全ケース遵守（全ケースcalls=2、修復発火なし）。

---

## 2. 5つの比較軸（ユーザー指定の主軸、16点スコアより優先）

| 観点 | Beforeでの既知の問題 | Original Afterで観測された内容 |
|---|---|---|
| **Surface Escape** | Alternativeを質問側から与えていた | Case1/2/3/4は質問文の暗黙のフレーム（Case1: Actor中心の問い方／Case3: Netanyahu個人政策という問い方）から離れ、Key Pointを別の構造・制度・別Actorへ再定義した。ただしCase3/4はEvidence欠如への「回避的な」ピボットの側面が強く、意図的なFrame-breakingと区別しにくい（後述4章）。Case5は北朝鮮の定型フレーム（核=体制保証）をそのままPrimary採用しており、Neutralと同型の弱さが**Original側でも再現**された。 |
| **Actor Depth** | 人物一般論が多い | `persistent_preference`は全Actorで生成され続けているが、`pattern_applicability_assessment`・`actor_model_challenge`は**5ケース中0ケース**で生成されず（Quality Guard `actor_model_challenged` failが4/5ケースで発生）。Neutralで見られた「命名Pattern＋適用性評価」の"deep"水準は今回**1件も再現されなかった**——ただし0章の通りActor Intelligence Retrieval自体が痩せていたため、Reasoning側の後退と単純比較できない。 |
| **Evidence Discipline** | 一般知識で穴埋め | `cap_confidence_by_evidence_class`は今回も全ケースで機能し、`model_general_knowledge`/`historical_actor_pattern`/`structural_reference`起点のKey Point/Main Scenarioは軒並みconfidence=低へ正しく低下（Case3のみ中、後述）。Case4は`main_scenario.confidence=判断不能`という、Evidence皆無時に無理に断定しない、より保守的な新しい挙動が観測された。 |
| **Confidence** | 根拠以上に中程度 | Key Point confidence: 低×4、中×1（Case3）。Main Scenario confidence: 低×3、中×1(Case3)、判断不能×1(Case4)。根拠不足のケースほど正しくConfidenceが下がっている(Case4)。 |
| **Scenario** | plausible story化 | 全ケースで`alternative_scenarios`(1〜2件)・`strongest_counterargument`・`disconfirming_signal`が生成され、single-story化はしていない。Case4は「Evidenceが無いので判断不能」という、ストーリー化そのものを拒否する最も保守的な挙動を示した。 |

---

## 3. Case別 Primary Assessment / Actor Generalization / Frame Break / Confidence

| Case | Original After Primary Key Point | control_type | confidence | Frame Break |
|---|---|---|---|---|
| Case1 Trump-Xi | 先端半導体・AI技術の対中輸出・投資をどこまで制度的に制限するか | institutionally_constrained | 低 | **あり**（質問文の「主要な意思決定主体」というActor中心の誘導から、制度・枠組み中心へ再定義） |
| Case2 Putin | ウクライナのNATO・EU完全統合の阻止 | mixed | 低 | **限定的**（Neutralの「政権生存」フレームより後退し、より一般的な地政学解説に近い"NATO拡大阻止"フレームへ回帰。国内政権維持は明示的に候補から棄却） |
| Case3 Netanyahu | イランのホルムズ海峡封鎖能力とその行使可能性 | structural | 中 | **フレームというより話題そのものが移動**（Netanyahu個人の政策という問いに対し、Evidence欠如を理由にイランの構造的制約へ主題を差し替え。この移動自体はEvidence Disciplineとしては誠実だが、依頼された問いへの応答としては弱い） |
| Case4 MBS | サウジの対米・対中・地域外交は石油市場構造に規定される | structural | 低 | **Evidence皆無ゆえの撤退的フレーム**（MBS個人要因の候補は明示的に「今回は採用しない」と棄却、構造要因への「安全側」の後退） |
| Case5 Kim | 北朝鮮体制存続の最終保証としての核・ミサイル戦力維持 | mixed | 低 | **なし（Neutralと同型の弱さ）**。H0がそのままPrimary、経済転換仮説(H1)はunresolvedのまま不採用 |

**Actor Generalization**（named pattern・applicability・model challengeの有無）:

| Case | Actor(s) | pattern_applicability_assessment | actor_model_challenge | 評価 |
|---|---|---|---|---|
| Case1 | trump, xi | なし | なし | partial（persistent_preferenceのみ） |
| Case2 | putin | なし | なし | partial |
| Case3 | netanyahu, iran_state | なし | なし | **generic寄り**（Actor Intelligence自体insufficient） |
| Case4 | (entity_id生UUID) | なし | なし | **generic寄り**（Actor名の名前解決すら失敗） |
| Case5 | kim | なし | なし | partial |

Neutral v3で観測された"deep"水準（Case2/3/4で命名Pattern×2件＋適用性評価）は
**今回5ケース中0ケースで再現されず**。0章の通り、これはActor Intelligence
Retrieval自体の劣化と強く相関しており、Reasoning側だけの問題と断定できない。

---

## 4. Case1特別観察: Trump-Xi Frame Discoveryは浅いままか、Neutral固有だったか

Neutralの5-case系列では、Case1のIndependent Frame Discoveryが v1以降 **2→1のまま
回復しなかった**（Case2-4は1→2へ回復したのに対し、Case1のみ据え置き）。今回の
Original Afterで、この弱さがOriginal Prompt自体に起因する構造的な問題なのか、
Neutral Prompt特有の副作用だったのかを検証した。

**観測結果**: Original AfterのCase1は、
- Primary Key Pointを「先端半導体・AI技術の輸出・投資規制」という**制度枠組み**へ
  明確に再定義し、
- 4件のCandidate Key Pointを比較した上で3件を具体的理由とともに棄却し、
- disconfirming_signal（「主要同盟国が規制を大幅緩和し、短期的な政治シグナルへ
  明確に転換したら誤り」）を明示し、
- Hypothesis Register 5件中2件retained/2件rejected/1件unresolvedと、単一ストーリーに
  収斂させていない。

これは、Neutralで観測された「Case1のみFrame Discoveryが低いまま」という弱さが
**再現されなかった**ことを意味する。少なくとも今回のOriginal Prompt実行では、
Case1は他ケース同様に構造的な再定義を行えている。

**解釈上の留保**: Case1は0章の劣化（historical_episode_ids 2→0等）の影響を
Case2/3/4ほど強く受けていない（Trump/Xiはprofile_source=activeのまま維持された、
`iran_state`のような新規未知Actorの出現もない）ため、比較的クリーンな観測と言える。
したがって暫定的な結論として、**「Case1の弱さはNeutral Prompt特有だった」という
仮説を支持する材料が得られた**。ただし単一Runの比較であり、確定的な因果関係の
証明ではない。

---

## 5. Hypothesis Register: retained / rejected / unresolved

| Case | H0 | H1 | H2 | H3 | H4 | retained/rejected/unresolved |
|---|---|---|---|---|---|---|
| Case1 Trump-Xi | retained | rejected | retained | unresolved | rejected | 2 / 2 / 1 |
| Case2 Putin | retained | unresolved | retained | rejected | rejected | 2 / 2 / 1 |
| Case3 Netanyahu | retained | unresolved | rejected | unresolved | rejected | 1 / 2 / 2 |
| Case4 MBS | retained | unresolved | retained | unresolved | rejected | 2 / 1 / 2 |
| Case5 Kim | retained | unresolved | retained | rejected | rejected | 2 / 2 / 1 |

全件retainedのケースは0件（依頼書が求める「全件無批判に保持しない」を維持）。
H0が全ケースでretainedなのは、いずれも「表層的だが完全に間違ってはいない」仮説
（Case1: 制度的技術管理、Case5: 核=体制保証等）がH0として設計されており、H0自体が
必ずしも「安易な結論」を意味しないため、これ自体は問題ではない。

---

## 6. 新規Knowledge Gap（`additional_research`）

| Case | 件数(priority付き) | 総investigation_questions | knowledge_gaps参照数 |
|---|---|---|---|
| Case1 Trump-Xi | 8（高2/中1/未設定5） | 5 | 4 |
| Case2 Putin | 4（高1/中1/未設定2） | 3 | 3 |
| Case3 Netanyahu | 7（高2/未設定5） | 5 | 3 |
| Case4 MBS | 5（高1/中1/未設定3） | 3 | 2 |
| Case5 Kim | 3（高1/中1/未設定1） | 3 | 1 |

Case3/4はいずれも「MBS/Netanyahu個人に関する直接Evidenceの欠如」自体がGapの
中心（例: Case4「MBSは直近5年で対米安全保障関係と対中経済協力のどのような
具体的合意・行動を取っているか」）——これは0章のRetrieval劣化を、AI自身が
Knowledge Gapとして自己申告した形になっており、Evidence Disciplineとしては
機能している（無いものを無いと認めている）。

---

## 7. Quality Guard結果

| Case | passed | failed | initial_failed_checks |
|---|---|---|---|
| Case1 Trump-Xi | 32 | 2 | stakeholders_grounded_in_source_pack, actor_model_challenged |
| Case2 Putin | 31 | 3 | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack, actor_model_challenged |
| Case3 Netanyahu | 30 | 4 | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack, **actor_intelligence_provenance_present**, actor_model_challenged |
| Case4 MBS | 31 | 3 | fact_claims_have_source_ref, **main_scenario_has_supporting_evidence**, stakeholders_grounded_in_source_pack |
| Case5 Kim | 31 | 3 | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack, actor_model_challenged |

いずれも修復(Stage3 repair)は発火せず、initial_failed_checksとremaining_failed_checks
は完全一致（＝repair対象外の定性的Guardのみがfailしている、既存方針通り）。

`actor_intelligence_provenance_present`(Case3)と`main_scenario_has_supporting_evidence`
(Case4)は、Neutral v3では発生していなかった**新規Fail**であり、いずれも0章の
Retrieval劣化（Netanyahu/MBSでActor Intelligence・Evidenceが実質ゼロだったこと）と
直接対応する。Guardが実際にこの劣化を正しく検出できていること自体は、Quality
Guardの実効性の証拠でもある。

`confidence_adjustments`（決定的Confidence補正の発火）はCase1/2/5で発火
（`key_point_assessment`/`main_scenario`、Case5は`key_actor_profiles[0]`も対象）
——Evidence Weighting修正(v2.6)が引き続き正しく機能している。

---

## 8. Failure Matrix（Before → Neutral After → Original After）

| Failure Pattern | Before(依頼書記載の既知症状) | Neutral After(v3時点) | Original After(今回) |
|---|---|---|---|
| Actor generalization（無条件の人物解説） | あり(例: Trump=dealmaker) | 解消(5/5でpartial/deep) | **partial止まり**(5/5、deep=0件。ただしRetrieval劣化の影響大) |
| Evidence substitution（一般知識の無自覚な穴埋め） | あり | Guard検出のみ(自動補正は`evidence_class`起点のみ) | 同型。ただしCase4は`confidence=判断不能`という、埋めずに保留する挙動も新規に観測 |
| Weak analogy（弱い類推を強く使う） | 未検証 | 発生なし(Guard発火0件) | 発生なし(Case2の類推も`analogy_strength=weak`と自己申告、confidence=低で整合) |
| Control structure（actor_controlled+entity空） | 症状の再現不可(Before再実行時) | 発生なし | 発生なし(Case4は`c47c90e6-...`という生entity_idながらcontrol_type=structuralで一貫、矛盾なし) |
| Historical/current conflation | あり(Case2-5で新規Fail、v1時点) | v2.5で解消(0/5) | 発生なし(0/5) |
| Unsupported confidence（根拠なき高Confidence） | あり | v1で4/5 Guard fail、v2で0/5に | **0/5**（全ケースconfidence=低/中/判断不能に収まり、高は0件） |
| Frame-breaking weakness | 未評価(Original Beforeでは未実施) | Case5のみ弱い(1/5) | Case2/3/5が弱い(3/5)——ただし多くがRetrieval劣化由来(0章参照) |
| Media underuse | 未評価 | 未評価(Media Intelligence OFF) | 未評価(Media Intelligence OFF、Freeze対象) |
| Actor-model-challenge missing | 未評価(概念自体が本フェーズで新設) | Case2/5でFail(2/5) | **Case1/2/3/5でFail(4/5)** — Retrieval劣化でActor Intelligence自体が薄く、Challenge生成の土台データが不足したことが主因と推定 |

---

## 9. 総括

- **Evidence Discipline系（Confidence capping、Weak Analogy、Historical/Current
  分離、Control Structure正規化）は、Original Promptでも一貫して機能した。** これは
  P0修正がNeutral Promptへの過適合ではなく、Prompt文言に依存しない決定的
  post-processingとして正しく汎化していることの直接的な証拠。
- **Key Point Reasoning（候補比較→棄却理由→disconfirming_signal）の「型」自体は
  Original Promptでも崩れなかった。** 5ケース全てで複数候補比較・明示的棄却理由・
  反証条件が生成された。
- **Case1のFrame Discovery弱さは再現されなかった**——Neutral Prompt特有の副作用
  だった可能性を支持する初期証拠が得られた（4章）。
- **一方、Actor Depth／Frame Breaking／Actor-Model-Challengeの評価は、今回
  Retrievalデータが大きく痩せた影響を強く受けており、「Reasoningが後退した」と
  単純に結論づけることはできない**（0章）。Case3/Case4は、Actor Intelligence
  自体が"none_available"という、Neutral v3では一度も発生しなかった状態に陥っており、
  これを除外して評価しないと不公平な比較になる。
- 依頼書が示す「Before平均9.6/16」との直接比較について: 本報告では**Case1以外の
  Before個別スコアの一次データを保有しておらず**（`before/scorecard.md`はCase1のみ
  クリーンなBefore実行と明記、Case2-5は手順汚染により参考データ扱い）、16点Rubricの
  Before個別再計算はできなかった。厳密な数値比較よりも、4章・9章で述べた質的な
  比較（Evidence Discipline・Key Point Reasoningの型の保持）を主たる結論として報告する。

## 10. 推奨される次の診断（実施はしていない、ユーザー判断待ち）

0章の劣化の原因究明（Actor Intelligenceデータの`status`/`as_of`が実際に変わったのか、
Foundation PackのEntity解決ロジックが特定Entity名で失敗しているのか等）を、次回
着手前に優先度高く確認することを推奨する。特にCase4で`actor_ref`が名前解決されず
生UUIDのまま出力された事実は、Reasoning品質とは独立した実装上の問題である可能性が
高い。

---

**ここで一度Stopする。** 依頼書の指示通り、Media Intelligence再統合・P1修正・
追加チューニングには進まない。次の判断は、本報告（特に0章のRetrieval劣化所見）を
確認したユーザーの指示を待つ。
