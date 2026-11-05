# 地政学ナレッジ＆インテリジェンスDB — Ingestion運用（Phase 2A、Phase 2Bで更新）

## 1. 方針: 常駐Schedulerは導入しない

Phase 2Aでは自動実行デーモンを持たない。手動トリガー（API/CLI）＋外部スケジューラ（Windows Task Scheduler / cron / Azure Scheduler等）からの定期呼び出しを前提とする。`geo_connector_base.calculate_next_run(update_cadence)`は次回推奨実行時刻を`geo_ingestion_runs.next_scheduled_at`へ**記録するだけ**で、実行はしない。

## 2. CLIでの手動実行

```powershell
# 仮想環境から実行（プロキシ・SSL設定はconfig.jsonから自動反映）
cd projects\geopolitical_monitor_dashboard
..\.venv\Scripts\python.exe geo_connector_cli.py world_bank --theme-id <uuid> --entity-id <uuid> --country-code JP
..\.venv\Scripts\python.exe geo_connector_cli.py ofac --theme-id <uuid>
..\.venv\Scripts\python.exe geo_connector_cli.py ofac --theme-id <uuid> --max-records 50   # テスト用（delisted判定は行われない）

# Phase2B: IMF（無認証）
..\.venv\Scripts\python.exe geo_connector_cli.py imf --theme-id <uuid> --entity-id <uuid> --country-code JPN

# Phase2B: UN Comtrade / WTO / Manifesto Project（Credential-required。事前に環境変数へAPIキーを設定すること）
$env:GEO_UN_COMTRADE_API_KEY = "..."
..\.venv\Scripts\python.exe geo_connector_cli.py un_comtrade --theme-id <uuid> --reporter-entity-id <uuid>
$env:GEO_WTO_API_KEY = "..."
..\.venv\Scripts\python.exe geo_connector_cli.py wto --theme-id <uuid> --entity-id <uuid>
$env:GEO_MANIFESTO_API_KEY = "..."
..\.venv\Scripts\python.exe geo_connector_cli.py manifesto --theme-id <uuid> --max-records 20   # テスト用
```

`status`が`completed`/`completed_with_warnings`なら終了コード0、`failed`なら1。`--is-demo`を付けるとDemo Fixtureとして作成される（既定検索・Foundation Packからは除外される）。Credential-required Connectorは`connector_credentials.{key}.env_var`（`config.json`）が指す環境変数が未設定の場合、`credential_configured=false`により実行前に`ConnectorDisabledError`で拒否される（詳細: `docs/geopolitical-connector-guide.md`1.2章）。

## 3. APIでの手動実行

| メソッド/パス | 内容 |
|---|---|
| `POST /api/geo/knowledge/connectors/world_bank/run` | body: `{theme_id, entity_id, country_code, date_range?, is_demo?}` |
| `POST /api/geo/knowledge/connectors/ofac/run` | body: `{theme_id, max_records?, is_demo?}` |
| `POST /api/geo/knowledge/connectors/imf/run` | body: `{theme_id, entity_id, country_code, is_demo?}`（無認証） |
| `POST /api/geo/knowledge/connectors/un_comtrade/run` | body: `{theme_id, reporter_entity_id, partner_entity_id?, is_demo?}`（Credential-required） |
| `POST /api/geo/knowledge/connectors/wto/run` | body: `{theme_id, entity_id, is_demo?}`（Credential-required） |
| `POST /api/geo/knowledge/connectors/manifesto/run` | body: `{theme_id, max_records?, is_demo?}`（Credential-required、Main Datasetのみ） |
| `GET /api/geo/knowledge/sources` | `geo_source_registry`一覧（`connector_enabled`/`credential_configured`を含む。ボタンのdisabled判定に使用） |
| `GET /api/geo/knowledge/ingestion-runs` | Ingestion Run一覧（`geo.sources-documents.tsx`のConnectorカードから利用） |
| `GET /api/geo/knowledge/ingestion-runs/{id}` | 単一Run詳細（fetched/inserted/updated/error件数、error_summary） |
| `GET /api/geo/knowledge/raw-snapshots/{id}` | Raw Snapshot参照（storage_path、content_hash） |

フロントエンドからは`geo.sources-documents.tsx`のConnectorカードから手動実行できる。Credential-required Connector（UN Comtrade/WTO/Manifesto Project）のボタンは、`GET /sources`の`credential_configured`が`false`の場合disabledになり、APIキー未設定である旨を表示する。

## 4. 外部スケジューラからの定期実行例

### Windows Task Scheduler

