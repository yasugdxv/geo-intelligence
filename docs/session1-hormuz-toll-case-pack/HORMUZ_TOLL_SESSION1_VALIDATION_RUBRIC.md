# HORMUZ_TOLL_SESSION1 — Validation Rubric

> 将来、実際にExpert AIを実行してこのCaseを評価する際に使うルーブリック。今回のCase Pack
> 作成では**適用しない**（Expert AI実行・S評価・Reading Portfolio評価はまだ行わない）。
>
> 前提: 評価対象はGold文章との一字一句の一致ではなく、Cue／Question／Actor／Relationship／
> Causal Structure／Hypothesis／Readingの構造的な一致である
> （`HORMUZ_TOLL_SESSION1_EXPERT_GOLD.md`参照）。

## semantic_role別の評価方針（2026-09-24修正）

R1〜R4は同格のReading Candidateではない。`semantic_role`に応じて適用する評価軸を変える。
**全Gold Unitが独立したReading Cardとして出ることは要求しない。**

| semantic_role | 対象Gold Unit | 適用する評価軸 |
|---|---|---|
| `reading_candidate` | R2, R3 | 3.Candidate Hit / 4.S Ranking / 5.Top3 Retention / 6.Top5 Retention / 7.Portfolio Rescue / 8.Final Exposure Hit |
| `cue_implication` | R1 | 1.Cue Hit / 2.Question-Reframe Hit / Step2 Reasoning内（`why_this_matters`等の説明文）でのExposure |
| `structural_explanation` | R4 | 9.Causal Structure Hit（Actor/Relationship含む）/ Step2 Reasoning Story内でのExposure |

R1（cue_implication）とR4（structural_explanation）に対して3〜8番の軸（Candidate Hit／
S Ranking等）を適用しない——これらはそもそも独立したReading Candidateとして生成される
ことを期待する性質のGold Unitではないため。

## 評価軸

### 1. Cue Hit
江口さんが着目した違和感（`gold_cues`、特に「核心は金額ではなく正当化の論理」）を、
AIが生成したCue／Anomaly系の記述（例: `key_point_assessment`、`initial_anomaly`）が
拾えているか。

### 2. Question / Reframe Hit
同じように問題設定を一段変えられたか（`gold_questions`と、AIが生成した
Analysis Question／Reframeの一致度）。「20%は高いか安いか」という表層的な問いに
とどまっていないかを確認する。

### 3. Candidate Hit
江口さん型Reading（R1〜R4のいずれか）が、CQA段階で生成された内部候補プール
（現行実装ではmain_scenario／alternative_scenarios、または`profile=deep`実行時は
`hypothesis_register`）に存在したか。

### 4. S Ranking
そのCandidateが最終的な配列上、何位相当（rank）になったか
（現行実装は独立したスコアリング関数を持たず、main_scenario=rank1固定、
alternative_scenariosは生成順という配列順で決まる——詳細は本Case Packと同時に
実施したExpert AIアーキテクチャ監査を参照）。

### 5. Top3 Retention
rank 1〜3以内に残ったか。

### 6. Top5 Retention
rank 1〜5以内（`display_top_n`既定値）に残ったか。

### 7. Portfolio Rescue
main_scenario／alternative_scenariosに含まれなかった場合、Reading Portfolio
（`alternative_causal_structure`／`premise_reversal`／`wildcard_non_obvious`の
3スロット）がこのReadingに相当する内容を独自に拾えたか。

### 8. Final Exposure Hit
最終的にStep2でユーザーへ表示される候補（`displayed=true`かつ、UIの初期表示
`display_top_n`件以内）に、このReadingに相当する内容が含まれたか。

### 9. Causal Structure Hit
文章表現の一致ではなく、Actor・Relationship・因果構造（例: R4の「二匹の虎」
バランス構造、R2の「交渉カードとしての海峡管理権」構造）が近いかどうかを判定する。
`gold_actors`／`gold_relationships`との対応を確認する。

### 10. Overfit / Gold Leakage
Runtime Input／Prompt／Retrievalへ、以下を絶対に混入させていないことを確認する。

- `HORMUZ_TOLL_SESSION1_EXPERT_GOLD.md`の内容
- 江口さんの言い回し（「二匹の虎」「裏読み」等の固有表現）
- Gold Reading ID・ラベル
- Gold Reasoning Pattern名
- Plaudの回答部分の要約
- 後日作成された分析資料でGoldを要約したもの

このCaseで使ってよいのは`HORMUZ_TOLL_SESSION1_BLIND_INPUT.md`
（Hard Blind評価）または`HORMUZ_TOLL_SESSION1_SESSION_FAITHFUL_INPUT.md`
（Session-Faithful評価）のみである。

## Hard Blind と Session-Faithful の使い分け

| | Hard Blind | Session-Faithful |
|---|---|---|
| 含む情報 | Situation + Known Background + 中立的なAnalysis Requestのみ | 上記に加え、分解質問（②〜⑤）を含む |
| 測るもの | AI自身がCue／Questionを立てられるか | 適切な問いを与えられればReasoning自体は到達できるか |
| Goldに未到達だった場合の解釈 | Cue Detection Gap または Question Formation Gap の可能性 | Reasoning Gap（問いを与えても読みに到達できない）の可能性が高い |
