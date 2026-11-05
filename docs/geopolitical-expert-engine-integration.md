# 地政学ナレッジ＆インテリジェンスDB — 専門家AI統合（Phase 2A、Phase 2B-2で拡張）

対象ファイル: `geo_expert_engine.py`（Phase1では意図的に無改変だった唯一の変更対象）

## 1. 後方互換の維持

`run_expert_request(client, azure_client, model, mode, theme_id, query_text, draft_text=None, *, case_id=None)`に`case_id`をキーワード専用引数として追加した。`build_source_pack()`と`_build_system_prompt()`はバイト単位で無改変。**`case_id`を渡さない既存呼び出しは、Retrieval Layerを一切呼ばず、Phase1以前と完全に同じ挙動になる**（`tests/test_geo_expert_engine.py`の既存3件、`tests/test_geo_phase2a.py::test_run_expert_request_without_case_id_is_backward_compatible`で確認済み）。

## 2. case_id指定時の処理フロー

```
run_expert_request(..., case_id=case_id)
  → build_source_pack()                      # 既存、無変更
  → case_id指定時のみ: build_case_context(client, case_id)
       → geo_retrieval_service.get_foundation_knowledge / get_relevant_precedents /
         get_relevant_mechanisms / get_relevant_reasoning_rules / get_reusable_knowledge(indicator) /
         get_contested_knowledge / get_case_context
       → Evidence必須条件フィルタ → Token Budget削減 → text組み立て
  → user_promptへcase_context["text"]を追記
  → LLM呼び出し（構造化、既存と同じ）→ Quality Guard（既存、無変更）→ 各テーブルへ保存
  → case_id指定時のみ: _record_case_retrieval_trace()（geo_retrieval_runs/results）
                       geo_cases.final_outputへresponse_jsonを保存
```

## 3. Context構築ルール（`build_case_context`）

### セクション別Top-K（`SECTION_TOP_K`、`geo_expert_engine.py`冒頭）

| セクション | Top-K |
|---|---|
| foundation | 5 |
| precedent | 3 |
| mechanism | 3 |
| reasoning_rule | 5 |
| indicator | 5 |
| live（Claim） | 10 |
| gap | 10 |
| observed_fact（Phase2B-2） | 10 |
| actor_claim（Phase2B-2） | 10 |
| external_assessment（Phase2B-2） | 10 |
| internal_expert_assessment（Phase2B-2） | 5 |
| internal_expert_reasoning（Phase2B-2） | 10 |

Case Hypothesisは全件（既に少数）。Phase2B-2で追加した5セクション（下記4.1章）はToken Budgetの対象外（Issue単位のTop-Kのみで制御する簡易化。既存のReusable/Foundationのみ引き続き文字数Budgetで間引く）。

### Token Budget

`DEFAULT_CHAR_BUDGET = 6000 * 2`（1トークン≒2文字の粗い概算、正確なtokenizerは未導入）。Case固有情報（Hypothesis/Gap）の文字数を先に差し引いてから、Reusable Knowledge・Foundation Knowledge・Live Contextの合計がこの予算に収まるよう調整する。

### 重複除外

`seen_ids`セットでKnowledge Item IDの重複を除去する（同一Itemが複数セクションから来た場合、最初に見つかったセクションのみ残す）。

### Evidence必須条件（`_has_supporting_evidence_chain`）

`structured_content`内のsources/evidence相当フィールドの自己申告では不十分なため、**`geo_knowledge_item_claims`で`relationship='supporting'`のリンクが1件以上あり、かつそのClaimが`geo_claim_evidence`を1件以上持つこと**を必須条件とする。満たさないactive Itemは`excluded`へ`exclusion_reason="no_supporting_evidence_chain"`として記録され、Expert Engineには渡らない。

この条件を満たすため、`geo_candidate_extraction_service.extract_candidates_from_case`は候補作成時に既存Caseの全Claimへ`geo_knowledge_item_claims`（supporting）を張る。`extract_candidates_from_external_claims`（World Bank/OFAC由来）は入力Claim全件へ同様にリンクする。**この2箇所の実装が無ければ、Active化されたKnowledge ItemはPhase2A以降Expert Engineへ二度と投入されなくなる**（Evidence必須条件を追加した際に必要になった、Phase1側への遡及的な修正）。

### Budget超過時の削減順序（final_scoreが低い順、下記1→4の優先度でグループ単位に間引く）

1. Reusable Knowledge（Precedent/Mechanism/Reasoning Rule/Indicator）の下位項目
2. Live Context（Claim）の下位項目
3. Foundation Knowledgeの下位項目
4. Case固有情報（Hypothesis/Gap）は最後まで残す（分析の骨格のため間引かない）

間引かれた項目は`excluded`へ`exclusion_reason="token_budget_exceeded"`として記録される。

## 4. Active/Contestedの分離

`get_foundation_knowledge`/`get_reusable_knowledge`（`status='active'`のみ）と`get_contested_knowledge`（別関数、Phase1で分離済み）を**別々に呼び出し**、システムプロンプト内でも別セクションとして扱う。

- 「【Knowledge DBから参照した確定情報（Foundation）】」「【Knowledge DBから参照した確定情報（Precedent/Mechanism/Reasoning Rule/Indicator）】」
- 「【対立する見方（Contested、確定事実として扱わないこと）】」

