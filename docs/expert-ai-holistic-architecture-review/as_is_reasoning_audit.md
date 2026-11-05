# Geopolitical Expert AI — As-Is Reasoning Audit

**目的**: Phase A(Architecture Inventory、6エージェント)で発見された「概念の重複・分散」を前提に、実際のランタイム挙動を追跡し、「Event→選択されたFocus→Actor/Human解釈→競合仮説→Scenario結論」という連鎖が、コード上どこまで実在するかをFactとして確定する。To-Be設計は本文書では提案しない。

**手法**: 7つの読み取り専用調査エージェントが `geo_expert_engine.py`(orchestration)・`geo_expert_schema.py`(prompt/schema)・`schemas/geo_expert_ai_response.schema.json`・`geo_expert_quality_guard.py`(Guard)・`geo_actor_intelligence_service.py`・`geo_expert_research_service.py`・`geo_persistent_preference_service.py`・`geo_relationship_service.py` を実際にfile:line単位で読み、ドキュメント上の意図ではなくコードの動作を報告した。以下はその統合。

---

## A. Current Expert AI in one sentence

Stage1という単一のLLM呼び出しが、Actor Intelligence(Focus決定前に機械的に厚く取得済み)を背景情報として与えられた状態で、Key Point・Focus・Stakeholder・Actor Profile・Main Scenarioを含む約20個の分析フィールドを**同時に**生成し、その後Stage2(条件付き・最大1回追加)が人間関係寄りの深い分析(Hypothesis Register・Directional Perception・Situation Actor Assessment)を**Stage1の結論には手を出せない形で**後付けし、決定的なQuality Guardが主に「各フィールドの内部整合性」を検証・confidence補正するが、Focus/Anomaly/Actorの間の**相互作用**は検証も強制もされない——という、単発生成＋後付け深掘り＋自己整合性チェックのアーキテクチャ。

## B. Actual runtime flow

`run_expert_request_with_mode` → `run_expert_request_staged`(`geo_expert_engine.py:1337`)。normal deep case_analysis pathの実際の順序:

| # | Step | 種別 | file:line | 入力 | 出力 | 消費者 |
|---|---|---|---|---|---|---|
|1|`build_source_pack`|決定的|`geo_expert_engine.py:1361`(def 265-392)|theme_id, query_text|source_pack(text/document_ids/evidence_ids)|user_prompt, known_ids, Guard|
|2|Research Sufficiency Gate + 条件付きExternal Research|決定的Gate→条件付きLLM/web検索|`:1368-1390`|source_pack|(再構築された)source_pack, final_sufficiency|user_prompt(Stage1より前に完了)|
|3|Knowledge Block + System Prompt構築|決定的|`:1392-1394`|mode|stage1_system_prompt|Stage1 LLM|
|4|base user_prompt組立|決定的|`:1396-1398`|source_pack, query_text|user_prompt|後続の全ブロック追記先|
|5|Case Context取得|決定的|`:1404`(def 722-894)|case_id|case_context_text|user_prompt|
|6|Foundation Pack + 構造的コンテキスト|決定的|`:1407-1410`|case_id|structural_text|user_prompt|
|7|Persistent Preference Block|決定的|`:1416-1419`|case_id|pp_block|user_prompt|
|8|**Actor Intelligence Pack取得**|決定的(LLM呼び出しなし)|`:1424-1430`(`geo_actor_intelligence_service.py:508`)|issue紐付け+1-hop Relationship|actor_intelligence_text(6番目のブロック)|user_prompt|
|9|Media Intelligence Pack|決定的|`:1437-1442`|—|media_intelligence_text|user_prompt|
|10|Institution Intelligence Pack|決定的|`:1450-1460`|Foundation Packの機関Entity|institution_intelligence_text|user_prompt|
|11|Bookkeeping(known_ids等)|決定的|`:1462-1503`|—|fabrication guard用ID集合|Guard|
|12|Stage1 schema構築|決定的|`:1526`(`geo_expert_schema.py:132-161`)|—|stage1_schema|Stage1 LLM|
|**13**|**Stage1 LLM呼び出し**|**LLM(1回、失敗時1回のみ再試行)**|`:1529-1531,1541-1543`|steps1-11で組み立てた完成済みuser_prompt|stage1_data(初期違和感〜Main Scenarioまで約20フィールド)|Stage2, merge, Guard|
|14|Stage2モジュール解決|決定的|`:1551`(`geo_expert_schema.py:274-280`)|mode, profile|使用するモジュール名リスト|Stage2 schema|
|**15**|**Stage2 LLM呼び出し(条件付き)**|**LLM(最大1回)**|`:1555-1569`|user_prompt + **「Stage1で確定した内容(前提として維持)」という明記付きでstage1_dataをJSON dump**|stage2_data|merge|
|16|決定的merge|決定的|`:1577`(`_merge_staged_response`, def 1252-1282)|stage1_data, stage2_data|response(単純dict.update)|以降の全処理|
|17|決定的Guard変換パイプライン(順序固定・累積的)|決定的|`:1580-1609`|response|confidence補正済みresponse|Quality Guard|
|18|Request/Source Pack永続化|決定的(persist=Falseなら省略)|`:1616-1631`|—|DB行|—|
|19|Quality Guard実行|決定的|`:1637-1640`(`geo_expert_quality_guard.py:1961`)|response|checks(pass/fail)|Repair判定|
|20|Repair(条件付き、最大1回追加LLM)|LLM(最大1回)|`:1652-1712`|**MODE_V12_CHECK_NAMESに該当する失敗のみ**がトリガー|パッチされたresponse|再merge, 再Guard|
|21|最終スキーマ適合検証|決定的|`:1718-1719`|response|—|—|
|22|Response永続化|決定的(persist=False時はin-memory)|`:1722-1748`|—|DB行, `geo_cases.final_output`更新|—|
|23|Audit Log|決定的(persist時のみ)|`:1766-1771`|—|—|—|
|24|Persistent Preference Candidate生成|決定的+条件|`:1779-1788`|`persistent_preference_change=="revised"`|候補行(feature flag既定OFF)|将来Caseの永続DB|
|25|Return|—|`:1790-1811`|—|最終dict|API呼び出し元|

