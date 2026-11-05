# Geopolitical Expert AI — Phase D Implementation & Validation Plan

前提: `as_is_reasoning_audit.md`(As-Is監査) + `phase_b_c_canonical_design.md`(Phase B/C設計、確定済み、絶対条件4件・責務分離の原則を含む)。本書はPhase D(Minimal Coherent Refactor)の実行計画。実装はここに定義するサブフェーズ順に、各サブフェーズごとにoffline test(pytest)で止血しながら進める。**実データLLM呼び出し・DB書き込みを伴う検証は、コード変更が全てoffline testを通過してから、都度ユーザーに確認の上で実行する。**

## 2026-09-07 レビュー反映(D-0.5)

ユーザーレビューにより以下3点を修正し、着手順序も変更した:

1. **Perception Gapは意味比較にしない**: `actor_expected_response`/`analyst_expected_response`は自然言語であり、deterministicな文字列比較は「同じ意味だが表現が違う」ケースを誤って"gap"と判定しかねない。→ **決定的処理は接続性(鎖が繋がっているか)の検証のみに限定**し、認識の意味的なズレの評価はCall1のHypothesis/Human Interaction推論(`hypothesis_register.reason`)へ委ねる。新規フィールドは追加せず、既存のGuard `checks`パターン(pass/fail)で報告する。
2. **Control Point↔Key Actor Guardは単純ID完全一致にしない**: `controlling_entity_ids`と`key_actor_profiles[].actor_ref`は異なるID空間になり得る(既存監査で確認済み)。→ 両者とも実際は`geo_entities`の`entity_id`という共通の正典空間を経由しているため、比較は`entity_id`同士で行う(prose text-matchingだった現行の`_key_point_controllers_covered_check`を置き換える)。かつGuardの意味を「controlling entityは必ずKey Actorでなければならない」ではなく、「`control_type`が人間の裁量を含意する場合(actor_controlled/distributed/mixed)にのみ、対応するDecision Makerが存在すべき」という条件付き検証へ変更する。`control_type`がstructural/institutionally_constrained/unknownの場合はこのGuardは発火しない(Test Dの構造優位ケースと整合)。
3. **`decisive_power_and_leverage`は正典データの表示用projectionに留める**: `control_type`+`controlling_entity_ids`だけから新しい分析文をdeterministicに生成すると、"なぜ実効的に制約できるか"という情報が失われる。→ 正典データは`control_type`/`controlling_entity_ids`/既存の`control_structure_summary`・`key_stakeholder_assessment.rationale`(引き続きLLM authored、変更なし)に一本化し、`decisive_power_and_leverage`は**これらを機械的に整形するだけの後方互換表示フィールド**(deprecated)として位置づける。新しい分析内容は一切生成しない。

加えて、**絶対条件4**(Scenarioは選択されたHypothesisの結果として生成される)を追加し、`main_scenario`に`derived_from_hypothesis_id`(nullable、`hypothesis_register[].hypothesis_id`参照)という最小限の構造的リンクを1つ追加する。Hypothesis taxonomyの新enum値は**旧enum値を置き換えず併存**させ、過去DBレスポンスの意味を書き換えない。D-5にはPreloaded Actor Intelligenceが"candidate prior"であり結論ではない、という明示的な指示を追加する。

着手順序も変更: D-1(共有key+旧taxonomy互換) → D-2(Prompt) → **D-4a**(接続性Guardのみ、意味比較なし) → D-5(Actor candidate prior改善) → D-3(call境界変更) → **D-4b**(新call構造に依存するGuard/Repair配線) → Offline full regression → 4-case Before/After。D-3より前に、D-3後にしか意味を持たないGuardを作らないための順序変更。

---

## サブフェーズ構成

### D-1. Schema変更(最小差分・後方互換)

対象: `schemas/geo_expert_ai_response.schema.json`

