# 定点レポート共通Preprocess 潜在不具合監査（2026-10-03）

対象: `geo_report_current_pipeline_service.py`（Preprocess本体）と、その出力を束ねる Prompt 1 / Prompt 2 パッケージ生成。
方針: 疑わしい箇所でも、証拠なしに仕様変更しない。コードを変えたのは「実データで再現できた確定バグ」と、
追加のみで既存挙動を変えない予防的ハードニングに限る。

## 0. 検証の土台

| fixture | 内容 | 総レコード | 備考 |
|---|---|---|---|
| US | US Midterm / Public Voice Monitor（17 Signal）, run `cadc7b32`, 2026-10-02 | 209,976 | raw 17ファイル(約1.3GB)あり |
| Evidence | Evidence Intelligence（6 Signal）, run `a1cb0baf`, 2026-10-02 | 391,909 | raw 13ファイル(約3GB)あり |
| 過去run | Evidence, run `032aa4be`, 2026-09-16 | 58,396 | 出力ZIPのみ（rawなし）。構造的かどうかの比較用 |

- **再現性の確認**: rawと同じmaster・同じ入力順で `run_preprocess` を再実行し、US・Evidence ともにfixtureと
  総数・非重複数・AC数・SC数が完全一致した（US 209,976 / 174,524 / 31,227 / 36,514、Evidence 391,909 / 253,092 / 9,739 / 13,007）。
  以降の「before」はこの再実行値。
- **回帰の判定基準**: 数値が変わっても回帰とは見なさず、(1) マッチング結果（Narrative・match_status・matched_terms）が
  全レコードで不変か、(2) 変化したレコードがすべて旧バグの除去で説明できるか、で判定した（§G, §H）。
- 実DB(Supabase)には接続していない。DB取得まわりの修正は、サーバ上限を再現したFakeと、修正前コードでの再現で検証した。

---

## A. Confirmed bugs（実データで再現、修正済み）

### A1. Prompt 2 base の record_cluster_map が 1,000 行で打ち切られる（重大）
- **current behavior**: `_fetch_all_records` が `limit=5000` で取得し「返りが5000未満なら完了」と判定。PostgREST の
  `db-max-rows`（Supabase既定1000件）で1回に返るのは1000件に頭打ちになるため、1ページ目で終了していた。
- **intended contract**: record map は Preprocess の全レコード（重複行含む）を含む。README_prompt2.md が「record-level weekly sentiment totals」の結合に使う。
- **reproduced evidence**: US の `record_cluster_map_001.csv` は 1,000 行のみ（総 209,976 件）、Evidence も 1,000 行（総 391,909 件）。
  `population_narrative_by_signal` / `_date` / `weekly_trend_raw_basis` / `timeline_basis` / `source_bucket_summary` の合計は
  US 818、Evidence 628（正しくは 174,524 / 253,092）。9/16 の過去 run は 58,396 行・2 chunk で正常（page_size 引き上げ前）。
  修正前コードをサーバ上限1000の Fake に当てると 209,976 件中ちょうど 1,000 件で止まる（fixtureと一致）。
- **impact**: 2026-10-02 の US / Evidence の Prompt 2 base ZIP は、record-level の集計が全体の 0.5% / 0.26% 分しかない。ZIP生成は成功扱い。
- **fix**: 空ページが返るまで取得を続ける。さらに ZIP 生成時に自動検証（A. 項目 #11）を行い、失敗なら例外にする。
- **regression risk**: 低。取得往復が増える（US 約210回、Evidence 約392回、1000行/回）。性能対策は §J。

### A2. `official` バケットが URL 全体への `gov` 部分一致で誤分類される
- **current behavior**: `get_source_bucket` が URL（パス含む）+ source_name に対し `gov|maff|meti|...` を部分一致。
- **evidence**: US の非重複 `official` 4,128 件のうち、政府ドメインは 255 件（6.2%）。残り 3,873 件は
  `/governor-race-...` を含むニュース 3,050 件、Reddit 306 件、`twitter.com/GovKathyHochul` 135 件、`meti` を含む語 59 件など。
  Evidence も同様（`official` 2,044 → 実質 858）。
