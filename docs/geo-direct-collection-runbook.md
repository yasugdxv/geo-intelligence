# GEO_DIRECT_COLLECTION_V1 運用手順（Direct Source Collection）

政府・国際機関などの重要Sourceから、**GDELTに依存せず**Geoへ直接最新情報を入れる仕組み。
Sustainability / ERMの記事・Crawlerには依存しない（見つけたSourceをGeo Registryへ登録し、Geoとして独立に収集する）。

## 構成

| 要素 | 役割 |
|---|---|
| Geo Source Registry（`geo_source_registry` / `geo_source_domains`） | Sourceの同一性と**承認ゲート**（`automation_allowed` / `connector_enabled`）が正 |
| `config/geo_direct_sources.yaml` | 取得口（feed URL・API・XML）、言語、Geo向けの決定的なkeyword方針、Coverage領域ラベル。**Source IDは書かない**（Registryのdomainで引く） |
| `geo_direct_collection_common.py` | 共通処理（Source解決・HTTP・Provenance付きSignal生成・Registry最終成功/失敗の記録） |
| `geo_connector_rss.py` | 汎用RSS/Atom Collector（防衛省・IAEA・WTO） |
| `geo_connector_federal_register.py` | 米連邦官報API（機関・検索語による決定的な絞り込み） |
| `geo_connector_sanctions_xml.py` | 構造化制裁リスト（国連・英国）。Baseline→Diff |
| OFAC | 既存Connector（`geo_connector_ofac.py`）をそのまま使用（新規実装なし） |

保存先: 記事型は `geo_external_signals`（`provider='rss'` / `'official'`、`source_role='official_statement'`、`source_id`列で出所を追跡）。制裁リストは `geo_sanctions_entries` / `geo_sanctions_entry_versions`。

## Provenance（各取得データで追跡できるもの）
`source_id`（列）／`source_domain`／`source_name`／`provider`／`url`（original）／`published_at`＋`timestamp_source='publisher'`／`observed_at`（収集時刻）／`raw_metadata.collector` / `connector_version` / `endpoint` / `ingestion_run_id`。Run単位は `geo_ingestion_runs`。生データは `geo_raw_snapshots`（content_hashで不変保存）。

## Dedup
`geo_external_signal_service` の `normalize_url` / `compute_dedup_key` / `bulk_upsert_signals`（`(provider, dedup_key)` 一意）。RSS再取得・scheduler再実行・手動再実行でも重複しない（実測済み）。

## 手動実行（CLI）
```
python geo_connector_cli.py rss [--source mod_japan --source iaea --source wto]
python geo_connector_cli.py federal-register
python geo_connector_cli.py sanctions [--list un_consolidated] [--list uk_fcdo]
python geo_scheduler_cli.py run COLLECTION_DIRECT_RSS        # Scheduler経由（結果はscheduler_job行に記録）
```

## 承認ゲート（Sourceを有効にする）
`automation_allowed=true`（steward承認。コードから自動変更しない）が必要。未承認のSourceは**失敗ではなく `not_ready` / `not_configured`** として扱われ、取得しない。
2026-10-06に、ユーザー指示のもとWave 1の6 Sourceを段階的に有効化した（`geo_audit` に `source_automation_enabled` を記録）:
1. RSS: 防衛省・IAEA・WTO　2. Federal Register　3. UN制裁・UK制裁。
新しいSourceを追加するときも、**1段階ずつ実データで確認してから**有効化する。

## 制裁リストの扱い（Baseline → Diff）
- **初回=Baseline**: 全件を `geo_sanctions_entries` / `versions`（`added`）へ登録。**Signal/Alertは作らない**（数千件のAlert防止）。
- **2回目以降=Diff**: 追加（added）・変更（amended）・削除（delisted）・再掲載（relisted）だけを検出し、**要約Signalを1件**（title例:「…更新: 追加3件・変更1件・削除0件」、変更例は`raw_metadata.examples`に最大30件）。XML全文・全件はSignalにしない。
- XMLが前回と同一で、Entryが欠けていなければ何もしない。途中失敗したBaseline（versionを持たないEntry）は再実行で補完する（削除はしない）。

