# Phase 3-0 Pilot Runbook（イラン・ホルムズ海峡Theater）

このRunbookは、`docs/geopolitical-phase3-pilot-results.md`で「未実施」として残っている
作業（Knowledge Item candidateの人間レビュー、Expert Review Loop）を、ユーザー・社内の
別専門家が実施する際の手順を示す。

## 1. Knowledge Item candidate 3件の人間レビュー（candidate→active）

対象:
- country_foundation (Iran): `8bf037e3-586a-4c5d-be9f-a80e2a36cb95`
- precedent (Islamabad MoU): `6be7294a-59ab-4d10-9cd6-1403ce1b28c6`
- leverage_card (US→UAE): `338dfdca-020d-4bf0-bdd5-2a8fa3fc0410`

修正済みの内容は`docs/geopolitical-phase3-pilot-results.md`および会話ログのテキスト提示を
参照（3件ともstructured_contentは既にレビュー指摘反映済み、`status=candidate`のまま）。

**★重要: このレビューが確認するのは「地政学的に何が真実か」ではない。** External Reference
Modelの原則（`docs/geopolitical-external-reference-model.md`）通り、外部の地政学的評価
そのものの真偽判定はここでは行わない。確認するのは次の3点のみ:

1. 記載内容が挙げたSource/Evidence（`geo_knowledge_item_claims`で紐付いたClaim、その先の
   Document/Chunk）と対応しているか（捏造・誤引用が無いか）
2. `precedent`/`leverage_card`/`country_foundation`のSchema構造（背景・triggering_event・
   outcome・target_dependency等）が妥当に整理されているか
3. 他Case・他Theaterでも再利用する価値があるReusable Knowledgeとして成立しているか

手順: Candidate Review Queue画面（`/geo/knowledge-review`）またはAPI
（`POST /api/geo/knowledge/{id}/review`）経由で、実際の人間（ユーザーまたは社内の別専門家）
が自分自身の`reviewer_id`で`review_knowledge_item`を実行する。**Claude/seedスクリプトの
actor_id（`phase3_pilot_seed_agent`）でこの承認を代行することはない。**

## 2. Expert Review Loop（Case1〜3）

各Caseについて、以下をユーザーと協働で実施する。

1. AI Draftは生成済み（`docs/geopolitical-phase3-pilot-results.md`のresponse_id参照）
2. ユーザーがAI Draftを社内の別専門家へ引き渡す（Claudeの管轄外）
3. 別専門家がExpert Final（レビュー後の最終見解テキスト）を作成する
4. ユーザーからExpert Finalのテキストを受け取る
5. `geo_expert_review_service.start_review_session`
   （`draft_expert_response_id`=手順1のresponse_id、`final_text`=手順4のテキスト）を実行
6. 必要に応じて`add_review_note`でレビューコメントを追加
7. `extract_deltas`を実行（**実LLM呼び出し、実行前にユーザーへ確認**）
8. 抽出されたChangeをユーザーと確認し、`promote_change`/`reinforce_change`/
   `keep_case_only`/`reject_change`のいずれかを判断・実行
   （`promote_change`のreviewer_idも実在の人間のIDを使う。詳細は
   `docs/geopolitical-internal-expert-learning.md`）

## 3. Reuse Test（Plan8章）の実施タイミング

上記2を最低1Case分実施し、Internal Expert Reasoning/Assessmentが最低1件Promoteされた後、
別のCaseの`geo_expert_engine.build_case_context`実行時（＝そのCaseで新規にAI Draftを
再生成する時）に、Promoteされた項目が「Internal Expert Reasoning Rules/Assessments」
セクションへ実際に出現するか、`geo_retrieval_results.used_in_final_context`で確認する。
取得された／されなかった／該当Case自体が無かった、のいずれであっても事実として
`docs/geopolitical-phase3-pilot-results.md`へ追記する。

## 4. Pilotデータの取扱い（終了時判断）

Pilot完了後（上記1・2が一通り終わった時点）、投入した`is_demo=false`の実データ
（Theme/Entity/Source/Claim/Knowledge Item/Case等）を、Taiwan Strait同様の実運用データ
として残すか、Pilot専用のため削除するかをユーザーと確認して決定する。