- **impact**: Media が Official に、SNS 投稿が Official（= Public Voice 判定の対象外）に入っていた。
- **fix**: official 判定を URL のホスト名（`.go.jp` / `.lg.jp` / `gov` ラベル）に限定。
- **regression risk**: 中。バケットは cluster key に含まれるため cluster 構成が変わる（§G, §H）。政府系ドメインの新規取りこぼしは増やしていない（判定を狭めたのみ）。

### A3. DocumentType `WordPress` が `pr_release`（Corporate_PR）になる
- **evidence**: `press|release` を document type に部分一致するため `WordPress` が全件 pr_release。US の WordPress 46 件（非重複14件）が全て該当。
- **fix**: `press` を単語として判定、`release` は部分一致。`PressRelease` / `Press Release` は従来どおり pr_release。

### A4. Analysis Cluster の source_class / reaction / attitude が「先頭 record の値」
- **current behavior**: Analysis Cluster の base key に source_class・reaction・attitude が含まれないのに、`ClusterStats` 作成時の最初の record の値がそのまま cluster ラベルになる。Sentiment Cluster は key に含むため常に一様。
- **evidence**: US で Analysis Cluster のラベルと不一致の record は source_class 11,407 件（6.5%）、attitude 9,353 件（5.4%）、reaction 4,391 件（2.5%）。Evidence は 15,330 件（6.1%）/ 27,150 件（10.7%）/ 4,864 件（1.9%）。Sentiment Cluster は全て 0。過去 run でも同じ（91 cluster が混在）。
  これが「同じ corpus なのに Analysis と Sentiment で Public_Voice / Fact_Only の件数が移動する」原因（US: PV 39,272 vs 37,269）。
- **fix**: cluster の確定時に、分布の最頻値でラベルを決め直す（同数は件数降順→キー昇順）。あわせて混在度 `source_class_purity` を列で追加。
  record 単位の真値は従来どおり `source_distribution` / `reaction_distribution` / `attitude_distribution`（ラベルは近似にすぎない）。
- **注意**: 最頻値にしても、cluster ラベル×件数の分布は record 単位の真値とは一致しない（§G）。source class の集計は必ず `source_distribution` か record map で行うこと。

### A5. recursive split の付け替えが record_id だけをキーにしており、別 Signal の行を巻き込む（潜在）
- **evidence**: dedup が Signal 単位のため、同じ documentId が複数 Signal に非重複として存在する（US で 10,379 件、余分な行 12,676）。
  合成データ（同一 documentId を S1・S2 に置き、S1 の cluster だけを分割）で、修正前コードは S2 の行が S1 の子 cluster を指した。実データでは分割自体が起きていないため未顕在。
- **fix**: キーを (分割前 cluster_id, record_id) に変更。

### A6. 同じ Run で Preprocess をやり直すと、旧実行の行が `geo_report_records` に残って record map に混ざる（検出のみ、削除は未実施）
- **current behavior**: `geo_report_records` は `execution_id` 列も `(run_id, record_id)` の一意制約も持たず、行は `run_id` だけで区別される。Preprocess の保存処理は旧行を消さない（手動の `_cleanup_stuck_run.py` のみ）。`_fetch_all_records` は `run_id` だけで取得する。
- **reproduced evidence**: Evidence run `a1cb0baf` の record map（1,000 行の標本）のうち **97 行（9.7%）が、この Run の入力ファイルではない旧ファイル（`...20260914-03xxxx.csv`、6 ファイル）由来**。cluster master に存在しない AC ID が 16 種類、再実行結果と cluster ID / 重複フラグが食い違う行が多数。US と過去 run では 0 件。
- **impact**: A1 を直して全件取得にすると、旧実行の行まで含めて取得するため、Evidence の Run は record map の総行数が manifest と合わず、検証で ZIP 生成が止まる（黙って旧データが混ざるより安全）。
- **fix（実施）**: バリデータに「manifest の `input_files` に無い `source_file` の行」を検出する error（`record_map_stale_source_file`）を追加。
- **未実施（要判断）**: 旧実行の行の削除（データ削除を伴う）、または `execution_id` 列の追加と Canonical Preprocess Execution での絞り込み（スキーマ変更）。当面は「Preprocess をやり直す場合は新しい Run を使う」運用が安全。cluster 系テーブルも `run_id` のみで区別されるため、同様の混入がないか確認が必要。

