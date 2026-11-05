# 地政学ナレッジ＆インテリジェンスDB — Indicator Monitor（Phase 2B）

## 1. 前提: 「indicator」という語が指す3つの独立した概念

このリポジトリには「indicator」を指す概念が3つ存在し、互いに独立している。混同しないこと。

| 概念 | テーブル | 用途 |
|---|---|---|
| (a) Signal Engine | `geo_indicator_definitions`/`geo_observations`（`geo_indicator_engine.py`） | 台湾MVP向け決定的計算指標。**Indicator Monitorはこれを指す** |
| (b) Reusable Knowledgeカード | `geo_knowledge_items`（`knowledge_type='indicator'`） | 自由記述のIndicatorカード（Phase1） |
| (c) 数値時系列 | `geo_metric_definitions`/`geo_metric_observations`（Phase2A） | World Bank/IMF/UN Comtrade/WTO/Manifesto等の外部統計 |

Indicator Monitor機能（本ドキュメント）が対象とするのは(a)のみ。

## 2. 閾値超過アラート（`geo_indicator_alerts`）

### 2.1 既存の「危険度色を使わない」方針との関係

`geo-indicator-status.tsx`には次の既存方針コメントがある。

> 指標の状態は「データ品質」（未取得/モック/実データ）のみを色で示す。指標の閾値は「案・校正前」（`geo_indicator_rule_versions.is_draft=true`）であり、LLM/コードが重大性を判定しているわけではないため、危険度を示すred/amber/green等の判定色は使わない（指示書の絶対原則3・7.1に基づく）。

Phase2Bで追加した閾値超過アラートはこの方針と矛盾しない。理由:

- Alertは`rule_definition.thresholds`に**人が明示的に設定した**閾値との比較結果であり、LLMの推測でも自動推論でもない（他のObservation計算方式と同じく、決定的にコードで判定する）
- **既存のIndicatorには影響しない**: `thresholds`未設定のIndicator（現状すべて）は`evaluate_threshold`が常に`None`を返すため、Alertは一切発生しない。閾値を追加するかどうかは完全に人の選択
- UI上は既存の`IndicatorStatusDot`（データ品質ドット）とは別の、明示的にラベル付けされた`IndicatorAlertBadge`として表示し、「人が設定した閾値との比較によるアラートであり、自動リスク判定ではない」ことをtitle属性で明記する（視覚的にも別コンポーネント・別色系統）

### 2.2 閾値の形式（direction/operator対応）

```json
{
  "thresholds": [
    {"severity": "critical", "compare_field": "raw_value", "operator": "above", "value": 100},
    {"severity": "warning", "compare_field": "deviation", "operator": "below", "value": -20}
  ]
}
```

`operator`は`above`/`below`/`gte`/`lte`の4種。`geo_indicator_engine.evaluate_threshold(observation, rule_definition)`がcriticalから順に評価し、最初に一致したseverityを返す（未定義・`insufficient_data`なら`None`）。

### 2.3 Observation単位での冪等性

`geo_indicator_alerts.observation_id`に**unique制約**があり、`create_alert_if_needed`は事前にexists検証を行う。同一Observationに対して`POST /api/geo/indicators/{id}/recalculate`を複数回実行してもAlertは重複作成されない（DB制約とアプリ側チェックの二重保証）。

## 3. Indicator Freshness

`geo_freshness_service.classify_indicator_freshness(indicator, latest_observation, *, now=None)`は、`classify_knowledge_freshness`/`classify_source_freshness`とは**独立した第3の関数**として実装した（意図的に使い回さない、Phase2A/2Bで別々に追加された概念のため）。

`geo_indicator_definitions.expected_update_cadence`（daily/weekly/monthly/quarterly/annual、`geo_connector_base.UPDATE_CADENCE_DAYS`と同じ語彙）を基準に、最新Observationの`as_of`からの経過日数で`current`/`review_due_soon`/`overdue`/`unknown`を判定する。`expected_update_cadence`未設定または`latest_observation`が無い場合は`unknown`。

## 4. Indicator Monitor画面

新規route `geo.indicator-monitor.tsx`（サイドバー「Indicator Monitor」から遷移）。全テーマ横断で`mvp_enabled=true`のIndicatorを一覧表示し、各行に最新値・乖離（`DeviationIndicator`、既存コンポーネント再利用）・鮮度バッジ・直近Alertバッジを表示する。

バックエンドは`GET /api/geo/indicators/monitor`（`api_server.py`）を1本追加しただけで、新しい計算ロジックは持たない。既存のIndicator一覧取得・最新Observation取得・`geo_indicator_alerts`取得をオーケストレーションするのみ。

個別Indicator詳細画面（`geo.theme.$themeId.indicator.$indicatorId.tsx`）にも同じ`useGeoIndicatorMonitor()`を再利用し、ヘッダー付近に鮮度バッジ・Alertバッジを追加した（専用のsingle-indicator向けエンドポイントは新設していない）。

## 5. データモデル

`sql/2026-08-06_geo_phase2b_indicator_alerts.sql`:
- `geo_indicator_alerts`（新規）: `alert_id, indicator_id, rule_version_id, observation_id(unique), severity(warning/critical), triggered_at, acknowledged_at, acknowledged_by`
- `geo_indicator_definitions`へ`expected_update_cadence`列を追加（ALTER、既存データ非破壊）

## 6. 既知の制約・スコープ外

- Alertの既読(`acknowledged_at`)を設定するUI操作は未実装（列は用意済み、DB直接操作またはAPI追加はPhase2B次ラウンド）
- 通知（Slack/Teams/メール等）連携は無い。画面を開いた時にのみ可視化される
- 鮮度バッジはIndicator（(a)）のみ対象。Knowledge Item/Sourceの鮮度は`docs/geopolitical-freshness-policy.md`（Phase2A、既存の個別ID参照APIのみ、一覧画面へのバッジ表示は未実装のまま）
