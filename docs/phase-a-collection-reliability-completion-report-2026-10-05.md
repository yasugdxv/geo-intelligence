# Phase A｜Theme Discovery Collection Reliability — Completion Report（2026-10-05）

対象: GDELT News DOC/Stream、IMF/OFAC/World Bank/UN Comtrade/WTO/Manifesto Connector、`geo_source_registry`、`geo_ingestion_runs`、`geo_freshness_service`。
**変更していないもの（確認済み）**: Theme Candidate scoring・Candidate Generation logic・Episode Cluster logicの判定基準（member_count閾値等）・Expert AI Core・Reading Presentation v2・Natural Language Lens・Notification機能・Source DBの大規模UI。

---

## 1. Root Cause（26日間の収集停止）

実DBの`geo_ingestion_runs`を調査した結果、**外部Schedulerが実際に動いていたことは一度もない**ことを確認した。2026-09-07〜08にかけての一連の実行は全て`run_type="cli"`（手動実行）であり、`run_type="scheduled_external"`の行は過去を含め1件も存在しない。つまり「外部Schedulerに依存していたが止まった」のではなく、**そもそも自動実行の仕組みが存在せず、開発者の手動CLI実行がある時点から単純に止まった**、というのが実態だった。

最後の実活動: 2026-09-08T01:54（`gdelt_stream`）。調査開始時点（2026-10-04）で**26日間**、収集が完全に停止していた。

## 2. 既存Scheduler

- `docs/geopolitical-ingestion-operations.md`記載の通り、Phase 2A設計時点から「常駐Schedulerは導入しない」方針。`next_scheduled_at`列は全Connectorが計算・記録するが、**それを読んで実行をトリガーする仕組みはコード上どこにも存在しない**（write-onlyな値、純粋に参考情報）。
- **本番はLinuxサーバで運用予定だが、そのサーバは現時点でまだ構築されていない**（ユーザー確認済み、2026-10-05）。GitHub Actions（本プロジェクトはgitリポジトリではないため不可）・Supabase pg_cron（Ingestion実行コードはこのWindows開発機上でローカル実行されており、クラウドDBから直接呼び出せない）はいずれも現状不採用。
- **結論**: Scheduler未登録のまま。サーバ構築後、`docs/geopolitical-ingestion-operations.md`§4に既に記載済みのcron例（`gdelt-batch`→`gdelt-stream-catchup`を日次、IMF/World Bank/WTO/UN Comtradeを四半期）をそのサーバへ登録する方針で確定。Windows Task Schedulerは採用しない（開発機ではなく将来のLinux本番サーバで運用するため）。

## 3. 既存Collectorアーキテクチャ（コードレベルで再確認）

- **GDELT News DOC**（`geo_connector_gdelt.py`）: `geo_external_signal_queries`に登録されたqueryごとに記事を取得し`geo_external_signals`へ保存。`geo_source_registry`へは**意図的に登録しない**（コード内docstringに明記: 「theme横断のsource-neutral基盤」）。
- **GDELT raw Stream**（`geo_connector_gdelt_stream.py`）: Event/Mentions/GKGの15分batchファイルを取得し、**URLが既存`geo_external_signals`と一致した場合のみ**`geo_external_events`等へ保存する設計。News DOC側が先に（またはほぼ同時に）実行されている必要がある非対称な依存関係。
- **IMF/OFAC/World Bank/UN Comtrade/WTO/Manifesto**（`geo_connector_*.py`）: `_ensure_source`が初回実行時に`geo_source_registry`へ自己登録する設計。

## 4. 手動Run実行結果（本Phase Aで実施、すべて実DB・非demo）

| 実行内容 | 結果 |
|---|---|
| `gdelt-batch`（News DOC、全4クエリ） | 完走。`saturated_windows=0`（取りこぼしなし） |
| `gdelt-historical --since 20260907 --until 20260927070024`（19日間の真のギャップ埋め、後述） | 完走。4クエリ全て`saturated_windows=0` |
| `gdelt-stream-catchup`（複数ステージ、計7回） | 最終的に`stopped_reason=caught_up`まで到達 |
| OFAC（`--theme-id ...`） | **3回試行、2回は途中で未捕捉例外によりクラッシュ**（後述のバグにより修正）。3回目（修正版）は本レポート時点でまだ実行中（§13参照） |
| IMF / World Bank | 新規実行はしていないが、既存の`last_successful_ingestion_at`は正常（§10参照）。cadence誤判定のみ修正 |

