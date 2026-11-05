# Geopolitical Expert AI — Phase B (Conflict/Duplication Classification) & Phase C (Canonical Reasoning Flow Design)

**前提**: `as_is_reasoning_audit.md`のAs-Is監査結果を受け、ユーザーの指示に基づき「5つの責務バケットへの再分類」→「KEEP/MERGE/MOVE/RESTRUCTURE/SIMPLIFY/DEPRECATE/REMOVEの分類」→「Human Strategic Interactionを中心とした正典的推論フローの設計」を一続きで行う。

**この文書のスコープ**: 設計のみ。コード変更は一切含まない。Phase D(実装)へは、この設計をユーザーが承認してから進む。

**中心原則(As-Isが示した核心的欠陥への直接対応)**:
1. Human Strategic Interactionは「部品はあるが連鎖がない」→ 共有キーで構造的に接続する
2. Main Scenarioは現在Human Interaction分析より前に確定する → 順序を反転させる
3. 新しいフィールドを増やすのではなく、既存の分散した部品を再利用・統合する → **正味のフィールド数は減少させる**

---

## Phase B: 責務バケット別 KEEP/MERGE/MOVE/RESTRUCTURE/SIMPLIFY/DEPRECATE/REMOVE 分類

### Bucket 1: Situation Framing(Cue/Observation — 何が起きていて何が通常と違うか)

| 現行要素 | 分類 | 理由・再利用先 |
|---|---|---|
|`initial_anomaly`|**MERGE**|専用ノートもGuardも無い(As-Is Section M)。単一のCue/Observation入口へ統合し、`focus_selection`が読む唯一の入力にする|
|`focus_selection.trigger_types`|**RENAME + MOVE**|"Anomaly種別"ではなく「どの思考Lens(政治的インセンティブ/Actor Model不一致/媒体乖離等)を起動するか」という**Activated Lens**へ改名。Cueそのものの記録(上記`initial_anomaly`統合後の単一Cueフィールド)と、Lens起動の判断は別物として明確に分離する|
|`disconfirming_signal`|**MOVE**|Situation Framingの仕事ではなく、Bucket5(Scenario/Validation)の「反証条件」そのもの。後述の統一Discriminator概念へ統合|
|media anomaly(`media_editorial_context`/`narrative_deviation`)|**KEEP(構造は維持)**|Stage2 deep限定の内部規律(Observable/Hypothesis分離)は質が高く、Cue入力の一種として機能する。ただし`primary_key_point`との構造的リンクIDが無い問題は、新規IDフィールドを増やさず「Cue Evaluationの入力の一つ」として一段上位のCue処理へ委譲することで解消(スキーマ追加なし)|
|Actor Model Challenge(検知の側面のみ)|**MERGE(分割の一部)**|「既存Actor Modelと合わない」という検知はCueの一種(model_mismatch)としてここへ吸収。ただし対応(どう仮説に反映するか)はBucket4へ分離(RESTRUCTURE、後述)|

### Bucket 2: Control / Actor Resolution(誰・何が動かすか)

| 現行要素 | 分類 | 理由・再利用先 |
|---|---|---|
|`control_type`|**KEEP**|Structure vs Actorの一次分類として有用、重複度は低い(As-Is Section F)|
|`controlling_entity_ids`|**KEEP / 昇格**|Control Pointの正典候補に昇格。「Key Actorに採用していない主体を指してよい」という現行設計は維持しつつ、**Key Actorと重複する場合は`key_actor_profiles.actor_ref`との一致を機械的に検証するGuardを新設**(現状皆無、Section G参照——実案件のドリフトが既に検出されている)|
|`decisive_power_and_leverage`(トップレベル)|**MERGE**|`controlling_entity_ids`/`control_type`の結論を案件全体で言い換えているだけの独立記述。**独立LLM生成をやめ、Control Point判定から機械的に要約・導出する決定的処理へ変更**(新規LLM出力ではなく既存判定の集約表示)|
|`key_stakeholder_assessment`|**SIMPLIFY**|`determination`(single/multiple/distributed/unclear)は`control_type`とほぼ同じ問いへの別回答。**「Decision-Maker Decomposition(個人の裁量が実質的に効くか)」を判定する単一のゲートへ単純化**——単純化後は「discretion_materially_matters: yes/no + 理由」のみを問い、yesの場合のみBucket3(Human Strategic Interaction)を必須化する。`selected_actor_ids`は**DEPRECATE**(誰も検証しておらず、`key_actor_profiles.actor_ref`と役割が重複——正典は後者に一本化)|
|Actor Intelligence選定(`select_key_actors`ランキング)|**KEEP(タイミングは限定的に見直し)**|完全なFocus後取得は現行CallBudget=3では追加呼び出しなしに実現不可(後述「Open Decision 1」)。当面は取得タイミングを維持しつつ、**Control/Actor Resolutionセクションが「Packのランキングとは独立に自分の言葉でwhy_actor_mattersを説明する」という既存の縛りを維持・強化**する|
|`stakeholders[]`/`additional_stakeholders`(レガシー派生配列)|**DEPRECATE**|`actor_ref`すら持たず、どのGuardにも読まれないオーファンリスト(Section G)。UI側の消費有無だけ確認の上で削除候補(Implementation Gap、後述)|

