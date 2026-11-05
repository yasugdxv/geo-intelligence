# Original 5-case (Non-Neutral) After v2 — 正式Scorecard

**STATUS: `VALID`**。`docs/expert-ai-validation/actor-retrieval-audit/report.md`で
特定したRunner設定ミス（`actor_profile_retrieval_status`が既定`"available"`のまま
だった件）を修正し、Neutral v3と同一の検証用Retrieval条件
（`actor_profile_lookup_statuses=("active","candidate")`、
`actor_profile_retrieval_status=None`）に固定した上で再実行した。**本番コードは
無変更**（Fingerprint一致確認済み、Freeze対象9ファイルすべてv3完了時点から不変）。

実行前にMBS/Netanyahu/Putinの3ActorについてRetrieval Snapshotを取得し、
`profile_found`/`precedent_count`が監査で復元した期待値と完全一致することを
確認してからAzure呼び出しへ進んだ（`run_config.json`/
`retrieval_snapshot_precheck.json`参照）。実行中、コード・DB・Prompt・Actor
Intelligenceへの変更は一切行っていない。

**実行日**: 2026-09-01。初回Original After（`docs/expert-ai-validation/
original_after/`、Retrieval設定ミスによりInvalid）とは別条件のため、番号を
分けて記録する。

---

## 1. 固定した実行条件

| 項目 | 値 |
|---|---|
| actor_profile_lookup_statuses | ("active", "candidate") |
| actor_profile_retrieval_status | None（本番既定"available"を明示的に解除、検証用のみ） |
| Feature Flag: Actor Intelligence | ON |
| Feature Flag: Media Intelligence | OFF |
| Model/Deployment | Global-AzureOpenAI-DEV-gpt-5.1 |
| mode / profile | case_analysis / deep |
| as_of_date（全5ケース共通） | 2026-08-31（`geo_cases.as_of_date`固定値、実行日に非依存） |
| Prompt/Schema revision | schemas/geo_expert_ai_response.schema.json fingerprint=77a892d2bd1f777f |
| Actor Intelligence revision | geo_actor_intelligence_service.py fingerprint=a4728e75b6b9c3ab（Neutral v1から不変） |
| Media Intelligence revision | geo_media_intelligence_service.py fingerprint=aa61f22f460b32a5（Neutral v1から不変） |

## 2. Retrieval Snapshot事前確認（Azure呼び出し前）

| Actor | profile_found | precedent_count | 判定 |
|---|---|---|---|
| MBS | True | 2 | OK（期待値と一致） |
| Netanyahu | True | 2 | OK |
| Putin | True | 2 | OK |

## 3. Token / Call数

| Case | calls | prompt | completion | total | wall clock |
|---|---|---|---|---|---|
| Case1 Trump-Xi | 2 | 82,542 | 11,964 | 94,506 | 823.3s |
| Case2 Putin | 2 | 82,003 | 14,272 | 96,275 | 294.3s |
| Case3 Netanyahu | 2 | 84,825 | 12,416 | 97,241 | 270.5s |
| Case4 MBS | 2 | 81,500 | 10,267 | 91,767 | 184.8s |
| Case5 Kim | 2 | 81,755 | 12,682 | 94,437 | 217.2s |
| **合計** | 10 | 412,625 | 61,601 | 474,226 | 1790.1s |

CallBudget<=3を全ケース遵守（全ケースcalls=2、修復発火なし）。

---

## 4. 16点Rubric（Neutral scorecardと同一基準、8項目×0/1/2）

項目: Key Point / Key Actor / Actor Depth / Surface Escape / Hypothesis Breadth /
Evidence Testing / Self-Challenge / Scenario