**最重要事実**: Step8(Actor Intelligence取得)はStep13(Stage1のFocus/Key Point決定)より確実に前に実行される(`stage1_data`という変数自体がStep13まで存在しない)。Step15(Stage2)はStep13の結果を「確定済み・上書き禁止」という明示付きで受け取る——つまりStage2はStage1に対して構造的に**遡及できない**。Repair(Step20)はMODE_V12_CHECK_NAMESという狭いチェック集合のみがトリガーであり、`focus_selection`/`hypothesis_register`/`actor_model_challenge`関連の失敗はRepairを一切起動しない(該当Guardは全て`run_quality_guard`の`checks`一覧には載るが"非repair対象")。

## C. Actual reasoning flow

**中心的発見: Stage1は「同時解決」であり「逐次推論」ではない。** `build_stage1_schema()`(`geo_expert_schema.py:132-161`)は約20フィールドを1個の`additionalProperties: false`なJSONオブジェクトとして1回のLLM呼び出しで要求する。フィールド順序はJSON Schemaのproperties順に過ぎず実行順ではない。

しかしプロンプト文言レベルでは、明確な依存順序が繰り返し指示されている(強制はされない、コンプライアンスは検証不能):

```
candidate_key_points(2-4候補、control_type判定)
  → primary_key_point(選定理由+棄却理由)
  → focus_selection(Primary Key Pointとは別軸、"今なぜこれか")
  → key_stakeholder_assessment(個別Actorを見る前にまずここ)
  → key_actor_profiles(1-3件深掘り、objective階層)
  → preference_action_gap_summary
  (並行して)
  key_actor_profiles → hypothesis-chain(Actor→Goal→Perceived Path→Event Role→Expected Response→Desired State)
  → main_scenario(retainedのH0-H4仮説から生成する、という指示)
  → strongest_counterargument/contradicting_evidence
  → additional_research/identified_knowledge_gaps
```

`decisive_power_and_leverage`(トップレベル、案件全体の力学)には対応する専用ノートがなく、`key_actor_profiles`/`key_stakeholder_assessment`と相互推論的(mutually inferred)、明示的順序なし。`initial_anomaly`にも専用ノートが存在せず、この連鎖の外側に置き去りにされている。

**この連鎖の構造的な弱点**: `main_scenario`は「H0-H4のうちretainedになった仮説から生成せよ」と指示されるが、その`status`(retained/rejected/unresolved)を正式に記録する`hypothesis_register`はStage2限定(deepプロファイルのみ)のフィールドであり、**Stage1にはそもそも存在しない**。つまりStage1は「Stage2で初めて生成される構造物を参照して生成せよ」という、時系列的に実行不可能な指示を受けている。Stage1は自力でH0-H4相当の内部推論を`candidate_key_points`だけで再構築せざるを得ない。

**Center of Gravity(実際のプロンプト重み・データフローに基づく)**:
1. **Key Point/Frame選定** — 最も濃密なガード網(9個の専用チェック関数)、Stage1必須、Focus Selectionという第2レイヤーまで持つ
2. **Actor Modeling(Profile/Big Flow)** — 永続DBとの往復あり(唯一書き戻し経路)、6種の候補actorリストが並存するほど過剰投資
3. **Scenario/Evidence Grounding** — confidence補正パイプラインが11関数、実行順序が明示的に管理される
4. **Hypothesis競合(H0-H4)** — Stage2限定、Guardは1個のみで非repair、Main Scenarioへの実質的フィードバックは無い
5. **Human Intent Modeling / Relationship Modeling** — `directional_perceptions`/`actor_expected_response`など個別フィールドは存在するが、Section Iで示す通り互いに接続されておらず、実質的に最も投資が薄い

**Q1-Q7(診断的質問への直接回答)**:

