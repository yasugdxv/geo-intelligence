# 地政学アプリ｜未実装・未接続機能 Closeout（Phase1〜7）

本ドキュメントは、「実装済みと認識していたが実際には未実装・未接続だった機能」のCloseout
（承認済みPlan: `C:\Users\269811\.claude\plans\pure-drifting-cosmos.md`）の完了時点の状態を記録する。

## 1. 実装結果

| Phase | 機能 | Before | After | Status |
|---|---|---|---|---|
| 1 | Intelligence AI画面の接続 | `/api/geo/chat`は簡易MVP実装（Retrieval/Sufficiency/Escalation/Needs登録を一切通らない） | `geo_intelligence_service.process_request`を直接呼ぶブリッジへ差し替え。Theme Context/Conversation Contextを分離して保持しつつ、Retrieval→Sufficiency→（既存条件で）Expert AI→Needs登録まで実行。sufficiency/expert_ai_called/intelligence_need_created/idを応答へ追加 | ✅ 実装・実データE2E確認済み（sufficient分岐含む、6章参照） |
| 2 | Intelligence LibraryへReport統合 | Knowledge Item/Entityのみ検索可能、Reportは一覧UIなし | Report検索（ILIKE、新規RPCなし）・Reportsタブ追加・一覧へtitle/theme等を付加 | ✅ 実装・実データE2E確認済み |
| 3 | Report Update/Versioning | 既存documentの再アップロードは無変更no-opのみ、Update手段なし | version/supersedes_id/report_identity_id追加。latest版からのみ新版作成可（非latestは409）。no-op版作成は409/422で拒否。diff（決定的処理）追加 | ✅ 実装・DB migration適用済み・実データE2E確認済み |
| 4 | Report Review → Knowledge Learning Loop | Expert Review LoopはCase専用（report_id無し） | `geo_expert_review_sessions.report_id`追加、`report_finding`知識型追加、exactly-one制約、Promoteのreport_finding分岐 | ✅ 実装・DB migration適用済み・実データE2E確認済み |
| 5 | Phase3+4の接続 | 個別には動くが繋がっていない | `start_report_review_and_version`オーケストレーション追加。API `POST /{report_id}/review`。フロントは共通`<ExpertReviewLoop>`へ切り出し、Report Detail（Reportsタブ展開）から呼べる。Promote後の知識がIssue紐付けされ既存Retrievalから見つかることを確認（既存の未接続も同時に修正） | ✅ 実装・実データE2E確認済み（report_finding/mechanismの両方の再利用を確認） |
| 6 | Intelligence Needs dismiss経路統一 | 汎用PATCHでも`status=dismissed`に変更可能（resolved_by/resolution_note未記録） | 汎用PATCHで`dismissed`指定を拒否（ValueError→400）。フロントに専用「却下」ボタン＋理由入力を追加、ドロップダウンから`dismissed`を除去 | ✅ 実装・単体テスト済み |
| 7 | デッドコード・古い経路整理 | - | 主要な整理完了（下記6章） | ✅ |

## 2. 変更ファイル

**Backend（新規）**
- なし（全て既存モジュールへの追加）

