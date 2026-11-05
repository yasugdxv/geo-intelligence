# Phase I-B1 — Prospective Expert Validation Package 仕様

## 目的

次に実際の専門家(江口さん等)レビューを実施する際、その場で即座に使える
「Freeze済みパッケージ」を用意しておく。Phase I-B0/I-B0.1(Retrospective Proxy)は
「生成完了後に既存資料と突き合わせる」方式だったが、Phase I-B1は逆で、**専門家に
見せる前に全てをFreezeし、専門家の判断が出るまでAI側は一切追加生成・修正をしない**。

## Freezeする内容・実装済みツール(Phase I-B0.1で確定)

`eval_harness/evaluation_snapshot.py`の`build_evaluation_snapshot(client,
analysis_session_id)`が、以下の必須項目を全て自動収集し、1つの自己完結的な
JSONへFreezeする(本番Report Workflow自体には一切書き込まない、read-onlyツール):

```
theme / session_title
all_cqas_candidates（rank・raw込みで全件）
candidate_ranks
displayed_top5_ids
selected_scenario_candidate_id
analysis_design_current + design_versions_history(version履歴全体)
linked_report_project_id + draft_or_report(Report Studio連携時)
ai_review_findings(Review Cycle + Comments、あれば)
knowledge_prompt_model_versions(候補ごとのmodel/prompt_version/cqas_knowledge_version)
generation_timestamps
content_sha256(改竄検知用ハッシュ)
```

`freeze_snapshot_to_file()`は**既存ファイルへの上書きを拒否する**——Expert Review
開始後にSnapshotが書き換えられることを構造的に防ぐ(Immutability Guarantee)。
`verify_snapshot_integrity()`でいつでもハッシュ照合による改竄検知が可能。

使い方: `python eval_harness/evaluation_snapshot.py <analysis_session_id> <out_path.json>`

## Evaluation Taxonomy(v2、Phase I-B0シリーズFreeze時点で確定)

`eval_harness/i_b1_evaluation_taxonomy.json`(v2)に、Expert Review後に使う
必須13ラベルを確定済み: `generation_miss` / `ranking_miss` / `selection_miss` /
`validated_candidate`(既存Phase G CQAS Signal)+ `investigation_question_
reframing`(H4)/ `actor_selection_miss`(H1)/ `observable_indicator_miss`(H2)/
`absence_anomaly_miss`(H6、新規)/ `rejection_logic_miss`(H3、修正版)/
`scenario_ranking_divergence`(H5a)/ `evidence_weighting_gap`(H5b)/
`expert_unique_insight` / `ai_review_miss`。補助ラベルとして`design_miss`/
`draft_reasoning_miss`も維持。`hypothesis_register_mapping`でH1〜H6との対応を
明示。**まだproduction logicへは一切フィードバックしていない——評価専用の
taxonomyとして確定させたのみ。**

### H5専用のReview設計(Scenario Ranking/Evidence Weighting/Rejection Logic/Actor Behavioral Reasoningの分離)

単に「このScenarioは良いか」という漠然とした評価ではなく、AIが提示した
候補セットについて専門家へ明示的に以下を尋ねる設計とする(`eval_harness/
i_b1_evaluation_taxonomy.json`の`review_design_notes.h5_decomposed_questions`
参照):

1. どのScenarioを最も有力と考えるか
2. なぜそれを採用するか
3. なぜ他のScenarioを採用しないか(候補ごとに)
4. 何が変われば順位が変わるか

これにより、H5a(Ranking)/H5b(Evidence Weighting)/H5c(Rejection Logic)/
H5d(Actor Behavioral Reasoning)を候補ごとの別フィールドとして評価できる。

### H6専用のReview設計(Non-Leading Rule、重要)

Expert Reviewの画面・台本は、**「何が欠けているか探してください」という形の
誘導を一切行わない**(`review_design_notes.h6_non_leading_rule`参照)。
「欠落actor/stakeholder/statement/sector/expected action/expected signal」を
専門家が**自発的に**指摘した場合のみ、`absence_anomaly_miss`または
`expert_unique_insight`として記録する。チェックボックス形式の「欠落候補リスト」
のような誘導的UIは使わず、自由記述欄を用意する。

## 専門家へ渡す時の手順(Leakage Prevention継続)

1. 上記7項目をこの順序でFreezeし、**専門家に見せる前に江口さん自身の既存資料・過去の
   実レポートは一切参照させない**(Phase I-B0と同じLeakage Prevention原則を維持)。
2. 専門家には「Theme → CQAS全候補 → Top5 → 選定理由 → Design → Draft → AI Review所見」
   の順に見てもらい、各段階でHungaryケースの`03_expert_review_form.md`と同様の構造化評価
   フォーム(Rating/Expert correction/Reason/Evidence expert would prioritize)を使って
   記入してもらう。
3. 評価後、`eval_harness/i_b1_evaluation_taxonomy.json`の正式ラベル(上記参照)を
   初めて確定できる(Phase I-B0/I-B0.1では`selection_miss`等は評価不可だった)。
   Draft/Report品質評価は既存11軸(Question Fit/Evidence Sufficiency/
   Fact-Inference Separation/Reasoning Strength/Actor Logic/Alternative
   Hypotheses/Scenario Coherence/Timeline/Confidence Calibration/Knowledge
   Gaps/Decision Utility)をそのまま使う。

## Phase I-B0/I-B0.1との違い(重要)

| | Phase I-B0/I-B0.1 | Phase I-B1 |
|---|---|---|
| 評価対象 | 既存の実資料(専門家がAI以前に自力で書いたもの) | AIが実際に生成したCQAS/Design/Draft |
| 開示順序 | AI生成 → 完了後に既存資料開示 | AI生成 → Freeze → 専門家が生のAI出力を直接レビュー |
| selection_miss評価 | 不可(Claudeが操作代行のため正式なUser Selectionではない) | 可能(専門家自身がTop5から選ぶ、またはClaudeの選定を専門家が評価) |
| ラベルの正式性 | proxy/directional evidenceのみ | 正式なPhase I-B完了評価 |

## 現時点のステータス

まだ実施されていない。次回、以下のいずれかの経路で実専門家レビューが確保された時点で、
新規テーマ(またはHungary/BC Strategy/Iran/US-Chinaとは別の、まだAIに一度も
見せていないテーマ)で、以下の順序でFreezeから開始する:

```
Theme
↓
CQAS all candidates
↓
Top5
↓
Selected/Rank1 scenario
↓
Analysis Design
↓
Draft
↓
AI Review
===== IMMUTABLE SNAPSHOT(evaluation_snapshot.py、SHA256付き) =====
↓
Expert Review
```

- Session3型の新規インタビューが実施される
- 既存の実運用フロー(Report Studio/IOS)を実際に業務で使う中で、専門家が実際に
  Review Cycleへコメントを残す

## No Tuning(Phase I-B1完了まで禁止)

CQAS prompt変更 / Ranking変更 / Actor expansion変更 / Reframing prompt追加 /
Absence Detection prompt追加 / Scenario selection変更。**現在のbaseline
(`99bc9dd`)を維持する。**

## Final Status(Phase I-B0シリーズFreeze時点)

| 項目 | 状態 |
|---|---|
| Phase I-B0シリーズ(Retrospective Proxy Evaluation) | **COMPLETE / FROZEN** |
| Phase I-B(正式) | **NOT COMPLETE** |
| Expert AI / CQAS content quality | **expert_unverified** |
| Phase J | **EXPERIMENT PLAN READY / IMPLEMENTATION NOT STARTED** |
| 次の正式Gate | **Phase I-B1 Prospective Expert Validation** |
