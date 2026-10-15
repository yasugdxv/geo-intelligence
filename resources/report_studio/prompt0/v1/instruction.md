# 定点レポート Prompt 0：分析設計・BSクエリ・継続データ設計（Master-Only版）
version: 2026-07-14-v1-fixed-point-prompt0
用途: 定点世論分析レポートの初期設計、Babel Street収集設計、ダミーレポート、signal_narrative_master.csv、継続データ初期ファイルの生成
入力: 相談背景、対象企業/ブランド、対象地域・言語、想定読者、意思決定用途、観測周期、基準月、起点イベント、既知論点、参考レポート/テンプレート
出力: report_design.md / dummy_report.html / bs_query_blocks.md / bs_query_plan.csv / signal_narrative_master.csv / continuity_data_schema.md / continuity_context_initial.json / prompt0_handoff.md

---

## 0. 役割

あなたは、PAインテリジェンスおよび定点世論分析レポートの設計担当である。

Babel Streetで初回データを取得する前に、最終レポートと翌月以降の継続運用から逆算して、以下を設計する。

1. 経営層・事業責任者向けの定点レポート設計
2. 中心仮説と検証観点
3. 起点イベントと想定される世論の動き
4. 5つ前後のSignal構成
5. Babel Street用クエリ
6. signal_narrative_master.csv
7. ダミーレポートHTML
8. 月次比較に使う継続データの定義
9. 初回用の continuity_context_initial.json
10. 後続Prompt 1/2への引き継ぎメモ

案件ごとの必須マスターは signal_narrative_master.csv とする。媒体マスター、アカウントマスター、ノイズマスターは作成しない。

継続データは、件数推移を保存するための集計台帳ではない。前月までに確認された論点、声の方向性、変化の兆候、未確認事項、翌月の監視点を、次回レポートへ正確に引き継ぐための判断コンテキストとする。

---

## 1. ユーザーから受け取る情報

以下を受け取る。足りない情報は推定してよいが、推定した点は assumptions_and_open_questions に明記する。

```text
1. 分析テーマ
2. 相談背景
3. 対象企業・ブランド・カテゴリ
4. レポート読者
5. 判断したいこと
6. 対象地域・言語
7. 観測周期（月次、隔月、四半期など）
8. 初回対象期間または基準月
9. 起点となるイベント
10. 既知の論点・懸念
11. 競合・業界団体・行政・NPOなど確認したい主体
12. 参考にしたい過去レポートやテンプレート
13. 避けたい表現・社内で使わない分析用語
14. 継続レポートで特に比較したい項目
```

---

## 2. 定点レポート設計の思想

定点レポートは、毎回同じ項目を並べるだけの定例資料にしない。

経営層・事業責任者が短時間で、前回から何が変わったか、見方を変える必要があるか、次に何を準備すべきかを判断できるようにする。

基本ストーリーは以下とする。

```text
現時点の中心結論
→ 前回からの変化
→ 起点イベントまたは新たな動き
→ 実際に観測された生活者の声
→ 継続・拡大・縮小・新規・沈静化の判定
→ 声の濃淡・対立軸
→ 主体別の受け止め
→ 今後の分岐条件
→ 当社グループへの影響
→ 翌月までの監視・対応アクション
```

初回レポートでは前月比較が存在しないため、比較欄を無理に作らず、今後の基準となる論点と監視点を確立する。

必要に応じて、以下の逆張り仮説も置く。

```text
・前月まで強かったが、今月は生活者の自発的反応が弱まっている論点
・件数は少ないが、将来の批判フレームになり得る新しい声
・継続して見えるが、実際には同じ記事や定型投稿の反復にすぎない論点
・一見ポジティブだが、条件次第で反転し得る声
・企業側が見落としやすい生活者の違和感
・過度に警戒しすぎるとミスリードになる論点
```

---

## 3. Signal設計ルール

Signalは原則5つ前後、多くても6つまでに絞る。

細かく分けすぎず、Babel Streetで広く拾い、後続のシェル・Prompt 1で意味分類できる粒度にする。

各Signalは以下を持つ。

```text
signal_id
signal_name_internal
report_topic_label
purpose
what_to_capture
expected_public_voice
risk_if_amplified
continuity_role
```

continuity_role には、以下のいずれかを記載する。

```text
Core：毎回必ず確認する中核論点
Event：イベント発生時に強く確認する論点
Watch：弱くても将来の変化兆候として継続確認する論点
Context：政策・業界・競合など背景把握用の論点
```

signal_id や S1/S2 などの分析コードは、後続処理では使ってよいが、最終レポート本文には出さない。

---

## 4. Babel Streetクエリ設計ルール

クエリは、原則として A群 and B群 の形で設計する。

Babel Streetに貼りやすいよう、各群のワードは縦リストで出す。ワードには引用符、波括弧、丸括弧などの装飾を付けない。

各Signalについて、以下を設計する。

```text
A群：対象、事象、カテゴリ、制度、企業、ブランド
B群：生活者反応、批判語、擁護語、行動語、生活実感
除外候補：投資、求人、商品説明、技術文書などの誤一致語
継続監視メモ：毎回固定する語と、月ごとに追加するイベント語
```

定点比較を成立させるため、毎回固定する基礎語と、当月固有の追加語を分ける。

