# AI Review Pack — Part B（Zelenskyy Oval Office Clash）

**この資料はPart A(Blind Expert Assessment)完了後にのみ提示してください。**
分析対象: Frozen AI Analysis(`docs/media-intelligence/m9_raw_responses/
m9_D_integrated_result.json`にM9.1決定的補正を事後適用したもの。Actor Intelligence
ON+Media Intelligence ON+Evidence Weighting Fix ON、実Azure実行、2026-08-31)。
本Packは意図的に「AIの能力を良く見せる資料」にしていません——Low confidence・
Unknown・矛盾するEvidence・棄却された仮説・Quality Guard違反履歴も含めて提示します。

---

## 1. Situation

- **Situation**: 2025年2月28日のオーバルオフィスでの米・ウクライナ会談決裂
  (鉱物資源合意の署名中止)から、3月3日〜11日の米国による対ウクライナ軍事支援・
  情報共有の一時停止、3月11日のジェッダ(サウジ)協議での関係正常化まで。
- **Timeframe**: 2025-02-28〜2025-03-11(+その後の情報環境への波及)
- **Core question**: この局面のKey Pointは何か、鍵となるActorの本当の優先順位は
  何か、情報環境をどう解釈すべきか、今後のScenarioは何か。
- **Why it matters**: 米国の同盟支援が「安全保障コミットメント」から「交渉カード」
  へ変質しつつあるかどうかを判断する試金石であり、今後の停戦交渉・欧州の対米
  依存度・ロシアの情報戦略に直接影響する。

## 2. Key Point Assessment(★本Packの最重要部分)

- **Primary Key Point**: 米国が対ウクライナ軍事支援・情報共有を交渉カードとして
  一時停止し得る構造(支援の条件付き性)
- **Control type**: actor_controlled(controlling_entity: Trump)
- **Primary hinge**: 米国大統領が支援・情報共有の一時停止・再開の裁量を交渉カード
  として用いるかどうか
- **Secondary hinges**: (1)ゼレンスキーが資源合意・停戦条件のRed Lineをどこまで
  譲歩可能とみなすか (2)NATO・欧州が米国の一時停止をどこまで補完するか
  (3)ロシアがこの対立を戦場エスカレーション・停戦条件硬化にどこまで結びつけるか
- **Selection rationale**: 時系列(口論→合意破談→支援停止→ジェッダで正常化)が、
  支援停止を交渉の一部として設計されたものとみる説明で最も整合的に説明できる。
- **Candidate key points(4件)とrejected理由**:
  1. (採用)支援の条件付き性
  2. ゼレンスキーのRed Line — 「ウクライナ側の一貫した傾向であり、局面固有の変化を
     規定しているのはむしろ米国側の新しいレバレッジの使い方」として不採用
  3. ロシアの「西側分裂」ナラティブ — 「局面の発生原因ではなく結果の一部」として
     不採用
  4. NATO・欧州の支援継続性 — 「secondary hingeとして扱う」として不採用
- **Supporting evidence**: 時系列の連動性、支援停止の期間限定性(3/3-3/11)、
  **トランプの過去の対イラン・ホルムズ海峡事案(取引志向パターン)**、ゼレンスキーの
  Persistent Preference(Red Line維持)
- **Contradicting evidence**: 「米政府・議会が支援停止理由を資源合意・対立と
  明確に無関係と否定する公式発言」「ジェッダ合意文書に技術的調整のみが記載」等
  (発生していれば本Key Pointの見立てを覆す)
- **Disconfirming signal**: 米国が今後、支援を一切交渉カードとして用いない、
  または支援停止が資源合意と無関係な国内要因(予算・技術的問題)だと一次情報で
  確認される場合
- **Confidence**: 中

## 3. Key Actor Assessment

### Actor: Donald Trump
- **Persistent Objective**: 米国第一(America First)路線での政策遂行、国内製造業・
  雇用保護を対外交渉の優先目標に位置づける
- **Current Priority**: 鉱物資源合意を米国内産業に有利な条件でまとめつつ、対ロ
  戦争の負担を抑える
