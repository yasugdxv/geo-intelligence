# Eguchi Expert AI Final v1.0 — 現行実装監査（AUDIT_AND_PLAN）

作成日: 2026-09-06
対象: `C:\Users\269811\Downloads\eguchi_expert_ai_final_v1_0\02_claude_code_implementation_prompt.md` の実行範囲Aのみ
実行内容: 静的読み取り監査。アプリコード・DB・接続設定は無変更。実LLM・実DB呼び出しは行っていない。

## A1. リポジトリ状態

- リポジトリ: `c:\Users\269811\python\projects\geopolitical_monitor_dashboard`（branch: `main`）
- 直近コミット: `e08567c Phase 5: add storage_path audit tool for geo_documents`
- 監査開始時点で以下の未コミット差分が存在した（本監査では一切触れていない。ユーザーの作業中ファイルの可能性があるため保持）:
  - 変更: `api_server.py`, `eco_frontend_stderr.log`
  - 未追跡: `api_server_phase7b.log`, `api_server_phase7c.log`
- 本監査はネットワーク・DB接続を行わず、6件の並行読み取り調査（サブエージェント）による静的コード監査のみで構成した。テストは実行していない（実行そのものが承認範囲外のため）。

## A2/A3. 意味的オブジェクト別の現状（実ファイル・関数・フィールド対応）

以下、`05_contract_mapping_reference.json`の12意味的オブジェクトごとに、現行実装で対応するファイル・関数・フィールドと一致度を記す。詳細な突き合わせは `semantic_mapping.json` を参照。

### SituationFrame（今回、本質的に何が動く局面か）
- 対応: `key_point_assessment`（`schemas/geo_expert_ai_response.schema.json:125-262`）— `primary_key_point`, `candidate_key_points[]`, `selection_rationale`, `rejected_candidates`, `hinge_structure`, `disconfirming_signal`。Stage1で生成（`geo_expert_engine.py:1501-1524`）。
- **既存で足りる**: `what_it_explains`/`what_it_does_not_explain` ≒ `explanatory_scope`/`unexplained_observations`。`rejected_candidates` ≒ `alternative_frame_refs`（ただし参照IDではなく埋め込みテキスト）。
- **構造的に不足**: `frame_id`による案件内の安定参照、`as_of`（基準時点）、`prior_frame_ref`（前回Frameとの明示連結）に相当するものは `geo_case_service.py` を含め **not found**。Case内でFrameがバージョン管理される仕組みは存在しない。

### HumanModel（欲求・認識・働きかけの信念）
- 対応: `ACTOR_PROFILE_SCHEMA.core_profile`（`geo_knowledge_item_schemas.py:449-460`）— `worldview / core_objectives / decision_preferences / red_lines / preferred_instruments / structural_constraints`。各要素は`evidence_ids/confidence/observed_at/last_reviewed_at/reviewed_by`を持つ（`_ACTOR_STATEMENT_ITEM`, 同290-303行）。
- **既存で足りる**: 欲求・選好(`core_objectives`)、反例(`decision_patterns[].counter_evidence_ids`)、適用条件(`issue_positions[].valid_from/valid_to`)。
- **構造的に不足**: 「相手（B）への認識」「働きかけの信念（XすればBがYする）」を構造的に持つ専用フィールドが **not found**。`worldview`は自由記述で対象entityへの構造参照を持たない。
- 良好な既存設計: `INSTITUTION_PROFILE_SCHEMA`（同514-553行）はPerson用`core_profile`系フィールドを一切持たず、個人発言を集団認識へ自動転記する処理も存在しない（安全側）。

### DirectionalPerception（AからみたBの帰属付き認識）
- **リポジトリ全体を検索したが、`belief_holder`/`perception_target`/`attributed_by`/`source_directness`に相当するフィールドは存在しない（not found）**。
- 最も近い実装は `geo_relationship_service.create_relationship`（14-35行、`from_entity_id`/`to_entity_id`+`dependency_direction`/`leverage_direction`）だが、これは客観的な依存関係の向きであり、「Aが持つBについての主観的認識」を表す構造ではない。
- 現状、A→BとB→Aの混同防止は `geo_expert_quality_guard._evidence_substitution_detected`（1312-1333行、一般化表現のヒューリスティック検知）というプロンプト依存の弱い後段ガードのみに頼っている。
- **この意味的オブジェクトは実質未着手**。README・02プロンプトの中核である「人間・方向付き関係モデル」の最大のギャップ。

