# 地政学インテリジェンスアプリ — Azure Web App へのデプロイ手順

> 作成: 2026-10-15 ／ サステナアプリ（app-jp1-sjp-bn-susiq-002 / susiq-frontend-001）で動いている方式を参考にしている。
> 古い `DEPLOY_GUIDE.md` / `Dockerfile` は **別アプリ（サステナアプリ）用**。このアプリでは使わない。

## 0. 構成は2通り（どちらでも動く。ローカルで動作確認済み）

| | **A：1サーバー（おすすめ）** | **B：2サーバー（サステナアプリと同じ）** |
|---|---|---|
| Web App | バック1つ（Python）。**画面も同じ Web App が配信** | バック（Python）＋ フロント（Node）の2つ |
| 認証・ネットワークの設定 | 1か所 | 2か所 |
| CORS / Private Network | **不要**（同じサイト） | 必要（バックの `CORS_ALLOWED_ORIGINS`） |
| ワークフロー | `deploy-azure.yml`（既定） | `deploy-azure.yml`（with_frontend=**false**）＋ `deploy-azure-frontend.yml` |

## 1. サステナアプリから引き継いだ運用ルール
- **ログインは「発行プロファイル」**。Web App の「発行プロファイルのダウンロード」の XML を、GitHub の Secrets に登録（バック `azure_backend`、フロント `azure_frontend`）。
- **Azure から PyPI へ届かない**（社内プロキシ）。GitHub 側（ジョブ内）で `pip install` を済ませ、仮想環境 `antenv` ごとデプロイする。**Web App に `SCM_DO_BUILD_DURING_DEPLOYMENT` は設定しない**（Oryx ビルドを動かさない）。
- 起動コマンドは **`python docker_entrypoint.py`**（起動のたびに環境変数から `config.json` を作り直し、API を起動する）。
- バックは **Private Endpoint** で、社内ネットワーク／VPN 経由の利用が前提（B の場合、`allow_private_network` を有効化済み）。

## 2. Azure でやること

### 2-1. バックの Web App（A・B 共通）：app-jp1-sjp-bn-geo-002
| 設定場所 | 値 |
|---|---|
| 構成 → スタック設定 | **Python 3.11** |
| 構成 → 全般設定 → 起動コマンド | `python docker_entrypoint.py` |
| 構成 → 全般設定 | **Always On: オン**、HTTPS のみ: オン |
| 監視 → 正常性チェック | パス `/api/health` |

**環境変数（アプリ設定）**：`config.json` は成果物に含めない。秘密の値はここに入れる（機密は Key Vault 参照が望ましい）。

| 名前 | 必須 | 値・説明 |
|---|---|---|
| `SUPABASE_URL` | ✅ | Supabase のプロジェクト URL |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | Supabase の service_role キー（**秘密**）。ローカル `config.json` では `supabase.key` に入っている値。`SUPABASE_KEY` は別途不要 |
| `AZURE_OPENAI_API_KEY` | ✅ | Azure OpenAI のキー（**秘密**） |
| `AZURE_OPENAI_ENDPOINT` | ✅ | Azure OpenAI のエンドポイント |
| `AZURE_OPENAI_DEPLOYMENT` | ✅ | デプロイ名。ローカル `config.json` の `azure.default_deployment` と**同じ値**（空だと AI が失敗する） |
| `AZURE_OPENAI_API_VERSION` | ✅ | `2025-04-01-preview` |
| `PERPLEXITY_API_KEY` | 任意 | Web 検索を使う場合（**秘密**） |
| `GEO_UPLOAD_BASE` | ✅ | `/home/data/uploads`（アップロードしたファイルを、再起動・再デプロイで消さない） |
| `GEO_EXPERT_REVIEW_CACHE_DIR` | **設定しない** | 成果物に入っている生成済みキャッシュ（`expert_review_cache/`）を使うため。設定すると、空の場所を見に行き、初回表示のたびに LLM で再生成する |
| `GEO_GDELT_STATE_DIR` | ✅ | `/home/data/state` |
| `GEO_CONVERSATION_MODE_ENABLED` | 任意（既定OFF） | `true` で、インテリジェンスAIの会話モード（ギャルとおっさんと私／コンサルと私と相棒／ステークホルダー会議）を有効にする。会話のたびに Azure OpenAI を呼ぶ（費用がかかる）。会話のサーバー保存を使うには、`sql/2026-10-19_geo_conversation_mode_storage.sql` の適用が必要（未適用でもブラウザ内保存で動く）。詳細は `docs/conversation-mode-v1-release-notes.md` |
| `GEO_CONVERSATION_MODE_WEB_SEARCH_ENABLED` | 任意（既定OFF） | `true` で、会話モードの Web 検索（Perplexity。`PERPLEXITY_API_KEY` が必要）を有効にする。送るのは「おすすめテーマ」の題名だけ。上のフラグがOFFなら無効 |
| `GEO_IA_CROSS_SOURCE_ENABLED` | 任意（既定OFF） | `true` で、インテリジェンスAI(会話)の通常モードの自由質問を、使えるすべてのソース（レポート・ナレッジ・記事・モニタリング・GDELT・情報ソースDB、条件によりWeb・面談ログ）へ横断で投げて答える（`/api/geo/intelligence-ai/presets/ask`）。OFFのときは従来の簡易チャット（`/api/geo/chat`）。Webへ送るのは、画面の同意チェックがあるときの質問文のみ。 |
| `CORS_ALLOWED_ORIGINS` | B のみ | フロント Web App の URL（例 `https://<フロントのアプリ名>.azurewebsites.net`）。カンマ区切りで複数可 |
| `HTTPS_PROXY` / `SSL_VERIFY` | 条件付き | Azure から外部へ直接出られない環境のみ（情シスに確認） |

