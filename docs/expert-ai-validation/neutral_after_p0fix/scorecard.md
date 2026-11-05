# Neutral Prompt Validation — After P0-1/P0-2/P1 Evidence Discipline Fix

比較対象:
- **Before（今回比較の基準）**: `docs/expert-ai-validation/neutral/`（3点修正前、14.2/16平均、
  Independent Frame Discovery 2,2,2,2,1）
- **After（今回）**: 本ドキュメント（P0-1 General Knowledge Confidence Cap、
  P0-2 Historical/Current Evidence分離、P1 Actor Pattern Applicability必須化の
  3点修正後）

**重要な前提**: 今回はThinking OS（`geo_expert_schema.py`のMODE_GUIDANCE等、Prompt
文面）には一切触れていない。実行後のFingerprint確認で`geo_expert_schema.py`は
Before/After修正前後で完全に同一（`358640565bdd689c`）であることを確認済み。
変更したのは`geo_expert_quality_guard.py`（決定的Corrector追加）・
`geo_expert_engine.py`（呼び出し配線）・`schemas/geo_expert_ai_response.schema.json`
（`evidence_class`/`historical_supporting_evidence_ids`/`confidence_adjustment_reason`
フィールド追加）のみ。ただしSchemaへ追加したフィールドのdescription文言はStructured
Output契約の一部としてLLMへ渡されるため、純粋な「後処理のみ」の変化ではなく、
Evidence分類をLLM自身にも意識させる効果を併せ持つ——これも依頼の「Evidence
Disciplineの強化」の範囲内であり、Thinking OS（Reasoning手順の指示文）とは別物。

**単一Run比較の限界**: 各Caseとも1回のみの実行（temperature=0.2、非ゼロ）であり、
Before/After間の差分にはCode変更の効果とモデル出力自体の揺らぎの両方が混在し得る。
以下の分析はこの限界を踏まえた上での所見である。

---

## 1. Neutral 5-case After score（16点Rubric）

| 項目 | Case1 Trump-Xi | Case2 Putin | Case3 Netanyahu | Case4 MBS | Case5 Kim |
|---|---|---|---|---|---|
| Key Point | 1 | 1 | 2 | 1 | 1 |
| Key Actor | 2 | 2 | 2 | 2 | 2 |
| Actor Depth | 1 | 1 | 1 | 1 | 1 |
| Surface Escape | 1 | 1 | 2 | 1 | 1 |
| Hypothesis Breadth | 2 | 2 | 2 | 2 | 2 |
| Evidence Testing | 2 | 1 | 2 | 1 | 2 |
| Self-Challenge | 2 | 2 | 2 | 2 | 2 |
| Scenario | 2 | 2 | 2 | 2 | 2 |
| **合計 /16** | **13** | **12** | **15** | **12** | **13** |

平均 13.0/16（Before 14.2/16から低下）。**これは想定内であり、ユーザーの指示通り
「点数上昇そのものは今回のゴールではない」**。Key Point/Surface Escapeが複数Caseで
1点に下がったのは、後述の通りPrimary Key Pointの実際の内容がBeforeより保守的・
構造的な方向へシフトしたこと（Independent Frame Discovery低下と直結）が主因であり、
Evidence Testingが上昇したCase（1,3,5）はEvidence分離・Confidence Capが正しく
機能した結果。両方向の変化が混在しており、これは「Evidence Disciplineだけを
締めた結果、一部で深読みへのブレーキが利きすぎた可能性がある」ことを示す重要な所見
として次項以降で詳述する。

---

## 2. Independent Frame Discovery Before/After

