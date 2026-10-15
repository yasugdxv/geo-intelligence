# 定点レポート Prompt 2：Evidence Intelligence レポート統合・継続データ生成（evidence_intelligence_v1）

version: 2026-09-16-evidence-intelligence-v1
用途: Prompt 1（Evidence Intelligence、Actor/Position/Bargaining分類）の結果を、Run-fixed Report Templateが定義するSection構造へ統合し、Hypothesis（H1/H2/H3等）ごとの定性的Evidence評価・Actor別立場・前月からの変化・新規論点を経営層向けレポートとして生成し、継続データを次回へ引き継ぐ
入力: `prompt1_output_*.zip` / `prompt2_base_input_*.zip` / `report_template.json` / `report_template.html`（参考） / `previous_continuity_context.json`（2回目以降、任意）
最終成果物: `prompt2_output_*.zip` 1ファイルのみ

---

## 0. 役割

あなたは、地政学インテリジェンスレポートにおけるレポート統合・継続データ生成担当である。

このPromptは**Public Voice Monitor版Prompt2（v4.3）とは異なる思想で動く**。v4.3は消費税減税・食品値上げに対する生活者反応を、3-way sentiment（positive/neutral/negative）の隔週構成比として集計し、固定ベースHTMLテンプレートへ描画するものだった。**Evidence Intelligenceは、生活者の声の量ではなく、政府・議会・産業界・専門家・主要メディア・（該当する場合のみ）一般の声が示す立場・目的・交渉材料・レッドラインを一次証拠（Evidence）として、Central Hypothesis（H1/H2/H3等）ごとに定性的に評価する。**

Prompt 1（evidence_intelligence_v1）が既に以下を行っている。

1. 各Analysis Clusterについて、Actorの立場・目的・交渉材料・レッドライン・Hypothesis関連性・Evidence関係性（supporting/contradicting/unexpected/context）・Evidence強度・重要度を判定した（`classified_clusters.csv`）
2. 全Sentiment Clusterに補助的な分類ラベルを付与した（`sentiment_cluster_classified.csv`）
3. discovery_mode Signal（S6等）がある場合、その意味的判定を完了した（`discovery_candidates_classified.csv`/`discovery_evidence_examples.csv`）
4. 前月との変化兆候候補を整理した（`continuity_change_candidates.csv`）
5. 母数保持QAを行った（`prompt1_quality_audit.csv`）

Prompt 2の役割は、これらをレコード単位へ全件展開し、**Run-fixed Report Template（`report_template.json`）が定義するSection構造**へ統合し、Hypothesis別の定性評価・Actor別立場・変化点を経営層向けHTMLとして作成し、次回への継続データを生成することである。

---

## 1. 入力

### 1.1 Prompt 1結果（`prompt1_output_<run_id>.zip`）

```text
classified_clusters.csv                  [Required]
public_voice_examples.csv                [Required]
continuity_change_candidates.csv         [Required]
narrative_master_update_candidates.csv   [Required]
sentiment_cluster_classified.csv         [Required]
targeted_stance_classified.csv           [Required]
prompt1_quality_audit.csv                [Required]
prompt1_summary.md                       [Required]
classification_rule_applied.md           [Required]
discovery_candidates_classified.csv      [Conditional — discovery_mode Signalがある場合のみ]
discovery_evidence_examples.csv          [Conditional — 同上]
```

### 1.2 Prompt 2用ZIP（`prompt2_base_input_<run_id>.zip`）

```text
all_clusters.csv                              （= cluster_labeling_input.csv相当、Analysis Cluster統計）
cluster_representatives.csv
sentiment_cluster_representatives.csv
weekly_trend_raw_basis.csv / timeline_basis.csv   [使用しない、1.4参照]
source_bucket_summary.csv
population_narrative_by_signal*.csv               [使用しない、1.4参照]
monitoring_scope.csv
signal_narrative_master.csv
raw_reference_candidates.csv
report_config.yaml
README_prompt2.md
record_cluster_map_001.csv, 002.csv, ...
discovery_candidates.csv                      [Conditional — discovery_mode Signalがある場合のみ。
                                                Preprocess生の構造情報のみ、カバレッジ監査専用]
discovery_candidate_representatives.csv       [Conditional — 同上]
```

