# 未解決事項・承認が必要な変更（決定ログ）

作成日: 2026-09-06 / 更新: 2026-09-06（ユーザー判断反映、P0・Tier1実装完了）

## 更新履歴

- **D1を調査・解決**: 追加調査の結果、CallBudget(3)はChat Completions API(構造化出力生成、Stage1/Stage2/Repair)のみを数え、search_budget(2)はResponses API+Web検索ツール(`run_external_research`)という**別の物理呼出し種別**を数えていることを実コード・実データで確認した。二重計上・実質超過は無い。**「別名のCallBudgetを足して実質増やさない」という制約への違反ではなかった**（ユーザーの当初の想定通り）。統合は行わない。詳細は下記「D1（解決済み）」参照。
- **D2を実装完了**: `geo_report_synthesis_service.synthesis_auto_promote_eligible(final_status, review_verdict)`を新設し、`review_verdict=="needs_human_review"`の場合は`geo_report_studio_api.run_synthesize_narrative`が自動昇格(`promote_synthesis`)をスキップするよう修正。生成・保存(`persist_synthesis`)は継続する。既存の手動再昇格エンドポイント(`repromote_synthesis`)は無改変で、人間承認後の昇格経路として機能する。新規テスト4件追加、既存テスト回帰確認済み(全618件pass)。
- **Tier1実装完了**: DirectionalPerception(`directional_perceptions`、Stage2新規モジュール、deepプロファイル限定)、ActorFlow.perceived_path/event_role(`key_actor_profiles`、Stage1常時)を実装。Groundingガード2件(`_directional_perceptions_grounded_check`、`_directional_perception_not_self_referential_check`)を追加。新規Azure呼び出しは追加していない(既存Stage1/Stage2の1回呼び出しへ相乗り)。
- **Tier1追加確認・実装完了（2026-09-06、ユーザー再確認を受けて）**:
  - **A. DirectionalPerceptionの入力側接続**: `geo_actor_intelligence_service.build_actor_intelligence_pack`の`relevant_relationships`投影が、方向(from/to)・相手(counterpart_entity_id/counterpart_name)を欠落させたまま、かつ「このActorを含まない関係」まで一律に付与していたバグを修正。`entity_id`（このActor）を含む関係のみに絞り込み、`direction`/`counterpart_entity_id`/`counterpart_name`/`dependency_direction`/`confidence`を追加。`geo_expert_engine._format_actor_intelligence_block`の描画も相手・方向を明示する形へ更新。既存Relationship DB(`geo_relationships`)→Actor Intelligence Pack→プロンプトという既存の retrieval経路を修正しただけで、新テーブル・新規write-backは無い。
  - **B. Reasoning Tier1の残り2項目**:
    1. Candidate Generation Prompt: `geo_expert_schema._CANDIDATE_GENERATION_OPERATORS_NOTE`を新設。Goal/Means・Perspective Shift・Counterparty Shift・Causal Belief・Optionality・Intermediate State・Third-party Audience・Time Horizon・Non-action・Changed Constraintの10種を局面に有効なものだけ選んで適用し、候補をActor→Goal→Perceived Path→Event Role→Expected Response→Desired Stateのつながった読みとして`hypothesis_register[].reason`へ記述するよう指示（Prompt強化のみ、新規schema field・新規LLM callなし）。
    2. Actor/Analyst分離: `situation_actor_assessment`へ`actor_expected_response`/`analyst_expected_response`をoptional追加、`likely_reaction`は後方互換のため維持。`geo_expert_schema._ACTOR_ANALYST_RESPONSE_SEPARATION_NOTE`で使い分けを指示。
  - オフライン回帰: 全621件pass（既存fixtureへの影響なし）。
- **Tier2/Tier3**: 今回は未着手（ユーザー方針: Tier2は設計・テスト準備までは可だが実装はTier1検証後、Tier3はSession3前にはやらない）。

---

## D1（解決済み）: CallBudget と search_budget の分離

**事実**: `geo_expert_engine.CallBudget`は合計3回のLLM呼出しを管理するが、`geo_expert_research_service.run_research_cycle`は独立した`search_budget=2`を持ち、両者は加算されない（実質最大5回）。既存コードのdocstring自体がこの分離を明記しており、隠蔽ではなく過去フェーズの意図的な設計判断と見られる。