`_build_system_prompt`にも「後者は対立する評価として扱い、確定事実であるかのように断定しない」という厳守事項が既にPhase1から含まれる。`get_candidate_knowledge`（candidate/in_review）は一切Expert Engineへ渡さない（UI表示専用）。

## 4.1 Phase 2B-2で追加した5セクション（詳細: `docs/geopolitical-external-reference-model.md`、`docs/geopolitical-internal-expert-learning.md`）

既存6セクション（Foundation/Reusable/Contested/直近のClaim/仮説/Gap）に加えて以下を追加する。既存6セクションのロジック・出力は無変更。

| セクション見出し | 取得元 | 絞り込み条件 |
|---|---|---|
| 【観測・記録された事実情報】 | `geo_retrieval_service.get_reference_claims_for_issue`＋`case_context["claims"]`をinformation_class別に振り分け | `information_class='observed_fact'`, `retrieval_status='available'` |
| 【Actor Claims（主体の発言、事実性は保証しない）】 | 同上 | `information_class='actor_claim'` |
| 【External Assessments（外部専門機関の見解、Source/Perspectiveを併記）】 | 同上 | `information_class='external_assessment'`。各行にSource名・perspectiveを解決して併記する（`_resolve_source_label`） |
| 【Internal Expert Assessments（本件・時点に紐づく専門家の判断）】 | `geo_retrieval_service.get_internal_expert_assessments`→`attach_case_finding_provenance` | `knowledge_type='case_finding'`, `information_class='internal_expert_assessment'`, `status='active' AND retrieval_status='available'`。各行に元Case名・as_of_date・Reviewerを併記する |
| 【Internal Expert Reasoning Rules（案件を越えて再利用可能な思考方法）】 | 既存の`reusable_included`（precedent/mechanism/reasoning_rule/indicator）から`information_class='internal_expert_assessment'`のものだけを分離 | 既存のstatus='active' AND retrieval_status='availableのAND条件済みの結果をさらに絞り込むのみ（新規クエリは追加しない） |

旧来の「【直近のClaim（Live Context）】」セクションは廃止し、`case_context["claims"]`をinformation_classで上記3セクション（観測・記録された事実情報/Actor Claims/External Assessments）へ統合する。どの分類にも当てはまらないClaim（internal_hypothesis等）は「【直近のClaim（その他）】」として引き続き表示し、データを失わないようにする。

System Promptには「外部機関のAssessmentや政府のClaimは、その主体の見方・主張として扱う」「Internal Expert AssessmentsとInternal Expert Reasoning Rulesを混同しない」「Internal Expert Assessmentsは当該Case・時点のものであり現在も有効とは限らない」という3つの厳守事項を追加した（既存文言は無変更）。

## 5. Retrieval Trace記録（`_record_case_retrieval_trace`）

`retrieval_mode="expert_context"`で`geo_retrieval_runs`へ1行、`included`（`used_in_final_context=true`、`inclusion_reason="within_budget_and_top_k"`）と`excluded`（`used_in_final_context=false`、`exclusion_reason`付き）それぞれに`geo_retrieval_results`を記録する。`geo.cases.$id.tsx`のRetrieval Trace一覧から確認できる。

Phase2B-2: `included`にはInternal Expert Reasoning Rules/Internal Expert Assessmentsも含まれ、記録時に対象Knowledge Itemの`usage_count`を+1・`last_used_at`を更新する（この2列はAnalytics専用でRetrieval Scoreには使わない。`docs/geopolitical-internal-expert-learning.md`3章）。External Reference（観測・記録された事実情報/Actor Claims/External Assessments）はRetrieval Traceの対象外（Claim側の`geo_retrieval_results.result_type='claim'`は既存スキーマ上サポートされているが、今回のスコープでは記録しない）。

## 6. 分析結果の永続化

`case_id`指定時のみ、LLM応答（`response_json`）を`geo_cases.final_output`へ保存する（`SupabaseClient.update()`は戻り値を返さないため、保存後に再取得はせず、呼び出し元がAPIレスポンスとして返す`result["response"]`をそのまま使う）。

## 7. API / フロントエンド

- `POST /api/geo/knowledge/expert/analyze`（body: `{case_id, mode, theme_id, query_text, model}`等）
- `geo.cases.$id.tsx`: 「Expert Engine実行」ボタン、応答JSON表示、Quality Guardバッジ、クリック可能なRetrieval Trace一覧（各結果の採否理由・スコア表示）。Phase2B-2でExpert Reviewセクション（Session開始・Delta Extraction・Promote/Reinforce/Keep/Reject）を同画面に追加した（新規routeは作らない）

## 8. テスト

`tests/test_geo_phase2a.py`: `test_build_case_context_excludes_knowledge_without_evidence_chain`、`test_build_case_context_includes_knowledge_with_evidence_chain`、`test_run_expert_request_with_case_id_persists_final_output_and_trace`、`test_run_expert_request_without_case_id_is_backward_compatible`。既存`tests/test_geo_expert_engine.py`（3件）は無変更のまま合格。

Phase2B-2: `tests/test_geo_phase2b2_external_reference.py`（二軸独立性・Source Reference Policy等13件）、`tests/test_geo_expert_review.py`（Expert Review Session/Delta Extraction/Promote/Reinforce等16件）を追加。既存回帰含め`pytest tests/ -q`で172件中166件pass（既知の無関係な失敗6件は既存の`test_sustainability_article_selector.py`5件＋`test_geo_official_crawler.py`1件、Phase2B-2の変更とは無関係）。