| Case | Before | After | 所見 |
|---|---|---|---|
| Case1 Trump-Xi | 2 | **1** | Before「半導体・AIは軍事・安全保障上の中核技術」という構造的必然性の指摘から、After「米国の対中先端半導体・AI技術輸出管理制度の運用方針」という、より制度運用寄り・穏当な蝶番へ後退。禁止された二項対立（恒久封じ込め vs 交渉カード）は避けているが、Beforeほど非自明な洞察ではない。 |
| Case2 Putin | 2 | **1** | Before「Putin政権の存続とエリート統制」（ワグネル反乱根拠）から、After「NATO東方拡大への脅威認識と抑止」という、より通説的な安全保障説明へシフト。ただし特筆すべき点として、Afterの`H3`（actor_model_breaking）に「戦争が国内権力維持・対内プロパガンダのツールに変質している」というBeforeの核心的洞察と同種の仮説が実際に検討されており、**Evidenceが不十分なため`rejected`とされた**——これは深読み自体が消えたのではなく、Evidence Disciplineの強化によって「弱い根拠のまま主結論に昇格させない」よう適切に抑制された可能性がある。 |
| Case3 Netanyahu | 2 | **2**（維持） | Before/After共に、禁止された二項対立（安全保障 vs 個人政治）のうちH0（安全保障説明）を明示的にrejectし、H1（政権延命・説明責任回避）をretain。同一のFrame-breakが再現された。 |
| Case4 MBS | 2 | **1** | Before「MBSの王位継承・権力集中」というActor中心の frame-breakから、After「米中対立の構造とエネルギー市場におけるサウジの位置」という構造的説明へシフト。AfterのH0（surface、retained）は「自律性とレバレッジを高める多角化戦略」であり、禁止された二項対立（China shift vs strategic autonomy）の一方（strategic autonomy）に内容が近づいている。 |
| Case5 Kim | 1 | **1**（維持） | ユーザーの事前期待通り、無理に2へ引き上げようとする挙動はなく、Before同様「核・ミサイル=体制存続の保証」という定型的読み筋を維持。後述の通りConfidenceは中→低へ正しく低下しており、これは狙い通りの改善（後述5番参照）。 |

**Acceptance Criteria照合**: Independent Frame Discovery>=1は5/5維持（基準:4/5）。
=2は1/5（Case3のみ）——**Before時点の4/5から大きく低下し、基準(3/5)を下回った**。
これは正直に報告すべき退行であり、次項「3点修正が及ぼした副作用の仮説」で扱う。

### 3点修正が及ぼした副作用の仮説（正直な考察）

Case1/2/4で共通するパターンは、「Beforeで採用されていたActor中心・非自明なPrimary
Key Point」が、Afterでは「より構造的・穏当・時に禁止された二項対立に近い」Primaryへ
後退したことである。仮説としては:
1. Schemaへ`evidence_class`/`historical_supporting_evidence_ids`を追加したことで、
   LLMがKey Point選定時点から「Historical/Actor Intelligence由来の主張は
   評価を割り引く」ことを強く意識するようになり、Actor Intelligence Pack（Historical
   Episode中心）に依拠したActor中心の大胆なKey Point選定を、より無難な構造説明へ
   自己検閲的に後退させた可能性がある。
2. 単一Run比較のため、単純なモデル出力の揺らぎである可能性も排除できない。

**この副作用が実際にCode変更由来かを切り分けるには、同一条件での複数回Run比較
（例えば各Case3回ずつ）が次のステップとして有効**——ただし今回はユーザー指示通り
Original Prompt Afterへ進む前にここで一度停止して報告する。

---

## 3. Actor Generalization Before/After

| Case | Before | After | 所見 |
|---|---|---|---|
| Case1 | partial | **deep（Guard準拠、一部Fallback）** | Trump/Xiとも`pattern_applicability_assessment`が3件生成（各1件は実質的評価=medium、残り2件はP1 Fallbackによるuncertain）。Guard上はdeepの体裁を満たすが、**深さの半分以上はP1決定的補完によるもの**であり、LLM自身が全Patternを評価しきったわけではない。 |
| Case2 | deep | **deep（維持、ただし実質評価が1→1件に減少）** | Beforeは2 Pattern両方に実質評価（medium, medium）があったが、Afterは1件のみ実質評価（medium）+2件がFallback（uncertain）。Guard上は変わらずdeepだが、実質的な条件付き評価の密度はやや低下。 |
| Case3 | deep | **deep（維持、同上のFallback混在）** | 同様のパターン。 |
| Case4 | deep | **deep（維持、実質評価2件+Fallback2件）** | 4 Case中最も実質評価が多く残っている（medium, low）。 |
| Case5 | partial | **deep（Guard準拠、実質評価は1件のみ）** | BeforeはPattern参照ありなのに評価0件（Guard Fail）だったが、AfterはFallbackにより3件（medium 1件+uncertain 2件）。Case1と同型の改善。 |

**Acceptance Criteria照合（Actor Depth維持）**: Guard準拠の意味では5/5が"deep"扱いに
改善したが、**これは主にP1の決定的Fallbackの効果であり、LLM自身が生成した実質的な
条件付き評価の「量」はCase2で1件減少、他Caseは概ね同水準**。「Confidence Capによって
分析自体が浅くなっていないか」というユーザーの懸念に対する答え: **Actor Assessment
自体の文章は浅くなっていない（`persistent_preference`/`relevant_decision_patterns`は
全Caseで維持）が、Pattern Applicability評価の生成密度はLLM側でやや控えめになった
傾向がある**。

