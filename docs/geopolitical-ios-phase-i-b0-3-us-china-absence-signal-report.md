# Geopolitical Intelligence OS — Phase I-B0.3: US-China Summit / Absence Signal Retrospective 完了報告

日付: 2026-09-16

## 1. Iran Evidence Commit Hash

`af94697`(Phase I-B0.2: Iran/Hormuz Retrospective Evaluation Evidence)。
既存baseline: `99bc9dd`(system-under-test)、`649625d`(evaluation-harness)。
本Phase(I-B0.3)の成果物はまだ未commit(本報告後、確定依頼を待つ)。

## 2. H5 Decomposition

`docs/geopolitical-ios-phase-j-hypothesis-register.md`にH5を確定・分解済み:

- **H5(親)**: Main Scenario Ranking Divergence — Status: preliminary。
  Evidence: AI Rank1 != Expert conclusion、Expert conclusionはAI Rank3に
  おおむね一致。**「正しいScenarioを生成できなかった」ではなく「candidate
  existedだがranking/selectionが異なった」**、と明記。
- **H5a** Scenario Ranking(候補の優先順位付けがExpertと異なる)
- **H5b** Evidence Weighting(Evidence重み付けが弱い)
- **H5c** Rejection Logic(棄却理由を明示的に組み立てられない、H3とは文脈が異なる)
- **H5d** Actor Behavioral Reasoning(Trump face-saving等のactor-specific
  behavioral constraintが未反映)

Iran固有のLimitationとして、証拠枯渇下での観測であることを明記し、
`possible ranking/selection weakness under sparse evidence`として記録した
(「AI ranking logicの欠陥」とは断定していない)。

## 3. US-China T0 Rationale

T0=2026-05-12(M016、訪中団メンバーリスト判明の瞬間)を採用。T0入力には
**客観的に確認済みの同行者名(Tim Cook/Larry Fink/Schwarzman)のみ**を記載し、
江口さん独自の解釈(「自動車・製薬の不在自体がメッセージ」)は一切含めなかった。
米中首脳会談の予定時期(5月中旬)は「既に公表済みの予定」という客観的事実として
含めたが、会談の結果・展開には一切触れていない。日付の後付け選定は行っていない
(Leakage Auditを先に実施し、PASSを確認してからT0を確定)。

## 4. Leakage Audit

`eval_harness/leakage_audit_us_china.py`実施(China/Trump/Xi/summit/visit/Hass
の6キーワード、geo_documents・geo_events・geo_knowledge_items全て対象)。
**PASS**。Trump/Xiの`geo_knowledge_items`(Actor Profile)が2026年8月末〜9月初旬
付で複数存在することを確認したが、Proxy経由では全て正しく除外された。

## 5. Evidence Availability

`geo_documents`: included=0, excluded_future=4。`geo_events`: 0/0。
Iran/BC Strategyと同様の証拠枯渇。ただし**本ケースの核心的Test(Absence Signal
検出)はT0入力テキスト自体に手がかりが含まれているため、この証拠枯渇の影響を
相対的に受けにくい**(§7参照)。

## 6. CQAS Candidate Coverage

3候補生成、全件Top5扱い。Rank1(選択済み)=テック・資本市場再調整の実質的関与、
Rank2=象徴的・演出的訪問、Rank3=中国側の制裁緩和狙い(米議会の抵抗で限定的)。

## 7. Absence Signal Result(★最重要発見)

**3候補全て・Design全てにおいて、「どの業界が不在か」への言及は一切無し**
(D. missing、confidence: **HIGH**——他の発見と異なり証拠枯渇の影響を受けにくい
ため)。全ての解釈が「誰が同行しているか」という肯定的情報のみから組み立てられ、
実専門家の分析の核心だった否定形のシグナル(自動車・製薬の不在)への言及は
候補・Design双方に一つも無かった。**これまでの4ケース中、最もクリーンかつ
断定的な発見。**

## 8. Actor Result

トランプは個人名で明示。**習近平は一度も個人名で登場せず**「中国指導部」という
一般表現のみ(トランプとの非対称性)。訪中団企業トップ3名(T0入力で直接与えた
客観的事実)はAIも正しく引き継いだが、**個別の意思決定変数としてではなく
一つの集合的ラベルとして扱われている**(各社が代表する利害の違いへの言及なし)。