## Scheduler（scheduler-ready）
`config/scheduler_jobs.yaml` に3 Jobを追加済み（`COLLECTION_DIRECT_RSS` 6時間ごと／`COLLECTION_FEDERAL_REGISTER` 日次／`COLLECTION_SANCTIONS_XML` 日次）。
**本番Linuxへのtimer登録は未実施**（Production Schedulerは配備待ち）。配備後は `docs/production-scheduling-runbook.md` の手順で `geo_scheduler_cli.py tick` を登録するだけでよい。

## 鮮度（Coverage別）
- **全体の鮮度**（`get_global_freshness`）= **広域メディア探索（GDELT等）のみ**。Direct Source（rss/official）は含めない。含めると、GDELTが止まっていてもDirect Sourceが覆わない領域まで「変化なし」と誤表示される。
- **Direct Sourceの鮮度**は `get_coverage_freshness` でSource・領域別に見る（`config/geo_direct_sources.yaml` の `coverage_areas`）。

## 既知の制約
- Monitoringの判定可否は全体鮮度（GDELT）に依存する。Direct Sourceで覆われるテーマだけを個別に判定可能にするには、テーマ単位の鮮度判定が必要（未実装・要判断）。
- WTOのRSSは約10件で、更新頻度が低い（2026-10-06時点の最新は2026-10-01）。
- UK制裁XMLは約22MB・robots.txtが403のため、日次を超える頻度で取得しない。
- 制裁リストのDiff経路は、実DBではまだリストの変更を観測していない（初回Baselineと「変更なし」は実測済み。Diffは単体テストで検証）。
- 実装上のSource IDのhardcodeはない。Registryのdomainが一意に解決できない場合は取得しない。

## Pilot運用ルール（Production Scheduler未配備の間）
Production Schedulerが本番Linuxに配備されるまで、Direct Collectionは**自動では動かない**。Live Pilotを始める前（および各評価セッションの前）に、次の順で実施する。

1. **Direct Collection Manual Run**（約5分。UK制裁が最も重い）
   ```
   python geo_scheduler_cli.py run COLLECTION_DIRECT_RSS
   python geo_scheduler_cli.py run COLLECTION_FEDERAL_REGISTER
   python geo_scheduler_cli.py run COLLECTION_SANCTIONS_XML
   ```
2. **Freshness Check**: `GET /api/geo/monitoring-themes/live-intelligence-status`（Coverage別のREADY/WARNING/BLOCKED）と、Monitoring画面の「情報源のカバレッジ」。`POST /api/geo/monitoring-themes/check-all` でテーマを再評価する。
3. **Pilot開始**: Coverage別の状態を評価者に伝える（WARNING=一部の情報源のみ最新、BLOCKED=その領域は広域ニュース依存/未設定）。

**「手動実行されていないためstale」は正しい挙動**である（防衛省・IAEA=約1日、Federal Register・制裁リスト=約2日で、Monitoring上のCoverageがstaleになる）。staleのまま「変化なし」と扱わない。
**GDELT停止だけで全領域をBLOCKEDにしない**: 領域ごとに判定する（Directで覆われる領域はWARNING/READYになり得る）。

---
# GEO_DIRECT_COLLECTION_V1_1 追加分（2026-10-06）

## 追加された取得口
| Source | 取得方式 | 実装 | cadence |
|---|---|---|---|
| 統合幕僚監部（日本） | 公開HTML一覧（`mod.go.jp/js/`）。**既存「防衛省」Sourceの2つ目のEndpoint**（別Sourceにしない） | `geo_connector_html_list.py`（パーサ`mod_js_list`） | 6時間 |
| CMF / UN News（中東）/ EIA / USTR | RSS | 既存の汎用`geo_connector_rss.py`（設定のみ） | 6時間 |
| **UKMTO** | 公開Web UIが使う公開JSON endpoint | `geo_connector_ukmto.py`（Source専用） | **30分（これより高頻度にしない）** |

## Provenance（Endpointの区別）
Collection Endpoint用のテーブルは作らない。Signalの`raw_metadata`で区別する。
- 統合幕僚監部: `canonical_source`=日本 防衛省（報道発表）／`canonical_source_id`／**`origin_section`=統合幕僚監部**／`collection_method`=html_list／`endpoint`／`endpoint_name`。`source_id`は防衛省。
- RSS: `collector`=rss／`endpoint`=feed URL／`source_key`。
- UKMTO: `collection_method`=public_json_endpoint／`endpoint_documented`=false／`natural_key`／`payload_hash`／`raw_snapshot_id`／`content_hash`／`version`／`baseline`。

