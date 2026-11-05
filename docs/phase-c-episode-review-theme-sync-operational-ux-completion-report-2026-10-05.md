# Phase C｜Episode Cluster Review → Theme Candidate Sync — 運用化・滞留可視化 Completion Report（2026-10-05）

対象: `geo_theme_candidate_pipeline_service.py`（新規）、`geo_theme_candidate_api.py`（エンドポイント追加）、フロントエンド（`geo.episode-candidates.tsx`／`theme-explorer-panel.tsx`／`geoThemeCandidateApi.ts`／新規コンポーネント）。
**変更していないもの（確認済み）**: Episode Cluster生成・判定ロジック（`geo_episode_cluster_service.py`）、Theme Candidate LLM Prompt・hallucination guard・fingerprint計算・Evidence grounding（`geo_theme_candidate_service.py`のCore部分）、Expert AI reasoning core、READING_UI_V1、SOURCE_INTELLIGENCE_V1。DB migrationは不要（既存列・既存`geo_ingestion_runs`の再利用のみ）。

---

## 1. 現状調査結果（実装前の報告、再掲）

フロントエンド探索Agent＋直接調査の結果、**Episode Cluster Review UI・Theme Discovery UI・Expert AI Handoffは全てフル実装済み**だった。唯一の欠落は、1) `sync_theme_candidates`を呼ぶUIボタンが存在しない、2) acceptedだがTheme Candidate未生成のclusterを可視化する手段が無い、の2点のみ。DB migrationは不要と判断し、そのまま実装まで進めた。

## 2. 実装内容

### Backend（新規ファイル: `geo_theme_candidate_pipeline_service.py`）
- `get_pipeline_status`: レビュー待ち/承認済み/Theme同期待ち/Theme Candidate生成済み/Handoff済みの5区分集計
- `list_pending_sync_material`: 既存`list_candidate_worthy_material`（cheap/deterministic、変更なし）の結果から、既に同じcluster_ids集合でTheme Candidateが存在するものを除外（決定論的、LLM不要）
- `sync_cluster` / `batch_sync_pending`: 既存`build_candidate_material`/`generate_candidate_framing`/`create_or_merge_candidate`をそのまま呼ぶだけの薄いラッパー。実行ごとに`geo_ingestion_runs`（`connector_type="theme_candidate_sync"`）へRun記録（新規table無し）
- `is_cluster_sync_eligible`: Accept直後の「Theme Candidate生成対象になりました」判定用

### API（`geo_theme_candidate_api.py`に追加）
`GET /pipeline-status`、`GET /pending-sync`、`GET /sync-runs`、`POST /sync-cluster/{episode_cluster_id}`、`POST /sync-pending`。既存の認証方針（mutation=`verify_ios_identity`必須、GET=無認証）をそのまま踏襲。

### Frontend
- `ThemePipelineStatus`（新規コンポーネント）: Episode Candidates画面上部に5区分件数を表示（「次に何をすべきか」が分かる注意アイコン付き）
- `BatchThemeSyncPanel`（新規）: 対象件数・実行前確認（AlertDialog）・成功/候補なし/失敗件数の表示
- `ThemeSyncGuidance`（新規、`EpisodeClusterRow`内）: Accept直後に「このEpisode ClusterはTheme Candidate生成対象です」＋個別Syncボタン＋「あとで」
- `EpisodePipelineEmptyStateHint`（新規、`theme-explorer-panel.tsx`内）: Theme Candidate 0件時に「データが無い」のか「Episode Reviewで止まっている」のかを区別して表示
- `geoThemeHighlightStore.ts`（新規）: Sync成功後「テーマ探索で確認」から該当Candidateの詳細Sheetを直接開く（既存`sourcePickStore.ts`と同じsessionStorage方式）
- candidate_origin表示（item 13）: **既に`geo_theme_explorer_service.py`で実装済み**（`generation_model`接頭辞から決定論的に判定、ラベル表示も既存）であることを確認。新規実装は不要と判断し、重複する独自実装は追加しなかった

## 3. Before / After

| | Before | After |
|---|---|---|
| Review待ち件数の把握 | 画面を開いて目視カウントするしかなかった | Pipeline Statusウィジェットで即座に把握 |
| acceptedだがTheme未生成の把握 | **不可能**（判定手段自体が存在しなかった） | 「Theme同期待ち」として明示、件数・対象cluster一覧をAPI/UIで取得可能 |
| Theme Candidate生成の実行 | `/sync` APIはあったが呼ぶ手段（UIボタン・CLI）が皆無 | 個別Sync・一括Syncボタンから実行可能 |
| 実行結果の追跡 | 不可能 | `geo_ingestion_runs`へRun記録、成功/候補なし/失敗を分けて表示 |
| 0件時の説明 | 「候補がありません」とだけ表示 | 承認済み/Theme同期待ち件数を示し、その場でSync可能 |

## 4. 発見・修正した重大バグ（実LLM呼び出しで判明）