- **Q1「Who is trying to make whom do what?」を問う最初のポイントは?** → **NOT PRESENT**として扱うべき。`event_role`(`key_actor_profiles[].event_role`)が最も近いが、role_typeは自由記述でtarget(誰に対して)を持たず、どの関数からも読まれない(`geo_expert_engine.py`/`geo_expert_quality_guard.py`に`event_role`の参照ゼロ)。
- **Q2「なぜこの手段・タイミング・対象なのか」を体系的に問うか?** → **PARTIAL**。`_CANDIDATE_GENERATION_OPERATORS_NOTE`が"Actor→Goal→Perceived Path→Event Role→Expected Response→Desired State"という連鎖を`hypothesis_register[].reason`という自由記述1箇所に narrateさせるのみで、構造化されたfield-to-fieldの強制はない。
- **Q3「表向きの目的 vs 実際の高次動機」を体系的に区別するか?** → **PARTIAL**。Goal/Means operatorが名指しでこれを指示するが、10 operatorのうち具体例を伴うのはこの1つのみで、他は3〜10文字の抽象的な括弧書きに留まる(参照される`03_candidate_operators.json`はリポジトリに実在しない)。
- **Q4「意図的に非自明な動機仮説を作るか?」** → **PARTIAL**。Goal/Means・Counterparty Shiftの2 operatorのみ具体的にそれを指示。「国内政治的立場」「圧力→交渉設定」のパターンはOperatorノートには無く、別のノート(`_FOCUS_SELECTION_NOTE`のbehavior_driving_force)にのみ部分的に存在。
- **Q5「その代替動機を永続Actor Intelligence/Decision Patternと突き合わせて検証するか?」** → **PARTIAL**。`pattern_applicability_assessment`/`actor_model_challenge`は存在し、Actor Intelligenceとの矛盾を検討する仕組みはあるが、**presence-checkのみ**(値=outcomeの中身はどのコードからも読まれない)。
- **Q6「AのBへの信念→A行動→Bの解釈→Bの反応、を1つの因果連鎖としてモデル化するか?」** → **NO**。Section Iで詳述。各リンクは孤立したフィールドで、共有キー(target_actor_ref等)による構造的接続がない。
- **Q7「その戦略的相互作用は実際にMain Scenarioへ影響するか?」** → **NO**。`main_scenario`はStage1で(Human Interaction系フィールドが1つも存在しない段階で)確定し、事後的な再計算経路はコード上どこにも存在しない(Section K参照)。

## D. Stage1 internal reasoning map

```
[前提として既に完成しているuser_prompt: Source Pack + Case Context + Foundation Pack
 + Persistent Preference + Actor Intelligence Pack(6番目) + Media/Institution Intelligence]
                          │
                          ▼
              ┌───────────────────────┐
              │   Stage1 単一LLM呼出    │  ← 1回のJSON生成、以下は全て同一呼出内の
              │ (call_llm_structured)  │     プロンプト上の推奨順序であり、構造的な
              └───────────────────────┘     強制・段階分けは一切ない
   initial_anomaly(専用ノート無し、連鎖の外側)
   decisive_power_and_leverage(専用ノート無し、mutually inferred)
        │
   candidate_key_points(control_type判定)
        │ 明示的A→B
   primary_key_point(selection_rationale)
        │ 明示的A→B
   focus_selection(Primary Key Pointとは別軸と明記)
        │ 明示的A→B
   key_stakeholder_assessment(個別Actorの前にここで判定)
        │ 明示的A→B
   key_actor_profiles(ultimate_objective→strategic_relationship_preference
                       →issue_objective→observed_action→perceived_path→event_role)
        │ 明示的A→B
   preference_action_gap_summary
        │ (並行)
   key_actor_profiles(同上フィールド群) ── Operator連鎖 ──▶ (仮説的推論、構造化先無し)
        │ 明示的A→B(ただしH0-H4のstatusを記録する箱=hypothesis_registerはStage2にしか無い)
   main_scenario
        │
   strongest_counterargument / contradicting_evidence
        │ 明示的A→B
   additional_research / identified_knowledge_gaps
```

## E. Stage2 internal reasoning map

Stage2は`_stage2_module_properties()`(`geo_expert_schema.py:181-221`)の5モジュールから、`resolve_stage2_modules(mode, profile)`が条件に応じて選ぶ(deep×case_analysisで最大5モジュール同時合成)。**Stage2プロンプトは`user_prompt + "【Stage1で確定した内容(前提として維持し、重複記述しないこと)】" + json.dumps(stage1_data)`という形でStage1出力を"確定済み"として明示的に固定する**(`geo_expert_engine.py:1561-1564`)。