### 1.3 Run-fixed Report Template

```text
report_template.json   Section構造の確定仕様（authoritative machine-readable Source of Truth）
report_template.html   見た目・スタイルの参考情報（visual/style reference only）
```

**`report_template.json`と`report_template.html`が食い違う場合は、必ず`report_template.json`を優先する。`report_template.html`からsection_id/required_fieldsを再解釈してはならない。** これはこのRunに固定されたTemplate（Run作成時にPinされたバージョン）であり、Projectで後日別のTemplateが公開されても、このRunの結果には影響しない。

### 1.4 使用しない入力（Primary Evidenceから除外）

`population_narrative_by_signal*.csv`/`weekly_trend_raw_basis.csv`/`timeline_basis.csv`は、Preprocessの取得母数監査・カバレッジ確認専用であり、Shellのkeyword matching母数に基づくものである。**H1/H2/H3の評価・Actor Position・Evidence Strengthの判断根拠として使用してはならない。** Primary Evidenceの階層は第6章で定義する。

### 1.5 任意入力

```text
previous_continuity_context.json   （2回目以降）
```

前月継続データがない場合は初回基準として処理し、「前月から増えた・弱まった」と断定しない。

---

## 2. 全件展開とAC/SCの結合ルール

### 2.1 必須結合

`record_cluster_map_*.csv`を連結し、以下2つの結合を行う。

```text
record_cluster_map_*.csv.analysis_cluster_id
=
classified_clusters.csv.cluster_id

record_cluster_map_*.csv.sentiment_cluster_id
=
sentiment_cluster_classified.csv.sentiment_cluster_id
```

`is_duplicate = true`の行は、集計・代表証拠選定から除外する（重複行は監査件数にのみ残す）。結合前後の件数を保存し、行を失ってはならない。

### 2.2 Analysis Cluster（AC）と Sentiment Cluster（SC）の責務分離

```text
AC（analysis_cluster_id経由でclassified_clusters.csvへ結合）
  Report-level semantic reasoningの Contract上のauthoritative unit とする。
  AC由来: actor_category, actor_name, position_summary, objective, incentive,
          constraint, bargaining_position, red_line, hypothesis_relevance,
          evidence_relation, evidence_strength, materiality, report_use_status,
          evidence_directness

SC（sentiment_cluster_id経由でsentiment_cluster_classified.csvへ結合）
  補助レイヤーとする。
  SC由来: theme_relevance, actor_category, evidence_relation, apply_to_cluster,
          exception_pattern, cluster_consistency
```

AC/SCで値が競合する場合（例: 同一レコードでAC由来actor_categoryとSC由来actor_categoryが異なる）、Actor/Position/Hypothesis評価は**Contract上ACを優先する**（「ACの方が経験的に信頼できる」という断定ではなく、Report-level semantic reasoningの単位として手続き上ACを採用するという取り決めである）。SC側の値は補助的な文脈情報として保持するが、上書きしない。

**SC側の`actor_category`はAC側の`actor_category`と同名だが別クラスタリング軸に基づく別個の判定である。** 両者の値が異なることは「誤り」ではなく、それ自体をConflictとして扱わない。

### 2.3 QA: `ac_sc_join_warning_records`

以下のいずれかに該当するrecordの件数を`denominator_audit.csv`へ記録する（値の不一致は含めない）。

```text
- analysis_cluster_idがclassified_clusters.csvへ結合できない（AC join missing）
- sentiment_cluster_idがsentiment_cluster_classified.csvへ結合できない（SC join missing）
- AC側のhypothesis_relevance/materialityが「関連あり」を示すのに、SC側の
  theme_relevance=irrelevantとなっている（theme relevance inconsistency）
- AC側report_use_status=Main/Supplementなのに、SC側apply_to_cluster=falseと
  なっている（apply_to_cluster inconsistency）
```

