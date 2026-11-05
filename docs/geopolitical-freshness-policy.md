# 地政学ナレッジ＆インテリジェンスDB — 鮮度・更新履歴ポリシー（Phase 2A）

対象ファイル: `geo_freshness_service.py`

## 1. 方針

鮮度状態は**保存せず、参照時に都度算出する**（`classify_knowledge_freshness`/`classify_source_freshness`はいずれも純粋関数）。理由: 鮮度フラグ自体をバッチ更新で保存すると、そのフラグ自体が古くなる（更新漏れが「今は鮮度良好」という誤った表示を生む）問題があるため。古いKnowledge/Sourceを自動削除する仕組みは持たない。

## 2. Knowledge Itemの鮮度（`classify_knowledge_freshness`）

`geo_knowledge_items`の`status`/`as_of_date`/`next_review_at`/`stale_after_days`（いずれもPhase1で追加済みの列）から算出する。戻り値は次の5値のいずれか。

| status | 判定条件 |
|---|---|
| `superseded` | `item.status == "superseded"` |
| `unknown` | `as_of_date`が無い |
| `review_due` | `next_review_at`が設定済みかつ今日 ≥ `next_review_at` |
| `stale` | `stale_after_days`設定済みかつ`(today - as_of_date).days > stale_after_days` |
| `current` | 上記いずれにも該当しない |

判定の優先順位は上表の順（`superseded`が最優先）。

## 3. Sourceの鮮度（`classify_source_freshness`）

`geo_source_registry`の`update_cadence`/`last_successful_ingestion_at`/`failure_count`（Phase2Aで`geo_connector_base.UPDATE_CADENCE_DAYS`に対応する既存列＋今回追加の`failure_count`）から算出する。

| status | 判定条件 |
|---|---|
| `unknown` | `last_successful_ingestion_at`が未設定（一度も成功していない） |
| `failing` | `failure_count >= 3` |
| `overdue` | 前回成功からの経過日数 > `update_cadence`が示す推奨間隔 |
| `review_due_soon` | 経過日数が推奨間隔の80%を超えている |
| `current` | 上記いずれにも該当しない |

`update_cadence`の推奨間隔（`geo_connector_base.UPDATE_CADENCE_DAYS`）: daily=1日, weekly=7日, monthly=30日, quarterly=91日, annual=365日（未設定時は30日を既定値とする）。

## 4. Ingestion Runとの関係

各Connector実行完了時（`geo_connector_base.complete_ingestion_run`呼び出し後、Connector側で）、`geo_source_registry.last_successful_ingestion_at`/`last_failed_ingestion_at`/`failure_count`を更新する。`failure_count`は成功時に0へリセットし、失敗時にインクリメントする（`geo_connector_world_bank.py`/`geo_connector_ofac.py`参照）。

## 5. API

| メソッド/パス | 内容 |
|---|---|
| `GET /api/geo/knowledge/freshness/knowledge-items/{id}` | 単一Knowledge Itemの鮮度分類 |
| `GET /api/geo/knowledge/freshness/sources/{id}` | 単一Sourceの鮮度分類（`days_since_last_success`/`failure_count`含む） |

現時点では一覧画面への鮮度バッジ表示は未実装（個別APIのみ）。Phase 2B候補として、Knowledge Search/Sources & Documents画面への鮮度バッジ一覧表示を残す。

## 6. テスト

`tests/test_geo_phase2a.py::test_classify_knowledge_freshness_stale_and_current`、`::test_classify_source_freshness_overdue_and_failing`。
