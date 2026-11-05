# アーキテクチャ方針

## 1. 既存スタックを踏襲する（グリーンフィールド標準案は採用しない）

`CLAUDE_CODE_BUILD_INSTRUCTIONS.md` 4章は「既存リポジトリの技術を優先する。グリーンフィールドの場合は以下を標準案とする」と明記している。
`geopolitical_monitor_dashboard`は`world_monitor_dashboard`のコピーであり、既に以下が稼働実績のある形で存在するため、非グリーンフィールドと判断する。

| レイヤー | 指示書の標準案 | 本プロジェクトの採用 | 理由 |
|---|---|---|---|
| Frontend | Next.js + TypeScript + App Router | **Vite + TanStack Start + React 19 + TypeScript**（既存`eco-digest-spark`） | 既存資産（ルーティング、i18n、shadcn/ui構成、API clientパターン）をそのまま再利用できる。Next.js化は無用な書き換えコストとリスクを生む |
| UI | Tailwind + shadcn/ui + Recharts | 同左（既存構成そのまま） | 既に導入済み |
| Backend API | FastAPI + Python | 同左（既存`api_server.py`） | 既存踏襲 |
| Database | PostgreSQL | **Supabase（PostgreSQL）をPostgREST経由で利用**、既存の手組み`SupabaseClient`（`article_crawler.py`）を再利用 | 公式`supabase-py`への切り替えは不要な変更。既存パターンとの一貫性を優先 |
| Vector Search | pgvector | Phase 4着手時にSupabase側の拡張有効化を確認のうえ採用可否を判断（`docs/assumptions.md`参照） | 現時点で確認できていないため保留 |
| ORM/Migration | SQLAlchemy + Alembic | **採用しない。既存と同じ「`sql/`配下に素のSQLファイルを追加し、ユーザーがSupabase上で手動実行する」運用を継続** | 既存プロジェクト全体でこの運用が一貫しており、Alembic導入は二重管理を生む |
| Object Storage | Azure Blob Storage | MVP（Phase1-4）ではローカルファイルシステム保存のadapter実装から開始し、`ObjectStorage`インターフェース越しにAzure Blobへ後日差し替え可能にする | 現時点でAzure Blobの契約・認証情報の有無が未確認（`docs/assumptions.md`参照） |
| LLM | Azure OpenAI | 同左（既存`ai_client.py`を`LLMProvider`インターフェース越しに再利用） | 既存踏襲、Suntory社内プロキシ設定（`config_utils.py`）もそのまま使える |
| Background Jobs | Azure Service Bus + Worker | **MVPでは導入しない**。既存の競合/記事パイプラインと同様、同期実行またはバッチスクリプト（cron相当）で開始し、必要性が明確になった時点(Phase 5)で再検討 | 既存プロジェクトにジョブキューが存在せず、MVPの規模では過剰 |
| Authentication | Microsoft Entra ID | **MVPではモック認証**。Phase 5でEntra IDへ切り替え | 指示書自体がMVPでのモック許容を明記（17章） |
| Logging/Tracing | 構造化ログ、request_id、trace_id | Phase 1から最小限（request_idのみ）導入し、Phase 5で強化 | 段階的導入 |

## 2. 既存のサステナビリティ機能との切り分け方針（最重要）

`geopolitical_monitor_dashboard`は現状100%サステナビリティ／競合モニタリング用のコードである。CLAUDE.mdの「大量変更禁止」「既存コードの無断置換をしない」という原則、および本指示書の「既存コードと新規機能の境界を明確にする」という要求に基づき、次の方針を採る。

