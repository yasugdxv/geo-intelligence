# 地政学インテリジェンス基盤 MVP — 実装計画

対象: `geopolitical_monitor_dashboard`
設計基準: `地政学インテリジェンス基盤_統合設計案_v2.xlsx`（ユーザー提供） / `CLAUDE_CODE_BUILD_INSTRUCTIONS.md`
初期テーマ: 台湾有事・台湾海峡危機（`Theme`設定データの1件として実装、専用ロジックとしてハードコードしない）

このドキュメントは指示書 第0章・第19章・第23章の要求に基づき、Phase 0（本ラウンド）の成果物として作成する。
Phase 1以降はここに記載した順序・完了条件に従って縦切りで実装し、各Phase終了時に「実装済み／未実装／既知問題／テスト結果」を報告する。

---

## Phase 0: Repository Assessment（本ラウンド）

### 成果物
- `docs/implementation-plan.md`（本ファイル）
- `docs/architecture.md`
- `docs/assumptions.md`
- `docs/data-model.md`

### 完了条件
- 既存コード（サステナビリティ記事・競合モニタリング機能）と新規機能（地政学ドメイン）の境界が明確 → `docs/architecture.md`の「切り分け方針」参照
- local devの起動方法が確定 → 既存`.claude/skills/run-world-monitor-dashboard`と同じFastAPI(8000)+Vite(自動選択ポート)構成を踏襲、Phase 1開始時にスキルをこのプロジェクト用に複製・パス修正する
- Azureとlocal adapterの方針が確定 → `docs/architecture.md`のアダプター設計参照（既存`ai_client.py`/`config_utils.py`を再利用）

このPhaseではコード・SQL・設定ファイルの変更は行わない。

---

## Phase 1: Vertical Slice（次ラウンド、要別途計画）

指示書7〜9章に基づき、最初の縦切りは以下の8項目とする。

1. 台湾テーマseed（`config/geo_taiwan_theme_seed.yaml` → `POST /api/geo/themes/seed/taiwan`）
2. 手動文書アップロード（1画面、PDF/HTML想定）
3. Document保存（`geo_documents`、hashによる重複検知）
4. Event Candidate抽出（LLM、JSON Schema検証、`verified=false`で保存）
5. Evidence表示（原文引用・出典URL・document_idへの遡及）
6. 1指標（MIL-01: PLA航空活動・中間線越境）のObservation生成
   - 外部取得が困難な場合は`is_mock=true`のサンプル文書を使用し、UIで明示する
7. Executive Overviewへの最小表示（総合レベル・Top Changes相当のプレースホルダ）
8. audit log（誰が何を参照・操作したかの記録）

### 完了条件（指示書21.1章に整合）
- UIから原文 → Event → Evidence → Observationへ追跡可能
- 同一文書の再取得が重複しない（content hashベース）
- Pydanticスキーマによるvalidationがある
- unit test（indicator計算・schema validation）とintegration test（upload→document→event→evidence）が通る

---

## Phase 2: Data & Signal MVP

- 12指標（指示書7.2）をconfig/DBから登録
- 公式ソースconnector scaffold（台湾国防部→台湾海巡署→台湾大陸委員会→台湾総統府→TSMC IR→外務省海外安全、の順で縦切り）
- Signal Monitor画面（7領域ヒートマップ・指標カード・時系列・baseline/deviation）
- Scenario Conditions（6シナリオの条件管理、条件充足状態の表示のみ）
- Alert Level 0〜2（自動生成はLevel0/1のみ、Level2はアナリスト承認必須）
- Evidence Timeline画面
- Alert & Action Center画面

## Phase 3: Expert AI MVP

- 7モードRouter（`theme_design`/`research_design`/`case_analysis`/`outline_review`/`draft_review`/`executive_brief`/`analyst_coaching`）
- Source Pack構築ロジック
- 既存`ai_client.py`のAzure OpenAI adapterを`LLMProvider`インターフェース越しに再利用
- Structured Response（`schemas/expert_ai_response.schema.json`によるvalidation）
- Quality Guard（指示書14.6の9項目チェック）
- Expert AI Workspace画面
- Prompt/Knowledge/Audit versionの保存

## Phase 4: Internal Report MVP

- ファイルアップロード（PDF/PPTX/DOCX/HTML/Markdown）
- metadata入力（title/department/owner/as_of/topic/region/time horizon/report type/status/confidentiality/allowed departments/valid_until）
- Report Claims抽出（Fact/Official Claim/Observation/Assessment/Assumption/Scenario/Impact/Recommendation/Uncertainty）
- RAG（pgvector利用可否をこの時点でSupabase側に確認、不可なら暫定的にキーワード検索でフォールバックしUIに明示）
- Report Library画面（status/as_of/validity/confidentiality表示、見解の前後比較）

## Phase 5: Hardening

- Entra ID認証への切り替え（MVPのモック認証から移行）
- 権限に基づくアクセスフィルタリング（検索結果・ベクトル検索・LLM入力への混入防止）
- バックグラウンドジョブ基盤（必要性が明確になった時点でAzure Service Bus等を検討、それまでは同期実行/バッチスクリプトを継続）
- retry/dead letter
- observability（構造化ログ、request_id/trace_id、LLM呼び出しログの整備）
- load and security tests
- deployment manifests（既存`Dockerfile`/`docker_entrypoint.py`/`DEPLOY_GUIDE.md`を拡張）

---

## Claude Codeの作業ルール（指示書23章を継承、本プロジェクトのCLAUDE.mdとも整合）

1. 各Phase開始前に計画を提示し、承認を得てから着手する
2. 既存リポジトリの規約（`SupabaseClient`パターン、`config_utils.py`、`tests/_fakes.py`、i18n `STRINGS`パターン等）を優先する
3. 要件を黙って削らない。差分・矛盾は`docs/assumptions.md`に記録する
4. Phaseごとに動く状態を維持する（壊れた中間状態でPhaseを終えない）
5. 各Phase終了時に「実装済み／未実装／既知問題／テスト結果」を報告する
6. 実データ取得ができない場合はモックを使ってよいが、UI・DBで`is_mock`を明示する
7. 外部ソースの仕様変更に備え、connector単位で分離する
8. AI Promptはソースコード中に散在させず、`prompt_versions`テーブル＋ファイルでversion管理する
9. 完了条件を満たさない状態を「完了」としない
10. 大量の一括変更・既存コードの無断置換はしない（CLAUDE.md準拠）
