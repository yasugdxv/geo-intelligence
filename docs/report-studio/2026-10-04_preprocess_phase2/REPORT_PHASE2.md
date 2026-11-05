# REPORT_PHASE2 — 定点レポート共通Preprocess 第2段階の修正とE2E検証（2026-10-04）

目的: **正しいPreprocess成果物を生成できる状態を固定し、その成果物をPrompt 1へ渡してよいかQAする。**
Prompt 1 / Prompt 2 は実行していない。Promptファイルも書き換えていない。DB schema migrationは実行していない。ファイル削除もしていない。

## 検証の前提（最初に読むこと）

- **新Runは「実DBではなく、本番の保存・エクスポート関数＋Fake DB」で作成した。** 実Supabaseへの大量書き込み
  （Run作成・約60万行のinsert）は外向きの副作用が大きいため、承認なしには実行していない。Fake DBはPostgREST
  `db-max-rows=1000`相当の上限を再現しており、前回のバグ（record mapが1,000行で終了）が再発すれば検出できる条件。
  **実DB上の新Runは、承認後に同じ手順で作成する必要がある**（§L）。
- 旧Evidence run（a1cb0baf）は再利用していない。新Runは `run_and_persist_preprocess` を最初から実行した。
- 「before」は、第1段階の修正を含む旧matcher（部分一致）＋旧masterの再実行結果。再実行は前回のfixtureと
  総数・cluster数まで一致することを確認済み（USで209,976 / 174,524 / 31,227 / 36,514）。
- 全テスト: **2,366件すべて通過**（第1段階は2,253件。第1段階で追加した回帰テストも全て維持）。

---

## A. Executive Summary

| 領域 | 今回やったこと | 結果 |
|---|---|---|
| Narrative keyword matcher | `keyword_matcher_v2`を新設（Latin token / Latin phrase / 大文字アクロニム / 明示的な語頭一致`*` / CJKを別処理）。`match_master`と`get_cluster_cue`を置換。include/contextの自己発火を禁止し、根拠を別々に記録 | 全USコーパスで`ICE`の誤ヒット92,328件(87%)、`govern`の誤ヒット84,309件を除去。**独立検証で語境界でない一致は0件**（US 177,588件、Evidence 37,499件） |
| Narrative master lint | 実データの語彙・ヒット率・言語分布を使うlintを組み込み、Preprocess毎に`narrative_master_lint.csv`を出力 | US masterの警告 1,450 → 553 |
| US Narrative master | 決定表から`signal_narrative_master_us_v2.csv`と変更前後一覧を生成（自動の一括書き換えではない） | 置換53語・1,375箇所、追加13、見直しのみ26 |
| Run isolation | 根本原因を特定。execution単位の期待record集合(`record_id_manifest.csv`)を保存し、export時にstale/missingを厳密比較して不一致ならZIP生成を失敗させる。artifactはcanonical executionから読む | Evidence旧runの混入（9.7%）を再現・検出。新Runはstale 0 / missing 0 |
| requires_review | 一律Trueをやめ、理由を分離（`review_reasons`） | US 38% / Evidence 21%のclusterが`false`になり得る |
| semantic coherence | 合成fixture A〜Dで検証。`lexical_v2` backendを**選択式で**追加（既定は変更しない） | Low / heterogeneous / splitに到達可能。実データでは既定化できない（§I） |
| 新Run E2E | US・Evidenceとも最初から生成 → Prompt 1 input → Prompt 2 base | record map全件、stale 0、missing 0、ZIP整合、検証error 0 |

**判定は§L**: US・Evidenceとも `GO WITH WARNING`（実DBでの新Run作成と、US master v2の公開が前提）。

---

## B. Confirmed Bugs Fixed（実データまたはtestで再現したものだけ）

| # | バグ | 根拠 | 修正 |
|---|---|---|---|
| B1 | Narrative keywordが単純な部分一致 | 全USコーパス(209,976件): `ICE`が105,731件にヒット、アクロニムは13,403件のみ（誤ヒット92,328件: `office` 17,708 / `prices` 11,411 / `vice` 8,056…）。`govern` 84,824件→515件（`Governor`だけで当たったのが69,339件）。`rent` 37,273→1,143、`lose` 34,721→6,445、`beat` 10,689→2,741、split termの`ad` 166,749→17,872 | keyword_matcher_v2（§D） |
| B2 | includeとrequired contextが同一surfaceの部分一致だけで両方成立（`govern`+`Governor`） | 修正前コードで再現（testで固定） | include語がcontext語に含まれる/同一なら、位置・出現回数に関係なく別根拠とみなさない |
| B3 | split term（cluster cue）も部分一致（`ad`→`had`/`head`/`already`） | 同上 | `get_cluster_cue`も同じmatcherを使用 |
| B4 | prompt1_input artifactを`run_id`だけで読んでいた（再実行したRunでは旧実行のartifactを任意に読み得る） | 同じRunに複数Preprocess executionがあるとartifactが複数存在（コードとFakeで再現） | canonical Preprocess executionのartifactを優先して読む（旧Runは従来どおりrun単位にフォールバック） |
| B5 | 旧実行のrecordがexportに混入し、silent warningすらなく続行し得た | §F。Evidence run a1cb0baf: record mapの9.7%が旧9/14ファイル由来 | 期待record集合との厳密比較。stale>0またはmissing>0でZIP生成を失敗させる |
| B6 | `requires_review`が全clusterで常にTrue | 実データ: 全29,951 / 9,643 clusterがTrue | 理由を分離。理由が空ならfalse（§H） |
| B7 | 標本に基づくlintのノイズ（同じ行で列挙済みの語形を「未カバー」、`*`を記号扱い） | 実データのlintで確認 | 同じ行で列挙済み/`*`指定済みの語形は未カバー扱いにしない |