- **Stated Preference**: 「米国の利益に資するディールを求める」
- **Estimated Real Preference**: 「資源合意を対ウクライナ支援・停戦条件とパッケージ
  で取引したい」
- **Intrinsic/Instrumental**: mixed(資源合意はintrinsicな関心、支援自体は
  negotiating_card寄り)
- **Red Line**: 「国内政治的に許容できないほどの長期・大規模関与」(局面固有の
  Red Lineは実は不明——AI自身が`red_line`欄で「不明」と明記)
- **Tradeable**: 支援の規模・条件(currently_tradeable=はい)
- **Constraint**: 議会・同盟国の反発、憲法上の三権分立
- **Leverage**: 対ウクライナ支援の一時停止・再開裁量、資源合意条件の提示・拒否
- **Relevant Historical Decision Pattern**: `trump_pattern_pressure_leverage_
  dependent_counterpart`(相手が米国へ構造的依存を持つ場合、圧力先行で実質的譲歩を
  引き出す。confidence=中)。ホルムズ海峡事案(20%通航料案→撤回しつつ圧力維持)を
  直接引用。
- **Relevant Exception**: `trump_pattern_threat_action_gap`(脅しと実行水準の
  乖離。NATO離脱言及は一度も正式手続きに至っていないが、パリ協定・イラン核合意
  では実際に離脱完遂——**「単一のconfidenceでは評価しきれない不確実性が残る」と
  Actor Profile自身が明記**)
- **Strategic Communication Pattern**: 非公式チャネル(SNS)で極端な初期姿勢を
  先に発信し、その後正式な政策行動に移る傾向(confidence=中)。Trial balloon
  history: NATO GDP比4%目標(2018年提起→不採用→2025年5%として実現)
- **AI Assessment / Evidence / Confidence**: 中(evidence_ids=null——**Actor
  Decision Patternへの参照はあるが、今回Case固有の直接Evidence IDは無い**)

### Actor: Volodymyr Zelenskyy
- **Persistent Objective**: 国家主権・領土一体性の維持、西側同盟国からの継続的な
  軍事・財政支援の確保
- **Current Priority**: ロシアの実質的撤退と検証可能な安全保障措置を伴う停戦
- **Stated Preference**: 「公正で持続的な平和を求める」
- **Estimated Real Preference**: 「ロシアに有利な停戦案は拒否し続ける」
- **Intrinsic/Instrumental**: intrinsic(停戦条件は本質的関心そのもの)
- **Red Line**: ロシアの撤退・検証なき停戦、領土譲歩を含む合意(currently_
  tradeable=いいえ)
- **Constraint**: 戦場の状況、対外支援依存
- **Leverage**: 継戦意思と国内支持、対米・対欧への道義的圧力
- **Relevant Historical Decision Pattern**: `zelenskyy_pattern_red_line_holds`
  (同盟依存度が高い相手との交渉でも核心的Red Lineは公開対立コストを負ってでも
  譲らない。confidence=高)。

  **⚠ same-case observation(独立したHistorical Precedentではない)**: この
  Patternの`supporting_precedent_ids`が指す先例(`c847ef10-...`)は、**今回まさに
  評価対象となっているオーバルオフィス衝突そのもの**である。これは「過去の別事案
  からの独立した根拠」ではなく、「今回のCase自体を1件の観測として記述したもの」に
  過ぎない——Actor Assessmentの独立した裏付けとして数えてはならない
  (Section2/M9.1で確立した「Historical Pattern≠Current独立Corroboration」の
  原則そのものが、ここでは「Historical Patternの中身自体が今回のCaseと同一」と
  いう、より根本的な形で成立している)。

  **専門家への質問**: この根拠(同一事件)を除いた場合でも、ゼレンスキーについての
  このActor Assessment(核心的Red Lineは公開対立コストを負ってでも譲らない)は
  妥当だと考えますか？ 他にどのような独立した過去の事例があれば、この評価を
  支持できると考えますか？