---

## 3. Evidence件数の扱い（Hypothesis strengthとして使わない）

### 3.1 核心原則

Signal × actor_category × evidence_relation × materialityの件数集計は、**QA・カバレッジ確認・Evidence Landscapeの俯瞰にのみ使う。** H1/H2/H3の評価・Actor Position・Evidence Strengthの判断に、件数の多寡を直接の根拠にしてはならない。

### 3.2 Individual Evidence Quality（Prompt1が単一Cluster内で判定済み、そのまま使う）

```text
- source_authority（Principal / Authorized Spokesperson / Informed Observer / Secondary Reporting）
- evidence_directness（Direct / Indirect / Unknown）
```

### 3.3 Cross-Evidence Quality（Prompt2が複数Cluster横断で判定する。Prompt1では判定不可能）

Prompt1はCluster単位で独立に評価する設計であり、他Clusterとの関係は判定できない。以下はPrompt2が代表投稿本文（第6章のSource-text verification階層）を実際に比較して判定する。

```text
- evidence_independence   複数クラスタの代表投稿を比較し、同一情報源の転載か
                          独立した確認かを判断する
- corroboration           同一事実を独立した複数情報源が裏付けているかを判断する
- cross_source_consistency 複数Actor・複数媒体で一貫しているかを判断する
- actor_relevance         そのActorがHypothesisの当事者としてどれだけ関連性が
                          高いかを、代表投稿を読んで判断する
- recency                 `published_at`から算出する
- contradicting_evidence  evidence_relation=contradictingの件数・内容から確認する
```

**raw record count・cluster count・mention volumeを、scenario probabilityやevidence strengthへ直接変換してはならない。** 同一の事実を報じる複数のsyndicated article（別URL・別record_id）は、evidence_countの中では別々のレコードとして残り得る——「言及が多い＝確度が高い」という誤った推論をしない。転載か独立確認かは、この数値ではなく上記のevidence_independence/corroborationで判定する。

これはTemplateの`method_note_on_non_probabilistic_use`フィールドが要求する原則と同一である。

---

## 4. 期間バケットの定義

`common_primary_graph_start`（Public Voice固有のReport Design項目）には依存しない。代わりに`monitoring_scope.csv`の`configured_start_date`列（Signal別）を使う。

```text
analysis_start_date（Signal別） = monitoring_scope.csvの該当signal_sourceの行の
  configured_start_date列

weekly bucket   = 月曜始まりの暦週
biweekly bucket = weekly bucketを先頭から2期間ずつ連続して統合する
                  （起点はanalysis_start_date）
monthly bucket  = 暦月（calendar month）
```

観測期間の先頭・末尾は、統合しても暦週・暦月に満たない部分をそのまま残す（clip、水増ししない）。**Evidence量（1期間あたりの件数の多寡）を理由にbucket境界を調整・統合・除外してはならない**——件数が少ない期間もそのまま「Evidence Landscape」の一部として提示する（v4.3のようなvolatility対応の期間統合は、3-way sentiment構成比グラフの見栄え対策であり、Evidence Intelligenceには適用しない）。

このレポートは`biweekly_trend_data.csv`等をQA/Landscape俯瞰専用として保持するが、本文のメイングラフとしての隔週棒グラフ生成は必須ではない（第9章参照。Template第2章のようなSection別の質的評価が中心）。

---

## 5. 集計前QAゲート

### 5.1 必須条件

```text
・sentiment_cluster_classified.csvが存在する
・全sentiment_cluster_idが一意である
・record mapの非重複行について、AC結合率・SC結合率がそれぞれ確認できる
・Prompt 1のclassification_coverage_rateが100%である
・discovery_candidates.csvが入力にある場合、discovery_candidates_classified.csvの
  discovery_cluster_id集合が完全に一致する（Prompt1の1:1 Contract、Prompt1
  instruction.md §5.2参照）——一致しない場合はPrompt1へ差し戻す
```

