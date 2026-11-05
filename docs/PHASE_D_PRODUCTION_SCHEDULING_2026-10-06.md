# Phase D: Production Scheduling / Automated Collection（2026-10-06）

> **Design / Change Log の正本**（Phase D.1で明示）。関連する正本:
> Runbook=`production-scheduling-runbook.md`／Completion Report=`phase-d-production-scheduling-completion-report-2026-10-06.md`／
> Freeze=`freeze/PRODUCTION_SCHEDULING_V1/`（D.1の変更は`PRODUCTION_SCHEDULING_V1_1/`）。
> 以降の変更は末尾の「Phase D.1」に追記する。

判定: **SCHEDULER READY / PRODUCTION DEPLOYMENT PENDING**
（アプリケーション実装は完了。本番Linuxサーバが未構築のため、OSのtimer/cronへの登録は未実施。登録したとは表示しない）

目的: 収集 → Signal → Event → Episode Candidate → Episode Cluster Candidate を定期・安全に自動実行し、
**Episode Review（Human Gate）で必ず止める**。新しい分析機能・推論ロジック・LLMは追加していない。DBマイグレーションなし。

## 構成（既存の土台に追加）
本Phase開始時点で、`geo_scheduler_service.py` / `geo_scheduler_api.py` / `config/scheduler_jobs.yaml` /
`tests/test_geo_scheduler_service.py`（Job定義・lock・stale補正・依存・catch-upギャップ・Dry Run・API）が既に存在していた
（別セッションの作業とみられる）。これを**再利用**し、足りない部分を追加した。

| 区分 | ファイル | 内容 |
|---|---|---|
| 新規 | `geo_scheduler_tick.py` | 周期判定・retry/backoff・heartbeat・stale回復・tick実行・Operations向け概況 |
| 新規 | `geo_scheduler_cli.py` | `tick` / `plan`(Dry Run) / `status` / `run` / `recover-stale`。tickのロックファイル付き |
| 新規 | `deploy/scheduler/*` | systemd service/timer（tick・OFAC専用枠）、cron例 |
| 新規 | `docs/production-scheduling-runbook.md`（正本。旧`production-operations-runbook.md`はDEPRECATED） | Runbook |
| 新規 | `tests/test_geo_scheduler_tick.py` | 34件 |
| 変更 | `geo_scheduler_service.py` | 依存チェックを緩和（下記）、IMF/World Bank実行（対象国設定時のみ）、日時解析の不具合修正 |
| 変更 | `config/scheduler_jobs.yaml` | tick設定（間隔・heartbeat許容・cooldown）、IMF/World Bankの`params` |
| 変更 | `geo_scheduler_api.py` | `/health`をheartbeatに基づく内容へ |
| 変更 | `geo_operations_service.py` / `operations-dashboard.tsx` / `geoOperationsApi.ts` | /geo/operationsにScheduler状態・Job状態・stale running・Action Queue |
| 変更 | `geo.episode-candidates.tsx` | Cluster一覧の並び替え（新しい順・古い順・根拠の多い順）、表示上限50→200 |
| 変更 | `tests/test_geo_scheduler_service.py` / `tests/test_geo_operations.py` | 設計変更に合わせて更新 |

## 設計判断
- **Job一覧**: COLLECTION_GDELT_DOC / COLLECTION_GDELT_STREAM / COLLECTION_IMF / COLLECTION_WORLD_BANK / COLLECTION_OFAC（UN Comtrade・WTO・Manifestoは資格情報なしで無効）。
  下流（Signal→Event→Episode Candidate→Cluster再計算→Situation Link）はGDELT stream Jobの内部で実行される（既存コード）ため、`build_*`の別Jobは作っていない。
- **対象**: Phase Aの AUTO_ACTIVE（IMF・OFAC・World Bank・GDELT）のみ。MANUAL_ONLYの105件は自動収集しない。
- **Cadence**: stream=15分／DOC=毎日（ギャップは自動catch-up）／OFAC=毎日・専用枠・別timer／IMF・World Bank=四半期。
- **状態の保存**: `geo_ingestion_runs`のみ（`scheduler_job`行＝Job結果、`scheduler_heartbeat`＝稼働の印、1行を更新）。
- **lock**: Job単位は上限内のrunning行（DB）、tick単位はロックファイル。OFACは別枠。
- **retry**: retryable（ネットワーク・429・5xx）／non-retryable（認証・スキーマ・無効）を分類。回数・待ち時間はYAMLの一箇所。上限で`exhausted`、6時間後に自動再開。無限retryなし。
- **stale回復**: Jobごとの上限時間（DOC 3h／stream 2h／OFAC 48h）を超えたrunningを、tickの冒頭で`failed`（`STALE_RUNNING_RECOVERED`）へ補正。過去に取り残された行も対象。成功扱いにしない。
- **依存チェックの変更**: 以前は「依存Jobの直近実行が成功していること」を要求していた。これでは1つのquery失敗でstreamも止まるため、
  **「依存Jobが実行中の間だけ待つ」**に変更（1 Sourceの失敗で全Pipelineを止めない）。
