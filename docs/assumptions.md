# 未確定事項と仮定（Phase 0時点）

指示書 第23.4章「未確定事項は`docs/assumptions.md`へ記録する」に基づく。要件を黙って削らず、判断が必要な点をここに残す。

## スコープ・共存に関する仮定

- **既存のサステナビリティ／競合モニタリング機能は本MVPのスコープ外とし、変更・削除しない。** 同一リポジトリ内で地政学ドメインと共存させる（`docs/architecture.md`の切り分け方針を参照）。将来「リポジトリを地政学専用に一本化する」「両ダッシュボードを1つのアプリに統合する」といった判断が必要になった場合は、その時点で改めて確認する。
- `.claude/skills/run-world-monitor-dashboard/`は`world_monitor_dashboard`向けパスのまま未更新。Phase 1着手時に本プロジェクト用へ複製し、パス（venv、起動対象ディレクトリ等）を修正する前提とする。

## 指標データに関する既知の齟齬（指示書7.1で明記）

- Excel「地政学インテリジェンス基盤_統合設計案_v2.xlsx」のシート名は「現行23項目の再設計」だが、ID付き実データ行は22件しかない。この差分（23 vs 22）は黙って補完せず、そのままこのファイルに記録する。Phase 1でシートを再確認し、欠落が「シート内の見出し行の数え間違い」なのか「本当に1項目が未定義」なのかを切り分ける。
- 初期ダッシュボードに表示する`mvp_enabled=true`の12指標は指示書7.2記載の暫定選定をそのまま採用する。設定で変更可能な構造にするため、コード変更なしに指標の有効/無効を切り替えられるようDB/config駆動にする。
- 閾値は「案」であり校正前であることをUIに明示する（指示書7.1）。

## 技術・インフラに関する仮定

- **pgvectorの利用可否は未確認。** SupabaseプロジェクトでpgvectorがEnableされているかは、Phase 4（内部レポートRAG）着手時に確認する。有効化できない場合は暫定的にキーワード検索（既存`article_filter.py`のキーワードマッチングパターンを参考）でフォールバックし、その旨をUIに明示する。
- **Azure Blob Storageの契約・認証情報は未確認。** MVP期間中はローカルファイルシステム保存で運用し、`ObjectStorage`インターフェース越しに後日差し替える。
- **Microsoft Entra IDのテナント設定・アプリ登録は未着手。** Phase 5まではモック認証（`config.json`のロール設定を読み込む形）で運用する。本番相当のユーザー・ロールをコードに固定しない。
- バックグラウンドジョブ基盤（Azure Service Bus等）は、既存プロジェクト全体が同期実行/バッチスクリプト運用であることに合わせ、MVPでは導入しない。データ取得量・処理時間が増え同期実行では支障が出る段階で、改めて導入要否を判断する。
- 公式ソースconnector（台湾国防部・台湾海巡署・台湾大陸委員会・台湾総統府・TSMC IR・外務省海外安全）は、各サイトのrobots.txt・利用規約を個別に確認したうえでPhase2にてconnector単位で縦切り実装する。Phase0時点では未確認。

## 台湾テーマseedに関する仮定

- 「責任あるマーケティング」相当のテーマ外項目や、Excelに記載のない指標・シナリオ条件については、Phase1のseed実装時に不明点があれば都度このファイルへ追記する（現時点ではExcel内容を未読み込みのため、具体的な欠落項目はPhase1着手時に精査する）。
- MIL-01（PLA航空活動・中間線越境）を最初の縦切り対象指標とする（指示書9.1）。外部取得が困難な場合は`is_mock=true`のサンプル文書を使用し、実データのように表示しない。

## Phase 0の確認事項

- 本ドキュメント一式（`implementation-plan.md`/`architecture.md`/`assumptions.md`/`data-model.md`）とチャット提示の内容をユーザーが確認し、Phase 1着手を承認した時点でこの節を「確認済み」に更新する。

## Phase 1実装中に判明した既知の問題（geo実装とは無関係の既存ギャップ）

