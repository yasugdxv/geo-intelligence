# US_CHINA_AI_SEMICON_SESSION2 — Validation Rubric

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
| `primary_reading` | GU1 | Candidate Hit / S Ranking / Top3・Top5 Retention / Portfolio Rescue / Final Exposure Hit / Causal Structure Hit（GU1は本ケースの中核であり、reading_candidate相当の評価軸に加えCausal Structure Hitも適用する） |
| `reading_candidate` | GU2 | Candidate Hit / S Ranking / Top3 Retention / Top5 Retention / Portfolio Rescue / Final Exposure Hit |
| `structural_explanation` | GU3 | Actor/Relationship Hit / Causal Structure Hit / Step2 Reasoning Story内でのExposure |
| `relationship_insight` | GU4 | Actor/Relationship Hit / Causal Structure Hit / Step2 Reasoning Story内でのExposure |
| `diagnostic_question` | GU5 | Cue Hit / Question-Reframe Hit / Step2 Reasoning内でのExposure |

特に重要な点: GU2（reading_candidate）だけを拾えても、GU1（上位目的としての良好な米中
関係）という**階層構造**を伴わない場合、「結論は似ているが構造化のプロセスを経ていない」
可能性がある。Causal Structure HitでGU1とGU2の関係（上位目的→道具）が再現されているかを
区別して評価する。

## 評価軸

### 1. Cue Hit
江口さんが着目した違和感（`gold_cues`）を、AIが生成したCue／Anomaly系の記述が拾えているか。
特に、初期分析が並列に置いた3つの目的（圧力・競争力・関係維持）に対して、階層関係を疑って
いるか。

### 2. Question / Reframe Hit
`gold_questions`と、AIが生成したAnalysis Question／Reframeの一致度。「安全保障 vs
企業競争力のどこで線を引くか」という表層的な問いにとどまらず、「Trumpのより上位の
対中Objectiveは何か」まで問いを差し戻せているか。

### 3. Candidate Hit
GU1（primary_reading）・GU2（reading_candidate）に相当する読みが、CQA段階で生成された
内部候補プールに存在したか。

### 4. S Ranking / 5. Top3 Retention / 6. Top5 Retention
GU1・GU2のCandidateが最終的な配列上、何位相当になったか。

### 7. Portfolio Rescue
main_scenario／alternative_scenariosに含まれなかった場合、Reading Portfolioが拾えたか。

### 8. Final Exposure Hit
最終的にStep2でユーザーへ表示される候補に、GU1・GU2に相当する内容が含まれたか。

### 9. Causal Structure Hit
文章表現の一致ではなく、Actor・Relationship・因果構造（GU1→GU2の「上位目的→道具」
構造、GU3の「上位目的から逆算されるActor対立」構造）が近いかどうかを判定する。GU4の
横断的パターン（TikTok・中国EVとの構造的類似）が言及されているかも確認する。

### 10. Overfit / Gold Leakage
Runtime Input／Prompt／Retrievalへ、`US_CHINA_AI_SEMICON_SESSION2_EXPERT_GOLD.md`の
内容、江口さんの言い回し、Gold Unit ID・ラベル、Gold Reasoning Pattern名、Session
文字起こしの該当箇所（TikTok・中国EVの例を含む）を絶対に混入させていないことを確認する。

このCaseで使ってよいのは`US_CHINA_AI_SEMICON_SESSION2_BLIND_INPUT.md`（Hard Blind評価）
または`US_CHINA_AI_SEMICON_SESSION2_SESSION_FAITHFUL_INPUT.md`（Session-Faithful評価）
のみ。

## Hard Blind と Session-Faithful の使い分け

| | Hard Blind | Session-Faithful |
|---|---|---|
| 含む情報 | Situation + Known Background + 初期分析（GPT草案）+ 中立的なAnalysis Requestのみ | 上記に加え、`5. 江口さんに聞きたいこと`の全質問セットを含む |
| 測るもの | AI自身が初期分析の並列目的構造を疑い、階層化できるか | 前提を疑うよう誘導する質問を与えられれば到達できるか |
| Goldに未到達だった場合の解釈 | Structuring Gap（目的間の階層関係を見出すプロセスの欠如） | Reasoning Gap（問いを与えても読みに到達できない） |

**注記**: 本Caseの「Hard Blind」は、他ケースと異なり、ニュースの生の事実だけでなく、
AIが事前に作成した初期分析ドラフト（Stakeholder Table含む）を含んでいる。この構造上、
Hard Blind評価は「白紙から候補を作れるか」ではなく「既存の（もっともらしい）初期分析を
批判的に検証できるか」を測ることになる点に注意する。
