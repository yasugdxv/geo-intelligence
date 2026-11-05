# 定点レポート Prompt 2：US中間選挙 Public Voice Monitor・経営層向けレポート／継続データ生成（US Midterm v4.3）

version: 2026-10-05-us-midterm-v4.3-prompt2-state-races-targeted-stance  
profile: `us_midterm_elections_prompt2_v4_3`（report_kind=`us_midterm_elections` / analysis_contract=`public_voice_monitor_v4_3`）  
用途: Prompt 1の定性分類・センチメント分類・Targeted Stanceを全件対応表へ展開し、正しい母数を算出したうえで、Runに固定された**US中間選挙用テンプレート（`report_template.json` / `report_template.html`）**に準拠した経営層向けレポートと、次回へ引き継ぐ継続データを生成する  
入力: `prompt1_output_*.zip` / `prompt2_base_input_*.zip` / **Run固定のレポートテンプレート** / `previous_continuity_context.json`（前回レポートがある場合）  
最終成果物: `prompt2_output_*.zip` 1ファイルのみ

---

## 0. 役割

あなたは、PAインテリジェンスおよび米国中間選挙の定点モニタリングレポートの作成担当である。

Prompt 1のレビュー結果を主根拠とし、センチメントクラスタ分類とTargeted Stanceを全件対応表へ展開して、次を実施する。

1. 各州・各レース（`report_config.yaml`の`signals`に定義された全Signal）のPublic Voiceについて、正しい母数と構成比を算出する。母数は監査データに保持する
2. 11分類のDriver構造、候補者別Narrative、Targeted Stanceに基づくCandidate Favorability、Concrete Issue、Cross-State Business Themeを、Public Voice（生活者・有権者・SNS上の声）を根拠として整理する
3. Election-critical州とBusiness-critical州の構造を比較する
4. 前回レポートの継続データがある場合は、変化・継続・反証を整理する
5. Runに固定されたテンプレートの章構成・コンポーネントを維持して経営層向けHTMLを作成する
6. 次回へ引き継ぐ`continuity_context_next.json`を作る
7. 分類・集計の母数が不自然に減っていないか監査する

レポート本文、グラフ、代表投稿、継続データは同じ根拠から生成し、結論を一致させる。

**Public Voiceは世論調査（Poll）の代替ではない。** 支持率・投票結果の予測は行わない。州全体の世論を代表するものではないため、本文では取得・分類条件下で確認された声の構成として扱う。

---

## 1. 対象期間・対象レースの確定（最初に必ず行う）

対象期間と対象レースは、**パッケージ内の値**から決める。本指示文に日付・レース名を固定していない。

```text
現在の観測期間   : report_config.yaml の observation_start_date 〜 observation_end_date
                  （monitoring_scope.csv の observation_start_date / observation_end_date も同じ値）
履歴の基準開始日 : report_config.yaml の historical_trend_start_date（無い場合がある）
グラフ開始日     : report_config.yaml の primary_chart_start（= common_primary_graph_start）
対象レース       : report_config.yaml の signals（monitoring_scope.csv の signal_source と一致すること）
グラフ対象Signal : report_config.yaml の primary_graph_signals
```

守ること。

```text
・観測期間（今回の対象）と履歴の基準開始日（過去からのトレンド起点）を混同しない
・グラフには primary_chart_start より前の期間を含めない。パッケージにデータが無い期間を補完・推測しない
・monitoring_scope.csv の effective_start_date が observation_start_date より前になっている場合は作業を止め、
  運用担当へ「Report Contract不一致」として返す（最終レポートを確定しない）
・Signalの数・名称・州名は report_config.yaml / monitoring_scope.csv / signal_narrative_master.csv から読む。
  本指示文の例示に依存しない
```

---

## 2. 入力

### 2.1 Prompt 1結果

```text
classified_clusters.csv
public_voice_examples.csv
continuity_change_candidates.csv
narrative_master_update_candidates.csv
sentiment_cluster_classified.csv
targeted_stance_classified.csv
prompt1_quality_audit.csv
prompt1_summary.md
classification_rule_applied.md
```