**Backend（変更）**
- `geo_intelligence_service.py` — `expert_ai_called`フィールド追加、`_call_expert_ai`のcontext付加、`theme_id`によるissue解決の追加経路（`resolve_entities_and_issues`拡張）
- `geo_intelligence_schema.py` — `GeoIntelligenceResponse.expert_ai_called`追加
- `api_server.py` — `geo_chat`ハンドラを本格パイプライン接続へ全面差し替え、`GeoChatResponse`追加
- `geo_search_service.py` — `search_reports`追加
- `geo_report_service.py` — `search_reports`/`list_reports`（latest-only）、Versioning関数群（`create_report_version`/`get_latest_report_version`/`list_report_versions`/`diff_report_versions`）、`start_report_review_and_version`オーケストレーション、`ingest_report`のv1自己完結化
- `geo_report_api.py` — `/search`、`/diff`、`/{report_id}/versions`、`/{report_id}/latest`、`POST /{report_id}/versions`、`POST /{report_id}/review`追加
- `geo_knowledge_item_schemas.py` — `REPORT_FINDING_SCHEMA`追加
- `geo_expert_review_service.py` — `report_id`対応、exactly-one検証、`_resolve_session_issue_ids`共通化、Promote後のIssue紐付け追加
- `geo_expert_review_api.py` — `StartReviewSessionRequest.report_id`追加
- `geo_retrieval_service.py` — `get_internal_expert_assessments`の`knowledge_type`フィルタへ`report_finding`追加
- `geo_knowledge_gap_service.py` — `update_intelligence_need`が`status=dismissed`を拒否
- `api_server.py`（**実DB/実Azure OpenAI E2E検証で追加発見・修正**）
  - `_GEO_CHAT_TIMEOUT_SECONDS`を60秒→280秒へ変更。実測でsufficient＋Expert AI呼び出しを
    含む処理が81秒〜256秒まで変動することを確認し、60秒（旧`geo_intelligence_api.py`と
    同じ値を踏襲していた）では実運用に不足していた。
  - `geo_chat`ハンドラの`sources`組み立てを、単純な`references[:5]`から「internal種別
    （Report/Knowledge Item由来）を先頭に並べ替えてから`[:8]`」へ変更。旧実装だと、
    `references`の末尾に位置するinternal_report/internal_expert_assessmentが表示から
    漏れ、Report Library統合（Phase2/5）の目的である「アップロードしたReportが
    Intelligence AI回答で実際に見える」が達成できていなかった（後述E2E Cで発見）。

**Frontend（新規）**
- `eco-digest-spark/src/components/geo/expert-review-loop.tsx`（`geo.cases.$id.tsx`から切り出し）
- `eco-digest-spark/src/components/geo/report-detail-card.tsx`

**Frontend（変更）**
- `eco-digest-spark/src/lib/api.ts` — `ChatMsg`拡張、`useGeoChatSend`戻り値拡張
- `eco-digest-spark/src/components/geo/geo-ai-chat.tsx` — Sufficiency/ExpertAI/Need バッジ追加
- `eco-digest-spark/src/lib/i18n.tsx` — 関連キー追加
- `eco-digest-spark/src/lib/geoKnowledgeApi.ts` — `useGeoReports`/`useSearchReports`/`useReportVersions`/`useReportDiff`/`useStartReportReview`追加
- `eco-digest-spark/src/routes/geo.knowledge-search.tsx` — Reportsタブ追加
- `eco-digest-spark/src/routes/geo.cases.$id.tsx` — `<ExpertReviewLoop>`へ差し替え
- `eco-digest-spark/src/routes/geo.intelligence-needs.$id.tsx` — dismiss専用導線追加、ドロップダウンから`dismissed`除去

**Tests（新規）**
- `tests/test_geo_chat_bridge.py`（8件）
- `tests/test_geo_report_search.py`（6件）
- `tests/test_geo_report_versioning.py`（9件）
- `tests/test_geo_report_review_loop.py`（7件）
- `tests/test_geo_report_review_orchestration.py`（2件）
- `tests/test_geo_intelligence_gap_service.py`へ2件追加（dismiss経路）
- `tests/_fakes.py` — `ilike.`演算子の解釈を追加（既存の未実装を修正、副次的に2件の既存テストの実データ不整合を発見・修正）

## 3. DB変更

2件の新規migrationファイルを作成し、**両方とも実DB（Supabase project `wlvjszxdyswyyrnumops`）へ
適用済みであることを本セッションで直接確認した**（`information_schema`相当の列存在チェック、
および下記3-2のCHECK制約の実挙動テストの両方で確認）。

1. `sql/2026-08-29_geo_report_versioning.sql` — `geo_reports`へ`version`/`supersedes_id`/
   `report_identity_id`追加、`document_id`単独UNIQUEを`(report_identity_id, version)`複合
   UNIQUEへ置き換え。
2. `sql/2026-08-29_geo_report_review_loop.sql` — `geo_expert_review_sessions`へ`report_id`
   追加、`review_type`へ`report_review`追加、`case_id`/`report_id`のexactly-one制約追加、
   `geo_knowledge_items.knowledge_type`へ`report_finding`追加。

