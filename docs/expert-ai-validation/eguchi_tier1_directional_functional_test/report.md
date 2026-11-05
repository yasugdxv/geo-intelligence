# Tier1 Human/Relationship Model — 機能試験（Trump⇄Xi 人物間相互作用ケース）

作成日: 2026-09-06
目的: Tier1全体の総合品質ベンチマークではなく、**DirectionalPerception / perceived_path /
event_role / Actor-Analyst Response Separationが実際に発火し意味を持つかの機能試験**（ON単独、
OFF比較は今回行わない）。

## Case選定・実行条件

- 既存Case1（`case_id=61447d94-...`、`Expert AI Benchmark: Trump-Xi US-China Semiconductor
  & AI Controls`）と同じtheme/実Evidence基盤を再利用。新規Fixture投入なし。
- **質問のみ変更**（既存の実Evidenceから自然に成立する問い、Tier1機能を出すための人工的な
  誘導はしていない）:
  > 「トランプ大統領と習近平国家主席は、今回の米中半導体・AI規制(輸出管理強化、Nvidia・TSMC
  > への制限、中国側の対抗措置等)を巡って、それぞれ相手にどのような行動を取らせたい、あるいは
  > 思いとどまらせたいと考えていると推定されるか。各々が「これをすれば相手はこう反応するはず
  > だ」と想定している経路と、実際に生じると考えられる相手の反応との間にはどのような違いが
  > あるか。」
- Evaluation Input Gate: 実文書3件（Nvidia輸出制限/TSMC 7nm停止/CNBC記事、いずれもis_mock=false、
  質問に直接関連）、Trump/Xi双方がactive Actor Intelligence Profile、Fixtureなし。
  Relationship record（geo_relationships）はTrump/Xiとも0件——このDBには元々どの主体間にも
  関係レコードが存在しないという既知の制約が残ったまま（Gate条件のうちRelationship側は
  不成立、Actor Intelligence側のみ充足）。
- `persist=False`（今回実装したdry-run、**実DBへの書込みが0件であることを実運用でも確認済み**
  ——実行後の`geo_cases.updated_at`が実行前と完全に同一だった）、`profile=deep`,
  `mode=case_analysis`。External Research=OFF（環境変数未設定で確認）。
- Reasoning LLM calls = 2/2（Stage1+Stage2、Repairなし）、Research calls = 0。

## 生成結果サマリ

`key_actor_profiles` = **Trump, Xi の2名のみ**（Congress/BIS等の制度アクターは今回登場せず、
質問の設計通り人物2名に絞られた）。品質ガード45件中44件pass、1件fail
（`supporting_knowledge_refs_grounded`、後述）。

## 必須確認5項目の結果

| フィールド | Trump | Xi |
|---|---|---|
| `directional_perceptions` | ✓ trump→xi 1件生成 | ✓ xi→trump 1件生成 |
| `perceived_path` | ✓ 生成（規制→ボトルネック→圧力→譲歩引き出し→報酬、という因果経路） | ✓ 生成（外圧を国内投資正当化に転用→迂回→国産化、という経路） |
| `event_role` | ✓ role_type=`bargaining_card` | ✓ role_type=`constraint` |
| `actor_expected_response`（situation_actor_assessment、3 Issue Component分） | ✓ 3件とも生成 | ✓ 3件とも生成 |
| `analyst_expected_response`（同上） | ✓ 3件とも生成 | ✓ 3件とも生成 |

## 意味評価（6観点）

### 1. A→BとB→Aを正しく分離できたか — ✅ Working
`trump→xi`（「強い規制→中国は早期にディールに応じる」）と`xi→trump`（「米国は簡単には緩めないが
完全封鎖はコストが高い」）は**内容が実質的に異なる**——どちらかを反転しただけの水増しではない。
双方向とも生成された初めてのケース。

### 2. 認識がEvidence/Actor Intelligenceにgroundedされているか — ⚠️ Partially working（重大な発見あり）
`trump→xi`はevidence_ids=null・confidence=低で、根拠薄弱であることを正直に示している（良好）。
`xi→trump`はevidence_ids=`["b5c17e2e-a432-4f0b-912f-90b6aa6ae8c5"]`を提示しているが、**この
IDは実在しない**——Source Pack内の実文書2件のID(`b5c17e2e-a432-4e45-ba45-7c618020326d`の
プレフィックスと、`755e629d-1ade-4f0b-912f-90b6aa6ae8c5`のサフィックス)を組み合わせて作られた
**捏造ID**であることを確認した。