- **Relevant Exception**: `zelenskyy_pattern_conditional_flexibility`
  (対米圧力の強弱に応じ、選挙延期という国内制度的論点への公式立場を複数回変化)
- **Strategic Communication Pattern**: 各国固有の歴史的記憶にカスタマイズした
  訴求(confidence=低)。「弾薬が必要だ、送迎ではない」という広く引用される発言は
  匿名リークが初出で本人の同時代的確認はできていない、との留保が明記されている。
- **AI Assessment / Evidence / Confidence**: 中(evidence_ids=null)

### Actor: Vladimir Putin
- **Persistent Objective**: ロシアの大国としての地位・影響圏の維持、NATO東方拡大
  の阻止
- **Current Priority**: 西側がウクライナ支援に疲弊し分裂しているという印象の定着
- **Stated Preference**: 「西側は分裂している」との主張
- **Estimated Real Preference**: 「分裂ナラティブを最大限利用したい」
- **Intrinsic/Instrumental**: domestic_signal(対立利用そのものが国内向け手段)
- **Red Line**: NATOの旧ソ連圏へのさらなる拡大
- **Tradeable**: 西側結束の弱体化は交渉上の優位として利用(はい)
- **Constraint**: エネルギー輸出収入への財政的依存、戦場の実態
- **Leverage**: プロパガンダ・情報戦、戦場での攻勢
- **Relevant Historical Decision Pattern**: `putin_pattern_deny_then_admit`
  (軍事的現状変更を当初否認、後に段階的に事実を認める。confidence=中)
- **Relevant Exception**: `putin_pattern_rhetoric_action_gap`(威嚇的発言と
  実行水準の乖離、ワグネル反乱での即時恩赦。トップダウン統制の限界を示唆)
- **Strategic Communication Pattern**: 統制されたチャネル(Direct Line/Valdai/
  国営メディア)で物語を先にコントロールし、後から必要に応じ修正(confidence=低)
- **AI Assessment / Evidence / Confidence**: **低**(salience=medium、他2名より
  明確に低い。observed_actionも「推定されるが具体的発言はここでは未確認」と
  自己申告している)
- **Alternative Interpretation**: 「ロシアがこの事件を過度に利用すると、第三国
  からの信頼を損なう可能性」(status=weak)

## 4. Decision Dynamics

```
Decision maker   : Trump(支援・情報共有スイッチの実質的操作権)
Veto player      : Zelenskyy(資源合意・不利な停戦案への署名拒否権)
Broker           : サウジアラビア(ジェッダでの場の提供・仲介)
Constraint       : 米議会・NATO・欧州同盟国(極端な方針転換を抑制)
Leverage holder  : Trump(支援on/off) / Zelenskyy(継戦意思・国際世論)
Spoiler          : Putin(対立の情報戦的利用、ただし戦場行動への転化は限定的)
```

```
Trump(資源合意条件を提示・支援停止で圧力)
  ↓ pressures
Zelenskyy(Red Line維持のため公開対立を選択)
  ↓ constrains(戦場・支援依存という制約下での対応)
NATO・欧州(支援を部分的に補完、対米への政治的圧力)
  ↔ brokers: サウジアラビア(ジェッダでの関係正常化の場を提供)
  ↓
Putin(対立を「西側分裂」ナラティブとして利用、ただし戦場での大規模行動変化は限定的と推定)
```

AIの`decisive_power_and_leverage`原文: 「結果を最終的に動かす力は、対ウクライナ
軍事支援・情報共有のスイッチを握る米国大統領（トランプ）と、戦争継続・停戦条件を
決めるロシア側（プーチン）の組み合わせにあり、ゼレンスキーは自国の主権・停戦条件
に関するRed Lineを通じて交渉の方向性を制約する役割を持つ。」

## 5. Information Environment(比較、記事件数ではなく質を見る)

| Lens | 内容 |
|---|---|
| Global mainstream | 「破局・屈辱」framing優勢(米欧ワイヤー・大陸欧州紙) |
| Ukraine-side | 大統領府は衝突への直接公式言及を確認できず(別Case知見、本Packでは
  簡略化のためAI出力に含まれる範囲のみ記載) |
