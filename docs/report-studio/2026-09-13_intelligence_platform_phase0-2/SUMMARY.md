# Report Studio → Intelligence Analysis Platform: Phase 0-2 実施報告

日付: 2026-09-13
対象Run: `d558df7e-ad9d-4805-8836-a75771b11441`（Arizona Governor他9レース、Prompt1
canonical execution `6e27804c`、AC 1,477 / SC 1,773 / TS 508、完全処理済み）
詳細設計: `docs/report-studio/INTELLIGENCE_PLATFORM_PHASE0_2_PLAN.md`

Production Pipeline（`geo_report_prompt1_orchestration_service.py`等）は今回一切
変更していない——全て新規ファイル・新規テーブル（7本）の追加のみ。

---

## 1. Current Architecture Audit（要点）

- Report Studioには2系統のClusterテーブルが並存（旧`geo_report_clusters`=Iran/EUDR専用・
  新`geo_report_analysis_clusters`/`geo_report_sentiment_clusters`=Current Pipeline）。
- Embedding基盤はコードベース全体に皆無だった（今回`sentence-transformers`を
  Benchmark専用optional dependencyとして新規導入）。
- Gold/Reference Datasetテーブルは存在しなかった（今回新設）。
- Expert AI Adapter Protocol（`ReportDesignExpertAdapter`/`GeoExpertDesignAdapter`）
  は既存・再利用可能。
- Representative選定は決定的reservoir sampling（`Add-Representative`と同一）。
- `geo_report_records`には本文列が無く、Babel Street Raw CSVも保存されていない
  （Section 5でも詳述）。

## 2. Keep / Change / Replace

Keep: Prompt1 Strong LLM Engine・Prompt2 deterministic aggregation・Execution Model・
Narrative Master契約・Prompt1/2 file contract。Change: 新規7テーブル（Additive only）。
Replace: 無し（Evidence/Fact/Claim/Insight CanonicalはPhase 5-6スコープ、今回作らない）。

## 3. Reference Dataset

`geo_report_reference_examples`へ`build_reference_examples_from_execution()`で構築。

| task | 件数 | 単位 |
|---|---|---|
| ac_classification | 1,477 | Analysis Cluster数 |
| sc_classification | 1,773 | Sentiment Cluster数 |
| targeted_stance | 883 | **target行数**（`item_key`=target_type:target_key単位、1クラスタ複数target可） |

**単位に関する重要な注記**: `targeted_stance`の883は「TS対象クラスタ数」ではなく
「target単位のstance行数」である。前回報告した**TS eligible clusters = 508**
（Prompt1本番実行で新フィルタにより対象となったSentiment Cluster数）とは異なる
母数を指す——508クラスタに対し、1クラスタが複数target（候補者・政党等）を持ちうる
ため、target行数としては883件になる（508クラスタ→883 target行、平均約1.74
target/クラスタ）。今後Reference Datasetのtargeted_stance件数を見る際は、
**クラスタ数（508）とtarget行数（883）を混同しない**こと。

すべて`source='current_prompt1_strong_llm'`, `reference_quality='reference_only'`,
`human_reviewed=false`（正直な現状表示、人手レビュー未実施）。immutable設計——
split割当は別テーブル(`geo_report_benchmark_split_assignments`)で管理。

**既知の制約**: Strong LLM分類済みRunはこの1件のみ。Run跨ぎのReference Dataset・
真のRun-level splitは今回構築不可能。

## 4. Feature Dataset

`geo_report_cluster_feature_service.py`でv1（no-embeddings）を計算。AC 1,477件・
SC 1,773件。実測で興味深い構造的差異を確認: **`source_entropy`はACで152/1,477件
（10.3%）が非ゼロだったのに対し、SCは1,773件中0件が非ゼロ**——SCのクラスタリング
キー自体に`source_class_preliminary`が含まれるため、クラスタ内は構造的に単一
sourceになる（バグではなく設計上の帰結）。`narrative_priority_score`はRouter専用
特徴として分離し、Semantic Census分類器の入力には使っていない。

## 5. Representative Selection Benchmark（未実施、既知のギャップ）

