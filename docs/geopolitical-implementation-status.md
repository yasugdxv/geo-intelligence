# 地政学ナレッジ＆インテリジェンスDB — 実装状況

このドキュメントは各Phase完了時に追記する（既存`docs/implementation-plan.md`の運用ルールを継承）。

---

## 2026-08-04: Phase 1 Completion Review実施 → Phase 2A着手

`docs/geopolitical-phase1-completion-review.md`を作成。Phase1は完了条件をすべて満たしていることを確認（詳細は同ドキュメント参照）。

**重要な発見**: config.jsonにEmbedding Deploymentが一切設定されていない（Azure OpenAIはchat用デプロイのみ）。Phase1で作成した`geo_knowledge_items.embedding vector(1536)`列の1536次元は未確認のまま設定した値。ユーザー指示（推測でVector Migrationを使用しない）に従い、**Phase 2AではVector Search機能を有効化せず、Full Text Search＋その他フィルタのみでHybrid Retrievalを実装する**方針を確定。

Phase 2Aの詳細実装計画はPlanモードで策定する。

---

## 2026-08-04: ドキュメント作成完了

### 実装済み
- `docs/geopolitical-knowledge-architecture.md`
- `docs/geopolitical-data-model.md`
- `docs/geopolitical-source-registry.md`
- `docs/geopolitical-operations-playbook.md`
- `docs/geopolitical-implementation-plan.md`
- 本ファイル（`docs/geopolitical-implementation-status.md`）

### 未実装
- Phase 1のコード一式（DB migration、サービス層、API、UI、テスト）はこれから着手

### 仮定
- Supabase側でpgvector拡張が利用可能かは未確認（Phase 1では使用しないため影響なし。Phase 2着手時に確認する）
- 既存アプリに認証機構が無いため、Role制御は開発専用の軽量チェックとする（`docs/geopolitical-operations-playbook.md`3章）
- 既存`geo_source_registry`のtrust_tier列は新規追加とし、既存の`source_tier`（一次/二次/三次/四次）とは独立に保持する

### 既知の制約
- Phase 1では自動Connector・Network Graph・Indicator Monitor・pgvector検索は実装しない（`docs/geopolitical-implementation-plan.md`のPhase 2参照）

### リスク
- `geo_knowledge_items`を汎用テーブルとして設計しているため、knowledge_type別のJSON Schema管理（`geo_knowledge_item_schemas.py`）の保守がPhase 2以降のカード種追加時に増える可能性がある

### 次の作業
- Core DB Migration（`sql/2026-08-04_geo_knowledge_core.sql`ほか）の作成

### 手動作業が必要な項目
- Migration適用後、ユーザーがSupabase SQL Editorで3ファイルを順番に実行する必要がある（Phase 1完了時にまとめて依頼する）

---

## 2026-08-04: Phase 1（中核ループ）実装完了

### 実装済み

**DB migration**（`sql/2026-08-04_geo_knowledge_*.sql`、3ファイル。**このセッションからは未実行、ユーザーがSupabase SQL Editorで手動適用する必要がある**）
- `geo_knowledge_core.sql`: geo_entities/geo_entity_aliases/geo_issues/geo_relationships/geo_assets/geo_dependencies/geo_knowledge_items/geo_knowledge_reviews/geo_cases/geo_case_hypotheses/geo_claims/geo_document_chunks/geo_claim_evidence/geo_knowledge_gaps/geo_entity_issues/geo_relationship_issues/geo_knowledge_item_issues/geo_case_issuesの新規作成、既存geo_source_registry・geo_documentsへの列追加（削除・上書きなし）
- `geo_knowledge_search_fts.sql`: tsvector/GIN（Full Text Search、Phase1の検索経路）
- `geo_knowledge_pgvector_optional.sql`: vector拡張・embedding列（Phase1では未使用、Phase2用の準備のみ）

**Pythonサービス層**（新規、既存コード無改変）
`geo_role_guard.py`（開発専用Role制御）、`geo_entity_resolution.py`、`geo_entity_service.py`（Entity CRUD・Alias・Merge）、`geo_relationship_service.py`、`geo_knowledge_item_schemas.py`（12種類のknowledge_type別JSON Schema）、`geo_knowledge_service.py`（Status遷移: candidate→in_review→active/contested/rejected→superseded/deprecated/archived、Review記録）、`geo_claim_service.py`（Claim/Evidence、文書chunking）、`geo_case_service.py`（Case/Hypothesis）、`geo_retrieval_service.py`（Retrieval Layer）、`geo_foundation_pack_service.py`（22項目Foundation Pack）、`geo_knowledge_gap_service.py`（Deterministic Gap 12種＋Analytical Gap Candidate 9種）、`geo_candidate_extraction_service.py`（LLM候補抽出、常にstatus=candidate）、`geo_connector_csv_excel_json.py`（手動Import）

**API**: `geo_knowledge_api.py`（新規、41エンドポイント、prefix `/api/geo/knowledge`）を作成し、`api_server.py`は「import 1行＋include_router 1行」のみ追加（既存`/api/geo/*`は無改変）

**フロントエンド**: `lib/geoKnowledgeApi.ts`（新規APIクライアント）、4画面（`geo.knowledge-search.tsx`、`geo.cases.tsx`＋`geo.cases.$id.tsx`、`geo.knowledge-review.tsx`、`geo.sources-documents.tsx`）、サイドバーに4リンク追加、i18n（ja/en）キー追加。`tsc --noEmit`エラーなしを確認済み

**テスト**: `tests/test_geo_knowledge_core.py`（13件、schema検証・Role制御・状態遷移・demo分離・claim/evidence制約・chunking冪等性）、`tests/test_geo_knowledge_e2e.py`（承認済みPlan記載の24手順すべてを検証する統合テスト）。すべて`tests/_fakes.py`のFakeSupabaseClient（新規テーブルのid_field追記のみ、既存分は無改変）で完結。全14件pass