### 3-1. 列の存在確認

`geo_reports.version`/`.supersedes_id`/`.report_identity_id`、
`geo_expert_review_sessions.report_id`の4列すべて実DBに存在することを確認済み。

### 3-2. 制約の実挙動確認（DBへ実際に不正な行をINSERTして検証）

すべて実Supabase（`requests`によるREST直叩き）で確認。サービス層のバリデーションを経由せず
DB制約自体が働くことを確認するため、あえてPythonのSupabaseクライアントで直接INSERTを試行した。

| 制約 | 検証方法 | 結果 |
|---|---|---|
| `geo_reports_version_check`（`version >= 1`） | 実在する`document_id`を使い`version=0`でINSERT | **23514 (check_violation)** で拒否を確認 |
| `geo_expert_review_sessions_exactly_one_subject_check`（`num_nonnulls(case_id, report_id) = 1`） | `case_id`/`report_id`両方NULLでINSERT | **23514 (check_violation)** で拒否を確認 |
| `geo_reports_identity_version_key`（`UNIQUE(report_identity_id, version)`＋latest-onlyポリシー） | 非latest版（v1）からの新版作成APIコール | サービス層で**409**（下記E2E D参照） |
| no-opバージョン拒否 | document内容・metadataとも同一の新版作成APIコール | サービス層で**422**（下記E2E D参照） |
| `geo_knowledge_items.knowledge_type`のCHECKへの`report_finding`追加 | 実際に`report_finding`のKnowledge Itemを作成 | 正常にINSERT成功（下記E2E D参照） |

### 3-3. 既存Reportのbackfill確認について

実DBの`geo_reports`テーブルは、本Closeoutでの実データ検証開始時点で**0行**だった
（本Closeout以前にReport Uploadが実運用で使われていなかったため）。したがって
「既存Reportが正常にbackfillされていること」を確認する対象データが実DBには存在せず、
この確認項目は**該当データなしのため実質的に該当なし（moot）**である。backfillロジック
自体（`report_identity_id = report_id`のUPDATE文）はmigration SQL上に存在し、新規行の
自己完結ロジック（`ingest_report`がPython側で`report_id`を先に生成し、
`report_identity_id`/`version`/`supersedes_id`を最初から自己完結させる）は下記E2E Cの
実アップロードで動作確認済み。

## 4. API

**新規**
- `GET /api/geo/reports/search?q=`
- `GET /api/geo/reports/diff?from_report_id=&to_report_id=`
- `GET /api/geo/reports/{report_id}/versions`
- `GET /api/geo/reports/{report_id}/latest`
- `POST /api/geo/reports/{report_id}/versions`
- `POST /api/geo/reports/{report_id}/review`

**変更**
- `POST /api/geo/chat` — レスポンスへ`sufficiency`/`expert_ai_called`/`intelligence_need_created`/
  `intelligence_need_id`を追加（既存`reply`/`sources`は維持）
- `PATCH /api/geo/intelligence-needs/{gap_id}` — `status=dismissed`指定時に400を返す

## 5. Frontend接続

**Intelligence AI（最重要）**:
```
Frontend (geo.intelligence-ai.tsx / geo.theme.$themeId.tsx, GeoAiChatPanel)
  → useGeoChatSend → POST /api/geo/chat（api_server.py, geo_chat）
  → geo_intelligence_service.process_request（同一プロセス内直接呼び出し、
    /api/v1/geo-intelligence/query の verify_api_key ゲートは経由しない）
  → retrieve_knowledge（Issue単位Retrieval＋FTS横断検索）
  → assess_sufficiency（sufficient/partial/insufficient）
  → generate_analysis → should_call_expert_ai（sufficient かつ analysis_requested かつ
    azure_client利用可能な場合のみ）→ 既存Expert AI（run_expert_request_with_mode）
    または_digest_analysis（LLM不使用の要約）
  → sufficiency != sufficientならgeo_knowledge_gap_service.record_external_request_gap
    でIntelligence Need自動登録
  → GeoAiChatPanelがsufficiency/expert_ai_called/intelligence_need_createdバッジを表示
```

