# M10-B: Media Intelligence再統合 — Case2/Case3 正式評価

**新規Azure呼び出し無し**。A/Bとも既存の確定済み結果を使用する（Freeze遵守）。

- Case2 A = `docs/expert-ai-validation/original_after_v2/raw/original_after_v2_case2_putin_result.json`
- Case2 B = `docs/expert-ai-validation/m10a2_case2_media_increment/m10a2_case2_putin_variant_B_result.json`（Source Pack文書レンダリング修正後の最終版）
- Case3 A = `docs/expert-ai-validation/original_after_v2/raw/original_after_v2_case3_netanyahu_result.json`
- Case3 B = `docs/expert-ai-validation/m10a3_case3_real_evidence/m10a3_case3_netanyahu_result.json`（3 Media Profile追加後の最終版）

A = Actor + Thinking OS + Evidence Discipline。B = A + Current Evidence + Media
Intelligence。Case2とCase3は評価軸を分ける——Case2は`MEDIA_EVALUABLE_DIRECT`
（Media Intelligenceの直接Increment評価）、Case3は`CURRENT_EVIDENCE_EVALUABLE`
+`MEDIA_EVALUABLE_STRUCTURAL_ONLY`（Structural Media ContextがActor分析を
どう補助するかの評価、media_source_context=nullはFail扱いにしない）。

---

## Case2 Putin（MEDIA_EVALUABLE_DIRECT）

| 項目 | A（Media無し） | B（Current Evidence + Media有り） |
|---|---|---|
| Primary Key Point | NATOのウクライナ加盟・安全保障上の位置づけ | **ロシア軍による戦闘強度の調整と限定的停戦の運用**（変化） |
| control_type | distributed | mixed |
| key_point confidence | 低 | **中**（current_indirect_evidence） |
| H0-H4 | H0=retained/H1=rejected/H2=retained/H3=unresolved/H4=rejected | H0=retained/H1=rejected/H2=**unresolved**/H3=**rejected**（新規理由付け）/H4=rejected |
| situation_actor_assessment | 3件 | 2件 |
| media_source_context | null | **narrative_deviation+media_editorial_context（Global Times、実evidence_ids付き）** |
| main_scenario confidence | 低 | 中 |
| Quality Guard failed | 1（stakeholders_grounded_in_source_pack のみ） | 5（+fact_claims_have_source_ref/cross_intelligence_contradiction_not_silently_dropped/unsupported_actor_assumption_not_promoted/actor_model_challenged、全て非repair対象の定性チェック） |
| removed_fabricated_ids | — | 0 |
| CallBudget | 2/2 | 2/2 |

### 7項目判定

1. **Primary Key Pointが変わったか**: ✅ 「NATO加盟」（抽象的・従来的な地政学解説）
   から「戦闘強度調整と限定的停戦の運用」（具体的・現行事象に基づく）へ変化。
2. **新しいFrame/Alternative Hypothesisが出たか**: ✅ H0が「限定停戦=部隊
   ローテーション利用」という、独立系観測（Kyiv Independent/ISW/Meduza）に
   直接基づく具体的Frameへ再構成された。
3. **H0-H4のretained/rejected/unresolvedが変わったか**: ✅ H2(retained→unresolved)、
   H3(unresolved→rejected、Global Times記事を反証根拠として明示)。
4. **Actor Decision Dynamicsが深まったか**: ✅ audience_analysis（Signal vs
   Substance分析）が新規生成され、「人道的・和平志向」という対外Signalと
   「局所的・限定的」という実質Substanceの乖離を具体的に記述。
5. **Current EvidenceとHistorical Media Patternの分離が維持されたか**: ✅
   `actor_evidence_separations`が実際に発動（situation_actor_assessment[2]から
   Wagner反乱の historical episode IDを除去）。media_editorial_context内の
   Historical Media Pattern起点hypothesisもconfidence=低へ正しくcap。
6. **Confidenceが不当に上がっていないか**: ✅ 上昇はあった（低→中）が、
   `evidence_class=current_indirect_evidence`という実際のEvidence根拠を伴う
   正当な上昇であり、Historical Media Patternだけによる上昇ではない。
7. **Main Scenario/disconfirming signalが改善したか**: ✅ Main Scenarioが
   「限定停戦は部隊ローテーション・戦力再編のためのツール」という、Kyiv
   Independent/ISW/Meduza/Global Timesの4Outletを横断する具体的根拠を持つ
   説明へ改善。

### Case2固有判定: Media固有のIncrementが本当にあるか

**Pass。** H0の弱化・H1系仮説の強化は、Media Evidence（Kyiv Independent/ISW/
Meduzaの独立系観測とロシア公式発表の食い違い）を直接の根拠として行われており、
Actor Intelligence単体では生成し得ない具体的Frame再定義である。narrative_
deviationはGlobal Timesの「人道的停戦」framingを西側/ISWの「限定的・軍事的」
framingと明示的に対比し、「Who benefits ≠ intentの証明」原則を守りながら
（`alternative_explanation`を必ず併記）評価している。

**結論: Case2 = Media Increment有 / Safety Pass / 直接価値**

---

## Case3 Netanyahu（CURRENT_EVIDENCE_EVALUABLE + MEDIA_EVALUABLE_STRUCTURAL_ONLY）