### Bucket 3: Human Strategic Interaction(人間同士の狙い・動かし方)

これが今回の再設計の核。**目標: 既存フィールドをほぼ全て再利用し、2つの最小限の新規リンクフィールドと1つの決定的比較処理だけを追加して、連鎖を構造的に閉じる。**

| 現行要素 | 分類 | 理由・再利用先 |
|---|---|---|
|`ultimate_objective`/`issue_objective`|**KEEP**|Interaction ObjectのActor.higher_order_goal / current_objectiveとしてそのまま再利用|
|`perceived_path`|**KEEP**|Interaction ObjectのMove.apparent_purposeの根拠として接続(明示的な参照関係を追加、新規フィールドではなくprompt上の接続指示)|
|`event_role`|**KEEP / 拡張(最小限の新規フィールド1個)**|`intended_target_actor_ref`を追加——「この一手は誰に対する手段/シグナルか」を明示。これがAs-Is Section Iで発見した最大の欠落(target参照が無い)への直接対応|
|`strategic_relationship_preference`|**RESTRUCTURE(スコープ限定)**|`target_actor_ref`を持たない設計欠陥(Section H)。**特定の相手を持つ内容は全て`directional_perceptions`へ書くことを必須とし、本フィールドは「特定の相手に紐付かない、Actorの一般的な外交スタイル」のみに用途を限定**(新規フィールド無し、用途を絞るのみ)|
|`directional_perceptions`|**KEEP / 昇格**|Human Strategic Interactionの構造的中核。**現在deepプロファイル限定だが、Decision-Maker Decomposition(Bucket2)がdiscretion_materially_matters=yesと判定した場合は必須化**(条件付き昇格。全件常時生成にはしない=スキーマ膨張回避)|
|`actor_expected_response`|**KEEP / 接続(最小限の新規フィールド1個)**|`target_actor_ref`を追加。現状A自身の`actor_ref`だけで結ばれ、誰への期待かが構造化されていない(Section I最大の欠落の2つ目)|
|`analyst_expected_response`|**KEEP / 接続**|同上、同じ`target_actor_ref`を共有|
|Perception Gap|**新規、ただし決定的処理のみ(LLM呼び出し無し)**|`actor_expected_response`と`analyst_expected_response`が同一`(actor_ref, target_actor_ref)`組で存在する場合、両者の内容差を検出する軽量な決定的Guard(新規LLM出力フィールドではなく、既存2フィールドの機械比較。差がある場合のみ`perception_gap_flag`的な軽量注釈を付与)|
|`preference_action_gap`|**MOVE(消費点の変更)**|フィールド自体は維持するが、消費先を「Bucket4のH1(Instrumental/Indirect)仮説生成のトリガー」に変更——現状どこにも接続されていない(Section I)ため、Hypothesis生成プロンプトが明示的にこのGapを読みに行く一文を追加(新規フィールドなし、接続を追加するのみ)|

### Bucket 4: Competing Interpretation(この行動の本当の意味は何か)

