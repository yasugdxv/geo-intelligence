# Production Scheduling Runbook（正本 / Canonical）

> **この文書が本番運用Runbookの正本です。**（2026-10-06、Phase D.1で、並行セッションが別々に作った
> `production-scheduling-runbook.md` と `production-operations-runbook.md` を、実装と照合して1本に統合した。
> 後者は `DEPRECATED / superseded` の参照ファイルになっている。）

対象: 収集 → Signal → Event → Episode Candidate → Episode Cluster Candidate → **Human Review** の自動運転（Scheduler）。
状態: **SCHEDULER READY / PRODUCTION DEPLOYMENT PENDING**
（Job定義・CLI・heartbeat・stale回復・retry・Dry Run・配備定義・Runbookは完成。本番Linuxサーバが未構築のため、OSのtimer/cron登録は未実施。登録したとは表示しない。）

> 原則: 自動で動くのは **Episode Cluster Candidate まで**。`candidate → accepted` と Theme Candidate生成は人間が行う（Human Gate）。LLMは使わない。

## 0. 文書の正本（Canonical）

| 種類 | パス |
|---|---|
| Runbook（本書） | `docs/production-scheduling-runbook.md` |
| Design / Change Log | `docs/PHASE_D_PRODUCTION_SCHEDULING_2026-10-06.md` |
| Completion Report | `docs/phase-d-production-scheduling-completion-report-2026-10-06.md` |
| Freeze | `freeze/PRODUCTION_SCHEDULING_V1/`（D.1の変更は `freeze/PRODUCTION_SCHEDULING_V1_1/`） |
| Job定義 | `config/scheduler_jobs.yaml` |
| 配備定義 | `deploy/scheduler/`（systemd service/timer・cron例） |

## 1. 全体像

```
【自動（Scheduler）】 15分ごとのtick
External Sources → Collection（GDELT DOC / GDELT Stream / IMF / World Bank / OFAC）
                 → Signal → Event → Episode Candidate → Episode Cluster Candidate（→ Situation Link）

【Human Gate（Schedulerは絶対に自動実行しない）】
Episode Cluster Candidate → Accept / Reject（/geo/episode-candidates）

【人が明示的に実行（Phase C UI）】
Accepted → Theme Candidate Sync → Theme Discovery → Monitoring / Research / Expert AI
```

Signal→Event→Episode Candidate→Episode Cluster（→Situation Link）は `COLLECTION_GDELT_STREAM` 1つのJob内に
埋め込み済み（既存 `run_gdelt_stream_catchup`）。`BUILD_*` の独立Jobは存在しない。
対象は Phase A で AUTO_ACTIVE と分類した Source（IMF・OFAC・World Bank と GDELT）だけ。MANUAL_ONLY の105件は自動収集しない。

## 2. コード構成

| ファイル | 役割 |
|---|---|
| `config/scheduler_jobs.yaml` | Job定義（周期・依存・retry・stale上限・catch-up・IMF/World Bankの`params`）。retry/backoffはここ**だけ**で管理 |
| `geo_scheduler_service.py` | Job状態・lock判定・stale検知・依存チェック・catch-upギャップ計算・Dry Run・実行（既存Connectorの薄いラッパー） |
| `geo_scheduler_tick.py` | 「今やるべきJobを判定して実行」する1回分（周期判定・retry/backoff・heartbeat・stale回復・Operations向け概況） |
| `geo_scheduler_cli.py` | OSのtimer/cronからの入口。`tick` / `plan` / `status` / `run` / `recover-stale` |
| `geo_scheduler_api.py` | `/api/geo/scheduler/*`（状態・Dry Run・手動実行） |
| `geo_gdelt_rate_limit.py` | GDELT DOCの**全体**レート制御・429クールダウン・収集の排他（D.1） |
| `geo_operations_service.py` | `/geo/operations` への接続（Scheduler状態・stale・Action Queue） |

新しいDB tableは無い。状態は `geo_ingestion_runs` のみ:
`connector_type='scheduler_job'`（Job結果）／`'scheduler_heartbeat'`（稼働の印。**1行**を更新し続ける）。

## 3. Scheduler（起動・停止・状態確認）

### 3.1 本番Linux（systemd timer。推奨）
定義: `deploy/scheduler/geo-scheduler-tick.{service,timer}`（15分ごと）、`geo-scheduler-ofac.{service,timer}`（OFAC専用枠・毎日）

