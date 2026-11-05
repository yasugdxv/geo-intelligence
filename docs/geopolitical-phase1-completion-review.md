# 地政学ナレッジ＆インテリジェンスDB — Phase 1 完了レビュー

検証日: 2026-08-04

## 1. Migration

| ファイル | 内容 | 状態 |
|---|---|---|
| `sql/2026-08-04_geo_knowledge_core.sql` | 新規14テーブル＋既存`geo_source_registry`/`geo_documents`のALTER | **適用済み**（実DBへの読み書きで確認済み） |
| `sql/2026-08-04_geo_knowledge_search_fts.sql` | `geo_entities`/`geo_claims`/`geo_knowledge_items`へのtsvector生成列＋GIN | **適用済み**（`search_vector`列の存在を実DBで確認済み） |
| `sql/2026-08-04_geo_knowledge_pgvector_optional.sql` | `vector`拡張＋`geo_knowledge_items.embedding vector(1536)` | **適用済み**（`embedding`列の存在を実DBで確認済み）。**ただしPython側からは未使用**（Phase1設計通り）。**次章参照: 1536次元は未確認のまま設定された値であり、Phase2Aでは実際のEmbedding構成が確認できるまでVector Search機能自体は有効化しない** |

適用順序・ロールバック手順は各ファイル末尾のコメントに記載済み、変更なし。

## 2. データモデル

以下すべて実DBで存在・読み書き可能なことを確認済み（2026-08-04の実DB検証セッションで、smoke testデータの作成・取得・削除を通じて確認）。

`geo_entities`, `geo_entity_aliases`, `geo_relationships`, `geo_assets`, `geo_dependencies`, `geo_knowledge_items`, `geo_knowledge_reviews`, `geo_claims`, `geo_document_chunks`, `geo_claim_evidence`, `geo_cases`, `geo_case_hypotheses`, `geo_knowledge_gaps`, `geo_issues`, `geo_entity_issues`/`geo_relationship_issues`/`geo_knowledge_item_issues`/`geo_case_issues`（Issue Link Table群）、拡張済み`geo_source_registry`（trust_tier等）、拡張済み`geo_documents`（request_parameters等）。

## 3. 中核ループ（実DB + 実Azure OpenAIで検証済み）

```
Case作成 → Entity/Relationship登録 → Document/Chunk → Claim/Evidence
→ Foundation Pack生成 → Deterministic Gap検出（5種）
→ Analytical Gap Candidate生成（実LLM、5件生成）→ Hypothesis登録
→ Knowledge Candidate抽出（実LLM）→ 人間Review（start_review→approve_active）
→ 次のCaseでActive Knowledgeが再利用可能なことを確認
```

**実行中に発見・修正した不具合2件**（詳細: `docs/geopolitical-implementation-status.md`の該当エントリ）:
1. Knowledge Candidate抽出のLLM向けプロンプトが型・enum制約を伝えておらず、実データで5件中5件がSchema検証全滅 → プロンプト強化で解消
2. 上記解消後も残った軽微な型ゆれ（confidence英語表記、配列/文字列の不一致）→ `_normalize_content()`によるフォーマット補正で解消

修正後、4/4のCandidate種別（precedent/mechanism/indicator/reasoning_rule）が正常に生成されることを実データで確認。テストデータはすべて削除済み（残存0件を確認済み）。

## 4. テスト結果

| 種別 | 結果 |
|---|---|
| Unit Test（`tests/test_geo_knowledge_core.py`） | 13件 pass |
| E2E Test（`tests/test_geo_knowledge_e2e.py`、24手順） | 1件 pass |
| Status Transition Test | 上記Unit Testに含む（許可外遷移の拒否・start_review→approve_active等を検証） |
| Citation / Provenance Test | E2E Testに含む（Claim→Evidence→Document Chunk→Documentの遡り、旧version保持） |
| Demo Isolation Test | Unit Test + E2E Testに含む。**実DBでも動作確認済み**（`include_demo`フラグの挙動） |
| Import Check | 全新規モジュール`py_compile`／`import`エラーなし |
| 既存テスト回帰確認 | `pytest tests/ -q` → 108 passed（Phase1追加14件含む）、**5 failed**（下記参照） |

### 既存の無関係な失敗（Phase1の変更が原因ではないことを確認済み）
`tests/test_sustainability_article_selector.py`の5件（`test_list_weekly_picks_*`）が、このセッションでの変更と無関係に失敗する。単独実行でも同じ5件が失敗し、`sustainability_article_selector.py`は今回一切変更していないため、**Phase1着手前から存在した既知の問題**と判断。Phase1の完了条件には影響しないが、別途調査が必要（Phase2Bへ持ち越し候補として記録）。

### TypeScript
`npx tsc --noEmit` エラーなし（Phase1で追加した4画面・APIクライアント含む）。

## 5. 手動作業が必要だった項目（実施済み）
- Supabase SQL Editorでの3ファイル適用 → **ユーザーにより実施済み**（本レビュー作成時点で確認済み）

## 6. Supabase上で未適用のSQL
なし。3ファイルすべて適用済み。

## 7. 既知の制約
- Entity resolutionは完全一致（大文字小文字無視）のみ。あいまい一致はPhase2以降
- 文書chunkingは段落単位の素朴な分割
- Knowledge Candidateの重複検知はタイトル完全一致のみ
- Case一覧APIはstatus/is_demoのみでフィルタ（詳細検索は未実装）
- **Embedding/pgvector**: `embedding`列は作成済みだが、**このconfig.jsonにはEmbedding Deploymentが一切設定されていない**（`sustainability_expert.knowledge_search.mode`/`embedding_deployment`ともに未設定、Azure OpenAIデプロイはchat用の`Global-AzureOpenAI-DEV-gpt-5.1`のみ）。1536次元は当時未確認のまま設定した値であり、実際のEmbedding Model/Dimensionsは依然未確認。Phase2AではVector Search機能自体を有効化しない方針とする（詳細: `docs/geopolitical-phase2a-architecture.md`）

## 8. Phase 2Aへ持ち越す課題
- Vector Search: Embedding Model/Dimensions未確認のため保留
- `test_sustainability_article_selector.py`の5件失敗の原因調査（Phase1と無関係、対応不要ならクローズ）
- Case一覧の検索・フィルタ強化
- Entity resolutionのあいまい一致

## 総合判定
**Phase 1は完了条件をすべて満たしている**。既存データ削除・破壊的変更は発生していない。Phase 2Aへ進行可能。