## 9. Reframing Result

`investigation_question`は入力テーマの転記のまま(A. input restatement)。
4ケース連続。

## 10. Counter-Hypothesis Result

**この観点ではむしろ強みを確認**: 3候補は「実質的関与」「象徴的演出」
「制裁緩和狙い」という質的に異なる3つの解釈軸を生成しており、単純な
「Counter-Hypothesis生成が弱い」という一般化は誤りだったことが判明。弱いのは
多仮説生成そのものではなく、Absence-basedな解釈軸が選択肢に含まれない点。

## 11. H1-H5 Update

- H1(Actor Selection): **inconclusive**(習近平の非対称欠落を確認、ただし
  Trump/3企業トップは適切)
- H2(Indicator Reasoning): **not_applicable**(本ケースの核心Testと無関係)
- H3(Counter-Hypothesis/Rejection Logic): **does_not_support**——US-Chinaでは
  むしろ良好な多仮説生成を確認、H3の当初の一般化(「多仮説生成が弱い」)を
  修正する必要があることが判明(狭義には「棄却理由の組み立て」が弱いのであって
  「複数解釈の生成」自体は弱くない)
- H4(Investigation Question Reframing): **supports**(4/4ケース、3/3 Family)
- H5(Main Scenario Ranking Divergence): **not_applicable**(本ケースには
  対応するSelection Logic材料が無い)

## 12. Cross-Family Comparison

| Family | ケース | 独立性 |
|---|---|---|
| A | Hungary | 独立 |
| B | BC Strategy + Iran | 同一局面、内部2ケースは独立再現として数えない |
| C | US-China | 独立 |

**3 Family中、全てを横断して再現された発見は1つのみ**: **H4(Investigation
Question Reframing、4/4ケース・3/3 Family)**。これがこのPhase I-B0シリーズ
全体で最も確度の高い発見。他の発見(Actor Selection、専門家独自視点の欠落)は
3 Family全てで何らかの形は見られるものの、深刻度・形が一貫しないため、単一の
メカニズムと断定できない。

## 13. Phase J Experiment Candidates

`docs/geopolitical-ios-phase-j-experiment-candidates.md`に6項目
(Actor Expansion/Indicator Reasoning/Absence Detection/Counter-Hypothesis
Rejection Logic/Scenario Ranking Evidence Weighting/Investigation Question
Reframing)を整理。各項目にobserved evidence/case families/confidence/
proposed evaluation/expected failure modeを記載。**最優先2項目**: Investigation
Question Reframing(最も確度が高い)とScenario Ranking(実務インパクトが
最も大きい可能性)。

## 14. Regression Artifact

`docs/geopolitical-ios-phase-i-b-evidence/phase_ib0_regression_artifact.json`
へUS-Chinaケースを追加、Family C定義・最終Cross-Family Summaryも追加済み。
個別ファイル: `eval_harness/phase_ib0_3_us_china_result.json` /
`_evaluation.json`、`eval_harness/leakage_audit_us_china_result.json`。

## 15. I-B1 Readiness

Production-ready状態を維持。実データでのFreezeは未実施のまま。

## 16. Remaining Limitations

- 4ケース・3 Case Family(独立2+関連2)という小標本、一般化不可。
- BC Strategy/Iranは同一Family内であり、独立再現として過大評価していない。
- 全ケースで証拠枯渇が繰り返し観測された(このDatasetの構造的特徴、T0選択の
  巧拙の問題ではない)——ただしUS-Chinaの核心発見(Absence Signal)はこの制約の
  影響を相対的に受けにくいため、他の発見よりconfidenceが高い。
- T0入力のBlinding完全性は技術的に保証されていない(Corpus Audit時に私自身が
  資料を読んでいるため)——M016の解釈は使わず客観的事実のみで構成する、という
  方法での緩和に留まる。
- **Phase Jへはまだ進まない**。実装着手条件(Phase I-B1、または異なるCase
  Familyでの追加独立Evidence)は引き続き未達。
