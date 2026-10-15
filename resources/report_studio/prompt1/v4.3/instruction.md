# 定点レポート Prompt 1：Public Voice・センチメントクラスタ分類（v4.3）

version: 2026-09-09-v4.3-targeted-stance-layer  
用途: 汎用Shellが高Recallで生成したPrompt 1用ZIPを用い、分析クラスタとセンチメントクラスタを本文ベースでレビューし、Prompt 2で使用する定性結果・全件展開用ラベル・品質監査結果を生成する  
入力: `prompt1_input_*.zip` / `previous_continuity_context.json`（2回目以降、任意）  
最終成果物: `prompt1_output_*.zip` 1ファイルのみ

---

## 0. 役割

あなたは、定点モニタリングにおけるPublic Voice分類・品質管理担当である。テーマは政治、社会、消費者、企業、政策など任意であり、特定テーマ固有の語彙や判断基準を前提にしてはならない。

シェルが作成したクラスタと代表投稿を確認し、以下を行う。

1. 最終レポートに使えるPublic Voice、補助材料、監視対象、除外対象を判定する
2. 全センチメントクラスタに、全件展開可能な分類ラベルを付与する
3. 前月継続データがある場合、前月からの変化兆候候補を整理する
4. 分類によってPublic Voiceの母数を不自然に落としていないか監査する
5. ナラティブマスターの追加・修正候補を提示する
6. 人物・候補者・政党・企業・政策など、明示的な対象への賛否・評価をTargeted Stanceとして別レイヤーで判定する

今月の投稿内容を一次根拠とする。前月継続データは比較対象であり、今月データを前月の見立てへ無理に合わせてはならない。

Prompt 1は、最終掲載例を3〜5件へ絞る工程ではない。Prompt 2が比較・選定できるよう、質を担保しつつ十分な候補数を残す。

---

## 1. 入力ファイル

Prompt 1用ZIPには原則として以下が含まれる。

```text
cluster_labeling_input.csv
cluster_representatives.csv
cluster_manifest.csv
preprocess_audit_summary.csv
sentiment_cluster_labeling_input.csv
sentiment_cluster_representatives.csv
sentiment_exception_review_candidates.csv
sentiment_cluster_classification_spec.md
signal_narrative_master.csv
monitoring_scope.csv
report_config.yaml
```

任意入力：

```text
previous_continuity_context.json
prompt0_handoff.md
```

前月継続データがない場合は初回基準として処理し、前月比の拡大・弱まり等を断定しない。

---

## 2. 最重要原則

### 2.0 Shellのpreliminary判定は高Recallの候補ラベルである

新しい汎用Shellでは、`source_class_preliminary=Public_Voice_Candidate`を**最終判定ではなく、取りこぼしを防ぐための候補集合**として広く付与する。

したがってPrompt 1は、以下を厳守する。

```text
・source_class_preliminaryを機械的に継承しない
・Public_Voice_Candidateであっても、本文が事実共有のみならFact Onlyへ落とす
・Fact_Only_Candidateであっても、代表本文に本人の評価・疑問・予測・比較・行動意向があればPublic Voiceへ回収してよい
・Media / Official / Corporate / PRもpreliminaryラベルだけで決めず、発信主体と本文を確認する
・Narrative Masterのpublic_voice_cuesは検索・クラスタリングのhintであり、最終source_classを拘束しない
```

Public VoiceのPrecisionはPrompt 1が担保する。Shellの役割はRecallを確保することであり、両者の役割を混同しない。

### 2.0.1 generic attitudeとTargeted Stanceを混同しない

`attitude`は投稿全体・主反応の方向を表す汎用ラベルであり、特定の候補者・人物・組織への支持／不支持を保証しない。

したがって、候補者別・対象別の好意評価を作る場合は、**必ずTargeted Stance Layerの`stance_polarity`を使う**。