---

## B. Likely bugs requiring product decision（変更していない）

| # | 内容 | 根拠 | 推奨 |
|---|---|---|---|
| B1 | **matcher が単語境界なしの部分一致**。短い英字キーワードが別の語に当たる | US の `ICE`（3文字）: S1 の 31.8%（3,410件）にヒットし、その 98% は `office` / `justice` / `service` / `police` / `Sharice` 等でアクロニムではない（S13 でも 91%）。`S*-08` が 17 Signal 中 14 でトップ Narrative。`govern`（`S*-10`）は required の `Governor` の部分文字列で、Governor と書くだけで発火 | 反事実測定: 単語境界で照合すると US S1 で 49.6%、S13 で 38.4% の record の Narrative 割当が変わり、fallback が 15%→37% / 6%→39% に増える（Evidence S3 は 4.3%）。**単純な matcher 修正では済まず、master の書き直し（`ICE` を大文字限定にする、語形を明示する等）とセットで判断が必要**。lint で警告済み |
| B2 | **pure RT が `Fact_Only_Candidate`**（rawに本文はあるが own text なし） | US の空欄代表（ユニーク 12,073 件）の 98.8% が純粋な RT、Evidence は 99.4%。修正後の US では非重複の約 45%（78,427 件）が retweet_only | RT を Public Voice に含めるか、別 class にするかの定義が必要。現状は仕様どおり（v4.4.0 の Shell と同じ）。`review_text_status` 列で区別できるようにした |
| B3 | **coherence の Low に到達できない** | §7 | 閾値・入力次元の再設計（案: cluster key 由来の次元を除外、CJK を文字 n-gram 化、1 段目が High 以外なら全件評価へ）。実データで分布を見てからの再チューニングが必要 |
| B4 | `duplicate_records` が常に 0、`eligible_records == cluster_records` | 重複 record は cluster に入らない設計 | 重複を保持 record の cluster に帰属させれば、転載数（増幅）を Prompt 1 に渡せる。是非はプロダクト判断 |
| B5 | Evidence モードでも Public Voice 語彙（`Public_Voice_Candidate` / `Fact_Only_Candidate` / attitude）が出る | Preprocess は mode を知らない | mode 別の列セットにするか、Prompt 側で無視する前提を明文化するか |
| B6 | `analysis_policy` が全モード固定のため、Evidence の Prompt 1 指示（49行目: `analysis_policy` / `report_title` が evidence 系か確認）の前者は常に確認不能 | §10 | 指示文を `analysis_contract` を見る形へ更新（Prompt 変更のため未実施） |
| B7 | 同一 URL（`#fragment` 違いの live feed）が 1 件に畳まれる | US の URL 重複 380 件中 118 件がフラグメント付き（例: jns.org の live blog） | 影響は全 record の約 0.07%。fragment を保持するか、URL 一致でも本文が違えば残すか |
| B8 | US の `primary_graph_signals` が空なのに master は 17 Signal とも `trend_role=primary` | 検証モジュールが警告 | Prompt 2 の最終グラフ分母の定義を確認 |

---

## C. Expected behavior（バグではない）

- **重複率が高い**: US 16.9%（media 38.2% / social 2.3%）、Evidence 35.4%。
  Evidence の S2 が 63.4% なのは、同じ Signal の CSV を 2 回アップロードしたため（後のファイルの 47,132 件が先のファイルと同一）。他 Signal は 25〜27% で、ほとんどが記事の転載。dedup は正しく動いている。
  重複ペア全件（US 35,452 / Evidence 138,817）を分類: 同一内容の転載（別ドメイン/別URL）が大半で、同一 URL・同一内容の完全重複は US 約 1.6%、Evidence 約 24.5%（アップロード重複）。