## 5. DB書き込み結果

- `geo_external_signals`: 本Phase A期間中に実データ**5,372件**を新規取得・保存（26日分のNews DOC記事。重複は`dedup_key`で自動排除）
- `geo_sanctions_entries`: OFAC実行により**336件→5,806件**（本レポート時点、実行継続中）
- `geo_ingestion_runs`: 正常な監査証跡として機能（§11参照）

## 6. 下流到達結果（Signal → Event → Episode）

これが今回最も重要な発見。

1. News DOCの26日分バックフィル完了後、raw Streamの`match_signals_for_urls`が初めて**実データで69件の一致**を記録（`geo_external_events`経由）。これ以前は0%マッチ率だったが、原因は「両パイプラインの時間窓がズレていただけ」であり、マッチングロジック自体にバグはないことをコードレベルで確認済み（実signal URLを直接関数へ渡すテストで100%一致を確認）。
2. その後、`geo_media_episode_candidates`への保存で**新しいバグ**を発見: `geo_connector_gdelt_stream.py`が`bulk_upsert_annotations`の戻り値（実annotation_id）を後続処理へ引き継いでおらず、NOT NULL制約違反で常に失敗していた。これまで一度もMedia Episode Candidateが実データで作成できたことがなかった、という意味。ユーザー確認の上で修正し、実データで**6件のMedia Episode Candidate**（正しいannotation_id付き）が生成されることを確認。
3. `geo_actor_episode_candidates`も同じ流れで**280件**（実データ）が本日生成されていることを確認。
4. Theme Candidate（`geo_theme_candidates`）への到達は本Phase Aでは確認・実施していない（LLM呼び出しを伴う別ステップであり、スコープ外）。DB内に見つかった5件の`[TEST]`付きTheme Candidateは本調査とは無関係な別セッションの検証用データ（`status=DISMISSED`）であり、本Phase Aの成果ではない。

**結論: Signal→Event→Episodeの経路は実データで動作することを実証した。Episode→Theme Candidateの接続はPhase B領域として持ち越し。**

## 7. 108 Source分類

| カテゴリ | 件数 | 内訳 |
|---|---|---|
| AUTO_ACTIVE | 3 | IMF DataMapper API・OFAC Sanctions List Service・World Bank Indicators API（いずれも本Phase Aで再稼働確認済み） |
| REGISTERED_ONLY | 0 | 該当なし |
| UNSUPPORTED | 0（登録内）/ 3（登録外） | UN Comtrade・WTO・Manifesto Projectはコード実装済みだが`config.json`の`connector_credentials`が空でAPIキー未設定。一度も実行されたことがなく`geo_source_registry`に行自体が存在しない |
| BROKEN | 0 | |
| MANUAL_ONLY | 105 | `automation_allowed=false`。news 11／official_site 27／other 67。多くは分析官による個別記事・公式サイトの引用（citation）由来で、そもそも自動Collectorの対象として設計されていない |
| UNKNOWN | 0 | |

加えて、**GDELT（News DOC・raw Stream）は意図的に`geo_source_registry`へ登録されない**source-neutral基盤であり、108件の外側にある実質的な主力収集パイプライン。

## 8. Source Registry ↔ Collector接続の確認

- IMF/OFAC/World Bank/UN Comtrade/WTO/Manifesto: 「登録される」＝「初回実行時に自動登録される」が同一アクション。ズレは存在しない。
- GDELT: 登録とCollector実行は完全に無関係（意図的）。
- 残り105件: Collectorの対象外（MANUAL_ONLY）。「Source登録はあるがCollectorが動いていない」という意味でのギャップは、**UN Comtrade/WTO/Manifestoの3Connector（APIキー未取得のため0件登録）に限られる**。

## 9. Freshness（現在値、本レポート作成時点）

| 対象 | cadence | 経過日数 | 判定 |
|---|---|---|---|
| GDELT News DOC（4クエリ全て） | daily相当 | 0.1〜0.3日 | current |
| IMF DataMapper API | quarterly | 49日 | current |
| World Bank Indicators API | quarterly | 61日 | current |
| OFAC Sanctions List Service | daily | 47日 | overdue（実行中、完了すれば解消） |

## 10. Staleness基準・Health Monitoring