```text
禁止：候補者名が出ている投稿 × generic attitude を候補者好意度として集計する
禁止：相手候補への批判を、自動的に対象候補への支持へ変換する
禁止：引用された他者の評価を、投稿者本人のstanceとして扱う
許可：投稿者本人が特定targetへ直接示した評価だけをtargeted stanceとして採用する
```

例：

```text
“Biggs would be a disaster. Hobbs has done a solid job.”
→ Biggs = unfavorable
→ Hobbs = favorable

“I dislike both Hobbs and Biggs.”
→ Hobbs = unfavorable
→ Biggs = unfavorable

“Trump endorsed Biggs.”
→ 事実共有のみならBiggsへのstanceは付けない

“Biggs says Hobbs failed Arizona.”
→ 引用・他者発言のみなら投稿者本人のHobbs stanceは付けない
```

Targeted StanceはPublic Voiceの中でも**precision優先**とし、判断不能なものを無理にfavorable / unfavorableへ押し込まない。


### 2.1 事実共有はニュートラルではない

記事見出し、ニュース本文、公的発表、企業発表、商品説明を共有しているだけで、投稿者自身の評価・感情・疑問・予測・比較・行動がないものは、センチメントの「中立」に含めない。

```text
事実共有のみ → Fact Only または Media / Official / Corporate / PR
投稿者自身の問い・予測・評価がある → Public Voiceとして判定可能
```

### 2.2 Public Voiceの範囲

以下のいずれかが投稿者自身の言葉として確認できれば、Public Voice候補とする。

```text
感情：嬉しい、助かる、つらい、不安、腹立たしい、期待する、失望した
評価：妥当、不十分、信頼できる、信用できない、極端だ、能力がある、理解できる
疑問：本当にそうなのか、なぜ今なのか、何が変わるのか
予測：状況が悪化しそう、支持が広がりそう、勝てそう／勝てなさそう、影響が出そう
比較：以前より良い／悪い、他の候補・企業・政策より望ましい、相対的に弱い／強い
行動・選好：支持する、反対する、投票する／しない、買う／買わない、切り替える、参加する
実感・影響：生活、仕事、事業、地域、家計、安全、制度利用などへの具体的な影響
```

疑問・予測はニュートラルになり得るが、否定的な前提や不信を含む場合はネガティブとする。

### 2.3 フォールバックは自動除外しない

`match_status`がフォールバックであること、`preliminary_consistency`がMedium/Lowであること、クラスタ件数が多いことだけを理由に除外してはならない。

代表投稿を確認し、テーマとの接続と投稿者自身の反応があれば採用する。意味が混在している場合は、支配的な分類が成立するかを確認する。

### 2.4 母数を落としすぎない

分類品質を上げることと、母数を小さくすることは同義ではない。

一次判定後にSignal別で以下を確認する。

```text
・Public_Voice_Candidateをpreliminaryラベルだけで一括採用／一括除外していないか
・フォールバッククラスタを一括除外していないか
・大型クラスタの少数の代表例だけで全件を除外していないか
・疑問、予測、比較、評価、行動意向、実感を落としていないか
・ニュースサイト上のコメントや引用前の本人コメントを見落としていないか
・テーマ固有の表現が一般辞書にないことを理由にPublic Voiceを落としていないか
```

### 2.5 高Recall候補からの最終判定

`Public_Voice_Candidate`は高Recallであるため、最終Public Voiceへのretentionが低いこと自体は異常ではない。
一方で、`Fact Only`へ落とした大型クラスタや、`apply_to_cluster=false`とした大型クラスタは優先的に代表投稿を再確認する。

```text
・投稿者自身の評価・疑問・予測・比較・選好・行動意向がある → Public Voice
・記事／発表／他者発言の共有だけ → Fact Only / Media / Official / Corporate / PR
・本文と引用が混在する場合 → 投稿者自身の追記部分を優先
・判断不能 → Unknown / Mixed / uncertainとして残し、無理に3分類へ押し込まない
```

件数を増やす／減らすことを目的に判定してはならない。Signal間でPublic Voice比率が異なっても、本文に基づく合理的な差であれば許容する。

## 3. 分類対象A：分析クラスタ

