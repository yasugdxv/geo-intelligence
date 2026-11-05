# Phase E｜Daily Intelligence Brief / Notification — Completion Report（2026-10-06）

**注記（並行作業について）**: 本Phase中、別セッション（python-f4）がPhase D.1（Scheduler/Connector/Runbook/Freeze provenanceの正本化）を並行して担当することを確認し、対象ファイル（`geo_connector_gdelt.py`・`geo_scheduler_*.py`・`config/scheduler_jobs.yaml`・`docs/production-*-runbook.md`・`docs/PHASE_D*`・`freeze/PRODUCTION_SCHEDULING_V1*`）には一切触れていない。本Phaseは新規ファイル（`geo_daily_brief_service.py`／`geo_daily_brief_api.py`／フロントエンド新規route・新規component）の追加のみで完結している。

目的: 「新しい分析AIを作る」のではなく、既存のインテリジェンス資産（Monitoring Alert / Theme Candidate / Episode Review backlog / Theme Sync backlog / Collection health）を、毎朝「今日どこを見るべきか」が分かる順序に整理すること。

---

## 1. Daily Brief UI

`/geo/daily-brief`（`DailyBriefPage`）。Operations（`/geo/operations`、「何を処理すべきか／システムは正常か」）とは別画面として新設した。構成は6部:
①今日のポイント ②重要な変化（Monitoring Alert） ③新しく見るべきテーマ（Theme Candidate） ④レビュー・判断が必要（Review/Theme Sync backlogの件数サマリ） ⑤システム上の注意（異常時のみ詳細、正常時は「運用上の異常なし」の一文） ⑥通知設定（画面表示フィルタのみ）。

サイドバーは「インテリジェンス」グループの先頭に追加した（既存の並び: テーマ探索→モニタリング→エキスパートAI相談→リサーチスタジオ→レポートレビュー→インテリジェンス運用、の直前）。既存route・既存グループ構造は変更していない。

## 2. API

`GET /api/geo/daily-brief`（`is_demo`・`since`はオプショナルクエリ）。1回の呼び出しで全セクションを返す（§33相当のフロントエンド複数呼び出しを避ける設計）。副作用は一切ない（GETのみ、Collectionの手動実行等とは独立）。

## 3. 選定ロジック（Selection logic）

決定論的固定優先順位（`_build_points`）: Critical/High Monitoring Alert → 新規Theme Candidate → accepted未Sync（Theme Sync backlog） → Evidence多いReview backlog → Collection異常。LLMに「何が重要か自由に選ばせる」実装はしていない——各カテゴリの有無・件数だけで並べる。

## 4. Manager Summary

「今日のポイント」上位3件のテキストをそのまま使う（`manager_summary`）。新しい文言は生成しない——AIが独自の経営影響を作り出さないようにするため。

## 5. Monitoring統合

`geo_operations_service.build_monitoring`（既存、未改修）が返す未確認Alert（`acknowledged_at is null`）をそのまま使う。各AlertにEvidence（`related_signal_ids` → `geo_external_signals`）を付与した。

## 6. Theme Candidate統合

`status=NEW`のTheme Candidateを`geo_theme_explorer_service.candidate_origin`（既存、未改修）で由来分類し、新しいものから最大5件表示。Evidenceは`geo_theme_explorer_service.attach_evidence_articles`と同じ参照パターン（`source_external_signal_ids` → `geo_external_signals`）で自前実装（Brief専用に軽量化、既存関数のロジック自体は変更していない）。

## 7. Article Discovery統合

`candidate_origin`が`"article_discovery"`のCandidateは「記事から発見」ラベルで区別表示する。実データで7件（後述の実DB確認時点）が該当し、全件Evidence付きで表示できることを確認した。

## 8. Review / Theme Sync backlogサマリ

全件は出さない。件数のみ: 「レビュー待ち49件、うちEvidenceが多い候補15件、最古27日前」（実データ、仕様の例文と同形）。Theme Syncは「承認済みのうちN件がテーマ未生成です」の一文＋既存Phase C機能（`/geo/operations`）への導線のみ。