```bash
sudo cp deploy/scheduler/geo-scheduler-*.service deploy/scheduler/geo-scheduler-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now geo-scheduler-tick.timer geo-scheduler-ofac.timer   # 起動（再起動後も自動）
systemctl list-timers 'geo-scheduler-*'                                         # 次回実行
sudo systemctl disable --now geo-scheduler-tick.timer geo-scheduler-ofac.timer  # 停止
journalctl -u geo-scheduler-tick.service -n 100 --no-pager                      # ログ
```
cronなら `deploy/scheduler/crontab.example`。Windows（開発機）はTask Schedulerで `...\.venv\Scripts\python.exe geo_scheduler_cli.py tick` を15分ごと。
パス・User・プロキシはサーバに合わせて編集する。`config.json`（秘密情報）は作業ディレクトリに置く（**コミットしない・内容を出力しない**）。

### 3.2 状態確認
```bash
python geo_scheduler_cli.py status        # Scheduler(heartbeat)とJobごとの状態
python geo_scheduler_cli.py status --json
python geo_scheduler_cli.py plan          # Dry Run: 今tickを実行したら何が動くか（DBを一切変更しない）
```
- Scheduler: `healthy`＝最終heartbeatが45分以内／`unhealthy`＝それより古い（timer/cronを確認）／`not_configured`＝heartbeatが一度も無い（未登録）。
- Job: `scheduled`(待機) / `due`(実行待ち) / `running` / `stale`(停止の疑い) / `backoff`(再試行待ち) / `exhausted`(再試行を使い切り) / `blocked`(設定・認証など再試行しても直らない) / `not_configured` / `disabled`
- 画面でも同じ情報を確認できる: `/geo/operations`

### 3.3 手動のtickは必ず `--no-heartbeat`
人が手で実行したtickも「稼働の印」を書くため、付けないと未登録なのに「稼働中」と表示される（Phase Dで実際に発生）。
`python geo_scheduler_cli.py tick --no-heartbeat`（OSのtimer/cronからは付けない）。

## 4. Collection

### 4.1 Jobと周期（`config/scheduler_jobs.yaml`）
| Job | 周期 | 備考 |
|---|---|---|
| COLLECTION_GDELT_DOC | daily | 記事（News DOC）。失敗した期間は次回そこから自動で再取得（§6） |
| COLLECTION_GDELT_STREAM | every_15_minutes | Event/Mentions/GKG。DOCが**実行中の間だけ**待つ（DOCが失敗していても動く） |
| COLLECTION_OFAC | daily（専用枠） | 約19,500件・30時間超。tickからは除外し、別timerで1日1回 |
| COLLECTION_IMF / WORLD_BANK | quarterly | 対象国を設定するまで `not_configured`（§10） |
| UN Comtrade / WTO / Manifesto | – | APIキー未取得のため無効 |

### 4.2 手動実行・Manual Trigger
```bash
python geo_scheduler_cli.py run COLLECTION_GDELT_DOC       # 周期・backoffを無視して今すぐ（lock・依存は有効）
python geo_scheduler_cli.py run COLLECTION_GDELT_STREAM
python geo_scheduler_cli.py run COLLECTION_OFAC            # 長時間。実行中は再実行されない
python geo_scheduler_cli.py run <JOB> --force              # 実行中lockを無視（障害時のみ。二重実行に注意）
python geo_connector_cli.py gdelt-batch                    # 従来のCLI（Scheduler経由でない直接実行）
```
API: `POST /api/geo/scheduler/jobs/{JOB}/run`（認証必須）。**別のGDELT DOC収集が実行中なら `skipped`**、
`POST /api/geo/external-signals/fetch`（Queryの手動実行）は **409「現在実行中です」** を返す。
Human Gate後のJob（`THEME_CANDIDATE_SYNC`）は拒否される（400）。UIボタンは新設していない。

## 5. Failure（失敗時の対応）

### 5.1 failed / retry
- 失敗は `scheduler_job` 行に記録され、retry可能かを自動分類する:
  - retryable: ネットワーク切断・タイムアウト・**429**・5xx
  - non-retryable: 認証/APIキー未設定・スキーマ不一致・Connector無効
  - 不明: retryable扱い（ただし回数で必ず止まる）
- 回数と待ち時間は `scheduler_jobs.yaml` の `retry_policy`（Jobごと→defaults）の**一箇所**。例: DOC=最大2回・5分×2^n、OFAC=最大1回・30分。
- 上限を超えると `exhausted`（人の確認待ち。6時間後に自動再開）。non-retryableは `blocked`（次の周期まで再試行しない）。**無限retryはしない。**
- 失敗は `/geo/operations` の収集欄とAction Queueに出る。