| モジュール | 生成内容 | Stage1書き換え能力 | 実際の分類 |
|---|---|---|---|
|`audience_signal`|audience_analysis(新規key), key_actor_action_substance(patchのみ)|action_substanceサブフィールドのみpatch可、他のkey_actor_profilesフィールドは触れない|**POST-HOC ENRICHMENT**|
|`media_context`|media_source_context(新規key)|皆無、`primary_key_point`との構造的リンクIDが無いと設計者自身が明記|**POST-HOC ENRICHMENT**|
|`situation_actor_assessment`|situation_actor_assessment, investigation_questions, hypothesis_register(全て新規key)|皆無(investigation_questionsはadditional_researchへ追記のみ、書き換えではない)|**POST-HOC ENRICHMENT**(人間解釈として最も深いモジュールだが、Primary Key Point/Focus/Main Scenarioを全く動かせない)|
|`directional_perception`|directional_perceptions(新規key)|皆無。プロンプト自体が「core_profile側へ書き写すな」と明示的に禁止|**POST-HOC ENRICHMENT**|
|`deep_scenario`|alternative_scenarios(**Stage1既存の簡易版を丸ごと上書き**), additional_stakeholders(stakeholders[]へ追記)|`alternative_scenarios`のみ上書き。`main_scenario`はどのStage2モジュールのプロパティ辞書にも存在せず触れない|**唯一の例外**: `alternative_scenarios`はlast-write-winsで上書きされるが、これは仮説競合の結果ではなく単なる`dict.update()`の副作用|

merge実装(`_merge_staged_response`, `geo_expert_engine.py:1252-1282`): `response = dict(stage1_data)` → 2つの名前付きpatch(`stakeholders`, `key_actor_action_substance`)→ `response.update(stage2_data)`。分岐条件付きロジック("if control_type==Xならkey_actor_profilesをYする"等)は皆無。

## F. Key Point / Focus / Control relationship

| 責任 | 担当オブジェクト | 備考 |
|---|---|---|
|「何が最も重要か」|`primary_key_point`|`candidate_key_points`から選定、evidence必須|
|「なぜ今か」|`focus_selection.why_now`|primary_key_pointとは明示的に別軸と設計者が明記|
|「誰が/何が結果を制御するか」|**RESPONSIBILITY OVERLAP**: `control_type`+`controlling_entity_ids`(key_point_assessment) / `decisive_power_and_leverage`(トップレベル) / `key_stakeholder_assessment.rationale`/`decision_structure` / `key_actor_profiles[].decision_power`/`ability_to_move`/`ability_to_block`|詳細レベルの重複だけが1箇所("control_structure_summaryの詳細理由はkey_stakeholder_assessment.rationaleへ一元化")解消されているが、分類・結論レベルの4つのフィールドはノーガードで並存|
|「何を監視すべきか」|`focus_selection.decision_sensitive_outcome`(最も近い適合)|`disconfirming_signal`/`monitoring_indicators`と概念的に重複するが本監査では未評価(既知課題として温存)|
|**下流でActor/Scenario推論を実際に制御するのはどれか**|**どれでもない**|`geo_expert_engine.py`に`focus_selection`/`primary_key_point`/`hinge_structure`/`selection_rationale`の参照は**ゼロ件**。`control_type`はStep17のguardで自己整合性チェック(自分自身をunknownへ補正)のみに使われ、Actor選定やScenario生成へ波及する分岐は存在しない|

## G. Actor selection / Actor Intelligence relationship

**Key Actor選定に単一の正解ソースが存在しない(NO SINGLE ACTOR SELECTION SOURCE OF TRUTH)。実際には最低5つの独立した/相互未検証のActorリストが同時に存在する:**

1. issue紐付け候補 ∪ 1-hop Relationship展開候補(`get_candidate_actor_entities`) — 機械的・LLM非依存
2. `select_key_actors`によるスコア順位付け・上位5件キャップ — 決定的、Actor Intelligence Packとして提示されるだけで**LLMへの拘束力なし**
3. `key_stakeholder_assessment.selected_actor_ids` — LLMが自ら宣言するが、**`key_actor_profiles[].actor_ref`と実際に一致するかを検証するGuardは存在しない**
4. `key_actor_profiles[].actor_ref` — 事実上の正典(唯一、永続DBへの書き戻し経路`persistent_preference`を持ち、`situation_actor_assessment`/`directional_perceptions`から厳格にgrounding検証される)
5. `key_point_assessment.controlling_entity_ids` — 意図的に別のentity_id空間で、Key Actorに選ばれなかった主体を指してもよい設計。1つの非ブロッキングadvisoryチェック(`_key_point_controllers_covered_check`)のみが橋渡しし、**このチェック自体のdocstringが実案件でのドリフト発見を記録している**
6. (番外)`stakeholders[]`/`additional_stakeholders` — actor_refすら持たない、完全にオーファン化した表示専用リスト

**Actor Intelligenceの実効的役割**: プロンプト内では8ブロック中6番目という地味な位置づけ(サイズ予算制御も無い)だが、`key_stakeholder_assessment`の「最優先材料」と明記され、`primary_key_point`/`candidate_key_points`/`focus_selection`/`main_scenario`(confidence上限付き)へ具体的に接続する指示が複数存在する。provenance(由来)はLLMの自己申告ではなく`attach_actor_intelligence_provenance`が事後決定的に付与し、`general_knowledge_used`さえもcoverage=noneの場合はLLMの自己申告を強制上書きする。**結論: BACKGROUND CONTEXTとBIASING PRIORの混在**——Primary Reasoning Driverではないが、Optional Enrichmentと呼ぶには強く配線されすぎている。

## H. Relationship model map — fact / preference / perception