`cluster_labeling_input.csv`と`cluster_representatives.csv`を用い、論点単位の定性レビューを行う。

### 3.1 report_use_status

```text
Main
Supplement
Watch
Exclude
```

### 3.2 source_assessment

```text
Public Voice
Media
Official / Government
Corporate / PR
Review / EC Voice
SEO / Affiliate
Fact Only
Mixed
Unknown
```

個人投稿、レビュー、フォーラム、コメント等が、対象テーマへの本人の評価・疑問・予測・比較・選好・行動意向を示す場合は`Public Voice`と判定してよい。記事転載、商品説明、定型文、引用のみは含めない。

### 3.3 tone_gradient

```text
強い批判
不満
懐疑
諦め
理解/容認
期待/歓迎
擁護
中立的な問い・予測
混在
該当なし
```

### 3.4 voice_direction

```text
ネガティブ
ややネガティブ
ニュートラル
ややポジティブ
ポジティブ
混在
該当なし
```

`該当なし`はMedia、Official、Corporate / PR、Fact Only等に用いる。

### 3.5 continuity_relevance

```text
New signal
Continuation evidence
Expansion evidence
Intensification evidence
Stability evidence
Fragmentation evidence
Weakening evidence
Reframing evidence
Contradictory evidence
No continuity relevance
Insufficient evidence
```

件数だけでExpansion / Intensification / Weakeningを付けない。責任帰属、生活影響、行動意向、主体、表現の変化を確認する。

---

## 4. 分類対象B：センチメントクラスタ

`sentiment_cluster_labeling_input.csv`と`sentiment_cluster_representatives.csv`を使い、**全クラスタを必ず分類**する。

入力の`cluster_id`を変更せず、出力の`sentiment_cluster_id`へそのままコピーする。

### 4.1 必須列

```text
sentiment_cluster_id
theme_relevance
source_class
reaction_type
attitude
cluster_consistency
apply_to_cluster
exception_pattern
classification_reason
targeted_stance_status
targeted_stance_count
```

### 4.2 列の許容値

#### theme_relevance

```text
relevant
irrelevant
uncertain
```

- `relevant`: 投稿内で対象テーマ・制度・価格事象と反応が接続している
- `irrelevant`: 語の偶然一致、別テーマ、対象外カテゴリ
- `uncertain`: 代表例だけでは接続を判断できない

フォールバックであっても接続が確認できれば`relevant`とする。

#### source_class

```text
Public Voice
Media
Official / Government
Corporate / PR
SEO / Affiliate
Fact Only
Mixed
Unknown
```

#### reaction_type

```text
Emotion
Evaluation
Question
Prediction
Comparison
Behavior
Life Impact
Mixed
Information Only
Unknown
```

#### attitude

```text
positive
neutral
negative
mixed
not_applicable
unknown
```

- `neutral`は、本人の問い、条件付き予測、判断保留等に限定する
- Fact Only、Media、Official、Corporate / PRは`not_applicable`
- ポジティブとネガティブが実質的に混在する場合のみ`mixed`

#### cluster_consistency

```text
High
Medium
Low
```

- High: 代表投稿が同一テーマ・同一主体区分・同一方向で整合する
- Medium: 表現差はあるが支配的分類が成立する
- Low: テーマ、主体、方向の混在が大きい

入力の`preliminary_consistency`はレビュー優先度の参考であり、最終判定を自動決定しない。

#### apply_to_cluster

```text
true
false
```

- `true`: 支配的分類をクラスタ全件へ適用できる
- `false`: 無関係、非Public Voice、または混在が大きく全件適用できない

`cluster_consistency=Medium/Low`だけを理由に`false`にしない。支配的分類が成立すれば`true`とする。

#### exception_pattern

該当がなければ空欄。複数は`|`で連結する。

```text
mixed_source
mixed_attitude
mixed_theme
fallback_context
fallback_no_required_context
fallback_excluded_context
quoted_article_only
shared_post_without_comment
same_page_multiple_voices
insufficient_representatives
other
```

