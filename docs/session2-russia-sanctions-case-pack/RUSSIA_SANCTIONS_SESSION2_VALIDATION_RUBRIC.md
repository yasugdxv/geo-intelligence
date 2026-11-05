# RUSSIA_SANCTIONS_SESSION2 — Validation Rubric

> 将来、実際にExpert AIを実行してこのCaseを評価する際に使うルーブリック。今回のCase Pack
> 作成では**適用しない**。
>
> 前提: 評価対象はGold文章との一字一句の一致ではなく、Cue／Question／Actor／Relationship／
> Causal Structure／Hypothesis／Readingの構造的な一致である。

## semantic_role別の評価方針

全Gold Unitが独立したReading Cardとして出ることは要求しない。`semantic_role`に応じて
適用する評価軸を変える。

| semantic_role | 対象Gold Unit | 適用する評価軸 |
|---|---|---|
| `reframe` | GU1 | Cue Hit / Question-Reframe Hit / Step2 Reasoning内でのExposure |
| `actor_insight` | GU2 | Actor/Relationship Hit / Causal Structure Hit / Step2 Reasoning Story内でのExposure |
| `evidence_practice` | GU3 | Cue Hit / Step2 Reasoning内でのExposure（Evidence参照方法として） |
| `reading_candidate` | GU4 | Candidate Hit / S Ranking / Top3 Retention / Top5 Retention / Portfolio Rescue / Final Exposure Hit |
| `structural_explanation` | GU5 | Actor/Relationship Hit / Causal Structure Hit / Step2 Reasoning Story内でのExposure |
| `diagnostic_question` | GU6 | Cue Hit / Question-Reframe Hit / Step2 Reasoning内でのExposure |

特に重要な点: このケースでは**GU1（前提への疑義）こそが江口さんの読みの核心**であり、
GU1を拾えたかどうかがこのケースの評価で最も重視されるべきである。GU4（reading_candidate）
だけを拾ってGU1を拾えていない場合、「結論は似ているが前提検証のプロセスを経ていない」
可能性があり、Causal Structure Hitで区別する。

## 評価軸

### 1. Cue Hit
江口さんが着目した違和感（`gold_cues`）を、AIが生成したCue／Anomaly系の記述が拾えているか。
特にGU1「初期分析の前提自体への疑義」を、AIも初期分析（Blind Input内のStakeholder Table）を
鵜呑みにせず検証しているか。

### 2. Question / Reframe Hit
`gold_questions`と、AIが生成したAnalysis Question／Reframeの一致度。「対ロ制裁強化の政治力学」
という表層的な整理にとどまらず、「Trumpは本当にそれを望んでいるか」まで問いを差し戻せて
いるか。

### 3. Candidate Hit
GU4（reading_candidate）に相当する読みが、CQA段階で生成された内部候補プールに存在したか。

### 4. S Ranking / 5. Top3 Retention / 6. Top5 Retention
GU4のCandidateが最終的な配列上、何位相当になったか。

### 7. Portfolio Rescue
main_scenario／alternative_scenariosに含まれなかった場合、Reading Portfolioが拾えたか。

### 8. Final Exposure Hit
最終的にStep2でユーザーへ表示される候補に、GU4に相当する内容が含まれたか。

### 9. Causal Structure Hit
文章表現の一致ではなく、Actor・Relationship・因果構造（GU2の「強者間直接取引」構造、GU5の
「異なる目的の両立」構造）が近いかどうかを判定する。

### 10. Overfit / Gold Leakage
Runtime Input／Prompt／Retrievalへ、`RUSSIA_SANCTIONS_SESSION2_EXPERT_GOLD.md`の内容、
江口さんの言い回し、Gold Unit ID・ラベル、Gold Reasoning Pattern名、Session文字起こしの
該当箇所を絶対に混入させていないことを確認する。既存の`existing_model_mapping`も
Generation側には渡さない。

このCaseで使ってよいのは`RUSSIA_SANCTIONS_SESSION2_BLIND_INPUT.md`（Hard Blind評価）
または`RUSSIA_SANCTIONS_SESSION2_SESSION_FAITHFUL_INPUT.md`（Session-Faithful評価）のみ。

## Hard Blind と Session-Faithful の使い分け

| | Hard Blind | Session-Faithful |
|---|---|---|
| 含む情報 | Situation + Known Background + 初期分析（GPT草案）+ 中立的なAnalysis Requestのみ | 上記に加え、`5. 江口さんに聞きたいこと`の全質問セットを含む |
| 測るもの | AI自身が初期分析の前提を疑えるか | 前提を疑うよう誘導する質問を与えられれば到達できるか |
| Goldに未到達だった場合の解釈 | Premise Verification Gap（前提検証プロセス自体の欠如） | Reasoning Gap（問いを与えても読みに到達できない） |

**注記**: 本Caseの「Hard Blind」は、他ケースと異なり、ニュースの生の事実だけでなく、
AIが事前に作成した初期分析ドラフト（Stakeholder Table含む）を含んでいる。この構造上、
Hard Blind評価は「白紙から候補を作れるか」ではなく「既存の（もっともらしい）初期分析を
批判的に検証できるか」を測ることになる点に注意する。
