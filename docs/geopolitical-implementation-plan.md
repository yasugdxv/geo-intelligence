# 地政学ナレッジ＆インテリジェンスDB — 実装計画

対象: `geopolitical_monitor_dashboard`
承認済みPlan: `C:\Users\269811\.claude\plans\warm-orbiting-lemur.md`（このドキュメントはその実行版）

## Phase 1: ナレッジ蓄積・レビュー・再利用の中核ループ（本ラウンド）

以下のループを実際に動かすことだけに集中する。

```
Case作成 → 既存ナレッジ検索 → Foundation Pack生成 → Knowledge Gap特定
→ 追加Evidence登録 → Case分析 → Knowledge Candidate抽出
→ 人間によるレビュー → Active Knowledge化 → 次のCaseで再利用
```

### 1.1 DB Migration（`sql/2026-08-04_*.sql`、ユーザーが手動適用）
1. `geo_knowledge_core.sql` — 新規テーブル一式＋既存`geo_source_registry`/`geo_documents`のALTER（詳細: `docs/geopolitical-data-model.md`）
2. `geo_knowledge_search_fts.sql` — tsvector/GINインデックス
3. `geo_knowledge_pgvector_optional.sql`（任意）— vector拡張・embedding列の準備のみ

### 1.2 Pythonモジュール（新規、既存コード非改変）
- `geo_knowledge_item_schemas.py` — knowledge_type別JSON Schema
- `geo_entity_service.py`, `geo_entity_resolution.py`
- `geo_relationship_service.py`
- `geo_knowledge_service.py`（status遷移、review記録）
- `geo_claim_service.py`（claim/evidence登録、document chunking呼び出し）
- `geo_retrieval_service.py`（Retrieval Layer interface）
- `geo_foundation_pack_service.py`
- `geo_knowledge_gap_service.py`（deterministic + analytical候補呼び出し）
- `geo_candidate_extraction_service.py`
- `connectors/geo_connector_csv_excel_json.py`（手動import、pandas使用）
- `geo_documents_store.py`の拡張（chunking追加。既存関数のシグネチャは壊さない）

### 1.3 API（`api_server.py`に`geo_knowledge_router`追加、既存`/api/geo/*`は変更しない）
entities, relationships, knowledge, knowledge/{id}/reviews, cases, cases/{id}/foundation-pack, cases/{id}/detect-gaps, cases/{id}/extract-candidates, knowledge-candidates, sources（手動登録）, documents, document-chunks, claims, claim-evidence, audit-log

### 1.4 フロントエンド（4画面）
`geo.knowledge-search.tsx`, `geo.cases.tsx`/`geo.cases.$id.tsx`, `geo.knowledge-review.tsx`, `geo.sources-documents.tsx`

### 1.5 テスト
- 単体: entity resolution, knowledge status遷移（許可外遷移の拒否含む）, claim/evidence登録, schema validation, demo isolation
- 統合: Case作成→Foundation Pack→Gap検出→Candidate抽出→Review→Active化の一連（`tests/_fakes.py`のFakeSupabaseClient使用）
- E2E: 「第三国による国際海峡管理・航行保護・通行料徴収」ケース（`tests/test_geo_knowledge_e2e.py`、24ステップ。詳細はPlanファイル参照）

### 1.6 完了条件
- migration適用後（ユーザー作業）、上記E2Eケースが実際のCase作成からActive Knowledge化・次Case再利用まで一気通貫で動く
- Candidateが承認前に本文へ事実として混入しない
- Demo Fixtureが既定で除外される
- 全claimがEvidence→Source Documentへ遡れる

## Phase 2A: 実運用接続（完了）

承認済みPlan: `C:\Users\269811\.claude\plans\warm-orbiting-lemur.md`。詳細設計は`docs/geopolitical-phase2a-architecture.md`ほかPhase2A新規ドキュメント群を参照。概要のみここに記す。

