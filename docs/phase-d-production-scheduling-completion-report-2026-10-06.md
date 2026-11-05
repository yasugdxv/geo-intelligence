# Phase D｜Production Scheduling / Daily Operations — Completion Report（2026-10-06）

> **正本（Canonical）**: 本書が Phase D の Completion Report の唯一の正本です（Phase D.1 で整理）。
> Runbook=`production-scheduling-runbook.md`／Design・Change Log=`PHASE_D_PRODUCTION_SCHEDULING_2026-10-06.md`／Freeze=`freeze/PRODUCTION_SCHEDULING_V1/`。
> 本文は Phase D 完了時点の記録で、**後から変わった事実は「§22 Phase D.1 追補」と各所の【D.1訂正】に記載**しています。

**注記（並行作業について）**: Phase D実装中、同一プロジェクトで複数のClaude Codeセッションが並行して作業していたことが判明した。Daily Operations Dashboard（`geo_operations_service.py`/`geo_operations_api.py`/フロントエンド）は別セッションが実装し、Scheduler本体（`geo_scheduler_service.py`）も途中から別セッションが`geo_scheduler_tick.py`（tick実行・heartbeat）・`geo_scheduler_cli.py`（OS入口）として並行拡張した。本報告は、自分（本セッション）が担当した部分（Job Registry設計・`geo_scheduler_service.py`の基礎実装・GDELT run_type修正・テスト・Runbook・最終検証）と、他セッションが補完した部分（tick/heartbeat機構・CLI）の両方を含む、プロジェクト全体としての完了状態を報告する。

---

## 1. Scheduling Architecture

```
【自動（Scheduler対象）】
External Sources → Collection(GDELT DOC/Stream, IMF, World Bank, OFAC)
                 → Signal → Event → Episode Candidate → Episode Cluster Candidate
【Human Gate（Scheduler は絶対に自動実行しない）】
Episode Cluster Candidate → Accept / Reject
【半自動（Phase C UI、Scheduler登録対象外）】
Accepted → Theme Candidate Sync → Theme Discovery → Monitoring/Research/Expert AI
```

downstream（Signal→Event→Episode Candidate→Episode Cluster→Situation Link）は`COLLECTION_GDELT_STREAM`1Job内に埋め込み済み（既存`run_gdelt_structured_ingestion`/`run_gdelt_stream_catchup`）。独立した"BUILD_*"ジョブは存在しない——ユーザー例の命名は内部フェーズとして記録するのみに留めた。

## 2. Scheduler Status

**Scheduler Ready / PRODUCTION DEPLOYMENT PENDING**。本番Linuxサーバは未構築（Phase A確定事項）のため、OSレベルのcron/systemd timer登録は実施していない。一方、コード・設定・CLI・テストは完成しており、開発機上で`geo_scheduler_cli.py tick`を手動実行すると実データで正しく動作することを確認済み（§16参照）。`GET /api/geo/scheduler/health`は、heartbeat（`scheduler_heartbeat`の1行）が一度も無ければ`not_configured`を返す（「正常」と誤認させない、item37）。**【D.1訂正】** Phase D完了時は「常に`configured:false`」だったが、tick/heartbeat機構の追加後はheartbeatに基づいて`not_configured`/`healthy`/`unhealthy`を返す。手動で実行したtickが「稼働中」と誤表示しないよう`tick --no-heartbeat`がある（Runbook §3.3）。

## 3. Job一覧

| Job ID | connector_type | 状態(本報告時点) |
|---|---|---|
| COLLECTION_GDELT_DOC | gdelt / gdelt_doc_catchup | 稼働中、最終成功あり |
| COLLECTION_GDELT_STREAM | gdelt_stream / gdelt_stream_catchup | 稼働中（depends_on: GDELT_DOC） |
| COLLECTION_IMF | imf | **not_configured**（対象entity未設定、§7） |
| COLLECTION_WORLD_BANK | world_bank | **not_configured**（同上） |
| COLLECTION_OFAC | ofac | 稼働中（専用枠、stale閾値48h） |
| COLLECTION_UN_COMTRADE/WTO/MANIFESTO | - | **disabled**（Phase A分類: UNSUPPORTED、credential未取得） |
| THEME_CANDIDATE_SYNC | theme_candidate_sync | manual_only_jobs（Scheduler対象外、Human Gate後） |

## 4. Cadence

GDELT Stream=高頻度（15分grid）、GDELT DOC=daily、IMF/World Bank=quarterly、OFAC=daily（専用枠、重いため同時実行しない）。全Sourceに同一cron式は適用していない。