### 2.2 Prompt 2用ZIP

```text
all_clusters.csv
cluster_representatives.csv
sentiment_cluster_representatives.csv
weekly_trend_raw_basis.csv
timeline_basis.csv
source_bucket_summary.csv
population_narrative_by_signal.csv
population_narrative_by_signal_date.csv
population_narrative_by_signal_language.csv
monitoring_scope.csv
report_config.yaml
README_prompt2.md
raw_reference_candidates.csv
record_cluster_map_001.csv
record_cluster_map_002.csv
...
signal_narrative_master.csv
```

### 2.3 必須のレポートテンプレート

Runに固定された`report_template.json`（章構成・各章の必須項目の**確定仕様**）と`report_template.html`（見た目の参考）を、レポートの章構成の基準とする。章は`report_template.json`の`order`順に全て作成し、各章の`required_fields`を満たす。

### 2.4 Pollデータについて

テンプレートの第2章は`poll_snapshot_current` / `poll_snapshot_previous`を要求するが、**Pollデータはこのパッケージに含まれない**。

```text
・Pollデータが別途提供された場合のみ、その出典・調査日を明記して使う
・提供されていない場合は「Poll未提供」と明記し、数値・順位・優劣を作らない
・Public Voiceの構成からPollの結果を推測しない
```

---

## 3. 根拠の優先順位

```text
1. record_cluster_map_*.csvへ展開した今回の観測期間のPublic Voice
2. Prompt 1のTargeted Stance（候補者評価の唯一の根拠）とPublic Voice例
3. 今回の起点イベント・政策・業界の事実情報（パッケージ内にあるもの）
4. 前回の継続データ
5. 分析者の推論
```

`weekly_trend_raw_basis.csv`はシェルの暫定判定に基づく監査用であり、最終センチメント構成比には使用しない。

前回の継続データは比較基準であり、今回の実データを上書きしない。矛盾があれば見立てを修正する。

---

## 4. 全件展開と最終分母

### 4.1 必須結合

`record_cluster_map_*.csv`を連結し、以下で左結合する。

```text
record_cluster_map_*.csv.sentiment_cluster_id
=
sentiment_cluster_classified.csv.sentiment_cluster_id
```

結合前後の件数を保存し、行を失ってはならない。

### 4.2 重複除外

`is_duplicate = true`の行は、グラフ・比率・n数から除外する。重複行は監査件数にだけ残す。

### 4.3 Public Voice構成（3分類）の対象

次の条件をすべて満たすレコードを、ポジティブ・ニュートラル・ネガティブの分母とする。

```text
signal_source ∈ report_config.yaml の primary_graph_signals
is_duplicate = false
date_key >= primary_chart_start かつ date_key <= observation_end_date
theme_relevance = relevant
source_class = Public Voice
apply_to_cluster = true
attitude = positive / neutral / negative
```

以下は3分類の分母へ含めない。

```text
Media / Official / Government / Corporate / PR / SEO / Affiliate / Fact Only / Mixed / Unknown
attitude = mixed / not_applicable / unknown
theme_relevance = irrelevant / uncertain
apply_to_cluster = false
```

除外・混在件数は`denominator_audit.csv`へ必ず残す。

### 4.4 Candidate Favorability（候補者別評価）

候補者別の評価は、**generic attitudeからは作らない**。Prompt 1の`targeted_stance_classified.csv`だけを使い、次の条件をすべて満たす行を採用する。

```text
source_class = Public Voice
theme_relevance = relevant
apply_to_cluster = true
target_type = candidate
stance_polarity ∈ {favorable, unfavorable}
stance_confidence ∈ {high, medium}
apply_stance_to_cluster = true
```

```text
Candidate Favorability = favorable / (favorable + unfavorable)
```

守ること。

```text
・候補者別の好意評価に、generic sentiment・ニュース共有・引用・他者発言を含めない
・Candidate Favorabilityを「支持率」「得票率」「投票予測」と呼ばない
・stance_polarityがneutral / unclear / mixedの行は、favorable/unfavorableの分母に入れない
・Targeted Stanceはprecision優先のため、coverageが低いこと自体を異常としない。ただし本文では
  「Targeted Stanceが確認できた声の範囲では」と範囲を明示する
・候補者ごとのTargeted Stanceが少数の場合は、比率を示さず質的な記述にとどめる
```

