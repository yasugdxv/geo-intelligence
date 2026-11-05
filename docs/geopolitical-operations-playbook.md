# 地政学ナレッジ＆インテリジェンスDB — 運用プレイブック（Phase 1、Phase 2A/2B-2で更新）

## 1. 案件（Case）のライフサイクル

### 1.1 案件開始
1. アナリストが分析テーマ（`geo_cases`）を登録
2. 対象地域・期間・主体を設定（`geo_case_issues`, `geo_entity_issues`経由）
3. `geo_foundation_pack_service`が既存Foundation Knowledge / Reusable Intelligence Knowledge（active状態のみ）を検索
4. Foundation Packを生成
5. `geo_knowledge_gap_service`がDeterministic Gapを機械的に検出
6. LLMがAnalytical Gap Candidateを提案（Evidence根拠必須、推測補完しない）
7. 推奨情報源・調査クエリを提示
8. アナリストが調査を開始（Source Document登録、Source Registry手動登録）

### 1.2 案件分析中
1. 新しいSource Document登録（`geo_documents`→`geo_document_chunks`）
2. LLMがClaim候補を抽出（`geo_claims`、必ず`origin_type`/`origin_id`を持つ）
3. Entity resolution（新規主体の場合はunresolved queueへ）
4. Supporting/contradicting evidenceを`geo_claim_evidence`経由でHypothesisへ紐付け
5. 主仮説（main）と代替仮説（alternative）を`geo_case_hypotheses`で更新
6. 変更履歴は`geo_knowledge_reviews`（Knowledge Item側）／`geo_audit_logs`（操作全般）で保存

### 1.3 案件終了時
1. 最終分析（`geo_cases.final_output`）を保存
2. `geo_candidate_extraction_service`がFoundation Update / Precedent / Mechanism / Indicator / Reasoning Rule候補を`geo_knowledge_items`（status=candidate）として生成
3. Reviewer（expert_reviewer/knowledge_steward）がCandidate Queueでレビュー
4. 承認した知識をactive化（`geo_knowledge_reviews`に記録、状態遷移はコードが検証）
5. 次回のFoundation Pack生成時から検索対象になる

### 1.4 事後評価
実際の結果が判明した場合、`geo_cases.actual_outcome`/`post_case_evaluation`に以下を保存する（過去の仮説は削除せず、当時の情報条件とともに残す）:
which_hypothesis_was_supported, which_assumptions_failed, missed_indicators, misleading_indicators, source_gaps, reasoning_errors, recommended_knowledge_updates, calibration_assessment

## 2. 更新頻度（Phase 1は手動運用が前提。Scheduler未実装）

| タイミング | 内容 | Phase 1での実施方法 |
|---|---|---|
| 案件開始時 | Foundation Pack生成 | API呼び出し（同期） |
| 案件中 | 随時更新 | 手動 |
| 案件終了時 | Knowledge Candidate生成 | API呼び出し（同期） |
| 週次 | Candidate Review | 手動（Reviewerが画面で確認） |
| 月次 | 重複統合、conflict確認 | 手動 |
| 四半期 | Country/Actor/Party/Relationshipの見直し | 手動 |
| 年次 | taxonomy・source・全体構造の棚卸し | 手動 |
| 政権交代時・選挙時・制裁更新時 | 該当Knowledge Itemの即時レビュー | 手動トリガー（Phase 2でイベント駆動化を検討） |

Phase 1ではSource Registryの`update_cadence`/`expected_release_cycle`列に**将来の運用頻度を記録するのみ**で、実行はしなかった。**Phase 2Aでは World Bank（quarterly推奨）/ OFAC（daily推奨）の2ソースについて手動トリガー（API/CLI）＋外部スケジューラ（Windows Task Scheduler/cron等）からの定期実行に対応した**（常駐Schedulerは持たない）。詳細・実行例: `docs/geopolitical-ingestion-operations.md`。Source単位の鮮度判定（overdue等）は`docs/geopolitical-freshness-policy.md`。

