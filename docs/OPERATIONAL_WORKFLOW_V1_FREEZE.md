# OPERATIONAL_WORKFLOW_V1 Freeze Manifest

| 項目 | 内容 |
|---|---|
| Freeze名 | **OPERATIONAL_WORKFLOW_V1**（Phase C: Operational Workflow / Pipeline Health） |
| Freeze日 | 2026-10-06 |
| 承認 | Phase C Closeoutの指示によりFreeze（ユーザー指示、2026-10-06） |
| Snapshot | `freeze/OPERATIONAL_WORKFLOW_V1/`（複製: `docs/OPERATIONAL_WORKFLOW_V1_FREEZE.md`） |
| 検証 | `python freeze/check_all_freezes.py`（全snapshot自身のSHA256照合） |
| SHA256SUMS | 23ファイル、`SHA256SUMS.txt` 自体のSHA256 = `b1685bd4ac95a61874429aa6aff715a44025ede7b99a5ec590af90e72fe7b5ac` |
| DBスキーマ変更 | なし |

## Freeze対象
geo operations summary API（`GET /api/geo/operations/summary`）/ Operations UI（`/geo/operations`）/ Pipeline Health /
Action Queue / Review backlog表示 / Theme sync backlog / individual sync / batch sync / failure・retry / idempotency /
Accept後導線（承認直後の案内。一覧から消えても表示、案内表示時に同期待ちを再取得）/ Theme Discoveryへの導線（0件時の理由表示含む）/
Monitoring Alert統合 / Collection issue表示。

設計の要点: 表示はDBの状態のみでLLMは使わない。Theme同期は既存 `geo_theme_candidate_pipeline_service` を再利用（新規の生成ロジックなし）。
AcceptとTheme生成は別操作（Human Gate維持）。セクション単位で部分障害に耐える。

## Validation
| 項目 | 結果 |
|---|---|
| Python | **2651 PASS**（全件。時刻依存だった既存テストはUTC基準に修正済み＝test hygieneのみ） |
| TypeScript | PASS |
| Frontend Build | PASS |
| 実DB / Browser E2E | PASS（`verification/real_db_e2e_results.md`） |
| READING_UI_V1 Gold 4 | SHA256一致（`verification/test_results/reading_ui_v1_gold_check.json`） |

## Freeze表現
- **Frozen snapshot unchanged**（各snapshotがFreeze時のSHA256と一致）。
- **Current working tree contains later Phase C / Phase D changes**（現行ソースがsnapshotより先に進むこと自体はRegression failureではない）。

## Known limitations（Freeze blockerではない）
`verification/known_limitations.md` を参照: 実LLM failureのbrowser再現なし／no_candidateの実DB再現なし／Review backlog 49件／
Production Scheduler未設定（Phase Dで対応）／Operations画面の更新約5秒／Theme生成タイトルの飛躍（既存ロジック）。

## 既存Freezeとの関係
`freeze/READING_UI_V1/`、`freeze/SOURCE_INTELLIGENCE_V1/`、`freeze/SOURCE_INTELLIGENCE_V1_1/` は変更していない。