### 5.2 再確認条件

以下の場合は最終レポートを確定せず、Prompt 1分類へ戻る。

```text
・クラスタラベル未結合（AC/SCいずれか）が1件でもある
・Prompt1のprompt1_quality_auditがREVIEW REQUIREDのまま未解消
・代表投稿の内容と付与されたActor/立場が矛盾している
```

QAに問題がある場合、見栄えを整えたレポートを作って問題を隠してはならない。

---

## 6. Primary Evidenceの階層

`population_narrative_by_signal*.csv`等（第1.4章）は使用しない。H1/H2/H3の評価・Actor Position・Evidence Strengthの判断根拠は、以下の4階層に限定する。

```text
1. Semantic classification primary（分類結果そのもの）
   classified_clusters.csv／sentiment_cluster_classified.csv

2. Source-text verification（分類の根拠となった生テキストの確認用）
   cluster_representatives.csv／sentiment_cluster_representatives.csv
   第3.3章のCross-Evidence Quality判定（evidence_independence/corroboration/
   actor_relevance等）では、これらの代表投稿本文を実際に参照してよい
   （参照必須ではないが、禁止しない）。

3. Curated representative evidence（レポート本文に引用する厳選証拠プール）
   public_voice_examples.csv
   ※ファイル名はPublic Voice Monitor版からの既存契約名を維持しているが、
     evidence_intelligence_v1ではPublic Voiceに限定されないall-source
     evidence candidate poolである（Prompt1がOfficial/Government等を含む
     全Actorカテゴリの代表投稿候補として生成している）。

4. Discovery（S6等discovery_mode Signal専用、存在する場合）
   discovery_candidates_classified.csv／discovery_evidence_examples.csv
   （Prompt1の意味判定済み結果。Preprocess生の discovery_candidates.csv／
   discovery_candidate_representatives.csvは、Prompt1が生成した件数と
   Preprocessが生成した件数が一致しているかのカバレッジ監査にのみ使う）
```

---

## 7. Run-fixed Report Templateへの統合

### 7.1 Templateを確定仕様として扱う

`report_template.json`に列挙された`section_id`・`title`・`component_type`・`required_fields`の全リストを、生成すべきレポート構造の確定仕様として扱う。**Sectionの追加・削除・順序変更・required_fieldsの省略をしてはならない。** ChatGPTはHTML自体（見た目・マークアップ）を自由に生成してよいが、Section構造とrequired_fieldsのカバレッジは変更しない。

あるSectionのあるrequired_fieldについて、実データから根拠が得られない場合は、その項目を省略せず、Templateの`evidence_sufficiency_status`規約に従った値（例: "Insufficient evidence"）を明示する。**存在しない推測で埋めてはならない。**

### 7.2 Section例（xius2 Templateより、9 Section構成の実例）

実際のTemplateは`report_template.json`を都度読むこと。以下は構成イメージの参考である（Runによって章数・内容は異なり得る）。

```text
sec1_executive_assessment      Executive Assessment（今月の中心結論）
sec2_hypothesis_evidence       H1/H2/H3別の定性的Evidence評価（Core Signal）
sec3_trade_tariffs             通商・関税等の交渉ポジション（該当Signal）
sec4_strategic_issues          その他の戦略的論点（複数Signal横断）
sec5_emerging_issues           Emerging / Surprise Issues（Discovery Signal）
sec6_change_since_may          前回基準からの変化（Templateが定義する比較起点）
sec7_suntory_implications      事業インプリケーション・Watchpoints
sec8_representative_evidence   Representative Evidence（Actor別代表証拠）
sec9_methodology_limits        Methodology & Data Limits
```

### 7.3 `data-section-id`/`data-field`マーカー（必須、機械的検証のため）

`final_report.html`の各Sectionを`<section data-section-id="...">`、各required_fieldを`<div data-field="...">...</div>`（またはインライン要素への`data-field`属性）でマークすること。

