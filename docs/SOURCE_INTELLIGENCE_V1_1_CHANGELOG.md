# SOURCE_INTELLIGENCE_V1_1 変更記録

**位置づけ**: `SOURCE_INTELLIGENCE_V1`（Freeze済み、`freeze/SOURCE_INTELLIGENCE_V1/`）に対する、評価期間中の**ユーザー要望による次版の変更**。
V1のsnapshotとManifestは**変更していない**（V1の基準としてそのまま保持）。V1_1のFreezeはまだ行っていない。
経緯は `docs/SOURCE_INTELLIGENCE_V1_FEEDBACK.md` の FB-001。

> 注意: 以下のファイルは現行（作業ツリー）がV1 snapshotと**意図的に異なる**。
> `python freeze/SOURCE_INTELLIGENCE_V1/verification/freeze_check.py` は、これらを「Freeze後に変更あり」と報告するのが正しい。

## 変更1: テーマ探索の入力を「調査プロンプト」型に（FB-001）

| 項目 | V1 | V1_1 |
|---|---|---|
| 入力 | 1行の検索ボックス | **大きなテキストエリア**（4行・リサイズ可）。見出し「どんなテーマを探しますか？」＋説明文 |
| ボタン | 「検索」 | **「テーマ探索開始」**（Ctrl/Cmd＋Enterでも開始。Enterは改行） |
| プリセット | なし | ~~7種のプリセット~~ → **変更2で削除**（不要との判断） |
| クリア | なし | 「入力と条件をクリア」 |
| 文章の解釈 | LLMが語・地域・新規性・期間を抽出（最大4語） | LLMの抽出を**最大6語**に。LLM不可時のフォールバックを、日本語の長文を助詞・動詞・依頼表現で区切って語を取る方式に改善（従来は空白区切りのため日本語の長文は1語になり何にも一致しなかった） |

プリセット: 中国×半導体・先端技術／台湾海峡の動き／ホルムズ・中東×供給網／米国の輸出管理・制裁・関税／サウジ・イスラエルなど中東外交／
ロシア・ウクライナ×エネルギー／新しく出てきた経済安保テーマ。文面は `geo.themeExplorer.preset.N.label / .text`（ja/en）で管理。

### 変更ファイル（V1 snapshotとのSHA256差分）

| ファイル | 内容 |
|---|---|
| `eco-digest-spark/src/components/geo/theme-explorer-panel.tsx` | 入力UI（Textarea・プリセット・ボタン・クリア） |
| `eco-digest-spark/src/lib/i18n-extra/theme-explorer.ts` | 文言（プロンプト見出し・説明・プリセット7種・ボタン名。ja/en） |
| `geo_theme_explorer_service.py` | 長文プロンプトの語抽出（フォールバック）、LLM抽出語数 4→6 |
| `tests/test_geo_source_library.py` | 長文プロンプトの語抽出と絞り込みのテスト2件を追加 |
| `docs/SOURCE_INTELLIGENCE_V1_FEEDBACK.md` | FB-001を記録（生きている文書のため差分は想定内） |

DB・API・migrationの変更なし。V1のAPI契約（`GET /api/geo/theme-explorer?q=`）は不変（`q`が長文になっただけ）。

## 確認結果（2026-10-05）
- ブラウザ: 入力欄・「テーマ探索開始」・プリセット7種が表示。プリセットのクリックで文章が入りフォーカス、追記して開始すると解釈結果（語・新規のみ）が表示される。エラーなし。
- テスト: 「Python full / 型チェック / ビルド」の再実行結果は本ファイル末尾の「回帰結果」に追記する。

## 回帰結果（V1_1変更後）
| 項目 | 結果 |
|---|---|
| Python full test | **2526 PASS**（V1の2524＋新規2件） |
| Frontend Type Check | PASS（エラー0） |
| Frontend Build | PASS |
| 画面確認（ブラウザ） | 入力欄・プリセット・「テーマ探索開始」・解釈結果の表示を確認。ページエラーなし |


## 変更2: 反応の見える化・フィルターの整理・並び替え・削除（FB-002）