Section Fの前提（クラスタ所属の全recordをrecord-level embeddingし、Typical/
Diverse/Boundary/Exceptionを選び直す）は、`geo_report_records`に本文列が無いため
当初実行不可能と判明した。ユーザー提供のBabel Street Raw CSV
（`C:\Users\269811\Desktop\BScontinuous\dropbox\`配下、9 signal分）を使い
`geo_report_benchmark_raw_text_service.py`で本文抽出する経路は実装・単体テスト
済みだが、**時間制約によりSection F本体（historical/current-rerun/new-rerunの
3-way比較、不一致サブセットレビュー）は今回未実施**。Phase 3着手前の宿題として
残す。

## 6. Semantic Census候補モデル（実装済み、5方式）

`geo_report_semantic_census_service.py`にfield単位classifierとして実装:

- **A. Small LLM**: `gpt-4o-mini`（`Global-AzureOpenAI-DEV-gpt-4o-mini`）、
  minimal schema（自由記述フィールドを含まない、field毎confidence付き）。
- **B. Embedding NN** / **C. Logistic Regression** / **D. Gradient Boosting**:
  representative centroid embedding（384次元、`paraphrase-multilingual-
  MiniLM-L12-v2`）を入力とするfield毎classifier。
- **E. Hybrid**: centroid embedding + 決定的数値特徴6個（`fallback_share`/
  `source_entropy`/`language_entropy`/`preliminary_consistency_score`/
  `complexity_score`/`representative_diversity_score`）をconcatenateしたLogistic
  Regression。

`narrative_priority_score`はいかなる分類器の入力にも使っていない（Router専用）。

## 7. Model Benchmark（field-wise reference agreement + cluster exact match、実データ実測）

sc_classification task、Test split（用語注記: 独立ground truthではないため
"accuracy"ではなく"reference agreement"と呼ぶ）:

| split_scheme | method | 所要時間 | cluster exact match | theme_relevance | source_class | reaction_type | attitude | cluster_consistency | apply_to_cluster |
|---|---|---|---|---|---|---|---|---|---|
| temporal | small_llm(gpt-4o-mini) | 555s | 0.069 | 0.564 | 0.711 | 0.564 | 0.505 | 0.739 | 0.881 |
| temporal | embedding_nn | 73s | 0.256 | 0.709 | 0.674 | 0.591 | 0.641 | 0.706 | 0.906 |
| temporal | logistic_regression | 66s | 0.288 | 0.741 | 0.756 | 0.668 | 0.715 | 0.768 | 0.906 |
| temporal | gradient_boosting | **962s** | 0.271 | 0.741 | 0.759 | 0.647 | 0.718 | 0.806 | 0.903 |
| temporal | hybrid | 64s | 0.282 | 0.732 | 0.759 | 0.665 | 0.709 | 0.768 | 0.906 |
| narrative_holdout | small_llm(gpt-4o-mini) | 438s | 0.077 | 0.627 | 0.662 | 0.627 | 0.641 | 0.739 | 0.782 |
| narrative_holdout | embedding_nn | 43s | 0.264 | 0.723 | 0.665 | 0.698 | 0.649 | 0.744 | 0.880 |
| narrative_holdout | logistic_regression | 40s | 0.347 | 0.802 | 0.760 | 0.715 | 0.760 | 0.740 | 0.880 |
| narrative_holdout | gradient_boosting | **443s** | 0.347 | 0.798 | 0.744 | 0.756 | 0.760 | 0.814 | 0.880 |
| narrative_holdout | hybrid | 41s | 0.351 | 0.798 | 0.764 | 0.736 | 0.773 | 0.748 | 0.880 |

**重要な所見**: Gradient Boostingは他手法の**10〜20倍以上の計算時間**を要しながら
（GB内蔵無しの単純なsklearn実装のため）、cluster exact matchで他手法を明確に
上回ることはなかった（narrative_holdoutでlogistic_regression/hybridと同着）。
**Logistic Regression/Hybridが速度・精度のバランスで最良**。

**用語上の注意（重要）**: Small LLMのTest set母集団は他methodと異なる（LLM呼び出しの
一部が失敗し除外されたため——後述Section 13 Risks参照）。したがって
small_llm行と他method行のcluster exact match/field agreementは**厳密な同一条件比較
ではない**。

## 8. Critical-field Metrics

Critical fields（`source_class`のPublic Voice/Fact Only区別・`theme_relevance`・
`attitude`・`apply_to_cluster`）は上表に含まれる。`apply_to_cluster`は全method・
全split_schemeで0.78〜0.91と最も高い一致率——Public Voice/Fact Only識別
（`source_class`）は0.66〜0.76で、Current Prompt1が最も慎重に判定するcritical
fieldの1つだけに、Lightweight方式による代替の難度もそれだけ高いことを示す。

## 9. Novelty-proxy Performance

`novelty_proxy_score`/`nearest_neighbor_distance`はEmbedding系4method（B〜E）に
ついて、Validation上の95パーセンタイルをnovelty gate閾値として計算・保存した
（実測: temporal=0.05、narrative_holdout=0.051、Section 13で詳述する既知の限界
あり）。**Small LLM(A)についてはこの実行で計算していない**——method非依存の量として
共有する設計（Plan Section E）だったが、実装が間に合わなかった既知のギャップ
（Small LLMのmandatory_rateは常に0）。

## 10. Calibration

field毎にIsotonic Regressionでraw confidence→calibrated confidenceのmapping
をValidation splitでfitし、Test/Router SimulationはTest側raw confidenceへ
適用したcalibrated confidenceのみを使用（生のraw_confidenceは意思決定に使わない）。

## 11. Router Simulation（Expected Reference Agreement、target escalation rate方式）

`geo_report_router_simulation_service.py`でmandatory escalation（novelty gate/
importance）適用後、残り母集団に`(t-m)/(1-m)`でconfidence cutoffを逆算する方式で
実行（`router_simulation_results.json`に全100行を保存）。target=0.3の代表値:

| split | method | importance | 実escalation率 | Strong LLM呼出 | 軽量method呼出 |
|---|---|---|---|---|---|
| temporal | logistic_regression | なし | 21.5% | 73 | 267 |
| temporal | logistic_regression | あり | 80.9% | 275 | 65 |
| narrative_holdout | logistic_regression | なし | 26.4% | 64 | 178 |
| narrative_holdout | logistic_regression | あり | 21.9% | 53 | 189 |

**重要な所見（importance routingの限界）**: `narrative_priority_score`の分布は
{0.2, 0.7, 1.0}の3値のみで、**このNarrative Masterでは約82%のクラスタが最高値
1.0（High priority＋primary trend_role）に該当**していた。そのためimportance
routing（閾値1.0）を有効にすると、temporal splitではmandatory_rateが82.3%に
跳ね上がり、confidence起因のescalation削減余地をほぼ消してしまう
（narrative_holdoutでは逆に、mandatory母集団の構成が異なりむしろ削減方向に働いた）。
**「重要度は分類結果を変えないがRouterでは使う」という設計は正しいが、この
Narrative Masterの`priority`列の付け方自体が粗すぎて実質的な選別力を持たない**
——Phase 3でNarrative Master設計時にpriorityをより選択的に付与することを推奨する。

**Oracle/Upper-bound注記**: 上表のescalated分は「Strong LLM=Reference相当
（100%一致）」という理論上限の仮定に基づく。Section F（Representative Benchmark）
が未実施のため、historical-vs-rerun実測一致率に基づくRealistic Expected Reference
Agreementは今回算出できていない——Phase 3でSection Fを完了させた後に追加する。

## 12. 推定Strong LLM削減率・token削減率・時間削減率

temporal/logistic_regression（importanceなし）で見ると、target=30%の設定で
実際のescalation率は21.5%——**残り78.5%のクラスタはLogistic Regression（384次元
embedding、CPU推論、API呼び出し不要）だけで済ませられる**という結果になった。
Current Prompt1のSC stage実測（92 LLM calls、input 1,333,634 / output 176,024
tokens、Section 12参照）に単純比例させると、理論上はLLM呼び出し・token消費とも
おおよそ8割前後の削減余地があることになる。**ただしこれは(a) Oracle仮定
(Section 11)、(b) Small LLM側の呼び出し失敗（Section 13）、(c) Representative
Benchmark未実施、という留保付きの試算であり、Phase 3で実際にStrong LLM
escalationを組み込んだEnd-to-Endの実行で検証する必要がある。**

## 13. Risks（既知の限界、正直に記録する）

- Reference DatasetはRun 1件のみに基づく——真のRun-level split未実施
  （Temporal/Narrative Holdout splitで部分的に補完）。
- Reference Label自体がStrong LLM出力であり独立ground truthではない
  （"Accuracy"ではなく"Expected Reference Agreement"と呼ぶ）。
- **Small LLM(gpt-4o-mini)呼び出しの一部が失敗している**（temporal: 710件中376件
  =53%成功、narrative_holdout: 597件中437件=73%成功）。`_classify_clusters_with_
  small_llm`が例外を握りつぶして継続する設計（Benchmark全体を止めないための安全策）
  になっているため、失敗の根本原因（content_filter/schema不整合/レート制限等）を
  未調査のまま。Phase 3では原因究明が必要。
- Section F（Representative Selection Benchmark）は本Runでは未実施
  （Section 5参照）。
- Novelty proxyはEmbedding系4methodのみに実装、Small LLMには未実装
  （Section 9参照、mandatory_rateが常に0になる既知のギャップ）。
- importance routingの閾値選択がこのNarrative Masterのpriority分布（実質2値化）
  に対して粗すぎる（Section 11参照）。
- Router SimulationのExpected Reference AgreementはOracle/Upper-bound前提
  （Section 11参照、Realistic版は未算出）。
- Gradient Boostingは計算コストに見合う精度向上が確認できなかった
  （Section 7参照）——Production候補から除外する方向を推奨。
- `sentence-transformers`/`torch`はBenchmark専用（`requirements-benchmark.txt`）
  としてProduction主要依存から隔離済み。
- Confidence Calibration自体も1 Runのみのデータで学習しており、汎化性は未検証。

## 14. 推奨Architecture（暫定）

- Small LLM単体よりも**Embedding系（特にLogistic Regression/Hybrid）の方が
  速度・精度ともに優れる**——384次元embeddingとField-wise Logistic Regressionの
  組み合わせが、現時点で最もコスト効率の良いSemantic Census候補。
- Gradient Boostingは計算コストに見合わないためProduction候補から除外。
- Small LLMは呼び出し失敗率が高く、単体では信頼性に課題——原因究明後、
  Embedding系とのHybrid Router（confidenceが低いものだけSmall LLMへ、さらに
  低いものだけStrong LLMへ、という多段Router）を検討する価値がある。
- importance routingを機能させるには、Narrative Masterの`priority`列をより
  選択的に付与し直す必要がある（現状は最高値に偏りすぎている）。
- GENERATIVE_FIELDS（`classification_reason`等）の生成方式が未解決な間は、
  Lightweight結果をCanonical Prompt1 Outputへ直接採用しない（Section 6/Plan
  Section H参照）。

## 15. Phase 3実装計画（提案）

1. Small LLM呼び出し失敗の根本原因調査・修正。
2. Section F（Representative Selection Benchmark）を実データで完了させる。
3. Novelty proxyをSmall LLMにも拡張し、Realistic Expected Reference Agreementを
   算出する。
4. Narrative Masterの`priority`列をより選択的に付け直し、importance routingの
   実効性を再検証する。
5. GENERATIVE_FIELDS（自由記述）のLightweight生成方式を設計・検証する。
6. AC task・Targeted Stance taskについても同様のBenchmarkを実施する（今回は
   sc_classificationのみ）。
7. 複数Runでのcross-run Gold/Reference蓄積を開始し、真のRun-level split・OOD
   検証の土台を作る。
8. 上記が完了した段階で、Shadow Mode（Current Prompt1をCanonicalにしたまま
   Semantic Census/Routerを並行実行）でのA/Bを開始する。

## 16. Go / No-Go（Phase 2.5完了により更新、Section 17以降参照）

Phase 2時点の暫定判定は「Go（条件付き）」だったが、Phase 2.5（Gate 1-3）の実施
により状況が更新された。**最終判定はSection 24を正とする。**

---

# Phase 2.5: Production Readiness Gate（2026-09-13実施）

Phase 3（Shadow Router実装）着手前に、Phase 2の"Go（条件付き）"判定が抱えていた
3つの未検証事項——① Small LLM失敗率の原因、② Logistic Regression/Hybridの優秀さが
「たまたま現在のreservoir代表投稿で十分」なことと交絡していないか、③ Oracle前提
（escalated分=Reference 100%一致）を現実的な値に置き換えられるか——をGate 1〜3として
検証した。実データ・実Strong LLM呼び出しで検証済み。

## 17. Gate 3: Small LLM Failure Triage

### 17.1 前提の訂正（重要、混同禁止）

初回rerun（`ai_client.py`のhttpx.Clientにtimeout未設定のまま実行）は3時間超stallした。
**一次原因はユーザー環境の一時的な外部ネットワーク断であり、Small LLM/Azure OpenAI/
Semantic Census自体の障害ではない。** timeout未設定は「ネットワーク断が起きた際に
無期限待機し、既存retry/backoffへ進めなかった」という増幅要因（fail-safeの欠如）に
過ぎない。修正（`ai_client.make_openai_client`のhttpx.Clientへ`read timeout=180s`/
`connect timeout=30s`を追加）はこの増幅要因への対策として維持するが、「Small LLMの
問題を直した」という扱いはしない。ネットワーク復旧後の安定した通信条件下でのrerunを
Gate 3の正式Benchmarkとして扱う。

### 17.2 障害taxonomy修正（"unknown"は実在するカテゴリではなかった）

初回の安定rerunで、失敗の大半が"unknown"に分類された。原因を調査した結果、
`geo_llm_common.call_llm_structured`が最終的に送出しうる例外は
`openai.RateLimitError`（内部backoff 5回を使い切った後）と`GeoLLMError`
（構造化出力→手動JSON抽出→訂正再試行の3段階すべてでSchema検証失敗）の2種類のみで
あり、instrumentation側が`GeoLLMError`の分類漏れを起こしていただけだった
（`_categorize_llm_exception`にGeoLLMError判定を追加して修正、コードパス上
排他的であることを確認済み）。修正後、SCを再計測したところ"unknown"は0件になり、
全件が`pipeline_schema_validation_failure`（Category C）または`api_other`
（Category B）へ正しく再分類された。

### 17.3 AC Semantic Census Field Registryの訂正（重要な設計上の発見）

AC分（初回、7-field）で`source_assessment`/`tone_gradient`/`voice_direction`/
`report_use_status`/`continuity_relevance`のfield agreementが0.0近辺に張り付いた。
原因調査の結果、これは**Small LLMの障害ではなく、そもそもこの5 fieldが
`ANALYSIS_CLUSTER_CLASSIFICATION_SCHEMA`上でもenum制約の無い自由記述field**
であることが判明した（`representative_quality`のみ`enum: ["High","Medium","Low"]`
を持つ）。実データでは、Historical Reference（本番Strong LLM出力）はこれらを
日本語自然文（例:「該当なし」「強い批判」）で出力する一方、Small LLMは英語の短い
ラベル（"Unclear"/"Not Used"等）で応答しており、exact-string-matchでの
Reference Agreementは構造的に無意味だった（当初、値がmojibakeに見えたため
Reference Dataset自体のencoding破損を疑ったが、生UTF-8バイト列を直接decodeして
正しい日本語であることを確認済み——データ破損ではなくlanguage/format mismatch）。

**Field Registry訂正（`geo_report_semantic_census_service.py`）**:
```
AC正式Categorical Census対象（enumを持つ2 field）:
  SEMANTIC_CENSUS_FIELDS_AC = ["noise_flag", "representative_quality"]

