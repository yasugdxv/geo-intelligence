# 地政学ナレッジ＆インテリジェンスDB — 検索・Retrieval設計（Phase 2A）

## 1. なぜVector Searchを見送るか

`config.json`に`sustainability_expert.knowledge_search.mode`/`embedding_deployment`が設定されておらず、Azure OpenAIデプロイはchat用の`Global-AzureOpenAI-DEV-gpt-5.1`のみ（Phase1完了レビューで確認済み）。Embedding Model名・次元数ともに未確認であり、ユーザーの明示指示（推測で1536次元を採用しない）に従い、**Phase2AではVector Searchを実装しない**。

- `geo_knowledge_items.embedding vector(1536)`列（Phase1作成）はそのまま維持するが、書き込み・読み込みとも一切行わない
- `geo_retrieval_results.vector_score`列は用意してあるが、**常にNULL**（`geo_search_service.py`の`_record_result`が固定でNoneを設定）
- Embedding Deploymentが将来確認された時点でPhase2B以降として実装する。次元数はその時点のモデル仕様に合わせて再設計する（今回作成した1536列をそのまま使えるとは限らない）

## 2. Hybrid Retrievalのスコア合成方式

DB側で全件取得してPython側でランキングするのではなく、PostgreSQL RPC関数（`sql/2026-08-05_geo_phase2a_retrieval_trace.sql`）で`ts_rank_cd`を使いTop-Kを計算させる。`plainto_tsquery('simple', ...)`のみでは日本語の複合語・表記ゆれに弱いため、**Entity・Knowledge Item（title/summary）・Claim（claim_text）のいずれも同じ3方式を合成する**（`geo_search_service.py`）。

| 方式 | 実装 | スコア |
|---|---|---|
| (1) 完全一致 | Entity: `geo_entity_resolution.find_entity_matches`（canonical_name/alias一致）。Knowledge Item/Claim: 正規化後の完全一致 | `EXACT_MATCH_SCORE=1.0` / `ALIAS_MATCH_SCORE=0.85` |
| (2) 正規化文字列の部分一致 | 前後空白除去・小文字化のうえ`ilike '%query%'`（`_partial_match_candidates`） | `PARTIAL_MATCH_SCORE=0.5` |
| (3) FTS | `client.rpc("search_geo_knowledge_items"/"search_geo_claims"/"search_geo_entities", ...)` → `ts_rank_cd` | `text_score`（0〜） |

`final_score`の合成式（`geo_search_service.py`実装値）:

- Entity: `entity_score + text_score * 0.3`
- Knowledge Item: `match_score + text_score * 0.3 + approval_score * 0.2 + recency_score * 0.1`（`approval_score`はstatus=active時1.0、それ以外0.5。`confidence`は高=1.0/中=0.6/低=0.3。Precedent/Historical Path Dependencyは`recency_score`を1.0固定にし古さで減点しない）
- Claim: `match_score + text_score * 0.5`

entity_score/source_score/recency_score/approval_scoreはRPCが返したTop-K結果に対してのみPython側で軽量に計算する（全件には適用しない）。

## 3. Retrieval Trace（`geo_retrieval_runs`/`geo_retrieval_results`）

`search_entities`/`search_knowledge_items`/`search_claims`は既定で（`log_trace=True`）実行のたびに`geo_retrieval_runs`へ1行、各結果に`geo_retrieval_results`へ1行を記録する。`retrieval_mode`は`hybrid_fts`（検索画面から）または`expert_context`（`geo_expert_engine.build_case_context`から、詳細: `docs/geopolitical-expert-engine-integration.md`）。`exclusion_reason`には`token_budget_exceeded`/`no_supporting_evidence_chain`等、Context Budget由来の除外理由も記録する。

フロントエンドでは`geo.knowledge-search.tsx`（自由テキスト検索、スコア内訳表示）と`geo.cases.$id.tsx`（Expert Engine実行時のRetrieval Trace一覧、クリックで結果の採否理由を表示）から参照できる。

## 4. API

| メソッド/パス | 内容 |
|---|---|
| `GET /api/geo/knowledge/search/entities?q=...` | Entity Hybrid Search |
| `GET /api/geo/knowledge/search/knowledge-items?q=...` | Knowledge Item Hybrid Search |
| `GET /api/geo/knowledge/search/claims?q=...` | Claim Hybrid Search |
| `GET /api/geo/knowledge/retrieval-runs/{id}` | Retrieval Run 1件の詳細（全結果のスコア内訳） |
| `GET /api/geo/knowledge/cases/{case_id}/retrieval-runs` | 案件に紐づくRetrieval Run一覧 |

## 5. 既知の制約・スコープ外（Phase 2B候補）

- Vector Search（Embedding Deployment確認後）
- 日本語形態素解析ベースの検索（現状は`simple`設定FTS＋正規化ilikeの組み合わせで代替）
- 検索結果のRelevance Feedback学習