第1段階のConfirmed Bug（record map 1,000行打ち切り、official誤分類、WordPress、AC先頭値ラベル、split cross-signal）は
全て維持している（回帰テスト通過）。

---

## C. Product Decisions Not Changed（勝手に変えていない）

- **RT / Fact_Only_Candidate**: pure RTは引き続き`Fact_Only_Candidate`。ただし区別できるかの監査結果を下に報告する。

  | SNS投稿の種類（raw、重複含む） | US (115,512件) | Evidence (81,070件) |
  |---|---|---|
  | 純粋なRT（own textなし） | 79,177（68.5%） | 20.9% |
  | 本文＋引用/添付（`[quoted @`・`ATTACHMENT`・共有投稿を含む） | 17,427（15.1%） | 28.7% |
  | 原投稿 | 18,534（16.0%） | 50.3% |
  | 添付のみ / 共有投稿のみ | 295 / 23 | 0.2% / 0.0% |
  | **RT＋投稿者のコメント** | **3** | ほぼ0 |
  | 空 | 53 | ほぼ0 |

  `review_text_status`で「pure RT」「添付/共有のみ」「本文なし」は区別できる。**「quote RT」と「RT+author comment」は、
  現行のShell出力からは区別できない**（コメント付きRTはrawでもほぼ存在しない。引用は`own text + quote`として本文側に入る）。
  将来のProduct decisionの材料: USは非重複の約45%（78,427件）がretweet_onlyでFact_Only。
- **カテゴリ語彙（Evidenceの`Public_Voice_Candidate`等）**: 変更していない。§Jの次フェーズ提案を参照。
- **coherenceの既定backend**: 変更していない（§I）。
- **dedup規則、representative選定、source class判定の仕様**: 変更していない。
- **Evidence master**: 変更していない（US masterのみ修正）。lintの指摘は§Kに記載。
- **ERROR時のRun停止**: 既存policy（Publish Gateは構造不備のみ停止）を確認し、keyword品質のlintは**停止条件にしていない**
  （警告として`narrative_master_lint.csv`に出すのみ）。

---

## D. Keyword Matcher

**現在の実装の特定**: Narrative割当を決めているのは`geo_report_current_pipeline_service.match_master`（`run_preprocess`から
1レコードにつき1回、`title + text`先頭3000字で呼ぶ）。`get_cluster_cue`が同じ部分一致でsplit termを判定していた。
他に割当を決める関数は無い。

### 旧 → 新

| 項目 | 旧 | 新（keyword_matcher_v2） |
|---|---|---|
| 照合 | `term.lower() in text.lower()` | 種類別（下表）。照合用の表面はNFKC＋記号を空白化。原文のmaster表記`surface`は保持 |
| 語境界 | なし | Latinはトークン/句の境界。LatinとCJKの境目（`AI規制`）も境界 |
| 語幹展開 | 偶然（部分一致） | **しない**。語形はmasterで明示（列挙、または`rent*`） |
| 大文字小文字 | 無視 | 句・単語は無視。**全て大文字の2〜3文字（`ICE`/`AI`/`US`/`GOP`）はcase-sensitive** |
| 句の揺れ | `US-China`と`US China`は別物 | 大文字小文字・連続空白・Unicode空白・句読点・ハイフンを吸収。**語順は変えない** |
| CJK | Latinと同じ部分一致 | 別matcher（NFKC、空白・記号を除いた文字列の部分一致。Latin用の境界を流用しない） |
| ハッシュタグ/ハンドル | 小文字化して部分一致 | CamelCaseは語に分割（`#StopTrumpCorruption`、`@KatieHobbs`）。全て小文字（`#katiehobbs`）は分割できない（既知の制限） |
| include×context | 独立性なし | 同一surface（includeがcontextに含まれる/同一）では両方成立させない |
| 監査列 | `matched_terms`のみ | 代表レコードに`matched_context_terms` / `matched_exclude_terms` / `match_method` / `matched_span`を追加。manifestとreport_configに`keyword_matcher_version` |

matcherの種類: `latin_token` / `latin_phrase` / `latin_acronym` / `latin_prefix`（`*`） / `cjk_phrase`。
Contractの例（`ICE`≠`office`、`govern`≠`Governor`/`governance`/`governing`、`rent`≠`parent`/`different`/`current`、`lose`≠`close`、
`beat`≠`Beatty`、`cost of living`=`Cost-of-Living`、CJKは別処理）は全てtestで固定した（68ケース）。

**指示との差分（要確認）**: 例示にあった「`rent`でOK: `rents`」「`lose`でOK: `losing`」は、「matcher側が勝手に語幹展開しない」
「複数形等はmaster側で明示」という方針に従い、matcherでは**許可していない**。masterに`rent*`または`rents`を書いた場合のみ一致する。

### false positive比較（全USコーパス209,976件、raw、`title+text`先頭3000字）

| 語 | 旧(部分一致) | 新 | 除去された誤ヒット | 代表的な誤ヒット語 |
|---|---|---|---|---|
| ICE | 105,731 | 13,403 | 92,328 | office / prices / vice / bice / service / police |
| govern | 84,824 | 515 | 84,309 | governor 61,654 / government 15,920 |
| rent | 37,273 | 1,143 | 36,130 | different / current / currently / parents |
| lose | 34,721 | 6,445 | 28,278 | close / closely / closed |
| beat | 10,689 | 2,741 | 7,948 | Beatty / beating |
| ad（split term） | 166,749 | 17,872 | 148,877 | had / made / Adams / ahead |
| unity / unite | 22,684 / 24,666 | 1,343 / 1,213 | 21,341 / 23,453 | community / united |