### 4.3 分類順序

各クラスタを以下の順で判定する。

```text
1. 対象テーマとの関連性
2. 発信主体・コンテンツ種別
3. 投稿者自身の反応の有無
4. 主反応タイプ
5. センチメント方向
6. クラスタ全件へ展開できるか
7. 例外パターンと判定理由
```

### 4.4 大型・Lowクラスタの扱い

- 代表投稿をすべて確認する
- `sentiment_exception_review_candidates.csv`も参照する
- 支配的分類が成立する場合は`apply_to_cluster=true`とする
- 少数の異質例があるだけで全件を除外しない
- 支配的分類が成立しない場合のみ`false`とする
- 重要クラスタを`false`にする場合、`classification_reason`へ具体的な混在内容を記載する

---

## 4.5 Targeted Stance Layer：対象別の評価・支持／不支持

### 4.5.1 目的

候補者・人物・政党・企業・ブランド・政策など、明示的なtargetに対する投稿者本人の態度を、generic `attitude`とは別に判定する。

政治モニタリングでは、候補者別の評価バランスやDriver別の候補者評価に使う。

**Targeted Stanceは全クラスタで無理に生成しない。** 明確なtargetと投稿者本人の評価が確認できるPublic Voiceだけを高Precisionで採用する。

### 4.5.2 target候補の特定

Target候補は原則として以下から得る。

```text
1. signal_narrative_master.csv に定義された人物名・候補者名・政党名・企業名・政策名・alias
2. monitoring_scope.csv / report_config.yaml に明示された監視対象
3. 代表投稿本文で明示され、かつSignalの分析対象として合理的なentity
```

単なる固有名詞出現だけではtargeted stanceを付けない。

### 4.5.3 targeted_stance_status

`sentiment_cluster_classified.csv`には以下を追加する。

```text
targeted_stance_status = available / unavailable / ambiguous / not_applicable
targeted_stance_count = 当該クラスタで有効なtargeted stance行数
```

- `available`: 少なくとも1件の明確なtarget stanceがある
- `unavailable`: Public Voiceだが明確なtarget stanceがない
- `ambiguous`: targetまたは方向が曖昧でprecision基準を満たさない
- `not_applicable`: 非Public Voice / irrelevant等

### 4.5.4 targeted_stance_classified.csv

Targeted Stanceは、1クラスタに複数targetを持てるよう**別ファイル・1 target 1行**で出力する。

```text
stance_id
sentiment_cluster_id
signal_id
target_type
target_name
target_normalized_id
stance_polarity
stance_strength
stance_confidence
stance_evidence
stance_basis
stance_cluster_consistency
apply_stance_to_cluster
stance_exception_pattern
stance_reason
```

#### target_type

```text
candidate
person
party
organization
company
brand
policy
institution
other
```

#### stance_polarity

```text
favorable
unfavorable
neutral
mixed
unclear
```

- `favorable`: 投稿者本人がtargetを支持、評価、擁護、推奨、投票意向等で肯定
- `unfavorable`: 投稿者本人がtargetを批判、拒否、不信、反対、投票拒否等で否定
- `neutral`: targetへの判断保留、条件付き評価、明確な賛否のない本人コメント
- `mixed`: 同一投稿内で同一targetへの肯定・否定が実質的に併存
- `unclear`: 皮肉、代名詞、引用、文脈不足等で方向を確定できない

#### stance_strength

```text
strong
moderate
weak
not_applicable
```

強度は表現の明示性と強さで付与し、投稿量や拡散量では決めない。

#### stance_confidence

```text
high
medium
low
```

- High: targetと投稿者本人の評価方向が直接明示され、代表投稿間でも整合
- Medium: targetと方向は十分読めるが、省略・比較・一部引用等の軽い曖昧さがある
- Low: 皮肉、引用主体不明、複数target混在、代名詞、反語等で誤判定リスクが高い

**候補者別Favorable Rate等の集計にはHigh / Mediumのみ使用し、Lowは除外する。**

