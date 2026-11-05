# Expert Reference Benchmark — Blind Run 比較レポート

**実施日**: 2026-09-07。**実行方式**: `geo_expert_engine.run_expert_request_staged`、`mode="case_analysis"`、`profile="deep"`、`persist=False`、`theme_id=None`(既存DBとの照合なし、query_textのみを情報源とする完全に独立した実行)。江口さんのM0xxコメント・Afterの追加フレーミング・Expert Reference文書の内容はAIに一切提示していない——各ケースのBefore(または実質的に同等な、江口さんの枠付け追加前の)本文のみをEvent/Evidenceとして与えた。

## 0. 実行結果サマリー(アーキテクチャ検証)

| ケース | 呼び出し回数 | Call1所要時間/トークン | Call2所要時間/トークン | Repair | defer_scenario |
|---|---|---|---|---|---|
|イラン|2|97s / 61k+15k|27s / 50k+3.8k|なし|True|
|米中関係|2|74s / 60k+11k|21s / 45k+2.9k|なし|True|
|ハンガリー|2|78s / 63k+12k|29s / 49k+4.1k|なし|True|
|BC Strategy|2|70s / 61k+11k|26s / 46k+3.7k|なし|True|

**D-1〜D-4bで実装した新アーキテクチャが、4件全てで設計通り動作した**: `stage2_modules`に`situation_actor_assessment`が含まれる(=defer_scenario=True)ことを確認し、Call1(Stage1+4モジュール合成: audience_signal/media_context/situation_actor_assessment/directional_perception)→Call2(Scenario専任)という2回構成が維持された。CallBudget増加なし、Repair発動なし(いずれも失敗したチェックはadvisory・非V12のみ)。全4件で`main_scenario.derived_from_hypothesis_id`がhypothesis_registerの`retained`な仮説を正しく参照している(絶対条件4の実データ確認)。

**発見(D-4bのHuman Interaction chain completeness Guardの実地検証)**: 4件全てで`situation_actor_assessment`と`directional_perceptions`が`null`だった一方、`key_actor_profiles[].event_role.intended_target_actor_ref`は多くのケース(イラン3/3、米中2/2、ハンガリー1/3)で実際に埋まっていた。これは`_human_interaction_chain_completeness_check`が設計通り`PARTIAL_EVIDENCE_GAP`(鎖の起点はあるが終点=situation_actor_assessmentまで閉じていない)と判定する状況——机上の設計だけでなく実データでも起き得ることが確認された。BC Strategyは3/3で`event_role: null`、ハンガリーのEU Actorのみ`event_role`ありという偏りも見られ、Human Interaction機構の発火はモデルの裁量に委ねられており安定して起動するわけではないことが分かった。

## 1. ケース別 評価軸スコア

