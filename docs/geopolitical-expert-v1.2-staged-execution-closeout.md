# 地政学専門家AI v1.2 段階実行(Staged Execution) Closeout

本ドキュメントは、v1.2段階実行化（Phase A/B）の完了時点の状態を記録する最終ドキュメントである。
経緯の詳細は以下を参照:

- `docs/geopolitical-staged-execution-pilot-results.md` — Phase A/B実データ比較結果（信頼性・token・時間・Knowledge削減効果）
- `docs/geopolitical-expert-strict-schema-contract-results.md` — Strict Schema Contract Check結果

## 1. 現行既定値・Feature Flag

| 項目 | 環境変数 | 既定値 | 現在の稼働状態 |
|---|---|---|---|
| 実行方式（一括／段階実行） | `GEO_EXPERT_EXECUTION_MODE` | 未設定時 `staged` | `staged`（未設定のまま運用） |
| Persistent Preference Candidate自動生成 | `GEO_EXPERT_PP_CANDIDATES_ENABLED` | 未設定時 `false` | `false`（未設定のまま運用、**Candidate自動生成は無効**） |

- 実行方式は`geo_expert_engine.resolve_execution_mode()`が判定し、API層（`api_server.py`/`geo_knowledge_api.py`）のリクエストパラメータ`execution_mode`で個別リクエストごとに上書きすることも可能（既定は環境変数に従う）。
- Persistent Preferenceの**既存Active Profileの再利用（読み取り、Stage0でのプロンプト注入）は常時有効**。Candidate自動生成のみがフラグで無効化されている。

## 2. ロールバック方法

問題発生時は、環境変数`GEO_EXPERT_EXECUTION_MODE=monolithic`を設定してAPIサーバーを再起動するだけで、コード変更・デプロイ不要で既存の一括方式（`geo_expert_engine.run_expert_request`）へ即座に戻せる。**一括方式は削除しておらず、段階実行方式と並存する形で維持している**（当面のロールバック用）。

Persistent Preference Candidate自動生成を万一有効化した場合も、`GEO_EXPERT_PP_CANDIDATES_ENABLED`を未設定または`false`に戻すだけで即座に無効化できる（コード変更不要）。

## 3. 既知の残存課題（次フェーズのチューニング課題）

今回のスコープでは追加実装を行わず、以下を次フェーズの検討課題として記録する。

1. **Quick／Standardに残るQuality Guard失敗項目の重要度確認**: 現状、`case_id`なし呼び出しで`stakeholders_grounded_in_source_pack`・`fact_claims_have_source_ref`が両プロファイルで残存することを確認済みだが、これらの内容・重要度（Critical相当かどうか）を精査できていない。**Criticalに相当する失敗がある場合は、`completeness_status`を`"complete"`にしない**よう、`run_expert_request_staged`の判定ロジックを見直す必要がある（現状は「モード別v1.2必須チェックが全て解消したか」のみで判定しており、これら2チェックのような非v1.2チェックの重要度は加味していない）。
2. **08_KNOWLEDGE_CASE_LIBRARYの常時投入要否**: 今回のKnowledge削減（06/10のモード別投入化）では、Case Library（3,315文字）は思考品質への影響リスクを踏まえ削減対象外とした。常時投入する必要性についてA/B検証を行い、必要なケース（few-shot的に有効な場面）のみ取得する方式（例: モード別、または関連度に応じた動的取得）を検討する。
3. **Persistent Preference Candidate自動生成の有効化判断**: 自然なLLM出力で`persistent_preference_change="revised"`が実際に生成される実ケースをまだ確認できていない（今回のCloseoutでは手動構成したkey_actor_profilesで導線のみ検証）。そうした実ケースが得られ、Candidate生成条件（Fingerprint重複防止・実質的変更判定・未処理Candidate重複防止）が意図通り機能することを確認してから、`GEO_EXPERT_PP_CANDIDATES_ENABLED`の有効化可否を判断する。
4. **Strict Schema Contract Checkの定期実行方式**: `scripts/geo_expert_strict_schema_contract_check.py`は現状手動実行のみ（実APIコール・トークン消費のためCI組み込みを見送っている）。定期実行（例: Schema変更時のみ／週次等）またはCIでの任意実行（オプトインジョブ等）の方式を検討する。
5. **対話的weekly_article経路のプロファイル**: 週次バックグラウンドの自動per-item補強（`_weekly_maybe_enrich_with_expert_ai`）は実データ確認済みで`quick`へ切替済みだが、`_call_expert_ai`経由の対話的weekly_article経路（`mode=executive_brief`）は`standard`のまま維持している。こちらも`quick`へ変更できるか、品質比較（対話的なぶん一発の応答品質がより問われるため、週次バックグラウンドより慎重な比較が必要）を行った上で判断する。
6. **Review modeでのRoot Premise Check／Review Findings実データ確認**: 今回のUI確認は`case_analysis`モードで実施し、Key Stakeholder／Key Actor Profile／主シナリオ／不足情報／Quality Guardの表示は確認済みだが、`draft_review`/`outline_review`モード（Reviewモジュール）でのRoot Premise Check（5項目のpass/warning/fail/not_assessable）およびCritical/Major/Minor別のReview Findings表示は、実データでの確認がまだ済んでいない。次回のUI回帰確認に追加する。

## 4. まとめ

- v1.2段階実行化（Phase A/B）は今回のスコープで完了。
- 既定は段階実行方式（`staged`）、Persistent Preference Candidate自動生成は無効（`false`）のまま運用する。
- 一括方式はロールバック用として当面維持する。
- 上記6項目は追加実装を行わず、次フェーズの検討課題として本ドキュメントに記録した。