**独立検証**（matcherを使わず、別の正規表現で「語境界の一致か」を実rawの本文で確認）:
US 177,588件の語一致、Evidence 37,499件の語一致で**違反0件**。内訳はUSで、通常の語境界175,936、`#`/`@`のCamelCase分割による一致1,652。
これにより受け入れ基準の `ICE` false positive ≈ 0、`govern→Governor` = 0、`rent`/`lose`/`beat`等 = 0 を満たす。

---

## E. Narrative Master（US）

- 生成物: `signal_narrative_master_us_v2.csv`（修正後）、`narrative_master_changes_us.csv`（変更前後・理由・旧/新のヒット数）、
  生成スクリプト`build_us_master_v2.py`（決定表）。**DBのmaster assetには反映していない**（Designで公開するまで実Runは旧masterを使う）。
- 原則: キーワードを消してfallbackを増やすのではなく、「旧matcherが実際に拾っていた正しい語形」だけを明示して再現率を維持する。
  旧でも拾えなかった語形への拡張はしない。

| 種類 | 語（件数は標本での旧ヒット） | 本来の意味 / 旧の誤ヒット | 対応 |
|---|---|---|---|
| 意味が曖昧 | `govern`（S*-10、17,858） | governing ability / governance criticism。旧: `Governor`(13,374)・`government`(3,007) | `governance; governs; governed`へ置換（`governing`は既存。`government`は候補者の統治能力ではないので含めない） |
| 誤ヒットが主因 | `rent` / `lose` / `beat` / `unite` / `ad` / `rally` | 家賃 / electability / 〃 / 党内融和 / 広告(split) / 集会 | 旧が拾っていた正しい語形だけ明示（`rent; rents; rental…`、`lose; loses`、`beat; beats; beating; beaten`…）。`losing`等は足さない |
| アクロニム | `ICE` | Immigration and Customs Enforcement | `ICE`はcase-sensitive exact tokenのまま。小文字表記向けに`ICE raids; ICE agents; ICE detention; immigration enforcement`を追加 |
| 再現率の維持（context） | `Democrat` / `vote` / `candidate` / `election` / `campaign` | 旧は`democrats`(5,702)・`voters`(5,960)等を拾っていた | `Democrat*` / `vote*` / `candidate*` / `election*` / `campaign*`（語頭一致を明示） |
| 再現率の維持（語形） | `trust` / `corrupt` / `honest` / `crime` / `border` / `scandal`… 約30語 | 複数形・派生形 | 旧が拾っていた形のみ列挙 |
| 再現率の維持（役職・州名） | `Governor` / `New York` / `Pennsylvania` / `Arizona`… | context欠落239件の原因（`New Yorkers` 54、`Governors` 51、`Pennsylvanians` 21） | `Governors`、`New Yorkers`、`Pennsylvanians`、`Illinoisans`等を明示 |
| 見直しのみ（変更なし） | `unity` / `gender` / `labor` / `transit` / `Ford` / `Barr`等26件 | v2のexact tokenで解消 | 理由つきで`reviewed_no_change`に記録 |

**lint（実データの語彙を使用）**: 旧master WARNING 1,450 / INFO 4,584 → v2 master WARNING 553 / INFO 4,547。
`include_inside_required`（`govern`⊂`Governor`）11→0、`required_exclude_overlap` 102→0。残るWARNINGは
`inflection_not_covered` 249（`Governor`88、`independent`34、`moderate`34…）、`generic_word` 98、`prefix_collision` 85、`punctuation_variant` 78、
`high_hit_rate` 41、`short_keyword` 2。いずれも`qa/narrative_master_lint_us_v2master.csv`に`recommended_action`つきで出力。

---

## F. Run Isolation

### root cause（コードとスキーマで確認）

| 確認項目 | 結果 |
|---|---|
| recordの識別key | `geo_report_records`は`run_id`のみ。`execution_id`列も`(run_id, record_id)`の一意制約も無い。`created_at`はDB既定値 |
| cluster / representativeテーブル | 同じく`run_id`のみ |
| artifact（`prompt1_input`） | `execution_id`を持つ（executionで区別可能） |
| 再実行時の挙動 | **insert only**。upsertも、delete+insertも、version appendも無い。旧行は残る（削除は手動の`_cleanup_stuck_run.py`のみ） |
| exportの取得 | `_fetch_all_records`は`run_id`のみで取得。prompt1_input artifactも`run_id`で引いており、複数executionがあると任意の1件 |
| 他のID | `report_id` = `run_id`。`input_file_id`相当の列は無い。`source_file`（ファイル名）と`created_at`は行に残る |

→ 同じRunでPreprocessをやり直すと、旧実行のrecord・cluster・artifactが残り、exportに混ざる。再現: 旧ファイル20件のPreprocessの後に
新ファイル30件でやり直すと、DBは50件になり、修正前のexportは50件を出す（testで固定）。

### Option A / Option B

| | A: schema変更なし | B: `execution_id`列追加 |
|---|---|---|
| 方法 | `created_at`が実行の`started_at`〜`completed_at`内かで絞る、または集合比較 | recordとcluster系5テーブルに`execution_id`を追加し、exportでcanonical executionで絞る |
| migration risk | なし | 中（5テーブルのALTER、アプリの保存・取得の同時変更） |
| cleanup | 旧行は残る | 旧行は`execution_id`で特定でき、削除対象を厳密に選べる（削除は別承認） |
| 後方互換 | `created_at`はDBとアプリの時計差・resume・並行実行で境界が曖昧 | 旧行は`NULL`のまま従来動作。新Runから厳密 |
| 既存Runの挙動 | 変わらない | 変わらない（NULL行は従来どおり） |
| Prompt packageのexport | 時刻窓の推定に依存（誤って正しい行を落とし得る） | 正確 |