| メカニズム | 分類 | 根拠 |
|---|---|---|
|`geo_relationships`(dependency_direction/leverage_direction/RELATIONSHIP_DOMAINS)|**RELATIONSHIP FACT**|客観的なdomain×dependency×leverageの多次元表現、DB永続、observer視点フィールドなし|
|`key_actor_profiles[].strategic_relationship_preference`|**A'S DESIRED RELATIONSHIP**(のはずだが構造的欠陥あり)|自由記述の単一文字列で`target_actor_ref`を持たない。複数の相手が存在する局面では、どの相手への志向かを区別できず、意図せず「Aの一般的な姿勢」と「Aの特定の相手Bへの姿勢」を混同する設計上の穴|
|`directional_perceptions[]`|**A'S PERCEPTION OF B**|`observer_actor_ref`/`target_actor_ref`必須、`claim_type`(stated/inferred_from_behavior/inferred_from_source/analyst_hypothesis)で確度を明示、grounding Guard・自己言及禁止Guードあり|

**コード自身が認識している2つの混同リスク**:
- `_directional_perception_not_self_referential_check`(`geo_expert_quality_guard.py:403-415`)は、LLMが「Bについての認識」枠に「Aの自己認識(本来core_profile側の内容)」を書いてしまう事例に対処するために存在する——つまり実際にこの混同が起きることをコードが前提にしている。
- `_DIRECTIONAL_PERCEPTION_NOTE`(`geo_expert_schema.py:747-751`)は「`relevant_relationships`(客観的関係)自体はperceptionではない、そこから推測する場合は`claim_type=analyst_hypothesis`とせよ」と明記——だが、この区別を検証するGuardはコード上どこにも実装されていない(プロンプトの警告文のみで、機械的な担保なし)。

## I. Human Strategic Interaction coverage

対象の因果連鎖「A wants B to do X → A believes B values Y → A acts Z (expecting to change B) → B interprets Z → B responds based on perception of A」をリンクごとに評価:

| リンク | 分類 | 根拠 |
|---|---|---|
|A's goal toward B|REPRESENTABLE BUT OPTIONAL|`ultimate_objective`/`strategic_relationship_preference`は自由記述、target actor参照なし|
|A's belief about B|**EXPLICIT FIRST-CLASS MECHANISM**|`directional_perceptions`(observer/target actor_ref必須、grounding Guardあり)——ただしdeepプロファイル限定、内容は汎用prose|
|A acts Z expecting effect on B|REPRESENTABLE BUT OPTIONAL|`event_role`にtarget参照なし。`actor_expected_response`はA自身の`actor_ref`のみで結ばれ、`target_actor_ref`が存在しない|
|B interprets Z|**IMPLICIT LLM REASONING ONLY**|専用フィールドなし、event/action横断参照キーが皆無|
|B responds based on perception of A|**NOT PRESENT**|`directional_perceptions`と`actor_expected_response`/`analyst_expected_response`を結びつけるコードはゼロ(engine.py・quality_guard.py双方でgrep 0件)|

**結論: 連鎖は1つの仕組みとして実在しない。** 両端(Aの信念=directional_perceptions、event_roleの粗い分類)は個別にはよく設計されているが、繋ぎ目(action→expected effect→interpretation→response)は共有キーを持たず、`_CANDIDATE_GENERATION_OPERATORS_NOTE`という1つのプロンプト文(「Actor→Goal→Perceived Path→Event Role→Expected Response→Desired Stateというつながった読みとして記述すること」)が、`hypothesis_register[].reason`という自由記述フィールドへ物語として書くことをLLMに期待しているだけで、構造的な強制もGuardも存在しない。

**Actor Expected vs Analyst Expected Response**: 両フィールドともコード上のどこからも読まれず(`geo_expert_engine.py`/`geo_expert_quality_guard.py`とも参照ゼロ)、差分計算・hypothesis_register連動・scenario連動のいずれも存在しない。「誤解のモデル」として機能させる設計意図はプロンプト文には明記されているが、実装は存在しない。

**Event Positioning(event_role)**: DESCRIPTIVE CLASSIFICATION。role_typeは固定enumではなく自由記述の推奨語彙(goal/means/bargaining_card/signal/constraint等)で、値ごとの追加質問(「誰に対する交渉材料か」等)を要求する分岐指示は皆無。コード参照ゼロ。

**Preference-Action Gap**: descriptive summary + draft_reviewモード限定のquality-gate的anomaly detectorに留まり、hypothesis生成・actor_model_challenge・scenario生成のいずれにも接続されていない。

## J. Hypothesis architecture

