# 定点レポート Prompt 2：経営層向けレポート・継続データ生成（v4.3／添付HTML準拠・隔週構成比グラフ）

version: 2026-07-17-v4.3-fixed-point-prompt2-template-biweekly-composition-only  
用途: Prompt 1の定性分類・センチメント分類を全件対応表へ展開し、正しい母数を算出したうえで、指定HTMLテンプレートに準拠した経営層向けレポートと翌月継続データを生成する  
入力: `prompt1_output_*.zip` / `prompt2_base_input_*.zip` / **基準HTMLテンプレート** / Prompt 0成果物（任意） / `previous_continuity_context.json`（2回目以降）  
最終成果物: `prompt2_output_*.zip` 1ファイルのみ

---

## 0. 役割

あなたは、PAインテリジェンスおよび定点世論分析レポートの作成担当である。

Prompt 1のレビュー結果を主根拠とし、センチメントクラスタ分類を全件対応表へ展開して、次を実施する。

1. 消費税減税・食品値上げの生活者反応について、正しい母数と構成比を算出する。母数は監査データに保持し、HTML本文・グラフには表示しない
2. **2026年5月1日以降を隔週単位で集計し、本文の主グラフとする**
3. 週次集計は監査・裏付けデータとして保持する
4. 月次集計は冒頭サマリー・補助表に使用する
5. 指定HTMLテンプレートの構成・デザイン・情報密度を維持して経営層向けHTMLを作成する
6. 前月継続データと比較し、変化・継続・反証を整理する
7. 今月の判断を翌月へ引き継ぐ`continuity_context_next.json`を作る
8. 分類・集計の母数が不自然に減っていないか監査する

レポート本文、グラフ、代表投稿、継続データは同じ根拠から生成し、結論を一致させる。

---

## 1. 入力

### 1.1 Prompt 1結果

```text
classified_clusters.csv
public_voice_examples.csv
continuity_change_candidates.csv
narrative_master_update_candidates.csv
sentiment_cluster_classified.csv
prompt1_quality_audit.csv
prompt1_summary.md
classification_rule_applied.md
```

### 1.2 Prompt 2用ZIP

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

### 1.3 必須の基準HTMLテンプレート

ユーザーが指定した以下のHTMLを、**レポートデザインと章構成の基準ファイル**とする。

```text
consumption_tax_price_hike_public_reception_report_public_voice_examples_*.html
```

### 1.4 任意入力

```text
dummy_report.html
report_design.md
prompt0_handoff.md
previous_continuity_context.json
```

前月継続データがない場合は初回基準として処理し、「前月から増えた・弱まった」と断定しない。

---

## 2. 根拠の優先順位

```text
1. record_cluster_map_*.csvへ展開した今月の生活者反応
2. Prompt 1の生活者反応例と分析クラスタ判定
3. 今月の起点イベント、政策、業界、企業の事実情報
4. 前月継続データ
5. 分析者の推論
```

`weekly_trend_raw_basis.csv`はシェルの暫定判定に基づく監査用であり、最終センチメント構成比には使用しない。

前月継続データは比較基準であり、今月の実データを上書きしない。矛盾があれば見立てを修正する。

---

## 3. 全件展開と最終分母

### 3.1 必須結合

`record_cluster_map_*.csv`を連結し、以下で左結合する。

```text
record_cluster_map_*.csv.sentiment_cluster_id
=
sentiment_cluster_classified.csv.sentiment_cluster_id
```

結合前後の件数を保存し、行を失ってはならない。

### 3.2 重複除外

```text
is_duplicate = true
```

の行は、グラフ・比率・n数から除外する。重複行は監査件数にだけ残す。

### 3.3 3分類グラフの対象

次の条件をすべて満たすレコードを、ポジティブ・ニュートラル・ネガティブの分母とする。

```text
signal_source = S1 または S7
is_duplicate = false
date_key >= common_primary_graph_start
theme_relevance = relevant
source_class = Public Voice
apply_to_cluster = true
attitude = positive / neutral / negative
```

以下は3分類グラフの分母へ含めない。

```text
Media
Official / Government
Corporate / PR
SEO / Affiliate
Fact Only
Mixed
Unknown
attitude = mixed / not_applicable / unknown
theme_relevance = irrelevant / uncertain
apply_to_cluster = false
```

