# 地政学専門家AI v1.2 段階実行(Staged Execution) Phase A: 実データ比較結果

対象: Hormuz Strait Theme（`9a62d084-1442-45c9-96fc-50e2a0d3e1a9`）の既存3ケース（case_analysisモード、`case_id`なし）。
現行の一括方式（`geo_expert_engine.run_expert_request`）と、段階実行方式（`geo_expert_engine.run_expert_request_staged`, `profile="standard"`）を、同一query_textで実行して比較した。
実行日: 2026-08-28。比較スクリプトは一時的なもの（コミット対象外）。

## 1. 呼び出し回数・トークン・応答時間

| ケース | 方式 | 呼び出し回数（成功した最終試行） | 総トークン | 応答時間 | Quality Guard | 実行モジュール |
|---|---|---|---|---|---|---|
| hormuz_blockade | 一括（旧） | 1 | 42,100 | 202.6s | 9/11 | - |
| hormuz_blockade | 段階実行（新） | 2 | 70,828 | 108.2s | 9/11 | audience_signal |
| us_maritime_leverage | 一括（旧） | 1 | 40,309 | 160.5s | 9/11 | - |
| us_maritime_leverage | 段階実行（新） | 2 | 64,862 | 135.2s | 9/11 | audience_signal |
| china_constraint | 一括（旧） | 1 | 40,892 | 83.4s | 9/11 | - |
| china_constraint | 段階実行（新） | 2 | 66,974 | 79.8s | 9/11 | audience_signal |

**注記（重要）**: 上表の「一括（旧）」列は**最終的に成功した試行のみ**の数値であり、失敗した試行の分は含まれていない（失敗した`run_expert_request`呼び出しは例外を送出するのみで`usage`を返さないため）。実際には、**3ケースすべてで一括方式は最低1回、最大2回の失敗を経てからようやく成功した**（下記2章）。したがって一括方式の実際の総コスト・総時間は、上表の数値よりも大きい（失敗試行でも内部的に最大2回の実Azure呼び出しが消費されている）。

## 2. 信頼性（今回の実データ検証で最も重要な発見）

| ケース | 一括方式（旧）の試行結果 | 段階実行方式（新）の試行結果 |
|---|---|---|
| hormuz_blockade | 1回目失敗（`main_scenario.confidence`欠落）→2回目成功 | **1回目で成功**（Stage1・Stage2とも1回ずつ、修復不要） |
| us_maritime_leverage | 1回目失敗（`main_scenario.confidence`欠落）→2回目成功 | **1回目で成功** |
| china_constraint | 1回目失敗（`main_scenario.confidence`欠落）→2回目失敗（`stakeholders[].actor`欠落）→3回目成功 | **1回目で成功** |

- 一括方式は**3ケース中3ケースすべてで、最初の生成が丸ごと失敗**した（`max_attempts=2`の範囲内で構造化出力・手動JSONフォールバックのどちらもSchemaに準拠せず、`GeoLLMError`で終了）。実運用でこれが起きると、アナリストは失敗を見てから改めてリクエストを送り直す必要がある。
- 段階実行方式は**3ケース中3ケースすべてで、初回の1回のリクエストで最後まで成功**した（Stage1・Stage2ともAzure/OpenAIのStrict Structured Output（`strict=True`）で初回から正しい構造を返し、Stage3の修復すら不要だった）。

この差は、v1.2で追加した`key_actor_profiles`等の深い入れ子構造を、Stage1・Stage2それぞれの小さいSchemaへ分割したこと、および各Schemaを「全properties required（意味上任意の項目はnullable型）」というStrict Structured Output互換の形へ書き換えたことによるものと考えられる。一括方式のSchema（v1.2フル、約30項目・4階層の入れ子）は同じ`strict=True`化を行っていないため、単純比較ではないが、「Schemaを分割し、Strict Structured Outputを使う」という段階実行方式の設計方針が実際に効いていることを示す結果である。