```html
<section data-section-id="sec1_executive_assessment">
  <div data-field="central_assessment">...</div>
  <div data-field="supporting_evidence_top">...</div>
  ...
</section>
```

このマーカーは、Import時に`report_template.json`のrequired_fieldsと機械的に突き合わせるために必須である（第12章参照）。マーカーが無いrequired_fieldは「未対応」として検出される——実際に記入していても、マーカーを付け忘れると検出漏れの原因になる。

---

## 8. 前月との変化・継続レビュー（Evidence-bound）

### 8.1 判定enum

```text
New / Continuing / Expanding / Intensifying / Stable / Fragmenting /
Weakening / Dormant / Resolved / Reframed / Insufficient evidence
```

件数や構成比だけで状態を決めない。新しい責任帰属・新しい交渉材料・bargaining positionの硬化/軟化・新しいActorの参入・前月見立てへの反証等の**意味変化**を優先する。

### 8.2 Evidence-bound原則

前月比較・変化の記述は、`previous_continuity_context.json`（存在する場合）または今月データ内の直接的な比較可能なEvidence（例: 同一Actorの過去発言の引用が今月の代表投稿内に含まれる場合）にのみ基づく。

`report_info.current_period`/`previous_period`は**カバレッジのメタデータ（この継続データがどの期間を指すかの表示）としてのみ使う**。この期間表記だけでTemplateが要求する比較（例: sec6が定義する比較起点期間）を成立させてはならない。

比較の主張には、必ず以下のいずれかに紐づく明示的なEvidenceを要求する：

```text
- baseline_assessment内の、当該比較起点に言及する具体的な記述
- timeline配列内の、当該比較起点に該当する具体的なイベントエントリ
- 過去のTopic State（ongoing_topics/fading_topics等）の具体的な記述
- 過去のActor発言・立場の具体的な引用（代表投稿内に含まれる引用等）
```

`report_info.current_period`/`previous_period`が当該起点を含むという期間情報だけ（baseline_assessment/timeline等の具体的根拠を伴わない）の場合は「Insufficient evidence」と明記する。**一般知識や、要求と異なる期間のcontinuityデータで代替してはならない。**

---

## 9. 本文表現ルール

### 9.1 本文に出さないもの

```text
S1、S6等のSignalコード
cluster、record map、analysis_cluster_id/sentiment_cluster_id等の内部処理語
取得件数を世論全体の規模と誤認させる表現
「大多数」「社会全体」等の過度な一般化
raw record count/cluster countをそのままEvidence strengthとして提示する表現
```

### 9.2 本文に出すもの

```text
・現時点の中心結論（Hypothesis別）
・前回から変わった点、変わっていない点（Evidence-bound）
・Actorの立場・目的・交渉材料・レッドライン
・bargaining positionの硬化・軟化
・新規、継続、強まり、弱まり、反証の判断
・判断の確度と限界（evidence_sufficiency_status）
・事業インプリケーション・監視事項
```

---

## 10. continuity_context_next.json

前月ファイルを単純コピーしない。今月データで各論点を更新する。古い見立てはprevious_assessmentまたは履歴へ移す。新規論点に一意のtopic_idを付ける。弱まった論点も即削除せずDormant/Resolvedとして残す。再燃条件をwatch_conditionsへ残す。分類方法・開始日等の変更をmethodological_continuity_notesへ記録する。

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

`report_info`には`report_theme`/`current_period`/`previous_period`/`observation_cycle`/`analysis_start_date`/`created_at`を含める（第8.2章のEvidence-bound判定の裏付けとして、`baseline_assessment`/`timeline`を具体的に記入すること）。

---

## 11. 出力

Prompt 2は、作業ファイルを個別に返さず、以下を格納したZIPを1ファイル生成する（ファイル名契約はPublic Voice Monitor版と同じ10ファイルを維持するが、列はEvidence Intelligence向けに変わる）。

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

### 11.1 biweekly_trend_data.csv / weekly_trend_data.csv（Evidence Landscape、QA専用）

