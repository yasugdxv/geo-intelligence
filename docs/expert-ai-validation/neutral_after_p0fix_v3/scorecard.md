# Neutral Prompt Validation v3 — Main Scenario Evidence Weighting Fix 確認

ユーザー指定の4項目に絞った最終確認（フルスコアリングは行わない）。

## 実行条件
Frozenルール（3点）を遵守: コード無変更のまま5ケース完了、実行中Fingerprint一致
確認済み（`geo_expert_schema.py`=Thinking OS本体を含め、v2完了時点から不変）。
全ケース`calls=2`。

---

## 1. Main Scenario evidence discipline = 5/5 pass

| Case | main_scenario.confidence | evidence_class | ID-based backstop | 判定 |
|---|---|---|---|---|
| Case1 Trump-Xi | 低 | model_general_knowledge | 該当UUID無し（自由記述のみ） | ○ |
| Case2 Putin | 低 | structural_reference（弱クラスでない） | **only_derived=true→強制cap発動** | ○ |
| Case3 Netanyahu | 低 | historical_actor_pattern | only_derived=true | ○ |
| Case4 MBS | 低 | historical_actor_pattern | only_derived=true | ○ |
| Case5 Kim | 低 | model_general_knowledge | only_derived=true | ○ |

**5/5 pass。** Case2が特に重要な実証: 自己申告`evidence_class`が弱クラス（`historical_
actor_pattern`等）に該当しない`structural_reference`だったにもかかわらず、
`supporting_evidence`内のUUID（`d0ef206d-...`＝ワグネル反乱Episode）が
`derived_intelligence_ids`にしか一致しないため、ID起点の決定的backstopが正しく
発動しconfidenceを低へ強制した。これはevidence_class自己申告の取りこぼしを防ぐ
二重防御が実データで機能した証拠。全ケースで`main_scenario.description`は保持
（削除なし）。

## 2. 既存3 Guard = 0 fail維持

`unsupported_actor_assumption_not_promoted`・`evidence_attribution_not_conflated`・
`actor_pattern_applicability_present`は5ケースとも`initial_failed_checks`に
一切出現せず。**0/5維持を確認。**

## 3. Independent Frame Discovery = 現状水準維持

| Case | v2 | v3 | Primary Key Point(v3) |
|---|---|---|---|
| Case1 | 1 | 1 | 「中国の軍事・監視用途AIへの先端半導体アクセスをどこまで制度的に制限するか」 |
| Case2 | 2 | 2 | 「NATOのウクライナ加盟（または準ずる安全保障コミットメント）の有無」 |
| Case3 | 2 | 2 | 「Netanyahu政権の国内政治生存が対Iran・Gaza政策の主な蝶番」 |
| Case4 | 2 | 2 | 「MBSの王位継承と国内権力基盤の安定化が最上位目的」 |
| Case5 | 1 | 1 | 「北朝鮮の核・ミサイル戦力をめぐる抑止・交渉構造」 |

**v2と完全に同一水準（1,2,2,2,1）を維持。** 悪化なし。Hypothesis Registerも
全ケースでH0-H4の5件を維持、全件retainedのケースなし。

## 4. CallBudget = 2/2維持

全5ケース`calls=2`。CallBudget<=3を維持。

---

## 副次的観察（ブロッカーではない、記録のみ）

`actor_model_challenged`がCase1/2/5で発生（v2はCase2/5の2件、v3はCase1/2/5の3件と
やや増加）。ユーザー指示により今回はP1として保留、対応しない。

---

## 結論

ユーザー指定4項目、**全て達成**。Evidence WeightingのP0系（Key Point層・Actor層・
Main Scenario層）は一貫して機能しており、Frame Discoveryも損なわれていない。

依頼書の通り、ここで一度停止する。Original 5-case Afterへの移行は、
ユーザーの明示的な判断を待つ。