**問題**: `02_claude_code_implementation_prompt.md`の絶対制約（55行目）「全LLM試行、repair、再評価は既存予算に計上。別名のCallBudgetを足して実質増やさない」と正面から矛盾する。

**再調査で判明した事実（2026-09-06）**: `CallBudget`の3回はいずれも`geo_llm_common.call_llm_structured`（Chat Completions API、構造化出力生成）のみを数える。`run_research_cycle`の`search_budget=2`は`run_external_research`が呼ぶ`azure_client.responses.create(...)`（Responses API + `web_search_preview`ツール）という**別のAPIエンドポイント・別の呼出し種別**を数えている。実データ（`docs/expert-ai-validation/r1-human-check-cases/raw/`のJSON）でも、同一レスポンス内で`usage.calls`（CallBudget側）と`research_metadata.research_cycle.search_calls_used`（search_budget側）が独立して記録されており、一方が他方を含んだり、実質的な二重計上をしている形跡はない。Research Cycle実行後にStage2が追加でもう1回走るという構造でもなく（Research→強化されたsource_packを使った通常のStage1、という順序）、LLM構造化出力生成は常に最大3回のまま。

**結論**: これは「LLM推論call」と「external research call」という異なる予算軸の正当な分離であり、design doc絶対制約（「別名のCallBudgetを足して実質増やさない」）への違反ではない。**統合しない。現状維持。**
ドキュメント上の意味を明確化する目的で、必要であれば`docs/geopolitical-expert-session2-reasoning-upgrade.md`等の該当箇所に「CallBudget=LLM推論call上限、search_budget=外部検索call上限、両者は別軸」という一文を追記する程度に留める（今回は未実施、次回の関連ドキュメント更新時に検討）。

## D2（修正済み）: Report Studio の自動昇格ゲート

**事実（修正前）**: `geo_report_synthesis_service.run_expert_review`はAI自己レビューのみで、`review_verdict=="needs_human_review"`であっても`geo_report_studio_api.run_synthesize_narrative`は分岐せず、決定的チェック(`run_synthesis_validity_checks`)がPASSであればそのまま`promote_synthesis`まで自動実行していた。

**ユーザー判断（2026-09-06）**: 「迷わず修正でよい。既存のHuman Review思想との矛盾。`needs_human_review=true`でも生成・保存は継続してよいが、自動昇格だけを止める。既存Human Review workflowを再利用し、新しい独立ワークフローは作らない」。P0として即修正を承認。

**実施した修正**:
- `geo_report_synthesis_service.py`に`synthesis_auto_promote_eligible(final_status, review_verdict) -> bool`を新設（`final_status=="PASS" and review_verdict != "needs_human_review"`）。
- `geo_report_studio_api.run_synthesize_narrative`の昇格条件をこの関数呼び出しに置き換え。`final_validity_status`自体は決定的Anti-Backflow Guardの結果のまま変更していない（review_verdictとは別軸のため混同しない）。
- `persist_synthesis`（draft/final_draft/final_html保存）は無条件のまま継続、ブロックされるのは`promote_synthesis`のみ。
- 人間が内容を確認した後は、既存の手動再昇格エンドポイント`POST /runs/{run_id}/syntheses/{signal_id}/{narrative_id}/promote`（`repromote_synthesis`、`final_validity_status=="PASS"`であれば呼び出し可能、無改変）からPromoteできる——新しい独立ワークフローは作っていない。
- `tests/test_geo_report_synthesis_service.py`に新規テスト4件を追加（PASS+approved/revisedで昇格可、PASS+needs_human_reviewで昇格不可、REVIEW REQUIREDでは常に昇格不可）。既存27件+新規4件、全31件pass。
- `geo_report_reassessment_service.py`（Phase6 Closed-loop Reassessment）も同じ`run_synthesize_narrative`を呼ぶため、この修正は両方の呼び出し経路に自動的に適用される（ロジックの複製なし）。

## D3. SituationFrameのCase内バージョニング

**事実**: `frame_id`/`as_of`/`prior_frame_ref`に相当する永続化構造が存在せず、「前回Frameとの明示的連結」はコード上表現できない。