**Intelligence Library**: `geo.knowledge-search.tsx`のReportsタブ → `useGeoReports`/
`useSearchReports` → `geo_report_service.list_reports/search_reports` → `ReportDetailCard`
で展開 → Version履歴（`useReportVersions`）・diff（`useReportDiff`）・Review開始
（`useStartReportReview` → `start_report_review_and_version`）→ `<ExpertReviewLoop>`
（Caseページと共通コンポーネント）でPromote/Reinforce/Keep/Reject。

## 6. テスト結果

- **Unit/Integration**: `python -m pytest tests/ -q` → 378 passed / 6 failed
  （既知の無関係6件、`test_geo_official_crawler.py`/`test_sustainability_article_selector.py`、
  本Closeout以前から存在する既存課題。api_server.pyのタイムアウト値/sources組み立て変更後も
  再実行し同じ結果であることを確認、新規リグレッションなし）。新規追加32件はすべてグリーン。

- **E2E（実DB + 実Azure OpenAI、全項目実施・完了）**: 2件のmigrationが実DBへ適用済みで
  あることを確認した上で、以下をすべて実際に稼働中の`api_server.py`（実HTTP、実Supabase、
  実Azure OpenAI）で実行した。使用データ: `hormuz_strait`テーマ
  （`theme_id=9a62d084-1442-45c9-96fc-50e2a0d3e1a9`）、`country_region=["Iran"]`
  （既存Entity「Iran」が2件のIssueへ紐付け済みであることを事前確認した上で使用）。

  **前提確認**
  - 4列（`geo_reports.version/supersedes_id/report_identity_id`、
    `geo_expert_review_sessions.report_id`）すべて実DBに存在（3-1章）。
  - CHECK制約2件（version/exactly-one）が実際にDBレベルで不正行を拒否することを確認（3-2章）。
  - 既存Reportのbackfillは対象データなし（0行）のため該当なし（3-3章）。
  - 通常Report Upload（v1）: `POST /api/geo/reports`を実HTTPで実行し、
    `version=1, supersedes_id=null, report_identity_id=report_id`と自己完結して
    作成されることを確認（migration後もv1作成は壊れていない）。

  **E2E C（Report Upload → Library → Issue紐付け → Intelligence AI Retrieval）**
  1. 実Report Upload（`POST /api/geo/reports`、`report_id=d5ddc6d5-...`）→ 200、
     `issue_ids`2件（`china-iran-influence`/`hormuz-reopening`）が実際に紐付いた。
  2. `GET /api/geo/reports/search?q=ホルムズ` → 該当Reportがヒット。
     `q=World Bank`/`q=OFAC`（Report以外の文書に存在するキーワード）→ **0件**、
     Phase2で懸念していた非Report文書との誤マッチが実データでも発生しないことを確認。
  3. `geo_claim_issues`を直接クエリし、Reportから生成されたClaimが実際に2件のIssueへ
     紐付いていることをDBレベルで確認。
  4. `POST /api/geo/chat`（`theme_id`指定、質問「ホルムズ海峡でのイランによるタンカー臨検
     強化について、既存の分析レポートはありますか？」）→ `sufficiency=sufficient`、
     `expert_ai_called=true`、`sources`の先頭2件がアップロードした当該Reportの
     Claim本文／タイトルであることを確認。
     **この過程で2件の実装ギャップを発見・修正**（`_GEO_CHAT_TIMEOUT_SECONDS`延長、
     `sources`のinternal優先並べ替え。詳細は2章参照）。修正後、`references`ではなく
     実際にAPIレスポンスの`sources`（フロントエンドが表示するフィールド）にReportが
     現れることを確認した。

  **E2E D（Report v1 → Review → v2/v3 → Diff → Promote → 次回Retrievalで再利用）**
  1. `POST /{report_id}/review`（v1→v2、レビュー後ファイル添付）→ 200。
     `version=2, supersedes_id=<v1のreport_id>`で新版作成、レビュー結果を反映した
     `review_session`（`report_id`は**v2**を指す、v1ではない）と、実Azure OpenAIによる
     delta抽出（`reusability_class=reusable_reasoning`、1件）が自動実行された。
  2. `GET /{report_id}/versions`・`GET /diff?from_report_id=&to_report_id=` →
     v1/v2/v3の3版履歴、`difflib`によるunified diffとmetadata_diffが正しく返る。
  3. `POST /changes/{change_id}/promote`（reusable_reasoning分） → `knowledge_type=mechanism`
     のKnowledge Item作成、`geo_knowledge_item_issues`へ2件のIssue紐付けが**自動的に**
     行われることを確認（Promote後のIssue紐付け追加、2章参照）。
  4. 追加でv2→v3のReviewサイクルをもう1回実施し、`reusability_class=case_specific_assessment`
     の変更を意図的に誘発（本件固有の時点判断を含む内容で再レビュー）。Promoteした結果、
     `knowledge_type=report_finding`のKnowledge Itemが作成され、
     `structured_content.report_id`が**v3（レビュー確定後の最新版）**を指すことを確認
     （v1/v2ではない）。
  5. `geo_retrieval_service.get_internal_expert_assessments`を実DBに対して直接呼び出し、
     上記report_findingが**1件返る**ことを確認（`knowledge_type in (case_finding,
     report_finding)`への拡張が実際に効いている）。
  6. `POST /api/geo/chat`で改めて関連質問を2パターン投げ、(a) mechanism型Knowledge、
     (b) report_finding型Knowledgeの両方が、それぞれ別のクエリの`sources`先頭付近に
     実際に現れることを確認。**Promote→Retrieval再利用の全経路を実データで実証**。
  7. Versioning制約の実運用確認: 非latest版（v1）から新版作成を試みると**409**、
     v3と完全同一内容・metadataでの新版作成を試みると**422**で、いずれもサービス層で
     正しく拒否されることを確認。

  **発見した実装ギャップ（本セッションで修正済み）**
  - `_GEO_CHAT_TIMEOUT_SECONDS`が60秒（旧`geo_intelligence_api.py`と同じ値）では、
    実Azure OpenAIでのsufficient＋Expert AI呼び出しパス（実測81〜256秒）に対して
    明らかに不足しており、504を引き起こしていた。280秒へ変更。
  - `geo_chat`ハンドラの`sources`が`references[:5]`という単純な先頭切り詰めだったため、
    `references`配列の末尾に位置しがちなinternal_report/internal_expert_assessment
    （Report Library統合の主目的）が表示から漏れていた。internal種別を優先する並べ替えに
    変更（`[:8]`）。