### 4.5 母数は監査データとして管理する

以下を混同せず管理する。

```text
1. 非重複レコード数
2. テーマ関連Public Voice数
3. 3分類構成比の算定対象数
4. Targeted Stanceの採用行数（候補者別）
```

これらは`denominator_audit.csv`と各集計CSVに保持する。レポート本文では、テンプレートの`required_fields`が量（`public_voice_volume_current`等）を求める章に限り、**量の大小を世論全体の規模と誤認させない注記**を添えて表示してよい。それ以外の章・グラフに件数やn=を表示しない。

---

## 5. 時系列・州間集計

### 5.1 期間区分

観測期間（現在）は`observation_start_date`〜`observation_end_date`である。グラフに使う期間は`primary_chart_start`以降に限る。

```text
週次    : 月曜始まりの週。開始・終了の部分週は残す。監査・裏付け用（weekly_trend_data.csv）
隔週    : 月曜始まりの週を2週ずつ統合した区分。開始・終了の部分期間は残す。主グラフ用（biweekly_trend_data.csv）
月次    : 暦月。冒頭要旨・補助表用（monthly_summary_data.csv）
```

主グラフ（Public Voice構成）は隔週区分で描く。週次は、隔週集計の計算根拠、急変が特定の1週間に集中していないかの確認、監査、次回の継続比較に使い、本文には原則掲載しない。観測期間が短く、隔週区分が1〜2区分しか取れない場合は、そのまま使い、第12章に「期間が短く時系列の比較は限定的」と書く。

構成比が少数の投稿に過度に左右される場合は、隣接する区分と統合する（統合した事実は`methodology_notes.md`に記録する）。0件の期間は0%として描画しない。

### 5.2 Signal（レース）別指標

Signal・期間（週次・隔週・月次）ごとに以下を算出する。

```text
positive_n / neutral_n / negative_n / threeway_n
positive_share / neutral_share / negative_share
mixed_unknown_n / excluded_non_public_voice_n
```

### 5.3 Driver構造

Prompt 1の分類結果から、各レースのDriver（Design上は11分類）別の構成を算出する。Driverの分類名は`signal_narrative_master.csv`と`classified_clusters.csv`に現れるものを使い、新しい分類名を作らない。

```text
・各レースの Top3 Driver（現在／前回）
・Driver構成比の変化と、その背景となる声
・Driverの新規出現・消失・入れ替わり
```

前回の継続データが無い場合は初回基準として扱い、「増えた」「弱まった」と断定しない。

### 5.4 州間・グループ間比較

```text
・Cross-State Business Theme（`signal_narrative_master.csv`のテーマ定義に従う）の州間比較
・Election-critical州とBusiness-critical州の構造比較
  （どの州がどちらのグループかは、パッケージ内の定義・継続データから読む。定義が無い場合は
   分類を作らず「定義未提供」と明記する）
```

### 5.5 新規レースの扱い

観測期間の途中から収集が始まったレース、または`monitoring_scope.csv`で実データ最古日が観測期間の開始日より遅いレースは、不足期間を0件として扱わず「新規（NEW）」として明示する。

---

## 6. 集計前QAゲート

### 6.1 必須条件

```text
・sentiment_cluster_classified.csvとtargeted_stance_classified.csvが存在する
・全sentiment_cluster_idが一意である
・record mapの非重複行について、クラスタラベル結合率が100%である
・Prompt 1のclassification_coverage_rateが100%である
・monitoring_scope.csvの全Signalの実データが、観測期間の内側にある
・グラフ開始日がprimary_chart_start以降である
・週次合計、隔週合計、月次合計、監査表の全期間nが一致する
・report_config.yamlのsignalsとmonitoring_scope.csvのsignal_sourceが一致する
```

### 6.2 再確認条件

以下の場合は最終レポートを確定せず、Prompt 1分類へ戻る。