1. Phase1成果物の検証（`docs/geopolitical-phase1-completion-review.md`）
2. `geo_expert_engine`とKnowledge DBの統合（`case_id`指定時のみRetrieval Layer経由でContextを統合、既存呼び出しは完全後方互換。`docs/geopolitical-expert-engine-integration.md`）
3. World Bank Indicators / OFAC Sanctions Listの自動Connector（`docs/geopolitical-connector-guide.md`）
4. FTS中心のHybrid Retrieval（完全一致＋正規化部分一致＋`ts_rank_cd`の合成。Vector Searchは見送り。`docs/geopolitical-retrieval-design.md`）
5. 鮮度・Ingestion Run管理（`docs/geopolitical-freshness-policy.md`、`docs/geopolitical-ingestion-operations.md`）
6. Case作成→Connector自動取得→Hybrid検索→Expert Engine分析→Retrieval Trace確認という一連の運用フローが動く状態

完了条件（`tests/test_geo_phase2a.py`18件＋既存Phase1回帰17件、計35件で確認済み）: Raw Snapshotがcontent_hashで重複防止・Immutable、OFACレコードが上書きされずversion保持（current_statusとchange_typeが独立管理）、delisted判定がRun completed時のみ発生、Evidence未到達のactive Knowledge ItemがExpert Engineから除外、theme_idのみの既存Expert Engine呼び出しが後方互換。

## Phase 2B 第1弾: 追加Connector / Network Graph / Indicator Monitor（完了）

承認済みPlan: `C:\Users\269811\.claude\plans\warm-orbiting-lemur.md`（Phase2B第1弾、ユーザーレビューにより認証要件・Credential管理・巨大IN句回避・閾値方向・Freshness独立性・Manifesto範囲を当初案から修正）。

1. 追加Connector（`docs/geopolitical-connector-guide.md`）: IMF DataMapper API（無認証、実アクセスで検証済み）、UN Comtrade/WTO/Manifesto Project（Credential-required、Source Registry 3ゲート分離＋credential reference方式）。Manifesto ProjectはMain Datasetのみ、Corpus全文は対象外
2. Network Graph表示（`docs/geopolitical-network-graph.md`）: DB側RPC1回でnode/edgeを返し、巨大`in.()`によるURL長超過を構造的に回避。`react-force-graph-2d`導入
3. Indicator Monitor画面（`docs/geopolitical-indicator-monitor.md`）: 閾値超過アラート（direction/operator対応、Observation単位で冪等）、Indicator Freshness（Knowledge/Source Freshnessとは独立した第3の判定関数）

完了条件（新規Mock Test 29件＋既存回帰、計148件中142件pass。既知の無関係な失敗6件は既存の`test_sustainability_article_selector.py`5件＋`test_geo_official_crawler.py`1件、いずれもテスト固定日付と実時刻の相対関係に起因する既存の環境依存でありPhase2Bの変更とは無関係）: Source Registryの3ゲートが独立して機能し`automation_allowed`がAPIキー有無で自動変化しないこと、Network GraphがRPC経由でしか動作せず巨大in.()を使わないこと、閾値アラートがObservation単位で冪等であること。

## Phase 2B 次ラウンド以降

- OFACの国籍・身分証明書番号取り込み（`sdn_advanced.xml`。住所[`add.csv`]は2026-08-19に取り込み済み。`citizen.csv`/`id.csv`という単純CSVは実在しないことをWeb検索で確認済み）、Manifesto ProjectのCorpus全文取り込み・chunking、Babel Street/Generic Authorized HTTP Connectorの追加
- 常駐Scheduler化（現状は手動トリガー＋外部スケジューラ呼び出しのみ）
- Embedding Deployment確認後のpgvector類似検索導入（`geo_knowledge_pgvector_optional.sql`は適用済み、次元数は確認後に再検討）
- Russia-Ukraine-EU-NATO / Iran-Israel-Gulf-Hormuz Theaterへの本格データ投入
- 本格RBAC・Entra ID認証連携
- Entity自動Merge、Contested自動解消（現状は人間のsteward操作のみ）
- Knowledge Item/Source向け鮮度バッジの一覧画面表示（Indicator向けは今回実装済み）
- Alert既読(acknowledged_at)設定UI、Alert通知連携（Slack/Teams/メール）

## Phase 2B-2: External Reference Model & Internal Expert Learning Layer（完了）