**重大な発見**: この捏造IDは既存の`scrub_fabricated_evidence_ids`（Evidence ID捏造の決定的
スクラブ機構）で除去されなかった。同関数のソースコードを確認したところ、
`key_actor_profiles`/`situation_actor_assessment`/`media_source_context`等の既存フィールドは
走査対象に含まれるが、**`directional_perceptions`が走査対象リストに含まれていない**
（Tier1実装時に追加し忘れた）。実際に`quality_evaluation.removed_fabricated_ids`には
`situation_actor_assessment[4]`関連の捏造ID除去は記録されているが、
`directional_perceptions`関連の除去は一切記録されていない。**これはTier1実装の実際の
バグであり、今回の機能試験で初めて顕在化した。**

### 3. Goal→Perceived Path→Expected Response→Desired Stateがつながっているか — ✅ Working
Trump: `issue_objective`（対中AI優位阻止＋交渉カード確保）→`perceived_path`（規制→ボトル
ネック→圧力→譲歩引き出し→報酬としての緩和）→`event_role`（bargaining_card）→
`directional_perceptions`（早期ディール期待）→`situation_actor_assessment.actor_expected_
response`（同内容の局面別展開）という一貫した連鎖が実際に生成された。Xi側も同様に一貫。
design docが意図した「Actor→Goal→Perceived Path→Event Role→Expected Response→Desired State」
というつながった読みが、初めて実地で確認できた。

### 4. Actor本人の期待とAnalyst予測に意味のある差があるか — ✅ Working
Trump: 本人期待「強い規制で中国が早期にディールに応じる」 vs Analyst予測「中国は痛みを受け
入れつつ迂回・国産化で長期戦を選び、早期ディールには応じない」。Xi: 本人期待「米国は中国の
痛みを見て一定緩和」 vs Analyst予測「安全保障上の懸念から簡単には緩和せず、長期デカップリング
進行」。**両者とも言い換えではなく実質的に異なる結論**であり、「本人の希望的観測」と「分析者の
実際の読み」という設計意図通りの分離ができている。

### 5. その差がCandidate/Scenarioに反映されたか — ✅ Working
`main_scenario.description`が明示的にこの落差を主軸に据えている:「トランプ側の想定では
...早期にディールに応じると見ている可能性があるが、実際には習近平は...長期戦を選び...
トランプの期待よりも『粘る中国』と『長期化する技術デカップリング』が現実化するリスクが高い」。
Actor期待とAnalyst予測の落差が、装飾的なメタデータに留まらず**Main Scenarioの結論そのもの
を形作っている**。これは今回確認できた中で最も明確なTier1の価値実証。

### 6. 人物像のもっともらしい創作が増えていないか — ⚠️ Partially working
`hypothesis_register`はH0(rejected)/H1(retained)/H2(unresolved)/H3(rejected)/H4(unresolved)
と、全件retainedにならず適切に競合させている。confidenceも一貫して低〜中に抑制されており、
過剰な断定は見られない。ただし上記2.の捏造ID問題があるため、「根拠なき断定」自体は防がれて
いても「根拠の実在性チェック」に穴があったことが今回判明した——**もっともらしい創作の抑制は
confidence/statusレベルでは機能しているが、evidence_id捏造レベルでは機能していなかった**。

## 判定まとめ

| 機能 | 判定 |
|---|---|
| DirectionalPerception（A⇄B分離・因果連鎖への統合） | **Working** |
| DirectionalPerceptionのEvidence捏造防止 | **Not working**（`scrub_fabricated_evidence_ids`未対応、要修正） |
| `perceived_path` | **Working** |
| `event_role` | **Working** |
| `actor_expected_response`/`analyst_expected_response`分離 | **Working**（言い換えではなく実質的な差を生成） |
| Candidate/Scenarioへの反映 | **Working**（Main Scenarioの結論に直接反映） |
| Relationship Pack由来のgrounding | **Not evaluable**（このDBにTrump-Xi間の関係レコードが
  存在しないため、この機能試験では発火材料が無かった） |

Guard PASS率（44/45）だけでこの結論を出したのではなく、上記の意味内容を個別に読んだ上での
判定である。

## 修正実施記録（2026-09-06、コード修正・オフライン回帰のみ、実LLM再実行なし）