- **9/30 止まり（US）**: raw に 10/1 の記録は 0 件。全タイムスタンプが UTC（`Z`）で、`date_key` も UTC 日付。end が排他的な export 条件と推定。`date_key` 生成・変換に不具合なし。
- **representative_count Contract**: 全 cluster で実出力件数 = min(cluster 件数, max(representative_count, 3))（US の master は 17 行が representative_count=10、他は 8）。master から導いた期待値との不一致は 0。選定は record_id 順や先頭 N 件ではなく、hash による決定的な一様 reservoir sampling。
- **`text_for_review` を Python が見落としている**わけではない（`documentText` は読んでいる）。RT / 共有投稿 / 添付のみは「own text なし」の仕様どおり。
- **cap-hit 以外が Not Evaluated**: 設計どおり（評価コストを cap-hit に限定）。
- **同じ record が複数 Signal に存在**: dedup は Signal 単位の設計（US 10,379 件）。

## D. Data-quality limitations

- `source_name` は全 record で空（raw に出版元列がない）。代表の出所の多様性は domain でしか測れない。
- exception 候補の 75.9% は `attitude=unknown`（極性語・反応語が無いだけ）、15% は本文欠損。
- Evidence の最終日（10/2）は 1,666 件の部分日（export 時刻が 06:xx UTC）。S2 の raw は 9/16 開始で、他 Signal（9/1）と開始日が違う。
- `population_narrative_by_signal_language.csv` はヘッダのみ（DB に language 列が無い既知の制限）。
- 非重複行で (record_id, signal) が重複するケースが Evidence で 2 件、過去 run で 5 件（同じ documentId が本文違いで再出現）。
- 代表 8 件のうち、media cluster では US 29% / Evidence 14% の枠が同一見出しの重複（全体 12.6% / 6.8%）。distinct-text サンプリングで取り戻せるのは US 1,938 枠・Evidence 1,177 枠（2.5〜3.2%）。効果が小さいため変更せず。

---

## 項目別の結論（指示 #1〜#11）

| # | 項目 | 判定 | 要点 |
|---|---|---|---|
| 1 | dedup | **expected** | key は「media/official/pr_release のみ URL（scheme+host+path小文字+query、fragment除去）、それ以外は title+text 先頭3000字の正規化 hash」、Signal 単位・先勝ち・fuzzy なし・ファイル跨ぎあり・Signal 跨ぎなし。false positive 候補は B7 と、同一 title・本文で別 author（US 363 / Evidence 817 件）のみで小さい。3000字切り詰めで長さが 20% 超違うのは 0.37〜0.84%（見た範囲は同一記事） |
| 2 | text_for_review 欠損 | **expected + 区別不足** | §B2。`review_text_status` を追加 |
| 3 | source class の cluster 依存 | **bug（A4）** | 修正 + `source_class_purity` |
| 4 | representative 選定 | **expected** | 件数 Contract 通過。改善余地は小（§D） |
| 5 | master 照合 | **lint 実装（警告）** | §下記 |
| 6 | exception / consistency | **unclear → 説明修正** | 下記 |
| 7 | coherence | **bug 寄り（B3）** | 下記 |
| 8 | 日付 | **expected** | §C |
| 9 | configured period | **契約の欠落 → 解決列を追加** | 下記 |
| 10 | mode routing | **責務が曖昧 → 契約名を明記** | 下記 |
| 11 | package 検証 | **未実装だった → 実装（A1 を検出）** | 下記 |