**採用**: 今回は**Option Aの考え方（schema変更なし）を、推定ではなく「厳密な集合比較」として実装**した。
`created_at`で絞るのではなく、Preprocess時に**今回のexecutionが作ったrecord集合**（`record_id_manifest.csv`、execution単位のartifact）を
保存し、export時にDBの行と`(record_id, signal, source_file)`の多重集合で比較する。stale>0 または missing>0 ならZIP生成を失敗させる
（Signalを跨いで同じrecord_idが存在するため、record_id単独ではなく3つ組で比較）。Option Bは`PROPOSED_execution_id_migration.sql`として
提案ファイルに残した（**実行していない**）。当面の運用は「Preprocessをやり直す場合は新しいRunを使う」。

### 実装した再発防止

- `record_id_manifest.csv`（Preprocessのartifact、execution単位）。Storageへ自動退避される大容量artifactにも対応。
- export時: `expected` vs `exported`を比較し、stale件数・missing件数・例・主なstaleの`source_file`を含むエラーで失敗。
  旧Run（manifestなし）は集合比較できないため、件数・`source_file`の検証にフォールバック。
- Prompt 1 input: manifestの非重複数とAnalysis/Sentimentのeligible合計を照合（別executionのartifactの混入を検出）。
- canonical executionのartifactを優先して読む。
- 検証した4シナリオ（test）: 正常（通る） / 再実行でstale（失敗、`S1_old.csv`の20件を指摘） / missing（失敗、3件欠落を指摘） /
  再実行後のartifact取得（canonicalの新executionを読む）。

---

## G. US Before / After

条件: **old** = 旧matcher＋旧master（第1段階修正後）/ **v2+old master** = 新matcherのみ / **v2+v2 master** = 新matcher＋修正master（新Run）。
matcher変更の効果とmaster修正の効果を分離している。

| 指標 | old | v2+old master | v2+v2 master（新Run） |
|---|---|---|---|
| total records | 209,976 | 209,976 | 209,976 |
| nonduplicate | 174,655 | 174,655 | 174,655 |
| matched narrative | 141,328 | 108,360 | **116,732** |
| fallback | 33,327（19.1%） | 66,295（38.0%） | **57,923（33.2%）** |
| Analysis / Sentiment clusters | 30,434 / 35,794 | 27,493 / 32,195 | 29,951 / 34,679 |
| representatives（Analysis） | 75,803 | 75,506 | 77,245 |
| `ICE`にmatchしたrecord | 19,604 | 3,151 | 3,196 |
| `govern`にmatchしたrecord | 22,916 | 150 | **0** |
| 短いLatin語(≤3文字)でmatchしたrecord | 19,604 | 3,151 | 3,196 |
| `govern`→`Governor`だけで成立 | 69,339件（全コーパス） | 0 | 0 |

dedup（非重複174,655）は3条件で不変。マッチング以外（bucket・source class・dedup）は第1段階の結果を保持している。

### Narrative分布（全17 Signal合算、非重複）

| Narrative | old | v2+old master | v2+v2 master | 主な理由 |
|---|---|---|---|---|
| -00 fallback | 33,327 | 66,295 | 57,923 | 旧substring誤ヒットの除去 |
| -03 党内融和/分裂 | 7,551 | 2,738 | 2,652 | `unity`→`community`、`unite`→`united`の誤ヒット除去 |
| -07 経済・生活費 | 27,088 | 23,687 | 24,394 | `rent`→`parent`/`current`等 |
| -08 社会課題 | 48,294 | 38,308 | 41,430 | `ICE`→`office`/`prices`/`police` |
| -09 electability | 8,234 | 5,669 | 6,848 | `lose`→`close`、`beat`→`Beatty` |
| -10 能力・統治 | 33,428 | 16,647 | 18,626 | `govern`→`Governor`/`government` |
| -11 キャンペーン事象 | 7,545 | 10,700 | 11,740 | 上位Narrativeの誤ヒットが減り繰り上がり |

Signal別のNarrative分布は`us_narrative_before_after.csv`（全Signal×Narrative、旧/中間/最終と差分）、
Driver Top 3は`us_driver_top3_before_after.csv`。**Driver Top 3は17 Signal中14で変化**した（例: S1は`S1-08>S1-10>S1-07`から
`S1-10>S1-08>S1-07`へ、S2は`S2-08>S2-10>S2-07`から`S2-11>S2-08>S2-07`へ）。

### 変化の分類（record単位、旧→最終）

| 分類 | 件数 | 判定 |
|---|---|---|
| Narrative不変 | 97,913 | — |
| fallbackのまま | 33,048 | — |
| matched → fallback（旧が衝突語だけで成立していた） | 24,262 | **期待された変化**（旧substring誤ヒットの除去） |
| Narrative変更 | 18,540 | 誤ヒットの除去で別のNarrativeが勝った（期待された変化） |
| fallback → matched（新しい語形・CamelCase分割で回収） | 279 | 再現率の改善 |
| **matched → fallback（衝突語以外が絡む、要確認）** | **613** | 下記 |