| Russia-side(official) | 代理人(報道官級)による強い調子の発信、プーチン本人の
  同時代的発言は未確認 |
| Specialist analysis | ISW/ACLEDが時期的相関を指摘しつつ因果は明示的に「unclear」 |
| Physical/OSINT | ACLED週次データ(民間人死者29%増、クルスク突出部約2/3奪還) |
| Alternative/third-country | 中国・トルコ・サウジは対立の是非より自国ナラティブ
  補強へ転換する傾向(Media Pattern) |

## 6. Media / Signal Intelligence

Observed/Hypothesisを明示的に分離(以下はFrozen AI Analysisのmedia_source_
context原文、M9.1補正適用後):

| # | Hypothesis(AI解釈) | Source attribution | Historical Media Pattern | Confidence | Alternative interpretation |
|---|---|---|---|---|---|
| 1 | 同一国pressは自国政府公式声明より感情的・道義的framingで対立を描く傾向 | single_source_only | `media_pattern_press_more_emotive_than_official_statement` | 低 | 投資報道が軍事専門家・OSINTの留保する因果を過大主張している可能性 |
| 2 | 単一匿名当局者に依拠したスクープはtrial balloonの可能性があるが独立裏取りなしに慎重に扱うべき | behavioral_corroboration(target=underlying_event_only) | `media_pattern_anonymous_backchannel_claim_meets_denial`(Zelenskyy/Takaichi両Case横断) | **中(元は高、M9.1補正で引下げ)** | 当局者が責任回避・相手非難のため偏った情報を流している可能性 |
| 3 | 第三国報道は対立の是非より自国既存ナラティブ補強を優先 | behavioral_corroboration | `media_pattern_third_country_self_interested_reframing` | 中 | 一部第三国は当事者性を薄める抑制的報道を選ぶ例外あり |

**#2には`confidence_adjustment_reason`が記録されている**: "Historical actor/
media patterns provide contextual plausibility but do not independently
corroborate the current anonymous-source hypothesis."(14章で詳述)

## 7. Evidence Conflict

### Conflict A: 情報共有停止の戦況への因果