## 7. 残課題

**今回対象外（Plan記載の明示的スコープ外）**
- Outbound Intelligence Needs Discovery、Sustainability Step2、Actor Intelligence再設計、
  Sustainability↔Geopolitics間のScheduler/Background Job/Retry基盤。

**技術的に残った課題**
1. **Intelligence AIパイプラインのレイテンシ**: 実Azure OpenAIでsufficient＋Expert AI呼び出しを
   含む処理が81秒〜256秒と大きく変動することが実測で判明した。今回はタイムアウト値を280秒へ
   延長する対症療法で対応したが、根本的にはExpert AIエンジン内の複数LLM呼び出しの直列実行が
   原因と推測される。フロントエンドの体感待ち時間としては長く、将来的に非同期化（ジョブ投入＋
   ポーリング/通知）を検討する余地がある（ただし本Closeoutのスコープ外のクロスアプリ非同期基盤
   とは別の、単一アプリ内の話）。
2. **`sources`の表示件数上限（`[:8]`）**: internal優先の並べ替えで当面の問題は解消したが、
   将来的にinternal Reportやreport_finding/case_findingの件数がさらに増えた場合、再び
   externalなTheme Context由来の情報が表示から押し出される可能性がある。ハードコードされた
   件数上限である点は変わっていないため、フロントエンドでの「もっと見る」的なUI拡張、または
   `internal`/`external`を別々のフィールドとして返す設計変更が将来候補。