- **既存のサステナビリティ関連コード・DBテーブル・フロントエンドルートには一切手を入れない**（`article_*.py`, `competitor_*.py`, `sustainability_*.py`, `eco-digest-spark/src/routes/{index,category,article,search,chat,competitors.*}.tsx`, 既存`sql/`配下のテーブル群）。削除・統合はスコープ外とし、ユーザーが将来明示的に指示した場合のみ対応する。
- **地政学ドメインは完全に新規のモジュール群として追加する。**
  - Python: 新規ファイルは`geo_`プレフィックスで統一（例: `geo_event_extractor.py`, `geo_signal_engine.py`, `geo_scenario_engine.py`, `geo_expert_ai.py`, `geo_alert_engine.py`）。既存の`article_analyzer.py`（LLMタグ付け・重要度スコアリング）や`competitor_classifier.py`+`competitor_change_detector.py`（構造化抽出・差分検知）は、地政学版の実装時に**パターンを参考にするが、コードは共有・改変しない**（ドメインが異なりプロンプト/スキーマも別物のため、共有すると変更の影響範囲が両ドメインに及んでしまう）。
  - API: `api_server.py`に新規`APIRouter`を追加し`/api/geo/*`配下にマウントする（既存`/api/competitors/*`等とは完全に独立したパス空間）。既存エンドポイントの変更は行わない。
  - DB: 新規SQLファイルは既存`sql/`ディレクトリの命名規約（`YYYY-MM-DD_説明.sql`）を踏襲しつつ、ファイル名に`geo_`を含め既存マイグレーションと区別できるようにする（詳細は`docs/data-model.md`）。既存テーブルへのカラム追加は行わない。
  - Frontend: `eco-digest-spark/src/routes/`配下に`geo.*.tsx`のルート群を新設する（例: `geo.overview.tsx`, `geo.signals.tsx`, `geo.scenarios.tsx`, `geo.evidence.tsx`, `geo.alerts.tsx`, `geo.expert.tsx`, `geo.reports.tsx`）。既存の`STRINGS`（i18n）オブジェクトに`geo.*`キーを追記する形で拡張し、既存キーは変更しない。`src/lib/api.ts`にも既存の型・関数を変更せず、地政学用の型・queryOptions・hooksを追記する。
- この切り分けにより、地政学基盤の開発中も既存のサステナビリティダッシュボードは無停止・無影響で稼働し続ける。将来的に「このリポジトリをどちらか一方の用途に一本化する」判断が必要になった場合は、その時点で改めてユーザーに確認する。

## 3. アダプター設計（指示書4.2）とMVPでの実装マッピング

| インターフェース | MVPでの実装 | 将来の差し替え先 |
|---|---|---|
| `LLMProvider` | `ai_client.py`のAzure OpenAIラッパーを地政学用の薄いラッパー（`geo_llm_provider.py`）越しに呼び出す | 別LLMベンダーへの切り替え時に差し替え |
| `EmbeddingProvider` | Phase 4まで未実装（内部レポートRAGで必要になった時点で実装、pgvector前提） | pgvector以外のベクトルDBへの切り替え |
| `ObjectStorage` | ローカルファイルシステム保存（`uploads/geo/`相当のパス、既存プロジェクトの`uploads/`慣習を踏襲） | Azure Blob Storage |
| `DocumentParser` | Phase1はHTML/PDFの本文抽出のみ（既存`article_crawler.py`のHTML抽出ロジック・エンコーディング判定パターンを参考に新規実装）、PPTX/DOCXはPhase4で追加 | 商用パーサーサービスへの拡張 |
| `SourceConnector` | Phase1は「手動URL登録」「ファイルアップロード」のみ。台湾国防部等の公式サイトconnectorはPhase2でconnector単位に分離して縦切り追加 | 各公式サイトのHTML構造変更に追従するconnectorごとの差し替え |
| `NotificationProvider` | MVPでは画面内通知のみ実装（実メール/Teams送信はしない、interfaceのみ用意） | 承認後にSMTP（既存`weekly_email_report.py`の設定を参考）やTeams Webhookを実装 |
| `IdentityProvider` | ローカル開発用モック認証（固定のダミーユーザーではなく、`config.json`のロール設定から読み込む形にしコードに埋め込まない） | Microsoft Entra ID（Phase 5） |

## 4. 動作確認（local dev）

Phase 1着手時に、既存`.claude/skills/run-world-monitor-dashboard/`を本プロジェクト用に複製・パス修正して使用する（venv・Node runtime・ポート構成は既存と同一、`geopolitical_monitor_dashboard`向けにパスのみ修正）。