**要確認の613件**: 当初は1,015件だった。raw本文で原因を分解したところ、include側768件（`stoptrumpcorruption`などのハッシュタグ内、
`government`、`dishonest`、`disqualified`、`choice`）とcontext側239件（`New Yorkers`、`Governors`、`Pennsylvanians`）だった。
context側はmasterに語形を明示、ハッシュタグはCamelCase分割で救済し、613件に減った。残りの主な語は`honest`(188)、
`independent`(59)、`Trump`(43)、`immigration`(33)など（`dishonest`/`independently`等、旧の部分一致でしか当たっていなかった形が主）。
613件の個別分解は第1回目の1,015件ほど詳細に行っていない。**意図しない欠落の可能性があるのはこの613件（全体の0.35%）**。

---

## H. Evidence Before / After と新RunのPreprocess QA

### 新Run（run `fresh-ev-run`、旧Runは再利用していない）

| 確認項目 | 結果 |
|---|---|
| 総 / 非重複 | 391,909 / 253,157 |
| Analysis / Sentiment cluster | 9,643 / 12,836 |
| **stale records** | **0**（期待391,909、export391,909） |
| **missing records** | **0** |
| record map | 全件、8 chunk（manifestのchunk数も8） |
| Prompt 2 package validator | **error 0**。warning 2件: `record_id_signal_not_unique`（同じSignal内で同じdocumentIdが本文違いで再出現、2件）、`configured_start_date_empty` |
| ZIP整合 | `testzip` OK |
| `analysis_contract` | `evidence_intelligence_v1`（`analysis_mode: evidence_intelligence`、`keyword_matcher: keyword_matcher_v2`も記録） |
| discovery（S6） | `discovery_candidates.csv` / `discovery_candidate_representatives.csv`が存在（Contract充足） |
| configured / effective start date | `configured_start_date`は全Signal空（masterに`trend_start_date`なし）。`effective_start_date`は`actual_min_date`由来（S1/S3/S4/S5/S6=2026-09-01、**S2=2026-09-16**）。由来は`effective_start_source`で確認可能 |
| source taxonomy | §J-2。Contractとの矛盾なし。ただし混在あり |
| 語境界の独立検証 | 37,499件の語一致で違反0 |

### 旧（第1段階修正後）との比較

matcherの変更は、Evidenceではfallback率をほとんど動かさなかった（全体で88.7%のまま）。

| Signal | 非重複 | fallback旧 | fallback新 | 備考 |
|---|---|---|---|---|
| S1 | 12,644 | 95.1% | 93.4% | `US-China summit`等のハイフン/空白表記を吸収 |
| S2 | 34,595 | 66.4% | 66.7% | |
| S3 | 7,468 | 59.8% | 63.6% | |
| S4 | 5,130 | 29.1% | 26.0% | |
| S5 | 78,880 | 90.2% | 88.8% | |
| S6（discovery） | 114,440 | 98.3% | 99.2% | `S6-N2`（security concern等）1,710→755: contextの`meeting`/`summit`の語形（`meetings`等）が部分一致で拾われていたのがexact tokenで落ちた |

record単位: matched→同Narrative 24,345 / fallback→matched 2,492 / matched→fallback 2,376 / Narrative変更 1,833。
**Evidenceのfallback率が高いのはmasterの設計（required contextの句が厳格、cueが説明文）によるもので、matcherの問題ではない。**
S6の悪化はUSと同じ種類（context語形の未明示）で、Evidence masterはlintの指摘（`inflection_not_covered` 86、`cues_not_phrases` 15、
`multilingual_keywords_missing` 3）を反映して別途見直す必要がある（今回は変更していない）。

---

## I. Semantic Coherence Tests

`tests/test_geo_report_semantic_coherence_fixtures.py`（10件）。実データを強制splitしていない（既定backendは`deterministic`のまま）。

| Fixture | 内容 | 期待 | 結果（`lexical_v2`） |
|---|---|---|---|
| A homogeneous | 200件すべて同じ意味 | splitしない | `High`、diversity ≤ 0.40、split 0、heterogeneous false |
| B heterogeneous | tariff / Taiwan / AI / Iran 各50件 | 異質を検知して分割 | **`Low`**、4分割（`split_reason=lexical_anchor=<語>`、depth 1）、各子は`High`で再分割しない、record mapは子clusterを指す |
| C syndicated duplicates | 同じ内容のMedia転載200件 | 量が多いだけではsplitしない | `High`、diversity 0.0、split 0（本文がoutlet毎に違うのでdedupされない構成） |
| D mixed source | Government/Media/Socialでも同じ意味 | source違いだけではsplitしない | `High`、split 0。**既定backendはsource_bucketの占有率をdiversityに入れ、同じ意味でも高く出る**（testで記録） |

- **Low branchへの到達**: Fixture Bと、`Low`かつ分割候補なしの別fixtureで`heterogeneous_cluster=true`に到達することをtestで保証。
- **既定backendの限界（`known_limitation`として記録）**: Fixture Bは既定の`deterministic`では`Low`にならず分割されない
  （cluster key由来の次元が占有率1.0になり、diversityが約0.5で頭打ち）。実データでLowが0件の理由。
- **v2の実データへの影響（報告のみ）**: US cap-hit 678 clusterのうち606（89%）、Evidence 794のうち734（92%）が`Low`。
  実際のclusterは「話題」ではなく「キーワードで括ったNarrativeの入れ物」であり、語彙の類似度では本質的に広く出るため、
  **v2を既定にすると事実上すべてのcap-hit clusterが分割される**。既定化は閾値の較正（ラベル付き標本）が済んでから。
- 追加の知見: SNSの`title`（`Post 12`等の定型語）は類似度を押し上げるため、v2は本文を優先する。CJKは文字bigramで評価する。

---

## 要求事項の個別確認

### requires_review（#18）