- `knowledge/sustainability_expert/suntory_sustainability_expert_base.json`が本プロジェクトに存在しないため、`api_server.py`をimportした時点で`sustainability_expert_common.load_expert_base()`が`FileNotFoundError`になり、**geoコードの有無に関わらずAPIサーバー自体が起動できない**。Phase0調査で判明していた「`world_monitor_dashboard`からのコピー時に`docs/`・`knowledge/`・`shared/`が欠落している」という既知の齟齬がそのまま実害として顕在化したもの。既存のサステナビリティ機能側の欠落であり、geo機能の実装（Pythonモジュール単体・pytest）には影響しない（`tests/`は`FakeSupabaseClient`経由で個々のモジュールを直接テストしており、`api_server`をimportしないため全テストは正常実行できる）。API疎通確認・ブラウザでの動作確認を行うには、`knowledge/`（および必要であれば`docs/`・`shared/`）を`world_monitor_dashboard`から複製するなどの対応が必要（ユーザー確認のうえ対応済み。`knowledge/`のみ複製し起動可能な状態にした）。
- ポート8000は`world_monitor_dashboard`側のAPIサーバーが既に使用しているため、本プロジェクトのAPIサーバーは`API_PORT=8001`で起動し、`eco-digest-spark/src/lib/api.ts`の`API_BASE`もこのプロジェクトの複製内でのみ`8001`に変更済み（`world_monitor_dashboard`側には影響しない）。

## UI地政学化・地政学専門家AI（MVP軽量版）に関する仕様決定

- ユーザー指示により、サイドバーの旧サスティナビリティ系メニュー（ダッシュボード/検索/カテゴリ/競合モニタリング/ブリーフィング）は**非表示化のみ**行い、ルート・コンポーネント自体は削除していない（URLを直接開けば引き続き動作する）。将来的に完全撤去するかはユーザー判断による。
- サイドバーの「イベント」は監視テーマ（`geo_themes`）の一覧・選択画面であり、文書から抽出される「Event Candidate」（`geo_events`）とは別概念。後者はテーマダッシュボード内の「検知イベント」タブに再配置し、名称衝突を避けた。
- `/`（旧サスティナビリティホーム）は`/geo/situations`へリダイレクトするようにした。旧ホームの描画コードは削除していない。
- **地政学専門家AI（`/api/geo/chat`、`GeoAiChatPanel`）は現時点でMVP軽量版**: 直近の検証済みEvent・Evidence・MIL-01の最新Observationをコンテキストとして渡し、通常のchat completionで応答するのみ。指示書14章が定義する7モードRouter・15段階Reasoning Pipeline・Source Pack・Quality Guard・Prompt/Knowledge/Model/Source Pack版の保存は未実装であり、これらはPhase3で本格実装する。既存のサスティナAI（`/api/chat`、`ai-chat.tsx`、`chat.tsx`）は無改変のまま維持し、地政学専門家AIとは完全に独立したコード（`geo-ai-chat.tsx`、`geoChatHistory.ts`）として実装した。

## Phase 2（指標・connector）＋ 地政学専門家AI本格版に関する仕様決定

- **（2026-08-03更新）SCM-01・MKT-01は追加実装済み**。両方とも無料・登録不要の公開APIのみで実現できることが判明したため、当初「商用データ」とまとめて見送っていた判断を訂正した。
  - SCM-01: TSMC Investor Relationsの月次売上ページ（公開HTML表）を`lxml`で決定的にパース。直近実績月のYoY(%)を操業状況の簡易プロキシとする（LLM不使用）。実クロールで`raw_value=35.6`（実際のページの値と一致）を確認済み
  - MKT-01: 台湾証券取引所(TWSE) OpenAPI（`openapi.twse.com.tw`、無料・登録不要）でTAIEX当日騰落率を、exchangerate-api.comの無料エンドポイント（`open.er-api.com`、無料・キー不要）でUSDTWDレートを取得。TAIEX騰落率の絶対値をボラティリティの簡易プロキシとする（LLM不使用、geo_source_registryを経由しない「生データ直結」方式、`geo_official_crawler.DIRECT_METHODS`参照）。実クロールで`raw_value=7.98`を確認済み
  - **LOG-01（商業通航のAIS）・FIN-01（海上保険料率）は引き続き未実装**。AISは無料枠のある業者（MarineTraffic等）に新規登録すれば限定的に可能だが、真のリアルタイム性は有料契約が前提になりやすい。海上保険の戦争リスク料率はLloyd's等の業者データが実質的な情報源であり、無料の公開データが存在しない。両方とも、ユーザーがデータ提供元との契約・APIキー取得を判断した場合にPhase2bとして対応する（代替として、ニュース記事からの言及ベースの粗い代理指標をEvent抽出方式で作ることも可能だが、精度は大きく落ちる）