**冪等性バグ**: 同一acceptedクラスタに対しTheme Candidate Syncを2回実行すると、実際のLLM呼び出しではactors/regions/themesの出力が毎回わずかに変わり、既存の`candidate_fingerprint`一致によるマージ判定が効かず、**重複したTheme Candidateが生成される**ことを実データで確認した（単体テストはモックLLMが常に同一出力を返すため、これを見逃していた）。

対応: LLM呼び出し・fingerprint計算（Core Generation Logic、変更禁止対象）には一切手を入れず、呼び出し側（`_sync_one_material`）でLLMを呼ぶ**前**に、同じcluster_ids集合を持つTheme Candidateが既に存在するかを確認し、あれば既存のものをそのまま返すガードを追加した。実データで、重複込みで1件作成→削除→修正後に再実行して`merged`（重複なし、LLM呼び出し自体も発生せず）になることを確認済み。回帰テスト`test_sync_cluster_is_idempotent_even_when_llm_output_varies`追加。

## 5. 実データE2E確認（ブラウザ経由、item 19の全ステップ）

1. `http://127.0.0.1:8080/geo/episode-candidates`でReview待ち50件を確認
2. 実クラスタ（Iran、quad4/root19、17件）の行でAcceptをクリック → Pipeline Statusが承認済み1→2、Theme同期待ち0→1へ即時更新（クロス invalidation修正後に確認）
3. 同じ行に「Theme Candidate生成対象です」ガイダンス＋Syncボタンが出現
4. 「Theme Candidateを生成」クリック → 実LLM呼び出し成功（200）、評価グラウンディング済みの新規Theme Candidate（「イランの対イスラエル・米国を含む多方面強硬シグナルと台湾有事リスク連関の可能性」）を実データで生成
5. 「テーマ探索で確認」クリック → `/geo/theme-discovery`へ遷移し、生成したCandidateの詳細Sheetが自動で開く（highlight機構）
6. 詳細Sheet内「Expert AI相談へ」クリック → プレビュー確認ダイアログ（分析テーマ・問い・アクター等が正しく反映）
7. 「この内容で相談を開始」クリック → 実Analysis Session（`a9a09186-...`、status=active）が作成され、`/geo/ios/analysis/{id}`へ遷移
8. DB確認: `geo_ios_analysis_contexts`に正しいcore_question/analysis_objective入りのIntake Context 1件、Theme Candidateのstatusは`NEW`→`HANDED_OFF`に正しく遷移

収集→Signal→Event→Episode Candidate→Episode Cluster Review→Accept→Theme Candidate Sync→Theme Discovery→Expert AI Handoffの**全経路が実データ・実UIで初めて一本につながった**。

## 6. 再実行安全性（item 20）

- 同一clusterへの複数回Sync: 実API経由で確認済み（`created`→`merged`、candidate_id同一、重複行なし）
- バッチSyncの一部失敗: 単体テストで確認（1件失敗しても他の処理を継続、件数集計も正しい）

## 7. Failure Handling（item 21）

LLM失敗時、clusterは`accepted`のまま保持され、Theme Candidateは作られず、再試行可能であることを単体テストで確認（`test_sync_cluster_llm_failure_does_not_change_cluster_status_and_is_retryable`）。クラッシュ・自動reject・半端なTheme Candidate保存のいずれも発生しない。

## 8. テスト・Full Regression

- 新規`tests/test_geo_theme_candidate_pipeline_service.py`: 14件（accepted→pending sync判定、sync success/no candidate/failure/retry、duplicate prevention、batch sync、冪等性のLLM非決定性対応を含む）全PASS
- `python -m pytest tests/` Full Regression: **2,597件 全PASS**（Phase A/B/既存Expert AI・Episode Cluster・Theme Candidate関連のテストに影響なし）
- フロントエンド: `tsc --noEmit`・`npm run build`とも成功（0エラー）。追加コードはeslint/prettierクリーン（既存ファイルの既存debtには触れていない）

## 9. 残課題

- Pipeline Health（item 15）の「Collection」段階表示は、Phase Aの`scripts/geo_collection_health_check.py`と重複するため今回は見送り（仕様上「可能であれば」の任意項目）
- 空画面（item 14）の実ブラウザでの0件表示は、現在Theme Candidateが18件存在するため実データでは再現確認していない（コードレビューと実装ロジックの妥当性確認のみ）
- Sync履歴（item 11）はAPI（`GET /sync-runs`）のみ実装、専用UI表示は今回のP0スコープ外
- Review待ち49件は今回未処理（Phase Cのスコープ外、人間レビューの実作業そのものは対象外）

## 10. 最終判定

> **Phase C: READY**

Episode Cluster Review→Accept→Theme Candidate Sync→Theme Discovery→Expert AI Handoffの運用フローを、担当者が「どこで止まっているか」「次に何をすべきか」を迷わず把握・実行できる状態にした。既存の推論ロジック（Episode Cluster判定・Theme Candidate LLM・Expert AI Core等）は一切変更せず、実データ・実LLM呼び出しで検証する過程で重大な冪等性バグを発見・修正できたことも含め、本Phase Cの目的は達成されたと判断する。Production SchedulingとHuman Accept以降の自動化は、ユーザー指示通り今回のスコープ外として持ち越す。