`geo_expert_quality_guard.scrub_fabricated_evidence_ids`へ、`directional_perceptions[].
evidence_ids`の走査を追加した（`situation_actor_assessment`と同じ`_clean_evidence_id_array`
決定的処理を再利用、新規Azure callなし、observer/target_actor_ref・perception等の他フィールドは
削除しない）。新規オフラインテスト3件を追加（fabricated ID除去/valid ID維持/
DirectionalPerception自体は消えないことの確認）。**リポジトリ全体626件（既存623+新規3）
全てpass**。実LLMでの機能試験の再実行は行っていない（オフラインテストで走査対象への
組み込みを確認できれば十分というユーザー判断のため）。

---

# Phase① Closeout（Eguchi Expert AI Final v1.0）

対象: 江口さん作成の設計フィードバック文書のうち、Session1・2＋過去レビューから得た思考法を
専門家AI設計へ反映し、実際に機能するかを確認するPhase①。本文書のTrump⇄Xi機能試験をもって
Phase①をCloseする。

## 最終判定

| 項目 | 状態 |
|---|---|
| Situation Frame | 既存実装（`key_point_assessment`）を活用、変更なし |
| Actor Big Flow（`perceived_path`/`event_role`） | **Working**（実地確認済み、本レポート） |
| DirectionalPerception（A⇄B分離） | **Working**（実地確認済み、本レポート） |
| Actor expected / Analyst predicted分離 | **Working**（実地確認済み、本人期待とAnalyst予測が実質的に乖離し、main_scenarioの結論へ反映） |
| Candidate Generation → Scenario接続 | **Working**（Prompt実装＋Scenarioへの接続を確認） |
| Event Role | **Working**（bargaining_card/constraint等、質的に妥当な区別を確認） |
| Evidence Grounding（DirectionalPerception分） | **Fixed**（`scrub_fabricated_evidence_ids`の走査漏れを本日修正、オフライン回帰626件pass） |
| Relationship persistent data | **NOT_EVALUABLE_DATA_GAP**（このDBに`geo_relationships`実データが0件のため。Phase①完了条件から除外） |
| EvidenceUse discrimination（usage分類等） | Deferred（Tier2以降） |
| Candidate relations（exclusive/compatible/subfactor） | Deferred（Tier2以降） |
| Frame persistence（SituationFrameのバージョニング） | Deferred（Tier3、Session3以降） |
| Human Model自動更新 | Deferred（対象外、意図的に見送り） |

## 経緯サマリ

監査(AUDIT_AND_PLAN)→P0修正(Report Studio Human Review Gate)→Tier1実装(DirectionalPerception/
perceived_path/event_role/Candidate Generation Operators/Actor-Analyst分離、および
Relationship→Actor Intelligence Pack→Promptの入力経路修正)→Evaluation Input Gate新設→
Case1 OFF/ON比較(Question Fidelity維持、ActorFlowの実効性確認、Key Actor Retention低下は
Repairの一発勝負設計による生成量希薄化と判明、バグではない)→DB write incident発見・復旧
(M9/Case1の`geo_cases.final_output`を提供元の実行前バックアップ・DB内在履歴から復元、
`persist=False`のdry-runオプションを新設)→Trump⇄Xi機能試験(DirectionalPerception等の
中核機能が実地で発火することを確認、捏造Evidence IDの走査漏れを発見・修正)。

## Phase①として持ち越さない開放課題（記録のみ）

- 既知バグ2件（`_relevant_rows_by_keywords`の英語stopword未対応、Web検索タイトルのみ保存）
  は今回のスコープ外のまま未修正。
- D3（SituationFrameバージョニング）/D4（上記既知バグ）は次フェーズの検討課題として保留。
- Tier1のaccess_scope（internal_only/public_safe軸）は当初案にあったが、ユーザー指示の
  Tier1範囲には含まれず未着手。

## 次段階（②）への引き継ぎ

AIは既に「大きな流れの中に局面を位置づける」「相手認識」「本人の期待と分析者の予測の分離」
「複数の読み」までは実地で生成できることを確認した。次に取るべきは、**その複数の読みの中で
何を強く見て、何を棄却し、どこで『これは違う』と判断するのか**という、Candidate Ranking/
Rejection LogicとFrame選択の根拠——Session3で江口さん本人からしか取れない部分である。