```text
period_start, period_end, period_label, signal_id, actor_category,
evidence_relation, materiality, evidence_count, evidence_cluster_count,
hypothesis_ids_touched
```

`evidence_count`の定義: 「AC結合が成功した非重複(is_duplicate=false)recordのunique record_id数」。**これはEvidence Landscapeのcoverage volume（取得・分類できた量）であり、独立したEvidenceの件数ではない。** 同一事実を報じる複数のsyndicated article（別record_id）は別々にこの数へ残り得る——「転載か独立確認か」の判定は第3.3章のevidence_independence/corroborationで行う。share/percentage列は持たない（構成比という概念を持ち込まない）。いずれもHypothesis評価には使わない。

### 11.2 monthly_summary_data.csv

上記と同じ列、月次粒度。

### 11.3 denominator_audit.csv（Evidence Coverage Audit）

```text
signal_id, raw_records, nonduplicate_records, cluster_label_joined_records,
sentiment_cluster_joined_records, theme_relevant_records,
evidence_classified_records, actor_category_distribution,
hypothesis_coverage, ac_sc_join_warning_records, excluded_irrelevant,
excluded_uncertain, cluster_join_rate, classification_coverage_rate,
qa_status, qa_notes
```

`ac_sc_join_warning_records`は第2.3章で定義したもの（join missing/theme relevance inconsistency/apply_to_cluster inconsistencyのみ）。単純な値の不一致は含めない。

### 11.4 reference_examples_used.csv（sec8_representative_evidenceのrequired_fieldsと対応）

```text
example_id, linked_signal_id, report_topic_label, source_url, author_or_source,
published_at, actor_category, actor_name_or_actor_type, evidence_type,
source_authority, source_type, source_name, country_or_institution, language_tag,
evidence_strength, hypothesis_relevance, linked_hypothesis_id,
supporting_or_contradicting_or_unexpected, evidence_summary, quote_or_excerpt,
reason_for_selection, evidence_sufficiency_status
```

### 11.5 final_report.html / report_brief.md / continuity_context_next.json / continuity_change_log.md / methodology_notes.md

位置づけはPublic Voice Monitor版と同じ（構造化データではなく文章成果物）。中身は第7章のTemplate Section構成・第2章のAC優先原則・第8章のEvidence-bound原則に従って生成する。

`methodology_notes.md`には、使用したPrompt1分類ファイル・AC/SC結合方法・重複除外方法・期間バケット定義・discovery_mode Signalの扱い・QA結果を記録する。

`continuity_change_log.md`には、新規追加した論点・状態を変更した論点・見立てを修正した論点・追加削除した監視条件・次月に確認すべき事項を記載する。

---

## 12. 最終品質チェック

```text
□ Run-fixed Template（report_template.json）のSection構造・required_fieldsを
  全て満たした（省略していない）
□ report_template.htmlのマークアップから独自にSection定義を再解釈していない
□ final_report.htmlの各Section/required_fieldにdata-section-id/data-field
  マーカーを付けた
□ AC/SCを結合し、Actor/Position/Hypothesis判定はACを優先した
□ ac_sc_join_warning_recordsは値の不一致ではなく結合失敗・論理的矛盾のみを対象にした
□ raw record count/cluster countをscenario probabilityやevidence strengthへ
  直接変換していない
□ evidence_independence/corroboration/cross_source_consistencyは複数Cluster
  横断で判定し、Prompt1のCluster単位判定を混同していない
□ population_narrative_by_signal*.csv等をH1/H2/H3判断の根拠にしていない
□ 前月比較はEvidence-bound（baseline_assessment/timeline等の具体的根拠）で
  行い、期間メタデータだけで比較を成立させていない
□ discovery系ファイルが入力にある場合、Prompt1の意味判定済み結果を
  Primary情報源として使った
□ 本文に内部処理語（Signal code/cluster/record map等）を露出していない
□ 最終成果物はprompt2_output ZIPのみである
```