### #5 Narrative master lint（`geo_report_master_lint_service.py`、新規）
検査: include⊂required / required⊂include / include と exclude の衝突 / 短い語・単語境界リスク / 名前だけで自己発火 / 汎用語 / ハイフン・引用符の表記ゆれ / alias collision / 多言語キーワード不足 / CJK・Latin の混在 / cue が説明文 / `A vs B` 形式の split term / Fallback 構成 / ID 重複 / 設定値の下限。
Run 作成時（`pin_run_snapshot`）に `master_lint_warnings` を返す（実行は止めない）。
- US（204 行）: 警告 335（うちデータ駆動の `high_hit_rate` 82）。`include_inside_required` 11（`govern` ⊂ `Governor`、6 Signal）。
- Evidence（30 行）: 警告 88。Evidence の `cues_not_phrases` 15（cue が長い説明文で、本文に一致しない）、`multilingual_keywords_missing` 3（S2/S5/S6 は入力の 19〜25% が非ラテン文字言語なのにキーワードはラテン文字のみ）。
- 過去 run のmaster（34 行）でも同系統の指摘（`A vs B` 形式 9 件、ハイフンのみの表記 66 件 等）。
- Evidence S1 の fallback 95% は、required が `US-China summit` のハイフン表記のみで空白表記にマッチしないことが一因（過去 run は両表記を足して 70%）。

### #6 exception / preliminary_consistency
- `review_reason` は 100% 同一の定数（US 48,175 件、Evidence 25,784 件）だった。fallback / mixed / unknown / 本文欠損 / 複数 reaction の混在。
- `preliminary_consistency=Low` の約 99% は `exception_candidate_count>0` と同値（US Analysis: Low 22,961 のうち 22,704）。Low になる条件（fallback>25% / cue=general / 件数>100 / exception あり）のうち exception だけが理由の cluster が約 33%。Medium は US で 6〜7 cluster のみ。意味的な不整合の指標ではない。
- **修正**: `review_reason` を具体的なコード（`fallback_match` / `mixed_attitude` / `unknown_attitude` / `missing_text` / `multiple_reaction`、`|` 区切り）に。`review_text_status` 列を追加。Prompt 1 入力の spec.md に「preliminary_consistency は機械的な参照値で意味的整合ではない」旨を追記。Prompt 本文は `review_reason` を参照していない。

### #7 semantic_coherence_status
- 入力: representatives（または全メンバー）の title+text を token 化した Jaccard 類似度 + attitude / source_bucket / matched_terms の最頻値の占有率。
- 実データ（US）: cap-hit 669（Analysis）/ 608（Sentiment）で、representatives 評価でも全件評価でも Low 0 件。diversity の最大値は Analysis 0.574、Sentiment 0.500（Low 閾値 0.65）。heterogeneous / split は常に 0。
- 原因: cluster key 由来で一定の次元（Sentiment は attitude・bucket・matched_terms 全て、Analysis は bucket・signature）が占有率 1.0 になり、本文が全く重ならなくても Sentiment は diversity ≤ 0.5。
- 到達性テスト: 完全に異質な cap-hit cluster を合成すると、Low → recursive split、および Low かつ分割候補なしで `heterogeneous_cluster=true` に到達できる（コード上は到達可能）。
- 追加の所見: CJK は連続文字列全体が 1 token になるため、ほぼ同文でも類似度が低い（日本語 0.33 / 英語 0.75）。1 段目（代表 8 件）が Low 以外だと全件評価に進まない（合成では見逃しを再現、US 実データでは差なし）。
- 閾値・次元の変更はプロダクト判断のため未実施。現状を `known_limitation` テストで記録。

### #9 configured monitoring period
- `configured_start_date` の出所は master の `trend_start_date`（行ごと）のみ。Evidence は master が空のため全 Signal で空。Prompt 2 EI 契約はこの列を Signal 別 `analysis_start_date` として参照する。Design Chat は `trend_start_date` を補完しない設計。Run には `period_start` / `period_end` があるが monitoring_scope には届いていなかった。
- **優先順位（定義・実装）**: ①master の `trend_start_date`（Signal 別）> ②Run の `period_start` > ③実データの最古日（設定値ではないため source に明示）。
  `configured_start_date` の意味は変えず、`effective_start_date` / `effective_start_source` を末尾に追加。