| | 内容 |
|---|---|
| Claim/Interpretation | 「米情報共有停止がロシア軍の前進を助け、多数のウクライナ兵が死亡した」 |
| Source A(TIME、匿名当局者複数) | 「この停止の結果、数百人のウクライナ人が死亡した」と明確な因果主張 |
| Physical/OSINT(ISW、3/5・3/9) | 「時期的相関は顕著だが直接的因果は不明」、2024年8月からの既存攻勢トレンド・北朝鮮軍増援という交絡要因を明記 |
| Physical/OSINT(ACLED、週次) | 「両方が奪還を後押しした可能性がある」というhedge付き表現 |
| AI weighting | Frozen AI Analysisの`media_source_context`はこの具体的因果主張を独立したhypothesisとして明示的には採用せず、より一般的なMedia Pattern(#1)のalternative_explanationとして間接的に言及するに留めている |
| AI conclusion | 明確な結論は出しておらず、`unknown_or_uncertain`欄で「一次情報がなく仮説レベル」と留保 |

### Conflict B: 支援停止=交渉カード vs 技術的調整

| | 内容 |
|---|---|
| Main Scenario(AI主解釈) | 支援停止は資源合意・交渉カード化の一部(confidence=中) |
| Alternative Scenario 1 | 「戦況評価・予算・技術的問題に基づく一時調整であり、因果は弱い」(confidence=低) |
| Actor historical pattern | Trumpの取引志向パターン(ホルムズ海峡事案)は主解釈を支持 |
| AI weighting | 主解釈をより整合的とするが、`strongest_counterargument`/`contradicting_evidence`欄で代替解釈の反証条件を明示的に保持 |
| AI conclusion | confidence=中に留め、一次情報(公式理由の明示)が出れば見直す前提を明記 |

## 8. AI Self-Correction Case(M9の失敗事例、削除していません)

- **Initial AI interpretation**: media_source_context hypothesis#2の初期出力
  (M9.1補正前)は`confidence=高`、`corroboration_basis=behavioral_
  corroboration`、`corroboration_target=underlying_event_only`のまま
  「trial balloonである可能性がある」と評価していた。
- **Why confidence became too高**: Actor Intelligence(Trumpの取引志向・非公式
  チャネル先行発信パターン)とMedia Intelligence(匿名スクープ=trial balloon
  というPattern)が同じ方向を示唆したため、AIはこの「2つのPriorの一致」を
  「独立した裏付け」であるかのように扱ってしまった。
- **Which Quality Guard detected it**: `anonymous_sourced_hypothesis_
  confidence_capped`(既存Session2 Quality Guard、`corroboration_target=
  hypothesis_itself`必須の契約に違反として検出)。
- **How M9.1 corrected it**: `apply_confidence_weighting_fix`という決定的
  Python処理(追加Azure Callなし)により、confidence=高→中へ自動的に引き下げ、
  `confidence_adjustment_reason`を記録。
- **Current interpretation**: confidence=中、「(trial balloonの可能性は)
  独立した裏取りがない限り慎重に扱うべきである」という留保文言はそのまま維持。

### 専門家への質問

1. あなたならこの匿名情報(単一当局者複数名による「口論の具体内容・支援停止の
   真因」報道)をどの程度重視しますか？
2. どの追加Evidenceがあればconfidenceを上げますか？
3. Actorの Historical Pattern(トランプの取引志向・非公式先行発信の傾向)は、
   今回のこの匿名情報を評価する際、どの程度使いますか？

## 9. Scenario Options

### Main Scenario(confidence=中)
支援停止は資源合意・広義の対ロ戦略・国内雇用政治に有利な条件を引き出すための
交渉カードだった。ゼレンスキーはRed Line維持のため公開対立の高コストを受け入れた
が、ジェッダで条件調整・面子回復の後、正常化に応じた。
- **Blocking actor**: 議会・NATO・欧州(極端な方針転換を抑制)
- **Trigger**: 資源合意署名中止→支援停止という時系列
- **Leading indicators**: 米政府・議会の支援決定の規模・条件・期間、ジェッダ後の
  資源合意再提示の有無
- **Switch condition**: 一次情報で支援停止理由が技術的要因のみと確認される場合
- **Disconfirming evidence**: 米政府が資源合意・対立との関連を明確に否定する
  公式発言

### Alternative Scenario 1(confidence=低)
支援停止は戦況評価・予算・技術的問題に基づく一時調整であり、資源合意・公開口論
との因果は弱い。
- **Switch condition**: 一次情報で技術的要因のみと確認され交渉カード説が否定
  される
- **Disconfirming evidence**: ホワイトハウス・国防総省・議会証言で資源合意/
  対立が支援停止に影響したと示唆される情報

### Alternative Scenario 2(confidence=中)
今回の対立と支援停止は、米国が対ロ戦争から距離を取りウクライナ支援縮小へ向かう
構造的転換の初期兆候であり、今後支援削減・停戦圧力が強まる。
- **Switch condition**: 複数年にわたり米支援が縮小し停戦圧力が顕著に強まることが
  確認される
- **Disconfirming evidence**: 支援停止後、米支援が元の水準・増加へ戻る

## 10. Scenario Derivation(Main Scenarioの因果Chain)

```
Key Point: 米国の支援を交渉カードとして扱うか同盟コミットメントとして扱うか
  ↓
Actor objective: Trump=資源合意で米国内利益確保、対ロ負担軽減
  ↓
Actor decision: 支援・情報共有の一時停止(3/3-3/11)
  ↓
Counterparty reaction: Zelenskyy=Red Line維持で公開対立を辞さず、ジェッダで条件調整に応じる
  ↓
Information/media signal: ロシア公式は代理人発信で「西側分裂」ナラティブ、Media Patternが示す通り第三国は自国ナラティブへ転換
  ↓
Scenario: 支援継続を条件付きレバレッジとして用いる「パッケージディール」構図が今後も継続
```