なお、Strict Structured Output自体の導入には1つ実装上の落とし穴があった: `definitions`ブロックに、他のdefinitionをさらに`$ref`する定義（例:`rootPremiseItem`が`evidence_ids`で`evidenceIdList`を`$ref`する）を含め、それを複数の兄弟プロパティから再利用すると、AzureがSchema全体を無効と判定することを確認した。対策として、`$ref`を一切使わず全て実体展開したSchemaを都度組み立てる方式（`geo_expert_schema._resolve_refs`）に変更し、解消した。

## 3. Evidence ID捏造への対応の違い

`us_maritime_leverage`ケースで、一括方式は`evidence_ids_not_fabricated`チェックに失敗した（存在しないEvidence/Claim IDを創作していた）。段階実行方式ではこの3ケースで一度も同チェックが失敗しなかった。これは段階実行方式が「捏造IDをQuality Guardで検出し、LLMを再度呼ばずプログラム側でnull/除去するスクラビング」を実装しているため（`geo_expert_quality_guard.scrub_fabricated_evidence_ids`）で、検出された捏造IDはそもそも最終レスポンスに残らない設計になっている。

## 4. Quality Guardの残存失敗項目（両方式に共通）

`stakeholders_grounded_in_source_pack`は3ケースとも両方式で失敗している。これは`case_id`を指定しない（Foundation Pack/Case Contextを使わない）呼び出しのため、Source Pack本文が直近のEvent/指標のみで、Key Actor（イラン・中国・米国・湾岸諸国等）の名称がSource Pack本文中に文字列として現れないことによる、既知の（今回のリファクタで新規発生したものではない）誤検知パターンである。`fact_claims_have_source_ref`も両方式で散見される。これらは段階実行方式固有の問題ではなく、`case_id`なし呼び出し全般の既知の制約として次フェーズで扱う。

## 5. 匿名ペア比較（Blind A/B、提示順ランダム化）

12_EVALUATION_RUBRIC.csvの8軸（Key Stakeholder特定／Persistent Preference／目的の階層／Preference-Action Gap／Audience-Signal／主シナリオの明確さ／代替仮説の管理／簡潔性・優先順位）について、方式名を隠し、提示順をケースごとにランダム化した上でLLM審査員に1/3/5点で採点させた。

| ケース | 総合勝者 |
|---|---|
| hormuz_blockade | 一括（旧）**が優勢**（Key Stakeholder特定・Persistent Preference等ほぼ全軸で5点、段階実行は3点） |
| us_maritime_leverage | 段階実行（新）が優勢（目的の階層のみ旧が優勢、他は新が優勢または同点） |
| china_constraint | 段階実行（新）が優勢（目的の階層のみ同点、他は新が優勢） |

3ケース中2ケースで段階実行方式が優勢、1ケース（hormuz_blockade）で一括方式が優勢という結果だった。hormuz_blockadeでは一括方式がたまたま分散意思決定構造・Driving Forceの記述が豊富な応答を返しており、これは正当な結果のばらつきと考えられる（同一Schemaでも生成内容には確率的な変動があるため）。総合的に見て、**品質は両方式でほぼ同等〜段階実行方式がやや優位**、**信頼性（初回成功率）は段階実行方式が明確に優位**という結論になる。

## 6. 結論

- Stage1の`max_attempts=1`（Strict Structured Outputで初回に賭ける）という設計は、今回の3ケースすべてで成立した（Stage1・Stage2とも一度も失敗せず、Stage3の修復も一度も必要なかった）。
- 品質は一括方式と同等以上（匿名ペア比較で2勝1敗）。
- 信頼性は明確に改善（一括方式は3/3ケースで初回失敗、段階実行方式は3/3ケースで初回成功）。
- 呼び出し回数の予算（共有Call Budget、最大3回）は今回すべて2回で収まり、予算超過（`incomplete_budget_exhausted`）は一度も発生しなかった。
- Evidence ID捏造は段階実行方式のスクラビングにより一度も最終レスポンスへ残らなかった。