- 注意: Evidence は fallback が ③ になり、S2 は 9/16、他は 9/1 と Signal で開始日が揃わない。Run 作成時に `period_start` を指定するのが確実。Prompt 2 EI の指示文が `effective_start_date` を読むようにする更新は未実施（B6 と同じく Prompt 変更）。

### #10 Report mode / Contract routing
- アプリ内の分岐は **enum**（`report_design.analysis_mode` を Run の config に snapshot）で、Prompt の version dispatch（`ANALYSIS_MODE_PROMPT1/2_VERSIONS`）と、report_title / final_graph_rule の既定値が決まる。`report_title` 文字列では分岐していない。一方 Preprocess は mode 非依存で、パッケージには構造化されたモード値が無く、`report_title` / `final_graph_rule` の文面からしか判別できなかった。
- **実装**: `report_config.yaml` に `analysis_mode` と `analysis_contract`（`public_voice_monitor_v4_3` / `evidence_intelligence_v1`）を末尾追記。run の mode が不明な旧 run では出さない（推測しない）。既存キーは不変。
- 自動検証で、契約と report_title / final_graph_rule の食い違い（例: Evidence 契約なのに `public_opinion_report`）を error にする。

### #11 Prompt 2 package validation（`geo_report_prompt2_package_validation_service.py`、新規）
`build_prompt2_base_input_zip` が生成後に検証し、error が 1 件でもあれば `ValueError` で ZIP を返さない。検証項目: 必須ファイル / ZIP の整合性 / record map の全件性・chunk 数・連番 / manifest の件数 / 派生 CSV の合計 / monitoring_scope の完全性と開始日 / report_config のモード整合 / discovery 条件付きファイル / cluster ID の一意性 / 日付範囲 / 重複フラグ / record map の AC・SC ID が cluster master に存在。
実fixtureでの結果: US = error 4（record map 欠落ほか）、Evidence = error 8（欠落 + 開始日なし + 旧実行の行の混入 97 件 + record map が参照する 16 種の cluster が master に無い）、過去 run = error 3（開始日なし、非重複数が manifest 41,773 に対し record map 41,774 の 1 件差）。修正後の US・Evidence は error 0。

---

## E. 修正PR相当の差分一覧

| 区分 | ファイル | 内容 |
|---|---|---|
| M | `geo_report_prompt_export_service.py`（+42/-4） | `_fetch_all_records` の終了条件修正（A1）、`RECORD_MAP_CHUNK_SIZE` 共通化、ZIP 生成後の自動検証と失敗時の例外 |
| M | `geo_report_current_pipeline_service.py`（+約170/-15） | `get_source_bucket`（A2/A3）、`finalize_cluster_labels`（A4）、split 付け替えキー（A5）、`review_text_status`、`get_exception_reasons`、`source_class_purity`、`resolve_effective_start` / `effective_start_*`、`resolve_analysis_contract` と yaml 追記、`RECORD_MAP_CHUNK_SIZE` |
| M | `geo_report_current_pipeline_persistence_service.py`（+32/-8） | mode / contract / Run の period_start の受け渡し、manifest の実 chunk 数、spec.md の説明追記 |
| M | `geo_report_run_provisioning_service.py`（+24/-6） | Run 作成時の master lint 警告（非ブロッキング） |
| A | `geo_report_master_lint_service.py` | master lint |
| A | `geo_report_prompt2_package_validation_service.py` | package 検証 |
| M | `tests/_fakes.py`（+7/-1） | `FakeSupabaseClient(max_rows=)`（既定は無効、既存テストに影響なし） |
| M | `tests/test_geo_report_prompt_export_service.py`（+9） | 既存テストは検証を既定で無効化する autouse fixture（検証専用テストは別ファイル） |
| M | `tests/test_geo_report_current_pipeline_service.py` | 列数 41→42 |
| 未変更 | `geo_report_processing_service.py` / `geo_report_cluster_semantic_service.py` | 旧 Iran/EUDR 契約・coherence は変更なし |

