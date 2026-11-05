# 地政学ナレッジ＆インテリジェンスDB — アーキテクチャ方針

対象: `geopolitical_monitor_dashboard`
関連: `docs/architecture.md`（既存の地政学インテリジェンス基盤＝指標・専門家AI Phase2の方針。本ドキュメントはその上に「ナレッジ蓄積・再利用」を追加する）

## 1. 目的

既存の地政学専門家AI（`geo_expert_engine.py`）は、案件（質問）ごとに`geo_events`/`geo_evidence`/`geo_documents`/`geo_observations`から一時的なSource Packを作るだけで、**分析で得た知見を蓄積して次の案件に再利用する仕組みを持たない**。本機能は、以下を一体で実現する永続的なナレッジ基盤を追加する。

1. 地政学テーマ分析の基礎情報を保持する
2. 国・組織・政党・企業・軍・国際機関等の関係性を保持する
3. 各主体の目的・アセット・依存・弱み・カード・制約を構造化する
4. 個別案件の分析で得られた前例・力学・判断ルールを再利用可能な知識として蓄積する
5. 蓄積した知識を次の案件分析で自動的に参照する
6. AIが生成した知識を、人間がレビューして承認・更新・失効できるようにする
7. すべての情報を、出典・取得日・適用時点・確信度・情報区分とともに管理する

中心構造は **Actor × Relationship × Issue/Theater × Time**。単なる国別プロフィールDBではなく、「誰が意思決定するか」「誰が止められるか」「何を守ろうとしているか」「誰が誰に依存しているか」「使えるカードは何か」「前例は何か」に回答できる構造を持つ。

## 2. Phase 1のスコープ（最重要）

仕様は非常に広範（4階層のナレッジ、5コネクタ、20画面相当、完全RBAC等）だが、**Phase 1は「ナレッジ蓄積・レビュー・再利用の中核ループを実際に動かすこと」だけに集中する**。

```
Case作成 → 既存ナレッジ検索 → Foundation Pack生成 → Knowledge Gap特定
→ 追加Evidence登録 → Case分析 → Knowledge Candidate抽出
→ 人間によるレビュー → Active Knowledge化 → 次のCaseで再利用
```

情報源の自動収集（Connector）・画面数の充実・Network Graph表示・Embedding/pgvector検索はPhase 2以降に明示的に先送りする（4章参照）。これはCLAUDE.mdの「大量変更禁止・過度な複雑化を避ける」という運用ルールと、「中核ループが完成する前に工数が分散する」というユーザーからの明示的なレビュー指摘を反映したもの。

## 3. ナレッジの4階層

| 階層 | 内容 | Phase 1での扱い |
|---|---|---|
| Foundation Knowledge | 政治制度、統治構造、主要政党、貿易・エネルギー依存、軍事力、歴史的対立等の比較的安定した基礎情報 | `geo_knowledge_items`（knowledge_type=country_foundation等）として保持。自動収集はせず手動登録・LLM抽出候補＋人間承認 |
| Reusable Intelligence Knowledge | Precedent Card / Mechanism Card / Reasoning Rule / Indicator Card / Foundation Update | `geo_knowledge_items`のknowledge_type値として実装（専用テーブルは作らない。7章参照） |
| Case Intelligence | 個別案件の仮説・シナリオ・結論 | `geo_cases` / `geo_case_hypotheses` |
| Live Signals | 政府発言・制裁・軍事行動等、随時変化する情報 | 既存`geo_events`／`geo_documents`をそのまま利用。Foundation Knowledgeへ混ぜない（Claim経由でのみFoundationの根拠になり得る） |

## 4. 情報区分（information_class）

`geo_claims`および`geo_knowledge_items`に必須。

- `observed_fact` — 公式統計、選挙結果、制裁発動、条約締結等
- `actor_claim` — 政府・政党・軍・企業の発言（事実性は保証しない）
- `external_assessment` — 研究機関・専門家・報道機関の解釈
- `internal_hypothesis` — 専門家AI・担当者が構築した仮説（**承認済みの基礎事実として扱わない**）

## 5. Actor×Relationship×Issue/Theaterのデータ構造（詳細は`docs/geopolitical-data-model.md`）

- Entity（`geo_entities`/`geo_entity_aliases`）を中心に、Relationship（`geo_relationships`）・Asset（`geo_assets`）・Dependency（`geo_dependencies`）がぶら下がる
- Leverage CardはPhase 1では専用テーブル化せず`geo_knowledge_items`のknowledge_type=leverage_cardとして保持し、構造が実運用で安定した後にPhase 2で専用テーブル化を検討する
- Issue/Theaterは既存`geo_themes`をTheater（例: US-China-Taiwan-Japan）として継続利用し、新規`geo_issues`をTheater配下の個別論点（例: Sanctions）として追加。Entity/Relationship/KnowledgeItem/Caseとの紐付けは、汎用junctionではなく個別Link Table（`geo_entity_issues`等）で行う（PostgREST/RLS/Cascadeの安定性を優先。詳細は6章）

## 6. Retrieval Layer（既存Source Packと新Foundation Packの接続点）

新規`geo_retrieval_service.py`に以下のinterfaceを定義する。

- `get_live_source_pack(theme_id, query_text)` — 既存`geo_expert_engine.build_source_pack`相当の一時的コンテキスト取得
- `get_foundation_knowledge(entity_ids, issue_ids)` — active化された`geo_knowledge_items`（country_foundation等）
- `get_reusable_knowledge(issue_ids, knowledge_types)` — precedent/mechanism/reasoning_rule/indicator
- `get_case_context(case_id)` — 案件のhypothesis/gap/claim一式
- `get_claims_and_evidence(entity_ids or case_id)` — claim+evidence