### 未実装（Phase 2以降）
World Bank等の自動Connector、定期Scheduler、Network Graph表示、Indicator Monitor、本格Metric時系列管理、pgvector類似検索・Embedding生成、3 Theaterへの本格データ投入、本格RBAC・Entra ID連携、`geo_expert_engine`のRetrieval Layer移行

### 仮定・設計判断（承認済みPlanからの補足）
- `geo_knowledge_reviews.review_decision`に、承認済みPlanには無かった`start_review`（candidate→in_review）を追加した。既存の6決定（approve_active等）だけではcandidateから動かす手段が無かったため
- Knowledge Statusの`contested`から`{active, rejected, superseded}`への再遷移を許可した（Plan記載の状態図は`candidate→in_review→.../active→.../rejected→archived`のみで、contestedの解消経路が明記されていなかったため、追加レビューで解消できる設計とした）
- Connectorは`connectors/`サブディレクトリではなく、リポジトリ全体の慣習（フラットな`geo_*.py`）に合わせ`geo_connector_csv_excel_json.py`として実装した（`connectors/`空フォルダが削除できずリポジトリに残っているが実害なし）
- 新APIルーターは`api_server.py`へ直接書き込まず`geo_knowledge_api.py`に分離した（1250行超の既存ファイルへの大量追記を避けるため）。フロントのAPIクライアントも同様に`lib/geoKnowledgeApi.ts`として分離した（`lib/api.ts`が既に1000行超のため）
- Entity⇔Knowledge Item⇔Relationship⇔Caseの関連付けは、直接のFKではなくすべて共通のIssue（`geo_issues`）タグ付け経由（Plan記載の設計通り）
- `SupabaseClient.update()`は`Prefer: return=minimal`のため更新後の行を返さない仕様と判明。当初`client.update(...)[0]`という誤ったコードを書いていたため、更新後に`select`で取り直す形へ全面的に修正した（実クライアント・FakeSupabaseClient双方で正しく動作することをテストで確認済み）

### 既知の制約
- Entity resolutionは完全一致（大文字小文字無視）のみ。あいまい一致・表記ゆれの自動吸収はPhase2
- 文書chunkingは段落単位の素朴な分割（トークン境界処理なし）
- Knowledge Candidateの重複検知はタイトル完全一致のみ

### リスク
- 21カラムのgeo_source_registry拡張・14個の新規テーブルを一度のmigrationで適用するため、Supabase側のタイムアウトや権限エラーが起きた場合は3ファイルを個別に分割適用する必要があるかもしれない（未検証、実DBが無いこのセッションからは確認不可）

### 次の作業（Phase 2）
`docs/geopolitical-implementation-plan.md`のPhase 2セクション参照

### 手動作業が必要な項目
1. Supabase SQL Editorで以下を**この順番**で実行する:
   `sql/2026-08-04_geo_knowledge_core.sql` → `sql/2026-08-04_geo_knowledge_search_fts.sql` → （任意）`sql/2026-08-04_geo_knowledge_pgvector_optional.sql`
2. 適用後、地政学バックエンド（`api_server.py`、API_PORT=8001）を再起動する
3. `/geo/cases`から実際にCaseを作成し、24手順のE2Eシナリオ（`docs/geopolitical-implementation-plan.md`参照）をUI上で一度手動確認することを推奨
4. 別件・無関係の既知事象: `tests/test_sustainability_article_selector.py`の5件が、このセッションの変更と無関係に（変更前から）失敗することを確認済み（詳細未調査、Phase1の変更が原因ではない）

---

## 2026-08-04: 実DBでの動作確認とバグ修正

ユーザーがSQL migrationを適用後、実Supabase DB + 実Azure OpenAIに対して中核ループ全体（Case作成→Entity/Relationship登録→Document/Chunk→Claim/Evidence→Foundation Pack→Deterministic Gap→Analytical Gap Candidate(実LLM)→Hypothesis→Knowledge Candidate抽出(実LLM)→Review→Active化→次Caseでの再利用）を実行し、すべてis_demo=trueで作成のうえ確認後に削除した（0件残存を確認済み）。

### 発見して修正したバグ
1. **Knowledge Candidate抽出でLLM出力がSchema検証に全滅**: `geo_candidate_extraction_service.py`のLLM向けschemaヒントが「必須キー名の列挙」のみで、型・enum制約を伝えていなかったため、5件中5件がSchema違反でskippedになった。実データで発覚。プロンプトにcandidate_type別の完全なproperty定義（type/enum含む）を埋め込む形に修正
2. **同上、型のゆれ**: 上記修正後も、`confidence`に英語("high")が入る、`escalation_path`（string型）に配列が入る、という2種の型ゆれが発生。`_normalize_content()`を追加し、Schema検証前に軽微な型ゆれ（配列→文字列結合、英語confidence→日本語enum）を機械的に補正するようにした（内容の創作は行わない、フォーマット補正のみ）
3. 上記2点の修正後、実データで4/4のcandidate（precedent/mechanism/indicator/reasoning_rule）が正常に生成されることを確認

### 確認できたこと（実データ）
- Deterministic Gap検出: 5種のgapを正しく検出（missing_entity/missing_historical_context/missing_precedent/insufficient_source_diversity/missing_indicator）
- Analytical Gap Candidate生成（実LLM）: 5件生成、いずれもavailable_evidence/missing_evidence等の必須項目を満たす
- Knowledge Candidate抽出（実LLM、修正後）: 4/4成功
- Review（start_review→approve_active）: 正常にstatus遷移
- 次のCase作成後、Active化したKnowledge Itemが`get_reusable_knowledge`で正しく再利用可能な状態で取得できることを確認（中核ループの最終目的を実データで達成）
- Demo分離（`include_demo`フラグ）が実DBでも正しく機能することを確認（当初「Foundation Packでentity 0件」と誤認したが、単に検証スクリプト側で`include_demo=True`を渡し忘れていただけと判明。実装は正しい）