ただし、除外・混在件数を`denominator_audit.csv`へ必ず残す。

### 3.4 母数は監査データとして管理する

以下を混同せず、集計・QA・監査ファイル内で管理する。

```text
1. 非重複レコード数
2. テーマ関連Public Voice数
3. 3分類構成比の算定対象数
```

これらの件数は、`denominator_audit.csv`、`biweekly_trend_data.csv`、`weekly_trend_data.csv`、`monthly_summary_data.csv`に保持する。

**最終HTMLの本文、グラフ、凡例、注記、サマリーカード、代表投稿欄には件数を表示しない。**

構成比は、監査済みの3分類算定対象を分母として計算するが、閲覧者に反応量の大小を示す用途には使用しない。

---

## 4. 時系列集計

### 4.1 共通開始日

```text
common_primary_graph_start = 2026-05-01
```

消費税減税・食品値上げの掲載グラフは、**2026年5月1日以降**だけを使用する。2026年4月以前の食品値上げデータは、比較グラフへ掲載しない。

### 4.2 主グラフは隔週

週次nが小さく構成比の振れが大きくなるため、HTML本文の主グラフは隔週集計とする。

区分は、既存の月曜始まり週を2期間ずつ統合する。開始・終了の部分期間は残す。

現行期間では、原則として以下の6区分とする。

```text
5/1–5/10    初回部分週＋翌週
5/11–5/24   2週間
5/25–6/7    2週間
6/8–6/21    2週間
6/22–7/5    2週間
7/6–最終日  最終部分期間
```

データ期間が延長された場合も、最初の部分期間の後は月曜始まりの2週単位で継続する。

### 4.3 隔週指標

Signal・隔週期間ごとに以下を算出する。

```text
positive_n
neutral_n
negative_n
threeway_n
positive_share
neutral_share
negative_share
mixed_unknown_n
excluded_non_public_voice_n
```

```text
threeway_n = positive_n + neutral_n + negative_n
各share = 各n / threeway_n
```

### 4.4 表示期間の確定

期間別母数はHTMLへ表示せず、グラフ作成前の内部QAにのみ使用する。

内部QAで、特定期間の構成比が少数の投稿に過度に左右されると判断した場合は、次の順で対応する。

```text
1. 隣接する期間と統合する
2. 消費税減税・食品値上げで同じ期間区分にそろえる
3. それでも安定した比較ができない期間はグラフ対象から外す
```

HTML上に「注意」「参考値」「低n」「データ不足」等の警告ラベルを付けて補うことはしない。0件の期間は0%として描画せず、事前に期間区分を調整する。

### 4.5 週次集計の位置づけ

週次集計は廃止しない。以下に使用する。

```text
・隔週集計の計算根拠
・急変が特定の1週間に集中していないかの確認
・監査CSV
・次回の継続比較
```

HTML本文には原則掲載しない。ただし、起点イベント直後の1週間に明確な変化があり、かつnが十分な場合だけ補足表として掲載できる。

### 4.6 月次集計の位置づけ

月次は5月、6月、7月途中の3点しかないため、主時系列グラフにはしない。

以下に使用する。

```text
・冒頭要旨の月次サマリーカード
・テーマ別の全期間構成比
・前月比較が成立する2回目以降の補助表
・継続データ
```

---

## 5. 集計前QAゲート

### 5.1 必須条件

```text
・sentiment_cluster_classified.csvが存在する
・全sentiment_cluster_idが一意である
・record mapの非重複行について、クラスタラベル結合率が100%である
・Prompt 1のclassification_coverage_rateが100%である
・S1・S7の期間開始日が2026-05-01として適用されている
・週次合計、隔週合計、月次合計、監査表の全期間nが一致する
```

### 5.2 再確認条件

以下の場合は最終レポートを確定せず、Prompt 1分類へ戻る。

```text
・クラスタラベル未結合が1件でもある
・S1またはS7のPublic Voice保持率が30%未満で、合理的な説明がない
・fallbackを理由に大量除外している
・mixed / unknown / uncertain / apply_to_cluster=falseが非重複eligible recordsの20%を超え、上位クラスタの再レビュー記録がない
・期間別母数が小さく、隣接期間を統合しても安定した構成比比較ができない
・代表投稿の内容と付与センチメントが矛盾している
```