```powershell
$action = New-ScheduledTaskAction -Execute "c:\Users\269811\python\.venv\Scripts\python.exe" `
  -Argument "geo_connector_cli.py ofac --theme-id <uuid>" `
  -WorkingDirectory "c:\Users\269811\python\projects\geopolitical_monitor_dashboard"
$trigger = New-ScheduledTaskTrigger -Daily -At 6am
Register-ScheduledTask -TaskName "GeoOfacIngestion" -Action $action -Trigger $trigger
```

### cron（Linux/WSL環境の場合）

```
0 6 * * * cd /path/to/geopolitical_monitor_dashboard && .venv/bin/python geo_connector_cli.py ofac --theme-id <uuid> >> logs/ofac_cron.log 2>&1
0 3 1 */3 * cd /path/to/geopolitical_monitor_dashboard && .venv/bin/python geo_connector_cli.py world_bank --theme-id <uuid> --entity-id <uuid> --country-code JP >> logs/wb_cron.log 2>&1
```

World Bankは`quarterly`、OFACは`daily`を推奨頻度とする（`geo_connector_base.UPDATE_CADENCE_DAYS`）。

### Azure Scheduler / Logic Apps

APIエンドポイント（`POST /connectors/{name}/run`）をHTTPアクションから呼び出す構成でも同様に実現可能（Azure環境固有の認証設定はスコープ外）。

## 5. 実行結果の監視

- `GET /api/geo/knowledge/ingestion-runs`で直近のRun一覧（status/fetched_count/inserted_count/updated_count/error_count）を確認
- `error_summary`にConnector内で捕捉した例外の要約が入る（1件のIndicator取得失敗等は他のIndicatorの処理を止めない設計）
- `geo_source_registry.last_successful_ingestion_at`/`last_failed_ingestion_at`/`failure_count`もRun完了時に更新される
- Source単位の鮮度（overdue判定含む）は`GET /api/geo/knowledge/freshness/sources/{source_id}`で確認（詳細: `docs/geopolitical-freshness-policy.md`）

## 6. 失敗時の対応

1. `error_summary`を確認し、原因（プロキシ/SSL/レート制限/フォーマット変更等）を特定する
2. Connectorの実装を修正しても、既存のRaw Snapshot・Documentは**Immutable**なので過去分は変更しない（再取得すれば新しいSnapshotとして保存される。同一content_hashなら重複保存されない）
3. `automation_allowed=false`にすると`ConnectorDisabledError`で実行を拒否できる（緊急停止手段）

## 7. スコープ外（Phase 2B候補）

常駐Scheduler、失敗時の自動リトライキュー、Slack/Teams等への失敗通知連携。

## 8. GDELT External Intelligence Layer（DOC / raw Event・Mentions・GKG stream）

DOC API（記事Signal取得）とraw Event/Mentions/GKG stream（Actor/Media Episode Candidate生成）は
別ファイル（`geo_connector_gdelt.py`/`geo_connector_gdelt_stream.py`）だが、**Historical
BackfillとDailyの別pipelineは作らない**——両方とも「既存の同一関数を、より広いsince/until
と、より多い呼び出し回数で使うだけ」という設計に統一されている。

### 8.1 Initial Load手順

1. `gdelt-historical --since <historical-start> --until <now> --window-hours 24 --promote-to-checkpoint`
   （`--query-id`省略＝**有効な全query**を対象にDOC側をwindow分割しながら投入）を実行し、
   各queryの`saturated_windows`が空であること・全体の終了コードが0（`all_clean=true`）で
   あることを確認する（非空/非0なら`--window-hours`を狭めて再実行）。完全解決していれば
   `--promote-to-checkpoint`によりqueryごとの定常Incremental checkpointへ即座に昇格する。
2. DOC側の投入結果から、実際にDBへ保存された対象Signal群の最古`published_at`を確認し
   （例: `select min(published_at) from geo_external_signals where provider='gdelt' and
   is_demo=false`）、それをraw stream Initial Loadの実用的な`since`とする（狭いwindowでの
   probe結果は規模感を掴む診断に留め、0件をAPI到達不能の証拠として扱わない——`sort=
   datedesc`の広域queryや狭いwindowにたまたま該当記事が無かっただけの可能性を排除
   できないため）。
3. `gdelt-stream-catchup --since <手順2で確認した最古の日時> --until <now>`でraw streamを
   追いつかせる（`since`を明示指定するため既定では`checkpoint_scope="adhoc"`として記録され、
   定常watermarkには影響しない）。
