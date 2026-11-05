# HORMUZ_BLOCKADE_SESSION3 — Validation Rubric

> 将来、実際にExpert AIを実行してこのCaseを評価する際に使うルーブリック。今回のCase Pack
> 作成では**適用しない**。
>
> 前提: 評価対象はGold文章との一字一句の一致ではなく、Cue／Question／Actor／Relationship／
> Causal Structure／Hypothesis／Readingの構造的な一致である。
>
> **Source Gapの注記**: このケースはSession 3ではなく2026-02-21のメール返信（M140）を
> 一次資料として使用している。将来Session 3の文字起こしが入手できた場合、このRubricと
> Gold Unitは更新が必要になる可能性がある。

## semantic_role別の評価方針

| semantic_role | 対象Gold Unit | 適用する評価軸 |
|---|---|---|
| `reframe` | GU1 | Cue Hit / Question-Reframe Hit / Step2 Reasoning内でのExposure |
| `primary_reading` | GU2 | Candidate Hit / S Ranking / Top3・Top5 Retention / Portfolio Rescue / Final Exposure Hit |

GU2は条件付きの読み（`condition`フィールド参照：全面戦争化＋米国による輸出/金融遮断という
条件下でのみ成立）である点に注意する。AIがこの条件を明示せずに「イランは死なばもろとも的に
封鎖する」とだけ出力した場合、Causal Structure Hitとしては部分点（条件の欠落）として扱う。

## 評価軸

### 1. Cue Hit
「専門家の見方が一致していること自体への懐疑」（GU1）を、AIが生成したCue／Anomaly系の
記述が拾えているか。

### 2. Question / Reframe Hit
「封鎖しないという結論の前提は、全面戦争下でも同じ形で成立するのか」という時制の検証
（`gold_questions`）に相当する問いをAIが立てられているか。

### 3. Candidate Hit
GU2（primary_reading）に相当する読み（死なばもろとも／得失が同じなら封鎖の合理性が
生まれる）が、CQA段階で生成された内部候補プールに存在したか。

### 4. S Ranking / 5. Top3 Retention / 6. Top5 Retention
GU2のCandidateが最終的な配列上、何位相当になったか。

### 7. Portfolio Rescue
main_scenario／alternative_scenariosに含まれなかった場合、Reading Portfolio
（特に`premise_reversal`スロット——GU1/GU2はまさに前提反転型の読みである）が拾えたか。

### 8. Final Exposure Hit
最終的にStep2でユーザーへ表示される候補に、GU2に相当する内容が含まれたか。

### 9. Causal Structure Hit
「開放と封鎖の得失が同じになる」という条件が明示されているか。単に「イランは自暴自棄に
なる可能性がある」という結論だけでは、条件（GU1）を欠いた劣化版とみなす。

### 10. Overfit / Gold Leakage
Runtime Input／Prompt／Retrievalへ、`HORMUZ_BLOCKADE_SESSION3_EXPERT_GOLD.md`の内容、
「死なばもろとも」という江口さん固有の言い回し、Gold Unit ID・ラベルを絶対に混入させて
いないことを確認する。

このCaseで使ってよいのは`HORMUZ_BLOCKADE_SESSION3_BLIND_INPUT.md`のみ（Session-Faithful
Inputは実質的に同一内容であり、追加の構造化質問は存在しない）。

## Hard Blind と Session-Faithful の使い分け

このケースはSource不足のため、Session-Faithful Inputに追加できる構造化質問が存在しない
（`HORMUZ_BLOCKADE_SESSION3_SESSION_FAITHFUL_INPUT.md`参照）。したがって、現時点では
Hard Blind評価のみが実施可能である。