### ActorFlow（目的＋達成経路＋現在地）
- 対応: `key_actor_profiles[]`（`schemas/geo_expert_ai_response.schema.json:285-424`）— `ultimate_objective → strategic_relationship_preference → issue_objective → observed_action` という目的階層と `preference_action_gap`, `primary_audience`/`secondary_audience`, `driving_forces[]`, `action_substance`, `red_line`。
- **既存で足りる**: 目的階層（design docの`goal_hypotheses`にほぼ対応）。`target_actor`/`intended_audience` ≒ `primary_audience`/`secondary_audience`。`desired_landing_state`は`situation_actor_assessment[].desired_outcome`として部分存在（ただしIssue Component単位でActor単位ではない）。
- **構造的に不足**: `event_role`（今回の出来事が達成経路のどこに入るか）、`perceived_path`（働きかけ→期待反応→得たい状態の因果連鎖）、`current_state`/`current_blockers`/`current_need`の専用フィールドは **not found**。
- 既存の良い設計: `persistent_preference`（恒久）と`issue_objective`（一時的経路）は既に意図的に分離されている（コード内コメントに明記）。

### InterpretationCandidate（主体・目的・経路・役割・反応・着地の統合説明）
- 対応: `hypothesis_register[]`（`schemas/geo_expert_ai_response.schema.json:789-809`）— `hypothesis_type`(surface/actor_intent_alternative/structural_alternative/actor_model_breaking/frame_breaking), `status`(retained/rejected/unresolved), `reason`, `supporting_evidence_ids`, `contradicting_evidence_ids`。Guard: `_hypothesis_pruning_present`（`geo_expert_quality_guard.py:1336-1354`）。
- 別に、DBレベルで`geo_case_hypotheses`（`geo_case_service.py:75-131`）という**名前は似ているが意味が異なる別物**が存在する（Case横断の永続仮説、`hypothesis_type`=main/alternative/rejected/superseded）。今後の設計・実装で両者の命名衝突に注意。
- **構造的に不足**: `hypothesis_register`は固定5類型ラベルの器であり、design docが求める「候補ごとの適用Operator・変更前提・参照モデル・成立条件」を持つ動的候補ではない。`candidate_relations`(exclusive/compatible/subfactor/unknown)、`operator_ids`（OP01〜14）は **not found**（Operator自体は design docが「毎回全型実行を強制しない」と明言しているため、これ自体は想定内）。

### EvidenceUse（証拠がどの判断・接続に使われたか）
- 対応: `evidence_relevance[]`（`schemas/geo_expert_ai_response.schema.json:216-227, 712-721`）— `evidence_id`, `claim_relevance`(direct/strongly_relevant/structural/peripheral), `source_type`（`geo_expert_engine._evidence_source_types`が`geo_source_registry`起点で決定的に上書き、217-261行）, `rationale`。
- **既存で足りる**: 出典属性（source_type）は決定的処理で信頼性が担保されている。
- **構造的に不足**: design doc 6.1-6.2が求める`usage`5分類（premise/feasibility/discrimination/model_update/editorial_context）そのものの軸が **not found**（`claim_relevance`は直接性の1軸のみ）。`discriminates_between`（候補間のどれを識別するか）、`content_state`、`temporal_fit`、`coverage`、`access_scope`も **not found**。
- 懸念: `cap_confidence_by_claim_relevance`（`geo_expert_quality_guard.py:1017`）がclaim_relevance→confidence降格という形で、本来別軸であるはずの「用途」と「信頼度」を部分的に混同する運用になっている（design docが明示的に禁じる混同）。