| 項目 | 内容 |
|---|---|
| **進行表示** | 「テーマ探索開始」を押すとボタンが「探索中…」になり、「AIが入力文を読み取り、テーマを探しています…（数秒かかります）」を表示。同じ入力で押しても必ず再実行する |
| **結果の説明** | 件数表示（絞り込み時は「N件（全M件中）」）。**候補が0件の時は理由を説明**（「テーマ候補はまだ1件もありません。候補は承認された状況データから作られ、いまは元データがないため結果は出ません」）。絞り込みで0件の時は「候補M件中0件」 |
| **プリセット** | 削除 |
| **フィルター** | 地域・テーマカテゴリ・注目度・期間・新規のみ・分類タブ（注目/新規/継続/過去）を**画面から廃止**。残すのは次の3つ |
| キーワード検索 | 候補の文面への単純な絞り込み（AIを使わない。空白区切りは全語を含む=AND）。入力から0.35秒後に反映 |
| 並び替え | **新しい順（既定）／古い順**（最新Signal日時） |
| 削除 | 各カードに「削除」ボタン（確認ダイアログつき）。**論理削除**（`DISMISSED`）。一覧から外れるだけでデータは保持、モニタリング・調査には影響しない |
| 不具合修正 | AIが文章から取り出した地域（例「台湾海峡」）を**完全一致**で適用していたため、地域タグ（「台湾」）の候補を取りこぼしていた → **緩やかな一致**（部分一致または文面に地域名）に変更 |

API（V1の契約は維持し、追加のみ）: `keyword` / `sort`（newest・oldest・attention。既定newest）/ `include_deleted`（既定false。削除済みを除外）、
応答に `available_total`（削除済みを除く候補の総数）と `sort` を追加。V1のパラメータ（region・category・level・bucket・new_only・period_days）は互換のため残置（画面は使わない）。

### 追加の変更ファイル
`eco-digest-spark/src/components/geo/theme-explorer-card.tsx`（削除ボタン）、`eco-digest-spark/src/lib/geoSourceLibraryApi.ts`（型）、
`geo_monitoring_theme_api.py`（パラメータ追加）、`geo_theme_explorer_service.py`、`theme-explorer-panel.tsx`、`i18n-extra/theme-explorer.ts`、`tests/test_geo_source_library.py`。

### 確認結果（実DB・ブラウザ）
進行表示、「探索中…」、同入力の再実行、新しい順／古い順、キーワード絞り込み（0件時の説明つき）、削除ダイアログのキャンセル、削除（一覧から消え、`DISMISSED`として保持）を操作して確認。
確認用に作った[TEST]候補5件は全てDISMISSED（有効0件）。

### 既知の事項
- **本番のテーマ候補は0件**。承認済みのSituation Linkが0件で、候補生成（sync）の材料が無いため。この画面の改善だけでは候補は増えない。
  「探索開始」で記事から候補をその場で提案する機能は、次版の検討事項（FEEDBACK FB-002）。
- カードの「注目度: 高/中/低」バッジと「根拠を見る」は残している（フィルターではなく表示。分かりにくければ次版で整理）。

---

## 変更3: Article Discovery（「テーマ探索開始」で直近の記事からテーマ候補をその場で提案）

2026-10-05 / FEEDBACK FB-003 への対応。V1 snapshot（`freeze/SOURCE_INTELLIGENCE_V1/`）・READING_UI_V1・Expert AI推論コアは変更していない。DBマイグレーションなし。

### 何ができるようになったか
| 項目 | 内容 |
|---|---|
| 入口 | 「テーマ探索開始」で、入力文（自然言語）から直近の記事を検索し、AIが「何が変わっているか」という単位でテーマ候補を提案する |
| 期間 | 既定は直近30日（入力文で「直近1週間」等と書けば変更可。上限180日） |
| 探索対象 | Source Selectorで選んだSourceだけに限定。未選択なら全登録Source＋既存記事すべて |
| 提案の扱い | 生成された候補は `NEW`（提案・未確定）。自動でMonitoring/Researchへ進まない。画面に「提案（未確定）」バッジ |
| 根拠 | 各候補に根拠記事（タイトル・Source・日付・元URL）を必ず表示 |
| 重複 | 既存の候補／Monitoring／Themeと重なる場合は新規作成せず「既存テーマに新しい動きがあります」として表示 |
| 材料不足 | 「該当する新しいテーマ候補は見つかりませんでした」を表示し、無理に候補を作らない |
| 進行表示 | 「入力を解釈 → 関連記事を検索 → テーマ候補を整理」の3段階を実際の処理状態として表示（見せかけの進捗ではない） |
| 由来の区別 | カードに「記事から発見」「Situationから発見」等のバッジ。既存フィールド `generation_model` の接頭辞（`article_discovery/` 等）から判定するため、スキーマ変更なし |
| 注目度バッジ | 画面から非表示（バックエンドの算出は残置） |