## 3. Role（開発専用、Production Securityではない）

> **Development-only role control. This role value is not authenticated and must not be used in production.**

| Role | Phase 1での権限 |
|---|---|
| viewer | active knowledgeの閲覧、Foundation Pack閲覧 |
| analyst | case作成、source登録、hypothesis作成、candidate生成トリガー |
| expert_reviewer | candidate review、content修正提案、active/contested/rejected判断 |
| knowledge_steward | entity merge/split確定、duplicate統合、source管理 |
| administrator | system configuration |

APIは各エンドポイントで`actor_role`が許可リストに含まれるかのみを検証する（パスワード・セッションなし）。Review操作は必ず`geo_audit_logs`へ記録する。本格的なEntra ID連携等はPhase 2以降で対応する。

## 4. Demo Fixtureの運用

- Russia-Ukraine-EU-NATO / Iran-Israel-Gulf-Hormuzの初期データは`is_demo=true`
- 検索・Foundation Pack・Candidate抽出・AI回答生成では既定で除外（`include_demo=false`）
- Demo Fixtureを本番ナレッジへ昇格させたい場合は、`is_demo`を`false`に変更する明示的なsteward操作を必要とする（自動昇格はしない）

## 5. 品質保証チェックリスト（Reviewerが承認前に確認する項目）

- source adequacy（出典が十分か）
- applicability（現在の案件・状況に適用可能か）
- conflicting evidenceの有無と扱い
- information_classが適切に区別されているか
- 主張の強さが根拠の強さと一致しているか

## 6. Phase 2Bへ先送りする運用項目

Network Graph表示を使った関係性レビュー、Indicator Monitorのアラート運用、pgvector導入後のセマンティック検索運用（Embedding Deployment確認後）、World Bank/OFAC以外のConnector（IMF/WTO/UN Comtrade/Manifesto Project/Babel Street等）、3 Theaterへの本格データ投入と定期メンテナンス、Entra ID認証移行後のアクセス権限運用、鮮度バッジの一覧画面表示。

## 7. Phase 2B-2で追加した運用（External Reference Model / Internal Expert Learning Layer）

詳細: `docs/geopolitical-external-reference-model.md`、`docs/geopolitical-internal-expert-learning.md`、`docs/geopolitical-expert-review-workflow.md`（Reviewer向け操作手順）。

### 7.1 Source Reference Policyの運用（knowledge_stewardの新しい責務）

新規Source登録時、`reference_availability`は既定`restricted`のまま作成される。そのSource由来のClaimをHuman Reviewなしで自動的にAIへ渡してよいと判断できる場合のみ、knowledge_stewardが`PATCH /api/geo/knowledge/sources/{source_id}/reference-availability`で`available`に切り替える。**これはSourceの信頼性・利用条件についての運用判断であり、個々のClaimの内容確認ではない。**

### 7.2 Expert Reviewの運用（expert_reviewerの新しい責務）

Case終了後（またはレポート提出後）、AI DraftとExpert Finalが揃っている場合はExpert Reviewセクションから振り返りを行うことを推奨する（必須ではない）。手順は`docs/geopolitical-expert-review-workflow.md`参照。既存の「5. 品質保証チェックリスト」（承認前の確認項目）とは独立した、承認後の振り返り作業である。

### 7.3 Retrieval Governance（`retrieval_status`個別変更）の位置づけ

`geo_knowledge_reviews`を使う既存のReview操作（4章のRoleが行う`status`のcandidate→active等の遷移）とは別に、`retrieval_status`（available/restricted/deprecated/archived）を個別に変更する操作がある（`docs/geopolitical-internal-expert-learning.md`5章のAPI）。これは既存の品質保証チェックリストの対象外（内容の正誤判定ではない）であり、Audit Logのみに記録され`geo_knowledge_reviews`には残らない。