| メカニズム | 何の候補か | 生成段階 | 比較 | 順位付け | 棄却 | Main Assessmentを変えられるか |
|---|---|---|---|---|---|---|
|`candidate_key_points`|WHAT MATTERS|Stage1常時|Yes(2-4件)|Yes(primary選定)|Yes(rejected_candidates)|同一LLM呼出内の副産物としてのみ、フィードバック機構は無し|
|`focus_selection`|attention配分(新しい説明ではない)|Stage1常時|No(単一オブジェクト)|No|No|コード上の消費者ゼロ|
|`hypothesis_register`(H0-H4)|WHAT EVENT MEANS/WHY ACTOR ACTS 混在|**Stage2限定**(deep profileのみ)|Yes|status(retained/rejected/unresolved)のみ|Yes、だがGuardは非repair対象|**構造的に不可能**——main_scenarioはこれが存在する前(Stage1)に確定済み|
|`strongest_counterargument`|WHAT EVENT MEANS への挑戦|Stage1常時|No(単一文字列)|No|No|同一呼出内の副産物のみ|
|Frame-breaking候補(H4)|WHAT EVENT MEANS|candidate_key_points内(Stage1)とhypothesis_register内(Stage2)に**二重に存在**|Yes(両方)|Yes(両方)|Yes(両方)|前者のみ理論上可能(同一呼出)、後者は不可能|
|Actor Model Challenge|WHY ACTOR ACTS|Stage1常時(per-actor任意)|No|No|outcome enumのみ|**No**——outcome値自体をどのコードも読まない|
|main/alternative scenarios|WHAT HAPPENS NEXT|Stage1(main、常時)+Stage2(deep_scenarioのalternative、上書き)|switch_conditions経由でYes|main が勝者|暗黙的|main_scenarioは不変。alternative_scenariosのみ上書き可(last-write-wins、仮説競合ではない)|
|media hypotheses|WHY ACTOR ACTS/WHAT EVENT MEANS混在|Stage2限定(media_context module)|Yes|No|Yes(status)|No、primary_key_pointとの構造的リンクIDが無いと設計者が自認|

**総括**: フィードバックループはコード上どこにも存在しない。全フィールドはStage1(1回のLLM呼出)またはStage2(Stage1確定後の1回の追加呼出)で単発的に書かれるのみ。唯一の例外(`alternative_scenarios`の上書き)も仮説競合の結果ではなく単なる`dict.update()`の副作用。

**Candidate Generation Operators(10種)の実効果**: 各Operatorはプロンプト上わずか3〜10文字の括弧書きのみで、具体例を伴う定義文書(`03_candidate_operators.json`)は**コードベースに実在しない**(死んだ参照)。「非自明な動機仮説を体系的に生成するか」への回答は**Partial**——Goal/Means(目的↔手段反転)とCounterparty Shift(対象↔本当の相手反転)の2つのみ具体的な指示を持ち、「国内政治的立場」「圧力→交渉設定」的パターンは別のノート(Focus Selectionのbehavior_driving_force)に部分的に存在するのみで、Operatorノート自体には無い。

## K. Scenario dependency map

**main_scenarioはStage1で、Human Interaction系フィールド(directional_perceptions/situation_actor_assessment/hypothesis_register)が1つも存在しない段階で確定する。** Stage2プロンプトは`stage1_data`を「確定済み・重複記述禁止」として明示的に渡すため、Stage2はmain_scenarioを一切書き換えられない。Repair(Step20)ですら再生成材料は元のuser_prompt+元のstage1_dataのみで、stage2_dataを含まないため、Repair経路でもStage2内容はmain_scenarioに反映されない。

**分類: SCENARIO BEFORE HUMAN INTERACTION ANALYSIS。** プロンプト上の「Main ScenarioはH0-H4のうちretainedの仮説から生成せよ」という指示(`_COMPETING_HYPOTHESES_NOTE`)は、hypothesis_registerがStage2限定フィールドである以上、Stage1単独では文字通りには実行不可能な指示になっている。

## L. Research / model update loop

**Research loop**: `assess_research_sufficiency`(決定的)→(不十分なら)`run_research_cycle`(LLM/web検索)→`source_pack`再構築、という一連はStage1呼び出しより**前**に完結する単発の証拠補充であり、Stage1が一度確定した結論を新証拠で見直す再帰的ループは存在しない。**分類: RESEARCH FILLS EVIDENCE**(RESEARCH CHANGES ANALYTICAL MODELではない)。永続Actor Intelligence(DB)はこのResearch loopから一切更新されない(別のDB経路)。Repairも研究を再実行しない。

**Model-update機構(3系統)の相互関係**:
| 機構 | 更新対象 | トリガー | 旧モデル保持 | 矛盾の明示 | 他機構との整合検証 |
|---|---|---|---|---|---|
|Pattern Applicability Assessment|Case内判断のみ(DB書き込み無し)|`relevant_decision_patterns`存在時|N/A|`applicability`列挙値のみ|**無し**|
|Actor Model Challenge|Case内判断のみ|LLM自己申告、"substantial" profile限定|N/A|`outcome`列挙値のみ(誰も読まない)|**無し**|
|Persistent Preference Update|**永続DB**(`geo_knowledge_items`)|`persistent_preference_change=="revised"`+新規evidence fingerprint|Yes(`supersedes_id`でバージョニング、人間承認まで非Active)|`supporting_evidence`/`contradicting_evidence`を保持するが「既存モデルと矛盾」という明示フラグは無し|**無し**|

**結論: 3機構間の矛盾を検出・調停するコードはゼロ件確認。** 同一Case・同一Actorについて、Pattern Applicability=lowかつPersistent Preference Change=reinforcedのような矛盾した結論が同時に成立しても、どのコードもそれを検出しない。

## M. Generated vs Operationalized matrix