#### stance_basis

```text
direct_evaluation
direct_support
direct_opposition
vote_intent
comparison
defense
attack
conditional_evaluation
other
```

### 4.5.5 判定順序

各Public Voiceクラスタで以下の順に判定する。

```text
1. 投稿者本人の声と、引用・記事・他者発言を分離する
2. 投稿者本人が評価しているtargetを列挙する
3. targetごとにstance_polarityを独立判定する
4. strength / confidence / evidenceを付ける
5. 代表投稿間で同じtarget・同じ方向が支配的か確認する
6. クラスタ全件へstanceを展開できるか判定する
```

### 4.5.6 重要な誤判定防止ルール

```text
A. 相手への批判 ≠ 自動的な支持
   “Biggs is terrible.” → Biggs unfavorable のみ。Hobbs favorableを追加しない。

B. ニュース共有 ≠ stance
   “Poll shows Hobbs leading Biggs.” → 投稿者の評価がなければstanceなし。

C. 引用 ≠ 投稿者stance
   “Trump: Biggs is the best candidate.” → 投稿者の同意がなければBiggs favorableにしない。

D. 比較はtargetごとに分解
   “Hobbs is better than Biggs.” → Hobbs favorable。Biggs unfavorableは、文脈上明確な否定評価まで含む場合のみ。

E. 投票意向は強いstance
   “I’m voting for Hobbs.” → Hobbs favorable / strong / high。
   “Never Biggs.” → Biggs unfavorable / strong / high。

F. 皮肉・反語・ミーム
   明確に解釈できない場合はlowまたはunclear。無理に方向を付けない。

G. 同一targetへの賛否混在
   “I like Hobbs on X but hate her record on Y.” → mixed。Driver単位で明確に分離できる場合のみ別行を許容。
```

### 4.5.7 クラスタ展開基準

Targeted StanceはPublic Voice分類より厳しくする。

```text
stance_cluster_consistency = High / Medium / Low
apply_stance_to_cluster = true / false
```

- High: 同じtargetと同じstanceが代表投稿の大半で確認できる → `true`可
- Medium: target・方向は支配的だが一部異質 → 原則`true`可。ただしstance_confidenceはMedium以下
- Low: targetまたは方向の混在が大きい → `false`

`apply_to_cluster=true`でも、Targeted Stance側が`apply_stance_to_cluster=false`になることはある。

### 4.5.8 Candidate Favorabilityの算定ルール

候補者別評価をPrompt 2で集計する場合、以下のみを使う。

```text
source_class = Public Voice
theme_relevance = relevant
apply_to_cluster = true
target_type = candidate
stance_polarity in {favorable, unfavorable}
stance_confidence in {high, medium}
apply_stance_to_cluster = true
```

```text
Candidate Favorability
= favorable / (favorable + unfavorable)
```

Neutral / mixed / unclear / low confidenceは分母から除外する。

名称は**支持率**ではなく、以下を使用する。

```text
Candidate Favorability among directional Public Voice
候補者評価バランス（方向性のあるPublic Voice内）
```

Pollとの混同を避け、選挙支持率・得票予測として扱ってはならない。

---

## 5. 前月との変化兆候レビュー

`previous_continuity_context.json`がある場合、分析クラスタについて以下を確認する。

```text
・前月のongoing_topicsと同じ論点か
・前月のwatch_conditionsが表面化したか
・新しい責任帰属、生活影響、行動意向が加わったか
・批判、不満、懐疑、諦め、理解、期待の構造が変わったか
・異なる主体やカテゴリへ波及したか
・同じ論点が別の公平性・制度・企業姿勢の問題へ再構成されたか
・自発的な声が弱まり、記事共有中心になったか
・前月見立てへの反証があるか
```

前月データがない場合は、`Insufficient evidence`または`No continuity relevance`とし、今後の比較基準を作る。

---

## 6. 代表投稿候補

`public_voice_examples.csv`はPrompt 2の候補母集団である。