| 項目 | Case1 Trump-Xi | Case2 Putin | Case3 Netanyahu | Case4 MBS | Case5 Kim |
|---|---|---|---|---|---|
| Key Point | 2 | 2 | 1 | 2 | 2 |
| Key Actor | 2 | 2 | 2 | 2 | 2 |
| Actor Depth | 1 | 2 | 2 | 2 | 2 |
| Surface Escape | 2 | 1 | 1 | 2 | 1 |
| Hypothesis Breadth | 2 | 2 | 2 | 2 | 2 |
| Evidence Testing | 2 | 2 | 2 | 1 | 2 |
| Self-Challenge | 2 | 2 | 2 | 2 | 2 |
| Scenario | 2 | 2 | 2 | 2 | 2 |
| **合計 /16** | **15** | **15** | **14** | **15** | **15** |
| **平均** | | | **14.8/16** | | |

**判定根拠の要旨**:
- **Actor Depth**がCase1のみ1点: `actor_model_challenge`が唯一Case1（Trump/Xi）だけ
  生成されず（Quality Guard `actor_model_challenged` fail）。他4ケースは
  `pattern_applicability_assessment`＋`actor_model_challenge`（inconclusive、具体的
  条件付き）が揃い、Netanyahu/MBSは命名Pattern2件も伴う"deep"水準に到達。
- **Surface Escape**がCase2/3/5で1点: Case2はNATO拡大阻止という「規制的だが
  従来的な」フレームに着地（政権生存フレームへは踏み込まず）。Case3はNetanyahu
  個人の問いからホルムズ海峡という構造要因へ主題ごと移動（Frame-breakingという
  よりTopic Driftに近い）。Case5はH0（核・ミサイル=体制保証）がそのままPrimary
  として残り、Neutral・初回Original After（v1）と3回連続で同一の弱さを再現。
  Case1/4は質問文の暗黙のフレーム（Actor中心の問い方）から離れ、制度・構造要因へ
  明確に再定義できている。
- **Evidence Testing**がCase4のみ1点: `fact_claims_have_source_ref`が唯一Case4で
  Fail（Source PackにMBS関連の直接Fact言及が少ないため）。他4ケースはこのチェックを
  含め`stakeholders_grounded_in_source_pack`以外は全てPass——v1（全ケース1点、
  Guard未達が常態化）から明確に改善した。

---

## 5. Before(依頼書 平均9.6/16) / Neutral v3(平均14.2/16) / Original After v2(平均14.8/16)

| | Original Before（依頼書記載） | Neutral After v3 | **Original After v2（本報告、正式）** |
|---|---|---|---|
| 平均スコア | **9.6/16** | 14.2/16 | **14.8/16** |
| Case別内訳 | 個別データ未保有（Case1のみ定性的Beforeあり、後述） | 14,15,15,15,12 | 15,15,14,15,15 |

**結論**: Depth & Grounding Upgradeの効果は、Neutral Prompt（質問側からAlternativeを
一切示唆しない中立的な問い方）だけでなく、**依頼書オリジナルのAlternativeを暗示する
問い方でも同水準（むしろ平均ではわずかに上回る）で再現された**。これは「Neutral
Promptに最適化しただけ」という懸念（ロードマップ④の問い）に対する明確な否定的
回答であり、P0修正・Reasoning強化が特定のPrompt文言に過学習していないことの
実証である。

**留保**: Original Beforeの個別ケーススコアの一次データ（誰がいつどう採点したか）を
本セッションは保有していない。`docs/expert-ai-validation/before/scorecard.md`は
Case1のみクリーンなPre-Fix実行だが、その時点では16点Rubric自体がまだ存在しなかった
（Rubricは後のNeutral検証で新設されたため、Before実行時に遡って採点されたもの
ではない）。したがって9.6/16という数値は依頼書側の一次評価をそのまま参照した
外部アンカーであり、本セッションで独立に再現・検証したものではない。

---

## 6. Case別 Primary Assessment（Original Before情報がある場合は併記）