AC自由記述field（GENERATIVE_FIELDS_ACへ移動、classification benchmark対象外）:
  source_assessment, tone_gradient, voice_direction,
  report_use_status, continuity_relevance
```
この5 fieldのexact-string agreementは**embedding_nn/logistic_regression/
gradient_boosting/hybridのどのmethodでも同様に無意味**になるため（class labelとして
学習しても過去の表現をそのまま暗記するだけ）、AC embedding系benchmarkが未実施の
段階でこの問題を発見できたことは、無駄な計算を避けられたという意味で価値がある。
SC_CENSUS_FIELDS（既存の6 field、いずれも実enumを持つ）は変更なし。

### 17.4 修正後の正式計測結果（安定通信・修正済みtaxonomy）

| task | split_scheme | 成功率 | transport | api | pipeline(schema) | cluster exact match | 備考 |
|---|---|---|---|---|---|---|---|
| sc_classification | temporal | 517/678 (76.3%) | 0 | 6 | 39 | 0.096→0.115(下記2回参照) | 6 field(全てenum) |
| sc_classification | narrative_holdout | 517/597 (86.6%) | 0 | 0 | 18 | 0.115 | 同上 |
| ac_classification | temporal | 570/570 (100%) | 0 | 0 | 0 | 0.451 | 2 field(noise_flag/representative_quality)のみ |
| ac_classification | narrative_holdout | 513/533 (96.2%) | 0 | 3 | 0 | 0.483 | 同上 |

- **Transport障害は0件**（両task・両split_scheme）——ネットワーク安定時はtimeout/
  接続断が発生しないことを確認。fail-safe自体が誤動作を起こしてもいない。
- **SC特有の`pipeline_schema_validation_failure`**（call毎の約35-40%が該当）は、
  AC（2 field、0件）とのコントラストから、SCの6 field・複数enum・per-item
  confidenceという構造の複雑さがgpt-4o-miniにとって構造化出力を安定して満たす上で
  一定の負荷になっていることを示唆する。ただし全batch単位の障害であり
  （成功後のitem単位missing/invalidはほぼゼロ——Gate 3当初のitem-level retry修正は
  正しく機能している）、同一batchを再送しても同じ内容では改善しにくい
  （Prompt1本番の`_bisect_content_filtered`に類する二分探索が無い）という設計上の
  限界がある。今回は追加実装しない（Section 17.5の判断を優先）。
- AC（修正後2 field）はnoise_flag 0.65-0.72、representative_quality 0.55-0.61と、
  SC並みの妥当なcategorical agreementを示した——Small LLM自体が全面的に信頼できない
  わけではなく、**enum制約のあるfieldでは相応に機能する**ことを示す。

### 17.5 Gate 3判定: Transport incident resolved / mitigated、Small LLMはPrimary候補から除外

- ネットワーク断インシデントは解消済み（fail-safe実装済み、今後同種の無期限
  stallは発生しない）。
- SC schema validation failure（約35-40%/call）は実在する、Small LLM固有の
  中程度の弱点として記録する（Production stability riskとして今後も監視対象）。
- しかしPhase 2で既に確認済みの通り、Logistic Regression/Hybridはcluster exact
  matchでSmall LLMの約3倍（0.28-0.35 vs 0.10-0.12）、速度で約15-20倍
  （64-66秒 vs 942-1023秒）、API呼び出し数でほぼ0 vs 数十回、という圧倒的な差が
  ある。**Small LLMを無理にPrimary経路へ採用する必要はない**——Logistic
  Regression/Hybridを引き続きPrimary候補とし、Small LLMは将来の3段階cascade
  （Logistic→Small LLM→Strong LLM）における中間tier候補として保留する
  （今回は評価しない）。

## 18. Gate 1: Representative Selection Benchmark

`geo_report_representative_benchmark_service.py`（新規）で実装。sc_classification、
temporal split・test split内から決定的サンプリングした150クラスタについて、
同一Strong LLM（`Global-AzureOpenAI-DEV-gpt-5.1`、Historical Referenceを生成した
execution `6e27804c`と同一deployment）で3-way比較を実施した。

### 18.1 3-way比較結果

| pair | exact match | field agreement範囲 | 意味 |
|---|---|---|---|
| A. Historical vs B. Current-Reservoir Rerun | 49.3% | 80.0-90.0% | **Reproducibility floor**——同一代表投稿・同一Strong LLMでも再実行結果は約半分しか完全一致しない |
| A. Historical vs C. New-Representative Rerun | 25.3% | 61.3-76.7% | 代表選定変更＋rerun noiseの複合効果 |
| B. Reservoir Rerun vs C. New-Representative Rerun | 27.3% | 60.0-81.3% | 代表選定方式単体の差（ただしSection 18.2の交絡あり） |

**最重要の所見**: A vs Bの49.3%が示す通り、Strong LLM自体が同一入力に対しても
約半分のクラスタで異なる結論を出す（`temperature=0.2`でもゼロではない）。これは
Gate 2のOracle前提（escalated分=Reference 100%一致）が理論上限に過ぎないことを
実測で裏付ける。

### 18.2 既知の交絡（正直に記録、B vs C数値の解釈に必須）

Representative件数・トークン量比較（`representative_stats_by_strategy`）:

| strategy | 平均representative件数 | 平均文字数 | 平均推定token数 |
|---|---|---|---|
| current_reservoir | 2.41 | 546 | 137 |
| typical_diverse_boundary_v1 | 2.08 | 7,872 | 1,968 |

新代表選定戦略は、本番の`text_for_review`（Preprocess時にキュレーションされた
短い抜粋）ではなく、Babel Street Raw CSVの生本文をそのまま使用しているため、
**約14倍多い生テキスト**をStrong LLMへ渡している。したがって"B vs C"の27.3%
exact matchは「どの投稿を選んだか」と「どれだけ生の/未加工のテキストを渡したか」
の効果が交絡しており、代表選定方式単体の効果として純粋に解釈できない。

### 18.3 不一致25件のscreening/provisional review（Claudeによる、analyst-verified ではない）

`build_review_bundle_for_disagreements`で全record本文込みのレビュー用データを
生成し、B vs C不一致109件から決定的に25件を抽出してレビューした。

| 判定 | 件数 |
|---|---|
| New representativeの方が妥当 | 10 |
| Current reservoirの方が妥当 | 8 |
| 判断不能/純粋なrerun noise（同一テキストでも結果が違う等） | 7 |

**2つの具体的で重要な発見**（代表選定方式の一般論ではなく、個別に確認された事実）:

1. **Current Reservoirのrepresentative選定に実在する欠陥を発見**: 25件中3件
   （`SC_ae94ff9aa8d2c4`/`SC_b796a84d1f6411`/`SC_c44906815e989e`）で、reservoir
   sampling選定のrepresentative（最大10件）が**全件空文字列**だった——にも
   関わらず、クラスタの全record pool（それぞれ50/84/22件）には実際に分類可能な
   内容を持つrecordが存在した（New representative rerunがそれを発見し、historical
   の"uncertain/Low consistency"より遥かに妥当な分類を出した）。これは
   Representative戦略の優劣という一般論ではなく、**現行Production Preprocessの
   reservoir sampling/`text_for_review`生成過程に実在する不具合**である可能性が
   高く、本Gate 1のスコープ外の別課題として記録する（Current Cluster Batch Engine
   側の追加調査候補）。
2. **New representative戦略自身の弱点も確認**: `SC_b6f8822f95e49d`で、生テキストを
   無制限に使う設計が仇となり、77,885文字の学術論文全文が単一representativeとして
   採用され、本来"relevant"だったクラスタが"irrelevant"に誤分類された。本番採用を
   検討する場合、representative文字数に妥当な上限（本Gate 1実装の無制限でも本番の
   500文字切り詰めでもなく、例えば1,000-2,000文字程度）を設ける設計が必要。

### 18.4 Gate 1判定

Representative戦略の比較は「新戦略が優れている」という単純な結論を支持しない
（交絡・実装課題があるため）。ただし、**現行Production代表選定に実在の不具合
（全representative空文字列）を発見できたこと自体がGate 1の価値**であり、
Phase 3のSC-only Shadow Router候補（Section 20）はこの発見に影響されない
（Current reservoirのまま進める設計のため）。Representative戦略自体の改善は
Phase 3B以降の課題として保留する。

## 19. Gate 2: Realistic Router Simulation（Logistic Regression / Hybridのみ）

実際のToken-aware batch packing（`_pack_token_aware_batches`/
`_estimate_representative_weight_by_cluster`、Prompt1本番と同一ロジック）で
Strong LLM側の実バッチ数を見積もり、target escalation rate 20/30/40/50/70%で
評価した（importance routing無効、Confidence+Noveltyのみで検証、というユーザー
指示に従う）。

### 19.1 SC-only削減率（実バッチ数ベース、本番と同一batch_size=20/max_tokens=12000）

| method | target rate | 実escalation率 | Test内Strong batch数 | 母集団投影Strong batch数 | SC単独削減率(baseline 89 batch) |
|---|---|---|---|---|---|
| logistic_regression | 20% | 18.2% | 4 | 21 | 76.4% |
| logistic_regression | 30/40%* | 21.5% | 4 | 21 | 76.4% |
| logistic_regression | 50% | 46.5% | 8 | 42 | 52.8% |
| logistic_regression | 70% | 67.9% | 12 | 63 | 29.2% |
| hybrid | 20% | 18.2% | 4 | 21 | 76.4% |
| hybrid | 30% | 26.8% | 5 | 26 | 70.8% |
| hybrid | 40% | 35.3% | 6 | 31 | 65.2% |
| hybrid | 50% | 42.9% | 8 | 42 | 52.8% |
| hybrid | 70% | 68.8% | 12 | 63 | 29.2% |

*30%と40%で実escalation率が同一になったのは、Validation側confidence分布の
分位点解決（`solve_confidence_cutoff_for_target_rate`）が離散データ点に丸められる
ための量子化artifact——不具合ではないが、この2点だけ見た目上差が無いことに注意。

### 19.2 Critical-field Reference Agreement（Oracle vs Realistic）

| method | target rate | Oracle | Realistic | Oracle-Realistic gap |
|---|---|---|---|---|
| logistic_regression | 20% | 80.3% | 71.0% | 9.3pt |
| logistic_regression | 30/40% | 81.1% | 70.2% | 10.9pt |
| logistic_regression | 50% | 87.1% | 63.5% | 23.6pt |
| logistic_regression | 70% | 92.3% | 57.8% | 34.5pt |
| hybrid | 20% | 80.1% | 70.8% | 9.3pt |
| hybrid | 30% | 82.2% | 68.6% | 13.6pt |
| hybrid | 70% | 92.4% | 57.5% | 34.9pt |

**重要な限界（正直に記録、数値をそのまま意思決定に使わない）**: Realistic値は
Gate 1で測定した単一の一様サンプルAgreement（A vs B、49.3%）を、target rateに
関わらず一律にescalated分へ適用している。escalation率を上げるとOracleは
（定義上）100%へ収束するが、Realisticは逆に低下する——これはRealisticの
計算方法が「escalated分（本来はconfidence/noveltyで選ばれた難しいsubset）」に
「一様サンプルの平均reproducibility」を機械的に当てはめているためのartifactで
あり、Strong LLMが難しいケースで実際にどの程度信頼できるかを反映していない
（ユーザー指摘通り、"全cluster平均Agreementを難しいescalated subsetへ無条件適用
しない"べきだったが、今回はGate 1のサンプリングをconfidence/noveltyで層別しな
かったため実施できなかった）。**Realistic欄はdirectionalな参考値に留め、
Escalated subset層別のGate 1再測定を今後の必須タスクとする。**

### 19.3 Scenario A: Total Prompt1 Call削減（AC/TS据え置き、SCのみRouter化）

Current Prompt1 baseline（execution `6e27804c`、`geo_report_execution_log`実測）:

| stage | calls | input tokens | output tokens | cached tokens |
|---|---|---|---|---|
| AC (prompt1_analysis_cluster) | 76 | 1,111,705 | 361,797 | 590,720 |
| SC (prompt1_sentiment_cluster) | 92 | 1,333,634 | 176,024 | 700,800 |
| TS (prompt1_targeted_stance) | 38 | 487,757 | 128,424 | 300,800 |
| **Total** | **206** | | | |

AC(76)/TS(38)を現状のまま維持し、SCのみRouter化した場合のTotal call削減率
（206 baseline基準）:

| method | target rate | SC batch数(母集団投影) | Scenario A total calls (76+SC+38) | Total削減率 |
|---|---|---|---|---|
| logistic_regression | 20% | 21 | 135 | 34.5% |
| logistic_regression | 30/40% | 21 | 135 | 34.5% |
| logistic_regression | 50% | 42 | 156 | 24.3% |
| hybrid | 20% | 21 | 135 | 34.5% |
| hybrid | 30% | 26 | 140 | 32.0% |

**ユーザー指摘の通り、SCを仮に100%削減してもAC(76)+TS(38)=114 callsは残るため、
Total Strong LLM call削減率の理論上限は約(206-114)/206≒44.7%である。したがって
「Total Strong LLM call削減率≥50%」をPhase 3AのGo条件として機械的に課さない
（Section 20で修正した基準を参照）。**

## 20. Phase 3の2段階分割（Scenario A/B）

Gate 1-3の結果を踏まえ、Phase 3を以下の2段階に分けることを推奨する。

### Phase 3A（推奨、次の実装対象）: SC-only Shadow Router（設計確定版、2026-09-13）

```
AC  → Current Strong LLM維持（変更なし）
SC  → Field-wise Logistic Regression（Primary）
       ├ Hybridは同時実行するChallenger（比較記録のみ、Router判断には使わない）
       ├ Gradient Boosting/Small LLMはPhase 3A主経路から除外
       ├ Confidence gate + Novelty gate + Evidence Sufficiency gateのみで
       │  escalation判断（importance routingは無効のまま、Section 20.4参照）
       └ Would-Escalate判定でもProductionでは実際にStrong LLMを省略しない
          （Shadow Modeのため、全SCは引き続きCurrent Strong LLMで分類される。
          Router判断は記録するだけで、89→21 batches等の削減は「Projected Savings」
          であり「Realized Savings」ではない——Section 20.3参照）