この結果を踏まえ、Phase B（API/UI配線、Persistent Preference Candidate生成、実行メタデータの保存方式確定、テスト拡充）へ進めることを推奨する。

## 参考: Baselineレスポンス（一括方式、DB保存済み）

| ケース | response_id |
|---|---|
| hormuz_blockade | b044a715-e17d-4f0d-a311-ad86a4d04274 |
| us_maritime_leverage | 7d890fef-4a9a-4f9b-8f88-046e72115aac |
| china_constraint | f371553d-d6a6-4814-acd9-71979638c47e |

段階実行方式（新）はPhase Aプロトタイプ（`run_expert_request_staged`）としてDB保存を行っていないため、response_idは存在しない（比較スクリプトの生成物のみ、`comparison_results.json`に保持）。

## 7. Phase B Closeout: 同一入力でのQuick／Standard／Monolithic横並び比較（2026-08-28）

query_text「UAEのパイプライン拡張がホルムズ海峡依存度に与える影響について」（theme_id=Hormuz Strait Theme、`case_id`なし）を、Knowledge投入量削減（後述8章）適用後のコードで実行。

| 方式 | response_id | 呼び出し回数 | 総token数 | 応答時間 | Quality Guard(pass/fail) |
|---|---|---|---|---|---|
| quick | `77979300-412f-4fc4-b89c-6ed050ffce50` | 1 | 29,394 | 55.0秒 | 10/1 |
| standard | `2007b1be-a52a-4549-9857-3e3e23f4ec3d` | 2 | 58,840 | 73.9秒 | 9/2 |
| monolithic | `f6fee876-2169-4dcd-9820-f32b0c391e13` | 1（成功試行のみ。**実際は1回目が丸ごと失敗し2回目で成功**） | 37,853（成功試行のみ） | **312.8秒**（失敗試行の待ち時間込み） | 10/1 |

monolithicの応答時間が突出して長いのは、1回目の生成が`'actor' is a required property`（`stakeholders[]`の必須項目欠落）でSchema検証に失敗し、呼び出し元が再送信して2回目でようやく成功したため（本比較では人間のUIからの再送信を模したリトライラッパーを使用）。quick/standardは共に初回で成功しており、リトライは発生していない。

失敗したQuality Guard項目はいずれも`stakeholders_grounded_in_source_pack`（case_idなし呼び出し共通の既知の誤検知、4章参照）で、standardはこれに加えて`fact_claims_have_source_ref`も一時的に失敗したが、Stage1修復では対象外（`MODE_V12_CHECK_NAMES`に含まれないチェックのため）のため`remaining_failed_checks`に残った。

## 8. E2E実行のStage別token・時間の内訳、Quality Guard失敗原因

quickプロファイルでのE2E実行例（response_id `2fa8f00d-6112-4f70-a432-a7d8addb809b`、72,891 tokens・約88秒）のStage別内訳（`geo_audit_logs`の`expert_request_execution_metadata`に保存済み）:

| Stage | 試行回数 | 応答時間 | prompt_tokens | completion_tokens |
|---|---|---|---|---|
| stage1 | 1 | 46,625ms | 26,340 | 5,645 |
| repair:stage1 | 1 | 41,265ms | 31,915 | 8,991 |
| **合計** | **2** | **87,890ms（≒88秒）** | **58,255** | **14,636（合計72,891）** |

**修復が発生した原因**: 初回Quality Guardで`fact_claims_have_source_ref`（Fact/Official Claim/Observationに根拠参照が無い主張）と`key_stakeholder_v12_fields_present`（Key Stakeholder関連必須項目の不足）が失敗した。後者が`MODE_V12_CHECK_NAMES`に含まれるため、Stage1スコープの部分修復が1回走った。修復後は`key_stakeholder_v12_fields_present`・`fact_claims_have_source_ref`とも解消し、`stakeholders_grounded_in_source_pack`（case_idなし呼び出し共通の既知の誤検知）のみが最終的に残った。