## UKMTO 運用
**APIの性格**: 正式に文書化された公開APIではなく、**公開Web UI（www.ukmto.org Recent Incidents）が通常の画面表示のために使う、認証不要の公開structured endpoint**（`https://sccd.royalnavy.mod.uk/api/ukmto/all`）。GETのみ。write系・投稿系・管理系・認証/WAFの回避は使わない。利用条件: www.ukmto.org は Open Government Licence で公開（再利用可。内容の正確性は保証されない）。
**Known limitation**: schema変更リスク／endpoint変更リスク／可用性の保証なし。変更されたら取得は**失敗として記録**され、Official / Maritime Advisory の Coverage は stale になる（他Sourceがfreshでも引きずられない）。
- **Natural key**: `{issuer}-{incidentNumber}-{発行年}`（例: `UKMTO-156-2026`）。同じ事案を再取得しても重複しない。
- **Version**: 同じ事案番号の内容（hash）が変わったら`version+1`、`previous_hashes`に履歴、`updated_detected_at`。表示制御field（`hideFromTicker`等）の変更は更新にしない。
- **Baseline**: Signalが0件の初回は全件を`baseline=true`で取り込み、**Monitoring Alertの対象にしない**（関連記事・Coverageには数える）。次回以降の新規・更新だけが既存Alert policyへ渡る。
- **Schema Guard**: listでない／必須field欠落／型の不一致は`schema_changed`として**失敗**（空データの成功にしない）。壊れたJSONは`malformed_json`、timeout・接続エラー・HTTPエラーも種類別に`error_summary`へ記録。**正当な空応答（`[]`）**は`mode=empty`（以前は事案があった場合は警告。既存Signalは変更しない）。
- **条件付きGET**: `Last-Modified`を`If-Modified-Since`で送る（ETagは無し）。304は「変更なし」。
- **最小間隔**: 30分未満は取得しない（`skipped_too_soon`。手動実行・直前が失敗の場合も同じ）。検証時のみ `python geo_connector_cli.py ukmto --ignore-min-interval`。
- **存在しないfieldは推測で埋めない**: UKMTOの応答に`status`は無いため保存しない（`pin_colour`・`incident_type_level`・`vessel_under_pirate_control`等をそのまま保持）。個別incidentの公式URLは無いため、公式の一覧ページ＋Geo側の同一性用fragment（natural key）をURLとする。
- **鮮度**: UKMTOは「完全な一覧」を返すため、**取得が健全であればfresh**（事案が無い期間はstaleにしない）。取得が3時間止まる／直近の取得が失敗ならstale。

## Pilot前の手動実行（Production Scheduler未配備の間）に追加
```
python geo_connector_cli.py ukmto          # UKMTO（30分以内の再実行は自動でskip）
python geo_scheduler_cli.py run COLLECTION_DIRECT_RSS   # RSS 7件＋統合幕僚監部（HTML list）
python geo_scheduler_cli.py run COLLECTION_FEDERAL_REGISTER
python geo_scheduler_cli.py run COLLECTION_SANCTIONS_XML
```
UKMTOは3時間以上実行しないとstaleになる（安全情報のため意図的に厳しい）。Pilotの前に必ず実行する。

## Keyword判定（部分一致の誤検知防止）
- 英大文字2〜4字の略語（`BIS`/`IRGC`/`ADIZ`/`TSMC`）は**単語境界**で判定（大文字小文字を区別）。`PLA`単独のような語は`Plans`/`Plant`に一致するため、`PLA Navy`等の語として成立する表現を使う。
- Federal Register等、機関メタデータを持つ記事は、略語の本文一致ではなく`agency:<正式名>`（例: `agency:Industry and Security Bureau`）で判定する。
- 追加時の注意: `tanker`のような一般語は広い（アデン湾等のHormuz圏外の事案にも一致する）。広い語を単独で足さない。

## Coverage 種類別（Middle East Maritime）
`GET /api/geo/monitoring-themes/live-intelligence-status` の `categories`: Official / Maritime Advisory（UKMTO）・Military / Security（CMF）・Shipping / Commercial（未設定）・Specialist Media（未設定）・Context（UN News・EIA。補助）・Broad Media（GDELT）。**Source数ではなく種類で見る**。国営・専門・報道Sourceは一次と同じ重みで鮮度判定に使わない。