| 症状 | 原因候補 | 対応 |
|---|---|---|
| Collection failure | ネットワーク・GDELT 429・credential失効 | `status` で分類を確認。retryableは次tickで自動retry。non-retryableは設定修正 |
| stale（runningのまま残留） | プロセスのクラッシュ | 5.2 |
| LLM failure / Theme Sync failure | Azure OpenAI一時障害 | Scheduler対象外。`/geo/operations` から再試行（Clusterはacceptedのまま保持される） |

### 5.2 stale（止まった「実行中」）
クラッシュや強制終了で `status=running` のまま残る行がある。Jobごとの上限時間（DOC 3h／stream 2h／OFAC 48h）を超えたrunningを、
tickの冒頭で自動的に `failed`（`STALE_RUNNING_RECOVERED`）へ補正する。**成功扱いにはしない**。過去に取り残された行も対象。
```bash
python geo_scheduler_cli.py recover-stale --dry-run   # 対象の確認（変更なし）
python geo_scheduler_cli.py recover-stale             # 補正
```
上限内のrunningは触らない（二重起動防止のlockとして扱う）。OFACは上限48時間なので、動いているか不明な場合はプロセスを確認してから補正する。

### 5.3 二重起動の防止
- Job単位: 上限内の `running` 行があれば同じJobを起動しない（DB）。
- tick単位: ロックファイル（`logs/scheduler_*.lock`。3時間以上古ければ無効）。OFACは別枠。
- **GDELT DOC単位（D.1）**: 収集の排他リース（§6.3）。定期・catch-up・手動のどれか1つだけが走る。

## 6. GDELT News DOC の429対策（Phase D.1）

### 6.1 原因（調査結果）
1. **間隔制御がプロセス内だけ**だった（`_last_request_at` はプロセス内変数）。API server・CLI・Scheduler・手動が別プロセスだと、
   それぞれが「5秒空けたつもり」でも全体では同時アクセスになり、GDELT（同一IPから5秒に1回）の制限を超えた。スレッド間も未保護。
2. **Retry-Afterを読まず**、429のたびに短い待ち（5→10→20→40→80秒）で最大5回連打して状況を悪化させた。
3. **失敗した窓でcheckpointが進んでいた**（最大の原因）。DOCのcatch-upは「飽和」した窓でしか進行を止めず、429等で取得に失敗した窓は
   「0件・非飽和＝確認済み」と扱われ、**失敗した期間が二度と再取得されなかった**。そのため記事が10月2日以降増えなかった。
4. 定期・catch-up・手動の同時実行を防ぐ仕組みが無かった（Job単位のDB lockはあったが、手動API/CLIは迂回できた）。

### 6.2 対策
| 項目 | 内容 |
|---|---|
| 全体の最低間隔 | 全プロセス・全スレッドで**6秒**（公式5秒＋余裕、＋0〜0.5秒のjitter）。設定値がそれより小さくても6秒を守る。送信枠を予約する方式（待っている間はロックを持たない） |
| 429 | **Retry-Afterがあれば尊重**。無ければ 30秒→90秒（+jitter）。待ち時間は**全プロセス共通のクールダウン**として共有 |
| 最大回数 | 1リクエストで429は3回まで。超えたら**全体で10分休む**（従来は5回連打）。クールダウンが2分を超える間は、待たずに即失敗（Job側のbackoffに任せる） |
| 失敗メッセージ | `GDELT status=429`を含める（Schedulerはretryableと分類） |
| checkpoint | **取得に失敗した窓があれば、そこでcheckpointを止める**（次回その窓から再取得）。catch-upは`failed`として返り、Schedulerがbackoffする |
| 排他リース | 定期・catch-up・手動の入口（`run_gdelt_ingestion` / `run_enabled_external_signal_queries` / `run_gdelt_doc_catchup*`）で取得。再入可能。別の収集が実行中なら `GdeltBusyError`（API=409、Scheduler=skip。失敗に数えない） |
| 状態の置き場 | `logs/gdelt_doc_rate_state.json`・`logs/gdelt_doc_collection.lease`（`GEO_GDELT_STATE_DIR`で変更可）。**単一ホスト前提**。複数ホストから同じGDELTへアクセスする場合は、DB共有状態への置き換えが必要 |

