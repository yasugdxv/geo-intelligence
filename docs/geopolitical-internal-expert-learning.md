# 地政学ナレッジ＆インテリジェンスDB — Internal Expert Learning Layer（Phase 2B-2）

対象読者: Expert Review機能の実装・運用を理解したい開発者・Reviewer。
承認済みPlan: `C:\Users\269811\.claude\plans\pure-drifting-cosmos.md`

## 1. 目的

社内専門家が実際の案件でAI Draftを修正して最終報告に仕上げる際、「何を重視し、どう比較し、どう仮説を修正したか」というReasoningは、現状どこにも構造化保存されておらず、再利用可能なInternal Knowledgeとして蓄積されていない。Internal Expert Learning Layerは、この差分からReasoning Knowledgeを抽出し、既存の`geo_knowledge_items`（`candidate→review→active`ワークフロー）を通じて品質担保したうえで蓄積する。

**「専門家が言ったから真実」とは扱わない。** Human Reviewは外部情報の承認フローではなく、社内専門家のReasoningを「他案件でも再利用する価値があるか」という観点でLightweight Reviewするために使う。

## 2. 標準フロー

```
AI Draft → Expert Final → Delta Extraction → Expert Knowledge Candidate → Lightweight Review → Reusable Internal Expert Knowledge
```

実装: `geo_expert_review_service.py`（既存`geo_candidate_extraction_service.py`と同じ構成: LLM呼び出し＋jsonschema検証＋型ゆれ正規化）。

### 2.1 Session開始（`start_review_session`）

AI DraftとExpert Finalの2文書を`geo_documents`として保存する（source_idは「Internal Expert Review」という固定Source行、`trust_tier=E`、`_ensure_internal_review_source`で存在すれば再利用・無ければ作成）。Draftは自由入力のテキスト、または既存`geo_expert_responses.response_id`（`draft_expert_response_id`）を指定してそのresponse_jsonを流用できる。

### 2.2 Reviewer Note（`add_review_note`、任意）

専門家が「なぜこの修正をしたか」を明示的にコメントとして残せる（`geo_expert_review_notes`、何回でも・0回でも可）。`note_type='free_text'`のみ今回のUI入力対象だが、`anchor_excerpt`/`source_format`列で将来のWord Comments/Track Changes取り込みを吸収できる拡張可能な構造にしてある。

### 2.3 Delta Extraction（`extract_deltas`）

Draft/Final文書を`geo_claim_service.chunk_document`でchunk化し、LLMへ「文言修正ではなく分析上意味のある変更のみ」を抽出させる。wording/grammar/tone/文短縮は候補にしない。

**Reviewer Noteが存在する場合、LLM独自の推測より専門家の明示的な理由を優先する**（system_promptで明示的に指示）。参照したNoteは`geo_expert_review_change_notes`（change_id, note_id, role）に記録する。roleは`explicit_reason`（専門家が明示した理由）/`supporting_comment`（補足）/`context`（背景情報）の3種で、将来「専門家本人の明示コメント由来」と「LLM推測由来」を区別できるようにする。

**冪等性**: `delta_extraction_status`が既に`completed`なら再抽出せず既存`geo_expert_review_changes`をそのまま返す（LLMは呼ばない）。

**Note ID検証**: LLMが返した`note_id`は、当該Sessionに実在するIDであることをコード側で検証してから`geo_expert_review_change_notes`へ保存する。存在しないnote_id参照は無視し（LLMの幻覚対策）、Change抽出自体は失敗させない。

### 2.4 抽出される情報

`change_type`（14種、ユーザー仕様どおり）、`reusability_class`（下記）、`draft_excerpt`/`final_excerpt`（片側のみでも可。stakeholder_addedはfinalのみ、stakeholder_removedはdraftのみ等）、`reasoning_summary`、`confidence`。関連するIssueは、Sessionが紐づくCaseの`geo_case_issues`から自動的に`geo_expert_review_change_issues`へリンクする（LLMに名前解決させて幻覚を招くより、既知のCase-Issue関係をそのまま使う）。

wording-only（draft_excerpt/final_excerptが両方null）の候補はChangeとして保存しない。

### 2.5 `reusability_class`（Case/時点依存の判断 vs 再利用可能な思考方法）

| 値 | 意味 | Promote先knowledge_type |
|---|---|---|
| `case_specific_assessment` | 当該Case・当該時点に紐づく判断 | `case_finding` |
| `reusable_reasoning` | 案件を越えて再利用可能な思考方法 | `change_type`に応じて`reasoning_rule`/`mechanism`/`precedent`（下表） |

`reusable_reasoning`時の`change_type`→`knowledge_type`マッピング（決定的、LLMに選ばせない）:

| change_type | knowledge_type | rule_category（reasoning_ruleのみ） |
|---|---|---|
| mechanism_added | mechanism | — |
| precedent_added | precedent | — |
| comparison_rule | reasoning_rule | comparison |
| evidence_weight_changed | reasoning_rule | evidence_weighting |
| stakeholder_added / stakeholder_removed | reasoning_rule | stakeholder_selection |
| main_hypothesis_change / alternative_hypothesis_added / uncertainty_adjustment | reasoning_rule | hypothesis_revision |
| scenario_change / trigger_added | reasoning_rule | scenario_reasoning |
| judgment_change / causal_chain_change | reasoning_rule | general |

`rule_category`は`REASONING_RULE_SCHEMA`の任意フィールド（Phase2B-2追加、requiredには含めない）。

### 2.6 Promote（`promote_change`）

既存の`geo_knowledge_service.create_knowledge_item`（status=candidate、`origin='internal_expert_review'`、`derived_from_review_session_id`/`derived_from_change_id`付与）と`review_knowledge_item`（`start_review`→`approve_active`の2段階、既存`ALLOWED_TRANSITIONS`通り）を**そのまま呼ぶだけ**。新しい状態遷移ロジックは作らない。`information_class='internal_expert_assessment'`で作成する。

`edited_content`未指定時は`_default_structured_content`が最小限の自動生成structured_contentを組み立てる（Schema検証は通すが簡素。実運用では`edited_content`指定（Edit & Promote）での加筆を推奨）。

### 2.7 Reinforce（`reinforce_change`）

既存Reasoningを別案件で再確認した場合、「重複（duplicate）」ではなく「reinforced（再確認）」として記録する。新規Knowledge Itemは作らず、対象itemの`support_count`のみ+1、`last_confirmed_at`を更新する。

### 2.8 Keep Case Only / Reject

`keep_case_only`: このReasoningは当該Case限りとし、Reusable Knowledgeへは昇格させない（Knowledge Item不作成）。
`reject_change`: 再利用価値が無いと判断し破棄する（Knowledge Item不作成）。

### 2.9 冪等性（すべての操作に共通）

`promote_change`/`reinforce_change`/`keep_case_only`/`reject_change`はいずれも、`disposition='pending'`の行のみを対象に取得・検証してから処理する（`_claim_pending_change`）。既に`pending`以外なら`ExpertReviewChangeNotPendingError`を送出し、Knowledge Item二重生成・support_count二重加算を防ぐ。DB更新も`WHERE disposition='pending'`を条件にした更新を使う（二重防御）。

## 3. Retrieval Scoreへの反映（`geo_search_service.search_knowledge_items`）

`origin='internal_expert_review'`のKnowledge Itemは、外部SourceのTrust Tierに依拠する既存の`confidence`enumベースのスコアではなく、以下の信号を使う:

```
source_score = min(1.0, 0.5 + 0.1 * min(support_count, 5) + 0.1 * confirmed_recency)
```

0.5は専門家によるPromote済みという事実そのものの基礎点。`support_count`（他案件での再確認回数、上限5で頭打ち）と`last_confirmed_at`の新しさ（既存`_recency_score`関数を流用）を信号とする。**`usage_count`/`last_used_at`はRetrieval Scoreに一切使わない**（Analytics専用、`_record_case_retrieval_trace`での記録のみ）。External Sourceのtrust_tierは参照しない。「Issue/Entity relevance」は既存の`match_score`/`text_score`（タイトル一致・FTS）がそのまま担う。

## 4. Expert Engineでの区分（Internal Expert Assessment vs Internal Expert Reasoning Rule）

`case_finding`（Case/時点依存の判断）と`reasoning_rule`/`mechanism`/`precedent`（再利用可能な思考方法）は明確に別物として扱い、`geo_expert_engine.build_case_context`でも別セクションにする。前者は「Internal Expert Assessments」、後者は「Internal Expert Reasoning Rules」。詳細: `docs/geopolitical-expert-engine-integration.md`。

`case_finding`由来のInternal Expert Assessmentを別Caseで利用する際は、元Case・判断時点(`as_of_date`)・Reviewer(`reviewed_by`)を必ずContextに表示する（`geo_retrieval_service.attach_case_finding_provenance`）。System Promptにも「当該Case・当該時点の判断であり、現在も有効とは限らない。現在のEvidenceと照合して利用する」ことを明記する。

## 5. Retrieval Governance API（Human Approvalではない運用操作）

- `PATCH /api/geo/knowledge/items/{knowledge_item_id}/retrieval-status`（`geo_knowledge_service.set_retrieval_status`）
- `PATCH /api/geo/knowledge/claims/{claim_id}/retrieval-status`（`geo_claim_service.set_retrieval_status`）
- `PATCH /api/geo/knowledge/sources/{source_id}/reference-availability`（`geo_connector_base.set_reference_availability`）

3つとも`geo_knowledge_reviews`（正誤判定用の重いテーブル）は使わず、`geo_audit.log_audit`にのみ記録する。

## 6. スコープ外（Phase 3以降）

意味的重複検出の自動化（reinforced判定は現状レビュー担当者の手動選択のみ、pgvector無しの制約から自然に導かれる）、Expert Performance Scoringの高度化、Word Comments/Track Changesの実取り込み（テーブル構造は用意済み）。