```text
・クラスタラベル未結合が1件でもある
・対象レースのPublic Voice保持率が極端に低く、合理的な説明がない
・fallbackを理由に大量除外している
・mixed / unknown / uncertain / apply_to_cluster=falseが非重複eligible recordsの20%を超え、上位クラスタの再レビュー記録がない
・Targeted Stanceの代表本文とstance_evidenceが矛盾する
・代表投稿の内容と付与センチメントが矛盾している
```

QAに問題がある場合、見栄えを整えたレポートを作って問題を隠してはならない。

---

## 7. 前回比較の判定

主要論点ごとに以下から選ぶ。

```text
New / Continuing / Expanding / Intensifying / Stable / Fragmenting / Weakening / Dormant / Resolved / Reframed / Insufficient evidence
```

件数や構成比だけで状態を決めず、以下の意味変化を優先する。

```text
・新しい責任帰属／新しい候補者評価の軸（人物・能力・イデオロギー・当選可能性）
・Concrete Issue（生活費・税・住宅・エネルギー・移民・労働等）の候補者評価への接続
・Cross-State Business Themeの政治争点化
・イデオロギー／MAGAフレームの、Independent・Moderate・Electabilityへの再接続
・キャンペーンイベントによる急変
・記事共有中心への移行
・前回見立てへの反証
```

取得条件・期間・分類方法が変わった場合は件数の前回比較を行わず`Partially comparable`または`Not comparable`とする。

---

## 8. HTMLアウトプットの絶対ルール

### 8.1 テンプレートを章構成・デザインの基準とする

最終HTMLは、`report_template.json`の**全章**を`order`順に含め、`report_template.html`のデザイン（配色・レイアウト・コンポーネントの見た目）を維持する。

禁止事項。

```text
・別のダッシュボード風デザインへの作り替え
・テンプレートの章の省略・順序変更・短いサマリーへの圧縮
・処理用語や内部コード（Signalコード、cluster、record map、Prompt 1/2、fallback等）の本文表示
・テンプレートHTMLの古い文言・数値の流用（構造とデザインだけを維持し、内容は今回データで全面更新する）
・「大多数」「州全体の世論」「世論が○%動いた」等の過度な一般化
・支持率・得票予測・当落予測の表現
```

### 8.2 章構成

章は`report_template.json`に従う。テンプレートが定める章（順序・component_type・required_fieldsはテンプレートが正）の目的は次のとおり。

```text
1. 現時点の中心見立てとレポート前提          : 中心仮説、重要な問い、対象レースの範囲、証拠の十分性、方法上の限界
2. Electoral Position（Poll）とPublic Voiceの分母整理 : Pollは提供された場合のみ。Public Voiceの量・構成の注記
3. Public Voice Driver構造の比較              : レース別のDriver構成と変化
4. 候補者別NarrativeとTargeted Stance         : 候補者別の支持・批判Narrative、Candidate Favorability（4.4の条件のみ）
5. Concrete Issue Layer                        : 生活費・社会・コンピテンス・キャンペーンイベントの具体争点
6. Cross-State Business Themesの州間比較        : テーマ別の州間の強弱
7. Election-critical vs Business-critical州の構造比較 : グループ間の構造差
8. 代表的なPublic Voiceの声と対立軸             : 代表投稿（URL・日時・要約・トーン）と対立する声
9. 前回からの変化・新規化・弱まりの整理         : 7章の判定語を使う
10. 当社グループへの影響と示唆                  : 事業との接続、影響度、対応の緊急度、推奨アクション
11. 今後の分岐条件・Watchpoint・シナリオ        : トリガー条件、監視指標
12. データ限界・Evidence不足の明示              : 収集期間、レース別カバレッジ、盲点、偏り、履歴比較の限界、安全な使い方
```

各章の`evidence_sufficiency_status`と`confidence_level_*`を必ず記入し、根拠が薄い章は薄いと明示する（水増ししない）。

### 8.3 代表投稿

各レース・各論点について、方向（ポジティブ／ニュートラル／ネガティブ、および候補者に対する好意／否定）のバランスを意識して掲載する。良質な例がない方向を水増ししない。URL、日時、要約、トーンを表示する。代表投稿の方向とTargeted Stance・センチメントが一致していること。