## 5. Collection Health

既存`geo_freshness_service.classify_source_freshness`・`geo_source_registry`のAUTO_ACTIVE/MANUAL_ONLY/UNSUPPORTED分類（Phase A）をそのまま再利用。Dashboard（`/geo/operations`）が問題のあるSourceを優先表示する設計（別セッション実装）。

## 6. Retry / Recovery

- retryable/non_retryableの分類は実機で確認済みのエラーパターン（ProxyError/ReadTimeout等=retryable、credential_missing等=non_retryable）に基づく。無限retryは禁止、max_retries/backoffはJob定義（`config/scheduler_jobs.yaml`）に集約、コード中への散在なし。
- stale検知（`detect_and_mark_stale_jobs`/`recover_stale`）は自動的に成功扱いにしない——`failed`へ補正するのみ。Job種別ごとにstale閾値を変える（OFAC=48h、他=2〜3h）。

## 7. Catch-up

GDELT News DOCの「7日で暗黙打ち切り、19日分を永久スキップ」バグ（Phase A発見）はConnector自体のコードを変更せず、Scheduler側の`compute_catchup_gap`が`geo_external_signal_queries.last_run_at`から真のgapを計算し、7日を超える場合は段階的backfillを推奨する設計で回避した。**【D.1訂正】** その後の調査で、記事が増えなくなった**真の原因は別**だと分かった: DOCのcatch-upは取得に失敗した窓（429等）でもcheckpointを進めていたため、失敗した期間が二度と再取得されなかった。Phase D.1でConnectorを修正し、失敗した窓があればcheckpointを止める（§22）。

## 8. Lock / Idempotency

Job Lockは新規table無しで、既存`geo_ingestion_runs.status='running'`行をそのまま使う。tickの多重起動防止はOSレベルのファイルlock（`geo_scheduler_cli.py`）で別途対応。既存のSignal/Event/Episode Candidate/Episode Clusterの重複防止ロジックは変更していない（Phase A/Bで検証済みのものをそのまま利用）。

## 9. Downstream到達

実データで、手動Trigger経由のGDELT DOC実行→Signal取得→（依存先）GDELT Streamの自動実行までを確認（§16）。

## 10. Human Gate

`run_job`/`execute_job`は、`manual_only_jobs`（`THEME_CANDIDATE_SYNC`）をjob_idとして渡すと`SchedulerError`で明示的に拒否する実装・テストを確認済み。Episode Cluster Candidateのaccepted遷移は一切自動化していない。

## 11. Review Backlog

Phase C時点のPipeline Statusに加え、Daily Operations Dashboard（別セッション実装）がReview待ち49件の並び替え（oldest/newest/evidence count等）・region/episode typeの絞り込みを提供。新しいAI判定での優先度付けは行っていない。

## 12. Theme Sync Backlog

Phase Cの個別/一括Syncボタンをそのまま維持。Scheduler/tickからは一切自動実行しない（item13/16遵守）。

## 13. Monitoring Alert

`geo_operations_service.build_monitoring`がDashboard上でAlertを表示（別セッション実装、既存Alert判定ロジックは変更なし）。

## 14. Daily Operations UI

`/geo/operations`（別セッション実装）。Pipeline Health・Action Queue・Review/Theme同期/Monitoring統合表示。本セッションは直接編集していないが、`/api/geo/scheduler/*`との連携可能性を確認済み。

## 15. Runbook

[docs/production-scheduling-runbook.md](production-scheduling-runbook.md) 作成済み。日次確認・障害時対応・再実行・Catch-up・Scheduler再起動・IMF/World Bank対象国設定（既知の未設定事項）・本番導入手順案・Freeze資産保護を網羅。

## 16. 実データE2E

- `GET /api/geo/scheduler/health` → `configured:false`、Job数8件・enabled 5件を実データで確認
- `GET /api/geo/scheduler/jobs/COLLECTION_GDELT_DOC/dry-run` → `would_run:true`、`gap_days:0`（DB変更なし確認済み）
- `POST /api/geo/scheduler/jobs/THEME_CANDIDATE_SYNC/run` → **400で拒否**（Human Gate遵守を実データで確認）
- `POST /api/geo/scheduler/jobs/COLLECTION_GDELT_DOC/run`（`X-IOS-Credential`付き手動実行）→ 実LLM/実API呼び出し成功、`run_type=manual`・`requested_by=pilot_approver`で正しくタグ付け（Scheduler本来の`scheduled_external`とは区別）
- `python geo_scheduler_cli.py status`/`plan`/`recover-stale --dry-run` → 全てDB変更なしで正しい状態を返却、OFACの古い`running`行（Phase Aの残留、約20時間経過）を48h閾値未到達として正しく「まだstaleではない」と判定
- GDELT Streamの実tickが稼働中であることを確認（他セッションまたは手動実行による実ティック、heartbeat 1分以内）