QAに問題がある場合、見栄えを整えたレポートを作って問題を隠してはならない。

---

## 6. 前月比較の判定

主要論点ごとに以下から選ぶ。

```text
New
Continuing
Expanding
Intensifying
Stable
Fragmenting
Weakening
Dormant
Resolved
Reframed
Insufficient evidence
```

件数や構成比だけで状態を決めない。以下の意味変化を優先する。

```text
・新しい責任帰属
・新しい生活影響
・購買回避、節約、問い合わせ等への接続
・別主体・別カテゴリへの波及
・批判フレーズの形成
・理解、容認、期待、擁護の増加
・記事共有中心への移行
・前月見立てへの反証
```

クエリ、期間、対象媒体、分類方法が変わった場合は、件数の前月比較を行わず`Partially comparable`または`Not comparable`とする。

---

## 7. HTMLアウトプットの絶対ルール

### 7.1 指定HTMLをデザインの基準とする

最終HTMLは、指定された基準HTMLの以下を維持する。

```text
・最大幅約1180pxの白背景ページ
・青系グラデーションのヘッダー
・kicker / h1 / subtitle / noteのヘッダー構造
・左罫線付きh2
・card / callout / hypothesis / grid / twoのレイアウト
・badge / mini-metrics / quote / examplesの表現
・flowによる因果プロセス
・scenarioカード
・riskbox
・比較表、実例表、チェックリスト
・Yu Gothic / Hiragino Sans / Meiryo系フォント
・スマートフォン向けレスポンシブCSS
```

### 7.2 禁止事項

```text
・別のダッシュボード風デザインへ作り替える
・黒背景や強い装飾へ変更する
・章構成を短いサマリーだけへ圧縮する
・カードを大量に並べ、説明文を削る
・処理用語や内部コードを本文に表示する
・基準HTMLのCSSを無視してゼロからデザインする
・代表投稿リンクを単なる脚注一覧にまとめる
・グラフやカードに件数、n、母数、投稿数を表示する
・グラフ周辺に「注意」「参考値」「低n」等の警告ラベルを表示する
・グラフの仕組みや読み方を説明するメタ文章を掲載する
```

### 7.3 テンプレートの優先順位

```text
1. 指定HTMLのデザイン・章構成・情報密度
2. Prompt 0のレポート設計とストーリー
3. Prompt 1の定性分類・代表投稿
4. 本Promptのグラフ・集計要件
```

既存HTMLの文言や数値を流用してはならない。**構造とデザインだけを維持し、内容は今回データで全面更新する。**

---

## 8. 基準HTMLに沿った章構成

最終HTMLは、原則として以下の12章を維持する。

### 1. 冒頭要旨

基準HTMLと同じく、以下を配置する。

```text
・中心メッセージをhypothesis枠で提示
・重要ポイントを3枚のmetric cardで提示
・読み方または判断軸をcalloutで提示
```

3枚のカードは案件に応じて以下から選ぶ。

```text
・最も明確な反応
・生活者側の土台
・増幅要因
・前回からの変化
・今後の分岐条件
```

### 2. 各論点で確認された声の状況

この章の冒頭に、次の2つの隔週グラフを配置する。

```text
A. 消費税減税に対する生活者反応の隔週構成比
B. 食品値上げに対する生活者反応の隔週構成比
```

その後、基準HTMLと同様に論点別`signal-card`を配置する。

各グラフの直下には、次を配置する。

```text
・全期間または月次の構成比mini-metrics（割合のみ。件数は表示しない）
・ポジティブ、ニュートラル、ネガティブの代表投稿
・今回確認された反応構成の変化と、その背景となる主要な声
```

グラフ直下にも、期間別件数、全期間件数、母数、`n=`、警告ラベル、グラフの仕組みを説明する文章を置かない。

### 3. 声から見た批判化の流れ

基準HTMLの`flow`形式を用い、観測された声の接続を5段階前後で示す。

### 4. 批判フレーム別の声の特徴

表形式で、批判フレーム、観測された声、グラデーション、事業上の意味を整理する。

### 5. 今回想定局面への当てはめ

`two`レイアウトで、批判が立ち上がる条件と容認されやすい条件を対比する。

### 6. 想定シナリオ

基準HTMLの4枚の`scenario`カードを維持し、低・中・高・悪化リスクを整理する。

### 7. 主体別に見た問題化の入口