| 現行要素 | 分類 | 理由・再利用先 |
|---|---|---|
|`candidate_key_points`|**KEEP / スコープ限定**|「What matters(局面の軸候補)」のみに限定。現在混在しているH0-H4仮説的内容(`_KEY_POINT_NOTE`が要求するH0-H4カバレッジ)は**REMOVE**、Bucket4の`hypothesis_register`のみへ一本化|
|`hypothesis_register`|**KEEP / 順序前倒し + タクソノミー簡素化**|Scenarioより**前**に生成される(順序転換、後述Phase C)。H0-H4(5分類)を**H0 Straightforward / H1 Instrumental・Indirect / H2 Counterparty・Audience Shift**の基本3種＋条件付き**H3 Constraint・Domestic Driver / H4 Frame-breaking**へ簡素化(enum値のRENAME、配列構造自体は維持)|
|`strongest_counterargument`|**MERGE**|`hypothesis_register`内の「最有力の棄却済み/拮抗する仮説」を言い換えているだけの独立フィールド。hypothesis_registerへ統合し、独立フィールドとしては廃止|
|Actor Model Challenge(対応の側面)|**MERGE**|H4(Frame-breaking)のトリガー条件として吸収。`outcome`という誰も読まない列挙値をやめ、H4仮説の`status`(retained/rejected)そのものが答えになる|
|Candidate Generation Operators(10種)|**REBUILD / SIMPLIFY**|存在しない参照文書(`03_candidate_operators.json`)を前提にした死んだコメントを削除。10個の抽象的な3〜10文字の括弧書きを、H0/H1/H2(+H3/H4)生成に直結する**具体例付きの少数のOperator**へ再構成(例: Goal/Means→H1生成、Counterparty Shift→H2生成、Changed Constraint→H3生成)|
|仮説評価基準(新設、ただし既存フィールドの再利用のみ)|**REUSE(新規フィールドなし)**|Evidence Fit=`evidence_relevance`既存、Actor Model Fit=`pattern_applicability_assessment`既存(MERGE後はここに統合)、Relationship Fit=`directional_perceptions`のgrounding既存、Means/Timing Fit=`event_role`/`perceived_path`の整合既存、Counterparty Response Fit=Bucket3の**Perception Gap**(新設は決定的処理のみ)。5つの評価観点は**新しいスキーマフィールドを1つも追加せず**、既存フィールドを「仮説ランキング時に参照する観点」として明示的に束ねるプロンプト構造のみで実現する|

### Bucket 5: Scenario / Validation(その読みなら次に何が起きるか)

| 現行要素 | 分類 | 理由・再利用先 |
|---|---|---|
|`main_scenario`|**MOVE LATER**|Human Strategic Interaction(Bucket3)とCompeting Interpretation(Bucket4)の後に生成(順序転換、Phase C参照)。これがAs-Is最大の指摘(HIGH)への直接対応|
|`alternative_scenarios`|**KEEP / 生成点を一本化**|現状Stage1簡易版とStage2詳細版の二重生成・上書き構造を廃止し、新Stage3(後述)で一度だけ生成する|
|`disconfirming_signal`・`retraction_conditions`(main_scenario)・`switch_conditions`(alternative_scenarios)・`focus_selection.discriminating_observation`|**MERGE**|4つの近接概念("何を見れば結論が変わるか")を、**適用対象のレベル(仮説レベル/Scenarioレベル)で使い分ける1つの統一Discriminator概念**へ統合。仮説レベルは`hypothesis_register`側、Scenarioレベルは`main_scenario`/`alternative_scenarios`側、という既存の入れ物へ書き込み先を絞るのみで新規フィールドは増やさない|
|`monitoring_indicators`|**KEEP**|Discriminatorとは異なる「次に何を見るか」という独立した観測リストとして維持|
|`additional_research`/`identified_knowledge_gaps`|**KEEP / 入力元を拡張**|現状Stage1の`investigation_questions`のみから充填されるが、Bucket4で`unresolved`のまま残った仮説も入力元に加える(接続追加のみ、新規フィールドなし)|

### 横断: Model-update機構

| 現行要素 | 分類 | 理由 |
|---|---|---|
|Pattern Applicability Assessment|**MERGE**|Bucket4の「Actor Model Fit」評価観点へ統合、独立オブジェクトとしては廃止|
|Actor Model Challenge(`outcome`)|**MERGE**|上記+Bucket1のCue検知へ分割吸収済み(重複表記のため独立フィールドとしては廃止)|
|Persistent Preference Update(`propose_persistent_preference_candidates`)|**KEEP(変更なし)**|唯一永続DBへ実際に書き戻す機構であり、人間承認ゲート・バージョニング(`supersedes_id`)も適切。**トリガー元をBucket4の再構成済みhypothesis outcome(特にH4/Actor Model Fit)に接続する**ことだけ追加し、機構自体は変更しない|