TS  → Current Strong LLM維持（変更なし）
```
Production Canonicalは引き続きCurrent Prompt1のまま——Shadow Modeで実Run上で
Logistic Regression予測（Primary）＋Hybrid予測（Challenger）とCurrent Strong LLM
SC分類を並行実行・比較する。**PrimaryをLogistic Regressionに一本化する理由**:
Phase 2実測でHybridと同水準の精度（cluster exact match 0.28-0.35、field agreement
60-91%、いずれもtemporal/narrative_holdout両splitで同程度）でありながら、Logistic
Regressionの方が(a)高速、(b)deterministic、(c)説明しやすい、(d) artifact管理が
容易、(e) inference costがほぼゼロ、という運用上の利点が大きい。HybridはChallenger
として並走させ、Primary(Logistic)との乖離が大きい場合の追加シグナルとして使う。

### 20.1 品質評価は3種類に分けて追跡する（単純な「Strong LLMと一致したか」を品質と
見なさない）

Gate 1で判明した通り、Strong LLM自身の同一入力再現率は49.3%に過ぎない。したがって
Shadow運用中の評価は以下の3軸に分けて追跡する（`Compatibility`のみを品質スコアと
みなすことを禁止する）:

1. **Compatibility**: Shadow Router（Logistic予測＋Router判断）とCurrent Canonical
   Strong LLM分類の一致率。運用上の「今この瞬間、Router結果は現行本番結果と
   どれだけ一致するか」を示す指標。
2. **Stability**: Strong LLM再実行結果と同一クラスタのHistorical Strong LLM結果の
   一致率（Gate 1のA vs Bと同じ枠組み、Runごとに部分サンプルで継続測定）。
   「そもそもCanonical自体がどれだけ安定しているか」の基準線。
3. **Adjudicated Quality**: ShadowとCanonical Strong LLMが不一致だったケースを
   実際のEvidence（record本文）に基づきレビューし、「Logisticが誤り」と単純に
   決めつけず、「Strong自身が不安定なだけ」「両方妥当」等の可能性も含めて判定する
   （Gate 1 Section 18.3のscreening/provisional reviewと同じ手法を継続適用）。

### 20.2 Evidence Sufficiency Gate（Representative空文字列問題へのGuard）

Section 18.3で発見した「reservoir sampling選定のrepresentativeが全件空文字列に
なりうる」不具合は、Representative Selector自体を今すぐProduction変更する必要は
無いが、**Routerがそれを自信満々にLightweight Acceptしてはいけない**。Router
mandatory escalation条件へ以下を追加する:
```
representative_count == 0
  or usable_text_count == 0
  or representative_text_too_sparse（合計文字数が閾値未満）
    → force would_escalate（confidenceに関わらず強制escalate）
