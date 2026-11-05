# Geopolitical Expert AI — Session 2 Intelligence Reasoning Upgrade（実装記録）

2026-08-30、承認済みPlan（Phase0-8、`C:\Users\269811\.claude\plans\pure-drifting-cosmos.md`）に基づき
Phase0-5を実装。目的は「局面のKey Pointを正しく置き、Key Actorの意思を起点に発言・行動・メディアを
解釈し、複数仮説をEvidenceで絞り込むReasoning精度を上げること」（データモデル拡張はその手段）。

## 実装済みPhase

### Phase 0: Before Baseline
実DB・実Azure OpenAIで、既存3 Pilot Case（`geo_cases`、Hormuz Theater）に対しSession2適用前
（現行コード）のBefore-AI応答を取得・保存済み（`docs/session2-golden-case-baseline/before_*.json`）。

**重要な制約（未解決のまま進行）**: `geo_expert_review_sessions`が全DBで0件、3 Pilot Caseは
`analyst=phase3_pilot_seed_agent`（自動シード）で`expert_reviewer=null`。Plan Phase0が要求する
「Expert Judgment provenance」（reviewer/review_source/review_date等）を満たすGolden Caseが
現時点で1件も存在しない。ユーザーへ確認したところ、実レビュー作成は「今は後回し」と判断
（[[geo-dashboard-pending-blockers]]参照）。**そのため本Phaseで実施できたのは技術的なBefore/After
比較のみであり、Expert Judgmentとの正式なRubric Pass/Fail判定（Plan Phase5節）は未実施・保留**。

### Phase 1: Key Point Selection Upgrade
- `schemas/geo_expert_ai_response.schema.json`: `key_point_assessment`追加（`candidate_key_points`/
  `primary_key_point`/`selection_rationale`/`rejected_candidates`/`hinge_structure`/`control_type`/
  `controlling_entity_ids`/`control_structure_summary`/`supporting_evidence_ids`/
  `contradicting_evidence_ids`/`disconfirming_signal`/`confidence`）。Stage1所有・常時生成。
- `geo_expert_schema.py`: `build_stage1_schema()`へ追加、`_KEY_POINT_NOTE`をMODE_GUIDANCEへ追加。
- `geo_expert_engine.py`: `_format_structural_context`の一般Entity一覧へ`entity_id`表示を追加
  （`_format_actor_intelligence_block`と同形式）。`stage1_visible_entity_ids`集合を新設し、
  Quality Guardのgrounding判定に使用。
- `geo_expert_quality_guard.py`: `_key_point_evidence_grounded_check`/
  `_key_point_control_type_consistency_check`/`_key_point_actor_refs_grounded_check`追加。
- **Phase1 Acceptance Test（実データ検証済み）**: Case1（`a26560a5-...`）で実際にStage1プロンプトを
  構築し、Foundation Pack上の8件のEntity全てが`canonical_name（entity_type、entity_id: <uuid>）`
  形式で実際にプロンプトへ含まれることを確認。

### Phase 2A: Persistent Actor Structure — Schema/Read対応
`geo_knowledge_item_schemas.py`の`ACTOR_PROFILE_SCHEMA`へ`core_objectives`のtier分類
（`_ACTOR_OBJECTIVE_STATEMENT_ITEM`）、`persistent_preference`の正式プロパティ化、新規
`strategic_communication_pattern`、`decision_patterns`へのpattern_id/pattern_role/
default_pattern_ref/exception_conditions追加。`geo_actor_intelligence_service.py`の
`build_actor_intelligence_pack`と`geo_expert_engine._format_actor_intelligence_block`を
拡張しExpert AIから読める状態にした（自動候補生成・昇格はPhase6、本Phaseでは実装しない）。

### Phase 2: Situation Actor Assessment
新規Stage2モジュール（`deep`プロファイル限定、`case_analysis`/`theme_design`）。Key Actor ×
Issue Componentの局面依存アセスメント（`salience`/`priority_rationale`/
`effect_on_actor_objective`/`instrumental_value`/`current_red_line`/`currently_tradeable`/
`stated_preference`/`estimated_real_preference`/`genuine_or_tactical`/
`primary_binding_constraint`/`primary_usable_leverage`等）。Quality Guard:
`_situation_actor_assessment_grounded_check`/`_situation_actor_assessment_salience_confidence_
consistency_check`/`_situation_actor_assessment_evidence_grounded_check`（推論的判断への
Evidence Discipline、任意・推奨分を含めて実装）。

### Phase 3: Media / Information Reasoning
既存`media_editorial_context[]`/`narrative_deviation[]`を拡張。Observable（`observed[]`、
意図を語れない事実表現）とHypothesis（`hypotheses[]`、category=editorial_framing/
source_signaling/audience_reaction）を構造的に分離、`corroboration_basis`/
`corroboration_target`で「出来事の確認」と「意図の確認」を区別、`omitted_actor_analysis[]`
（entity_idでgrounding）追加。Media Hypothesisの自動Claim化はしない（response_jsonのみ）。
Quality Guard 6件追加（`_media_observed_fields_free_of_inference_language`/
`_media_editorial_hypotheses_not_uniformly_confirmatory`/
`_media_beneficiary_not_treated_as_intent_proof`/`_media_hypothesis_evidence_grounded_check`/
`_anonymous_sourced_hypothesis_confidence_capped`/`_media_omitted_actor_analysis_grounded`）。

