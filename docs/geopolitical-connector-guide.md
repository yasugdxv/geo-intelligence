# 地政学ナレッジ＆インテリジェンスDB — Connectorガイド（Phase 2A・Phase 2Bで更新）

対象読者: 新しい外部情報源Connectorを追加する開発者。既存6 Connector（World Bank / OFAC / IMF / UN Comtrade / WTO / Manifesto Project）を前例として使う。

## 1. 共通Connector Interface（`geo_connector_base.py`）

既存`geo_official_crawler.py`/`geo_documents_store.py`と同じ「クラスではなく関数群」のスタイルを踏襲する。すべてのConnectorはこの順序で共通関数を呼び出す。

```
check_automation_allowed(source_row)
  → create_ingestion_run(...)
  → fetch（Connector固有）
  → find_or_save_raw_snapshot(...)   # content_hashで重複防止、Immutable
  → link_run_to_snapshot(...)        # geo_ingestion_run_snapshotsへ記録
  → save_as_document(...)            # geo_documents（content_hashで重複防止）
  → （Connector固有: chunking / parse / normalize / resolve_entities / generate_claim_candidates）
  → complete_ingestion_run(...)
```

| 関数 | 役割 |
|---|---|
| `check_automation_allowed(source_row)` | `automation_allowed=false`なら`ConnectorDisabledError`を送出 |
| `create_ingestion_run(client, *, source_id, connector_type, run_type="manual", requested_by=None, request_parameters=None, is_demo=False)` | `geo_ingestion_runs`へ`status="running"`で1行作成 |
| `find_or_save_raw_snapshot(client, *, source_id, raw_bytes, ...)` | `content_hash`で既存Snapshotを検索。無ければ`uploads/geo/raw_snapshots/`へ保存し新規行作成。戻り値`(snapshot, is_new)` |
| `link_run_to_snapshot(client, *, ingestion_run_id, raw_snapshot_id, role="primary_payload")` | `geo_ingestion_run_snapshots`へ記録（role: primary_payload/pagination_page/error_response） |
| `save_as_document(client, *, theme_id, source_id, title, extracted_text, mime_type, ...)` | `content_hash`で既存Documentを検索。無ければ`geo_documents`へ1行作成。戻り値`(document, is_new)` |
| `complete_ingestion_run(client, *, ingestion_run_id, status, counts=None, ...)` | `geo_ingestion_runs`をUPDATE（`status`: completed/completed_with_warnings/failed/cancelled）。**`SupabaseClient.update()`は`return=minimal`のため戻り値なし**、呼び出し元は必要なら`get_ingestion_run`で取り直す |
| `calculate_next_run(update_cadence, from_time=None)` | `daily/weekly/monthly/quarterly/annual`から次回予定時刻を計算（記録のみ、自動実行はしない） |

**Raw Snapshotは原則Immutable**。`geo_connector_base.py`にはUPDATE操作を一切実装していない。再解析が必要な場合は新しい`parser_version`で別途処理し、raw本体はそのまま残す。

### 1.1 Source Registryの3ゲート（Phase2B）

`check_automation_allowed(source_row)`は以下3つの**独立した**ゲートをすべて検証する。いずれか1つでも満たさなければ`ConnectorDisabledError`を送出する。

| 列 | 意味 | 誰が/何が変更するか |
|---|---|---|
| `automation_allowed` | terms/steward承認（このソースを自動取得してよいという法務・運用上の判断） | **コードからは絶対に自動変更しない**。Reviewer/stewardの明示操作のみ |
| `connector_enabled` | 運用者による一時停止/再開スイッチ | 運用者がDB/APIから直接設定（既存Connectorは既定`true`のまま移行、影響なし） |
| `credential_configured` | APIキーが実際に利用可能か | `sync_credential_status()`がConnector実行のたびに都度再評価して書き込む。無認証Connectorは触らない（既定`true`のまま「該当しない」を意味する） |

3つを分離した理由: 「APIキーが設定されているかどうか」で`automation_allowed`（法務・運用上の承認）を自動的に書き換えてしまうと、承認プロセスをコードが代行してしまうことになるため。

### 1.2 APIキーの保管方式（Credential-required Connector）

APIキーが必要なConnector（WTO/UN Comtrade/Manifesto Project）は、**`config.json`に実キー値を一切保存しない**。`config.json`には環境変数名への参照（credential reference）のみを持たせ、実際の値はOS環境変数から読む。

```json
"connector_credentials": {
  "wto": {"env_var": "GEO_WTO_API_KEY"},
  "un_comtrade": {"env_var": "GEO_UN_COMTRADE_API_KEY"},
  "manifesto_project": {"env_var": "GEO_MANIFESTO_API_KEY"}
}
```

```python
resolve_credential(config, credential_key) -> str | None   # env_varの値をos.environから読むだけ、config.jsonには実値を書かない
sync_credential_status(client, *, source_id, config, credential_key) -> bool  # credential_configuredを都度再評価
```