---

## 4. 3 Guard failures Before/After

| Guard | Before(fail数/5) | After(fail数/5) | 目標 | 判定 |
|---|---|---|---|---|
| `unsupported_actor_assumption_not_promoted` | 4/5（Case2,3,4,5） | **3/5（Case2,3,4）** | 0/5 | 未達（改善はしたが目標未到達） |
| `evidence_attribution_not_conflated` | 4/5（Case2,3,4,5） | **2/5（Case2,4）** | 0/5 | 未達（大幅改善したが目標未到達） |
| `actor_pattern_applicability_present` | 2/5（Case1,5） | **0/5** | 5/5 pass | **達成** |

### なぜ`unsupported_actor_assumption_not_promoted`が3/5で残るのか（根本原因を特定済み）

`cap_confidence_by_general_knowledge_flag`は「`evidence_ids`が空か」だけを見て
Current Evidenceの有無を判定していた。しかしCase2(Putin)/Case3(Netanyahu)/
Case4(MBS)では、Actor Profileの`evidence_ids`に**Historical Episode ID
（Actor Intelligence由来のDerived Intelligence、例: Case2の`d0ef206d-...`は
`actor_intelligence_provenance.historical_episode_ids`にも同一IDが登録されている）
が非空のまま残っている**——つまり「Current Evidenceがある」とCorrectorが誤認し、
Confidence Capを見送ってしまった。**P0-2の"Historical Evidenceの決定的分離"を
`key_point_assessment`にしか適用しておらず、`key_actor_profiles[].evidence_ids`
自体には適用していなかったことが根本原因**。

### なぜ`evidence_attribution_not_conflated`が2/5で残るのか（同根の原因）

同じ理由で、`separate_historical_evidence_in_key_point`は`key_point_assessment`
のみを対象にしており、`situation_actor_assessment[].evidence_ids`は対象外だった。
Case2/Case4では、`situation_actor_assessment[]`のいずれかの項目が、
`key_point_assessment`側では正しく分離されたのと同一のHistorical Episode ID
（例: Case2の`d0ef206d-...`、Case4の`4e4c23bd-...`）を`evidence_ids`として
引用しており（`evidence_class`もLLM自身が誤って`current_indirect_evidence`と
自己申告）、Guardが検出した。

**両者とも根本原因は同一**: Historical/Current Evidence分離をKey Point
Assessmentだけでなく`key_actor_profiles[].evidence_ids`と
`situation_actor_assessment[].evidence_ids`にも拡張する必要がある。これは
次の修正候補として明確（ユーザーの判断待ち、今回は実施しない）。

---

## 5. Confidence changes（正しい低下の確認、ユーザー最重視項目）

| Case | 対象 | Before | After | 評価 |
|---|---|---|---|---|
| Case1 | key_point_assessment | 中 | **低** | Evidence構成がHistorical中心と判明したための正しい低下 |
| Case1 | main_scenario | 低 | 中 | 上昇（Current Evidence 2件は変わらず、Historical1件が明示分離されたことでむしろ整理された） |
| Case1 | actor(trump/xi) | 低/低 | 低/低 | 変化なし（Beforeから既にgeneral_knowledge_used+evidence無しで低、Guard対象外） |
| Case2 | key_point_assessment | 中 | **低** | Historical Evidence分離＋Current Evidence構成の見直しにより低下 |
| Case2 | main_scenario | 中 | 中 | 変化なし |
| Case2 | actor(putin) | 中 | 中（未低下） | **上記4番の根本原因により本来低下すべきところ据え置き（残存Gap）** |
| Case3 | key_point_assessment | 中 | **低** | 正しい低下 |
| Case3 | main_scenario | 中 | 中 | 変化なし |
| Case3 | actor(netanyahu) | 中 | 中（未低下） | 同上、残存Gap |
| Case4 | key_point_assessment | 中 | **低** | 正しい低下 |
| Case4 | main_scenario | 中 | 中 | 変化なし |
| Case4 | actor(MBS) | 中 | 中（未低下） | 同上、残存Gap |
| Case5 | key_point_assessment | 中 | **低** | 正しい低下 |
| Case5 | main_scenario | 中 | 中 | 変化なし |
| Case5 | actor(kim) | 中 | **低** | **正しく低下（このCaseのevidence_idsはBefore/After共にHistorical IDのみだったが、Afterでは当該IDがそもそもevidence_idsに引用されずCorrectorの条件を満たしたため正しく機能）** |