### 横断: Guard / 実行方式

| 現行要素 | 分類 | 理由 |
|---|---|---|
|`validate_media_knowledge_item`クラスタ(6関数、到達不能)|**REMOVE(推奨)、ただしユーザー確認が必要**|確認済みdead code。将来のMedia KB機能で使う予定が無い限り削除推奨——Open Decision(後述)|
|confidence補正パイプライン(11関数、順序固定)|**KEEP**|設計自体は健全(順序の意図がコメントで明記済み)。フィールド所有者の変更(Stage再編)に伴い、各関数が読む対象オブジェクトの生成タイミングとの整合だけPhase Dで再確認する|
|レガシー`run_expert_request`(confidence補正パイプライン非対応)|**DEPRECATE(推奨)**|`execution_mode`既定は`staged`のため通常到達しないが、フォールバック経路として矛盾を抱えたまま残存するリスクを解消——Open Decision(後述)|

---

## Phase C: Canonical Reasoning Flow

### 概念上の14ステップと、責務バケット・論理ステージへのマッピング

| 元の14ステップ | 責務バケット | 論理ステージ |
|---|---|---|
|Event/Context|(入力、Stage0のまま)|—|
|Plausible Frames|Bucket1(`candidate_key_points`、スコープ限定後)|論理Stage 1|
|Cue/Anomaly Activation|Bucket1(統一Cue→Activated Lens)|論理Stage 1|
|Control Point Discovery|Bucket2(`controlling_entity_ids`昇格)|論理Stage 1|
|Decision-Maker Decomposition|Bucket2(簡素化された`key_stakeholder_assessment`ゲート)|論理Stage 1|
|Choice / Human・Political・Relationship Model|Bucket3(Interaction Object)|論理Stage 2|
|Primary Hinge / Primary Decision Question|`focus_selection.why_now`/`decision_sensitive_outcome`(Bucket4の問いとして再定義)|論理Stage 2|
|Interpretation Candidates|Bucket4(H0-H2+H3/H4)|論理Stage 2|
|Ranking/Rejection|Bucket4(5評価観点、既存フィールド再利用)|論理Stage 2|
|Dominant Flow|Bucket2の`control_type`確定(Interaction結果を踏まえ再確認)|論理Stage 3冒頭|
|Scenario/Mental Simulation|Bucket5(`main_scenario`/`alternative_scenarios`)|論理Stage 3|
|Discriminating Validation|Bucket5(統一Discriminator + `monitoring_indicators`)|論理Stage 3|
|Focus/Model Update|Persistent Preference Update(変更なし)|論理Stage 3後、既存の事後処理のまま|

### 物理LLM呼び出しへのマッピング(CallBudget=3を維持する案)

```
Physical Call A(1回目、必須)
  = 論理Stage 1(Situation/Control Resolution)
  + 論理Stage 2(Human Strategic Interaction + Competing Motives)
  ── 同一呼出内だが、schema property順序 + 構造的リンク
     (event_role.intended_target_actor_ref, actor_expected_response.target_actor_ref,
      hypothesis_register[].related_actor_refs 等、既存actor_ref共有によるGuard検証可能な接続)
     によって「同時解決」ではなく検証可能な依存関係を持たせる

        ↓ 「Call Aで確定した内容」として明示的にJSON dump(現行Stage1→Stage2パターンを流用)

Physical Call B(2回目、必須)
  = 論理Stage 3(Synthesis/Scenario/Validation)
  ── main_scenario/alternative_scenariosはここで初めて、かつ一度だけ生成
     (現行のStage1簡易版+Stage2詳細版の二重生成・上書きを廃止)

Physical Call C = Repair(条件付き、最大1回)
  ── MODE_V12_CHECK_NAMESを拡張し、新設した構造的リンク検証
     (Control Point↔Key Actor整合、Interaction↔Hypothesis actor_ref整合、
      Perception Gap算出前提の(actor_ref,target_actor_ref)組の整合)
     の失敗もRepairトリガーへ昇格させる(現状はadvisory・非repairのまま放置されている)
```

これにより**物理LLM呼び出し回数は現行と同じ(最大3回)**のまま、「ScenarioがInteraction/Hypothesisより先に確定する」という最大の構造欠陥を解消できる。`audience_signal`/`media_context`は引き続きPhysical Call Aの条件付きモジュールとして残し(Human-Actor軸とは直交する層のため無理に統合しない)、`situation_actor_assessment`/`directional_perception`の役割はPhysical Call Aの核(Interaction Object)へ吸収・格上げする形でモジュール数を実質削減する。