## 9. Operations異常（システム上の注意）

異常が無ければ「運用上の異常なし」の一文のみ（正常時に「Collection正常」等を大きく出さない、という指示を遵守）。実データでは3件（データ更新停止・OFAC残留stale・gdelt失敗run）を検出し表示した。

## 10. 重複抑制（Duplicate suppression）

`geo_monitoring_themes.theme_candidate_id`（既存列）でAlertの元テーマとTheme Candidateを突き合わせ、該当するCandidateは「新しく見るべきテーマ」から除外し、Alert側に`related_theme_candidate_id`を付与する。新しいLLM突き合わせは実装していない。単体テストで、同一テーマがAlertとTheme Candidate両方に出ないことを確認済み（`test_duplicate_theme_is_suppressed_from_new_themes_when_already_an_alert`）。

## 11. Evidence

Alert・Theme Candidateのいずれも、記事タイトル・Source・公開日・URLを表示する（AI自身の説明だけで終わらせない）。実データのEvidenceリンクはブラウザで実際にクリック可能な外部URLとして表示されることを確認した（スクリーンショット確認済み）。

## 12. Research Studio handoff

新しいhandoffは作らず、「テーマ探索で見る」ボタンから既存の`geo_theme_highlight_candidate_id`（sessionStorage、Phase C既存機構）経由でTheme Discoveryへ遷移し、そこで既存の「詳しく調べる」（Research Studio作成）・「モニタリングに追加」ボタンをそのまま使う設計にした。実ブラウザで、Daily Briefのカードをクリック→Theme Discoveryの該当Candidate詳細が自動で開くことを確認した（スクリーンショット確認済み）。

## 13. Expert AI handoff

同様に新しい入力方法は作らず、Theme Discovery側の既存Expert AI Handoff（`geo_theme_candidate_handoff_service`／`find_or_create_session_for_theme_candidate`）に委ねる設計とした。Daily Brief自体からは直接のExpert AI起動ボタンは置いていない（Theme Discovery経由でのみ到達）——既存の一本の経路に統一し、二重実装を避けるための意図的な選択。

## 14. Cache / LLM呼び出し

- **LLMは一切呼ばない**。全セクションが既存のDB列・既存サービス関数の読み取りのみで構成される。単体テスト`test_build_brief_does_not_call_llm`で`geo_llm_common.call_llm_structured`が呼ばれないことを固定した。
- **Fingerprint**: 各セクションの件数・ID・タイムスタンプからSHA256を算出し`fingerprint`として返す（`daily_intelligence_brief_v1`のバージョンタグと併せて、将来の変更検知・UI側の差分表示に使える形にした）。ただし現時点ではDB読み取り自体を省略する最適化はしていない——本Phaseの読み取りはLLMを伴わない軽量な集計のみであり、省略の効果が小さいため（既知の制約として§18に記載）。
- **Seen/Unseen**: 新しいDB列は追加していない。Theme Candidateは既存の`status=NEW`、Alertは既存の`acknowledged_at`をそのまま「未確認」の判定に使う。「前回確認以降」はブラウザのlocalStorage（`geo_daily_brief_last_seen_at`）にのみ保持し、`since`パラメータとしてAPIに渡す。無指定時は直近24時間にフォールバックする。未確認のCandidate/Alertは、sinceの新旧に関わらず常に対象にしている（「未確認を優先」の指示どおり）。

## 15. Notification Preference（設計のみ）

画面右上に「すべて表示／要確認以上のみ／重要のみ」の選択を置いた。ブラウザのlocalStorageにのみ保存し、サーバへは送らない（DB migration不要）。現時点では「今日のポイント」の表示フィルタとしてのみ機能し、メール／Teams／Slack通知の実送信は行っていない（次フェーズ候補）。

## 16. 実DB E2E