```
Novelty gate・Confidence gateと同列の、3つ目のmandatory gateとして扱う。

### 20.3 Shadow Modeは実削減を伴わない（Projected Savings ≠ Realized Savings）

Phase 3A期間中、Productionは引き続き**全SCをCurrent Strong LLMで分類する**。
Shadow Routerの判断（`lightweight_accept`/`would_escalate`）は記録するのみで、
実際のLLM呼び出しを省略しない。したがってGate 2で示した「89→21 batches」等の
数値はPhase 3A期間中は**Projected Savings**（推定節約量）であり、Realized Savings
（実際に削減された呼び出し量）ではない。複数Runのshadow結果で問題ないことを
確認できてから、初めてProduction Routingへ移行し実際にStrong LLM呼び出しを
省略する。

### 20.4 Importance RoutingはPhase 3Aで無効のまま

現行Narrative Masterの`priority`列は82%のクラスタが最高値に張り付いており
（Section 11既出）、Router signalとしてほぼ機能しない。Phase 3Aの最初のShadowは
Confidence + Novelty + Evidence Sufficiencyの3 gateのみに絞り、解釈を単純に保つ。
Importance routingはNarrative Master側の`priority`付与ロジックを再設計してから
別途追加する。

### Phase 3B/4（将来、Architecture試算のみ・Production-ready扱いしない）: AC再設計

```
AC categorical field（noise_flag/representative_quality）
  → Lightweight Census化