### API（追加のみ）
- `POST /api/geo/theme-explorer/discover` — 探索ジョブ開始（要認証、空prompt=400）。`job_id` を返す
- `GET /api/geo/theme-explorer/discover/{job_id}` — 進行状況と結果のポーリング
- 既存 `GET /api/geo/theme-explorer` の各テーマに `candidate_origin` / `candidate_origin_label` / `proposal_state` / `evidence_articles` / `evidence_total` を追加

### 変更・追加ファイル
- 新規: `geo_theme_discovery_service.py`、`tests/test_geo_theme_discovery.py`、`eco-digest-spark/src/components/geo/theme-discovery-results.tsx`
- 変更: `geo_theme_explorer_service.py`、`geo_monitoring_theme_api.py`、`geo_source_recommendation_service.py`（根拠記事のdomainが一意に登録Sourceへ解決できる場合のみ推薦に反映）、
  `theme-explorer-panel.tsx`、`theme-explorer-card.tsx`、`source-selector.tsx`、`geoSourceLibraryApi.ts`、`i18n-extra/theme-explorer.ts`、`tests/test_geo_source_library.py`

### 性能・コスト設計
検索はDBで絞ってから（`published_at` の下限＋キーワードOR）、関連度上位40件・同一domain最大6件だけをLLMに渡す。同じ入力は10分キャッシュ（再表示は約0.1秒）。

### 実DB・ブラウザでの確認結果
- A 「台湾海峡で最近注目すべきテーマ」: 根拠記事つきの候補を提案（進行表示は実処理に連動、約13〜30秒）
- B Source限定: 選択した6Sourceだけの記事が使われ、選択外は混ざらない。関連記事が少なすぎる設定（3Source）では候補を作らず「見つかりませんでした」
- C 根拠記事は全件タイトル・Source・日付・元URLつき
- D 同じ入力はキャッシュ、言い換えは「既存テーマの更新」として表示され、候補は増えない
- E 関連記事が無いQuery（アルゼンチンの農業補助金）は候補を作らない
- F Situation由来の既存候補も従来どおり表示
- G Monitoring追加・詳しく調べる（Research Studioに使用Sourceが引き継がれる）・Expert AI相談（確認ダイアログ）の導線を確認

### 既知の制約
- 現在DBにある関連記事は少なく（GDELT取得の絞り込みのため）、Queryによっては材料不足になる
- AIが1件の出来事を「常態化」のように読み過ぎる場合がある。提案は未確定で、根拠記事を見て判断する前提
- 継続取り込みの記事には `source_id` が付かないため、Source推薦は「domainが一意に登録Sourceへ解決できる場合」のみ
- 探索にはAI（Azure OpenAI）と認証が必須。AIが使えない時はエラーを表示する（「結果なし」と誤解させない）
- 探索で提案された候補はテスト用の[TEST]名ではなく実データとして残る（削除すると同じ候補を再提案できなくなるため、そのまま残置し、画面から「削除」できる）

### 補足（Freeze時点の追記）
- 変更ファイルには上記のほか `eco-digest-spark/src/routes/geo.theme-discovery.tsx`（画面文言の更新）も含まれる。
- Freeze前にArticle Discovery由来の候補11件を確認し、**4件を却下（DISMISSED）、7件を「提案（未確定）」のまま残置**した。
  却下理由と一覧: `freeze/SOURCE_INTELLIGENCE_V1_1/verification/article_discovery_candidates_at_freeze.md`
- Freeze後（評価期間中）はArticle Discoveryも変更しない。改善点は `SOURCE_INTELLIGENCE_V1_FEEDBACK.md` に記録し、次の改修はV1_2として扱う。