凡例: **REFERENCE_AVAILABLE**(Referenceに直接比較材料あり)/**REFERENCE_PARTIAL**(部分的・間接的にあり)/**NOT_EVALUABLE**(Referenceに材料なし、採点しない)。スコアは0(Missing/opposite)〜3(Strong expert-like match)。

### イラン(benchmark_strength: STRONG)

| 軸 | Reference可用性 | Score | Evidence note |
|---|---|---|---|
|Cue/Framing|REFERENCE_AVAILABLE(M032/M140)|**1**|AIの`initial_anomaly`は「トランプの衝動的決定・出口戦略欠如という意思決定プロセスの異常」に着目。M140は「石油輸出維持という前提が戦時下で成立するか」という**前提そのものへの懐疑**、M032は「お願いする立場を良しとするか」という**面子・体面の疑問**。AIはプロセス異常には気づいたが、Eguchi特有の「前提を名指しで疑う」動きは見せなかった。|
|Question Selection|REFERENCE_AVAILABLE(M032/M140/M086)|**2**|AIのdecision_sensitive_outcome「体制転換維持か限定合意受容か」は、M086の central question(段階的交渉/膠着/戦争再開のどれか)と同じ方向。ただしM086ほど具体的な3択の形にはなっていない。|
|Humanization|REFERENCE_PARTIAL|**2**|`event_role`でTrump→Iran_regime_core(means)、Iran_regime_core→Trump(bargaining_card)、Netanyahu→Trump(signal)という**主体→対象の指向性ある一手**を明示的に生成——これは新設計の狙い通り。ただし`situation_actor_assessment`/`directional_perceptions`は空で、「Aの認識」「Aが期待するBの反応」までは到達していない。|
|Answer Angles/Hypotheses|REFERENCE_PARTIAL|**2**|H0(額面通り)/H1(国内政治手段)/H2(Audience shift)/H3(イスラエル等の制約駆動)/H4(frame-breaking)の5仮説を自律生成。特にH1・H3はEguchiの他資料(BC Strategy Reference)が示す「中間選挙向けカード」論と同じ方向。|
|Actor Model Connection|NOT_EVALUABLE(このケースの入力にActor Intelligence Packが無く、公平な評価ができない)|**該当なし**|`pattern_applicability_assessment`は全て`applicability: uncertain`、実質的な過去事例接続なし——ただしこれはcase_id無しの実行設計上の制約であり、モデルの能力不足と断定できない。|
|Selection Logic|REFERENCE_AVAILABLE(M086)|**2**|`main_scenario.selection_rationale`はH0を主軸としH1〜H3を退ける理由(Evidence不足)を明示。M086は面子コスト・経済/政治圧力という**具体的な因果メカニズム**で退けており、AIの理由付けはより一般的(Evidence不足)に留まる。|
|Rejected Alternative|REFERENCE_AVAILABLE(M086)|**2**|`rejected_candidates`・H4の棄却理由は明示的。M086ほど具体的な機序(ガソリン価格・中間選挙)への言及はない。|
|Scenario Derivation|REFERENCE_PARTIAL(M086の結論)|**3**|`derived_from_hypothesis_id="H0"`、selection_rationaleで「retainedなH0→Actor Interaction(event_role割り当て)→main_scenario」という連鎖を明示的に記述——**絶対条件4が意図通り機能した最も明確な実例**。結論の方向性(戦争継続・早期解決なし)もM086の「戦争再開の可能性が高い」と同じ方向。|

### 米中関係(benchmark_strength: MODERATE)

| 軸 | Reference可用性 | Score | Evidence note |
|---|---|---|---|
|Cue/Framing|NOT_EVALUABLE(M016の「不在のシグナル」はEvent Pack範囲外の後日情報)|該当なし|AIの`initial_anomaly`は「安全保障ショックの中でも会談が予定通りとされる点」「40日間の空域閉鎖」に着目——後者はM016の"omission-as-signal"と同じ**観察スタイル**の萌芽だが、対象は別の事実。|
|Question Selection|NOT_EVALUABLE|該当なし|Reference側にこの時点のQuestionが存在しない。|
|Humanization|REFERENCE_PARTIAL|**2**|Trump⇄Xiのevent_roleは双方`means`で、指向性は明示されるが底が浅い(双方向の"means"のみ、認識・期待は空)。|
|Answer Angles/Hypotheses|REFERENCE_PARTIAL|**2**|H2(国内Audience向けシグナル仮説)はUS-China ReferenceのTier2仮説(「対米投資の実質合意は限定的」)と方向性は異なるが、**同じ「表向きと実質のギャップ」という着眼**を持つ。|
|Actor Model Connection|NOT_EVALUABLE|該当なし||
|Selection Logic|NOT_EVALUABLE|該当なし||
|Rejected Alternative|NOT_EVALUABLE|該当なし||
|Scenario Derivation|NOT_EVALUABLE|該当なし|`derived_from_hypothesis_id="H0"`で機構自体は機能。|

**米中関係についての所見**: このケースはBenchmark自体の限界が最も顕著に出た——Eguchi本人の最強のCue(M016の代表団不在シグナル)は、Event Pack(4/6時点)より後(5/12)の情報に基づいており、時間的に比較不能。したがって大半の軸がNOT_EVALUABLEとなった。これはAIの失敗ではなく、**Reference自体の時間断面のズレ**によるもの——方法論上の教訓として次項にまとめる。

### ハンガリー(benchmark_strength: MODERATE)

| 軸 | Reference可用性 | Score | Evidence note |
|---|---|---|---|
|Cue/Framing|REFERENCE_AVAILABLE|**3**|**最重要の一致**。AIの`initial_anomaly`「オルバン(右派)がティサ党(同じ中道右派)に3分の2超で敗北」「右派vs左派ではなく右派同士の対立」は、EguchiがAfterで追加した枠付け(「非リベラル民主主義政権の崩壊」「右派ポピュリスト勢力への戦略的再考」)と**独立に同じ着眼**に到達している。|
|Question Selection|REFERENCE_PARTIAL|**2**|primary_key_point「文化戦争→経済停滞・腐敗への関心シフト」は明示的な疑問文ではないが、Eguchi自身もFraming形式だった(方法論3.1の根拠)ため、形式面は完全に一致。|
|Humanization|REFERENCE_PARTIAL|**1**|event_roleが3 Actor中1件(EU→Magyar、opportunity)のみ。オルバン・マジャル本人同士の直接的な指向性のある一手はほぼ生成されず、Humanizationは限定的。|
|Answer Angles/Hypotheses|REFERENCE_AVAILABLE|**3**|**H4(frame_breaking)が`status=retained`のまま**「今回の政権交代はポピュリズムの終焉ではなく、右派政権の存続条件が文化戦争の強度ではなく経済実績・腐敗の少なさであることを示した」——これはHungary ReferenceのTier2「Possible Non-straightforward Hypothesis」(反現職投票・イデオロギー転換ではない)と**ほぼ同一の結論**。ただし`hypothesis_pruning_present`チェックが失敗(全件retained、H0-H4の淘汰が起きていない)——多様な仮説は生成したが実際の選別(棄却)が機能していない。|
|Actor Model Connection|NOT_EVALUABLE|該当なし||
|Selection Logic|REFERENCE_PARTIAL|**1**|全仮説がretainedのため、実質的な選別ロジックが機能していない——Iran(M086)のような明確な淘汰は見られない。|
|Rejected Alternative|NOT_EVALUABLE(Referenceにこのケースの棄却理由データなし)|該当なし||
|Scenario Derivation|REFERENCE_PARTIAL|**2**|`derived_from_hypothesis_id="H0"`で機構は機能。ただしH0/H1/H3を「組み合わせて」main_scenarioとする、という説明で、単一retained仮説からの明確な一本道の導出ではない。|

### BC Strategy(benchmark_strength: MODERATE-STRONG)

| 軸 | Reference可用性 | Score | Evidence note |
|---|---|---|---|
|Cue/Framing|REFERENCE_AVAILABLE|**1**|AIの`initial_anomaly`「軍事的合理性より感情・選挙動員に依存する決定プロセス」に着目。ReferenceのInitial Cue(空母配備という**観測可能な軍事指標**)とは性質が異なる——AIはプロセスの質に着目、Referenceは物理的タイミング指標に着目。|
|Question Selection|REFERENCE_AVAILABLE|**2**|`decision_sensitive_outcome`「選挙カードとして本気かどうか」は、Referenceの背後にある問い(攻撃切迫の実態)と同じ方向感だが、切り口が異なる(政治動機 vs 軍事的timing)。|
|Humanization|NOT_EVALUABLE(event_roleが全Actorでnullのため評価不能)|該当なし|このRunでは3 Actor全てevent_role未生成——D-4bのHuman Interaction機構が発火しなかった実例。|
|Answer Angles/Hypotheses|REFERENCE_AVAILABLE|**3**|**H1(instrumental_indirect、選挙カード仮説)がmain_scenarioの基盤として採用された**。これはBC Strategy Referenceの本文(「中間選挙での敗北を察知しリスクを冒して攻撃する必要がある」)およびTier2の非正面仮説と**方向・内容ともに高い一致**。|
|Actor Model Connection|REFERENCE_AVAILABLE|**3**|**最重要の一致**。`historical_analogies_used`で「ベネズエラ作戦成功後にトランプが対外強硬行動を国内政治目的のために利用したパターン」を明示的に引用——これはBC Strategy Reference Tier1に実在する「侮辱されると激発するトランプ」パターン(マドゥロ→ハメネイ師への類推)と**同一のActor Model接続**。Event Packに含めた情報を正しく拾い、類推として活用できている。|
|Selection Logic|REFERENCE_PARTIAL|**2**|selection_rationaleでH1を「Goal→Means→Timingの因果骨格を最も明確に示す」として選定——Referenceの背後の選択ロジック(具体的機序への言及)ほど深くはないが方向は一致。|
|Rejected Alternative|REFERENCE_PARTIAL|**2**|rejected_candidatesは明示的だが、Reference側にこのケースの棄却理由データが薄いため部分評価。|
|Scenario Derivation|REFERENCE_PARTIAL|**2**|`derived_from_hypothesis_id="H1"`——機構は機能し、H1由来のシナリオ(段階的エスカレーション)を導出。|

## 2. 横断的な発見

### 2.1 新アーキテクチャ(D-1〜D-4b)の実データ検証: 成功

4ケース全てで、絶対条件2・3・4(Human Strategic Interaction/Hypothesis Register確定後にのみMain Scenarioを生成し、`derived_from_hypothesis_id`で追跡可能な形で連鎖する)が実際に機能した。これは設計・オフラインテストだけでなく、初めての実データ・ブラインド実行で確認できた重要な成果。

### 2.2 「Humanization」の実現度はムラが大きい

`event_role.intended_target_actor_ref`(指向性のある一手)は生成されたケース(イラン3/3、米中2/2)と、ほぼ生成されなかったケース(BC Strategy 0/3、ハンガリー1/3)に分かれた。`situation_actor_assessment`/`directional_perceptions`(認識・期待の層)は**4ケース全てで完全に空**。D-3で「Call1側へ吸収」した設計自体は機能しているが、**モデルが実際にそれを使うかどうかは局面依存で不安定**——Iran/BC Strategyのように「面談記録・専門家評価」という形式のEvent Packでは(Actorの心理・動機への言及が多いため)発火しやすいが、Hungary(選挙結果報告)のような形式では発火しにくい傾向が見える(n=4での傾向、確証ではない)。

### 2.3 最も強い一致は「非正面仮説の独立到達」

ハンガリーのH4(frame_breaking)とBC StrategyのH1(instrumental_indirect)は、いずれもEguchi自身の実際の分析(Tier1/Tier2)と**独立に、方向・内容ともに高い一致**を示した。これは新AIが「正面の説明で満足せず、間接的・構造的な代替説明を自律生成する」という、今回の設計全体が目指した核心的能力の獲得を強く示唆する。

### 2.4 「Cue/Framing」の一致度は総じて低い(ハンガリーを除く)

ハンガリー以外の3ケースでは、AIの`initial_anomaly`は「意思決定プロセスの異常」に着目する傾向が強く、Eguchiの実際のCue(前提への懐疑=M140、面子・体面の疑問=M032、観測可能な物理指標=BC Strategyの空母配備)とは**着眼のレイヤーが異なる**。AIは「何がおかしいか」は捉えるが、「その先にある、疑うべき前提は何か」まで踏み込む頻度がEguchiより低い可能性がある。

### 2.5 Selection Logic/Rejected Alternativeは「機構は動くが深さが浅い」

全ケースでhypothesis_register/main_scenario.selection_rationaleは機能し、理由も明示されるが、Eguchi(M086)の理由付けが「ガソリン価格」「中間選挙の政治コスト」という**具体的因果メカニズム**を示すのに対し、AIの理由付けは「Evidence不足」「Evidence Fit/Actor Model Fit等の5観点」という**やや抽象的な評価枠組みの言葉**に留まる傾向がある。

### 2.6 米中関係ケースの方法論上の教訓

Reference側の最強のCue(M016)が、Event Packの時間断面より後の情報に基づいていたため、比較の大半がNOT_EVALUABLEになった。**今後Event Packを設計する際は、Reference側のCue/Question材料の日付とEvent Packの時間断面を事前に揃える必要がある**——これは方法論0.6として追記すべき教訓。

## 3. Session3ターゲット(AIとEguchiの差分から生成)

1. **前提懐疑の型**: 「Iranの石油輸出優先という前提は、戦時下でも成立するか」(M140)のような、**既存の定説・専門家コンセンサスを名指しで疑う**動きをAIは自発的に行わなかった。Session3で聞くべき問い: 「専門家の見方が一致しているとき、何を根拠にそれでも疑いますか?具体的にどこを見ますか?」

2. **選択ロジックの因果具体性**: AIの仮説選択理由は「Evidence不足」という否定形が多く、Eguchi(M086)のような「ガソリン価格→中間選挙→政治コスト」という**具体的な因果の鎖**を示さない。Session3で聞くべき問い: 「ある仮説を残すとき、単に『反証がない』からではなく、具体的にどんな圧力・コストの連鎖を思い浮かべますか?」

3. **Human Interaction層(認識・期待)の空白**: `situation_actor_assessment`/`directional_perceptions`が4ケース全てで空だった。これはAIの限界というより、モデルがこの層を「使う理由」を十分に認識していない可能性を示す。Session3で聞くべき問い: 「一手を読むとき、相手が自分をどう見ているかを、いつ・どうやって具体的に考えますか?」

4. **観測可能な物理指標へのこだわり**: BC StrategyのReferenceでは「空母配備完了」という具体的・観測可能な軍事指標がWhy nowの核だったが、AIは「意思決定プロセスの質」という抽象的な着眼に留まった。Session3で聞くべき問い: 「『そろそろ動きそうだ』と感じるとき、実際に何を確認しに行きますか?」

## 4. 制約・留保

- 全4ケースとも`case_id`無し(theme_id=None)で実行したため、Actor/Institution/Media Intelligence Packが一切適用されていない。Actor Model Connection軸の評価は、この設計上の制約により4ケース中3ケースでNOT_EVALUABLEとした——本来の本番運用(既存Actor Intelligenceが利用可能な状態)であれば、より豊かな接続が生成される可能性がある。
- 米中関係ケースはReferenceとの時間断面のズレにより大半の軸がNOT_EVALUABLE。この結果は「AIの能力を測れなかった」ことを意味し、「AIが劣っていた」ことを意味しない。
- スコアリングは筆者(Claude)による0-3のsemantic rubric判定であり、江口さん本人による検証を経ていない——この比較結果自体も`RECONSTRUCTED_NOT_EXPERT_CONFIRMED`の性質を持つ。
