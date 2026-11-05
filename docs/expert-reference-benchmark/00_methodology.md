# Expert Reference Benchmark — 方法論(Prototype検証版)

## 0. 目的

「旧AIより新AIが良くなったか」ではなく、**「新AIが江口さんの考え方をどこまで再現できているか」**を測るための評価セットを作る。ソースはBefore/After比較ではなく、`C:\Users\269811\Downloads\eguchi_expert_ai_final_v1_0`(以下「原資料パッケージ」)に同梱された、江口さん本人の実資料(実メール抜粋33件M0xx、実報告書Before/After 4組)。

**この文書はまだPrototype段階(ハンガリー1ケースのみ)。型の検証が済んだ後、残り3ケース(BC Strategy/米中関係/イラン)へ横展開する。**

## 1. 素材の性質(正直な評価)

原資料パッケージ自身が明記する通り(`00_README.md`: 「Afterを正解とするものではない」、`06_source_manifest.json`: 「編集者・編集理由・立証性は証明しない」)、この素材は**完全なGold Standardではない**。実際に4組のBefore/After diffを読んだ結果:

- **イラン(R006→R002)・米中関係(R007→R003)**: 部下・外部専門家の分析をほぼそのまま整形・再配信したもので、江口さん独自の仮説生成の痕跡は薄い。
- **ハンガリー(R008→R004)・BC Strategy(R005→R001)**: 本文は元原稿(竹節氏起案)からほぼ不変だが、**冒頭に江口さんが新規追加した"なぜ重要か"の枠付けパラグラフ**がある。これは実際に本人が付け加えた文章であり、Initial Cue/Focus/Why nowの直接的な素材になる。

したがって、**素材の強弱はケースによって系統的に異なる**。これを無視して4ケース均等に採点すると、素材の薄さをAIの失敗と誤認するリスクがある。ケースごとに「何を評価できる素材か」を明示し、埋まらない列は無理に埋めない。

## 2. 3階層のProvenance構造

| 階層 | 内容 | provenance_status |
|---|---|---|
|**Tier 1: Source-supported reference**|江口さん本人の実文章(追加パラグラフ・実メール抜粋)から直接確認できるもの|`EXPERT_STATED`(本人の文章そのもの)|
|**Tier 2: Reconstructed reference**|M0xx断片・Section3対応表・本文内容から分析者が再構成するもの|`RECONSTRUCTED_NOT_EXPERT_CONFIRMED`|
|**Tier 3: Unknown / Session3 target**|現資料からは取得不能なもの|`NOT_YET_CAPTURED`|

Tier1とTier2の境界で特に注意する点: **Before側に既にあった内容(部下・外部専門家起案)を、江口さんが「採用」しただけなのか、江口さん独自の「候補生成」なのかを区別する**(原資料パッケージ8.2章の明示的指示)。Afterで採用された内容は「本人が同意・選択した」ことの証拠にはなるが、「本人がゼロから生成した」ことの証拠にはならない。この区別をTier1の各項目に注記する。

## 3. 評価する項目(Reference Table、ハンガリーProtoype検証後に確定)

```text
Event / Situation              [Tier1]
Initial Cue                    [Tier1、本人の追加文がある場合のみ]
Focus                          [Tier1]
Why now                        [Tier1]
Expert Question / Framing      [Tier1]
Observed Answer Angle(+origin_status) [Tier1(本文採用)、起案の帰属を必須併記]
Actor / Counterparty referenced[Tier1]
---
Possible Actor Model Connection      [Tier2]
Possible Non-straightforward Hypothesis [Tier2]
Possible Counterparty Model          [Tier2]
---
Selection Logic                [Tier3]
Rejected Alternative           [Tier3]
Discriminator                  [Tier3]
```

### 3.1 `Expert Question` → `Expert Question / Framing`(ハンガリー検証後の修正)