### 次の作業
Phase 2への着手、またはユーザーからの追加フィードバック待ち

---

## 2026-08-05: Phase 2A（実運用接続）実装完了

承認済みPlan: `C:\Users\269811\.claude\plans\warm-orbiting-lemur.md`。ユーザーから4ラウンドの詳細フィードバック（Raw Snapshot/Claim接続明確化、Ingestion Run Snapshot個別Link Table化、OFACの現在状態と変更履歴の分離、FTSのRPC化、Context Budgetルール明文化等）を受け、全て反映のうえ実装した。

### 実装済み

**DB migration**（`sql/2026-08-05_geo_phase2a_*.sql`、4ファイル。**このセッションからは未実行、ユーザーがSupabase SQL Editorで手動適用する必要がある**。適用順序: `geo_phase2a_ingestion.sql`→`geo_phase2a_metrics.sql`→`geo_phase2a_retrieval_trace.sql`→`geo_phase2a_freshness.sql`）
- `geo_phase2a_ingestion.sql`: `geo_raw_snapshots`（Immutable、content_hash unique）, `geo_ingestion_runs`, `geo_ingestion_run_snapshots`, `geo_entity_match_candidates`, `geo_sanctions_entries`, `geo_sanctions_entry_versions`、`geo_source_registry`のALTER
- `geo_phase2a_metrics.sql`: `geo_metric_definitions`, `geo_metric_observations`, `geo_knowledge_item_claims`、`geo_claim_evidence`のALTER（metric_observation_id追加、one_target制約を4択に更新）
- `geo_phase2a_retrieval_trace.sql`: `geo_retrieval_runs`, `geo_retrieval_results`、FTS用RPC関数3種
- `geo_phase2a_freshness.sql`: `geo_knowledge_items.last_evidence_at`、knowledge_type_check制約への4種追加

**Pythonサービス層**（新規/拡張、詳細は各docs参照）
`geo_connector_base.py`（共通Ingestion Run/Raw Snapshot処理）、`geo_connector_world_bank.py`、`geo_connector_ofac.py`、`geo_metric_service.py`、`geo_freshness_service.py`、`geo_search_service.py`（Hybrid Retrieval）、`geo_retrieval_service.py`拡張（6関数追加）、`geo_candidate_extraction_service.py`拡張（外部由来4種＋`extract_candidates_from_external_claims`）、`geo_expert_engine.py`統合（`case_id`パラメータ、後方互換維持）、`geo_knowledge_item_schemas.py`拡張（4スキーマ追加）、`geo_connector_cli.py`（新規）

**API**: `geo_knowledge_api.py`に19エンドポイント追加（41→60）。connectors/run、ingestion-runs、raw-snapshots、entity-match-candidates、sanctions-entries、metrics/observations、search（hybrid）、retrieval-runs、freshness、expert/analyze、candidates/extract-from-claims

**フロントエンド**: `lib/geoKnowledgeApi.ts`拡張、既存4画面拡張（新規画面は追加しない）。`geo.sources-documents.tsx`（Connector実行UI＋Ingestion Run一覧）、`geo.knowledge-search.tsx`（自由テキストHybrid検索＋スコア内訳）、`geo.cases.$id.tsx`（Expert Engine実行ボタン＋Retrieval Trace表示）、`geo.knowledge-review.tsx`（外部由来Candidateの出典表示）。`tsc --noEmit`エラーなしを確認済み

**テスト**: `tests/test_geo_phase2a.py`（新規18件）＋既存Phase1回帰（`test_geo_knowledge_core.py`/`test_geo_knowledge_e2e.py`/`test_geo_expert_engine.py`、計17件）を合わせて**全35件pass**。`tests/_fakes.py`にPhase2Aテーブルのid_fieldマッピング、`rpc()`スタブ、`geo_raw_snapshots.fetched_at`の既定値を追加（本番コードは無変更、フェイク側のDB-defaultシミュレーション不足を補っただけ）

**ドキュメント**: 新規6件（`geopolitical-phase2a-architecture.md`, `geopolitical-connector-guide.md`, `geopolitical-ingestion-operations.md`, `geopolitical-retrieval-design.md`, `geopolitical-expert-engine-integration.md`, `geopolitical-freshness-policy.md`）、更新5件（`geopolitical-source-registry.md`, `geopolitical-data-model.md`, `geopolitical-operations-playbook.md`, `geopolitical-implementation-plan.md`, 本ファイル）

### 発見して修正したバグ

`geo_claim_service.link_evidence()`が`metric_observation_id`パラメータを受け付けない状態で、`geo_connector_world_bank.py`側は既にこのkwargで呼び出すコードになっていた（実行時に`TypeError`で必ず失敗する状態）。Phase2Aテスト作成の準備中（Fakeの拡張作業中）に発見し、シグネチャを4択検証に修正して解消。修正直後の回帰テストで影響なしを確認済み。

### 仮定・設計判断（承認済みPlanからの補足）

- `tests/_fakes.py`のFakeSupabaseClientは実DBの`default now()`列を一般シミュレートしないため（既存`created_at`のみ既定値を持つ設計だった）、`geo_raw_snapshots.fetched_at`にも同様の既定値を追加した。実DBでは`insert()`が`return=representation`のため`fetched_at`はDB側で正しく返る（本番コードの修正ではない）
- OFACの`list_date`（正式発効日）はSDN CSV単体からは取得できないため、Phase2Aでは常に`valid_from=null`のまま（推定しない、Plan通り）。将来add.csv等で発効日相当の情報が確認できればPhase2Bで補完する

### 既知の制約

- Vector Search未実装（Embedding Deployment未確認のため。`docs/geopolitical-retrieval-design.md`）
- OFACは住所/国籍/識別子CSV（add.csv/citizen.csv/id.csv）を取り込まない（sdn.csv/alt.csvのみ）
- Ingestion Runは手動トリガー＋外部スケジューラ前提。常駐Schedulerは無い
- 鮮度分類APIは個別ID参照のみ。一覧画面へのバッジ表示は未実装
- `test_sustainability_article_selector.py`の5件失敗は引き続きPhase1/2Aと無関係（このセッションでも再確認済み、対応不要）

