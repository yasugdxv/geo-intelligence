# 検証計画（オフライン／実LLM／実DB／人手意味レビューの区別）

作成日: 2026-09-06
本文書はテスト設計であり、実LLM・実DB呼び出しの承認ではない（`04_requirements_and_acceptance.json`の`test_budget_policy`を踏襲）。

## 既存テスト資産（重複させず拡張する対象）

| 既存テストファイル | カバー範囲 |
|---|---|
| `tests/test_geo_expert_engine.py` | Stage1/Stage2オーケストレーション |
| `tests/test_geo_expert_quality_guard.py` | 決定的post-processing群 |
| `tests/test_geo_expert_review.py` | Review Delta/Promote |
| `tests/test_geo_expert_research_service.py` | Research Sufficiency/External Research |
| `tests/test_geo_expert_staged_execution.py` | execution_mode切替 |
| `tests/test_geo_expert_depth_grounding_upgrade.py` | Evidence Weighting/Historical分離 |
| `tests/test_geo_expert_v1_2_cases.py` / `_repair.py` | 既存Case回帰、Repair経路 |

新規テストはこれらのファイルへの追加を基本とし、新規テストファイルの作成は明確に別モジュールを追加する場合(例: DirectionalPerceptionの新スキーマ)に限定する。

## クラス1: オフライン（deterministic_unit / integration、実LLM不要）

Tier1/2の各追加フィールドについて、以下を`geo_expert_quality_guard.py`または`geo_expert_schema.py`のユニットテストとして追加する。

| test_id (04より) | 検証内容 | 対応Tier |
|---|---|---|
| T02 | 旧payload(新フィールドなし)を読込・出力してもrepair扱いにならない | 全Tier共通 |
| T03/T04 | DirectionalPerception追加後、A→B認識がB→Aへ反転しないこと、モックデータでunit検証 | 1-1 |
| T20/T21 | 既存Temporal分離ロジックの回帰（変更しないため既存テストのまま） | 変更なし確認 |
| T24 | active/available/ACLフィルターが新フィールド追加後も迂回されない | 1-1, 1-3 |
| T32 | 新フィールドに不存在ID・他案件IDを混入させた際の検出 | 1-1, 2-1 |
| T33 | feature flag OFF時、既存Quick/Standard経路が無変更で動作する回帰 | 全Tier共通、最優先 |

これらは`docs/expert-ai-eguchi-phase1-audit/`配下ではなく既存`tests/`配下に実装する（監査文書自体はコードではないため）。

## クラス2: 実LLM・実DB（承認後、対象環境・予算・入力snapshotが明示された場合のみ）

現時点でNOT RUN。以下は承認され次第の実行候補であり、今回は実行しない。

- Tier1実装後、既存5-case Regression（Trump-Xi/Putin/Netanyahu/MBS/Kim）で既存のCallBudget・Guard結果に変化がないことを確認（凍結Baselineの上書きはしない、新規Runとして別ディレクトリに保存）。
- Tier1のDirectionalPerception追加が既存Actor Intelligence Retrieval・Media Intelligenceの出力を変化させないことを確認（新フィールドが空でも既存フィールドが劣化しないこと）。

## クラス3: 人手意味レビュー（semantic_review）

構造チェック（Guard PASS）だけで完了と呼ばない、という04のgate_policyに従い、以下は人手レビュー対象として明示的に区別する。

| test_id | 内容 |
|---|---|
| T05/T06/T16/T19/T25/T26/T27/T30/T31 | Prompt強化・Tier1-2追加後の局面理解・Actor Flow・候補・沈黙解釈の質を、原文照合で評価。単一Run成功を安定再現と呼ばない。 |
| T34 | 旧版(現行)/新版(Tier1-2実装後)比較。局面・根拠・候補・着地の質とばらつきを評価。 |

## Tier 0関連の検証

- 0-1 (CallBudget統合)を実施する場合: 統合前後でtotal call数・token数のログ差分を実測し、既存の「追加Azure Callなし」という制約が壊れていないことを確認。
- 0-2 (Report Studio承認ゲート)を実施する場合: `needs_human_review`判定発生時に実際に`promote_synthesis`がブロックされることをunit test化(`tests/test_geo_expert_review.py`または新規`tests/test_geo_report_studio_service.py`)。

## 明示的にNOT RUNとするもの（今回のAUDIT_AND_PLANの範囲）

- 実Azure OpenAI呼び出しを伴う全てのテスト。
- Hormuz/EUDR等の既存凍結Baselineへの再実行・上書き。
- DB migration適用・適用後の実データ確認。
- Session3・Cross-Narrative・UI刷新関連のテスト（対象外のため設計しない）。

## 完了状態の分離

design doc 10.1の3分類（構造検査／意味品質／実環境）に、本リポジトリの既存運用にある「本番承認」を加えた4段階で報告する:
`設計対応済み` → `オフライン動作確認` → `実環境確認` → `意味品質確認` → `本番承認`
各Tier項目がどの段階まで進んだかを、B〜D段階の報告で個別に明記する。