承認済みPlan: `C:\Users\269811\.claude\plans\pure-drifting-cosmos.md`（ユーザーレビューにより、Source Reference Policyの導入・status/retrieval_statusの真の独立性・Internal Expert AssessmentとReasoning Ruleの分離・Link Table化・reinforced概念・スコアリング信号分離・用語統一・冪等性/Note ID検証の計19点を当初案から修正）。

地政学の外部情報（Think Tank分析・政府発表・報道分析等）を「人間が承認して正しいKnowledgeにする」モデルから、「誰が・いつ・どの立場から何を述べたかを保持しそのままAIが参照する」モデルへ転換し、あわせて社内専門家のAI Draft→Expert Final差分からReasoningを抽出・蓄積する仕組みを新設した。

1. External Reference Model（`docs/geopolitical-external-reference-model.md`）: Retrieval Status（available/restricted/deprecated/archived）とEpistemic Type（既存`information_class`に`internal_expert_assessment`を追加）の二軸モデル。Source Reference Policy（`geo_source_registry.reference_availability`、Steward操作、Claim個別のHuman Reviewは要求しない）
2. Internal Expert Learning Layer（`docs/geopolitical-internal-expert-learning.md`）: Expert Review Session→Delta Extraction（LLM、Reviewer Note優先反映）→Promote/Reinforce/Keep Case Only/Rejectという新フロー。既存の`create_knowledge_item`/`review_knowledge_item`をそのまま呼ぶだけで新しい状態遷移ロジックは作らない
3. Expert Engineの5セクション追加（`docs/geopolitical-expert-engine-integration.md`4.1章）: 観測・記録された事実情報/Actor Claims/External Assessments/Internal Expert Assessments/Internal Expert Reasoning Rules。既存6セクションは無変更
4. Retrieval Governance API（`docs/geopolitical-internal-expert-learning.md`5章）: Knowledge Item/Claim/Source単位でのretrieval_status変更（内容の正誤判定ではない運用操作）

完了条件（新規テスト29件＋既存回帰、計172件中166件pass。既知の無関係な失敗6件は既存の`test_sustainability_article_selector.py`5件＋`test_geo_official_crawler.py`1件、Phase2B-2の変更とは無関係）: `retrieval_status`が`status`から独立して変更可能であること（後続レビュー操作が上書きしない）、Source方針変更が`available`/`restricted`のClaimのみ再同期しdeprecated/archivedを復活させないこと、Expert Review系操作（promote/reinforce/keep/reject）が冪等であること、LLMが返した実在しないnote_idが無視されること。

### 手動作業が必要な項目

1. Supabase SQL Editorで`sql/2026-08-19_geo_phase2b2_external_reference.sql`を適用する（Phase1・Phase2A・Phase2Bのファイルが適用済みであることが前提）
2. 適用後、地政学バックエンド（`api_server.py`、API_PORT=8001）を再起動する
3. 実DBに対しis_demo=trueでE2Eシナリオ（承認済みPlan13章）を1回通し確認後に削除することを推奨（Theaterに実Entity/Issueデータがまだ無いため、検証用の最小限のis_demo Entity/Issueを一時作成する必要がある点は実施前にユーザーへ確認する）

## Phase 3（将来）

本格運用、複数Theater展開、品質評価・仮説精度の事後評価、taxonomy/source/全体構造の年次棚卸しの自動化検討。

## Claude Codeの作業ルール（既存`docs/implementation-plan.md`の運用ルールを継承）

1. 既存リポジトリの規約（`SupabaseClient`パターン、`config_utils.py`、`tests/_fakes.py`）を優先する
2. 要件を黙って削らない。差分・スコープ縮小は本ドキュメントおよび`docs/geopolitical-implementation-status.md`に明記する
3. Phaseごとに動く状態を維持する（壊れた中間状態でPhaseを終えない）
4. 各Phase終了時に「実装済み／未実装／仮定／既知の制約／リスク／次の作業／手動作業が必要な項目」を`docs/geopolitical-implementation-status.md`へ追記する
5. 既存の`geo_events`/`geo_evidence`/`geo_expert_engine`等、現在動いているコード・データは変更しない
6. SQL migrationはこのセッションから実行しない（Supabase SQL Editorでユーザーが手動適用）
7. 新規pip installが必要になった場合は実行前に確認する
8. 大量の一括変更・既存コードの無断置換はしない（CLAUDE.md準拠）