### リスク

なし（既存テーブル削除・データ上書きは発生していない。既存Phase1コード・テストは無変更のまま合格）

### 次の作業（Phase 2B）

`docs/geopolitical-implementation-plan.md`のPhase 2Bセクション参照

### 手動作業が必要な項目

1. Supabase SQL Editorで以下を**この順番**で実行する（Phase1の3ファイルが適用済みであることが前提）:
   `sql/2026-08-05_geo_phase2a_ingestion.sql` → `sql/2026-08-05_geo_phase2a_metrics.sql` → `sql/2026-08-05_geo_phase2a_retrieval_trace.sql` → `sql/2026-08-05_geo_phase2a_freshness.sql`
2. 適用後、地政学バックエンド（`api_server.py`、API_PORT=8001）を再起動する
3. `/geo/sources-documents`からWorld Bank/OFAC Connectorを実際に1回ずつ手動実行し、Ingestion Run記録・Raw Snapshot・Metric/Sanctions Entry・Claim/Evidence・Entity Match Candidate生成までを確認することを推奨（is_demo運用または確認後に削除、実施する場合はユーザーへ確認のうえ行う）

---

## 2026-08-17: Phase 2B 第1弾（追加Connector / Network Graph / Indicator Monitor）実装完了

承認済みPlan: `C:\Users\269811\.claude\plans\warm-orbiting-lemur.md`（Phase2B第1弾）。ユーザーがPhase2B優先領域として選んだ3点（追加Connector、Network Graph、Indicator Monitor）に絞って実装した。当初案に対しユーザーレビューで4点の重要な修正を受けた: (1) UN Comtradeは「無認証・500件/日次無制限」という前提を採用せずFree APIキー方式に変更、(2) IMFの認証要否は実アクセスで検証してから確定（推測で決めない）、(3) Source Registryの`automation_allowed`をAPIキー有無で自動変更せず`connector_enabled`/`credential_configured`と3分離、(4) Network GraphのEntity一括取得は巨大PostgREST `in.()`に依存せずDB側RPCとした。

### 実装済み

**DB migration**（`sql/2026-08-06_geo_phase2b_*.sql`、3ファイル。**このセッションからは未実行、ユーザーがSupabase SQL Editorで手動適用する必要がある**。適用順序: `geo_phase2b_source_gating.sql`→`geo_phase2b_relationship_graph.sql`→`geo_phase2b_indicator_alerts.sql`）
- `geo_phase2b_source_gating.sql`: `geo_source_registry`へ`connector_enabled`/`credential_configured`を追加
- `geo_phase2b_relationship_graph.sql`: `get_relationship_graph_for_issue` RPC関数（node/edgeをDB側結合1回で返す）
- `geo_phase2b_indicator_alerts.sql`: `geo_indicator_alerts`（`observation_id`にunique制約）、`geo_indicator_definitions`へ`expected_update_cadence`追加

**追加Connector**（`docs/geopolitical-connector-guide.md`参照）
- `geo_connector_imf.py`（無認証、IMF DataMapper APIへの実アクセスで認証不要を検証済み。`api.imf.org`のSDMX APIはdataflow特定できず不採用）
- `geo_connector_un_comtrade.py`（Credential-required、`config/geo_un_comtrade_flows_seed.yaml`でmax_records_per_call等を設定値管理）
- `geo_connector_wto.py`（Credential-required。クエリパラメータ名は実アクセス未検証のため、実キー取得後の初回実行前にAPI Consoleでの確認が必要と明記）
- `geo_connector_manifesto.py`（Credential-required、Main Datasetのみ。Corpus全文・chunkingは対象外）
- `geo_connector_base.py`拡張: `resolve_credential`/`sync_credential_status`（credential referenceパターン、実キー値はconfig.jsonへ保存しない）、`check_automation_allowed`を3ゲート検証に拡張

**Network Graph**（`docs/geopolitical-network-graph.md`参照）
- `geo_relationship_service.get_relationship_graph_for_issue`（RPC1回でnode/edge取得、巨大in.()回避）
- `geo_entity_service.ENTITY_TYPE_DISPLAY_CATEGORY`（entity_type24種→5グループへの畳み込み）
- フロントエンド: `react-force-graph-2d`導入（ユーザー承認済み）、`components/geo/geo-relationships-tab.tsx`を`geo.theme.$themeId.tsx`の新規Tabとして追加

**Indicator Monitor**（`docs/geopolitical-indicator-monitor.md`参照）
- `geo_indicator_engine.py`拡張: `evaluate_threshold`（direction/operator対応）、`create_alert_if_needed`（Observation単位で冪等）
- `geo_freshness_service.classify_indicator_freshness`（Knowledge/Source Freshnessとは独立した第3の判定関数）
- API: `GET /api/geo/indicators/monitor`（集約、新しい計算ロジックは持たない）
- フロントエンド: 新規route`geo.indicator-monitor.tsx`、`components/geo/geo-freshness-badge.tsx`（`IndicatorFreshnessBadge`/`IndicatorAlertBadge`）、既存Indicator詳細画面へバッジ追加

**テスト**: `tests/test_geo_phase2b_connectors.py`（16件）、`tests/test_geo_phase2b_network_graph.py`（3件）、`tests/test_geo_indicator_engine.py`（新規、9件）、`tests/test_geo_phase2b_indicators.py`（6件）— 計29件。既存回帰含め`pytest tests/ -q`で148件中142件pass。`tsc --noEmit`エラーなし

**ドキュメント**: 新規2件（`geopolitical-network-graph.md`、`geopolitical-indicator-monitor.md`）、更新4件（`geopolitical-connector-guide.md`、`geopolitical-ingestion-operations.md`、`geopolitical-source-registry.md`、`geopolitical-data-model.md`、`geopolitical-implementation-plan.md`、本ファイル）