Credential-required Connectorの実行フローは以下の順序になる（無認証Connectorにはこの手順は不要）。

```
_ensure_source(...)
  → sync_credential_status(...)          # credential_configuredを再評価
  → source_rowを再select（最新のcredential_configuredを反映）
  → check_automation_allowed(source_row)  # 3ゲートすべて検証
  → resolve_credential(...)               # 実際のAPIキー値を取得
  → （以降は無認証Connectorと同じ流れ）
```

実際にキーを設定するには、`config.json`へ上記`connector_credentials`セクションを追記したうえで、`env_var`に指定した名前のOS環境変数（例: `GEO_WTO_API_KEY`）を設定する。

## 2. 既存Connector

### 2.1 World Bank Indicators Connector（`geo_connector_world_bank.py`）

- 無認証の`https://api.worldbank.org/v2/country/{country}/indicator/{code}`を使用
- 対象Indicatorは`config/geo_world_bank_indicators_seed.yaml`で管理（コードにハードコードしない）。現在7指標: Population, GDP, GDP Growth, GDP per Capita, Inflation, Trade % of GDP, Total reserves
- Pagination対応（`metadata.pages`）、Retry（`MAX_RETRIES=3`、指数バックオフ）
- 1指標ごとに: Raw Snapshot（生JSON）→ Metric Definition/Observation（数値本体、`revision_id`で改訂管理）→ 要約Document/Chunk → Claim（Document ChunkとMetric Observationの**2本**のEvidenceを持つ）
- `run_world_bank_ingestion(client, *, theme_id, entity_id, country_code, date_range="2015:2026", requested_by="system", is_demo=False)`

### 2.2 OFAC Sanctions List Connector（`geo_connector_ofac.py`）

- 公開CSV（`sdn.csv`/`alt.csv`/`add.csv`）を取得。住所（`add.csv`）は2026-08-19に追加済み。国籍・身分証明書番号は
  `citizen.csv`/`id.csv`という単純CSVでは提供されておらず（Web検索で実在しないことを確認済み、旧版の本ドキュメントの
  記述は誤りだった）、OFACの関連XML形式`sdn_advanced.xml`でのみ取得できるため別タスクとして持ち越し
- `add.csv`は`ent_num`で`sdn.csv`と1対多（1エンティティに複数住所）。列は`ent_num, add_num, address,
  city_state_province_postal_code, country, add_remarks`（null値はOFAC側の慣例で`"-0-"`のまま、既存の
  sdn.csv/alt.csv同様に正規化しない）。`source_record_hash`の算出対象に住所を含めているため、名称・program・
  remarksが同一でも住所のみ変わった場合はamendedとして新versionを作成する
- chunkingは段落分割ではなく**レコード単位**（1 sanctions entry = 1 chunk、`section`=external_record_id、`chunk_index`=行番号）
- `source_record_hash`が前回と同一なら新versionを作らず`last_seen_at`のみ更新。差分がある場合のみ`change_type`（added/amended/delisted/relisted）に応じた新versionを作成し、直前versionの`valid_to`を先に更新する
- delisted判定は`max_records=None`（全件取得＝`status="completed"`）の場合のみ実行
- Entity解決は`geo_entity_resolution.resolve_or_flag`で候補化するのみ（**自動Merge禁止**）
- `run_ofac_ingestion(client, *, theme_id, requested_by="system", max_records=None, is_demo=False)`

### 2.3 IMF DataMapper API Connector（`geo_connector_imf.py`、無認証）

- IMFの認証要否は当初未確定だったため、実装前に実アクセスで検証した（推測で決めない）。`api.imf.org`のSDMX 3.0/2.1 REST APIはdataflow識別子が特定できず404が続いたため不採用。**`https://www.imf.org/external/sdmx/...`ではなく`https://www.imf.org/external/datamapper/api/v1/`（IMF DataMapper API）を使用**。`/indicators`（指標メタデータ一覧）・`/countries`（国一覧）・`/{indicator_code}/{country_code}`（データ本体）のいずれも無認証・実データで200応答を確認済み（2026-08-14実施）
- World Bank Indicators APIのような`page`パラメータ形式のページングを持たず、1コールで対象国・指標の全期間分の年次系列が返る（`_fetch_indicator`はpaging無し）
- 対象Indicatorは`config/geo_imf_indicators_seed.yaml`で管理。現在5指標（NGDP_RPCH, NGDPD, NGDPDPC, PCPIPCH, PPPGDP）、いずれも`/indicators`エンドポイントで実在確認済みのコード
- World Bank Connectorと同じEvidence構成（Document Chunk + Metric Observationの2本）
- `run_imf_ingestion(client, *, theme_id, entity_id, country_code, requested_by="system", is_demo=False)`

### 2.4 UN Comtrade API Connector（`geo_connector_un_comtrade.py`、Credential-required）