| フィールド | GENERATED | DISPLAYED | PERSISTED | USED DOWNSTREAM | CAN CHANGE CONCLUSION |
|---|---|---|---|---|---|
|`initial_anomaly`|Yes(常時)|Yes|Yes(response_json)|**No**(Guardゼロ、engine参照ゼロ)|No|
|`focus_selection`|Yes(常時、内容は任意)|Yes|Yes|**No**(3 Guardは自己形状チェックのみ)|No|
|`preference_action_gap`|Yes(常時)|Yes|Yes|Partial(draft_reviewモードのquality-gateのみ)|No(仮説生成には未接続)|
|`directional_perceptions`|Yes(deep限定)|Yes|Yes|Partial(grounding Guardのみ、自己整合性)|No(他フィールドとの接続皆無)|
|`situation_actor_assessment`|Yes(deep限定)|Yes|Yes|Partial(自己整合性Guard多数)|No(Primary Key Point/Main Scenarioへ波及不可)|
|`hypothesis_register`|Yes(deep限定)|Yes|Yes|Partial(1 Guard、非repair)|**No**(main_scenarioより後に生成されるため構造的に不可能)|
|`actor_model_challenge`|Yes(常時、任意)|Yes|Yes|Partial(presence-checkのみ、outcome値は誰も読まない)|No|
|`disconfirming_signal`|Yes(必須)|Yes|Yes|**No**(Guardゼロ)|No|
|`monitoring_indicators`|Yes(常時)|Yes|Yes|未評価(本監査対象外)|未評価|

**総括**: 上記9項目のうち、実際に他の結論を変え得るものは**ゼロ**。すべて「生成されて表示・保存はされるが、他の推論を動かす消費者を持たない」という意味で装飾的("advanced reasoning that only decorates the output")である。

## N. Top structural concerns(影響度順)

| # | 観察 | 分類 | Severity | 根拠 |
|---|---|---|---|---|
|1|Human Strategic Interaction連鎖(A believes→A acts→B interprets→B responds)が構造的に接続されていない|RESPONSIBILITY FRAGMENTATION + GENERATED-BUT-NOT-USED|**HIGH**|Section I。今回の一連の追加(perceived_path/event_role/directional_perceptions/actor_expected_response)が目指した中核機能そのものが未接続|
|2|main_scenarioがHuman Interaction分析より前に確定し、事後の再計算経路が皆無|ORDERING INVERSION|**HIGH**|Section K。Stage2がどれだけ深い人間関係推論をしても、Main Scenarioに反映される正規の経路が無い|
|3|Key Actor選定に単一の正解ソースが無く、最低5つの未検証な並存リストがある|MULTIPLE SOURCES OF TRUTH|**HIGH**|Section G。`_key_point_controllers_covered_check`は実案件でのドリフトを既に検出済み|
|4|control_type/controlling_entity_ids/decisive_power_and_leverage/key_stakeholder_assessment.rationaleが「誰が結果を制御するか」に対して4つの独立回答を持ち、相互検証皆無|RESPONSIBILITY OVERLAP|**MEDIUM**|Section F|
|5|actor_expected_response/analyst_expected_responseの差分が計算も利用もされていない(実装が意図と乖離)|GENERATED-BUT-NOT-USED|**MEDIUM**|Section I。「誤解のモデル」という設計意図がプロンプトのみに存在し実装が伴わない|
|6|initial_anomaly/disconfirming_signalが必須フィールドにも関わらずGuardゼロ、内容検証皆無|GENERATED-BUT-NOT-USED|**MEDIUM**|Section M|
|7|Cue/Anomaly系5概念(initial_anomaly/focus_selection.trigger_types/disconfirming_signal/media anomaly/actor_model_challenge)が全てANNOTATIVE ONLY|GENERATED-BUT-NOT-USED|**MEDIUM**|Section F|
|8|Model-update3機構(Pattern Applicability/Actor Model Challenge/Persistent Preference)間の矛盾検出・調停が皆無|DUPLICATE REPRESENTATION|**MEDIUM**|Section L|
|9|Candidate Generation Operatorsの参照文書(`03_candidate_operators.json`)がコードベースに実在しない(死んだ参照)|DEAD LOGIC|**LOW**|Section J。実害は「具体例が無いので抽象的な3-10文字の指示にモデルの背景知識で対応させている」程度|
|10|レガシー実行方式`run_expert_request`は決定的confidence補正パイプライン(11関数)を一切実行しない、と別監査(Phase A)で確認済み|EXECUTION PATH ASYMMETRY|**LOW-MEDIUM**|既定`execution_mode="staged"`のため通常は到達しないが、フォールバック経路として残存|

## O. Five factual questions the To-Be design must resolve