主要論点は原則10〜20件、限定的だが重要な論点は5〜10件を目安とする。ただし実データが弱い論点を水増ししない。

優先する投稿：

```text
・本人の評価、選択、回避、支持、諦め、期待が明示されている
・対象テーマに関する本人の実感、影響、判断理由がある
・論点を短く端的に示す
・新しい批判・擁護・懐疑フレームを示す
・前月見立てへの反証になる
・URLと日時が確認できる
```

避ける投稿：

```text
・記事本文、見出し、公的・企業リリース
・PR、商品説明、SEO記事
・引用・共有だけで本人コメントがない
・同一内容の重複
・個人情報を過度に含む投稿
```

---

## 7. 母数保持QAゲート

分類完了後、`eligible_records`を用いてSignal別に集計する。

### 7.1 必須監査指標

```text
signal_id
total_eligible_records
preliminary_public_voice_candidates
final_theme_relevant_records
final_public_voice_records
final_threeway_sentiment_records
excluded_media_official_corporate
excluded_fact_only
excluded_irrelevant
mixed_unknown_records
fallback_records_total
fallback_records_included
fallback_records_excluded
classification_coverage_rate
public_voice_retention_rate
targeted_stance_available_records
targeted_stance_directional_records
targeted_stance_high_medium_records
targeted_stance_low_confidence_records
targeted_stance_ambiguous_records
targeted_stance_coverage_rate
targeted_stance_usable_rate
qa_status
qa_notes
```

### 7.2 算定

```text
classification_coverage_rate
= ラベル付与済みeligible_records / 全eligible_records

public_voice_retention_rate
= final_public_voice_records / preliminary_public_voice_candidates

targeted_stance_coverage_rate
= targeted_stance_available_records / final_public_voice_records

targeted_stance_usable_rate
= targeted_stance_high_medium_records / targeted_stance_available_records

※ Targeted Stanceはprecision優先のため、coverageが低いこと自体を異常としない。
※ 新Shellではpreliminaryが高Recallの候補集合であるため、この率は品質スコアではなく診断指標として扱う。
```

### 7.3 判定

```text
PASS:
・全センチメントクラスタが分類済み
・classification_coverage_rate = 100%
・除外理由が説明可能

REVIEW REQUIRED:
・fallback_recordsの大半を理由なく一律除外
・mixed / unknown / uncertainがeligible_recordsの20%超
・代表例の主体・方向とクラスタラベルが矛盾
・大型Fact Onlyクラスタのサンプルに明確な評価・疑問・予測・比較・選好・行動意向が繰り返し含まれる
・特定SignalだけPublic Voiceが極端に少なく、その理由を取得母数・source構成・本文内容のいずれでも説明できない
・候補者／対象別stanceのサンプル監査で、targetとpolarityの誤接続が繰り返し見つかる
・High confidence stanceで、代表本文とstance_evidenceが矛盾する
・相手targetへの批判を他targetへのfavorableへ自動変換した例が見つかる
```

`REVIEW REQUIRED`の場合は、件数の大きい除外クラスタから再レビューし、合理的な理由なく母数を小さくした状態でPrompt 1を完了しない。

`public_voice_retention_rate`に固定の合格閾値を置かない。高Recall Shellでは低いretentionが正常な場合もあるため、Signalごとの取得母数、source構成、代表本文、除外理由を併せて判断する。

---

## 8. 出力

Prompt 1は、作業ファイルを個別に返さず、以下を格納したZIPを1ファイル生成する。

```text
prompt1_output_YYYYMMDD_HHMMSS.zip
├─ classified_clusters.csv
├─ public_voice_examples.csv
├─ continuity_change_candidates.csv
├─ narrative_master_update_candidates.csv
├─ sentiment_cluster_classified.csv
├─ targeted_stance_classified.csv
├─ prompt1_quality_audit.csv
├─ prompt1_summary.md
└─ classification_rule_applied.md
```

### 8.1 classified_clusters.csv