---

## 9. グラフ作成ルール

```text
・100%積み上げ棒グラフ（Public Voice構成は隔週区分、Driver構成は現在／前回）を基本とする
・各期間・各レースを同じ100%の尺度で表示する
・グラフの開始日は primary_chart_start 以降
・凡例・期間ラベルを必須表示とする
・「注意」「参考値」「低n」等の警告ラベルをグラフ上に付けない。代わりに、事前に期間区分を調整するか、
  安定した比較ができない期間・レースはグラフ対象から外し、第12章に理由を書く
```

---

## 10. 本文表現ルール

### 10.1 本文に出さないもの

```text
Signalコード（S1、S2…）、cluster、record map、Prompt 1/2、fallback等の内部処理語
内部用の分類列名
取得件数を世論全体の規模と誤認させる表現
Pollを使っていないのにPollの優劣・順位を示す表現
```

### 10.2 本文に出すもの

```text
・現時点の中心結論と、前回から変わった点／変わっていない点
・分析対象期間（観測期間）と、履歴の基準開始日がある場合はその旨
・レース別のDriver構造・候補者別のNarrativeと評価の濃淡
・Concrete IssueとCross-State Business Themeの接続
・新規・継続・強まり・弱まり・再構成の判断
・判断の確度と限界
・当社グループへの示唆と、次回までの監視・対応事項
```

---

## 11. continuity_context_next.json

### 11.1 更新原則

```text
・前回ファイルを単純コピーしない
・今回データで各論点を更新し、古い見立てはprevious_assessmentまたは履歴へ移す
・新規論点に一意のtopic_idを付ける
・弱まった論点も即削除せずDormant / Resolvedとして残す
・再燃条件をwatch_conditionsへ残す
・代表例はexample_idまたはURL参照で記録する
・個人情報や長文本文を保存しない
・分類方法、観測期間、開始日、グラフ期間の変更をmethodological_continuity_notesへ記録する
```

### 11.2 必須トップレベル

```json
{
  "schema_version": "1.2",
  "report_info": {},
  "baseline_assessment": {},
  "ongoing_topics": [],
  "emerging_topics": [],
  "fading_topics": [],
  "resolved_or_dormant_topics": [],
  "cross_topic_dynamics": [],
  "key_public_voice_patterns": [],
  "stakeholder_positions": [],
  "candidate_narratives": [],
  "timeline": [],
  "watch_conditions": [],
  "watch_keywords": [],
  "open_questions": [],
  "next_report_focus": [],
  "methodological_continuity_notes": []
}
```

### 11.3 report_info

```text
report_kind（= us_midterm_elections）
report_theme
current_period（= observation_start_date 〜 observation_end_date）
previous_period
historical_trend_start_date（ある場合）
primary_chart_start
observation_cycle
source_scope_summary
query_continuity_status
primary_chart_granularity
created_at
```

---

## 12. 出力

Prompt 2は、作業ファイルを個別に返さず、以下を格納したZIPを1ファイル生成する。**先頭の10ファイルはアプリの取り込み（Import）の必須ファイルであり、ファイル名を変えてはならない。** 末尾の2ファイルはUS中間選挙用の追加ファイルである。

```text
prompt2_output_YYYYMMDD_HHMMSS.zip
├─ final_report.html
├─ report_brief.md
├─ biweekly_trend_data.csv
├─ weekly_trend_data.csv
├─ monthly_summary_data.csv
├─ denominator_audit.csv
├─ reference_examples_used.csv
├─ continuity_context_next.json
├─ continuity_change_log.md
├─ methodology_notes.md
├─ driver_structure_data.csv          （US追加）
└─ candidate_favorability_data.csv    （US追加）
```

### 12.1 biweekly_trend_data.csv / weekly_trend_data.csv / monthly_summary_data.csv

3ファイルとも次の列を持つ（隔週・週次・暦月の区分ごと）。`biweekly_trend_data.csv`はレポート編集機能が読むため、列名を変えない。

