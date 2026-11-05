# 地政学ナレッジ＆インテリジェンスDB — Network Graph（Relationship Explorer、Phase 2B）

## 1. 目的

Issue単位でEntity（国・組織・人物等）間のRelationshipをグラフとして可視化する。既存の`geo_relationship_service.list_relationships_for_entity`/`list_relationships_for_issue`はテキスト/一覧向けであり、ノード-エッジ型の可視化画面は存在しなかった（Phase1では`useGeoKnowledgeRelationships`フックのみ先行定義されていたが、使用するUIは無かった）。

## 2. データ取得: 巨大なPostgREST `in.()`を避ける

`world_monitor_dashboard`の`fetch_engagement_map`で実際に発生した「737件超のarticle_idを1つの巨大な`in.()`フィルタに詰め込んだ結果、PostgRESTがURL長超過で400エラーを返し、それが握りつぶされて件数0件表示になっていた」バグ（このセッション冒頭で発見・修正済み）と同じ落とし穴を、Entity一括取得で再現しないよう設計した。

Relationship→Entityの結合をアプリケーション側の2段階クエリ（Relationship取得→関連Entity ID一覧→Entity一括取得）にすると、Entity数が多いIssueで同じ問題が起きうる。そこで**DB側RPC関数1回**で結合を完結させる（Phase2Aの`search_geo_*`と同じ設計方針）。

```sql
-- sql/2026-08-06_geo_phase2b_relationship_graph.sql
create or replace function public.get_relationship_graph_for_issue(
    p_issue_id uuid, p_include_demo boolean default false
) returns jsonb ...
```

`geo_relationship_service.get_relationship_graph_for_issue(client, issue_id, include_demo=False)`は`client.rpc(...)`を呼ぶだけの薄いラッパー。`geo_knowledge_api.py`の`GET /api/geo/issues/{issue_id}/relationship-graph`がこれを公開する。

## 3. ノード色の設計（dataviz skillに基づく）

`geo_entities.entity_type`は24種類、`geo_relationships.relationship_domain`は13種類あり、いずれもcategorical色として使うには種類が多すぎる（実務上の上限は7〜8種）。

- **ノード色**は`geo_entity_service.ENTITY_TYPE_DISPLAY_CATEGORY`（Python）／`ENTITY_TYPE_DISPLAY_CATEGORY`（`geo-relationships-tab.tsx`、TS側で重複管理）で5グループに畳み込む: `state`（国家・政府）/`political_social`（政治・社会主体）/`security`（軍事・治安）/`economic`（経済主体）/`geography_asset`（地理・インフラ資産）。詳細区分（24種）はノードラベル・ホバー時ツールチップで表現する
- **エッジ**は色分けせず、relationship_domain（13種）ごとのチェックボックスフィルタで絞り込む
- ダークモード対応（`prefers-color-scheme`に応じて2色パレットを切り替え）、ホバー時ツールチップ、**テーブルビューへの切り替え**を実装（canvas描画の代替手段、アクセシビリティ対応）

## 4. ライブラリ選定

`eco-digest-spark`には元々recharts(^2.15.4)のみ導入済みで、ノード-エッジ型グラフ専用ライブラリは無かった。**`react-force-graph-2d`をユーザー承認のうえnpm installした**（canvas描画、force-directed layout標準搭載、数百ノードでも軽快）。候補として検討した`@xyflow/react`（DOM要素でアクセシビリティは有利だが自動レイアウト別途要）、`vis-network`（保守がやや停滞気味）は不採用。

## 5. 画面構成

新規route作成ではなく、既存`geo.theme.$themeId.tsx`の`Tabs`（overview/documents）に3つ目のTab「relationships」を追加し、`components/geo/geo-relationships-tab.tsx`（issueセレクタ→ドメインフィルタ→グラフ/テーブル切替）を新設した。

## 6. 既知の制約・スコープ外

- Entity/Relationshipの現状データ量はほぼ空（`geo_theme_seed.py`はIndicatorのみseedし、Entity/Relationshipは手動/Case作業時に個別作成される想定）。大規模グラフでのパフォーマンス検証は未実施
- グラフレイアウトの保存・共有機能は無い（毎回force-directedで再計算）
- 複数Issue横断のグラフ表示は無い（1 Issueずつ）