江口さんの思考は必ずしも疑問文として外化されるわけではない。断定的なフレーミング(見立ての提示)として出てくるケースもある(ハンガリー・BC Strategyの冒頭追加パラグラフはいずれも断定文)。評価したいのは文型(疑問文か断定文か)ではなく、**何を論点化したか**である。したがって本項目は「疑問文として明示された問い」と「断定的フレーミングとして提示された論点」のいずれの形でも記録してよいものとし、記録時にどちらの形式だったかを一言添える。

### 3.2 `Observed Answer Angle` の `origin_status`(ハンガリー検証後の追加、必須)

`Observed Answer Angle`には、以下いずれかの`origin_status`を必ず併記する。「江口さんが書いた最終レポートにある」ことを「江口さん独自の思考」と誤認しないための必須の区別。

| origin_status | 意味 |
|---|---|
|`EXPERT_GENERATED`|江口さん本人がゼロから生成したことが確認できる(例: 他文書に無い独自の追加文・問いかけ)|
|`EXPERT_REFRAMED`|他者・外部専門家の分析素材を、本人が明確に読み替え・再構成したことが確認できる|
|`EXPERT_ADOPTED_EXTERNAL`|他者(部下・外部専門家)が起案した内容を、本人がほぼそのまま採用・是認して自分の名前で配信した(内容そのものは本人の独自生成ではない)|
|`UNCLEAR`|起案者・生成経緯が資料から判別できない|

### 3.3 `benchmark_strength`(ケース単位、必須)

各ケースの冒頭に`benchmark_strength: STRONG / MODERATE / WEAK`を付け、「このケースは何を評価するのに強いか」を明示する。4ケースを同一の評価軸で均等採点しない——ケースごとの強みを生かす(5章参照)。

## 4. 評価の中心(型検証の対象)

```text
Event
  ↓
何に引っかかった？(Initial Cue)
  ↓
どんな問いを立てた？(Expert Question)
  ↓
問いにどんな回答角度を当てた？(Answer Angle)
  ↓
どのActor/Counterparty理解を使った？
  ↓
何をFocusにした？(Focus/Why now)
```

この一連が、原資料から実際に再構成可能かをハンガリーケースで検証する。Selection Logic/Rejected Alternative/Discriminatorは、この資料単体では埋まらないことをむしろ明示し、Session3インタビューのターゲット項目として残す。

## 5. ケース別の評価対象(4ケース横展開完了、確定版)

| ケース | benchmark_strength | 主に評価できる型 | 主に評価できない型 |
|---|---|---|---|
|ハンガリー|MODERATE|Initial Cue/Focus/Why now(断定的Framing形式の発見源)|Selection Logic/Rejected Alternative/Discriminator、Tier2の独立裏付け(M0xx無し)|
|BC Strategy|MODERATE-STRONG|Initial Cue/Focus/Why now(観測可能な軍事指標という具体的な形)、Actor行動パターン引用(Tier1で既出)|Selection Logic/Rejected Alternative(部分的にDiscriminator候補あり)|
|米中関係|MODERATE|Actor/Counterparty/非正面仮説(M016「不在のシグナル」が強い)|Initial Cue/Focus/Why now(枠付け追加文が本ケースに無い)|
|**イラン**|**STRONG**|**Question/Framing選定、前提懐疑(premise challenge)、非正面仮説、かつSelection Logic/Rejected Alternativeへ部分的に到達**(M086経由)|Discriminatorは依然弱い|

4ケースを同じ評価軸で均等採点しない。**4ケースを通じた最重要の発見**: Source-supported/Reconstructed/Unknownの境界を最も強く左右するのは「その資料が最終成果物(配信済みレポート)か、思考の途中経過(内部メールでの即応コメント)か」という軸である。最終成果物(R00X Before/After)は本人独自の生成が冒頭の枠付け文に限られがちだが、途中経過(M0xx)は短くても本人の思考の痕跡そのものであり、Question/Framing・Selection Logicはここからしか得られない。今後の資料収集では"やり取り途中の実メール"を優先すべき。