### 発見した問題（Phase2Bの変更が原因ではないことを確認済み）

`tests/test_geo_official_crawler.py::test_crawl_source_creates_events_and_observation`が新たに失敗するようになった。原因を調査した結果、`tests/_fakes.py`の`FakeSupabaseClient.insert()`が付与する固定の`created_at`既定値（`"2026-07-17T00:00:00+00:00"`）と、`geo_indicator_engine.compute_event_count_observation`の「直近30日」ローリングウィンドウ判定が、実際の壁時計時刻が2026-08-16を超えたことで初めて食い違うようになったための失敗（本セッション実行時点の実時刻は2026-08-17）。Phase2Bで`geo_official_crawler.py`・`compute_event_count_observation`・関連するfakeロジックのいずれも変更していないため、コード変更が原因ではなく、固定日付フィクスチャの経年劣化。`test_sustainability_article_selector.py`の5件と同種の、既存の環境依存的テスト脆弱性として記録するに留め、本セッションでは修正しない（スコープ外）。

### 仮定・設計判断（承認済みPlanからの補足）

- IMF Connectorは実アクセス検証の結果、当初想定していた`api.imf.org`のSDMX 3.0/2.1 REST APIではなく、`https://www.imf.org/external/datamapper/api/v1/`（IMF DataMapper API）を採用した。前者はdataflow識別子（agencyID/dataflowID/version）を特定できず404が続き、後者は`/indicators`・`/countries`・`/{code}/{country}`のいずれも無認証・実データで200応答を得られた
- WTO Timeseries APIの個別クエリパラメータ名（i/r/p/ps等）は、実際のAPIキーが無く実アクセス検証ができなかったため、複数の一般公開情報から得た値を採用した。ベースURL・認証ヘッダ方式（Azure APIM、`Ocp-Apim-Subscription-Key`）は確認済み。実キー取得後、初回実行前にAPI Consoleでの再検証が必要（`config/geo_wto_indicators_seed.yaml`にコメントで明記済み）
- 閾値超過アラート機能は、既存`geo-indicator-status.tsx`の「危険度を色で断定しない」という指示書由来の方針と矛盾しないよう設計した（Alertは人が明示的に設定した閾値との比較結果であり自動リスク判定ではないこと、既存のIndicatorには影響しないことをdocs/UIの両方で明記）

### 既知の制約

- WTO Connectorの実データ取得は未検証（APIキー未取得のため）
- Manifesto ProjectのCorpus全文・chunkingは対象外（Main Datasetのみ）
- Alertの既読(acknowledged_at)設定UIは未実装
- Network Graphの大規模データでの性能検証は未実施（現状Entity/Relationshipデータがほぼ空のため）
- `test_sustainability_article_selector.py`5件・`test_geo_official_crawler.py`1件は引き続き無関係な既存の失敗（前者はPhase1以前から、後者は今回発見。いずれもテスト固定日付の経年劣化）

### リスク

なし（既存テーブル削除・データ上書きは発生していない。既存Phase1/2Aコード・テストは無変更のまま合格）

### 次の作業（Phase 2B次ラウンド）

`docs/geopolitical-implementation-plan.md`のPhase 2B次ラウンドセクション参照

### 手動作業が必要な項目

1. Supabase SQL Editorで以下を**この順番**で実行する（Phase1・Phase2Aのファイルが適用済みであることが前提）:
   `sql/2026-08-06_geo_phase2b_source_gating.sql` → `sql/2026-08-06_geo_phase2b_relationship_graph.sql` → `sql/2026-08-06_geo_phase2b_indicator_alerts.sql`
2. 適用後、地政学バックエンド（`api_server.py`、API_PORT=8001）を再起動する
3. Credential-required Connector（WTO/UN Comtrade/Manifesto Project）を使う場合、`config.json`へ`connector_credentials`セクションを追記し、対応するOS環境変数（`GEO_WTO_API_KEY`等）にAPIキーを設定する（`config.json.example`参照）
4. `/geo/sources-documents`からIMF Connectorを実際に1回手動実行し、Ingestion Run記録・Metric Observation生成を確認することを推奨（is_demo運用または確認後に削除、実施する場合はユーザーへ確認のうえ行う）。UN Comtrade/WTO/Manifesto ProjectはAPIキー取得後に同様の確認を推奨

---

## 2026-08-19: 状況確認、およびOFAC Connectorへの住所（ADD.CSV）取り込み追加

### 状況確認（実施内容）
Phase 2B第1弾完了後、手動作業項目が実際にどこまで済んでいるかを実DBに対して検証した。
- Supabase migration（`sql/2026-08-06_geo_phase2b_*.sql`3ファイル）は適用済みと確認（`/api/geo/indicators/monitor`が実データで200 OK、Source Registryの3ゲート列も実データで動作確認）
- 一方、Credential-required Connector（WTO/UN Comtrade/Manifesto Project）のAPIキーはまだ未取得（`config.json`の`connector_credentials`キー自体が存在しないことを構造のみ確認、値は見ていない）
- `geo_entities`・`geo_issues`が実DBで0件のまま（Theaterへのentity/relationshipデータ投入がまだ未着手）であることを発見。この影響で、無認証で動くはずのIMF Connectorすら`entity_id`必須パラメータに紐付ける国Entityが無く実行できない状態
- ユーザーの意向により、上記2点（APIキー未取得／Theater未投入）は保留リストとして記録し、着手しない方針とした（詳細はメモリ`geo_dashboard_pending_blockers`参照、Claude Code外部の記憶システムのため本ドキュメントには詳細を書かない）