### Human Strategic Interaction Object(最小差分スキーマ)

新規に追加するのは**2つの参照フィールドと1つの決定的比較処理のみ**。オブジェクト自体を新設せず、既存の`key_actor_profiles`/`situation_actor_assessment`の中に配置する:

```
key_actor_profiles[]                          既存フィールドのみ
  ├─ ultimate_objective / issue_objective      (= higher_order_goal / current_objective)
  ├─ perceived_path                            (= Moveの根拠)
  └─ event_role
       ├─ role_type / explanation              既存
       └─ intended_target_actor_ref            ★新規1(参照のみ、自由記述ではない)

directional_perceptions[]                      既存フィールドのみ(昇格・条件付き必須化)
  ├─ observer_actor_ref / target_actor_ref      既存(= perception_of_[counterpart])
  ├─ perception / claim_type / evidence_ids     既存

situation_actor_assessment[]                    既存フィールドのみ
  ├─ actor_ref                                  既存
  ├─ actor_expected_response
  │    └─ target_actor_ref                     ★新規2(参照のみ)
  └─ analyst_expected_response
       └─ target_actor_ref                     ★新規2と同一組で共有

[決定的処理・新規LLM出力なし]
  perception_gap_check(actor_ref, target_actor_ref)
    = actor_expected_responseとanalyst_expected_responseが
      同一(actor_ref,target_actor_ref)組で両方存在する場合、
      内容差を検出しGuardの注釈として付与(confidence補正パイプラインへの追加関数、12個目)
```

これで、`directional_perceptions`(AのBへの認識)→`event_role.intended_target_actor_ref`(その認識に基づくAの一手、誰宛か)→`actor_expected_response`(Aの期待、対象B)→`analyst_expected_response`(分析者の予測、対象B)→Perception Gap、という一連が**共有するactor_ref/target_actor_refによって機械的に検証可能な鎖**になる。

### Hypothesis(Competing Interpretation)の簡素化

```
hypothesis_register[]                          既存配列を維持、hypothesis_typeのenum値のみ変更
  ├─ hypothesis_type:
  │    H0_straightforward                      (旧surface)
  │    H1_instrumental_indirect                (旧actor_intent_alternative、
  │                                              preference_action_gapが主トリガー)
  │    H2_counterparty_audience_shift          (旧structural_alternative、
  │                                              event_role.intended_target_actor_refが主トリガー)
  │    H3_constraint_domestic_driver           (条件付き、旧の一部を再構成)
  │    H4_frame_breaking                       (旧frame_breaking + actor_model_breaking統合、
  │                                              Actor Model Challengeの吸収先)
  ├─ status: retained/rejected/unresolved       既存のまま
  └─ reason                                    既存のまま、ただし5評価観点
                                                 (Evidence/Actor Model/Relationship/
                                                  Means-Timing/Counterparty Response Fit)
                                                 を明示的に参照する自由記述へ強化
```

---

## 正味の変化(「小さく・一貫性のある」原則の検証)

| 分類 | 件数 |
|---|---|
|REMOVE/DEPRECATE(独立フィールドとして廃止)|`decisive_power_and_leverage`(独立LLM生成→導出処理化)、`strongest_counterargument`、Actor Model Challenge(独立オブジェクト)、Pattern Applicability Assessment(独立オブジェクト)、`key_stakeholder_assessment.selected_actor_ids`、`stakeholders[]`/`additional_stakeholders`、hypothesis_type enum 5値→実質3〜5値へ整理、media-KB dead guard 6関数 = **合計8〜9個のフィールド/オブジェクト/関数を削減**|
|MERGE(統合先へ吸収、独立表記を廃止)|`initial_anomaly`→Cue統一、`disconfirming_signal`+`retraction_conditions`+`switch_conditions`+`discriminating_observation`→統一Discriminator = **合計6個の近接概念を2つの統一概念へ集約**|
|新規追加(最小限)|`event_role.intended_target_actor_ref`、`actor_expected_response`/`analyst_expected_response.target_actor_ref`、Perception Gap決定的関数1個 = **合計3個の最小追加(すべて参照フィールドか決定的処理、新規LLM生成フィールドはゼロ)**|

