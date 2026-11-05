# RUBIO_VANCE_ROLE_CHOREOGRAPHY — Validation Rubric

> 将来、実際にExpert AIを実行してこのCaseを評価する際に使うルーブリック。今回のCase Pack
> 作成では**適用しない**。
>
> **重大な注記**: このケースはBlind Inputの情報量が著しく少ない（転送元記事本文が
> 未確保のため）。このRubricで`GENERATION_GAP`/`RETRIEVAL_GAP`の判定が出た場合、それは
> Expert AIの能力不足ではなく、Case Pack自体のSource不足に起因する可能性が高いことを
> 踏まえて解釈すること。

## semantic_role別の評価方針

| semantic_role | 対象Gold Unit | 適用する評価軸 |
|---|---|---|
| `cue_implication` | GU1 | Cue Hit / Question-Reframe Hit / Step2 Reasoning内でのExposure |
| `primary_reading` | GU2 | Candidate Hit / S Ranking / Top3・Top5 Retention / Portfolio Rescue / Final Exposure Hit / Causal Structure Hit |
| `relationship_insight` | GU3 | Actor/Relationship Hit / Causal Structure Hit / Step2 Reasoning Story内でのExposure |
| `actor_insight` | GU4 | Actor/Relationship Hit / Causal Structure Hit / Step2 Reasoning Story内でのExposure |

## 評価軸

### 1. Cue Hit
「発言と行動の不一致」（GU1）を、AIが生成したCue／Anomaly系の記述が拾えているか。
**ただし**、Blind Inputには具体的な行動事実（訪問国・会合出欠）が含まれていないため、
AIがこのCueを拾えなくても、それは能力不足ではなくSource不足による可能性が高い
（Failure Classificationでは`RETRIEVAL_GAP`を優先的に検討すること）。

### 2. Question / Reframe Hit
「発言と行動どちらが本音か」「意図的な役割分担ではないか」（`gold_questions`）に相当する
問いをAIが立てられているか。

### 3. Candidate Hit
GU2（primary_reading、Good Cop/Bad Cop Role Choreography）に相当する読みが、CQA段階で
生成された内部候補プールに存在したか。

### 4. S Ranking / 5. Top3 Retention / 6. Top5 Retention
GU2のCandidateが最終的な配列上、何位相当になったか。

### 7. Portfolio Rescue
main_scenario／alternative_scenariosに含まれなかった場合、Reading Portfolioが拾えたか。

### 8. Final Exposure Hit
最終的にStep2でユーザーへ表示される候補に、GU2に相当する内容が含まれたか。

### 9. Causal Structure Hit
「Rubio=Good Cop、Vance=Bad Cop」という役割構造、およびGU3（ウィトコフ・クシュナーとの
並行構造）が、単発の指摘ではなく政権全体のパターンとして言及されているかを確認する。

### 10. Overfit / Gold Leakage
Runtime Input／Prompt／Retrievalへ、`RUBIO_VANCE_ROLE_CHOREOGRAPHY_EXPERT_GOLD.md`の
内容、江口さんの言い回し、Gold Unit ID・ラベルを絶対に混入させていないことを確認する。
`existing_model_mapping`（G06 Means-Goal/Role Choreography等）もGeneration側には渡さない。

## Hard Blind と Session-Faithful の使い分け

このケースはSource不足のため、Session-Faithful Inputに追加できる構造化質問が存在しない
（`RUBIO_VANCE_ROLE_CHOREOGRAPHY_SESSION_FAITHFUL_INPUT.md`参照）。したがって、現時点では
Hard Blind評価のみが実施可能であり、かつその評価自体もSource不足の影響を強く受ける。

## 本Caseを評価に使う前の推奨事項

転送元記事本文（"Delicate Détente: Rubio Warns of 'Deindustralisation' of the West"）を
確保できない限り、このCaseは他の4ケースと同列のBlind Test評価には適さない。確保できた
場合は、Blind Input・Expert Gold・本Rubricの更新が必要である。