| Case | Original After v2 Primary Key Point | control_type | confidence |
|---|---|---|---|
| Case1 Trump-Xi | 米国の対中先端半導体・AI輸出規制（および関連投資規制）の強度と範囲 | institutionally_constrained | 低 |
| Case2 Putin | NATOのウクライナ加盟・安全保障上の位置づけ | distributed | 低 |
| Case3 Netanyahu | イランのホルムズ海峡妨害能力とその行使実績 | structural | 中 |
| Case4 MBS | サウジの安全保障保証と政権安定に対する米国依存度の再設計 | mixed | 低 |
| Case5 Kim | 北朝鮮の核・ミサイル能力をめぐる抑止・エスカレーションのバランス | mixed | 低 |

Case1のOriginal Before（`before/scorecard.md`、唯一クリーンな定性データ）は
「米国の対中AI・半導体規制の強度・適用範囲（輸出管理・投資規制）の強度と適用範囲」
——**Original After v2とほぼ同一の骨格**であり、Key Pointの置き方自体は大きく
変わっていない。変化しているのはその後のReasoning（候補比較・棄却理由・
disconfirming_signal・Evidence Discipline）の質である。

---

## 7. Actor Depth: Before(generic) → Neutral(partial/deep) → Original After v2

| Case | Actor(s) | Original After v2評価 | 根拠 |
|---|---|---|---|
| Case1 | trump, xi | **partial** | pattern_applicability_assessmentはあるが決定的フォールバック由来（`pattern_applicability_fallbacks`発火）、actor_model_challengeは未生成（Guard fail） |
| Case2 | putin | **partial〜deep** | actor_model_challenge生成（inconclusive、具体的条件付き）、pattern_applicability_assessmentはフォールバック由来 |
| Case3 | netanyahu | **deep** | 命名Pattern2件、pattern_applicability_assessment（LLM生成）、actor_model_challenge（inconclusive）が揃う。Neutralと同水準まで復元 |
| Case4 | MBS | **deep** | 命名Pattern2件、applicability、actor_model_challengeが揃う。加えて`actor_ref`も"MBS"へ正しく名前解決（初回Runの生UUID出力は完全に解消） |
| Case5 | kim_jong_un | **partial〜deep** | actor_model_challenge生成、pattern_applicability_assessmentはフォールバック由来 |

Before（依頼書記載）: 全Actor共通で無条件の人物解説（`Trump=dealmaker`等）。
Original After v2では**5/5ケースでgenericを脱し**、3/5ケース（Netanyahu/MBS/Putin）で
実質的な"deep"評価に達した——Neutral v3（3/5ケースがdeep: Putin/Netanyahu/MBS）と
**ほぼ同一の分布**であり、Actor Depthの改善もNeutral Promptへの過適合ではなく
汎化していることを支持する。

---

## 8. Hypothesis Register: retained / rejected / unresolved

| Case | H0 | H1 | H2 | H3 | H4 | retained/rejected/unresolved |
|---|---|---|---|---|---|---|
| Case1 Trump-Xi | retained | unresolved | retained | unresolved | rejected | 2 / 1 / 2 |
| Case2 Putin | retained | rejected | retained | unresolved | rejected | 2 / 2 / 1 |
| Case3 Netanyahu | retained | unresolved | retained | rejected | unresolved | 2 / 1 / 2 |
| Case4 MBS | retained | unresolved | retained | rejected | rejected | 2 / 2 / 1 |
| Case5 Kim | retained | unresolved | retained | unresolved | rejected | 2 / 1 / 2 |

全件retainedのケースは0件。全件で少なくとも1件はrejected、1件はunresolvedとして
残っており、単一ストーリーへの収斂を避けている。

---

## 9. Evidence Discipline実証（決定的補正の実発火ログ）

初回Original After（v1）ではActor Intelligenceデータが痩せていたため観測できなかった、
「豊富なデータがあってもEvidence Disciplineが機能するか」を今回初めて確認できた。