### 発見して訂正した誤り
`docs/geopolitical-connector-guide.md`・`docs/geopolitical-implementation-plan.md`に記載されていた「OFACの住所/国籍/識別子CSV（add.csv/citizen.csv/id.csv）」という前提が誤りだったことをWeb検索で確認した。OFACが実際に公開しているレガシーCSVは`SDN.CSV`/`ALT.CSV`/`ADD.CSV`/`SDN_COMMENTS.CSV`のみで、`citizen.csv`/`id.csv`というファイルは実在しない。国籍・身分証明書番号は別形式の`sdn_advanced.xml`（関連XML、UN Sanctions Data Model準拠）でのみ取得できる。両ドキュメントの記述を訂正済み。

### 実装済み
`geo_connector_ofac.py`に`ADD.CSV`（住所）取り込みを追加（実在確認済みの3ファイル目、`sdn_advanced.xml`対応は別タスクとして持ち越し）。
- `_parse_add()`: `ent_num`をキーに1対多で住所リストを構築（列: `ent_num, add_num, address, city_state_province_postal_code, country, add_remarks`）
- `_record_hash()`に住所を含めるよう拡張（名称・program・remarksが同一でも住所のみ変わった場合にamendedとして新versionを作るようにするため。含めないと住所更新が検知されず既存versionに反映されないバグになる）
- `geo_sanctions_entry_versions.addresses`列に実データを保存（従来は`[]`固定だった）
- `CONNECTOR_VERSION`/`PARSER_VERSION`を1.0→1.1に更新
- テスト: `tests/test_geo_phase2a.py`に住所保存・住所のみ変更時のamended検知を検証する新規テスト1件を追加。既存OFACテスト3件のモック（`_patch_ofac_requests`）もadd.csvレスポンスを区別できるよう修正（そのままだとadd.csvリクエストがalt_bytesを受け取ってしまい壊れる状態だった）
- 全テスト`pytest tests/ -q`で143件pass（既知の無関係な失敗6件のみ、Phase2B時点から変化なし）

### 実データでの検証（ユーザー承認の上で実施、確認後に削除済み）
`config/geo_imf_indicators_seed.yaml`等とは別に、実際のOFAC公開CSV（本物のtreasury.govのadd.csv含む3ファイル）に対し`is_demo=True`・`max_records=5`で1回実行し、住所列（`address`/`city_state_province_postal_code`/`country`）が実データで正しくパースされること（例: `BANCO NACIONAL DE CUBA`の4住所が国別に正しく分離される等）を確認した。OFAC側のnull値慣例`"-0-"`はそのまま格納される（既存のsdn.csv/alt.csv同様、正規化はしない）。検証後、作成した`geo_sanctions_entries`/`geo_sanctions_entry_versions`/`geo_ingestion_runs`/`geo_raw_snapshots`/`geo_documents`/`geo_document_chunks`/`geo_entity_match_candidates`/`geo_claims`/`geo_claim_evidence`の全行をIDベースで削除し、0件残存を確認済み。

### 既知の制約
- OFACの国籍・身分証明書番号（`sdn_advanced.xml`）は引き続き未実装（別タスク）
- WTO/UN Comtrade/Manifesto ProjectのAPIキー未取得のまま（ユーザー保留）
- Theater（台湾海峡）のEntity/Relationshipデータ未投入のまま（ユーザー保留、IMF Connector等の実行がブロックされる根本原因）

### 次の作業
ユーザーの保留解除待ち（APIキー取得、または台湾海峡Theaterのentity/relationship投入）。手が空いている間の候補: `docs/geopolitical-implementation-plan.md`のPhase 2B次ラウンドのうち、上記2点に依存しない項目（Knowledge Item/Source向け鮮度バッジ一覧表示、Alert既読UI、常駐Scheduler化の設計）

### 手動作業が必要な項目
なし（今回の変更はmigration不要、コード変更のみ）
5. `npm run build`実行時に生成された`eco-digest-spark/.output`等のビルド成果物ディレクトリが残存している（削除にはユーザー確認が必要なため未削除、動作に影響はない）

---

## 2026-08-20: Phase 2B-2（External Reference Model & Internal Expert Learning Layer）実装完了

承認済みPlan: `C:\Users\269811\.claude\plans\pure-drifting-cosmos.md`。ユーザーがPhase 2B-2として提示した詳細仕様書に対し、Plan提示→フィードバック反映を4ラウンド実施（Source Reference Policy導入、status/retrieval_status二軸の真の独立性、Internal Expert AssessmentとReasoning Ruleの分離、Link Table化、reinforced概念、スコアリング信号分離、用語統一、冪等性/Note ID検証）のうえ実装した。

### 実装済み

**DB migration**（`sql/2026-08-19_geo_phase2b2_external_reference.sql`、1ファイル。**このセッションからは未実行、ユーザーがSupabase SQL Editorで手動適用する必要がある**）
- `geo_source_registry`: `reference_availability`/`reference_availability_set_by`/`reference_availability_set_at`追加
- `geo_knowledge_items`: `information_class`に`internal_expert_assessment`追加、`knowledge_type`check制約の既存不整合是正（Phase2A追加4種がSQL未反映だった）、`retrieval_status`/`origin`/`derived_from_review_session_id`/`derived_from_change_id`/`usage_count`/`support_count`/`last_used_at`/`last_confirmed_at`追加
- `geo_claims`: `information_class`に`internal_expert_assessment`追加、`retrieval_status`/`source_id`追加
- 新規テーブル6件: `geo_claim_issues`, `geo_expert_review_sessions`, `geo_expert_review_notes`, `geo_expert_review_changes`, `geo_expert_review_change_entities`/`_issues`/`_notes`
- FTS RPC関数`search_geo_knowledge_items`/`search_geo_claims`を`retrieval_status`等の新規列対応で再定義（戻り値列構成変更のためdrop→create）
- backfill: 既存Knowledge Itemはstatusからretrieval_statusを導出、既存Claimはsource_id解決可否とorigin_typeに応じてretrieval_statusを確定