### 6.3 状態の確認
```bash
python geo_scheduler_cli.py status --json   # gdelt_doc: クールダウン残り・収集実行中か・最小間隔
python -c "import geo_gdelt_rate_limit as r; print(r.get_state(), r.lease_status())"
```
- **クールダウン中**は、新しい収集もGDELTへ送らずに即失敗する（短時間に連打しない）。明けてから再実行する。
- 落ちたプロセスのリースは、30分更新が無ければ自動で無効になる。
- 社内プロキシ経由で出口IPが共有されている場合、他の利用者のアクセスも制限に数えられる可能性がある（429が単独実行でも出る場合は、時間をおく）。

## 7. Catch-up（取りこぼしの回復）
- **GDELT DOC（定常）**: queryごとのcheckpoint（最後に確定した時刻）から現在まで自動で追いつく。失敗した窓があればそこで止まり、次回そこから再開する。`plan` の `catchup_gap` でギャップが分かる。
- **長期の欠損の回復**（checkpointが失敗を飛び越えて進んでいる場合）は、過去の範囲を明示して取り直す。重複は既存のdedupで防がれる（同じ記事は増えない）:
  ```bash
  python geo_connector_cli.py gdelt-historical --since <欠損の少し前 YYYYMMDDHHMMSS> --until <現在> --window-hours 24
  ```
  1回で全期間を処理せず、429が出たらクールダウンが明けてから再実行する（`status`で確認）。
- **GDELT stream**: 最後に確定したwatermarkから現在まで、1回のtickで最大 `10コール×100バッチ` 追いつく。残りは次のtick（`追いつき途中`と表示）。
- 同じ期間を再実行しても、Signal・Event・Episode Candidate・Clusterは重複しない（dedupe_key／既完了バッチの除外／Clusterはcandidate状態のみ再構築）。

## 8. Human Review（人が行う）
自動処理は Episode Cluster Candidate まで。
1. `/geo/operations` →「次にやること」→ **[レビューする]**（`/geo/episode-candidates`）
2. 並び順（新しい順／古い順／根拠の多い順）を選び、Clusterを確認して **Accept**（またはReject）
3. Accept直後の案内、または 9 へ

## 9. Theme Sync（人が明示的に実行）
`/geo/operations` の「承認済み・Theme同期待ち」から **[Theme Candidateを生成]**（個別）／**[未同期をまとめて生成]**（一括）。ここでだけLLMが使われる。
失敗してもClusterはacceptedのまま残り、再試行できる。同じClusterの再実行で重複しない。

## 10. 設定が必要なもの
### 10.1 IMF / World Bank 対象国
`config/scheduler_jobs.yaml` の `COLLECTION_IMF` / `COLLECTION_WORLD_BANK` の **`params`**（`theme_id` / `entity_id` / `country_code`）を人が決めて入力すると有効になる。
未入力（null）の間は `not_configured` で、実行も失敗も記録しない（`geo_entities` に対象国のentityを登録し、そのIDを書く。`docs/geopolitical-connector-guide.md`参照）。
### 10.2 OFAC
約19,500件を1件ずつ処理し、Phase Aの実測で30時間超。専用枠で他Jobと同時に動かない。止まった場合はstaleとして補正され、次回は最初から再実行される（処理方式の最適化は対象外）。
### 10.3 通知
メール/Slack/Teams通知は未実装（次フェーズ候補）。異常は `/geo/operations` で確認する。

## 11. 日次運用（担当者）
1. 朝、`/geo/operations` を開く
2. **Collection異常**（収集停止・Scheduler停止・止まった「実行中」・429クールダウン）を確認
3. **重要Monitoring Alert**を確認
4. **Episode Review backlog**を確認 → レビューして **Accept**
5. 必要なものを **Theme Sync**
6. **Theme Discovery / Research / Expert AI** へ進む

## 12. 本番デプロイの状態
- **完了**: Job定義、CLI、周期判定、retry/backoff、heartbeat、stale回復、lock、catch-up、GDELT DOCの全体レート制御・排他、Dry Run、Operations画面連携、配備定義（systemd/cron）、Runbook
- **未実施（PRODUCTION DEPLOYMENT PENDING）**: 本番Linuxサーバでの timer/cron 登録（サーバ未構築のため）。登録後は `status` が `healthy` になり、`/geo/operations` が「稼働中」と表示する。

## 13. Freeze資産の保護
`freeze/READING_UI_V1/`、`freeze/SOURCE_INTELLIGENCE_V1/`、`freeze/SOURCE_INTELLIGENCE_V1_1/`、`freeze/OPERATIONAL_WORKFLOW_V1/` は変更しない。
整合性: `python freeze/check_all_freezes.py`（各snapshot自身のSHA照合。現行treeが先に進むこと自体は失敗ではない）。