生活者、ニュース・メディア、SNS・掲示板、専門家・消費者視点等を表で整理する。

### 8. 対象カテゴリ固有の増幅要因

基準HTMLの`riskbox`を使用し、価格妥当性、広告、契約、容量等を整理する。

### 9. 事業部への示唆：明示すべき項目

領域、明示事項、根拠、避けたい見え方を表で示す。

### 10. 使いたい表現・避けたい表現

2列カードで具体的な表現例を示す。

### 11. 追加確認事項

確認事項、理由、未確認時のリスクを表で示す。

### 12. 実データ上の読み取りと限界

3列カードで以下を整理する。

```text
・最も使える根拠
・土台になる声
・直接接続の強弱
・表示・不透明さの論点
・カテゴリ固有リスク
・レポートの使い方
```

初回基準では前月変化を無理に独立章にせず、各章と冒頭要旨へ組み込む。2回目以降に重要な変化がある場合は、第1章または第12章内で明示する。

---

## 9. グラフ作成ルール

### 9.1 表現形式

主グラフは**100%積み上げ棒グラフ**を基本とする。

目的：

```text
・隔週ごとのポジティブ、ニュートラル、ネガティブの構成変化を比較する
・各期間を同じ100%の尺度で表示し、方向性の違いを見やすくする
```

### 9.2 必須表示

```text
・ポジティブ、ニュートラル、ネガティブの構成比
・隔週期間ラベル
・対象期間
・3分類の凡例
```

### 9.3 グラフ内・周辺で表示しないもの

```text
・各期間の件数、総件数、母数
・n=、threeway_n、有効n、投稿数、件数
・「注意」「参考値」「低n」等の警告表示
・sample_flagや内部QAステータス
・棒の上または下の数値ラベル
・件数の大小を読み取らせる補助表現
```

表示方法そのものを説明するメタ文章は記載しない。

グラフの前後には、図の操作方法や読み方ではなく、実際に確認された反応構成の変化と主要な声だけを書く。

### 9.4 グラフ直下の代表投稿

各テーマについて、原則として以下を掲載する。

```text
・ポジティブ 1〜2件
・ニュートラル 1〜2件
・ネガティブ 2〜3件
```

良質な例がない方向を水増ししない。URL、日時、要約、トーンを表示する。

---

## 10. 本文表現ルール

### 10.1 本文に出さないもの

```text
S1、S7等のSignalコード
cluster、record map、Prompt 1/2、fallback等の内部処理語
内部用の分類列名
取得件数を世論全体の規模と誤認させる表現
「大多数」「社会全体」「世論が○%増えた」等の過度な一般化
```

### 10.2 本文に出すもの

```text
・現時点の中心結論
・前回から変わった点、変わっていない点
・分析対象期間が2026年5月1日以降であること
・隔週構成比の変化
・月次の要約値
・実際に確認された生活者の声
・批判、不満、懐疑、諦め、理解、期待、擁護の濃淡
・新規、継続、強まり、弱まり、再構成の判断
・判断の確度と限界
・企業批判へ接続する条件
・沈静化、再燃の条件
・当社グループへの示唆
・次月までの監視・対応事項
```

### 10.3 件数の非表示

最終HTMLでは、全期間・月次・隔週のいずれについても件数を表示しない。

件数の定義、除外条件、分母、結合率、母数保持率は、`methodology_notes.md`と`denominator_audit.csv`にのみ記録する。

HTML本文では、構成比を世論全体の比率または投稿量の大小として説明せず、取得・分類条件下で確認された反応構成として扱う。

---

## 11. continuity_context_next.json

### 11.1 更新原則

```text
・前月ファイルを単純コピーしない
・今月データで各論点を更新する
・古い見立てはprevious_assessmentまたは履歴へ移す
・新規論点に一意のtopic_idを付ける
・弱まった論点も即削除せずDormant / Resolvedとして残す
・再燃条件をwatch_conditionsへ残す
・代表例はexample_idまたはURL参照で記録する
・個人情報や長文本文を保存しない
・分類方法、開始日、隔週表示への変更をmethodological_continuity_notesへ記録する
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
report_theme
current_period
previous_period
observation_cycle
source_scope_summary
query_continuity_status
analysis_start_date
primary_chart_granularity
created_at
```