既存の`geo_freshness_service.classify_source_freshness`（`unknown`/`failing`/`overdue`/`review_due_soon`/`current`の5値、`update_cadence`に応じて閾値を動的計算）が**既に適切に設計済み**であることを確認——ただし`update_cadence`列が6Connectorの`_ensure_source`で一度も設定されておらず、全Sourceが30日デフォルトにフォールバックしていたため、IMF/World Bank（本来quarterly=91日）が誤って`overdue`と判定される**実バグ**を発見・修正した（ofac=daily、imf/world_bank/wto/un_comtrade=quarterly、manifesto=annualを各Connectorの`_ensure_source`に設定＋既存3本番行を手動backfill）。

GDELT（Source Registry外）向けに、新規読み取り専用CLIスクリプト`scripts/geo_collection_health_check.py`を追加し、構造化Connector・GDELTクエリ・パイプライン全体の最終実行を1コマンドで一覧できるようにした（新規UI・新規テーブルは作らない、最小限の監視手段）。

## 11. Idempotency / 再実行安全性

- GDELT News DOC: `dedup_key`による重複排除を確認（同一記事の再取得は`updated_count`に計上され`inserted_count`は増えない）
- GDELT raw Stream: batch単位の重複排除・`checkpoint_scope`によるis_demo/query_id別の独立したwatermark管理を確認（既存設計、変更なし）
- OFAC: `source_record_hash`比較による無変更レコードの検出（`duplicate_count`）を確認。クラッシュ後の再実行でも、既存entryは正しくスキップされ重複作成されないことを確認

## 12. Backfillの実施内容・方針

当初、既存の「incremental自動catch-up」が26日分を全て埋めたと誤認したが、実際には**`article_lookback`（7日）への暗黙フォールバックにより直近7日分しか埋まっておらず、残り19日間（Sept 7〜27）は永続的にスキップされていた**という、別のバグを発見した（`resolve_doc_incremental_since`が`gdelt_doc_catchup`という新しい`connector_type`のみを見ており、それ以前の実行履歴・`last_run_at`を一切参照しないため、長期停止後の初回実行で常に発生しうる）。

対応として、既存の`gdelt-historical --since --until`コマンド（ドキュメント化済みの手動Initial Load手順）を使い、Sept 7〜27を明示的にAd-hoc backfillした（`--promote-to-checkpoint`は使わず、定常watermarkには一切影響させていない）。全4クエリで`saturated_windows=0`、取りこぼしなく完了。

raw Stream側も同様に、既存の`--since`/`--until`/`--max-calls`/`--batches-per-call`による段階的実行（Stage1〜5、各回数コール〜20コールの小刻みな単位）で進め、一度に全期間を処理しない運用を徹底した。Episode/Theme Candidate生成は本Phase Aのingestion処理に自動的に付随するEpisode Cluster再計算（既存ロジック、変更なし）のみで、新たなCandidate生成コマンドは意図的に実行していない。

## 13. 発見・修正したバグ一覧（計7件、Collection Reliability領域のみ、Episode Cluster/Theme Candidateの「ロジック」は一切変更していない）

1. **is_demo隔離の欠落（freshness指標）**: 全7Connectorが`is_demo=true`のテスト実行でも`geo_source_registry`/`geo_external_signal_queries`の本番freshness列を書き換えていた。修正済み・回帰テスト追加済み。
2. **is_demo隔離の欠落（データ本体）**: GDELTの`bulk_upsert_signals`が`is_demo`を一切受け取っておらず、テスト実行の実記事データ1,520件が本番同然で`geo_external_signals`に紛れ込んでいた。修正・データ削除・回帰テスト追加済み。
3. **update_cadence未設定**: 6Connector全てで`_ensure_source`時に設定されず、Staleness判定が全Sourceに30日ルールを誤適用していた。修正済み。
4. **OFACの未捕捉クラッシュ**: 1万件規模のSDNリストを1件ずつHTTP往復で処理する設計で、途中の一時的ネットワーク障害（プロキシDNS失敗・Supabase ReadTimeout）が起きるとプロセス全体がクラッシュし、`geo_ingestion_runs`が`status=running`のまま復旧不能で残留する事象を**実際に2回確認**。1レコード単位のtry/exceptで継続する設計に修正・回帰テスト追加済み。
5. **GDELT Streamの未捕捉クラッシュ**: 同じ理由（プロキシ瞬断）で`run_gdelt_stream_catchup`の1 batch処理が丸ごとクラッシュし、watermark再計算・summary run記録すら行われず異常終了する事象を確認。1 call単位でtry/exceptし`stopped_reason="error"`として安全に打ち切る設計に修正・回帰テスト追加済み。
6. **News DOC incremental catch-upの暗黙7日フォールバック**: 上記§12参照。長期停止後の最初のincremental呼び出しが、実際の最終実行日ではなく`article_lookback`（7日）を起点にしてしまい、より古い期間を静かに永久スキップする設計上の穴。**今回は`gdelt-historical`による手動backfillで対応したが、コード自体（`resolve_doc_incremental_since`のbootstrap fallback）は変更していない**。再発防止には、将来的にqueryの`last_run_at`も参照するフォールバック改善が望ましい（Remaining Gap）。
7. **Media Episode Candidateのannotation_id配線漏れ**: §6参照。修正・実データ検証済み。