| Case | historical_evidence_separations（発火箇所数） | actor_evidence_separations（除去したHistorical Episode ID数） | pattern_applicability_fallbacks |
|---|---|---|---|
| Case1 | 0 | 0 | 2箇所（trump, xi） |
| Case2 | 3 | 3件除去（key_actor_profiles[0]から2件、situation_actor_assessment[1]から1件） | 1箇所 |
| Case3 | 1 | 1件除去 | 1箇所 |
| Case4 | 3 | 2件除去 | 1箇所 |
| Case5 | 3 | 2件除去 | 1箇所 |

**Case2 Putinが典型例**: Actor Intelligence Packには`d0ef206d-...`（ワグネル反乱）・
`c7582597-...`（Direct Line/Valdai Club）という2件のHistorical Episode IDが提示
されていたが、LLMがこれらを`key_actor_profiles[0]`や`situation_actor_assessment[1]`の
**Current Evidenceであるかのように引用しようとした際、決定的post-processingが
実際にその場でこれを検出・除去した**。これはP0-2/2.5で実装した
`separate_historical_evidence_in_actor_and_situation_assessment`が、豊富な
Actor Intelligenceデータが実際に存在する条件下でも正しく機能することの直接証拠。

---

## 10. Quality Guard結果

| Case | passed | failed | initial_failed_checks |
|---|---|---|---|
| Case1 Trump-Xi | 32 | 2 | stakeholders_grounded_in_source_pack, actor_model_challenged |
| Case2 Putin | 33 | 1 | stakeholders_grounded_in_source_pack |
| Case3 Netanyahu | 33 | 1 | stakeholders_grounded_in_source_pack |
| Case4 MBS | 32 | 2 | fact_claims_have_source_ref, stakeholders_grounded_in_source_pack |
| Case5 Kim | 33 | 1 | stakeholders_grounded_in_source_pack |

`stakeholders_grounded_in_source_pack`は既知の既存制約（本アップグレードのスコープ外、
Source Pack本文に主体名の完全一致表記が無い場合の既知の誤検知傾向を含む）。
`actor_intelligence_provenance_present`は5/5ケース全てPass（初回Runで唯一
Case3のみFailしていたのが解消——Actor Intelligenceデータ復元の直接効果）。
`main_scenario_has_supporting_evidence`も5/5 Pass（初回RunのCase4 Failが解消）。
いずれのケースも修復(Stage3 repair)は発火せず。

---

## 11. Failure Matrix（Before → Neutral v3 → Original After v2）

| Failure Pattern | Before | Neutral After v3 | Original After v2 |
|---|---|---|---|
| Actor generalization | あり | 解消(5/5でpartial/deep) | 解消(5/5でpartial/deep、3/5がdeep) |
| Evidence substitution | あり | Guard検出のみ | Guard検出のみ（同型、未変更範囲） |
| Weak analogy | 未検証 | 発生なし | 発生なし（全historical_analogies_usedがweak/medium、confidenceは中/低に整合） |
| Control structure | 症状再現不可 | 発生なし | 発生なし |
| Historical/current conflation | あり(v1時点) | 解消(0/5) | **解消かつ実発火を確認(0/5、5ケース中4ケースで実際に補正が発動)** |
| Unsupported confidence | あり | 0/5 | **0/5**（高Confidenceは1件も発生せず） |
| Frame-breaking weakness | 未評価 | Case5のみ弱い(1/5) | Case2/3/5が弱い(3/5) |
| Media underuse | 未評価 | 未評価(OFF) | 未評価(OFF、Freeze対象) |
| Actor-model-challenge missing | 未評価(概念新設) | Case2/5でFail(2/5) | **Case1のみFail(1/5、4/5で生成成功)** |