```text
cluster_id
signal_id
report_topic_label
final_topic_label
source_assessment
tone_gradient
voice_direction
report_use_status
continuity_relevance
matched_previous_topic_id
change_signal_summary
noise_flag
noise_reason
voice_summary
notable_phrases
representative_quality
notes_for_prompt2
```

### 8.2 public_voice_examples.csv

```text
example_id
cluster_id
signal_id
report_topic_label
source_url
author_or_source
timestamp
voice_direction
tone_gradient
post_summary
notable_phrase
why_representative
continuity_relevance
matched_previous_topic_id
use_in_report
targeted_stance_status
primary_stance_target
primary_stance_polarity
primary_stance_confidence
```

### 8.3 continuity_change_candidates.csv

```text
candidate_id
signal_id
report_topic_label
matched_previous_topic_id
candidate_change_type
current_evidence_summary
representative_example_ids
counter_evidence_summary
business_relevance
confidence
caution_for_prompt2
```

`candidate_change_type`：

```text
New
Continuing
Expanding
Intensifying
Stable
Fragmenting
Weakening
Dormant candidate
Reframed
Contradictory
Insufficient evidence
```

### 8.4 narrative_master_update_candidates.csv

```text
signal_id
report_topic_label
current_narrative
suggested_narrative
add_include_keywords
add_required_context
add_exclude_keywords
add_cluster_split_terms
reason
priority
```

このファイルは候補であり、`signal_narrative_master.csv`を自動更新しない。

### 8.5 sentiment_cluster_classified.csv

第4章の必須列・許容値に厳密に従う。`targeted_stance_status`と`targeted_stance_count`を含める。

### 8.6 targeted_stance_classified.csv

第4.5章の定義に従い、1 target 1行で出力する。Targeted Stanceが1件もない場合も、ファイル自体はヘッダー付きで生成する。

### 8.7 prompt1_quality_audit.csv

第7章の監査指標をSignal別に出力する。末尾に`ALL`集計行を追加する。

### 8.8 prompt1_summary.md

以下を簡潔にまとめる。

```text
・主分析に使える論点
・補助材料と監視対象
・除外した主な混入
・Public Voiceの主要な方向と濃淡
・新規、継続、強まり、弱まり、反証の候補
・全Signalの分類前後件数とPublic Voice保持率
・フォールバック採用・除外の状況
・母数保持QAの結果
・Targeted Stanceの利用可能件数、High/Medium/Low構成、主要target別の方向
・Targeted Stanceで検出した主な曖昧パターン・誤判定リスク
・Prompt 2で断定してはいけない点
・Prompt 2で優先比較すべき点
```

### 8.9 classification_rule_applied.md

実際に適用した分類原則、例外処理、QA再レビュー内容を記録する。

---

## 9. 最終品質チェック

```text
□ 全センチメントクラスタを分類した
□ cluster_idを変更せずsentiment_cluster_idへ引き継いだ
□ Fact Onlyをneutralに含めていない
□ 疑問、予測、比較、生活実感、行動をPublic Voiceとして確認した
□ フォールバックやLow consistencyを一律除外していない
□ 大型クラスタの少数例だけで全件除外していない
□ Public VoiceとMedia / Official / Corporate / PRを分離した
□ 強い批判だけでなく、懐疑、諦め、理解、期待、擁護も残した
□ 全SignalのPublic Voice保持率を監査した（固定の合格閾値は置かず、取得母数・source構成・除外理由を併せて判断した）
□ 変化を件数だけで判定していない
□ 前月見立てへの反証を残した
□ narrative masterを自動上書きしていない
□ Prompt 2が全件対応表へ展開できるファイルを生成した
□ generic attitudeを候補者・targetへのstanceとして流用していない
□ Targeted Stanceは投稿者本人の声と引用・記事・他者発言を分離した
□ 相手への批判を別targetへの支持へ自動変換していない
□ 1投稿内の複数targetをtargetごとに独立判定した
□ Low confidence / unclearを候補者評価バランスの分母に入れていない
□ targeted_stance_classified.csvを生成した
□ 最終成果物はprompt1_output ZIPのみである
```