**Phase 1**: `geo_foundation_pack_service.py`（Case Workspace向け）がこのRetrieval Layerを使用する。既存`geo_expert_engine.build_source_pack`は**変更しない**（現在動いている軽量Q&Aフローを壊さないため）。
**Phase 2**: `geo_expert_engine`を同じRetrieval Layerへ移行し、単発質問でも蓄積済みナレッジ（Precedent等）を参照できるようにする。

## 7. Event / Claim / Evidence の役割分担

- **geo_events**（既存、変更なし）: 日時・場所・主体を持つ観測イベント（政府声明、制裁発動、船舶通航量低下、軍事行動等）。indicator engineの計算対象。
- **geo_claims**（新規）: イベント・文書・構造情報・分析から抽出した個別の主張（例:「政府は航行保護費用を利用国に負担させる意向を示した」）。`origin_type`（event/observation/document/knowledge_item/case/manual）＋`origin_id`で発生源を必ず1つ持つ。
- **geo_evidence**（既存、変更なし）: indicator engineとEvent検証専用。
- **geo_document_chunks**（新規）: 文書のページ・節・引用箇所を保持する分割単位。
- **geo_claim_evidence**（新規）: Claimを`geo_document_chunks`/`geo_events`/`geo_observations`のいずれか1つへ接続する根拠関係（evidence_role: supporting/contradicting/contextual/qualification）。
- **登録ルール**: 同一の一次情報から「観測」が取れればgeo_events、そこから「解釈を伴う主張」を派生させる場合はgeo_claims（origin_type=event）に分ける。同一内容をgeo_eventsとgeo_claimsへ二重に生登録しない。

## 8. Knowledge Statusと承認ガバナンス

状態: `candidate → in_review → active / contested / rejected`、`active → superseded / deprecated / contested`、`rejected → archived`。これ以外への直接遷移はコードが拒否する。

LLMは Entity Merge／Fact確定／Knowledge Active化／Conflicting Evidence削除／Contested Knowledge解消／Legal Conclusion確定を**提案するだけで確定しない**。これらは**権限を持つ人間の明示的なレビュー操作を経て初めて確定する**。コードの役割は「権限があるか」「許可された状態遷移か」「必須項目が揃っているか」を検証し、条件を満たした場合に人間が下した決定をDBへ反映することであり、**コード自身が内容の正しさを自動判断してActive化するわけではない**。

## 9. Knowledge Gapの2種類

- **Deterministic Gap**（コードが機械的に判定）: missing_entity, missing_actor_profile, missing_relationship, missing_dependency, missing_historical_context, missing_precedent, missing_legal_basis, missing_quantitative_data, stale_knowledge, conflicting_evidence, insufficient_source_diversity, missing_indicator
- **Analytical Gap Candidate**（LLMがEvidenceに基づき提案。推測で補完しない）: applicability_gap, precedent_comparison_gap, causal_gap, legal_institutional_gap, actor_intent_gap, countermeasure_gap, enforcement_gap, alternative_explanation_gap, falsification_gap

## 10. Demo Fixtureの分離

Russia-Ukraine-EU-NATO / Iran-Israel-Gulf-Hormuzの2 Theaterは外部アクセスも既存データも無いため、**空の初期テンプレート＋`is_demo=true`を付けた少量のfixtureのみ**を用意する（事実の捏造はしない）。全検索APIは`include_demo=false`を既定とし、Foundation Packも明示指定時のみdemoを含める。AI回答生成ではdemoデータを使わない。

## 11. Role制御（開発専用、Production Securityではない）

現状アプリ全体に認証機構が無いため、Phase 1では本格RBAC・ログイン基盤を導入しない。リクエストの`actor_id`/`actor_role`を軽量チェックするのみで、API・UIに以下を明示する。

> Development-only role control. This role value is not authenticated and must not be used in production.

Review操作は必ずAudit Log（既存`geo_audit_logs`）に記録する。本格的なEntra ID連携等は`docs/geopolitical-operations-playbook.md`に「Phase 2以降」として明記する。

## 12. 検索

Phase 1はSQL filter + PostgreSQL Full Text Search（tsvector/GIN）+ entity/relationship/issue/status/temporal filterのみ。pgvector・Embeddingは`sql/2026-08-04_geo_knowledge_pgvector_optional.sql`でテーブル・拡張の**準備のみ**行い、Python側からは使用しない（Phase 2で使用開始）。

## 13. Phase 2以降に明示的に先送りする項目

World Bank/OFAC/IMF/WTO/UN Comtrade/Manifesto Project/Babel Street/Generic Authorized HTTPの各Connector、定期Scheduler・自動更新、Network Graph表示、Indicator Monitor、本格的なMetric時系列管理、pgvector検索・Embedding生成、3 Theaterへの本格データ投入、本格RBAC・Entra ID連携、既存`geo_expert_engine`のRetrieval Layer移行。Phase 1ではInterface・設定項目・disabled状態のみ用意し、実装はしない。

## 14. 既存資産の再利用（変更しないもの）

- `SupabaseClient`（`article_crawler.py`） — そのままDBアクセス層として使用
- `geo_documents_store.py` — Document Upload・content_hash重複排除をそのまま拡張利用（chunking機能を追加するのみ）
- `geo_llm_common.call_llm_structured` — LLM構造化出力呼び出しをそのまま使用
- `geo_audit.py` / `geo_audit_logs` — 監査ログをそのまま継続利用
- `geo_expert_engine.py` / `geo_expert_schema.py` / `geo_expert_quality_guard.py` — **変更しない**（現在動いている軽量Q&Aフロー）
- `tests/_fakes.py`のFakeSupabaseClientパターン — 新サービス層のテストに継続利用