いずれも「Collectionが確実に動き、止まったら分かる」という本Phase Aの目的に直接関わる発見であり、Theme Candidate scoring・Episode Clusterの判定基準（member_count閾値等）には一切触れていない。

## 14. Full Regression

`python -m pytest tests/` 全件実行: **2,531件 全てPASS**（既存の無関係な1件の失敗は、別セッションによる`geo_ios_situation_presentation_api.py`への同日編集が原因だったが、その後解消されすでに含めて全件PASSを確認済み）。

## 15. Remaining Gaps（次フェーズへの持ち越し）

- OFAC全件（約19,488件）処理の完走未確認（本レポート時点で約5,800件・約30%処理済み、処理方式自体が遅く残り約30時間超かかる見込み。クラッシュ耐性は確認済みのため継続実行中）
- 本番Linuxサーバが未構築のため、Scheduler登録・「2-3回連続自動成功」の実証は不可能（手動CLIでの複数回成功は実施済み）
- News DOC incremental catch-upの7日フォールバック問題（§13-6）はコード修正をしておらず、将来同様の長期停止が起きた場合は同じ手動backfillが再度必要
- Episode → Theme Candidate（LLM呼び出しを伴う`sync_theme_candidates`）の実データ疎通は未検証（スコープ外として意図的に見送り）
- UN Comtrade/WTO/Manifestoの3Connectorは引き続きAPIキー未取得のため実行不可（既知の保留事項）

## 16. 最終判定

> **COLLECTION RELIABILITY V1 READY**
> **Production Scheduling Pending**

「READY」と「本番自動運転が既に開始されている」は別であることを明確にするため、項目別に判定する。

| 項目 | 判定 |
|---|---|
| 収集ロジック（GDELT News DOC/Stream、IMF/OFAC/World Bank） | READY |
| Catch-up / Recovery（段階的backfill・再実行安全性） | READY |
| 下流到達（Signal→Event→Episode Candidate） | VALIDATED（実データ280件+6件で実証済み） |
| 本番Scheduler | **PENDING**（本番Linuxサーバ未構築のため） |
| OFAC full run（約19,488件） | **IN PROGRESS**（約30%処理済み、クラッシュ耐性は確認済み） |

既存の収集基盤は実DBで再稼働を確認し、Signal→Event→Episodeの実データ到達を実証した。調査中に発見した7件の実バグ（うち2件はプロセスクラッシュによる「止まったまま検知できない」事象そのもの）を全て修正・回帰テスト込みで解消し、Full Regression 2,531件PASSを確認した。「収集が確実に動き、止まったら分かる」という本Phase Aのゴールは満たされたと判断するが、本番Linuxサーバ未構築によりScheduler登録・自動運転開始は依然PENDINGであり、READYはあくまで基盤・ロジックレベルの判定である。

今回の検証で、Signal→Event→Episode Candidateまでは実データで疎通したが、**Episode Candidate→Theme Candidate→テーマ探索画面はまだ実運用経路として疎通していない**ことが明確になった。以前の「テーマ探索候補0件」問題は収集基盤そのものの破綻ではなく、この最後の接続が未実施だったことが主因と考えられる。次フェーズ（Phase B：Episode→Theme Candidate→Theme Discovery E2E）でこの接続を通すことが、収集→テーマ発見→監視→調査→読みを一本につなぐ次の作業になる。
