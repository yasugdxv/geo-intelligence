# Issue: 起動時のschema check失敗で Report Studio 全体が503のまま残る（既存問題）

**記録日**: 2026-10-05 / **性質**: 既存の設計上の問題。Source Intelligence（Source DB / Monitoring / Research連携）の
Freezeを止める問題ではないため、今回は**修正していない**（記録のみ）。

## 発生条件
APIサーバー（`api_server.py`）の**起動（import）時**に、Report Studio系の各routerが
`geo_report_studio_service.check_schema()` を1回だけ実行する。その中で必須テーブル・列を
`select ... limit=1` で確認するが、**このとき1つでもSELECTが失敗**（例: Supabase側のstatement
timeoutによる500、一時的な接続エラー）すると `_SCHEMA_OK=False` となり、以降そのプロセスが
生きている間は**再チェックされず**、対象routerの全エンドポイントが503になる。

2026-10-05の実例:
- 失敗したテーブル: `geo_report_cluster_representatives(id,run_id,cluster_id,record_id)`（空テーブルだが応答に60〜90秒かかり500）。
- 同時間帯に `geo_report_current_pipeline_persistence_service.py` 等の並行変更・実行があり、DB負荷が高かった可能性がある（推測）。
- 約30分後に同じ `check_schema` を直接実行すると成功（8.7秒）。つまり**一時的な負荷による失敗**で、
  スキーマ自体は最新だった。エラーメッセージは「migrationが未適用」と表示されるため、**原因を誤解しやすい**。

## 影響
- 503になるrouter: `/api/geo/report-studio/*`、Current Pipeline、Prompt2、ChatGPT Fallback（prompt export）、Report Edit
  （起動ログに `[geo_report_*_api] WARNING: ... disabled` が出る）。
- 画面: Report Studio一覧・プロジェクトページ（Research Studioの「Source調査」タブを含む）が表示できない。
- 影響しないもの: Source Library / Monitoring / Theme Explorer / Source Research API（`/api/geo/source-research/*`）。
  Source Researchのプロジェクト作成は `geo_report_projects` へ直接書くため成功するが、画面（Report Studio）が開けない。

## 復旧方法
- DBが落ち着いた状態で**APIプロセスを再起動**する（`API_PORT=8001`、venvのPythonで `api_server.py` を起動）。
- 起動に数分かかることがある（起動時のschema checkが複数回走るため）。起動ログに `Report Studio disabled` が出ていなければ復旧。
- 確認: `GET /api/geo/report-studio/projects` が200。

## 関連コード
- `geo_report_studio_service.py` — `REQUIRED_SCHEMA_CHECKS` / `check_schema()`（失敗時に「migration未適用」文言を返す）
- `geo_report_studio_api.py:42-49` — モジュールimport時に `_SCHEMA_OK, _SCHEMA_ERROR = studio.check_schema(_client)`、`_require_schema_ok` が503を返す
- `geo_report_current_pipeline_api.py` / `geo_report_prompt2_api.py` / `geo_report_prompt_export_api.py` / `geo_report_report_edit_api.py` — 同じパターン
- `geo_db_client.py` — `SupabaseClient`（connect 10s / read 60s、最大3回リトライ、ハング監視90s）。リトライにより失敗判定まで90秒前後かかる

## 修正案（今回は未実施。別タスクで検討）
1. check_schemaを「起動時1回」ではなく、失敗時は**TTL付きで再チェック**する（一時的な失敗から自動復旧）。
2. エラーを「migration未適用」と「DB一時障害（timeout/5xx）」で**区別して表示**する（400/404のみをmigration未適用と判定）。
3. 起動時のチェックを遅延実行/並列化し、起動時間を短縮する。
4. `geo_report_cluster_representatives` 等の巨大/高負荷テーブルが `limit=1` で遅い原因（bloat・ロック・index）をDB側で調査する。