AC自由記述field（source_assessment等5 field）
  → 全clusterではなく、Reportに必要な（Lightweight Censusで「使う」と
     判定された）clusterのみStrong LLMで生成
```
AC自由記述fieldをどのように評価・生成するかは未解決のArchitecture課題として残す
（Section 21参照）。この設計が確定するまで、AC Lightweight結果をCanonical Prompt1
Outputへ直接採用しない。

## 21. Phase 3AのGo条件（修正版、機械的な単一閾値ではなく複数指標を個別評価）

Section 19.3の通り、AC/TSを維持する前提でTotal Strong LLM call削減率50%以上を
要求するのは構造的に達成不能なため、Phase 3AのGo判定では以下を個別に確認する
（単一のPass/Fail閾値には統合しない）:

1. **SC Strong LLM workload削減**: 実測。target 20-30%で約70-76%削減
   （母集団投影89→21バッチ）——達成。
2. **Total Prompt1 workload削減**: 実測。target 20-30%で約32-35%削減——
   AC/TS維持という制約下では妥当な水準。
3. **Critical-field品質**: Oracle 80-82%（target 20-30%）、Realistic 68-71%
   （ただしSection 19.2の限界注記あり、層別再測定が必要）。
4. **Representative Benchmark**: 新戦略が既存以上、または有意な劣化なし、という
   単純な結論は出せなかった（交絡あり）。ただし**Phase 3Aの対象はcurrent_reservoir
   のまま**のため、この論点はPhase 3Aの障害にならない。
5. **Novelty gate機能**: 実装済み（`solve_novelty_gate_threshold`、Validation上位
   5パーセンタイルを強制escalate）——目標rateがmandatory_rateを下回らない限り機能。
6. **Small LLM**: Primary候補から除外（Section 17.5）、Phase 3Aの障害にならない
   （SC RouterはLogistic Regression/Hybridのみを使う）。

## 22. 未解決のまま残す論点（次セッションへの申し送り）

1. **Gate 1のconfidence/novelty層別再測定**（Section 19.2の限界を解消するために
   必須）: Routerが実際にescalateするcluster subset（低confidence/高novelty）
   からStrong LLM rerun sampleを取り、その部分集合固有のreproducibility rateを
   測定する。
2. **Current Reservoir Representative選定の実在する不具合**（Section 18.3の
   全representative空文字列問題）の原因調査——Semantic Census/Router以前の、
   Current Pipeline Preprocess側の課題として別途対応する。
3. **Representative文字数の上限設計**（Section 18.3の77,885文字問題）——Gate 1の
   New Representative戦略を本番採用候補にする場合は必須。
4. **AC自由記述fieldのLightweight生成方式**（Section 20 Phase 3B）は未設計。
5. **SC schema validation failure（約35-40%/call）の二分探索対応**
   （Section 17.4）は今回実装しないと決定したが、Small LLMを将来cascadeの中間
   tierとして採用する場合は再検討する。

## 23. Phase 3A実装計画（確定版、2026-09-13、次の実装対象）

1. Shadow Router Execution種別を新設（`geo_report_executions`の`execution_method`
   拡張、または新規stage）——Production Canonicalには一切影響しない。
2. 実Run上でLogistic Regression（Primary）予測＋Hybrid（Challenger）予測（Validation
   上で解いたconfidence cutoff・novelty gate閾値・Section 20.2のEvidence
   Sufficiency閾値を固定適用）とCurrent Strong LLM SC分類を並行記録。
   Gradient Boosting/Small LLMは記録対象に含めない（Primary/Challenger以外は
   Phase 3Aでは評価しない）。
3. **Productionでは全SCを引き続きCurrent Strong LLMで分類する**
   （Section 20.3、実削減はまだ発生しない）。
4. 実行のたびにCompatibility/Stability/Adjudicated Qualityの3指標（Section 20.1）
   をダッシュボードで可視化（既存Report Studio UIへShadow Router結果タブを追加）。
5. 複数Run（最低3-5件、可能なら別期間・narrative構成の異なるRun・新事件が発生した
   Runを含む）でShadow結果を蓄積してから、Production Routing移行（Canonical切替）
   の是非を判断する——今回のGate 1-3は単一Run（Arizona/Georgia等9レース）のみに
   基づくため、Run間の汎化性は未検証（Section 23.1のCross-Run Generalization Gate
   参照）。**新Runでも、Phase 2で解いたconfidence cutoff/novelty閾値/calibrationは
   最初は固定して使う**——Run毎に閾値を再チューニングすると、実質的にTestデータを
   Validationとして使い続けることになり、Cross-Run Generalizationの検証にならない。

### 23.1 Cross-Run Generalization Gate（Production Routing移行前の本当の関門）

単一Run内でのBenchmark・Gate 1-3は十分に実施した。次に価値があるのは**Runを
跨いだ汎化性の検証**であり、これがProduction Routing移行前の実質的な最終関門
となる。

```
Run 1  Phase 2で学習・評価済み（本Run、閾値/calibrationの学習元）
Run 2  完全out-of-sample Shadow（閾値は固定のまま適用）
Run 3  完全out-of-sample Shadow
...    （可能なら: 別期間のRun／narrative構成が変化したRun／
        新しい事件・論点が発生したRun／将来的にはContinuity/Monitoring
        ありのRunを含める）