- **CYB-01（重要インフラへのサイバー攻撃）の情報源は、指示書に名指しされていなかったためWeb検索で調査し、國家資通安全研究院(NICS)の資安週報を採用した**。他の5ソース（台湾国防部・海巡署・大陸委員会・総統府・外務省海外安全ホームページ）は指示書8.1章の優先順位どおり
- 公式ソースの登録（`geo_source_registry`）は、`geo_official_crawler.py`の`SOURCES`定義を唯一の正本とし、`geo_theme_seed.py`側には重複登録しない設計にした（2箇所に同じURLリストを持つと将来ドリフトするため）。`ensure_source()`が初回クロール時に自動登録する
- **地政学専門家AIの15段階Reasoning Pipelineは、逐次15回のLLM呼び出しにはしていない**（レイテンシ・コスト・実装複雑度が過大なため）。Source Pack作成（コード、決定的）→1回の構造化LLM呼び出し（15段階が要求する内容を1つのJSON構造として出力）→Quality Guard（コード、決定的、9項目）→保存、という設計にした（`geo_expert_engine.py`）。7モード共通で1つのレスポンススキーマ（`schemas/geo_expert_ai_response.schema.json`）を使い、モードごとに重視するセクションをプロンプト側の指示で切り替える（`geo_expert_schema.MODE_GUIDANCE`）
- **Knowledgeの`01_SYSTEM_POLICY.md`のみ本セッションで新規作成**。`03〜10_KNOWLEDGE_*.md`はユーザーのDownloadsフォルダに実在した「現行の専門家GPT資産」（2026年7月31日の専門家インタビューを基にした思考パターン集）をそのまま複製した（`knowledge/geo_expert/`）
- Quality Guardの「主体の裏取り」チェック（`stakeholders_grounded_in_source_pack`）はSource Pack本文への簡易的な部分一致であり、表記ゆれ（英語/日本語表記の違い等）で誤検知しうる。Guard自体は回答を承認しないため実害は無いが、精度を上げる場合はPhase2b以降で改善する
- **実クロールで検証済み（2026-08-03）**: 台湾国防部・海巡署・NICS・総統府・外務省海外安全ホームページの5ソースは到達・保存・指標計算まで成功（海巡署のMAR-01は実データでraw_value=8件を確認）。**台湾大陸委員会(mac.gov.tw)のみ、実ブラウザ相当のUser-Agent/Accept/Accept-Languageヘッダーを使っても403 Forbiddenで到達できない**（WAFによるIPベースのブロックの可能性が高い）。ヘッダー偽装をさらに強めて回避を試みることはしていない（サイト側の意図的なアクセス制限を強引に回避する方向のため）。POL-01・ECO-01はこのソースが復旧するか、代替の公式ソースが見つかるまでinsufficient_dataのままとなる。回避策として、必要であればユーザーが手動でページを保存し、既存の「文書アップロード」機能（`geo.theme.$themeId`の文書タブ）経由で取り込むことができる
- LLMの構造化出力が本文JSON後に余分な文章を続けて出力し`json.decoder.JSONDecodeError: Extra data`で失敗するケースを実クロールで確認・修正済み（`geo_llm_common._extract_json_object`を中括弧の対応を数える方式に変更）。また、1ソース・1指標のLLM呼び出し失敗が他のソース・指標の処理まで止めないよう、`geo_official_crawler.crawl_source`にtry/exceptを追加した
