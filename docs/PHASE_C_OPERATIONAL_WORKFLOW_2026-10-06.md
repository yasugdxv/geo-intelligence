# Phase C: Operational Workflow / Pipeline Health（2026-10-06）

目的: 収集 → 候補整理 → レビュー → テーマ化 → モニタリング の流れを、日常運用で止めずに回せる状態にする。
新しい分析機能は追加していない。DBマイグレーションなし（SQL変更なし）。

## 調査で分かったこと
- Episode Review → Theme Candidate同期（個別・一括・同期待ち一覧・実行履歴）は既に実装済み
  （`geo_theme_candidate_pipeline_service.py`）。本Phaseはこれを**再利用**し、生成ロジックは新規実装していない。
- 「同期待ち」は `geo_theme_candidates.source_episode_cluster_ids`（jsonb）とcluster_idsの集合の一致で決定論的に判定（LLMなし）。
- 承認済みでも根拠が少ない（3件未満かつ傾向なし）Clusterは同期対象外（異常ではない）。運用画面では別枠で注記。
- 履歴は既存 `geo_ingestion_runs`（connector_type=theme_candidate_sync）を再利用。レビュー履歴は最新のreviewed_by/atのみ。

## 追加・変更
| 区分 | ファイル | 内容 |
|---|---|---|
| 新規 | `geo_operations_service.py` | 運用Summaryの集計（LLMなし、セクション単位で部分障害に耐える） |
| 新規 | `geo_operations_api.py` | `GET /api/geo/operations/summary`（1本で全体を返す） |
| 変更 | `api_server.py` | ルーター登録 |
| 新規 | `eco-digest-spark/src/routes/geo.operations.tsx`、`components/geo/operations-dashboard.tsx`、`lib/geoOperationsApi.ts` | 運用画面（サイドバー「インテリジェンス運用」） |
| 変更 | `components/app-sidebar.tsx`、`lib/i18n.tsx` | メニューと翻訳キー |
| 変更 | `routes/geo.episode-candidates.tsx` | Accept直後の案内が出ない不具合の修正（下記） |
| 変更 | `components/geo/theme-explorer-panel.tsx` | テーマ候補0件の理由（レビュー待ち件数・レビューへの導線）を表示 |
| 変更 | `lib/geoThemeCandidateApi.ts`、`lib/geoEpisodeClusterApi.ts` | 実行後に運用Summaryを再取得 |
| 新規 | `tests/test_geo_operations.py` | 19件 |

## 不具合の修正（Accept直後の案内）
`/geo/episode-candidates` は既定で「candidate」のみ表示するため、Acceptした行が一覧から消え、
「Theme Candidateを生成」の案内（§11）も一緒に消えていた。さらに、ページ表示時に始まった同期待ち取得が
Accept前の古い一覧のまま返り、案内が出ない競合もあった。
→ 今回Acceptしたクラスタの案内は一覧に出ていなくても表示し続け、案内の表示時に最新の同期待ち一覧を取り直す。
AcceptとTheme生成は引き続き別操作（Human Gateを維持）。

## Freezeとの関係
- `freeze/*` のスナップショットは変更していない（V1・V1_1ともSHA256自己検査OK、READING_UI_V1 Gold 4件一致）。
- 現行の原本のうち `theme-explorer-panel.tsx` はV1_1 snapshotと差分が出る（Phase C = V1_2扱いの変更。Article Discoveryのロジックではなく、0件時のヒント表示のみ）。
  `freeze_check.py` はこの1件を「original-changed-after-freeze」と報告する（想定どおり）。
- `app-sidebar.tsx`・`i18n.tsx` はV1 snapshotの対象ファイル。同様にV1からの差分（V1_1で既に差分あり）。

## 既知の制約・残課題
- Collection: 定期実行(Scheduler)は未登録。運用画面は「未設定」と表示し、`scheduled_external` のrunが3日以内にあれば「設定済み」とする設計。実登録は次Phase。
- 収集の「更新停止」判定は `automation_allowed=true` のSource（現在3件）のみ。手動専用105件は対象外。
- OFAC full runは既存のまま（運用画面には「実行中」と表示）。
- 実際のLLM失敗はE2Eで再現していない（単体テストで検証: 失敗後もaccepted・同期待ちのまま、再試行可）。
- 既存テスト `test_geo_actor_intelligence_service.py::test_resolve_reference_date_with_none_case_id_skips_query_and_falls_back_to_today`
  は、UTCとローカル日付がずれる時間帯（日本時間の午前0〜9時）に失敗する（本Phase以前からのテスト側の問題、未修正）。
- 運用画面の文言は日本語固定（既存のPhase C部品と同様）。メニュー名のみja/en。
- LLMが作るTheme Candidateの題名に、根拠から飛躍した内容（例:「台湾有事テーマとの接続可能性」）が混じることがある（既存の生成ロジックの挙動。本Phaseでは変更していない）。