### FeasibilityAssessment（意思・権限・能力・実装・相手反応・効果）
- 対応: `action_substance`（definitions, `schemas/geo_expert_ai_response.schema.json:34-48`）— `stated_signal`/`concrete_measure`/`target_scope`/`legal_or_policy_force`/`implementation_status`/`enforcement_status`/`discretion`/`assessment`。`assets_dependencies_leverage[]`（`_derive_assets_dependencies_leverage`, `geo_expert_engine.py:1216-1226`）。
- **既存で足りる（粗い粒度）**: 意思・権限・実装は`action_substance`一個に畳み込まれ既に表現されている。
- **不足**: 「相手反応」「効果」の明示的な分離フィールドが無い（`assessment`という自由記述のみ）。`assets_dependencies_leverage`は`kind`+`summary`のみで実行可能性の接続評価はしていない。
- `institution_intelligence`関連（Institution Profile側の実装機能）はHumanModelと同様に良好（Person固有フィールドとは分離、`geo_institution_intelligence_service.py`）。
- `identified_knowledge_gaps`は「DBへ自動書き込みしない」設計だが、`geo_knowledge_gap_service.py`側の実際のGap管理（`geo_knowledge_gaps`テーブル）とは接続されていない（**disconnected**）。

### DominantFlow（他主体の選択肢・実行条件を最も左右する流れ）
- 対応: 2軸に分散して既存。①`key_stakeholder_assessment.determination`(single/multiple/distributed/unclear)。②`key_point_assessment.control_type`(actor_controlled/institutionally_constrained/structural/distributed/mixed/unknown、`normalize_control_type`で整合検証, `geo_expert_quality_guard.py:904`)。
- **既存で足りる**: design docの`single_actor/coalition/distributed/structural/mixed/unresolved`は実質この2軸の組み合わせでほぼ表現可能。既存Guardで矛盾検出も機能している。
- 注意: 単一の`dominant_flow`フィールドへ統合すると2つの既存Guardロジックの再設計が必要になり影響が大きい。接続（参照関係の明示）で足りる可能性が高い。

### ConditionalDevelopment（候補を仮定した限定的な行動→反応→帰結）
- 対応: `main_scenario`/`alternative_scenarios`（`schema.json:699-788`）— `confidence`, `supporting_evidence`, `evidence_relevance[]`, `switch_conditions`, `strengthening_conditions`/`weakening_conditions`, `observable_indicators`, `historical_analogies_used[]`, `evidence_class`。
- 「予測を観測にしない」原則は**複数の決定的Guard関数の合成で機能的に保証**されている: `evidence_class`→`cap_confidence_by_evidence_class`（`geo_expert_quality_guard.py:951`、historical/general_knowledgeのみの場合confidence降格）、`separate_historical_evidence_in_key_point`/`_in_actor_and_situation_assessment`（呼出し: `geo_expert_engine.py:1580-1584, 1663-1665`）、`apply_confidence_weighting_fix`。
- **不足**: design docが提案する共通`epistemic_status`タグ（observed/attributed_claim/expert_assessment/ai_hypothesis/conditional_prediction/unknown）自体は **not found**。「本人が期待する反応」と「分析者が予測する反応」を明示的に分けるフィールドも **not found**（`likely_reaction`が単一自由記述で両者を混在させ得る）。

### AnomalyAndUpdate（期待と観測の不一致・更新記録）
- 対応: `pattern_applicability_assessment[]`（`schema.json:391-407`）+ `actor_model_challenge`（408-417行）+ `preference_action_gap_summary`（528-531行）+ `geo_persistent_preference_service.propose_persistent_preference_candidates`（81-145行）。
- **既存で良好**: `anomaly=none`を許す設計、`update_reason`/`knowledge_update_candidate_ref`相当が既に実装され、**候補生成後は人間の`review_knowledge_item`呼び出しまでactive化されない**ことをコードで確認（自動`approve_active`呼び出しは無し）。
- 注意すべきリスク: `seed_actor_profile`/`seed_institution_profile`（`geo_actor_intelligence_service.py:166-239`等）は`activate=True`で`start_review`→`approve_active`を**同一関数内で連続実行**する（Seed/CLI運用限定だが、人間レビューをバイパスする経路として存在）。