1. `key_actor_profiles[].event_role`へ`intended_target_actor_ref`(nullable string)を追加。
2. `situation_actor_assessment[].actor_expected_response`/`.analyst_expected_response`をオブジェクト化するか、隣接する`target_actor_ref`(nullable string、両フィールドで共有)を追加。**既存が`["string","null"]`の単純型のため、objectへ変更するかsiblingとして追加するかは実装時に既存テストへの影響が少ない方を選ぶ**(推奨: sibling追加、型変更を避ける)。
3. `main_scenario`へ`derived_from_hypothesis_id`(nullable string、`hypothesis_register[].hypothesis_id`参照)を追加(絶対条件4対応)。`alternative_scenarios[]`にも同名の参照フィールドを追加し、どのHypothesisに対応するAlternativeかを構造的に示す。
4. `hypothesis_register[].hypothesis_type`のenumへ新taxonomy値を**追加**(既存値は削除・リネームしない): `H0_straightforward`/`H1_instrumental_indirect`/`H2_counterparty_audience_shift`/`H3_constraint_domestic_driver`/`H4_frame_breaking`を追加し、旧値`surface`/`actor_intent_alternative`/`structural_alternative`/`actor_model_breaking`/`frame_breaking`は**enumに残したままdescriptionへ"legacy(過去レスポンス互換用、新規生成では使用しない)"と明記**。プロンプト側(D-2)でLLMには新taxonomy値のみを使うよう指示することで実質的な移行を行い、schema・DBレベルでは過去データの意味を書き換えない。
5. `strongest_counterargument`(トップレベル必須)を`hypothesis_register`側へ機能吸収するため、schema上は`required`から外し`deprecated`とコメント(削除はしない、確定#2のdeprecated方針)。
6. `actor_model_challenge`・`pattern_applicability_assessment`も同様に`deprecated`コメント化(スキーマからは削除しない)。
7. `decisive_power_and_leverage`は正典データ(`control_type`/`controlling_entity_ids`/`control_structure_summary`/`key_stakeholder_assessment.rationale`、いずれも変更なしでLLM authoredのまま維持)からの**表示用projectionに限定**するため、Stage1 schemaの`required`からは外し、descriptionへ「deprecated: 後方互換の要約表示のみ、新しい分析はここへ書かない」と明記(D-3で表示専用の決定的整形処理に置き換え、新規分析文は生成しない)。

Guard: 新設の`intended_target_actor_ref`/`target_actor_ref`のgrounding check(`stage1_visible_entity_ids`または`key_actor_profiles[].actor_ref`集合に含まれるか)を追加。既存の`_directional_perceptions_grounded_check`と同型のパターンを流用。`derived_from_hypothesis_id`のgrounding check(`hypothesis_register[].hypothesis_id`集合に含まれるか)も同様に追加。

**成果物の検証**: 既存テストのfixture更新(新規nullable fieldの`None`追加)、新規grounding checkのoffline test。LLM呼び出し・DB書き込みなし。

### D-2. Prompt文言変更

対象: `geo_expert_schema.py`

1. `_KEY_POINT_NOTE`からH0-H4カバレッジ要求を削除し、`candidate_key_points`は純粋に"What matters"候補のみを問う文言へ縮小。
2. `_COMPETING_HYPOTHESES_NOTE`をH0/H1/H2(+H3/H4)taxonomyへ書き換え、各typeのトリガー条件を明記(H1↔`preference_action_gap`、H2↔`event_role.intended_target_actor_ref`、H4↔既存Actor Model Challenge相当の検討)。**新taxonomy値のみを使うよう明記**(旧enum値=surface等は生成時に使用しないこと、と明示)。5評価観点(Evidence/Actor Model/Relationship/Means-Timing/Counterparty Response Fit)を`reason`フィールドで参照するよう指示文を追加。**加えて、`actor_expected_response`と`analyst_expected_response`の意味的な差(認識のズレ)をこの`reason`内で評価対象とするよう明記**(責務分離の原則: 意味解釈はここでLLMが行い、Guardは接続性のみを見る)。
3. `_PERCEIVED_PATH_EVENT_ROLE_NOTE`へ`intended_target_actor_ref`の記入指示を追加。
4. `_ACTOR_ANALYST_RESPONSE_SEPARATION_NOTE`へ`target_actor_ref`記入指示を追加。
5. `_CANDIDATE_GENERATION_OPERATORS_NOTE`を10operatorsから、H0/H1/H2/H3/H4生成に直結する少数のOperatorへ再構成し、各々に具体例を1文added(死んだ`03_candidate_operators.json`参照コメントは削除)。
6. `_FOCUS_SELECTION_NOTE`のtrigger_types群を「Activated Lens」という呼称へ整理(schemaのenum値自体は変更しない、説明文言のみ調整——確定#2の"呼称変更のみ、構造変更なし"方針に合わせ、無理に別スキーマへ分離しない)。
7. `disconfirming_signal`/`retraction_conditions`/`switch_conditions`/`focus_selection.discriminating_observation`の説明文を統一Discriminator概念として相互参照させる(どのレベル=仮説 or Scenarioで使うかを明記)。
8. **(D-3実施後に効く文言、ここで先行して書いておく)** Call B(Scenario/Validation)のprompt冒頭に、絶対条件4に対応する因果順序の明示指示を追加: 「(1)retainedなhypothesisを特定する→(2)その仮説が依存するactor interaction(directional_perceptions/event_role/actor_expected_response等)を特定する→(3)そのinteractionからmain_scenarioを導出し`derived_from_hypothesis_id`へ記録する→(4)競合するhypothesisであれば何が変わるかをalternative_scenariosへ反映する」という順序をこの通り踏むこと、単にhypothesis_registerを踏まえたと書くだけでは不十分であること、を明記。
9. Actor Intelligence Pack冒頭(`_ACTOR_INTELLIGENCE_NOTE`)へ「Preloaded Actor Intelligenceはcandidate priorであり、Key Actorであることの確認・結論ではない。Actor Intelligenceが豊富な主体を自動的にdecisive actorとみなさないこと。Control Point/Decision-Maker Decompositionの判断に基づき、Packに無い主体を選ぶことも妨げられない」という明示的な指示を追加(D-5のスコアリング改善と対になる、バイアス抑制策)。

**成果物の検証**: プロンプト文言変更のみでschema構造は変えないため、既存のschema関連テストは無影響のはず。offline testで確認。

### D-3. Engine call境界の再構成(最大リスク、絶対条件2・3の核心)

対象: `geo_expert_engine.py`, `geo_expert_schema.py`(`build_stage1_schema`/`build_stage2_schema`/`resolve_stage2_modules`)

**現状**: Call1(Stage1)が`main_scenario`含む約20フィールドを常時生成 → Call2(Stage2、条件付き)が`hypothesis_register`等を後付け生成、Call1の結果には手を出せない。

**変更後**:
- `hypothesis_register`/`situation_actor_assessment`/`directional_perceptions`/`investigation_questions`が発火するprofile/mode組み合わせ(現行`_PROFILE_ADDITIONAL_MODULES`のdeep×theme_design/case_analysis等)では、**Call1のschemaから`main_scenario`/`alternative_scenarios`/`strongest_counterargument`を除外**し、これらのモジュールをCall1側へ引き上げて同時生成する。
- 上記の場合、**Call2のschemaを新設し、`main_scenario`/`alternative_scenarios`(統合済み、簡易版と詳細版の二重生成を廃止)/`additional_research`最終化を担当**させる。Call2のprompt頭に、現行Stage1→Stage2パターンと同じ「Call1で確定した内容(hypothesis_register含む)を前提として維持」という明示文を付ける。
- 上記が発火しないprofile/mode(standard/quick等、hypothesis_register非対象)では、**現行通りmain_scenarioはCall1に残す**(呼び出し回数を増やさないため)。
- `audience_signal`/`media_context`モジュールは引き続きCall2側の条件付きモジュールとして残す(Human-Actor軸と直交するため無理に統合しない)。
- `decisive_power_and_leverage`をLLM出力から、merge処理内の**表示用projection関数**へ変更する。新しい分析内容は生成せず、既存の`control_type`/`controlling_entity_ids`/`control_structure_summary`/`key_stakeholder_assessment.rationale`(いずれもLLM authoredのまま変更しない)を機械的に整形して埋めるだけの後方互換フィールドとする。
- `main_scenario`生成(Call B)は、`derived_from_hypothesis_id`を必ず埋めるよう要求し(D-2の因果順序指示と対)、この値が`hypothesis_register[].hypothesis_id`のretainedな要素と一致することをD-4bのGuardで検証する。
- Repair対象(`MODE_V12_CHECK_NAMES`)へ、D-1で追加したgrounding check、D-4aの接続性Guard、D-4bのControl Point↔Key Actor整合チェック・`derived_from_hypothesis_id`整合チェックを追加。

**具体的な分岐点**: `run_expert_request_staged`内、Stage1 schema構築(現`:1526`相当)の直前に「この呼び出しでhypothesis-bearingなStage2モジュールが発火するか」を`resolve_stage2_modules`の結果から事前判定し、`build_stage1_schema(include_scenario=not_will_fire)`のようなフラグを渡す形にする。

**この段が最大の変更なので、D-3のみ着手前に一度differential diffをレビューしてから進める。**

### D-4a. Guard変更(接続性のみ、D-3より前に実施)

対象: `geo_expert_quality_guard.py`

1. D-1のgrounding check追加(前述、`intended_target_actor_ref`/`target_actor_ref`/`derived_from_hypothesis_id`)。
2. **Interaction Link連続性チェック**(意味比較ではなく接続性のみ)を新設: `key_actor_profiles[]`ペアについて、`directional_perception_present`/`event_role_present`/`actor_expected_response_present`/`analyst_expected_response_present`の4フラグを機械的に集計し、`interaction_link_status: complete/incomplete`として`checks`(既存のpass/fail報告パターン)へ記録する。**"認識がどうズレているか"の意味評価は一切行わない**——それは`hypothesis_register.reason`内でLLMが行う(D-2の指示による)。新規schemaフィールドは追加せず、既存の`quality_evaluation.checks`形式で報告するのみ。
3. `_hypothesis_pruning_present`・D-1のgrounding checkを`MODE_V12_CHECK_NAMES`へ追加(絶対条件1・2を壊す出力を実際にRepairでブロックできるようにする——現状これらは非repair対象で放置されている)。
4. `pattern_applicability_assessment`関連(`ensure_pattern_applicability_assessment`, `_actor_pattern_applicability_present`)・`_actor_model_challenged`は、D-1で該当フィールドがdeprecated化されるのに合わせ、Guard自体もdeprecatedコメントを付けて無効化(削除はしない、確定#2方針)。

### D-4b. Guard変更(D-3=call境界変更に依存するもの、D-3の後に実施)

1. `_key_point_controllers_covered_check`(現行はtext-matching advisory)を置き換え、**Control Point↔Decision Maker整合チェック**として再実装する: `control_type`が`actor_controlled`/`distributed`/`mixed`(=人間の裁量を含意)の場合**にのみ**発火し、`controlling_entity_ids`の各値を`geo_entities.entity_id`という共通の正典空間で`key_actor_profiles[].actor_ref`(が指すentity_id)と比較する。`control_type`が`structural`/`institutionally_constrained`/`unknown`の場合は発火しない(Test Dの構造優位ケースで誤検出しないことを確認する)。意味は「controlling entityは必ずKey Actorでなければならない」ではなく「**人間の裁量を含意するControl Pointに対応するDecision MakerがActor reasoningに存在しない**」という検出に変更する。`MODE_V12_CHECK_NAMES`へ追加。
2. `main_scenario.derived_from_hypothesis_id`が`hypothesis_register[].hypothesis_id`のretainedな要素の中に存在することを検証するGuardを新設、`MODE_V12_CHECK_NAMES`へ追加(絶対条件4の構造的な最低ライン)。

### D-5. Actor Intelligence候補スコアリング調整

対象: `geo_actor_intelligence_service.py`

`score_actor_relevance`へ、Foundation Packの構造的Entity/Leverage Cardに現れる主体への加点ルールを追加(issue_linked/high_leverage_relationshipに次ぐ第3のスコア要素)。呼び出し構造・CallBudgetは変更しない。

---

## 検証計画

### オフラインテスト(各サブフェーズ完了ごとに実行、LLM呼び出しなし)

- D-1: 新規grounding checkのunit test、既存fixtureの更新確認
- D-2: 既存schema関連testの無影響確認(prompt文言のみの変更のため)
- D-3: `run_expert_request_staged`の呼び出し回数・CallBudget消費が変更前後で変わらないことを、モックLLM応答を使ったunit testで確認。既存の`persist=False`ゼロ書き込みテストの再実行。
- D-4: 新設Guard・Perception Gap比較関数のunit test、confidence補正パイプラインの実行順序が壊れていないことの確認(既存11関数+新設1関数の順序テスト)
- D-5: スコアリング調整のunit test

### 実データBefore/After比較(offline test全通過後、ユーザー確認の上で実施)

1. **手法**: 既存の3ケース(Trump-Xi半導体/Iran-Hormuz/Kim-DPRK、`docs/expert-ai-validation/eguchi_3case_event_based_off_on/`に既存Beforeデータあり)を再利用。Beforeは既存保存データをそのまま使い、Afterのみ新規実行(`persist=False`、External Research OFF、既存のEvaluation Input Gate 6条件を維持)。
2. **因果接続確認(絶対条件1)**: `directional_perceptions[].observer_actor_ref/target_actor_ref` → `event_role.intended_target_actor_ref` → `actor_expected_response.target_actor_ref` → `analyst_expected_response.target_actor_ref`が、同一のactor/target組み合わせで実際に一貫して埋まっているかを機械的に突き合わせる(単なる目視ではなく、生JSONをスクリプトで突合)。
3. **順序確認(絶対条件2・3・4)**: Afterの生レスポンスにおいて、`main_scenario.derived_from_hypothesis_id`が実際にretainedな`hypothesis_register`要素を指しているかを機械的に確認した上で、`retained hypothesis→参照するactor pair→想定interaction→main_scenarioの帰結`という4点が内容的に一貫しているかを、単なるキーワード一致ではなく人手+LLM評価チェックリストで確認する(絶対条件4の中核指標)。
4. **非回帰確認**: 既存pytestスイート全体を実行しゼロ regressionを確認。加えて過去に固定した安全策(捏造Evidence IDガード、persist=False、actor/analyst expected separation、directional A→B/B→A非対称)の既存テストが無変更で通過することを確認。
5. **Functional Test A-D(元の巨大要求で指定済み)**:
   - Test A(米中半導体): 3つの並立Primary Focusに分裂していないか、Congress cueが政治的インセンティブ調査を起動し「誰が」まで特定するか
   - Test B(Iran/Hormuz): 「イランは自滅するから動かない」という歴史的frameの単純反復ではなく、抑制前提の変化に気づくか
   - Test C(北朝鮮): 「体制存続」で停止せず、現在の漸進的行動を説明できるか
   - Test D(新規、structure-first対照ケース): 構造的・物理的制約が支配的な局面で、不要なDecision-Maker Decompositionを強制していないか(Human-first-but-not-always gateの動作確認)

### Stop/Report基準

- オフラインテストが全て通過し、かつ実データBefore/After比較で絶対条件1-3が実際に満たされていることを確認できた時点で、Phase Dを完了として最終報告書(元の巨大要求が指定した10セクション形式)を作成する。
- 途中でオフラインテストが継続的に赤字化する、または実データ比較で絶対条件を満たせないことが分かった場合は、そこで一旦停止しユーザーへ報告する。

---

## 着手順序(2026-09-07改訂)

D-1(共有key追加+旧taxonomy互換維持)→ D-2(Prompt文言、Call B因果順序指示・Actor Intelligence candidate prior指示を含む)→ **D-4a**(接続性Guardのみ、意味比較なし)→ D-5(Actor candidate prior改善)→ D-3(call境界変更、最大リスク、diffレビューを挟む)→ **D-4b**(D-3後の新call構造に依存するGuard/Repair配線)→ オフラインテスト総点検 → 実データBefore/After比較(ユーザー確認後)→ 最終報告書。

D-3より前にD-3後にしか意味を持たないGuard(Control Point↔Decision Maker整合、`derived_from_hypothesis_id`整合)を作らないよう、D-4を前後半に分離した。