confirmed bug の修正: A1〜A5（A6 は検出のみ）。予防的ハードニング: lint・package 検証・contract 明記・effective start・`review_text_status`・`source_class_purity`・exception 理由・manifest chunk 数。
DB スキーマ変更なし。追加列はすべて CSV の末尾（列名で読む既存 consumer と互換）。

## F. 追加テスト（64件、全て通過。`tests` 全体 150 ファイルも通過）

- `tests/test_geo_report_preprocess_latent_bug_audit.py`（22）: bucket（gov / WordPress）、AC 多数派ラベル、split 付け替え、review_text_status、exception 理由、effective start、analysis_contract、purity、coherence の到達性（Low / split / heterogeneous）、`known_limitation` 記録（key 一定次元・CJK・1段目の見逃し）
- `tests/test_geo_report_prompt2_package_validation_service.py`（19）: 欠落 record map、旧実行の行の混入、chunk 欠け、派生 CSV 不一致、重複フラグ、cluster ID、開始日、日付範囲、モード整合、discovery、ZIP 整合、`db-max-rows` 相当でも全件取得、打ち切り時に ZIP を生成しない
- `tests/test_geo_report_master_lint_service.py`（21）: 各 lint コードと run 作成時の警告
- `tests/test_geo_report_current_pipeline_persistence_service.py`（+2）: contract / effective start / manifest / spec の統合
- **修正前コードでの確認**: preprocess 側の新規テストのうち 14 件は修正前コードで失敗する（特に A5 の split 付け替えを再現）。修正前の `_fetch_all_records` は上限 1000 の Fake で 209,976 件中 1,000 件で停止。

## G. US fixture before / after（`run_preprocess` 再実行）

| 指標 | before | after | 説明 |
|---|---|---|---|
| 総レコード | 209,976 | 209,976 | 不変 |
| 非重複 | 174,524 | 174,655 | +131。旧 official から URL dedup 対象外のバケットに移った 133 件が dedup されなくなった |
| Analysis / Sentiment cluster | 31,227 / 36,514 | 30,434 / 35,794 | バケットが cluster key に含まれるため、旧 official cluster が media / social 等へ統合 |
| **マッチング結果が変わった record** | — | **0** | Narrative・match_status・matched_terms は全件不変（matched 141,204→141,328 は上記 dedup 差のみ） |
| Official | 4,452 | 265 | 旧バグ除去（-4,187） |
| Media | 48,661 | 52,024 | +3,363 |
| Public_Voice_Candidate | 37,269 | 37,860 | +591（旧 official の Reddit / Twitter 等が戻る） |
| Unknown / Corporate_PR | 5,304 / 135 | 5,649 / 121 | |
| バケットが変わった record | — | 5,903 | 5,857 が旧 official から（media 4,848 / social 525 / unknown 357 / blog 107）、46 が WordPress |
| Analysis のラベル不一致（source_class） | 11,407（6.5%） | 6,379（3.7%） | 最頻値にしても混在は残る。真値は `source_distribution` |
| AC と SC の source class 差（PV） | +2,003 | -4,719 | **改善ではなく見え方の変化**。AC ラベルは最頻値（少数派は見えない）、SC は record 単位の真値。集計は record 単位で行うこと |
| cap-hit の coherence | Medium 591 / High 78 | Medium 601 / High 77 | Low 0 のまま。heterogeneous / split は 0 |
| record map 行数 | 1,000 | 209,976（5 chunk） | A1 修正。検証は error 0 |

## H. Evidence fixture before / after

| 指標 | before | after | 説明 |
|---|---|---|---|
| 総 / 非重複 | 391,909 / 253,092 | 391,909 / 253,157 | +65（旧 official 等から移った 67 件が dedup されなくなった） |
| Analysis / Sentiment cluster | 9,739 / 13,007 | 9,613 / 12,877 | 同上 |
| **マッチング結果が変わった record** | — | **0** | |
| Official | 2,044 | 858 | -1,186 |
| Media | 142,141 | 143,110 | +969 |
| Public_Voice_Candidate | 58,838 | 58,965 | +127 |
| バケットが変わった record | — | 1,728 | 旧 official から 1,683、旧 pr_release（WordPress）から 45 |
| Analysis のラベル不一致（source_class / attitude） | 15,330 / 27,150 | 10,724 / 17,399 | |
| configured_start_date | 全 Signal 空 | 空のまま。`effective_start_date` を解決（source=actual_min_date） | master / Run の期間が無いため実データ fallback |
| record map 行数 | 1,000 | 391,909（8 chunk） | 検証は error 0 |