4. Initial Loadが完了し`remaining_batch_count=0`（`stopped_reason="caught_up"`）になった
   ことを確認したら、**同じ範囲を`--promote-to-checkpoint`付きで再実行**し、その完了範囲を
   明示的に定常Incremental checkpointへ昇格させる（これを行わないと、次回`since`省略の
   catch-upがInitial Loadで取り込んだ範囲を無視してbootstrap相当の挙動になる）。promotionは
   不完全なcatch-upや、既存checkpointとの間にgapが残る範囲に対しては自動的に拒否され
   （`promotion_rejected_reason`で理由が返る）、定常watermarkが誤って巻き戻ることはない。
5. 必要に応じて`split-candidates`/`generate-statement-candidates`を手動実行する（自動結線
   はしない、既存2B-2B/2B-3A-1方針を維持）。

広範囲（数ヶ月）の本格的Historical catch-upは、小規模試行で挙動を確認してから、規模と
所要時間を確認のうえ実行すること。

### 8.2 定常運用手順

```powershell
..\.venv\Scripts\python.exe geo_connector_cli.py gdelt-batch
..\.venv\Scripts\python.exe geo_connector_cli.py gdelt-stream-catchup --batches-per-call 100 --max-calls 10
```

`gdelt-batch`（DOC、日次、`run_enabled_external_signal_queries`が各enabled queryについて
`run_gdelt_doc_catchup(since=None)`を呼ぶ——固定7日lookbackではなくqueryごとのcheckpoint
から自動catch-up）→`gdelt-stream-catchup`（raw stream、日次、`since`省略で直近の
`checkpoint_scope="incremental"`のwatermarkから自動catch-up）を、DOC→raw streamの順で
実行する（raw streamのURLマッチングは既存Signal集合に依存するため）。`since`を明示指定
しない限り、DOC・raw streamともに長時間の運用停止があっても最後に確定したcheckpointから
自動的に再開する（DOCは`connector_type='gdelt_doc_catchup'`、raw streamは
`connector_type='gdelt_stream_catchup'`、それぞれ`request_parameters.checkpoint_scope=
'incremental'`行）。

### 8.3 is_demo・queryごとの分離

is_demo=true/falseのcheckpoint・watermark・completed/missing batch判定は完全に独立している
（demo環境での試験実行がproductionの定常watermarkや進捗判定に影響することはなく、逆も
同様）。DOC側は`query_id`単位でも完全に独立したcheckpointを持つ——あるqueryのcatch-up
完了が他queryのcheckpointを進めることはない。運用コマンドを叩く際は常に対象環境
（`--is-demo`の有無）を明示すること。

### 8.4 promotionのガードレール

`--promote-to-checkpoint`は、そのcatch-up呼び出しが完全解決している場合に加え、既存の
定常watermarkが存在するなら**既存watermarkから今回のrangeまでが完全に連続していること**
（橋渡し区間に未取得gapが無いこと・新しいcompleted_throughが既存より後退しないこと）を
確認した場合のみ有効になる（既存watermarkが無いInitial Load初回のみ、このrange自身を
そのままbootstrap checkpointにできる）。不完全なcatch-upや、既存checkpointとの間にgapが
残っている場合、または既存より古いrangeを後からpromoteしようとした場合は
`promotion_rejected_reason`（`incomplete_catchup`/`checkpoint_gap`/`would_regress_checkpoint`）
付きで拒否され、`checkpoint_scope`は`"adhoc"`のまま据え置かれる（定常checkpointが誤って
巻き戻ることはない）。

### 8.5 404/欠番の扱い（raw stream）

`geo_ingestion_runs.request_parameters.batch_gap=true`の行は「maturity cutoff（既定6時間、
`config.json`の`geo_external_signals.gdelt_stream.batch_maturity_hours`で変更可）を超えた
時点でMentions/GKG両方が404だった恒久的な欠番」として記録され再試行されない。まだmature
でないbatchの404や片方だけの404はretryableのまま残る。`--allow-immature-batches`
（`bypass_maturity_clamp`）は調査目的の明示的debugオプションであり、通常運用では使用しない。

### 8.6 監視

既存の`GET /api/geo/knowledge/ingestion-runs`に加え、`gdelt-stream-catchup`の
`remaining_batch_count`/`stopped_reason`/`completed_through`/`checkpoint_scope`（exit
code）で「追いつけていない」状態とその理由（`max_calls_reached`か`no_progress`か）・
確定済み範囲・それが定常checkpointに反映されたかを検知する。DOC側は`gdelt-historical`/
`gdelt-batch`出力の`saturated_windows`を毎回確認し、非空であれば取りこぼしの可能性が
あるため`--window-hours`を狭めて再実行する。
