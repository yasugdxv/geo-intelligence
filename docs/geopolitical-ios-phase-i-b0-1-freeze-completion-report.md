# Geopolitical Intelligence OS — Phase I-B0.1 Freeze 完了報告

日付: 2026-09-16

## 1. Freeze Commit

`git diff dd1f918`で正確に13ファイルへ特定できる形でcommit済み(並行Report Studio
作業には一切触れていない、内容grep済み)。

- commit: `99bc9dd` — "Phase I-B0/I-B0.1: Retrospective Proxy Evaluation + As-Of
  Evaluation Harness"
- 対象: `AsOfEvaluationClient`、Leakage Audit、Hungary/BC Strategy Regression
  Artifact、cross-case evaluation、I-B1 package spec(初版)
- **Phase JのBefore baselineとしてこのcommitを使用する。**
- CQAS/Expert AI/Report Studio本体は無変更(commit差分に該当ファイルなし)。

## 2. I-B1 Package Readiness

`eval_harness/evaluation_snapshot.py`を実装・スモークテスト済み(Hungaryセッション
で実行、`content_sha256`によるIntegrity Check成功、既存ファイルへの上書き拒否
=Immutability Guaranteeも確認済み)。

Freeze対象7カテゴリ(Theme/All CQAS Candidates/Candidate Rank/Displayed Top5/
Selected Scenario/Analysis Design+Design Versions/Draft/AI Review Findings)に
加え、Knowledge/Prompt/Model VersionsとGeneration Timestampsも自動収集する設計。
本番Report Workflow(`geo_ios_*`のservice/APIレイヤー)は一切変更していない
——読み取り専用のserialize処理のみ。

**実データでのFreeze(実際の新規テーマ)はまだ実施していない**——実Expert Session
の機会が確保された時点で着手する。

## 3. Evaluation Snapshot Design

`eval_harness/evaluation_snapshot.py`参照。Immutability(上書き拒否)+
Integrity(SHA256ハッシュ)の2重の保護を実装。

## 4. Evaluation Taxonomy

`eval_harness/i_b1_evaluation_taxonomy.json`に12ラベルを確定(既存4種+新設8種、
詳細は当該ファイル参照)。production logicへは未フィードバック。

## 5. Additional Retrospective Corpus Audit(候補報告のみ、未実行)

Downloadsの実資料パッケージに残る、Hungary/BC Strategy以外の2ケースを再監査した。

| 候補 | 分類 | 理由 |
|---|---|---|
| **Iran/ホルムズ海峡情勢**(継続的、2026年2〜5月、M0xx実メール17件・benchmark_strength=STRONG) | **eligible_with_limitations** | 4ケース中最も強い実資料(唯一Tier3 Selection Logicへ部分到達、M086に棄却理由付きの候補検討が直接記録)。ただし: (a)単一の明確なT0が無い(継続的局面、複数のM0xx日付が候補) — T0=2026-05-14(M086)を推奨、Selection Logic材料を最大限活用できるため。(b)2026-08月付のイラン関連文書がDBに実在することを既に確認済み(本Phaseの調査で発見)——AsOfEvaluationClientの使用が必須、素のままの実行は不可。(c)BC Strategyと主題が重複(どちらもイラン攻撃切迫を扱う)——「Hungaryとは異なる分析タイプ」という条件は満たすが、「BC Strategyとの差別化」は限定的。(d)T0=2026-02〜03台だと証拠枯渇(BC Strategyと同じ現象)のリスクがあり、T0選択がEvidence充足度を大きく左右する |
| **米中関係/米中首脳会談**(2026-04-06 Hass講演、M016=2026-05-12「不在のシグナル」) | **eligible_with_limitations** | Hungary/BC Strategyいずれとも異なる分析タイプ(二国間サミット力学・「何が無いか」に着目するAnomaly検出)。Actor/Counterparty材料は4ケース中最も豊富。ただし: (a)Before/After diff自体に江口さん独自の枠付け追加文が無く、Initial Cue/Framing検証には不向き(M016単体はTier1級だが範囲が狭い)。(b)DB汚染状況は今回未確認(BC Strategyで実施したような"China"/"Trump visit"/"Ryan Hass"キーワード検索を、この監査では実行していない——実行禁止の指示に従ったため)。(c)T0候補(2026-05-12)は実際のサミット(5/14-15)の直前で、時間的余裕が非常に狭い |
| Phase R1 Blind/Human Checkケース(`r1-blind-cases`/`r1-human-check-cases`) | **insufficient**(再確認) | Corpus Audit時点で確認済みの通り、実専門家判断が一切記録されていない(AI生成の質問文＋AI自身の回答のみ) |
| Zelenskyy Expert Review Pack(`docs/expert-review/`) | **insufficient**(再確認) | `03_expert_review_form.md`は空欄のテンプレートのまま、実施記録なし |

**実行はしていない。** 次にRetrospective Corpusを追加する場合、Iran(T0=2026-05-14
推奨)が最有力候補。

## 6. Phase J Hypothesis Register

`docs/geopolitical-ios-phase-j-hypothesis-register.md`にH1〜H4を記録した
(Actor Selection、Indicator Reasoning、Counter-Hypothesis、Investigation
Question Reframing)。**コード変更の根拠には使っていない。**

## 7. Temporal Entity State Issue

`docs/geopolitical-temporal-entity-state-versioning-design-note.md`に、
entity属性・actor role/affiliation・current posture・historical validity・
source provenanceの5項目について、現状の制約と将来必要になりうる履歴構造を
整理した(実装は一切行っていない)。

## Remaining Limitations

- Phase I-B0/I-B0.1はn=2、Phase I-B正式完了ではない(expert_unverified継続)。
- Actor/Media Intelligence Pack Builderの全経路の個別追跡は未完了(共通
  `client.select()`パターンから推定した蓋然性に留まる)。
- 追加Retrospectiveケース(Iran/米中関係)はいずれも監査のみで実行しておらず、
  DB汚染状況の完全確認も一部未了(米中関係)。
- Evaluation Snapshotツールは実データでのFreeze運用実績がまだ無い(スモーク
  テストのみ)。
- Phase Jへの着手判断は、実専門家レビュー(Phase I-B1)の機会確保、または
  上記Iranケースでの追加Retrospective実施のいずれかを経てから行うことを推奨する。