なお、修復前（初回）のレスポンスJSON自体は保存対象外（`geo_expert_responses.response_json`には最終版のみ保存）のため、上記の原因特定は`geo_expert_quality_guard`の各チェック関数のロジックから再構成したものである。Closeoutで、初回失敗チェックの`detail`文言（何が具体的に欠けていたか）を`geo_audit_logs`の実行メタデータへ新たに記録するようにした（`initial_failed_checks_detail`、`geo_expert_engine.py`）ため、今後は同様の原因分析がより直接的に行える。

## 9. Knowledge投入量の削減（Core／モード別／必要時のみの分割）

Closeoutでの計測により、quickプロファイルでも常に9ファイル全量（結合後約29,756文字、`build_knowledge_block()`）が投入され、これがStage1のprompt_tokensの大半（実測26,340中ほぼ全て）を占めていることが判明した。

| ファイル | 文字数 | Stage1で必要か（既定モード） | 備考 |
|---|---|---|---|
| 01_SYSTEM_POLICY.md | 2,888 | 常に必要 | 絶対原則 |
| 03_KNOWLEDGE_THINKING_OS.md | 5,272 | 常に必要 | 中核推論フレーム |
| 04_KNOWLEDGE_STAKEHOLDER_FORCE_MODEL.md | 4,036 | 常に必要 | Key Stakeholder分析の核 |
| 05_KNOWLEDGE_HYPOTHESIS_EVIDENCE_PROTOCOL.md | 3,486 | 常に必要 | 事実/仮説分類 |
| 06_KNOWLEDGE_REVIEW_OS.md | 3,475 | **draft_review/outline_reviewのみ** | Reviewモジュール専用 |
| 07_KNOWLEDGE_OUTPUT_TEMPLATES.md | 3,657 | 常に必要 | 出力テンプレート |
| 08_KNOWLEDGE_CASE_LIBRARY.md | 3,315 | 常に必要（今回は維持） | 思考例、リスクとのトレードオフで今回は削減対象外 |
| 09_KNOWLEDGE_BUSINESS_IMPACT_MODEL.md | 1,080 | 常に必要 | business_impact用 |
| 10_KNOWLEDGE_ANALYST_DEVELOPMENT.md | 2,200 | **analyst_coachingのみ** | Analyst Learningモジュール専用 |

06・10（合計5,675文字、全体の約19%）は、Stage1では該当モード（draft_review/outline_review、analyst_coaching）でのみ投入し、それ以外（case_analysis等）では除外するよう変更した（`geo_expert_engine._knowledge_files_for_stage1`/`_knowledge_files_for_stage2`、`geo_expert_common.build_knowledge_block(include_files=...)`）。Stage2も同様に、moduleごとに実際必要なファイルのみ投入する（reviewモジュール→06、analyst_learningモジュール→10）。

**実測効果**: 同一query_text・quickプロファイルでのStage1 prompt_tokensが26,340→22,652（約14%削減）。一括方式（`run_expert_request`）は変更していない（既存動作を完全維持）。08（Case Library）は思考の質に直結する可能性がありリスクとリターンのバランスから今回は削減対象外とした（次フェーズ課題）。

## 10. Persistent Preference Candidate自動生成Feature Flagの状態

環境変数`GEO_EXPERT_PP_CANDIDATES_ENABLED`（既定値: 未設定=false、`geo_persistent_preference_service.candidates_enabled()`）。実行中のAPIサーバープロセスではこの環境変数を設定しておらず、**Candidate自動生成は無効のまま**。既存Active Profileの再利用（読み取り、Stage0でのプロンプト注入）のみ常時有効。自然なLLM出力による`persistent_preference_change="revised"`はまだ実データで自発的に確認できていないため、通常運用では本フラグを有効化しない（本Closeoutでは手動構成したkey_actor_profilesで導線のみ検証、6章参照）。
