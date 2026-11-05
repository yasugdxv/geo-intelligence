# Report Studio: Expert Session V1 Freeze Manifest

- **Tag**: `REPORT_STUDIO_EXPERT_SESSION_V1`（commit `ff5bc7a`）
- **前身checkpoint**: `READING_STUDIO_CREATE_V1`（commit `dad8a08`、保持済み）
- **日付**: 2026-09-25
- **性質**: 次回Expert Session向けの安定版Freeze。Report Studio全体の最終Freezeではない。Expert AI Core（geo_expert_engine.py / geo_expert_schema.py）・CQAS・S Ranking・Reading Exposure選定ロジック・Actor Intelligence・Retrievalには一切変更なし。

## 対象範囲

### CREATE MODE（`READING_STUDIO_CREATE_V1`由来、本Freezeに含む）
- ページ上部: Context Label / Title / Current Situation / Question
- Analysis Brief（最大3件の番号付きポイント）
- Reading Overview（俯瞰リスト）
- Reading Card刷新: 意味内容ベースの新Tag（王道/有力な別解/前提を疑う/構造から読む/ジャンプ視点）、human-readable title/thesis、Narrative（WHO→OBJECTIVE→ACTION→INTERACTION→CURRENT_SITUATION→DIRECTION）、3-step Reasoning、Actor Reaction Chain、根拠・反証条件アコーディオン
- Presentation Transformation Layer（`geo_ios_reading_presentation_transform_service.py`、Expert AI Coreとは別立ての薄いLLM呼び出し、既存構造化fieldのみ再編集、cache付き）
- Generate More Readings（10方向+自由入力、既存Reading Exposure Composerの`generate_directed_exploration_reading`を再利用）

### REVIEW MODE（本Freezeで新規追加）
- Report Studio 2入口: 「新しく分析する」/「既存レポートをレビューする」
- Step1 Existing Report Import（PDF/Word/テキスト貼り付け、`geo_documents_store.extract_text`再利用、PowerPoint未対応）
- Existing Reading Reconstruction（Primary Reading/Key Actors/Actor Objectives/Relationships/Cue/Assumptions/Causal Logic/Direction/Scenario/Evidenceを、レポートに明示された範囲でのみ抽出、`not_explicit_fields`で不足を正直に記録）
- Current Reading(Reading A)+Alternative Reading(B以降)を既存`geo_ios_scenario_candidates`へ相乗りさせ、CREATE MODEと同じReading Studio（同一Reading Card component）で表示
- Revision Strategy（維持して補強/変更/統合）選択
- Revise flow（既存Draft versioningへ相乗り）、Original/Revised/Compareタブ
- Generate More Readings のREVIEW MODE対応
- Resume対応（session再訪時、取込済みレポート・再構成Reading・生成済みAlternative・選択・修正方針・Revised Draftすべて維持）

### Draft / AI Review / Expert Review / Approval
本Freezeでは無変更。既存の`geo_ios_report_drafts`（immutable versioning）・Review Cycle・Approval Eventインフラをそのまま再利用（CREATE/REVIEW両モード共通）。

## E2Eテスト結果（実DB・実LLM、2026-09-25実施）

| ケース | 内容 | 結果 |
|---|---|---|
| Migration | mode列/geo_ios_review_imports table | PASS |
| CASE A | Replace戦略 | PASS |
| CASE B | Keep/Reinforce戦略 | PASS |
| CASE C | Integrate戦略（Resume経由でも検証） | PASS |
| CASE D | Generate More Readings | PASS |
| Resume | セッション離脱→再訪 | PASS |
| CREATE MODE Regression | 既存セッションでの選択・生成 | PASS |
| Semantic Compare安全性修正後の目視確認 | Replace/Keep/Integrate 3件 | PASS |

## Test counts

- geo_ios pytest: 386 passed（Freeze前後で変化なし、REVIEW MODE追加分の新規ユニットテストは今回未追加——E2Eスクリプトでの実DB検証のみ）
- TypeScript: 0 errors
- ESLint: 0 errors（pre-existing・無関係な`react-refresh`警告1件を除く）

## Known Gaps

- **PowerPoint未対応**: `.pptx`/`.ppt`は`geo_documents_store.extract_text`が未対応（`python-pptx`等の新規依存が必要、install前確認が必要なため今回は見送り）。
- **Compareはsemantic diffではなくsafe simplified comparison**: revision戦略の指示文から確実に判定できる範囲でのみchanged/unchangedを断定し、それ以外は「変更の可能性あり（要確認）」と表示する3状態モデル。新しいLLM semantic diff呼び出しは追加していない。
- **Supporting Analysis / Watchpoint linkage未実装**: 周辺分析・次に見るものとReadingの紐付けは、REVIEW MODEでは未対応（§14-16スコープ外のまま）。
- **Review Mode Chat context extension未実装**: REVIEW MODEではChat（この局面についてさらに話す）自体を表示しない。
- REVIEW MODE固有の新規pytestユニットテストは未追加（実DB・実LLM E2Eスクリプトでの検証のみ）。

## 次フェーズ

新機能追加はSTOP。Expert Session準備へ移行:
1. 過去モデルケース
2. Fresh Case 2件
3. Report Studio demo
4. Existing Report Review demo
5. Learning Workbench demo
6. Expert AI + Platform提案資料