### ResearchNeed（重要前提・実行条件・候補識別の不足）
- 対応: `investigation_questions[]`（`schema.json:502-527`）+ `geo_knowledge_gap_service.py`（`DETERMINISTIC_GAP_TYPES`/`ANALYTICAL_GAP_TYPES`、20種のドメイン別分類）+ `assess_research_sufficiency`（Direct Evidence件数としきい値`_SUFFICIENT_THRESHOLD=2`ベース）。
- **既存で足りる**: `stop_condition`（sufficient_comparison/no_new_evidence/evidence_unobtainable/budget_limit）が`attempt_state`/`stop_reason`に近い。
- **構造的に不足**: design docが最重視する「識別力(discrimination)の有無」による`acquired_not_discriminating`と`not_acquired`の区別が **どこにも実装されていない**。`assess_research_sufficiency`はDirect Evidence件数のみで判定し、「この観測はA/B候補を区別できるか」という判定軸自体が存在しない。`permitted_sources`/`access_scope`も **not found**。

### ReviewTrace（人間・AI・外部見解・編集の違い）
- 対応: `geo_expert_review_changes`（before/after excerpt）+ `geo_expert_review_change_notes`（reason役割: explicit_reason/supporting_comment/context）+ `promote_change`（`geo_expert_review_service.py:424-518`、reusable_reasoning→Knowledge Item化）。
- **既存で足りる**: before/after、change_type（14種）、reusable_rule昇格の骨格は良好。「After全文を無条件の正解としない」設計（wording-onlyは除外、抜粋のみ昇格）も確認済み。
- **最大のギャップ**: `reviewer_type`（human/AI/external）を区別する専用フィールドが **not found**（`reviewer_id`/`reviewer_label`は自由文字列のみ）。
- **重大な発見（RQ16に直結）**: Report Studio側の`run_expert_review`（`geo_report_synthesis_service.py:474-485`）は**人間が関与しないLLM自己レビュー**であり、`verdict=="needs_human_review"`であっても`run_synthesize_narrative`（`geo_report_studio_api.py:328-404`）は分岐せずそのまま`promote_synthesis`まで自動実行する（392-398行）。**人間承認ゲートが実質存在しない。** 一方、Case/Report Review Loop側（`geo_expert_review_service.promote_change`とは別の`geo_report_service.start_report_review_and_version`、414-449行）は人間のPromote操作が必須であり、この2経路の扱いが不揃いである。

## A2追加. CallBudget/予算の一貫性（RQ10・T15） — 確認された不一致

- `CallBudget`（`geo_expert_engine.py:1148-1166`）はStage1/Stage2/Repairの合計3回のみを管理。
- `run_research_cycle`（`geo_expert_research_service.py:359-365`）は`search_budget: int = 2`という**別立ての予算**を持ち、docstring自身が「既存CallBudget（合計3）とは別立て」「戻り値のsearch_calls_usedは既存CallBudgetに影響しない」と明記している（`geo_expert_engine.py:1712-1714`も同旨）。
- design docの絶対制約（02プロンプト55行目）「全LLM試行、repair、再評価は既存予算に計上。別名のCallBudgetを足して実質増やさない」に対し、**現状は明確に不一致**（意図的な設計コメント付きだが、外部設計文書の要求とは食い違う）。実質上限は3ではなく最大5。
- これは新規発見ではなく既存コードの設計判断だが、今回の外部要件と正面から矛盾するため、`decision_log.md`で承認要否をユーザーに確認する。

## A2追加. 既知バグの現況再確認（過去メモリとの照合）