**総括**: `key_point_assessment.confidence`は**5/5全ケースで中→低へ正しく低下**——
これはP0-2のKey Point Evidence分離＋Confidence Capが実データで完全に機能している
明確な証拠。一方Actor個別の`confidence`は5件中2件（Case1のtrump/xi=元々低、
Case5=正しく低下）のみが期待通りで、Case2/3/4の3件は上記4番の根本原因により
据え置きのまま——**Key Point層は成功、Actor Profile層は部分的に未達**という
層ごとの非対称な結果である。

---

## 6. Current vs Historical evidence separation（各ケース一覧）

`historical_supporting_evidence_ids`（新設）による決定的分離が実際に機能したかを
Key Point Assessment / Main Scenario単位で集計（Actor個別のevidence_idsは上記4/5番
参照、こちらは分離未実装のため対象外）。

| Case | 対象 | Before Current | Before Historical(推定) | After Current | After Historical |
|---|---|---|---|---|---|
| Case1 | Key Point | 0 | 0 | 1 | **1（分離済み）** |
| Case1 | Main Scenario | 2 | 0 | 2 | 1 |
| Case2 | Key Point | 0 | 1 | 2 | **1（分離済み）** |
| Case2 | Main Scenario | 4 | 1 | 4 | 1 |
| Case3 | Key Point | 0 | 1 | 0 | **1（分離済み）** |
| Case3 | Main Scenario | 1 | 1 | 2 | 1 |
| Case4 | Key Point | 0 | 2 | 0 | **1（分離済み）** |
| Case4 | Main Scenario | 0 | 2 | 1 | 1 |
| Case5 | Key Point | 0 | 1 | 4 | **0（Historical混入なし）** |
| Case5 | Main Scenario | 0 | 2 | 4 | 0 |

「Before Historical(推定)」は、Afterで確立した`derived_intelligence_ids`
（Actor Intelligence Packの`historical_episode_ids`/`pattern_ids`/
`exception_episode_ids`の合算）の集合を使い、Before側の`supporting_evidence_ids`/
`supporting_evidence`のうち同集合に含まれるものを事後的に数え直した値（Before実行
当時はこの分離機構自体が存在しなかったため、生データを同じ基準で再集計）。

**Key Pointについては全5ケースでHistorical Evidenceが`historical_supporting_
evidence_ids`へ正しく決定的分離されていることを確認**（Case5は元々混入していた
Historical IDがそもそも今回のsupporting_evidence_idsに現れず、混入なし)。

---

## 7. General Knowledge使用箇所

全7 Actor Profile中、After実行でも**7/7が`general_knowledge_used=true`**
（Before同様、変化なし——General Knowledge使用自体を禁止する設計ではないため
想定通り）。うちConfidence内訳:

- **低（Guard対象外）**: Case1 Trump/Xi（evidence_ids空、Cap前から低）、
  Case5 Kim（evidence_ids空、Cap機能により低へ）
- **中のまま（残存Gap）**: Case2 Putin、Case3 Netanyahu、Case4 MBS
  （いずれも`evidence_ids`にHistorical Episode IDのみが入っており、
  上記4番の根本原因によりCap未発動）

---

## 8. Pattern applicability結果

全5ケース・全7 Actor Profileで`pattern_applicability_assessment`が非空
（`actor_pattern_applicability_present`が5/5 pass、目標達成）。ただし上記3番の
通り、生成された評価の一部（Case1で2/3件、Case2で2/3件、Case3で2/3件、Case4で
2/4件、Case5で2/3件）はP1 Fallback（`applicability=uncertain`、決定的補完）で
あり、LLM自身による実質評価ではない。Fallbackが発生する理由は、
`relevant_decision_patterns[]`（文字列リスト）と`pattern_applicability_assessment[]
.pattern_summary`の完全一致判定のため、**LLMが別の言い回しでPatternを評価した
場合も不一致とみなされFallbackが余分に追加される可能性がある**（軽微な実装上の
注記、今回のデータでは実害はないが、次回改修候補として記録）。

---

## 9. Knowledge Gap / Unknownの増減

| Case | identified_knowledge_gaps件数 Before→After | 所見 |
|---|---|---|
| Case1 | 3→3 | 変化なし |
| Case2 | 3→3 | 変化なし |
| Case3 | 3→**4** | 増加 |
| Case4 | 3→3 | 変化なし |
| Case5 | 3→**4** | 増加 |