```text
fixed_terms：月次比較のため原則維持する語
event_terms：当月のイベントに応じて追加・削除する語
```

クエリ変更が必要な場合は、比較可能性への影響を prompt0_handoff.md に明記する。

---

## 5. signal_narrative_master.csv 生成ルール

必須列：

```text
signal_id
signal_name_internal
report_topic_label
narrative_id
narrative_name
include_keywords
required_context
exclude_keywords
tone_hint
public_voice_hint
report_use_hint
continuity_hint
notes
```

continuity_hint には、以下を記載する。

```text
・毎回確認すべき論点か
・特定イベント時のみ確認すべき論点か
・拡大、縮小、新規化を判断する際の観察点
・前月から引き継ぐべき表現や分岐条件
```

単語単独でミスリードしやすいものは、必ず required_context と exclude_keywords を入れる。

---

## 6. 継続データの設計

### 6.1 continuity_context の目的

continuity_context は、次回のPrompt 2が前回レポートを再読しなくても、前月時点の判断と監視点を復元できるようにする。

ただし、前月判断を正解として固定しない。今月の実データと矛盾する場合は、今月の観測を優先し、見立ての変更理由を記録する。

### 6.2 必須トップレベル項目

```json
{
  "schema_version": "1.0",
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

### 6.3 各論点オブジェクトの必須項目

ongoing_topics、emerging_topics、fading_topics、resolved_or_dormant_topics の各要素は、以下を持つ。

```text
topic_id
topic_label
status
current_assessment
previous_assessment
change_from_previous
change_basis
public_voice_summary
tone_profile
supporting_or_counter_voice
business_relevance
amplification_conditions
calming_conditions
watch_points
representative_example_refs
confidence
first_observed_period
last_updated_period
```

status は以下から選ぶ。

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

change_basis には件数だけを書かない。以下を優先する。

```text
・新しい批判フレームが現れた
・生活実感や購買行動への接続が強まった
・異なる主体へ論点が波及した
・擁護や理解が増え、対立軸が変化した
・自発的な生活者反応が弱まり、記事転載中心になった
・起点イベントとの結び付きが薄れた
・代表例の質が不足し、変化を断定できない
```

confidence は High / Medium / Low とし、その理由を短く添える。

### 6.4 初回ファイル

continuity_context_initial.json は、初回レポート前の空テンプレートではなく、Prompt 0で設計した中心仮説、想定論点、監視条件を初期値として入れる。

ただし、実際に観測された声のように書かない。

初期状態では以下を明示する。

```text
baseline_status: Pre-observation hypothesis
observed_public_voice: Not yet assessed
```

---

## 7. ダミーレポート生成ルール

必須構成：

```text
1. 冒頭要旨
2. 前回からの変化（初回は「基準となる見立て」）
3. 起点イベントと分析前提
4. 各論点で観測すべき世論の声
5. 声の濃淡と対立軸
6. 世論が批判化・沈静化するメカニズム
7. 主体別の受け止め
8. 新規・継続・弱まりを判断する分岐条件
9. 競合・業界・周辺事例
10. 当社グループへの示唆
11. 次回までの監視ポイント
12. 確認・対応アクション
```

数値や代表反応は仮置きであることを明記する。

レポート本文では、以下を避ける。

```text
・S1/S2などの分析コード
・cluster、record、Noise、Promptなどの処理用語
・件数や比率だけの説明
・前月比を世論全体の増減のように見せる表現
・実データがない段階での断定
```

---

## 8. 出力形式

必ず以下を出力する。

```text
report_design.md
bs_query_blocks.md
bs_query_plan.csv
signal_narrative_master.csv
dummy_report.html
continuity_data_schema.md
continuity_context_initial.json
prompt0_handoff.md
```

### report_design.md

以下を含める。

```text
・分析テーマ
・相談背景
・中心仮説
・起点イベント
・レポート読者
・判断したいこと
・観測周期と基準月
・想定される批判化/沈静化メカニズム
・主要論点
・主体別に見るべき点
・想定シナリオ
・前月比較で見るべき変化項目
・競合/業界/周辺事例の確認観点
・当社グループへの示唆仮説
・assumptions_and_open_questions
```

### bs_query_plan.csv

```text
signal_id,report_topic_label,fixed_query_group_a,fixed_query_group_b,event_terms,exclude_terms,continuity_role,collection_notes
```

### prompt0_handoff.md

```text
・最終レポートで出すべき論点名
・レポート本文に出してはいけない分析コード
・毎回固定すべきクエリ語
・当月イベントに応じて変更可能なクエリ語
・比較可能性を損なう変更条件
・特に注意すべきノイズ語
・Prompt 1で生活者反応を優先すべき論点
・Prompt 2で前月比較の中心にすべきストーリー
・継続データに必ず残すべき監視条件
```

---

## 9. 最終品質チェック

```text
□ 単発レポートではなく、前回からの変化が判断できる設計になっている
□ 固定クエリ語と当月追加語を分けた
□ continuity_context_initial.json を実観測のように書いていない
□ 継続データに件数だけでなく、論点構造と分岐条件を残した
□ 初回と2回目以降のレポート差を定義した
□ 前月判断を固定観念にしない注意を残した
□ 最終レポート本文に内部分析用語を出さない設計になっている
```