**結論: フィールド/オブジェクト単位で見て、削減8〜9・統合6・新規3という比率であり、「新しい概念を積み増す」設計ではなく「減らして繋ぐ」設計になっている。**

---

## 保持すべき既存の安全策(変更しないことを明示)

- 捏造Evidence IDスクラブ(`scrub_fabricated_evidence_ids`、`directional_perceptions`対応済み分含む)
- `persist=False`とその全DB書き込みパスのゼロ書き込み保証
- Candidate(仮説)とScenario(予測)の区別そのもの(呼び出しタイミングを変えるだけで、概念区別は維持)
- `actor_expected_response`/`analyst_expected_response`の分離(廃止せず、むしろ接続を強化)
- `directional_perceptions`のA→B/B→A非対称記録の原則
- Actor Big Flow(`perceived_path`/`event_role`)の改善
- Research Sufficiency Gateの挙動(RESEARCH FILLS EVIDENCEのまま、本設計ではRESEARCH CHANGES ANALYTICAL MODELへの拡張は提案しない)
- Evidence/Provenance処理(`attach_actor_intelligence_provenance`等の決定的処理)
- Relationship永続データの欠落(`NOT_EVALUABLE_DATA_GAP`)は引き続き偽装しない——Session3インタビュー結果を待つ

---

## Open Decisions → 確定(2026-09-07、ユーザー承認済み)

1. **Actor Intelligence取得タイミング**: CallBudgetは増やさない。事前取得(Focus決定前)は維持。ただし候補選定を「issue紐付け+1-hop Relationship上位5名固定」から、**Foundation Packの構造的Entity/Leverage Card情報とのCandidate整合を加点する**方向へ調整し、Control Point Discoveryが実際に注目する主体をあらかじめ拾いやすくする(スコアリング関数の調整のみ、呼び出し構造は変えない)。Focus後取得(理想形)は今回見送り。
2. **legacy配列/古いschema(`stakeholders[]`/`additional_stakeholders`、`hypothesis_type`旧enum値等)**: 即削除しない。**deprecated化**(新フローから参照・生成しない、既存フィールド自体とデータは残す)。UI等の互換性確認後、別フェーズで削除判断。
3. **media-KB dead guard 6関数**: 将来のMedia Intelligence再接続予定が確認できない限り、**REMOVEはせず`deprecated`コメントを付けて残置**(安全側)。今回のPhase Dスコープには含めない。
4. **legacy `run_expert_request`**: Phase Dの機能追加・変更対象から除外し、staged経路との差異をこれ以上広げない。削除・改修は別フェーズ。

## Phase D 実装の絶対条件(ユーザー指定、2026-09-07に条件4を追加)

1. `directional_perceptions → event_role → actor_expected_response → analyst_expected_response` が同一actor/targetの共有キーで機械的に追跡できること(**接続性の保証のみdeterministic。認識のズレの意味解釈はLLM推論に委ねる——後述の責務分離原則を参照**)
2. `hypothesis_register`の評価結果がmain_scenario生成前に必ず存在すること
3. Stage2相当のHuman Interaction分析がpost-hocではなくMain Scenarioの入力になること
4. **Scenarioは「Interactionを受け取った」だけでなく、選択されたHypothesisを使って生成されること**——main_scenarioは`retained hypothesis→参照するactor pair→想定interaction→main scenarioの帰結`という因果順序で導出され、これを検証可能にする最小限の構造的リンク(`main_scenario.derived_from_hypothesis_id`)を持つこと

## 責務分離の原則(2026-09-07追加)

> **構造的な接続(鎖が繋がっているか)はdeterministicに保証する。**
> **人間の思惑・認識差の意味解釈(どうズレているか)はLLM推論にやらせる。**
> **Scenarioは、その人間理解から選ばれた仮説の結果として初めて生成する。**

この原則により、D-4のGuardは「意味比較」を一切行わない。意味評価(Perception Gapがどう重要か等)は`hypothesis_register.reason`内でLLMが行う。

## Phase Dでやらないこと(ユーザー指定)

- 新しいHuman Strategic Interaction巨大オブジェクトを作らない
- 新しいActor Profile schemaを増やさない
- 新しいH0-H4派生カテゴリを大量追加しない
- 新しいLLM callを増やして解決しない

具体的な実装計画は `phase_d_implementation_plan.md` を参照。