```

### 23.2 Phase 3A最小ログ仕様

各SC clusterについて記録する項目:

```
run_id, cluster_id
logistic prediction（field毎）
field-wise calibrated confidence
overall calibrated confidence
nearest-neighbor distance / novelty score
evidence sufficiency（representative_count/usable_text_count等）
router decision（lightweight_accept | would_escalate）
router reason（low_confidence | novelty | insufficient_evidence | ...）
canonical Strong LLM結果
field毎agreement / critical-field disagreement
human/adjudication status（Section 20.1のAdjudicated Quality用、未着手=pending）
```

Run単位で集計する項目:

```
actual/would-be escalation率
projected Strong batches / calls / tokens / wall-clock
critical-field agreement
minority-class precision/recall
novelty capture率
disagreement率
```

## 24. Phase 3 Go / No-Go（最終判定）

**Phase 3A（SC-only Shadow Router）: Go。**
SC単独でのLLM呼び出し削減余地（実測70-76%、母集団投影ベース）が大きく、
Small LLMより明確に優れたLogistic Regression/Hybridという実行可能な代替手段が
あり、Production Canonicalを変更しないShadow Modeでの検証はリスクが低い。
Critical-field品質のRealistic値には未解決の限界（Section 19.2/22.1）があるが、
Shadow Mode（実際の判断には使わない、比較記録のみ）で運用しながら追加データを
蓄積することで解消できる。

**Phase 3B（AC再設計・Total Strong LLM 50%以上削減）: No-Go（時期尚早）。**
AC自由記述fieldのLightweight生成方式が未設計であり、これを解決しない限り
AC側のLLM処理量を安全に削減する手段がない。Section 20の通り、まずPhase 3Aを
実データで検証してから着手する。

**Production Canonical: 変更なし。** Current Prompt1がCanonicalのまま、
Phase 3AはShadow Modeでのみ実行する。

## 25. Phase 3A バックエンド実装・実データ検証（2026-09-13、同日中に実施）

Section 20-23の設計に基づき、`geo_report_shadow_router_service.py`（新規）＋
`sql/2026-09-13b_geo_report_shadow_router.sql`（新規、`geo_report_shadow_router_models`/
`geo_report_shadow_router_predictions`の2テーブル）を実装・テスト（6件、既存回帰含め
全1222件pass）。

### 25.1 実装内容

- `train_shadow_router_model`: Phase 2 Benchmarkと同じTrain=fit/Validation=
  calibration+threshold解決の枠組みで学習し、分類器・calibrator・confidence
  cutoff・novelty gate閾値・Train centroidsを凍結してpickle+base64で永続化する
  （Cross-Run Generalization検証のため、新しいRunへ適用する際も再学習しない）。
  method='logistic_regression'→role='primary'、'hybrid'→role='challenger'
  （Section 20の合意通り、Gradient Boosting/Small LLMは対象外）。
- `apply_shadow_router_to_run`: 凍結済みモデルを任意のRunへ適用。Evidence
  Sufficiency Gate（representative件数・有効文字数閾値）→Novelty Gate→
  Confidence Gateの順でmandatory escalation判定を行い、Canonical Prompt1 SC
  分類結果が利用可能な場合はCompatibility（field agreement/critical_field_
  disagreement）も計算する。
- `compute_shadow_router_run_summary`: Run単位集計（Section 23.2）。batch/token
  数は常に"Projected Savings"と明記する。

### 25.2 実データ検証（Run `d558df7e`、single-run smoke test）

Phase 2の`sc_classification`/`temporal`データで学習し、同一RunのValidation+Test
（678クラスタ）へ適用した結果:

| method | role | escalation率 | 推定Strong batch数 | Compatibility(cluster exact match) |
|---|---|---|---|---|
| logistic_regression | primary | 27.0%（183/678） | 10 | 27.4% |
| hybrid | challenger | 32.3%（219/678） | 11 | 27.3% |

Compatibility（Shadowと現行Canonical Strong LLMの全field完全一致率）は両methodとも
約27%——Phase 2の元Benchmark（cluster exact match 0.28-0.35、Section 7）と近い
水準であり、**永続化→ロード→適用という一連のパイプラインが元のBenchmarkと矛盾
しない結果を再現できている**ことを確認した（sanity check）。escalation reason
の内訳（insufficient_evidence 39件・low_confidence 108-144件・novelty 37件）も
分布として妥当。

**重要な限界（正直に記録）**: これは単一Run内での「学習→保存→ロード→適用」の
smoke testであり、Section 23.1が定義する**真のCross-Run Generalization検証
ではない**（第二の独立Runの実データが本セッション時点で存在しないため）。
`geo_report_shadow_router_models`は既に汎用的に設計済み（`trained_on_run_id`と
`apply_shadow_router_to_run`の`run_id`が独立して指定できる）ため、次に実施すべき
ことは新しいRunでこの同じmodel_idをそのまま適用し、閾値を再学習せずにShadow結果を
確認することである（Section 23.1参照）。

### 25.3 Provenance拡張 + Routing Safety指標（2026-09-13、ユーザー指摘を受けて追加）

Cross-Run Shadow開始前に必須の2点を追加実装（`sql/2026-09-13c_geo_report_shadow_
router_provenance_and_safety.sql`、`geo_report_shadow_router_models`へ`model_version`/
`reference_dataset_execution_id`/`calibration_json`列を追加。テスト2件追加、
既存回帰含め全1223件pass、実データで再学習・検証済み）:

1. **Provenance修正**: `embedding_model`/`embedding_version`が学習時に`None`の
   まま保存される実装漏れを発見・修正（`sentence-transformers/paraphrase-
   multilingual-MiniLM-L12-v2`を正しく記録するようになった）。加えて
   `reference_dataset_execution_id`（学習に使ったReference Datasetの元Prompt1
   execution_id、実データでは`6e27804c`）と`model_version`（`v1`）を新規記録。
2. **Routing Safety指標**（Compatibilityとは独立、ユーザー指摘）:
   `lightweight_accept`と判断したクラスタに限定した不一致率
   （`routing_safety_disagreement_rate`）と、field毎のagreement率
   （`field_agreement_by_field`、overall/lightweight_accept_onlyの両方）を追加。

実データ再検証結果（Run `d558df7e`、678クラスタ）:

| method | routing_safety_disagreement_rate | 主要field(lightweight_accept限定)agreement |
|---|---|---|
| logistic_regression | 68.5% | apply_to_cluster 94.1% / cluster_consistency 82.2% / attitude 77.2% / source_class 76.0% / theme_relevance 74.9% / reaction_type 71.3% |
| hybrid | 67.8% | apply_to_cluster 93.5% / cluster_consistency 81.9% / attitude 78.9% / source_class 76.0% / theme_relevance 74.7% / reaction_type 73.0% |

**解釈上の重要な注意（2026-09-13ユーザー指摘により訂正）**: 当初「Strong LLM
自身の再現率49.3%を踏まえると統計的に自然な水準」と記述したが、この表現は
言い過ぎであり訂正する。6-field joint disagreement（約68%）は、各fieldの
わずかな不一致が積み重なって急激に悪化するmetricであり、単純なエラー率として
解釈すべきではない——ここまでは正しい。しかし、Gate 1の49.3%（別の母集団・
別の条件で測定したStrong LLM再現率）と本metricの数値を直接比較して「この水準は
妥当」と結論づけることはできない——母集団・fields・条件が同一ではないため、
その比較から"問題なし"と断定するのは誤りである。正確な表現は次の通り:
**6-field joint disagreementは高い（約68%）が、これは全fieldにまたがる不一致を
複合させたmetricであり、直接的なエラー率として解釈すべきではない。Field単位の
agreementは有意に高い（70-94%）。また、Strong LLM自身のrerun再現性（Gate 1、
49.3%）は、Historical Referenceが決定論的なground truthではないことを示して
いる。したがって、Production Safetyの判断にはCritical-fieldの不一致と、
Adjudicated Disagreement（実Evidenceに基づくレビュー）評価が必要であり、特に
lightweight-acceptされたケースについてそれが欠かせない。** Logistic/Hybridは
本metricで同水準（大差なし）。joint基準ではなくfield単位・「危険な値反転
パターン」単位でRouting Safetyを再設計する作業を25.4で追加した。

### 25.4 Critical Flip指標（2026-09-13、ユーザー指摘を受けて追加・実データ検証済み）

`routing_safety_disagreement_rate`（6 field中どれか1つでも不一致＝joint）だけでは
「positive→neutral」のような軽微なズレと、業務上意味が真逆になる反転を区別できない
という指摘を受け、明示的に列挙した**危険な値反転パターンのみ**を検出する
`critical_flip_*`系の指標を追加した（推測でパターンを広げず、Prompt2の3-way分母
計算（G.1章、`source_class="Public Voice"`必須）や採否判定に直結する4通りのみ:
`theme_relevance`のrelevant⇔irrelevant、`apply_to_cluster`のTrue⇔False、
`attitude`のpositive⇔negative、`source_class`のPublic Voice⇔Fact Only/Media）。
実装過程で型不一致バグを発見・修正（分類器は`str(label)`で学習するため
`apply_to_cluster`の予測値は文字列"True"/"False"になるが、canonical側はDBの
boolean型——str化せず比較すると常に不一致扱いになるバグがあった）。テスト2件追加、
既存回帰含め全1224件pass。

**実データ結果（Run `d558df7e`、lightweight_accept限定）**:

| method | lightweight_accept率 | joint disagreement(any-field) | **critical flip率** | 内訳(件数) |
|---|---|---|---|---|
| logistic_regression | 73.0%（495/678） | 68.5% | **32.1%（159/495）** | theme_relevance 106 / source_class 45 / apply_to_cluster 29 / attitude 3 |
| hybrid | 67.7%（459/678） | 67.8% | **30.9%（142/459）** | theme_relevance 97 / source_class 34 / apply_to_cluster 30 / attitude 3 |

**この結果は正直に重く受け止める必要がある**: joint基準の68%を「複合指標だから
直接のエラー率ではない」と切り分けた上でも、業務上意味が真逆になる反転だけに
絞った結果が約31-32%というのは、低い数字ではない。特にtheme_relevance
（クラスタをレポートに含めるか否かを左右する）とsource_class（3-way分母の
`Public Voice`必須条件に直結する）が主要因である点は、単なる「表現のブレ」では
なく実際の集計結果に影響しうる誤分類であることを意味する。

Gateの実効性（`novelty_gate_captured_disagreement_count`=28件、
`evidence_sufficiency_gate_captured_disagreement_count`=25件、いずれもLogistic）
は「escalateされた183件中、Novelty/Evidence Sufficiency Gateが正しく拾えていた
不一致ケース」を示すが、lightweight acceptされた495件中159件のcritical flipと
比べると、**Gateが拾いきれていない危険なケースの方が数として遥かに多い**という
非対称性が明確に見える。これはCross-Run Generalization検証・Adjudicated Quality
レビューを行う前段階でも、現状のConfidence/Novelty閾値だけでは不十分である
可能性を示す重要な実データ証拠であり、Production Routing移行の判断において
Section 25.5のGate（特に4番目）を軽視できない根拠になる。

### 25.5 現状ステータスと次のGate（確定）

> **Phase 3A backend: Complete**
> **SC Shadow Router: Technically operational**
> **Production readiness: Not yet established**
> **Next Gate: Frozen-model Cross-Run Generalization + Routing Safety（特にcritical flip）**

第二の独立Runができるまでは、同一Run（`d558df7e`）でこれ以上モデル・閾値を
調整しない——学習済みmodel_id（`logistic_regression`: `1734afc1-...`、
`hybrid`: `0be427b5-...`）と閾値・calibrationを凍結したまま待つことが、検証と
して正しい。次の独立Runが得られた際は以下の4点で判定する:
1. Escalation rateが20-30%前後に留まるか。
2. Critical-field disagreement（特にcritical flip率）が許容範囲か。
3. Novelty/Evidence Sufficiency Gateが危険ケースをStrong側へ拾えているか
   （本Runでの非対称性が再現するか要確認）。
4. **Lightweight Acceptの不一致を実Evidenceで20-30件程度レビューし、
   Strong better/Shadow better/Both acceptable/Ambiguousに分類する**——
   Strong LLM自身の再現性の限界（Gate 1、49.3%）がある以上、
   `Shadow ≠ Strong`を直ちに`Shadow = wrong`とは扱わない。

### 25.6 最終確定（2026-09-13、本セッションの区切り）

Phase 3Aは以下の状態で確定し、次の独立Runが得られるまでこのRunでの追加調整は
行わない:

```
Phase 0-2.5              COMPLETE
Phase 3A Backend         COMPLETE
SC Shadow Router         OPERATIONAL
Frozen Logistic Primary  READY FOR OUT-OF-SAMPLE TEST
Production Routing       NO-GO YET
Remaining Production Gate:
  Cross-Run Generalization
  + Critical-Flip Adjudicated Quality
  + Projected Savings Stability