## 17. Failure E2E

`tests/test_geo_scheduler_service.py`でモックベースのネットワーク障害・stale running・retry分類を確認（本番Sourceを意図的に壊すことはしていない）。実データでは、OFACの実際のクラッシュ残留行（Phase A由来）がstale閾値未到達として正しく保持されることを確認。

## 18. Full Regression

`python -m pytest tests/`: **2,689件 全PASS**（0失敗）。【D.1訂正】最終のFreeze時点は2,690件。D.1完了時の件数は§22。`tsc --noEmit`・`npm run build`とも成功（0エラー）。Theme Discovery/Episode Candidates/Monitoring/Source Library/Expert AI/Reading UI/Report Studio関連ファイルは本Phase Dで一切編集していない（純粋な追加のみ）。

## 19. Production Deployment Status

**PRODUCTION DEPLOYMENT PENDING**。本番Linuxサーバが存在しないため、systemd timer/cron等へのOSレベル実登録は行っていない。Runbook §8に導入手順案を記載済み、サーバ構築後すぐ着手できる状態。

## 20. 残課題

- IMF/World Bankの対象国（entity_id/country_code）が未設定（業務判断待ち、§7）
- OFACの古いrunning行（Phase A由来、2026-10-05 04:08開始）はstale閾値48h未到達のため残存中——48h経過後は`recover-stale`で自動補正されるか、手動で先行対応可能
- 本番Linuxサーバ構築後のOSレベルScheduler実登録（Runbook §8に沿って実施）
- 【D.1訂正】`/geo/operations`とScheduler APIの統合は、`geo_operations_service.build_collection`が`scheduler_overview`を取り込む形で完了（Scheduler状態・Job状態・stale・GDELT休止を表示）

## 最終判定

> **Phase D: Scheduler Ready / Production Deployment Pending**

「日次運用として止まらず回る」ための基盤（Job定義・lock・stale検知・catch-up・retry方針・Human Gate・Runbook・手動実行・Dry Run）は実データで検証済み。本番サーバ構築という外部要因のみが残課題であり、それ以外のPhase Dの目的は達成されたと判断する。

---

## 22. Phase D.1 追補（Hardening、2026-10-06）

**判定は変わらない: SCHEDULER READY / PRODUCTION DEPLOYMENT PENDING**（本番Linux未構築、timer/cron未登録）。

### 22.1 文書の正本化（A）
- Runbookは `production-scheduling-runbook.md` に統合（実装と照合し、誤りを訂正）。`production-operations-runbook.md` は DEPRECATED/superseded の参照ファイル。
- 旧Runbookの誤り: IMF/World Bankの設定キー（実装は`params: {theme_id, entity_id, country_code}`）、catch-upの原因説明（上記【D.1訂正】）。
- Freeze `PRODUCTION_SCHEDULING_V1` のManifestに Canonical documents・Freeze provenance・更新履歴を追記（Freezeのコードは不変）。

### 22.2 Freezeの内容レベルの確認（A-3）
- SHA整合: 25/25（provenance追加後。初回は24件）。
- snapshotの`src/`だけを元の構成に上書きした一時ツリーで、Scheduler関連テスト **151件PASS**。Manifestの設計要点がコードと一致することを確認。
- **git**: HEADは2026-09-25のまま。Phase A〜Dの成果物の多くは**未コミット**（変更111・未追跡463）で、gitからはFreezeの内容を再現できない。再現の根拠は `SHA256SUMS.txt` とsnapshotの`src/`。

### 22.3 GDELT News DOC 429（B）
原因・対策・確認方法は Runbook §6。要点:
1. 間隔制御がプロセス内だけ（複数プロセス・スレッドで同時アクセス）、Retry-Afterを読まず、429で高速に連打。
2. **失敗した窓でcheckpointが進み、その期間が二度と再取得されなかった**（記事が10月2日以降増えない最大の原因）。
3. 対策: 全プロセス共通の最低6秒間隔＋429の全体クールダウン（Retry-After尊重、無ければ30/90秒、3回で10分休止）、収集の排他リース（定期・catch-up・手動の同時実行を防ぐ。API=409、Scheduler=skip）、失敗した窓でcheckpointを止める。

詳細（実データでの確認結果・残課題）は本節の末尾「22.4」を参照。