### 2-2. フロントの Web App（B のみ）
| 設定場所 | 値 |
|---|---|
| スタック | **Node 22** |
| 起動コマンド | `node server/index.mjs` |

### 2-3. 発行プロファイルの取得
Web App → 概要 → 「**発行プロファイルのダウンロード**」。ダウンロードしたファイルの**中身（XML 全体）を GitHub の Secrets に貼る**（次節）。
チャット・メール・コミットには載せない。ダウンロードできない場合は、権限（Contributor 相当）と基本認証（SCM）の許可を情シスに依頼する。

## 3. GitHub でやること

### 3-1. リポジトリ（1フォルダ運用：作業フォルダはこのまま。GitHubへは「必要なファイルのスナップショット」だけを送る）
- 作業は、これまでどおり**このフォルダだけ**で行う。別フォルダは作らない。
- GitHub（`yasugdxv/geo-intelligence`）へ送る内容は、コード・設定・画面のソース・SQL・テスト・デプロイ設定のみ（約860ファイル・15MB）。
  `uploads/`、`docs/` の巨大zip、ログ、評価結果、`config.json` は**含まれない**。過去の開発履歴も送られない。
- 手順:
  1. 内容の確認（コミットは作らない）: `python deploy/geo_azure/publish_clean_snapshot.py --dry-run`
  2. スナップショットを作る（ローカルのみ）: `python deploy/geo_azure/publish_clean_snapshot.py -m "日本語のメッセージ"`
  3. 送る（**送る前に内容を確認**。main へ直接ではなく作業用ブランチへ）:
     `python deploy/geo_azure/publish_clean_snapshot.py -m "..." --push https://github.com/yasugdxv/geo-intelligence.git --branch 作業用ブランチ名`
  4. GitHub でプルリクエスト → main に取り込み → Actions で Run workflow
- 作業フォルダの作業ツリー・ブランチ・未コミットの変更には触れない。前回送った内容から削除したファイルは、次回のスナップショットから自動で消える。
- 空のリポジトリには main が無くプルリクエストを作れないため、**初回だけ**は main へ直接送る（承認のうえ）。2回目以降は作業用ブランチ＋プルリクエスト。
- 検査: 秘密情報らしきファイル・文字列、50MB超のファイルが見つかると、スナップショットを作らずに止まる。

### 3-2. Secrets（Settings → Secrets and variables → Actions → Secrets）
| 名前 | 値 |
|---|---|
| `azure_backend` | バックの発行プロファイル（XML 全体） |
| `azure_frontend` | フロントの発行プロファイル（B のみ） |

### 3-3. Variables（同じ画面の Variables タブ。秘密ではない値）
| 名前 | 値 | 必要な場合 |
|---|---|---|
| `GEO_BACKEND_APP_NAME` | バックの Web App 名（未設定なら `app-jp1-sjp-bn-geo-002`） | 名前が違うとき |
| `GEO_FRONTEND_APP_NAME` | フロントの Web App 名 | B のみ |
| `GEO_BACKEND_URL` | バックの URL（`https://…azurewebsites.net`。画面のビルド時に埋め込まれる） | B のみ |