「以前の数字と違う」ことの内訳は、(1) 旧 official の誤分類が解消されたバケット・class の移動、(2) それに伴う cluster 構成の変化、(3) URL dedup 対象外になった少数 record、の 3 つで説明できる。マッチング自体は変わっていない。

## I. Prompt 1 / Prompt 2 への影響

**Prompt 2（要対応）**
- **2026-10-02 の US / Evidence の Prompt 2 base ZIP は不完全**（record-level の集計が全体の 0.5% / 0.26%）。そのまま Prompt 2 に使わないこと。US は標本に旧実行の行の混入が無く、DB に全件保存されている前提（実 DB は未確認）であれば、修正後のコードで同 Run の Prompt 2 base を再生成すれば全件入る（Preprocess の再実行は不要）。再生成時は自動検証が件数を確認するため、前提が違えば止まる。**Evidence は旧実行の行が混ざっているため（A6）、再生成は検証で止まる**。新しい Run で Preprocess からやり直すのが安全。
- ただし Run の cluster / バケット（Official 過大）は旧ロジックのまま。Prompt 1 の結果をこれから使うなら、Preprocess の再実行を推奨。
- `monitoring_scope.csv` に `effective_start_date` / `effective_start_source` が増える。Prompt 2 EI の `analysis_start_date` は引き続き `configured_start_date` を読む（空）。指示文の更新が必要（B6）。

**Prompt 1**
- `cluster_labeling_input.csv`: Analysis の source_class / reaction / attitude が最頻値になり、`source_class_purity` が末尾に増える。
- `cluster_representatives.csv` / exception CSV: `review_text_status` が末尾に増える。`review_reason` の値が具体化。
- `report_config.yaml`: `analysis_mode` / `analysis_contract` が増える。
- Official が大幅に減り、旧 official の SNS 投稿が Public_Voice_Candidate / Fact_Only に戻る（US で Public_Voice_Candidate +591）ため、Prompt 1 が見る cluster の顔ぶれが変わる。

## J. まだ未解決の項目

- 過去 run（9/16）で非重複数が manifest 41,773、record map 41,774 と 1 件食い違う（原因未特定。raw なし）。検証は厳密一致のため、当該 run は error になる。
- プロダクト判断待ち: B1（matcher と master）、B2（RT の扱い）、B3（coherence の再設計）、B4、B5、B6、B7、B8。
- Prompt 指示文の更新（`analysis_contract` / `effective_start_date` を読む）は未実施。
- record 取得の往復回数（US 約 210、Evidence 約 392）。Supabase の `db-max-rows` を引き上げる、またはサーバ側で集計する案。当初 page_size を 5000 にしたのは往復回数の削減が目的だった。
- A6（旧実行の行の混入）の根本対応: 旧行の削除、または `execution_id` 列の追加。データ削除・スキーマ変更を伴うため判断待ち。cluster 系テーブルの混入有無も未確認。
- 実 Supabase での通し確認は未実施（Fake と修正前コードでの再現のみ）。実 DB の Evidence Run は、実際に旧行が残っているかを直接確認していない（fixture の record map からの推定）。
- 旧 `run.ps1` 時代の過去 US run（Desktop/BScontinuous）は再検証していない。
- Evidence の S2 など、実データ fallback の開始日が Signal で揃わない問題（Run の `period_start` 指定で回避可能）。
- master の内容修正（`ICE` / `govern` / ハイフン表記 / 説明文の cue）は内容の持ち主の判断。lint は警告のみ。