2/5ケースで増加、他は横ばい。減少したケースは0件——「Evidence不足を素通りせず
知識ギャップとして記録する」方向性は後退していない。加えて、Case3の
`unknown_or_uncertain`文言には「一般知識で補うことは避けたため、詳細な評価は
行っていない」という自己申告が新たに現れており（Before同箇所にはこの明示的な
自己申告はなかった）、Evidence Discipline強化の質的な効果として記録に値する。

---

## 10. Call count / tokens

| Case | Before tokens | After tokens | 差分 |
|---|---|---|---|
| Case1 | 94,492 | 98,222 | +3,730 |
| Case2 | 93,791 | 95,537 | +1,746 |
| Case3 | 97,797 | 102,082 | +4,285 |
| Case4 | 96,750 | 96,094 | -656 |
| Case5 | 94,815 | 99,361 | +4,546 |
| **合計** | 477,645 | 491,296 | **+13,651（+2.9%）** |

全ケースとも`calls=2`を維持（CallBudget<=3、達成）。Token増加は主にSchema拡張
（新規description文言がStructured Output契約に含まれるため）と、Evidence分離
関連の追加的な自己申告記述によるもの。

---

## 11. Regression

3点修正実装完了時点で**179/179 pass**（旧170＋新規9）。本Neutral再実行の前後で
Fingerprintを再計算し、実行中にコードが一切変更されていないことを確認済み
（`geo_expert_engine.py: 4ea394e67c93a1c5`、`geo_expert_quality_guard.py:
9040fcb0d9c71b87`、`schemas/geo_expert_ai_response.schema.json: ae68d50f371dbb7d`、
`geo_expert_schema.py: 358640565bdd689c`——Before/After実行前後で完全一致）。

---

## 12. 新たに発生したFailure

| Case | 新規Failure | 判定 |
|---|---|---|
| Case2 Putin | `fact_claims_have_source_ref` | Beforeでは発生していなかった。P0-1/P0-2/P1のいずれとも無関係な既存(M8世代)Guardであり、単一Run比較におけるモデル出力の揺らぎ（未出典のFact Claimが今回のみ発生）の可能性が高い。原因調査は今回のスコープ外。 |
| その他4ケース | なし | Case1/3/4/5では新規Failureは発生せず、Case1/3/5は複数Guardが解消（改善のみ）、Case4は変化なし。 |

---

## Acceptance Criteria 総合判定（今回の7項目、ユーザー基準）

| 項目 | 基準 | 結果 |
|---|---|---|
| `unsupported_actor_assumption_not_promoted` | 0/5 | ✗ 3/5（原因特定済み、4番参照） |
| `evidence_attribution_not_conflated` | 0/5 | ✗ 2/5（原因特定済み、4番参照） |
| `actor_pattern_applicability_present` | 5/5 pass | ○ 達成 |
| Independent Frame Discovery >=1（5/5）、4/5維持 | 5/5, 4/5 | ○ >=1は5/5維持／✗ =2は1/5のみ（4/5→1/5、大きく低下） |
| Actor Depth維持 | 3件deep維持 | △ Guard準拠では5/5がdeep化したが、実質評価密度はCase2でやや低下（3番参照） |
| 正しいConfidence低下 | Key Point層で発生することを確認 | ○ 5/5全ケースでkey_point_assessment.confidenceが中→低へ正しく低下 |
| CallBudget<=3維持 | 全ケースcalls<=3 | ○ 達成（全ケースcalls=2） |

**総合所見**: Key Point層のEvidence Discipline（P0-2）は非常にクリーンに機能した
（5/5でConfidence適正化、Historical/Current分離も5/5成功）。Actor Pattern
Applicability必須化（P1）はGuard基準で完全達成（5/5 pass）。一方、Actor Profile層
のGeneral Knowledge Confidence Cap（P0-1）とEvidence分離は、`key_actor_profiles[]
.evidence_ids`/`situation_actor_assessment[].evidence_ids`まで対象を拡張して
いなかったため、3/5・2/5で目標未達——**根本原因は特定済みで、対応は技術的に
明確**（Key Pointで実装済みの分離ロジックをActor/Situation層へも適用する）。
加えて、Independent Frame Discoveryが4/5→1/5へ大きく低下したことは、点数上昇を
求めないという今回の方針に照らしても軽視すべきではなく、次回の修正判断材料として
正直に記録する（単一Run比較のため確定的な因果ではないが、注視すべきシグナル）。

依頼書の指示通り、ここで一度停止する。Original Prompt After実行・Media
Intelligence改修・Actor Intelligenceデータ追加・Prompt再調整は、ユーザーの
明示的な指示があるまで着手しない。