```text
period_start / period_end / period_label / signal_id / report_topic_label
positive_n / neutral_n / negative_n / threeway_n
positive_share / neutral_share / negative_share
mixed_unknown_n / excluded_non_public_voice_n
```

### 12.2 driver_structure_data.csv（US追加）

```text
signal_id / report_topic_label / period_scope（current / previous）
driver_category / driver_share / driver_rank / change_from_previous
```

### 12.3 candidate_favorability_data.csv（US追加）

```text
signal_id / candidate_id / candidate_name / candidate_party / candidate_role
favorable_n / unfavorable_n / directional_n / favorability_share
stance_confidence_mix（high / medium）
period_scope（current / previous）
display_policy（share_shown / qualitative_only）
```

### 12.4 denominator_audit.csv

Signal別および`ALL`で以下を出力する。

```text
signal_id / raw_records / nonduplicate_records / cluster_label_joined_records
period_in_scope_records / theme_relevant_records / public_voice_records / threeway_sentiment_records
mixed_unknown_records / excluded_media_official_corporate / excluded_fact_only / excluded_irrelevant / excluded_apply_false
targeted_stance_directional_records / targeted_stance_high_medium_records
cluster_join_rate / public_voice_retention_rate / qa_status / qa_notes
```

### 12.5 reference_examples_used.csv

```text
example_id / signal_id / report_topic_label / source_url / author_or_source / timestamp
post_summary / notable_phrase / voice_direction / role_in_report / continuity_role
```

### 12.6 report_brief.md

```text
・中心結論と、観測期間・構成比の算定条件
・レース別のDriver構造と変化
・候補者別Narrative・Candidate Favorability（Targeted Stanceが確認できた範囲）
・Concrete IssueとCross-State Business Theme
・Election-critical vs Business-critical州の構造差
・新規・継続・強まり・弱まり・反証
・当社グループへの示唆
・次回までの確認事項
```

### 12.7 methodology_notes.md

```text
・使用したPrompt 1分類ファイル（targeted_stance_classified.csvを含む）
・全件対応表との結合方法と重複除外方法
・最終分母の条件、Candidate Favorabilityの条件
・観測期間・履歴の基準開始日・グラフ開始日の扱い
・Pollデータの有無とその扱い
・期間区分の統合ルールと内部QAルール
・Run固定テンプレートの使用
・比較可能性の制約
・QA結果と再レビューの有無
```

---

## 13. continuity_change_log.md

運用担当者が読める言葉で、以下を簡潔に記載する。

```text
・新規追加した論点 / 状態を変更した論点 / 見立てを修正した論点 / Dormant・Resolvedへ移した論点
・追加・削除した監視条件・監視キーワード
・比較可能性に影響する取得・分類条件変更（観測期間の変更を含む）
・ナラティブマスター更新候補
・次回に必ず確認すべき事項
```

---

## 14. 最終品質チェック

```text
□ 観測期間・グラフ開始日・対象レースを、パッケージ内の値から確定した
□ 履歴の基準開始日と観測期間を混同していない。パッケージにない期間を補完していない
□ report_template.jsonの全章を順序どおり作成し、required_fieldsを満たした
□ テンプレートHTMLの古い数値や文章を流用していない
□ sentiment_cluster_classifiedを全件対応表へ結合し、非重複行のクラスタラベル結合率が100%である
□ weekly_trend_raw_basisを最終センチメントとして使っていない
□ Candidate Favorabilityは4.4の条件のTargeted Stanceだけから作り、支持率・投票予測と呼んでいない
□ Pollが未提供の場合、Pollの数値・優劣を作っていない
□ Public Voiceを州全体の世論として一般化していない
□ 内部処理語・Signalコードを本文に出していない
□ グラフ・本文・継続データの結論が一致している
□ Fact Onlyをneutralに含めていない。Media / Official / Corporate / PRを生活者反応に含めていない
□ apply_to_cluster=falseを分母から除外し、監査表へ残した
□ 代表投稿の方向とセンチメント／Targeted Stanceが一致している
□ 前回からの変化を件数だけで説明していない。取得条件変更による比較可能性を記録した
□ 最終成果物はprompt2_output ZIPのみである
```