`review_reasons`列（`|`区切り）を追加し、`requires_review = 理由が空でない`とした。理由: `fallback` / `keyword_collision`（lintで警告された語でmatch） /
`source_identity_uncertain`（`source_class_purity<0.9`または`unknown`バケット） / `missing_text` / `mixed_reaction` / `cap_hit` /
`low_semantic_coherence` / `representative_conflict`。

| | clusters | requires_review=false | 主な理由（複数該当あり） |
|---|---|---|---|
| US | 29,951 | **11,372（38.0%）** | keyword_collision 8,572 / mixed_reaction 4,856 / fallback 4,695 / source_identity_uncertain 4,060 / missing_text 2,493 / cap_hit 1,127 / representative_conflict 207 |
| Evidence | 9,643 | **2,044（21.2%）** | fallback 5,388 / source_identity_uncertain 1,993 / mixed_reaction 1,426 / keyword_collision 1,231 / cap_hit 798 / missing_text 174 / representative_conflict 157 |

`preliminary_consistency`とは独立（Lowは機械的ヒントで意味的不整合ではない、という説明をPrompt 1 inputのspec.mdに追記済み）。
閾値（`missing_text`=50%、`mixed_reaction`=30%、`source_identity_uncertain`=純度0.9）は初期値で、実データで再調整する前提。
`keyword_collision`がUSで29%と多いのは、lintが`generic_word`/`high_hit_rate`と判定した語（`economy`、`taxes`、`immigration`等）で
matchしたclusterを含むため（要確認のシグナル、誤りの確定ではない）。

### EvidenceのPublic Voice語彙（#20）

- Shellの`source_class_preliminary`には2つの概念が**混在**している: ①ソース種別（`Official` / `Media` / `Corporate_PR` / `Unknown`）と、
  ②Public Voice適格性（`Public_Voice_Candidate`=本文あり / `Fact_Only_Candidate`=本文なし、SNSバケットのみ）。
  Evidenceには中立な`Social`のclassが無く、SNS投稿は常に②のどちらかに入る（Evidence: Public_Voice_Candidate 58,965 / Fact_Only 15,456）。
- Evidence Prompt 1は`source_class_preliminary`を「最終判定ではなく候補集合」と明記し、独自の`actor_category`で判定する設計なので、
  **`analysis_contract=evidence_intelligence_v1`との直接の矛盾は無い**。ただしPrompt側はこれらの語彙に言及しておらず、
  EvidenceのPrompt 1は、意味の違う2種類のclassが混ざったまま入力される。
- 次フェーズの提案: record単位の`source_type`（バケット由来、モード非依存）と`public_voice_eligibility`（Public Voice専用、
  Evidenceでは使わない/空）を別列にする。今回はtaxonomy migrationはしていない。

---

## J. Prompt Contract Updates Needed（Promptファイルは未変更）

### J-1. 新しい列・値を現行Promptが参照しているか

| 追加・変更した項目 | 場所 | Prompt 1 (v4.3 / EI) | Prompt 2 (v4.3 / EI) | 提案 |
|---|---|---|---|---|
| `analysis_contract` / `analysis_mode` | report_config.yaml | 参照なし | 参照なし | EI Prompt 1（49行目）の「`analysis_policy`/`report_title`が`evidence_intelligence`系か確認」を`analysis_contract`確認へ。`analysis_policy`は全モード固定のため、現行の確認は成立しない |
| `keyword_matcher`（version） | report_config.yaml / manifest | 参照なし | 参照なし | 比較時にmatcher versionが同じか確認する注意書き |
| `effective_start_date` / `effective_start_source` | monitoring_scope.csv | 参照なし | **EI Prompt 2は`configured_start_date`を`analysis_start_date`として参照**（2箇所） | EI Prompt 2が`effective_start_date`を読み、`effective_start_source`が`actual_min_date`のSignalでは開始日が揃わない旨を扱う |
| `review_text_status` | 代表CSV・exception CSV | 参照なし | 参照なし | `retweet_only`は本文が無いのではなく「own textが無い」ことをPrompt 1が理解する |
| `source_class_purity` | cluster_labeling_input.csv | 参照なし | 参照なし | 1.0未満はAnalysis Clusterに複数source classが混在。`source_distribution`で判断 |
| `review_reasons` / `requires_review`（値の意味が変更） | cluster_labeling_input.csv | `requires_review`も参照なし。`preliminary_consistency`は参照あり（参考値として扱う記述） | 参照なし | `review_reasons`でレビューの優先理由を示す |
| `matched_context_terms` / `matched_exclude_terms` / `match_method` / `matched_span` | 代表CSV | 参照なし | 参照なし | Narrative割当の根拠として参照可能 |
| `review_reason`の値（細分化） | exception CSV | 参照なし | 参照なし | 影響なし |
| `Fact_Only_Candidate`/`Public_Voice_Candidate` | — | v4.3のみ言及（68〜69行、158行） | 参照なし | EI Prompt 1には言及なし（§J-2の混在を明記する候補） |

### J-2. 影響する値の変化（Promptが読む入力の変化）

- Officialが大幅に減る（US 4,452→265）。旧officialの記事・SNSがMedia/Public_Voice_Candidateに戻る（第1段階）。
- fallback（`-00`）のclusterが増える（US 19%→33%）。Prompt 1が「要再分類」を多く受ける。これは旧substring誤ヒットで
  実際より多くNarrativeに割り当てられていたことの裏返し。
- Driver Top 3が変わる（US 17 Signal中14）。前回比較（continuity）がある場合、旧Runとの差はmatcherの変更によるもの。

---

## K. Remaining Risks