1. Human Strategic Interaction連鎖(A's belief→A's action→B's interpretation→B's response)を、自由記述の物語ではなく、共有キー(event/action ID、target_actor_ref)で構造的に接続する設計は必要か、それとも現状の「個別フィールド+prose連結」で十分と判断するか?
2. main_scenarioを、Stage2で生成されるHuman Interaction分析(hypothesis_register/directional_perceptions/situation_actor_assessment)の後に確定させる(順序転換)、または現状の順序を維持しつつ別の反映経路を設けるべきか?
3. Key Actor選定の5つの並存リストのうち、どれを唯一の正典(source of truth)とし、残りをどう従属させる(あるいは廃止する)か?
4. 「誰が/何が結果を制御するか」という問いに答える4つのフィールド(control_type/controlling_entity_ids/decisive_power_and_leverage/key_stakeholder_assessment)を統合するか、それとも役割を明確に再分担するか?
5. Cue/Anomaly系の5概念とModel-update系の3機構を、それぞれ単一の所有者を持つ1つの仕組みに統合すべきか、あるいは意図的に多層のまま、相互矛盾だけを検出する調停層を追加するのが適切か?

## P. One-page As-Is reasoning diagram

```
[Actor Intelligence Pack取得]  ═══PRELOADED BEFORE FOCUS═══╗
 (issue紐付け+1-hop、決定的、Stage1変数が存在する前)          ║
                                                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    STAGE 1 (1回のLLM呼出)                         │
│  ══════════ GENERATED IN SAME LLM CALL ══════════                │
│                                                                    │
│  initial_anomaly ┄┄(NOT USED DOWNSTREAM)┄┄▶ (何も変えない)         │
│                                                                    │
│  candidate_key_points                                              │
│        │ TRUE SEQUENTIAL DEPENDENCY(プロンプト指示、構造強制なし)   │
│        ▼                                                          │
│  primary_key_point ──▶ focus_selection ┄┄(NOT USED DOWNSTREAM)┄┄▶ │
│        │                    (他のどのコードにも読まれない)          │
│        │ TRUE SEQUENTIAL DEPENDENCY                                │
│        ▼                                                          │
│  key_stakeholder_assessment ──▶ key_actor_profiles                │
│        (perceived_path/event_role/actor_expected_response含む)     │
│        │ TRUE SEQUENTIAL DEPENDENCY(H0-H4連鎖、prose頼み)          │
│        ▼                                                          │
│  main_scenario ★★★ ここで確定、以降不変 ★★★                       │
│        │                                                           │
│        ▼                                                          │
│  strongest_counterargument ──▶ additional_research                │
│                                                                     │
│  decisive_power_and_leverage ┄┄(専用ノート無し、mutually inferred)  │
└─────────────────────────────────────────────────────────────────┘
                          │
                          │  "Stage1で確定した内容(前提として維持)"
                          ▼  という明示付きでStage1 JSON全体を注入
┌─────────────────────────────────────────────────────────────────┐
│         STAGE 2 (条件付き、最大1回のLLM呼出)                       │
│  ══════════ POST-HOC ONLY ══════════                              │
│                                                                     │
│  situation_actor_assessment ┄┄┄┄┐                                  │
│  hypothesis_register(H0-H4)  ┄┄┄┼┄┄ main_scenarioへ書き戻し不可     │
│  directional_perceptions   ┄┄┄┄┤   (どのコードも許可していない)     │
│  media_source_context      ┄┄┄┄┘                                  │
│                                                                     │
│  alternative_scenarios ══▶ (唯一の例外: Stage1簡易版を丸ごと上書き) │
│                              main_scenario自体は不変                │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
        決定的Guard(confidence補正11関数、自己整合性中心)
                          │
                          ▼
        Repair(条件付き、MODE_V12_CHECK_NAMESのみがトリガー
              ＝focus_selection/hypothesis_register/actor_model_challenge
                の失敗はRepairを起動しない)
                          │
                          ▼
                    Persistence / Response
```

**凡例が示す核心**: Actor Intelligenceは「PRELOADED BEFORE FOCUS」という矢印で入り、Stage1は「GENERATED IN SAME LLM CALL」という1つの箱の中で(プロンプト上は順序があるが構造的強制の無い)"TRUE SEQUENTIAL DEPENDENCY"を辿り、`main_scenario`という不動点に到達する。Stage2はその不動点の外側で"POST-HOC ONLY"の深掘りを行うが、唯一`alternative_scenarios`だけがその不動点の**外側にある別フィールド**を上書きできる。`initial_anomaly`/`focus_selection`/`disconfirming_signal`/`actor_model_challenge`はいずれも"NOT USED DOWNSTREAM"のラベルが付く——生成されるが、誰にも読まれない。

---

## 付録: 調査担当・元資料

本文書は以下7件の読み取り専用調査(2026-09-07実施、コード変更なし)を統合したもの。個別の生資料(file:line単位のフル引用)はエージェント実行ログに保持されているが、本ドキュメントには要点のみ転記した。

1. Runtime order & Stage1 internal dependencies(Section B, C, D)
2. Preloaded Actor Intelligence effect on Stage1(Section G後半)
3. Actor ownership & Relationship fact/preference/perception(Section G, H)
4. Human Strategic Interaction & Event Positioning & Preference-Action Gap(Section I) — 最重要
5. Hypothesis architecture & Candidate Generation Operators(Section J)
6. Key Point/Focus/Control hierarchy & Cue/Anomaly operational status(Section F)
7. Stage2 revision power & Scenario ordering & Research/Model-update loop(Section E, K, L)
