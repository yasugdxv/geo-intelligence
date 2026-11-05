# Phase J Hypothesis Register(最終確定版、Phase I-B0シリーズ Freeze時点)

作成日: 2026-09-16。Phase I-B0(Hungary)/I-B0.1(As-Of Harness+BC Strategy)/
I-B0.2(Iran/Hormuz)/I-B0.3(US-China/Absence Signal)、計4ケース・3 Case Family
(Family A=Hungary独立、Family B=BC Strategy+Iran関連、Family C=US-China独立)
の結果を統合した**最終仮説集**。

ここに記載する内容は全て**仮説**であり、Phase Jの変更指示・優先度確定ではない。
**Phase I-B1(実専門家レビュー)またはさらなるRetrospective Evidenceによる裏付けが
得られるまで、CQAS/Prompt/Ranking/Actor Expansion/Reframing/Absence Detection/
Scenario Selectionへは一切反映しない。**Case Family(独立性)の定義は
`docs/geopolitical-ios-phase-i-b-evidence/phase_ib0_regression_artifact.json`の
`cross_case_comparison.case_families`を正とする——BC Strategy・Iranは同一Family
内の2ケースであり、独立再現として数えない。

---

## H1｜Actor Selection / Expansion

**Status**: preliminary / inconclusive

**Reason**: 3 Family全てで何らかのActor具体化不足が見られるが(Hungary 2v5、
BC Strategy 0v10、Iran部分改善、US-China習近平の非対称欠落)、severityと形が
一貫しない(BC Strategyのみ突出して深刻、Iranは改善傾向、US-Chinaは特定の
非対称パターン)。単一のメカニズムと断定できない。

---

## H2｜Observable Indicator Reasoning

**Status**: preliminary

**Evidence**: Iran family(BC Strategyの空母配備指標欠落、Iranの生産減少耐性
論争の希薄化)。

**Limitation**: 1 independent family(Family B)のみで観測。他Familyでは
対応する具体的指標が実専門家資料側にも無く、比較不能。

---

## H3｜Rejection Logic(旧仮説を修正)

**Status**: refined hypothesis

**旧仮説(棄却)**: Counter-Hypothesis generation is weak(複数の解釈軸を
生成する力自体が弱い)。

**修正理由**: US-Chinaケースで、3つの質的に異なる解釈軸(実質的関与/象徴的
演出/制裁緩和狙い)が生成されており、多仮説生成そのものは弱くないことが
判明した。

**新仮説**: Multiple hypotheses can be generated, but explicit rejection
reasoning may be weak, especially when actor-specific behavioral constraints
are required.(複数仮説の生成自体はできるが、明示的な棄却理由の組み立てが
弱い可能性がある。特にActor固有の行動的制約(例: トランプの面子維持困難)を
要する場合に顕著。)

**Evidence**: Hungary(「ポピュリズムの終焉ではない」という留保の欠落)、
Iran(棄却仮説自体は生成されたが、棄却理由=面子維持困難が欠落)。

---

## H4｜Investigation Question Reframing

**Status**: strongest retrospective finding

**Evidence**: 4/4 cases、3/3 independent case families。Analysis Designの
`investigation_question`が、4ケース全てで入力テーマの転記のままだった。
**Phase I-B0シリーズ全体で、独立Case Family全てを横断して再現された唯一の
発見。**

---

## H5｜Main Scenario Ranking / Evidence Weighting

**Status**: preliminary

**Evidence**: Iran M086 only(n=1、Selection Logic材料を持つ唯一のケース)。
AI Rank1 != Expert conclusion、Expert conclusionはAI Rank3におおむね一致。

**重要な区別**: 「正しいScenarioを生成できなかった」のではない
(`candidate existed`)。優先順位付け・選定が異なった(`ranking / selection
differed`)。

**Confidence上限**: Retrieval Evidenceがほぼ枯渇した状況下での観測であり、
`possible ranking / selection weakness under sparse evidence`として記録する
(「AI ranking logicの欠陥」と断定しない)。

**分解(評価仮説のみ)**:
- **H5a｜Scenario Ranking**: 候補の優先順位付けがExpertと異なる。
- **H5b｜Evidence Weighting**: 各Scenarioを支持・反証するEvidenceの重み付けが弱い。
- **H5c｜Rejection Logic**: 棄却理由を明示的に組み立てられない(H3と関連するが、
  H5cは「Scenario間の相対評価」文脈、H3は「単独のニュアンス生成」文脈)。
- **H5d｜Actor Behavioral Reasoning**: Trumpの面子維持等、actor-specific
  behavioral constraintがScenario選択に反映されない。

**Priority**: Phase I-B1で最優先検証。

---

## H6｜Absence / Anomaly Detection(新規独立仮説)

**Status**: preliminary but high-quality evidence

**Evidence**: US-Chinaケース(n=1 independent family=Family C)で、3/3 CQAS
candidates + Designでabsence signalが完全欠落(自動車・製薬の不在という
実専門家の核心的シグナルに、AIは一切言及しなかった)。

**Important distinction**: これは一般的なCounter-Hypothesis生成の失敗
(H3)ではない。**与えられなかった/欠落した情報を、分析上のシグナルとして
扱うこと自体の失敗**である。H3が「複数の解釈を生成しどれを退けるか」の
問題であるのに対し、H6は「そもそも否定形のシグナルに気づくか」という、
より手前の段階の問題。

**Confidence note**: 他の発見と異なり、Retrieval Evidence枯渇の影響を相対的に
受けにくい(T0入力テキスト自体に手がかりが含まれていたため)——この点で
HIGH confidenceの根拠を持つが、n=1 independent familyであることに変わりはない。

---

## Phase J Experiment Priority(検証優先順位、実装順ではない)

**Tier 1**:
1. Investigation Question Reframing(H4)
2. Scenario Ranking / Evidence Weighting(H5)

**Tier 2**:
3. Absence / Anomaly Detection(H6)
4. Rejection Logic(H3)

**Tier 3**:
5. Actor Expansion(H1)
6. Observable Indicator Reasoning(H2)

**Phase I-B1完了前のproduction変更は禁止**(CQAS prompt/Ranking/Actor
expansion/Reframing prompt/Absence Detection prompt/Scenario selectionの
いずれも)。

## 扱い方針

- Phase Jで実際にCQAS/Prompt/Ranking等へ着手する場合、まずPhase I-B1
  (実専門家レビュー)による裏付けを得てから優先順位を決めること。
- H1〜H6はいずれも「小標本(n=1〜4、Case Family単位では3)での方向性シグナル」
  であり、「Expert AI/CQASの性能はxx%」のような一般化した主張の根拠には
  使わない。