| 既知課題 | 記録日 | 現況 |
|---|---|---|
| `_relevant_rows_by_keywords`が英語stopword未対応 | 2026-09-04 | **未修正のまま現存**。`_extract_query_keywords`(`geo_expert_engine.py:100-135`)ではKatakana/Latin候補にstopword適用なし。類似の修正(`_query_named_entity_terms`)は別ファイル(`geo_expert_research_service.py`)の別経路にのみ存在し、Source Pack構築本体には未適用。 |
| Web検索がタイトル+URLのみで本文取得なし | 2026-09-04 | **未修正のまま現存**。`qualify_and_persist_citations`(`geo_expert_research_service.py:337-347`)で`extracted_text = title`のまま。 |
| Media Profile文字数予算(6000字で切り捨て) | 2026-09-02 | **修正済み確認**。`MEDIA_PACK_CHAR_BUDGET`は12000へ引き上げ済み(`geo_media_intelligence_service.py:22`)。 |
| build_source_packのquery keyword抽出(旧無条件recencyフォールバック) | 2026-09-01 | **修正済み確認**。Katakana/Latin優先＋Kanjiフォールバックのロジックに置換済み。 |
| Confidence Cap設計課題（Knowledge CoverageとAnswer Confidenceの混同） | phase-bb backlog | **意図的に未対応のまま(P1)**。ドキュメントに明記、実LLM運用データを見てから着手する方針。 |

## A4. Prompt強化で足りる部分／構造変更が必要な部分（区分）

### (a) Prompt／Pack順序・説明改善で足りる可能性が高いもの
- SituationFrameの`explanatory_scope`/`unexplained_observations`の明示的な記述強化。
- `evidence_relevance.rationale`をより「識別診断」志向にする説明追記（ただし軸自体が無いため効果は限定的）。
- Media Intelligenceの`corroboration_basis`選択における「同一起源転載」への注意喚起の追記（既にスキーマdescriptionにあるが強化余地あり）。

### (b) optional構造化項目の追加が必要なもの（既存caseレスポンスへのoptional extensionとして、新テーブルなしで対応可能）
- DirectionalPerception: `situation_actor_assessment`または`key_actor_profiles`へ、belief_holder/perception_target/attributed_by/source_directness相当のoptionalフィールド。
- ActorFlowの`event_role`/`perceived_path`/`current_state`系。
- EvidenceUseの`usage`分類・`discriminates_between`。
- ReviewTraceの`reviewer_type`。
- ResearchNeedの`gap_type`(premise/feasibility/discrimination/scope)と`acquired_not_discriminating`区別。
- InterpretationCandidateの`candidate_relations`（exclusive/compatible/subfactor/unknown）。
- Media Intelligenceの「観測範囲（収集対象・期間）」明示フィールド。
- access_scope（内部利用 vs 外部送信可否の別軸）。

### (c) source span／帰属／時点／権限の既存情報が足りないもの
- 外部送信時のACL強制（現状は「たまたま機微情報を含めていない」という結果論の安全性であり、強制ガードではない）。ここは新フィールドというより**既存retrieval_status/reference_availabilityをResearch送信経路へ強制適用するガード**の追加が必要。

### (d) 現行の1/2段構成では接続できず、別途判断が必要なもの
- SituationFrameのCase内バージョニング（`frame_id`/`as_of`/`prior_frame_ref`）— 現状Frame概念自体が永続化されておらず、「前回Frameとの差分」を扱うには保存境界の設計判断が必要。
- CallBudget統合方針（3+2を統合するか、明示的に合計5と再定義するか）— ユーザー判断が必要。
- Report Studio自動昇格ゲートの扱い（needs_human_reviewをブロック条件にする設計変更は、既存の自動運用フローに影響するため承認が必要）。

## 監査の限界

- 6件の並行サブエージェント調査に基づく。各エージェントはfile:line付きの一次情報を報告しているが、本監査自体はコード全文の逐次読了ではなく対象ファイル群への集中調査であり、リポジトリ全体（100+ファイル）の完全な悉皆調査ではない。
- 実LLM・実DB検証は一切行っていない（design docのT class区分でいう`semantic_review`系は未実施）。
- `04_requirements_and_acceptance.json`の34テストのうち、既存テストとの対応関係の精査（どのT-IDが既存`tests/test_geo_expert_*.py`のどのテスト関数に相当するか）はこの初回監査では行っていない。`validation_plan.md`で次段階の課題として整理する。