**Pythonサービス層**（既存拡張＋新規1件）
- `geo_knowledge_service.py`: `review_knowledge_item`にretrieval_status初回遷移限定の初期化を追加（二軸の独立性を守るため、以降の遷移では触れない）。`set_retrieval_status`新設
- `geo_claim_service.py`: `_resolve_source_id`/`_resolve_initial_retrieval_status`（origin_typeだけでInternal/Externalを判定しない）、`set_retrieval_status`、`link_claim_to_issue`新設
- `geo_connector_base.py`: `set_reference_availability`新設（Source単位のReference Policy、available/restrictedのClaimのみ再同期）
- `geo_retrieval_service.py`: `get_reference_claims_for_issue`、`get_internal_expert_assessments`、`attach_case_finding_provenance`新設。既存`_knowledge_items_for_issues`/`get_contested_knowledge`にretrieval_status AND条件を追加（既存のstatusフィルタは維持、一軸への統合はしない）
- `geo_expert_engine.py`: `build_case_context`に5セクション追加（既存6セクションは無変更）。Retrieval Trace記録時にusage_count/last_used_atを更新（Analytics専用、Retrieval Scoreには使わない）
- `geo_search_service.py`: `search_knowledge_items`/`search_claims`にretrieval_status AND条件追加（既存status引数はそのまま）。`origin='internal_expert_review'`のKnowledge Itemはtrust_tierベースのスコアではなく`0.5 + support_count + confirmed_recency`式を使う
- `geo_expert_review_service.py`（新規）: Session開始・Reviewer Note追加・Delta Extraction（LLM、Note優先反映、Note ID検証）・Promote/Reinforce/Keep Case Only/Reject（すべて`disposition='pending'`条件の冪等操作）

**API**: `geo_expert_review_api.py`（新規、9エンドポイント、prefix `/api/geo/expert-review`）を作成し`api_server.py`は「import 1行＋include_router 1行」のみ追加。`geo_knowledge_api.py`へ3エンドポイント追加（Knowledge Item/Claim/Sourceそれぞれのretrieval-status変更、既存claims系エンドポイントの隣）。実際にバックエンドを起動し、全12エンドポイントが`/openapi.json`に正しくマウントされていることを確認済み

**フロントエンド**: `lib/geoKnowledgeApi.ts`拡張（Retrieval Governance・Expert Review系フック追加）、`components/geo/geo-epistemic-badge.tsx`（新規、Epistemic Type/Retrieval Statusバッジ）、`geo.cases.$id.tsx`へExpert Reviewセクション追加（新規routeは作らない、既存の平置きカード構成に合わせた。タブ構造は元々無いページだったためPhase2Bの`geo-relationships-tab.tsx`方式ではなく素直な新規セクション追加とした）、`geo.sources-documents.tsx`へreference_availabilityトグル追加。`tsc --noEmit`エラーなしを確認済み

**テスト**: `tests/test_geo_phase2b2_external_reference.py`（新規13件）＋`tests/test_geo_expert_review.py`（新規16件）＋既存Phase1/2A/2B回帰、計172件中166件pass（既知の無関係な失敗6件は変化なし）。`tests/_fakes.py`に新規6テーブルのid_fieldマッピングを追加（本番コード無変更）

**ドキュメント**: 新規3件（`geopolitical-external-reference-model.md`, `geopolitical-internal-expert-learning.md`, `geopolitical-expert-review-workflow.md`）、更新5件（`geopolitical-data-model.md`, `geopolitical-expert-engine-integration.md`, `geopolitical-operations-playbook.md`, `geopolitical-source-registry.md`, `geopolitical-implementation-plan.md`、本ファイル）

### 発見して修正したバグ

1. **`promote_change`の状態遷移不足**: `geo_knowledge_items`の`ALLOWED_TRANSITIONS`は`candidate→in_review→active`の2段階だが、実装当初`promote_change`が`approve_active`のみを1回呼んでおり`InvalidStatusTransitionError`が必ず発生する状態だった（新規テスト実行時に発覚）。`start_review`→`approve_active`の2段階呼び出しに修正
2. **既存テストのretrieval_status未設定によるKeyError/絞り込み漏れ**: `create_knowledge_item`にretrieval_status列を追加した際、FakeSupabaseClientはDB列のdefaultをシミュレートしないため、既存テストが`client.update(..., {"status": "active"})`で直接statusだけ書き換えている箇所（`tests/test_geo_phase2a.py`2箇所、`tests/test_geo_knowledge_e2e.py`1箇所）で新しいretrieval_status AND条件によりデータが検索結果から漏れる状態になった。`retrieval_status: "available"`を明示的に追加して解消（本番コードの修正ではなく、テストフィクスチャを新列に合わせて更新）
3. **Source未設定行でのKeyError**: `_resolve_initial_retrieval_status`が`geo_source_registry.reference_availability`を直接インデックスアクセスしており、既存Connector（OFAC等）の`_ensure_source`が新列を明示的に設定していない行に対しKeyErrorになった。`.get(..., "restricted")`への変更で解消（DB列のdefaultと同じ値に安全側でフォールバック）

### 仮定・設計判断（承認済みPlanからの補足）

- `information_class`の値`actor_claim`はリネームせず維持し、ユーザー草案の「Official Claim」とはdocs上で同義語として明記するに留めた（既存check制約・データ・複数docsへの影響が大きく機能的利得がないため）
- Expert Review Changeの`related_entity_ids`はLink Table（`geo_expert_review_change_entities`）のスキーマのみ用意し、実際のPopulateは行っていない（LLMにEntity名の名前解決をさせると幻覚のリスクがあるため、Case-Issue関係（`geo_case_issues`）経由の自動リンクのみ実装した。Entity側は将来Reviewer手動指定等での拡張余地として残す）
- `case_specific_assessment`のPromoteは、Sessionに`case_id`が無いと失敗する（`case_finding`スキーマが`case_id`必須のため）。`edited_content`で明示指定すれば回避可能
- `_default_structured_content`（edited_content未指定時の自動生成）は`precedent`等の必須項目が多いknowledge_typeに対しては簡素なプレースホルダーになる。実運用では`edited_content`指定（Edit & Promote）を推奨する設計とした