### Phase 4: Investigation Questions / Alternative Hypothesis
`investigation_questions[]`新設（situation_actor_assessmentと同一Stage2モジュール、新規Azure
呼び出しなし）。`question_type`（analytical_question/evidence_check/knowledge_gap）と
`can_current_evidence_answer`の双方向契約をQuality Guard（`_investigation_question_answer_
grounded`）で検証。`can_current_evidence_answer=false`の項目のみを決定的Python処理
（`_merge_investigation_questions_into_additional_research`、正規化Fingerprintでdedupe）で
`additional_research[]`へ反映。

## 回帰テスト
`tests/`全体で384件成功（既知の無関係な事前課題6件を除く: `test_geo_official_crawler.py`1件、
`test_sustainability_article_selector.py`5件、いずれもSession2と無関係）。

## Azure Structured Output Strict Mode契約チェック（実Azure、`scripts/geo_expert_
strict_schema_contract_check.py`）
Stage1・全Stage2モジュール単体・実運用と同じ合成パターン（`audience_signal+media_context+
deep_scenario+situation_actor_assessment`）を含む10パターン全てが`strict=true`で受理された。

## Phase5: 実データE2E（部分実施、Golden Case Rubricは保留）

Case1（`a26560a5-f265-424f-8677-ada5774be085`、ホルムズ海峡、`profile=deep`）で実DB・実Azureに
よるAfter-AI実行を1件実施（`docs/session2-golden-case-baseline/after_a26560a5-....json`）。

**技術的検証結果**:
- Azure call数: 2回（Stage1+Stage2、CallBudget=3以内）。token: prompt 71,357 / completion
  21,198 / 合計92,555。
- Quality Guard: 22 passed / 2 failed。失敗2件は`fact_claims_have_source_ref`/
  `stakeholders_grounded_in_source_pack`という**Session2以前から存在する既存チェック**であり、
  Before実行時点（`quality_failed: 2`）と失敗数が変わっていない。**Session2で新規追加した
  Reasoning系チェック（Key Point/Situation Actor Assessment/Media/Investigation Questions
  関連）は全件合格**——新しいReasoning要素が品質劣化を生んでいないことを示す。
- `key_point_assessment`: 4件のcandidate_key_pointsを比較検討した上でPrimary
  （「イスラマバードMoUで長期管理体制をイラン・オマーン協議に先送りした暫定性」、
  control_type=institutionally_constrained）を選定。`rejected_candidates`に他3候補の棄却理由
  を具体的に記述。`controlling_entity_ids`は実在するentity_id（IRGC/Iran）を正しく参照し、
  架空のIDは検出されなかった。Before AIには本フィールド自体が存在しなかった
  （`primary_key_point_field_present: false`）。
- `situation_actor_assessment`: 10行（上限目安12-15の範囲内）。`instrumental_value`で
  intrinsic/instrumental/mixedを実際に区別し、`stated_preference`と`estimated_real_
  preference`の乖離（例: イランの最大20%罰金要求は「表明された起点であり、実際にはより
  低い率での妥協も視野に入れている可能性」）を明示。
- `investigation_questions`: 5件生成、全てcan_current_evidence_answer=falseで
  `additional_research`へ決定的に反映（重複判定は正規化Fingerprint、LLM側が独自に生成した
  類似だが完全一致ではない項目とは別エントリとして残る——Planで許容した既知のトレードオフ）。

**未実施・保留（Golden Case Expert Review provenance未整備のため）**:
- Plan Phase5の正式Pass/Fail判定（Golden Case Rubric、A vs C比較、Before=2/Before<2群分けの
  非劣化・改善判定）は、Expert Judgment provenance付きGolden Caseが0件のため実施できない。
- A/B/C（Reasoning Only / Actor Intelligence Only切り分け）比較、Session3 Gap抽出も同様に保留。
- Phase6（Persistent Actor Structure候補生成）・Phase7（Media Profile永続化）・Phase8（UI）は
  Plan通りPhase5評価完了後に着手する方針のため、**現時点では未着手**（Phase5が技術検証のみで
  正式Passに至っていないため）。

## 次のアクション
ユーザーが実Expert Reviewの作成に着手した時点で、本docsの「Before-AI」データ
（`docs/session2-golden-case-baseline/before_*.json`）とAfter-AIを突き合わせ、Plan Phase5の
正式Golden Case Rubric評価を完了させる。それまでは本実装は「技術的に完成・実データ検証済みだが
Expert Judgmentとの正式な精度比較は未実施」という状態で維持する。