1. **実DBで新Runを作成していない**（Fake DBで本番の保存・export関数を通した）。実DB上の新Run作成・Export・検証は承認後に必要。
   特に社内プロキシ経由の実DBでは、record取得が約210回（US）/約392回（Evidence）の往復になり、時間とタイムアウトの確認が必要。
2. **US master v2は未公開**。DBのmaster assetを更新して新しいDesign Versionを公開しない限り、実Runは旧master（＋新matcher）になり、
   fallbackが約38%になる。
3. **matched→fallbackの613件**（0.35%）は個別に分解しきれていない。
4. **Evidence masterは未修正**。context語形の未明示（`meeting`/`summit`等）、cueが説明文、非ラテン文字言語(19〜25%)に対するキーワード不足がlintで指摘されている。
   Evidenceのfallback率は88.7%と高い（S6はdiscovery modeで設計上高い）。
5. Evidenceの`effective_start_date`はSignalごとに揃わない（S2=9/16、他=9/1）。Run作成時の`period_start`指定、またはmasterの`trend_start_date`で回避できる。
6. 全て小文字のハッシュタグ（`#katiehobbs`）は分割できず、候補者名のcontextを取りこぼす。
7. pure RT（US非重複の約45%）が`Fact_Only_Candidate`。Prompt 1のPublic Voice分母に効く。Product decisionが必要。
8. `keyword_collision`のレビュー理由はlintの警告に依存し、USでは29%のclusterに付く。語の見直しが進むと減る。
9. coherenceの既定backendは実質的にLowに到達しない。v2は閾値較正前。
10. 旧Runの`geo_report_records`に残った旧実行の行（Evidence a1cb0baf等）は、削除・`execution_id`追加の判断待ち。
11. Preprocessの実行時間: USでmatcher導入後は約2倍（同時実行3本の状態での値。単独での計測はしていない）。
12. 過去run（9/16）の非重複数の1件差は未解決（rawなし）。

---

## L. Go / No-Go

### US Midterm → Prompt 1: **GO WITH WARNING**

根拠（満たしたAcceptance Criteria）: 全テスト通過 / ZIP整合 / record map全件(209,976) / stale 0 / missing 0 / manifest件数一致 /
substring false positive解消（独立検証で違反0） / `ICE` false positive ≈ 0、`govern→Governor` = 0、`rent`/`lose`/`beat` = 0 /
master lint実施・出力 / Narrative分布のbefore/after説明可能 / Driver Top 3の変化を比較可能 /
第1段階のConfirmed Bug（official・WordPress・AC先頭値・split cross-signal）の再発なし。

警告（Prompt 1へ進む前に必要）:
1. 実DBで新Runを作成する（承認後）。実DBの結果が本報告のFake DBでの結果と一致することを`stale/missing`と検証結果で確認する。
2. **US master v2をDBに公開する**（未公開のままでは旧masterで動く）。
3. fallbackが33%になること、Driver Top 3が変わることを、Prompt 1の担当が理解していること。
4. pure RTの扱い（Product decision）が未決定であること。

### Evidence Intelligence → Prompt 1: **GO WITH WARNING**

根拠: 完全な新Run / 旧run混入 0 / Prompt 2 record map全件(391,909) / stale 0 / missing 0 / `analysis_contract=evidence_intelligence_v1` /
discovery input（S6）のContract充足 / 検証error 0 / 語境界の独立検証で違反0。

警告（Prompt 1へ進む前に必要）:
1. 実DBで**新しいRun**を作成する。旧Run（a1cb0baf）は旧実行の行が混ざっているため再利用しない。
2. fallback率88.7%（S6は99%）。Evidence masterは今回未修正で、matcher変更ではほぼ改善していない。これを前提にPrompt 1の結果を読む
   （masterの見直しをすれば改善する見込み。lintの指摘を参照）。
3. `configured_start_date`は空で、`effective_start_date`は実データの最古日由来（S2のみ9/16）。**Prompt 2に進む前**に、
   Run作成時の`period_start`指定、またはPrompt 2 EIが`effective_start_date`を読む更新が必要（Prompt 1には影響しない）。

### NO-GOにならなかった理由と、GOにするための具体条件

どちらも`NO-GO`ではない（Preprocess成果物自体は検証済みで正しい）。`GO WITH WARNING`を`GO`にする条件:
- US: 実DBの新Runで検証error 0・stale 0・missing 0を確認、US master v2を公開、613件の個別分解、pure RTのProduct decision。
- Evidence: 実DBの新Runで同上、Evidence masterの語形・cue・多言語キーワードの見直し（または現状のfallback率を受容する合意）、開始日の運用決定。

---

## 付録: 変更ファイル一覧と追加テスト

| 区分 | ファイル | 内容 |
|---|---|---|
| A | `geo_report_keyword_matcher.py` | keyword_matcher_v2 |
| M | `geo_report_current_pipeline_service.py`（+185/-26） | match_master・get_cluster_cueの置換、自己発火禁止、監査列、lint組み込み、review_reasons、coherence backend配線、manifest/yamlのmatcher version |
| M | `geo_report_master_lint_service.py`（+148/-12） | 語彙ベース検出、CSV出力、acronym対応、同じ行の語形、`*`、generic語拡張 |
| M | `geo_report_cluster_semantic_service.py`（+95/-7） | `lexical_v2` backend（既定は変更なし） |
| M | `geo_report_prompt2_package_validation_service.py`（+61/-0） | record集合比較、Prompt 1件数検証、matcher version警告 |
| M | `geo_report_prompt_export_service.py`（+52/-8） | canonical executionのartifact、集合比較、Prompt 1 input検証 |
| M | `geo_report_current_pipeline_persistence_service.py`（+12/-2） | `record_id_manifest.csv` / `narrative_master_lint.csv` artifact |
| A | docs: `signal_narrative_master_us_v2.csv`、`narrative_master_changes_us.csv`、`build_us_master_v2.py`、`us_*_before_after.csv`、`ev_*_before_after.csv`、`PROPOSED_execution_id_migration.sql`（未実行）、`qa/*` | 成果物 |
| A | `tests/test_geo_report_keyword_matcher.py`（68）、`..._run_isolation.py`（8）、`..._semantic_coherence_fixtures.py`（10）、`..._review_reasons_and_lint_integration.py`（11） | 新規テスト |
| M | `tests/test_geo_report_master_lint_service.py`、`..._prompt2_package_validation_service.py`、`..._current_pipeline_service.py`、`..._persistence_service.py` | 仕様変更に合わせた更新と追加（列数43、artifact数12等） |