### 3-4. 実行（Actions タブ → Run workflow。手動実行のみ）
- **A**：「Deploy backend to Azure Web App」→ `with_frontend` は **true（既定）**
- **B**：①「Deploy backend…」を `with_frontend` = **false** で実行、②「Deploy frontend to Azure Web App」を実行

## 4. デプロイ後の確認
| 確認 | 期待 |
|---|---|
| バックの `/api/health` | `{"status":"ok", ... "db":"reachable"}` |
| バックの `/docs` | Swagger の画面 |
| 画面（A: バックの `/geo/intelligence-ai` ／ B: フロントの URL） | インテリジェンス AI の画面 |
| 画面で「変化の兆候を探す」等を実行 | 結果が表示される |

| 症状 | 見る場所・原因 |
|---|---|
| 起動しない／503 | 監視 → ログストリーム。`ModuleNotFoundError` は仮想環境（antenv）が成果物に入っていない可能性。起動コマンドが `python docker_entrypoint.py` か確認 |
| `db: unreachable` | `SUPABASE_URL` / キー、Supabase への外向き通信 |
| AI が「応答を処理できません」 | `AZURE_OPENAI_*`（特に `AZURE_OPENAI_DEPLOYMENT`） |
| B で画面は出るが API が失敗（ブラウザのコンソールに CORS エラー） | バックの `CORS_ALLOWED_ORIGINS` にフロントの URL（末尾の `/` なし）が入っているか。バックが Private Endpoint のみなら、VPN/社内ネットワークから開いているか |
| ワークフローのデプロイ手順が 401 など | 発行プロファイルの貼り付け漏れ・期限切れ。基本認証（SCM）が許可されているか |
| デプロイ後に画面が白い（A） | `/assets/...` が 404 でないか。`with_frontend` が true で実行されたか |

## 5. 注意・未検証
- **SQL は Supabase に手動適用が必要**（DB は今のまま）。未適用: `sql/2026-10-14_geo_ia_extensions.sql`（専門領域コメント／記事本文キャッシュ／会話のサーバー保存）。適用前もアプリは動く。
- `config.json` は、起動のたびに環境変数から作り直される（`/home` に書き出される。サステナアプリと同じ）。環境変数を変えたら、再起動で反映される。
- **未検証**: ① Python 3.11 の Web App での `antenv` の読み込み（サステナと同じ方式。初回のログで確認）。② Azure 上からの外向き通信（Supabase / Azure OpenAI / Perplexity）。③ Private Endpoint 経由の実利用。
- 定期バッチ（記事収集・スケジューラ）はこの Web App では動かしていない。
- ローカル開発は従来どおり（`python api_server.py` と `npm run dev`）。
- 1サーバー構成の成果物のローカル確認: `GEO_STATIC_BUILD=1 npm run build`（`eco-digest-spark/`）→ `python deploy/geo_azure/build_package.py`。

### 5-1. 実際に起きた失敗: デプロイが「Package deployment using ZIP Deploy failed」で止まる（2026-10-22）
- **症状**: GitHub Actions の「Deploy to Azure Web App」が失敗。Azure（デプロイセンター／Kudu）のログの先頭が `oryx build /tmp/zipdeploy/extracted ... --platform python` で、続いて `Could not fetch URL https://pypi.org/simple/...` `SSLError` `No matching distribution found for fastapi==...`。
- **原因**: Web App で **Azure 側のビルド（Oryx）が有効**になっている。`requirements.txt` を見つけて、Azure 側で `pip install` を走らせ、社内ネットワークの制約で PyPI に届かず失敗する。このワークフローは、GitHub 側で `pip install` を済ませて `antenv` ごと載せる方式なので、Azure 側のビルドは不要（かつ失敗の原因）。
- **対処**: Azure ポータル → 構成 → アプリケーション設定で、`SCM_DO_BUILD_DURING_DEPLOYMENT` と `ENABLE_ORYX_BUILD` を **削除、または `false`** にして保存（アプリが再起動する）→ ワークフローを再実行。ログの先頭に `oryx build` が出なくなれば、Azure 側のビルドは無効になっている。
- **予防**: 新しい Python の Web App は、作成時に `SCM_DO_BUILD_DURING_DEPLOYMENT=true` が自動で入ることがある。新規作成・作り直しのたびに確認する。