### 既知の制約

- Reasoning Ruleの意味的重複検出は自動化していない（reinforced判定はレビュー担当者の手動選択のみ、pgvector無しの制約から自然に導かれる）
- Word Comments/Track Changesの実取り込みは未実装（`geo_expert_review_notes`のテーブル構造のみ拡張性を確保）
- Expert Review Changeの`related_entity_ids`Link Tableは未Populate（上記参照）
- `test_sustainability_article_selector.py`5件・`test_geo_official_crawler.py`1件は引き続き無関係な既存の失敗

### リスク

なし（既存テーブル削除・データ上書きは発生していない。既存Phase1/2A/2Bコード・テストは無変更のまま合格）

### 次の作業

実DBでのE2E検証（承認済みPlan13章のホルムズ海峡シナリオ、is_demo運用）の実施、またはユーザーからの追加フィードバック待ち

### 手動作業が必要な項目

1. Supabase SQL Editorで`sql/2026-08-19_geo_phase2b2_external_reference.sql`を適用する（Phase1・Phase2A・Phase2Bのファイルが適用済みであることが前提）
2. 適用後、地政学バックエンド（`api_server.py`、API_PORT=8001）を再起動する
3. 実DBに対しis_demo=trueでE2Eシナリオを1回通し確認後に削除することを推奨（Theaterに実Entity/Issueデータがまだ無いため、検証用の最小限のis_demo Entity/Issueを一時作成する必要がある点は実施前にユーザーへ確認する。`geo_dashboard_pending_blockers`メモリ記録済みの保留事項と関連）

---

## 2026-08-20（同日中の続き）: 実DBでのE2E検証実施、実DB特有のバグ2件を発見して修正

ユーザーがSupabase SQL Editorで`sql/2026-08-19_geo_phase2b2_external_reference.sql`を適用。適用後、実DBに対して以下を実施した。

### Migration適用確認
バックエンドを起動し、`reference_availability`列（trust_tier A1/A2のSourceが`available`、それ以外`restricted`とbackfillされていることを実データで確認）、Expert Review系エンドポイント（新テーブルへの404応答で疎通確認）、FTS RPC関数`search_geo_knowledge_items`/`search_geo_claims`（新パラメータ`p_retrieval_status`込みで直接呼び出し、エラー無しを確認）が正しく機能していることを確認した。

**RLSの抜け漏れをユーザーが指摘**: 既存の全migration（Phase1〜Phase2B）を確認した結果、新規テーブルは例外なく`create table`直後に`alter table ... enable row level security;`を設定する一貫したパターンだったが、Phase2B-2の新規7テーブルにこれが抜けていた。適用前に発見し、全7テーブルへ追加（`create table`数とのRLS設定数の一致を機械的に検証済み）。

### 実DB E2E検証（ホルムズ海峡シナリオ、承認済みPlan13章）
LLM呼び出しのみモック（コスト削減、LLM自体は既存ユニットテストで検証済みのため）、それ以外はすべて実Supabaseへの書き込みで実施。is_demo=trueだと`build_case_context`配下の`get_reference_claims_for_issue`等がPhase1から一貫した仕様通りis_demoデータを除外してしまうため、Claim/Caseはis_demo=falseで作成し、IDベースでの確実なクリーンアップを徹底する方式に変更した（Theater/Case構造は本物のTaiwan Strait Theme配下にテスト用Issueを一時作成する形で代用）。

9項目すべてPASS、クリーンアップ後の残存確認で全8テーブル0件を確認済み。

### 発見して修正した実DB特有のバグ（FakeSupabaseClientでは検出できなかった）

1. **`promote_change`が古いスナップショットを返す**: `create_knowledge_item()`の戻り値（`status='candidate'`時点のスナップショット）を、直後の2回の`review_knowledge_item()`呼び出し（`start_review`→`approve_active`）の後もそのまま呼び出し元へ返しており、実際には`status='active'`へ更新済みにもかかわらず`status='candidate'`のまま返る状態だった。`FakeSupabaseClient.update()`は辞書オブジェクトをin-place更新するため既存のPython参照も追従して見えてしまい、単体テストでは発覚しなかった（実際のHTTP経由の`SupabaseClient`は別リクエストのため追従しない）。`review_knowledge_item()`呼び出し後に`get_knowledge_item()`で明示的に再取得するよう修正
2. **PromoteされたKnowledge ItemにEvidence必須条件が張られていない**: `geo_expert_engine._has_supporting_evidence_chain`（既存、Phase1から）は`geo_knowledge_item_claims`（`relationship='supporting'`）経由でEvidenceまで遡れないactive Knowledge Itemを除外する。`promote_change`はこのリンクを一切作っていなかったため、Promote直後のitemが即座に他CaseのExpert Engineから除外される状態だった（`docs/geopolitical-expert-engine-integration.md`に既に記載されている「この2箇所の実装が無ければActive化されたKnowledge ItemはExpert Engineへ二度と投入されなくなる」という既知の落とし穴と同種）。`_link_supporting_evidence_for_promotion()`を新設し、Expert Final文書のchunkを根拠にClaim/Evidenceを作成してリンクするよう修正（既存`geo_candidate_extraction_service.py`と同じ対応パターン）

いずれも実DB E2E検証で初めて発見できた（FakeSupabaseClientによる既存の単体テスト・回帰テストでは検出不可能な種類のバグ）。修正後、回帰テスト2件を`tests/test_geo_expert_review.py`へ追加し、実DB E2Eを再実行して全項目PASS・クリーンアップ0件を再確認済み。`pytest tests/ -q`で174件中168件pass（既知の無関係な失敗6件は変化なし）。

### 次の作業
Phase 2B-2は完了。ユーザーからの追加フィードバック待ち、または保留中の2件（WTO/UN Comtrade/ManifestoのAPIキー、Theater本格Entity/Issue投入）の解除待ち。

### 手動作業が必要な項目
なし（migration適用・E2E検証とも完了）