Frame-breaking weaknessがNeutral(1/5)よりOriginal After v2(3/5)で悪化しているように
見える点は、13章で詳述する通り、**Case2/3は「フレームが浅い」のではなく「別の
妥当なフレームに着地している」**（Case2: NATO拡大阻止という主流の説明、Case3:
Netanyahu個人よりIran構造要因を重視）ため、Neutralの「政権生存」「個人責任回避」
という着地点と比べて相対的にconventionalだが、誤りとは言えない。Case5のみが
3回の実行全て（Neutral・v1・v2）で一貫して同一の弱さ（H0=核・ミサイル=体制保証を
無批判にPrimary維持）を示しており、これが唯一の**構造的**Frame-breaking弱点と
判断する。

---

## 12. Case1特別観察の再確認: Trump-Xi Frame DiscoveryはNeutral固有の弱さだったか

初回Original After（v1、Retrieval設定ミスがあった状態）とOriginal After v2
（Retrieval正常）の両方で、Case1は一貫して「主要な意思決定主体」というActor中心の
問い方から離れ、「制度化された技術管理枠組み」という構造・制度要因へ再定義できて
いる（v1: institutionally_constrained、v2: institutionally_constrained、両方とも
disconfirming_signal・4件の候補比較・3〜2件の明示的棄却を伴う）。

**これでActor Intelligenceデータの充足度に関わらず、2回連続で同じ結果が再現された。**
Neutralでのみ観測された「Case1が2→1のまま回復しない」というFrame Discovery弱さは、
**Neutral Prompt特有の副作用だった、という結論を維持してよい確度に達した**
（Retrieval劣化という交絡因子を排除した上での再現のため）。

一方でActor Depthは、v1（Actor Intelligence劣化下）ではpattern_applicability_
assessmentが皆無だったのに対し、v2（正常）ではフォールバックで補完されている
（`pattern_applicability_fallbacks`）ものの、`actor_model_challenge`は依然として
Case1だけ生成されなかった——これはCase1固有の残存Gapとして次段階の検討候補にする。

---

## 13. 総括

- **正式なOriginal Before(9.6/16) → Original After v2(14.8/16)比較が初めて成立した。**
  依頼書の外部アンカー(9.6/16)との単純な数値比較では約5.2点の改善。ただし11章の
  留保通り、Before側は本セッションが独立に検証した数値ではない。
- **Neutral v3(14.2/16)とOriginal After v2(14.8/16)がほぼ同水準（後者がわずかに
  上回る）** であり、Depth & Grounding UpgradeがNeutral Promptへの過適合ではなく
  汎化していることを、Actor Intelligenceデータを揃えた公正な条件下で実証できた。
- **Evidence Discipline系の決定的補正（Historical/Current分離、Confidence
  Capping）が、豊富なActor Intelligenceデータを持つ条件下でも実際に発火し、
  正しく機能することを直接確認した**（9章）——初回Run（データが痩せていた）では
  この検証ができていなかった。
- **Case5 Kimの Frame-breaking弱さは、Neutral・Original v1・Original v2の3回連続で
  再現された唯一の構造的弱点**であり、次のCalibration対象として最優先候補とする。
- Case2/Case3のFrame Discoveryは「弱い」というより「Neutralとは異なる、より
  Conventionalだが妥当な着地点」であり、単純な劣化とは評価しない。

## 14. Remaining Gaps（Session3 Calibration向け）

1. Case5 Kimの核心Frame（核・ミサイル=体制保証）が3回連続で無批判にPrimary維持
   される傾向——北朝鮮分析特有の定型パターンへの依存が疑われる。
2. Case1のみ`actor_model_challenge`が2回連続で生成されない——Trump/Xiという
   Actor特性（decision_patternsの内容）か、Prompt側の問題かは未特定。
3. `stakeholders_grounded_in_source_pack`が依然として最頻出のFail（5/5中4ケース）
   ——本アップグレードのスコープ外だが、Source Pack側の主体名表記の一貫性改善が
   将来的なQuality Guard通過率向上に寄与する可能性がある。

---

**ここで一度Stopする。** Media Intelligence再統合・P1修正には、この報告の
ユーザー確認を経てから着手する。