| 確認項目 | 結果 |
|---|---|
| Monitoring Alert | 実データでは現在、未確認・非テスト(`[TEST]`でない)かつactiveなMonitoring Themeが無く、Alert 0件（Empty state経路）。ロジック自体は単体テストで検証済み |
| 新規Theme Candidate | 実データで5件表示（全件`article_discovery`起源、Evidence付き） |
| Article Discovery Candidate | 上記5件すべてが該当、「記事から発見」ラベルで表示確認 |
| Review backlog summary | 実データ「レビュー待ち49件、うちEvidenceが多い候補15件、最古27日前」を確認 |
| Theme Sync backlog | 実データで0件（Theme同期待ちなし）を確認 |
| Collection異常 | 実データで3件（データ更新停止・OFAC stale・gdelt失敗run）を検出・表示 |
| Theme Discoveryへの遷移・ハイライト | ブラウザで実クリック→該当Candidate詳細が自動で開くことを確認 |
| レビュー画面への遷移 | `/geo/episode-candidates`への遷移を確認 |
| 運用画面への遷移 | `/geo/operations`への遷移を確認 |

## 17. Browser E2E

Playwrightで`/geo/daily-brief`を実際に開き、5セクション全ての見出し・内容・Evidenceリンク・サイドバー項目の表示を確認した（フルページスクリーンショット取得済み）。「新しく見るべきテーマ」カードの「テーマ探索で見る」「レビューする」「運用画面で確認」ボタンの実クリック遷移も確認した。

## 18. Full Regression

- `python -m pytest tests/`: 新規`tests/test_geo_daily_brief.py`（15件、brief selection／alert inclusion／theme candidate inclusion／article discovery inclusion／duplicate suppression／review backlog summary／theme sync backlog／collection failure(有無両方)／empty state／evidence／partial API failure／LLM grounding／fingerprint変化・安定性／APIエンドポイントを網羅）を追加し、全体で**2,705件 全PASS**（既存2,690件 + 新規15件）。
- `tsc --noEmit`・`npm run build`とも成功（0エラー）。
- `freeze/check_all_freezes.py`: 既存5Freeze（READING_UI_V1／SOURCE_INTELLIGENCE_V1／SOURCE_INTELLIGENCE_V1_1／OPERATIONAL_WORKFLOW_V1／PRODUCTION_SCHEDULING_V1）すべて`frozen snapshot files N/N match`・`RESULT: Frozen snapshots unchanged`を確認。本Phaseでは新規Freezeは作成していない（Phase Eの完了をもって次回Freeze候補とするかはユーザー判断）。

## 19. Known limitations（次フェーズ候補を含む）

- 実データ上、非テスト・activeなMonitoring Themeが現状存在しないため、「重要な変化」セクションの実データE2EはEmpty state経路のみ確認できた（Alertありの経路はロジック・単体テストでのみ検証）。
- Cacheはfingerprint算出・バージョンタグ付けに留まり、DB読み取り自体をスキップする最適化はしていない（LLMを伴わないため影響は小さいと判断）。
- Notification Preferenceは画面表示フィルタのみで、メール／Teams／Slack通知の実送信・配信スケジュールは未実装（設計のみ、との指示どおり）。
- Daily Digest HTML exportは未実装（nice-to-have、本Phase必須ではない）。
- Expert AI handoffはDaily Briefから直接ではなく、Theme Discovery経由の既存導線に統一した——将来的にDaily Brief上に直接ボタンを置く場合は、既存の`find_or_create_session_for_theme_candidate`をそのまま呼ぶAPIを1本追加するだけで拡張可能。
- Scheduler連携によるBrief自動生成タイミング（例: 07:00データ更新→07:30下流→08:00 Brief準備完了）は、Phase D側のScheduler登録が本番サーバ未構築のため、今回は設計のみに留め、実際の自動生成スケジュールは組んでいない。

## 最終判定

> **Phase E: Daily Intelligence Brief Ready**

新しい分析AIを作らず、既存のMonitoring／Theme Discovery／Episode Review／Theme Sync／Collection資産を「今日どこを見るべきか」の順序に束ねる仕組みを実装し、実データ・実ブラウザで動作確認した。
