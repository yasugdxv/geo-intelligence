# 最小差分案（承認待ち・未実装）

作成日: 2026-09-06
前提: `actual_state_audit.md` / `semantic_mapping.json` の監査結果に基づく。**本文書はこれから何をするかの提案であり、まだ何も実装していない。**

## 優先度付け方針

design docの絶対制約（既存R1/Session2/Actor/Reference/Reviewを作り直さない、11概念を11サービスに分けない、恒久モデル新設を第一案にしない）に従い、以下の順で「接続」を優先し、新規構造は最小限にする。

## Tier 0（今回発見した既存設計との不一致 — 実装ではなくユーザー判断が先）

| # | 内容 | 対応ファイル | 判断が必要な理由 |
|---|---|---|---|
| 0-1 | CallBudget(3) と search_budget(2) が別立てで、design doc絶対制約（別名予算での実質増加禁止）と矛盾 | `geo_expert_engine.py:1148-1166`, `geo_expert_research_service.py:359-365` | 統合すると既存の予算前提で動くコード・ログ・テストに影響。現状維持を選ぶ場合はdesign docとの不一致を明示的に受け入れる判断が要る。 |
| 0-2 | Report Studio の `run_expert_review` が人間承認ゲート無しで `promote_synthesis` まで自動実行 | `geo_report_synthesis_service.py:474-485`, `geo_report_studio_api.py:328-404` | 既存運用でここに人間確認が別途入っているか要確認。仕様変更は本番Report生成フローへ直接影響する。 |

→ この2件は`decision_log.md`で個別に確認する。

## Tier 1（優先度高: design doc冒頭の中核要求に直結、影響範囲が限定的）

| # | 意味的オブジェクト | 変更内容 | 対象ファイル | 影響範囲 |
|---|---|---|---|---|
| 1-1 | DirectionalPerception | `situation_actor_assessment`または`key_actor_profiles`へ、belief_holder(暗黙=actor自身)/perception_target/attributed_by/source_directnessを持つoptional配列を追加 | `geo_expert_schema.py`, `schemas/geo_expert_ai_response.schema.json` | Stage1/Stage2スキーマのみ。既存フィールドは変更なし。 |
| 1-2 | ActorFlow | `event_role`/`perceived_path`/`current_state,blockers,need`をkey_actor_profilesへoptional追加 | 同上 | 同上 |
| 1-3 | access_scope | Source/EvidenceUseへaccess_scope(internal_only/public_safe、既定internal_only)を追加し、`run_external_research`の送信ペイロード組み立て時に強制フィルタを適用 | `geo_knowledge_item_schemas.py`, `geo_expert_research_service.py` | 既存データはinternal_only扱い(安全側デフォルト)。外部送信経路にガードを追加するのみで通常フローは無変更。 |

## Tier 2（優先度中: 既存フィールドの粒度不足を埋める）

| # | 意味的オブジェクト | 変更内容 | 対象ファイル |
|---|---|---|---|
| 2-1 | EvidenceUse | `evidence_relevance` itemへusage(premise/feasibility/discrimination/model_update/editorial_context)とdiscriminates_betweenをoptional追加 | `geo_expert_schema.py`, `schemas/geo_expert_ai_response.schema.json` |
| 2-2 | ResearchNeed | `assess_research_sufficiency`にdiscrimination軸を追加、`acquired_not_discriminating`という新stop_condition値を導入 | `geo_expert_research_service.py` |
| 2-3 | FeasibilityAssessment | `action_substance`へexpected_response/effectをoptional追加 | `geo_expert_schema.py` |
| 2-4 | InterpretationCandidate | `hypothesis_register` itemへcandidate_relations(exclusive/compatible/subfactor/unknown)をoptional追加 | 同上 |
| 2-5 | ConditionalDevelopment | `situation_actor_assessment.likely_reaction`をactor_expected_response/analyst_expected_responseへ分離(旧フィールドは維持) | 同上 |
| 2-6 | ReviewTrace | `geo_expert_review_changes`または`sessions`へreviewer_type(既定=human)を追加(DBマイグレーション要) | `geo_expert_review_service.py`, 対応SQL |

## Tier 3（優先度低〜今回は見送り候補: Prompt強化のみで様子見）

| # | 内容 | 理由 |
|---|---|---|
| 3-1 | Media origin家系の機械的クラスタリング | 既存`corroboration_basis`＋Guardで機能的にある程度カバーされている。誤判定リスクを避けるためPrompt強化を先に試す。 |
| 3-2 | SituationFrameのCase内バージョニング(frame_id/as_of/prior_frame_ref) | 保存境界の設計判断が必要で構造が大きくなりやすい。今回は見送り、Tier横断課題として`decision_log.md`で扱う。 |
| 3-3 | 既知バグ2件（英語stopword未対応、Web検索本文未取得） | design docの直接要求ではないが、EvidenceUseの`content_state`/`temporal_fit`拡張と合わせて修正すると効率的。Tier2完了後に着手候補として記録。 |
| 3-4 | Operator library(OP01-14)のcode化 | design doc 9.2で「毎回全型実行の強制」を明示的に禁止しており、専用フィールド(operator_ids)化は不要。Prompt/Pack内の候補生成指示強化のみで対応する。 |

## 実装しないこと（再確認）

- 新しいLLM call、新しいDBテーブル11個相当の新設は行わない。
- 既存R1/Session2 Upgrade/Actor・Institution・Media Intelligence/Reference/Review/Report Studioのgreenfield書き直しは行わない。
- active/available/ACLの既存フィルターを迂回しない。
- 恒久Profile/Ruleの自動active化は追加しない(Tier1-3のaccess_scopeも既定値は安全側)。
- Session3、Cross-Narrative機能、UI刷新、外部自動配信は対象外。

## 想定するfeature flag

design docの指示通り、新項目は既定OFFのoptional extensionとして実装する。フラグ名は既存の`GEO_EXPERT_EXECUTION_MODE`等の命名規約(`GEO_EXPERT_*`のUPPER_SNAKE_CASE環境変数)に合わせて、実装フェーズ(B)開始時に個別に決定する。