**論点**: これを実装するには、Caseレスポンスのどの粒度でFrameを保存するか（Case全体の最新状態のみか、Frame変遷の履歴を残すか）という設計判断が必要で、design doc 9.3「段階的に実装する」の対象になり得る。

**求める判断**: 今回のTier1-2実装後、D3を次フェーズの検討課題として保留してよいか、それとも今回の最小差分に含めるか。

## D4. 既知バグ2件の扱い（stopword未対応／Web検索本文未取得）

**事実**: 過去メモリに記録された2件の既知バグ（`_relevant_rows_by_keywords`の英語stopword未対応、Web検索がタイトル+URLのみで本文未取得）は、今回の監査でも未修正のまま現存することを確認した。

**論点**: これらはdesign docの直接要求ではないが、EvidenceUseの`content_state`（Tier2-1で追加検討中）と密接に関わる（本文が無ければcontent_stateを正しく分類できない）。

**求める判断**: 今回の最小差分(Tier1-2)に含めるか、別タスクとして独立に進めるか。

## D5. 監査対象外・確認できなかった事項

- Report Studio独自データ系列(`geo_report_records`等)へのM130的取扱い制限の継承有無は、今回の調査対象ファイル内では確認できなかった（「Not found（要追加確認）」とサブエージェントが報告）。追加調査が必要な場合は次回セッションで実施する。
- 04の34テストと既存`tests/test_geo_expert_*.py`内の個別テスト関数との一対一対応表は、今回の初回監査では作成していない（`validation_plan.md`に大枠のみ記載）。承認後、B段階着手前に詳細対応表を作る。
- 内部レビュー文書・Report文書のSourceが、Steward判断を経ずに生成コード自身によって`reference_availability="available"`へ自動設定される点（`geo_expert_review_service._ensure_internal_review_source`, `geo_report_service._get_or_create_report_source`）は、既存の運用前提（Stewardが手動設定）とコードの実態に乖離がある。M130等の機微情報がここに混在した場合のリスクとして記録するが、今回のTierには含めていない。追加確認が必要か判断を仰ぐ。

## 実装状況まとめ（2026-09-06時点）

- **完了・テスト済み**: P0(Report Studio Human Review Gate)、Tier1(DirectionalPerception、ActorFlow.perceived_path/event_role)。オフライン回帰618件pass。
- **未実装（ユーザー方針により今回スコープ外）**: Tier1のaccess_scope（internal_only/public_safe軸）——これは元のminimal_change_planではTier1優先度高としていたが、ユーザーの今回の指示にはaccess_scopeの言及が無く、Tier1として明示承認されたのはDirectionalPerception/perceived_path/event_roleの3点のみ。access_scopeは次回指示を待つ（Tier1の残課題として`minimal_change_plan.md`に維持）。
- **設計・テスト準備までは可、実装は待ち**: Tier2（EvidenceUse usage/discriminates_between、InterpretationCandidate candidate_relations）。ユーザー指示:「Tier1を検証してから」。
- **停止**: Tier3全項目（Frame persistence、Dynamic Human Model auto-update、Candidate scoring engine、自動Dominant Flow selection高度化、Cross-Narrative統合、新DB体系）。Session3前にはやらない。
- **D3(SituationFrameバージョニング)/D4(既知バグ2件)**: 未着手のまま、次回判断待ち。

## 次のステップ（ユーザー承認待ち）

Tier1実装後、既存Baselineを壊していないことの確認と、同一ケース（Trump-Putin想定）でのOFF/ON比較が残っている。これは**実Azure OpenAI呼び出しを伴う**（コスト発生、実LLM検証）ため、`validation_plan.md`の「クラス2: 実LLM・実DB」区分に従い、以下をユーザーに確認してから実行する:
1. 比較に使う具体的な入力（theme_id/case_id、Trump-Putinの想定局面が既存DBにデータとして存在するか、無ければFixture投入が必要か）。
2. 使用するprofile（`deep`——directional_perceptionモジュールはdeepプロファイル限定で導入したため）とmode（`case_analysis`想定）。
3. 予算・モデル（既存CallBudget=3、search_budget=2の枠内で問題ないか）。
4. 既存の凍結Baseline（Hormuz/EUDR等）には触れず、新規Runとして別ディレクトリに保存する方針でよいか。