3. **実データでのsufficient分岐の実証（Phase1既存Case群）**: 本Closeout以前からの既存
   Hormuzテーマデータ（Case群）単体では、Expert AI Escalationが発火する組み合わせ
   （stakeholder+dynamics+substantive knowledge同時）が揃わず、Unit/Integration
   （モックLLM）でのみ確認できていた。今回、新規Uploadした実Reportを絡めた質問では実際に
   `sufficient`＋`expert_ai_called=true`を実データ・実Azure OpenAIで再現できた（E2E C/D）ため、
   本項目は実質的に解消したと考えてよい。
4. **Report「紐づくCase」表示**: Report一覧では表示せず、詳細表示でのみ遅延計算する設計とした
   （Plan記載通り）。今回は詳細表示にもこの計算を実装していない（Version履歴・diff・Reviewを
   優先したため）。次回追加候補。
5. `tests/_fakes.py`の`ilike.`実装は`*text*`形式の単純な部分一致のみ対応（複雑な
   ワイルドカードパターンは未対応）。現状の使用箇所では十分。
6. ~~本E2E検証で実DBに投入したテストデータが残っている~~ →
   **対応済み**: Report 3版、Knowledge Item 2件、Review Session 2件、および関連する
   Claim/Claim Evidence/Claim-Issue紐付け/Document Chunk/Document（Report本体＋
   Review用draft/final文書）を、依存関係の順序（子テーブル→親テーブル）で全て実DBから
   削除し、`geo_reports`/`geo_expert_review_sessions`が0行に戻ったことを確認済み。

## 8. 追記: 全体Cleanup（2026-08-29〜30実施）

Phase1〜7 Closeout完了後、新機能追加を行わずに横断的なArchitecture Review/Optimizationを
実施した。詳細な監査結果・修正方針・Before/After計測は別ドキュメント
`docs/cleanup-step0-baseline.md`（Step 0 Baseline + Step 7 After比較）と、承認済みPlanファイル
（実装内容の一次情報）を参照。要点のみ記す:

- **P0（実害あり、修正済み）**: (1) Report Versioning導入後、旧Versionが新Versionと一緒に
  Retrieval・週次候補へ混入し続けるバグを修正（`geo_intelligence_service._resolve_internal_
  reports`に`_filter_latest_report_documents`を追加、常に高々2 SELECTで完結）。実DBで
  2つの独立したReport系列を使い、旧Versionの除外・新Versionへの正しい置換の両方を
  stable identifier基準で実証。(2) Actor Intelligenceの`retrieval_status`未フィルタを修正
  （`get_actor_profile_item_by_entity_id`/`_relevant_precedents_for_entity`に`retrieval_status`
  引数を追加、AI/Packへ渡す呼び出し箇所のみ`"available"`を指定）。
- **P1（非効率、修正済み）**: `attach_case_finding_provenance`（N+1→2 select）、
  `_resolve_relationships_assets_dependencies`（N+1→2 select、新規`list_relationships_for_
  entities`追加）、`ingest_report`の`geo_claim_issues`存在チェック（C×I select→C select、
  insert回数は不変）、`expert-review-loop.tsx`の6mutationへエラー表示追加。
- **見送った項目（P2）**: `search_reports`の全件取得列削減（title一致経路の出力データが
  破損するため見送り）、`get_relevant_relationships`の削除（grepのみでは判断不可のため
  docstring注記のみ）、`FOUNDATION_KNOWLEDGE_TYPES`のactor_profile重複投入リスク（Expert AI
  意味論変更を伴うため見送り）、`/api/geo/expert/requests`と`/api/geo/knowledge/expert/analyze`
  の統合（両方とも生きた経路のため見送り）。詳細な見送り理由はPlanファイル参照。
- **DB schema変更なし**（本Cleanupの方針通り）。
- **全体回帰**: Step 0 Baseline（378 passed / 6 known failed）→ Step 7 After（391 passed / 6
  known failed、known failureはtest node id単位でBaselineと完全一致）。
- 実DB/実Azure OpenAI E2Eで、Retrieval/Report Review→Knowledge再利用/Actor Intelligence Pack
  生成のいずれもregressionなしを確認。Intelligence AI/Expert AIのAzure call数・latencyは
  テストデータ追加・LLM非決定性により単純比較できない旨を明記（詳細はcleanup-step0-baseline.md）。