- **Human Gate**: Schedulerのコードは`cluster_status`・Theme同期に触れない（テストで検証）。THEME_CANDIDATE_SYNCはmanual_only。

## 実データで見つけた不具合（修正済み）
DBのタイムスタンプは小数秒の桁数が一定ではなく（例 `.67346` は5桁）、Python 3.10の`fromisoformat`が解釈できずtickが落ちた。
日時解析を桁数に依存しない形に直し、運用画面側（以前は解析失敗を黙って無視していた）も同じ解析に揃えた。回帰テスト追加。

## 実データE2E（2026-10-06）
- **Schedulerの起動 → 収集**: `tick --only COLLECTION_GDELT_STREAM`を実DBで実行。周期判定で「前回成功から15.7時間」→実行。
  **70バッチ（約27万件の生レコード）を処理し、catch-upのチェックポイントを17.5時間分進めた**（success状態）。
- **stale回復**: 17.2時間「実行中」のまま取り残されていた行を、tickが自動で`failed`（STALE_RUNNING_RECOVERED）へ補正。補正後に通常実行できた。
- **冪等性**: 同じ範囲をもう一度実行（`run COLLECTION_GDELT_STREAM`）しても、Signal・Event・Episode Candidate・Clusterの件数は変わらない（7729/634/280/6/49+2）。
- **Human Gate**: 実行前後で accepted=2・reviewed=0・candidate=49 のまま。Review backlogで止まっている。
- **注意（正直な結果）**: 今回の期間は、Signalに一致するEventが**0件**で、新しいEpisode Candidate/Clusterは生まれなかった
  （一致率は元々低い。Phase Aは26日で69件）。このため「Collection→…→Cluster」の**新規生成**は今回の実行では観測できていない。
  下流の実装（既存コード）の挙動はPhase Aの実データ（280件のActor Episode Candidate）と既存テストで確認済み。
- **モックE2E（実DB・`is_demo=true`で隔離）**: ネットワーク失敗→retryable→backoff中は再実行されない／最大回数でexhausted／
  実行中は二重起動しない／stale回復／heartbeatは1行のまま、を確認。
- **Browser**: /geo/operations でScheduler状態（未設定）・Jobごとの状態・収集・Review backlog・Theme同期・Monitoringを確認。エラーなし。
  Episode Reviewの並び替え（新しい順/古い順/根拠の多い順）も確認。
- **手動tickのheartbeat**: 人が実行したtickも稼働の印を書くため、検証では本番側を「稼働中」と誤表示した。
  `tick --no-heartbeat`を追加し、今回書かれた行は`scheduler_heartbeat_e2e`へ切り離した（本番は「未設定」の実態に戻っている）。

## 既知の制約・残課題
- **本番登録**: 本番Linuxサーバ未構築のため、timer/cron未登録（PRODUCTION DEPLOYMENT PENDING）。登録手順は`docs/production-scheduling-runbook.md`（正本。旧`production-operations-runbook.md`はDEPRECATED）。
- **GDELT News DOCの429**: 実データで`GDELT status=429`（1回/5秒の制限）による失敗が多発し、記事データ（Signal）が2026-10-02を最後に増えていない
  （画面は「データ更新なし」）。Schedulerは429をretryable（backoff付き）として扱うが、**DOCコネクタ側の呼び出し間隔の制御は既存コードのまま**。
  複数の実行（手動・API）が同時にGDELTを叩くと悪化する。次の候補: DOCのrate limit対応。
- IMF/World Bankは対象国（theme_id/entity_id/country_code）が未設定のため`not_configured`（人が決める）。
- OFACは実行中の行が約21時間（上限48時間）。完了を確認していない。
- 通知（メール/Slack/Teams）は次フェーズ。