- `https://comtradeapi.un.org/data/v1/get/{typeCode}/{freqCode}/HS`。認証は`Ocp-Apim-Subscription-Key`ヘッダ（Azure APIM方式）。**Free APIキー登録が必須**（comtradeplus.un.orgで無料登録。「無認証・500件/日次無制限」という古い情報は採用していない）
- `reporterCode`/`partnerCode`は**UN M49数値コード**（ISO3ではない、例: 日本=392、World=0）。対象フロー（reporter/partner/commodity/period）は`config/geo_un_comtrade_flows_seed.yaml`で管理
- 1コールあたりの取得上限（`max_records_per_call`）はseedの設定値として持つ（公式仕様変更に追従できるよう、コードにハードコードしない）
- 貿易は双方向のため、`geo_metric_service.upsert_metric_observation`の`counterpart_entity_id`（reporter⇔partner）を使う
- `run_un_comtrade_ingestion(client, *, theme_id, reporter_entity_id, partner_entity_id=None, requested_by="system", is_demo=False)`

### 2.5 WTO Timeseries API Connector（`geo_connector_wto.py`、Credential-required）

- `https://api.wto.org/timeseries/v1/data`。ベースURLとAzure APIM方式の認証ヘッダ（`Ocp-Apim-Subscription-Key`）は複数の一般公開情報から確認済みだが、**個々のクエリパラメータ名（i/r/p/ps等の略称）はAPIキー未取得のため実アクセスで検証できていない**。`credential_configured=false`の間は実行前に弾かれるため実害はないが、実際にキーを取得し初めて実行する前に、apiportal.wto.orgのAPI Consoleで1件検証すること（`config/geo_wto_indicators_seed.yaml`のコメント参照）
- `run_wto_ingestion(client, *, theme_id, entity_id, requested_by="system", is_demo=False)`

### 2.6 Manifesto Project Database Connector（`geo_connector_manifesto.py`、Credential-required、Main Datasetのみ）

- `https://manifesto-project.wzb.eu/api/v1/get_core`。APIキー必須（manifesto-project.wzb.euで登録）
- **今回のスコープはMain Dataset（政党×選挙年ごとのコード化済み変数: rile score等）のみ**。個々の綱領全文ドキュメント（Corpus、`texts_and_annotations`相当）の取得・保存・chunkingは行わない（`geo_source_registry.content_storage_allowed`/`external_redistribution_allowed`の利用条件確認が必要なため、Phase2Bの別段階へ持ち越し）
- Entity解決はOFAC同様`geo_entity_resolution.resolve_or_flag`で候補化するのみ（自動Merge禁止）。RILEスコアは`geo_metric_observations`へ保存（entity_id=政党Entity、period=選挙年）
- `run_manifesto_ingestion(client, *, theme_id, requested_by="system", max_records=None, is_demo=False)`

## 3. 新しいConnectorを追加する手順

1. `geo_source_registry`への`_ensure_source`相当の関数を用意する（`trust_tier`、`access_method`、`connector_type`を設定。既存2 Connectorの実装を参照）
2. `check_automation_allowed`→`create_ingestion_run`を呼ぶ
3. HTTP取得はConnector固有関数で実装（`config_utils.load_config`/`make_proxies`でプロキシ・SSL設定を反映。`try/except`で失敗時も他の処理を継続できる設計にする — World Bank Connectorは1指標の失敗が他指標に波及しない）
4. `find_or_save_raw_snapshot`→`link_run_to_snapshot`→（必要なら）`save_as_document`
5. データ種別に応じた正規化・Entity解決・Claim/Evidence生成をConnector固有に実装する。Evidence種別（Document Chunk / Event / Observation / Metric Observation）のうち何を使うかは情報の性質で選ぶ（数値時系列は`geo_metric_observations`、構造化レコードはレコード単位Chunk、既存の`geo_events`/`geo_observations`と重複する情報は二重登録しない）
6. `complete_ingestion_run`で終了。`geo_source_registry.last_successful_ingestion_at`/`last_failed_ingestion_at`/`failure_count`を更新する
7. `geo_connector_cli.py`へサブコマンドを追加し、`geo_knowledge_api.py`へ`POST /connectors/{name}/run`を追加する
8. `tests/test_geo_phase2a.py`のパターンに倣い、`requests.get`をモックした単体テストを追加する（実APIを叩くテストは既定で書かない）

## 4. スコープ外（Phase 2B次ラウンド以降）

Babel Street（商用）、Generic Authorized HTTP Connector、OFACの国籍・身分証明書番号取り込み（`sdn_advanced.xml`、単純CSVではなくOFAC独自の関連XML形式のため別途パーサ実装が必要。住所[`add.csv`]は2026-08-19に取り込み済み）、Manifesto ProjectのCorpus全文取り込み・chunking、定期Scheduler常駐化（現状は手動トリガー＋外部スケジューラ、`docs/geopolitical-ingestion-operations.md`参照）。