```

**次のGateは「Cross-Run Generalizationのみ」ではなく、常に2軸同時に検証する**:
Frozen Logistic Modelを独立Run Bへ適用し、①Generalization（escalation率・
field性能がRun Aと同水準を維持するか）と②Routing Safety（lightweight_accept中の
critical flip件数、その不一致をEvidenceまで戻ってadjudicateした結果）を同時に
見る。Critical flipのadjudication分類は`geo_report_shadow_router_predictions.
adjudication_status`列に既に用意済みの4値（`reviewed_shadow_correct`/
`reviewed_canonical_correct`/`reviewed_both_plausible`/`reviewed_undecidable`）が
そのままShadow correct/Strong correct/Both acceptable/Ambiguousに対応するため、
追加のスキーマ変更は不要——これによって初めて「Critical flip 30-32%のうち、
本当に危険なLightweight誤判定は何%か」が分かる。

**現モデルは今から改善しない**: 159/495件のcritical flipを見てthreshold/
特徴量を調整すると、Run AがValidation化し次のCross-Run Testの意味が弱まる。
Logistic（Primary）・Hybrid（Challenger）ともに、model_id・閾値・calibration・
embedding model/versionを凍結したまま待つ。LogisticとHybridのcritical flip率も
ほぼ同水準（32.1%/30.9%）だったため、より単純なLogisticをPrimaryとして固定する
判断を維持し、Hybridも今は再学習・改善しない。

次の独立Runが得られた際の実施順序: ①Current Strong Prompt1を通常通り最後まで
実行しCanonical結果を得る（Shadow Routerのために特別な手順は不要）→②同じRunへ
Frozen Shadow Router（Logistic Primary）を適用→③Generalization + Routing
Safetyを同時に評価。

### 25.7 未実施（次のタスク）

1. **Cross-Run Generalization検証**（最優先）: 別の独立Run（別期間/別narrative
   構成）で、本Runの学習済みmodel_idを**再学習せずそのまま**適用する。
2. **Adjudicated Quality**: critical flip 20-30件程度を実Evidence（cluster所属
   record本文）まで戻ってレビューし、Strong better/Shadow better/Both
   acceptable/Ambiguousに分類する（Gate 1 Section 18.3と同じ手法）。
3. UI（Shadow Router結果タブ）は未着手。
4. Compatibility/Stability/Adjudicated Qualityの3軸のうち、実装済みはCompatibility
   （+ Critical Flip）のみ。Stability（Gate 1のA vs Bと同種の継続測定）は
   運用開始後に着手する。
5. Shadow Router Execution種別（`geo_report_executions`との統合）は未実装——現状は
   `geo_report_shadow_router_models`/`predictions`単体で完結する設計にとどめている。