| 項目 | A（Media無し、実質Current Evidence無し） | B（Current Evidence + Media Profile有り） |
|---|---|---|
| Primary Key Point | **イランのホルムズ海峡妨害能力とその行使実績**（Netanyahuから逸脱） | **ネタニヤフの政権生存とICC・国際プロセスへの防御**（Netanyahu中心を維持） |
| control_type | structural | actor_controlled |
| key_point confidence | 中 | 低 |
| H0-H4 | H0=retained/H1=unresolved/H2=retained/H3=rejected/H4=unresolved | H0=retained/H1=unresolved/H2=unresolved/H3=rejected/H4=rejected |
| media_source_context | null | **null（Profile追加後も不発、ただしDirect文書0件のため正常）** |
| main_scenario confidence | 低 | 低 |
| Quality Guard failed | 1（stakeholders_grounded_in_source_pack のみ） | 1（同上のみ） |
| removed_fabricated_ids | — | 0 |
| CallBudget | 2/2 | 2/2 |

### 7項目判定

1. **Primary Key Pointが変わったか**: ✅（最重要の変化）依頼された問い
   （Netanyahuの政策）から丸ごと逸脱していた状態（A）から、Netanyahu自身の
   政権維持・法的リスク回避という**依頼された問いの中心を保った**状態（B）へ
   改善。
2. **新しいFrame/Alternative Hypothesisが出たか**: ✅ candidate_key_pointsに
   「ホルムズ海峡の封鎖・管理枠組みをめぐる米・イラン・オマーン・中国の力学」
   「IRGCによるイラン国家運営の実質的掌握」が新規候補として出現（Gulf
   International Forum/TIME記事由来）——ただし両方とも明確な理由でPrimaryから
   除外され、Netanyahu中心のFrameを乗っ取らなかった。
3. **H0-H4のretained/rejected/unresolvedが変わったか**: ✅ H2(retained→
   unresolved)、H4(unresolved→rejected)。
4. **Actor Decision Dynamicsが深まったか**: ✅ situation_actor_assessmentの
   1項目「対イラン・ホルムズ危機への姿勢」が実document_id（d163ef87=CNBC記事）
   を引用し、Netanyahuの安全保障環境として構造的に統合された。
5. **Current EvidenceとHistorical Media Patternの分離が維持されたか**: ✅
   `confidence_adjustments`が4フィールドで発動（key_point/situation_actor_
   assessment[0]/main_scenario/key_actor_profiles[0]）、`removed_fabricated_
   ids=0`。
6. **Confidenceが不当に上がっていないか**: ✅ むしろ低下（中→低）——Netanyahu
   自身についての直接的Current Evidenceは無く、Actor Intelligence（Historical
   Actor Pattern）に依拠していることを正しく反映。
7. **Main Scenario/disconfirming signalが改善したか**: ✅ Main Scenarioが
   Hormuz/IRGC情勢をNetanyahuの安全保障ナラティブ正当化の文脈として明示的に
   組み込む形へ改善（「ホルムズ海峡をめぐる米・イラン戦争とIRGCの構造的台頭に
   より...ネタニヤフはガザ戦争継続と対イラン強硬姿勢の正当化に利用しつつ」）。

### Case3固有判定: Structural Evidenceを過大解釈せず、Decision Contextとして
適切に使えているか

**Pass。** 9文書中Direct=0/Structural=7/Peripheral=2（`docs/expert-ai-
validation/case3_document_relevance_classification.md`）という構成の中、
Bはこれらを一度も「Netanyahu自身についての一次証拠」として誤用せず、
一貫して`primary_binding_constraint`（最重要制約1つに絞る設計）や
`issue_component_description`として構造的制約に位置づけた。Media Profile
（Al Jazeera/SCMP/Gulf International Forum）が存在するにもかかわらず
media_source_contextが生成されなかった点も、Direct文書が0件だったことと
整合しており、「Profileがあるから無理に生成する」という誤昇格は発生していない。

**結論: Case3 = Media Increment限定的（Structural Contextとしては有効）/
Safety Pass / 構造制約価値**

---

## 総括表

| Case | Evaluability | Media Increment | Safety | 結論 |
|---|---|---|---|---|
| Case2 | Direct | 有 | Pass | 直接価値（Frame再定義・Hypothesis Competitionへの因果的寄与を確認） |
| Case3 | Structural Only | 有（限定的、構造統合としては有効） | Pass | 構造制約価値（Netanyahu中心を維持しつつ環境要因を正しく従属統合、誤昇格なし） |

## M10-B Stop Gate判定

| 条件 | 判定 |
|---|---|
| Case2でMedia固有Incrementあり | ✅ |
| Case3でStructural Contextの誤昇格なし | ✅ |
| Historical Media Patternだけでconfidence上昇 = 0 | ✅（両ケースとも決定的補正が確認・発動） |
| Current/Historical混同 = 0 | ✅（`removed_fabricated_ids=0`両ケース、`actor_evidence_separations`が実際に発動） |
| Actor分析をMediaが不必要に押し出さない | ✅（Case3でNetanyahu中心維持、Case2でもPutin中心の分析が維持されている） |
| CallBudget ≤3 | ✅（両ケースとも2/2） |
| Regression維持 | ✅（Expert AIスコープ197件、本セッション中の全コード変更後も継続してPass確認済み） |

**M10-B Stop Gate 全項目通過。**

## 結論

Media Intelligence再統合の設計検証は、Case2（直接価値）・Case3（構造制約価値）
という異なる2種類の成功パターンで裏付けられた。Media Activationは「同一
Situation/Decision Point + 意味のある情報差」という境界で正しく機能し
（Media Activation Test Condition B含む一連の検証で実証済み）、Evidence
Disciplineの分離機構（Historical/Current分離、Confidence Capping、Fabrication
Scrub）は、Media Intelligenceが実際に価値を生むリッチな条件下でも一貫して
機能し続けることを確認した。

次段階（江口さんへのExpert Review、Session3）へ進む技術的な根拠は揃った。
Case1/4/5の`MEDIA_NOT_EVALUABLE_DATA_GAP`は、今回のスコープでは埋めず、
将来のMedia coverage backlogとして残す。