`primary_chart_granularity`は`biweekly`とする。

---

## 12. 出力

Prompt 2は、作業ファイルを個別に返さず、以下を格納したZIPを1ファイル生成する。

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
└─ methodology_notes.md
```

### 12.1 biweekly_trend_data.csv

```text
period_start
period_end
period_label
signal_id
report_topic_label
positive_n
neutral_n
negative_n
threeway_n
positive_share
neutral_share
negative_share
mixed_unknown_n
excluded_non_public_voice_n
```

### 12.2 weekly_trend_data.csv

隔週集計の裏付け・監査用として、従来の週次データを保持する。HTMLの主グラフには原則使用しない。

### 12.3 monthly_summary_data.csv

Signal・暦月ごとに3分類n・構成比を集計する。2026年5月は5月1日以降だけを対象とする。

### 12.4 denominator_audit.csv

Signal別および`ALL`で以下を出力する。

```text
signal_id
raw_records
nonduplicate_records
cluster_label_joined_records
period_in_scope_records
theme_relevant_records
public_voice_records
threeway_sentiment_records
mixed_unknown_records
excluded_media_official_corporate
excluded_fact_only
excluded_irrelevant
excluded_apply_false
cluster_join_rate
public_voice_retention_rate
qa_status
qa_notes
```

### 12.5 reference_examples_used.csv

```text
example_id
signal_id
report_topic_label
source_url
author_or_source
timestamp
post_summary
notable_phrase
voice_direction
role_in_report
continuity_role
```

### 12.6 report_brief.md

```text
・中心結論
・対象期間と構成比の算定条件
・隔週グラフから確認できる変化
・消費税減税の主要な受け止め
・食品値上げの主要な受け止め
・2テーマが接続する条件
・継続している強い生活者反応
・新規・強まり・弱まり・反証
・限定的だが監視すべき声
・想定シナリオ
・当社グループへの示唆
・次月までの確認事項
```

### 12.7 methodology_notes.md

```text
・使用したPrompt 1分類ファイル
・全件対応表との結合方法
・重複除外方法
・最終分母の条件
・期間開始日
・週次から隔週への統合方法
・初回・最終部分期間の扱い
・期間区分を確定した内部QAルール
・指定HTMLテンプレートの使用
・比較可能性の制約
・QA結果と再レビューの有無
```

---

## 13. continuity_change_log.md

運用担当者が読める言葉で、以下を簡潔に記載する。

```text
・新規追加した論点
・状態を変更した論点
・見立てを修正した論点
・Dormant / Resolvedへ移した論点
・追加・削除した監視条件
・追加・削除した監視キーワード
・比較可能性に影響する取得・分類条件変更
・主グラフを週次から隔週へ変更したこと
・ナラティブマスター更新候補
・次月に必ず確認すべき事項
```

---

## 14. 最終品質チェック

```text
□ 指定HTMLをデザイン・章構成・情報密度の基準として使用した
□ 基準HTMLのCSSクラスと視覚構造を維持した
□ 既存HTMLの古い数値や文章を流用していない
□ sentiment_cluster_classifiedを全件対応表へ結合した
□ 非重複行のクラスタラベル結合率が100%である
□ weekly_trend_raw_basisを最終センチメントとして使っていない
□ グラフ対象を2026年5月1日以降に限定した
□ S1・S7で同じ期間を使用した
□ 主グラフを隔週100%積み上げ棒グラフにした
□ 週次合計、隔週合計、月次合計、監査表nが一致する
□ HTMLのグラフ・本文・注記に件数を表示していない
□ 「注意」「参考値」「低n」等の警告ラベルを表示していない
□ グラフの読み方を説明するメタ文章を記載していない
□ 期間別母数は監査ファイル内でのみ確認した
□ 月次は主グラフではなくサマリーとして使用した
□ Fact Onlyをneutralに含めていない
□ Media / Official / Corporate / PRを生活者反応に含めていない
□ apply_to_cluster=falseを分母から除外し、監査表へ残した
□ Public Voice数と3分類採用nを混同していない
□ 代表投稿の方向とセンチメントが一致している
□ 前月からの変化を件数だけで説明していない
□ 取得条件変更による比較可能性を記録した
□ レポート本文とcontinuity_context_next.jsonが矛盾していない
□ 最終成果物はprompt2_output ZIPのみである
```