テスト総数: 2,253 → **2,366**（全て通過）。DB schemaの変更なし。追加列は全てCSVの末尾（列名で読む既存consumerと互換）。

---

## 追記（2026-10-04 夜）: 実DBでの新Run作成と実行の結果

承認を受けて、実Supabaseに対して以下を実行した。**削除は一切していない（追加と上書きのみ）。Prompt 1 / Prompt 2 は実行していない。**

| 項目 | US Midterm | Evidence Intelligence |
|---|---|---|
| プロジェクト | US中間選挙1（`USsel1`） | 習近平訪米3（`xius3`） |
| 実施した書き込み | master v2をdraft保存→**Design公開（version 2）**→Run作成・固定 | 公開済みversion 2のままRun作成・固定（旧Run a1cb0bafは未変更） |
| 新Run | `73aa5253-f727-4b57-b0dc-25c4ae06aabc`（master asset `86d658dc`=v2） | `c4c1af44-511e-400c-8519-056c92be59c9` |
| Preprocess（実DB） | **completed**（execution `79dddbdb`、1つだけ） | **completed**（execution `9fc627ed`、1つだけ） |
| 総 / 非重複 | 209,976 / 174,655 | 391,909 / 253,157 |
| Analysis / Sentiment cluster（DB） | 29,951 / 34,679 | 9,643 / 12,836 |
| Prompt 1 input ZIP（実DBから生成） | OK（37.5MB） | OK（43.5MB） |
| 実DB件数検証（stale/missing） | **stale 0**、records行数=manifest、非重複=manifest、Signal別=manifest | **stale 0**、同左 |
| 契約 | `public_voice_monitor_v4_3`（run作成時のlint警告178件を返却） | `evidence_intelligence_v1`（prompt2_config_warnings なし） |

- 件数検証は`qa/real_db_counts_verification.json`。`Prefer: count=exact`で、総数・非重複数・manifestの`input_files`以外のsource_file（stale）・
  Signal別件数（`record_id_manifest.csv`と照合）・cluster数を確認した。**中断後の再開（チェックポイント再開）でも二重insertは起きていない**
  （件数がmanifestと一致）。Fake DBでの結果（US cluster 29,951/34,679、Evidence 9,643/12,836）とも一致した。
- **Prompt 2 base ZIPの実DBからの生成・検証は未完了**（下記）。FakeではUS/Evidenceとも全件・error 0を確認済み。

### 実DBで見つかった問題: record取得が遅い（要・索引の追加）

- **現象**: Prompt 2 baseは`geo_report_records`を`run_id`で絞り、`id`でキーセットページングして全件取得する。実DBでは**1ページ（1000件）が平均約20〜40秒**かかり、
  一部は`statement timeout`（HTTP 500、code 57014）で失敗した。US（約210ページ）で80分超、Evidence（約392ページ）はその倍以上の見込み。
  実際に、USを並列取得で約1時間走らせても終わらなかったため、本番DBへの負荷を止めるために中断した。
- **原因**: `geo_report_records`に`(run_id, id)`の索引が無い。範囲を絞っても1ページの時間が変わらないため、DB側が毎回runの行全体を読んでいると考えられる。
- **コード側の対応（実施済み）**: 必要な列だけ取得、5xxは待ってから再試行しページを半分に縮小、uuid空間を16分割して並列取得（スキーマ変更なし）。テスト済み。
  ただしDB側のコストは変わらないため、並列度を上げても限界がある。
- **根本対策（未実施・承認とあなたの操作が必要）**: `RECOMMENDED_index_for_record_export.sql`の1行（`create index concurrently ... (run_id, id)`）を
  Supabase SQL Editorで実行する。索引の追加のみでデータは変えない。これで1ページが数十ミリ秒になり、数分で取得できる見込み。
- 索引を追加したら、`real_export_qa.py`（読み取りのみ）で、実DBのPrompt 1 / Prompt 2 baseのZIP生成・自動検証・record集合の厳密比較（stale/missing）を完了できる。

### 更新したGo / No-Go

- **US → Prompt 1: GO WITH WARNING のまま**（実DBで新Run作成・master v2公開・Preprocess完了・件数検証OK・Prompt 1 input生成OKまで達成）。
  残る警告: pure RTのProduct decision、fallback 33%・Driver Top 3の変化の理解、613件の個別分解。
- **Evidence → Prompt 1: GO WITH WARNING のまま**（実DBで新Run・Preprocess完了・件数検証OK・stale 0）。残る警告: Evidence masterの未修正（fallback 88.7%）。
- **Prompt 2に進む前の必須条件**: 上記の索引を追加し、実DBでPrompt 2 baseの生成・検証（stale/missing 0）を完了すること。Evidenceはさらに開始日の運用
  （`effective_start_date`をPrompt 2 EIが読む／Run作成時の`period_start`指定）を決めること。
