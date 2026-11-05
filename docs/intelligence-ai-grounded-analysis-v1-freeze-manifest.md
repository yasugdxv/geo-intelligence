# INTELLIGENCE_AI_GROUNDED_ANALYSIS_V1 — Freeze Manifest

**Freeze日**: 2026-09-30
**Freeze対象（Phase 2F-1〜2F-3のStable Core）**:
- `geo_intelligence_ai_search_service.py`（Permission-filtered Lexical Hybrid Search、Backend metadata filtering）
- `geo_intelligence_ai_analysis_service.py`（Context selection、Lineage deduplication、Grounding rules、Source trace、Cross-report synthesis、Review/version awareness）
- `geo_intelligence_ai_api.py`（`POST /api/geo/intelligence-ai/search`、`POST /api/geo/intelligence-ai/analyze`）
- `sql/2026-09-29_geo_intelligence_ai_report_search.sql` / `sql/2026-09-29b_geo_intelligence_ai_search_snippet.sql`
- `tests/test_geo_intelligence_ai_search_service.py`（22件）/ `tests/test_geo_intelligence_ai_analysis_service.py`（10件）
- `scripts/_phase2f3_validation.py`（実DB Validation Casesの再現スクリプト）
- `docs/intelligence-ai-grounded-analysis-v1-validation-results.json`（Case A/B/C実行結果の生データ）

以降、この8機能（下記Freeze条件）のロジックは変更しない。Phase 2F-4（True Multi-turn Chat）はこのCoreを**呼び出す側**として上に積む。新しい要件が生じてこのCore自体を変更する必要がある場合はV2として別途設計する。

---

## 背景：この機能がなぜ作られたか

Phase 2F-1で「Permission-filteredな自然言語Report/Knowledge検索」、Phase 2F-2で「Search Results UI」、Phase 2F-3で「複数Reportを横断したGrounded AI Analysis」を段階的に実装した。目的は一般的なRAG Chatではなく、「過去にどう分析していたか／複数レポートで何が共通・相違しているか／時系列で見方がどう変化したか／Expert Review済みかどうか／どのSourceが根拠か」を、常にSourceまで遡って確認できる状態を作ることだった。

3 Phaseを通じて確定した絶対順序は以下:

```
Normalized Identity → Permission Evaluation → Eligible Corpus → Metadata Filter
→ Retrieval/Ranking → Context Selection → LLM Synthesis → Source Trace
```

AI（LLM）はこの順序のどこにもPermission判断を持たない。LLMへ渡す時点で利用可能Sourceは完全に確定している。

---

## 設計原則（V1で確定した責務分離）

### Permission Evaluation（`resolve_eligible_report_ids`/`resolve_eligible_knowledge_item_ids`）
既存`geo_knowledge_access_policy`（Phase 2E）をそのまま使う。新しい権限ロジックは作らない。人間ユーザーは`requester_context`（認証済みIosIdentity由来）、Agentは`is_internal_caller`/`caller_domain`（認証済みCallerIdentity由来）で判定する。

### Metadata Filter（Phase 2F-3で追加）
Permission判定に**合格した行だけ**へdate_from/date_to/region/theme/key_actor/review_status/source_type/confidentiality_labelを適用する。Knowledge Itemはregion/theme/key_actor列を持たないため、これらのfilter指定時は「データが無いので一致不明」として除外する（fail-safe側）。

### Retrieval/Ranking（`search()`）
既存Full Text Search RPC（`search_geo_reports`/`search_geo_knowledge_items`、'simple' config、ts_rank_cd）へ、Permission+Metadata Filter適用後のID集合を`p_eligible_ids`として渡し、ranking計算自体をそのeligible corpusだけに限定する。RPCの結果はPython側でも再度eligible集合と突き合わせる（defense-in-depth）。embedding/pgvectorは使わない（Azure OpenAIにembeddings用Deploymentが存在しないため確認済み）——本Coreは真のsemantic検索ではなくLexical Hybrid Search（FTS + metadata）。

### Context Selection（`analyze()`内`_dedupe_by_lineage`/`_select_context`）
同一Report由来のReport-hit/Knowledge-hitを、`report_identity_id`（Knowledge Itemは`source_report_id`経由で正規化）で1 context sourceへ統合する（No Duplicate Source Inflation）。統合時はapproved版があれば優先、無ければ最新版（Version Awareness簡易ルール）。上位`MAX_CONTEXT_SOURCES=8`件のみLLMへ渡し、各sourceもtitle/metadata/summary/snippetのみ（本文全文は渡さない、token control）。

### LLM Synthesis（Grounding Rules）
Promptで「与えられたSource抜粋のみを根拠にする、一般知識・外部情報で穴埋めしない」を明記。findings各要素へ`source_supported`/`cross_report_synthesis`/`ai_inference`の3区分を必須化。insufficient_evidenceは「意味のある回答が実質作れない場合のみtrue」（開発中に過剰発火を発見し、prompt修正で解消——後述）。

### Source Trace（Hallucination Guard）
LLMへは実sourceのUUIDではなく`ref_id`（S1, S2...）のみを渡す（IDのhallucinationそのものを構造的に防ぐ）。LLM出力が存在しないref_idを引用した場合、そのfindingをPython側で機械的に除外する（LLM出力の引用を無条件に信用しない）。

---

## 開発中に発見・修正した設計上の問題

1. **`_knowledge_item_result`のsource_report_id取得元**: `structured_content.source_report_id`とgeo_knowledge_items自体のtop-levelな`source_report_id`列の両方が実データには存在する（`geo_ios_knowledge_publication_service.py`が両方へ同じ値を書き込む）。lineage正規化・review_status derivationはtop-level列を、表示用metadataはstructured_content側を参照する——用途ごとに正しい参照元を使う必要があった（テストfixtureの不整合として発見、本番コードは元から正しい）。
2. **insufficient_evidenceの過剰発火**: 当初promptでは「十分な根拠が無い場合はtrue」とだけ指示していたため、LLMが「一部の詳細（数値・対策案等）が無い」だけでtrueを返すケースが実DB検証（Case A/B/C）で3件とも発生した。「質問に対して意味のある回答が実質的に何も作れない場合のみtrue」と明確化するprompt修正を行い、再検証で解消したことを確認した。

---

## Freeze条件（すべて確認済み）

| 項目 | 結果 |
|---|---|
| Permission Evaluation順序 | Metadata Filter/Retrievalより必ず先（コード構造として強制、ユニットテストで確認） |
| Unauthorized source漏洩 | 0（実DB検証Case Aで、同じ検索語にヒットする別部門Report 1件を混入させ、answer/source_refsのいずれにも一切現れないことを確認） |
| selected_source_refsのPermission再検証 | 毎request再評価（キャッシュ無し、ユニットテストで異なるrequester_contextで異なる結果になることを確認） |
| Hallucination Guard（存在しないref_id引用） | findingを機械的に除外（ユニットテストで確認） |
| No Duplicate Source Inflation | 同一Report由来のReport-hit/Knowledge-hitを1 context sourceへ統合（ユニットテストで確認） |
| Version Awareness | 同一lineageでapproved版を優先、無ければ最新版（ユニットテストで確認） |
| Temporal Analysis | Context sourcesをdate昇順で提示、実DB検証（Case B、2026-01/04/07）でLow→Medium→Highの時系列変化が正しく整理されることを確認 |
| Review Status Awareness | 未レビュー（uploaded_only）をExpert Review済みとして扱わない（実DB検証Case Cで確認） |
| Backend metadata filter | Permission合格後の候補だけに適用（ユニットテスト10件で確認） |
| バックエンドregression | **2052 PASS**（新規テスト20件含む、regressionゼロ） |
| Frontend | TypeScript/ESLint/build すべてPASS |

---

## 関連ファイル

- `geo_intelligence_ai_search_service.py` / `geo_intelligence_ai_analysis_service.py` / `geo_intelligence_ai_api.py` — 実装本体
- `tests/test_geo_intelligence_ai_search_service.py` / `tests/test_geo_intelligence_ai_analysis_service.py` — 単体テスト（計32件）
- `scripts/_phase2f3_validation.py` — 実DB Validation Cases（A/B/C）の再現スクリプト（実行するとtest report/knowledge_itemを作成・分析・削除まで一括実行する）
- `docs/intelligence-ai-grounded-analysis-v1-validation-results.json` — 上記スクリプトの実行結果生データ（Case A/B/C全文、permission_check結果を含む）
- `eco-digest-spark/src/lib/geoIntelligenceAiApi.ts` / `eco-digest-spark/src/components/geo/geo-intelligence-ai-search.tsx` / `eco-digest-spark/src/routes/geo.intelligence-ai.tsx` — Frontend（Search Results UI + Analysis UI、**Freeze対象外**——Phase 2F-4でChat UIとして拡張される前提）

## 依存する既存機構（このFreezeの対象外、無変更）

- `geo_knowledge_access_policy.py`（Phase 2E、Permission判定の単一の正）
- `geo_report_service.py`（`get_reports_by_ids`/`derive_report_review_status`/`_build_search_text_source`等、Report Service全体は広く共有されているためこのFreeze自体の対象ではない）
- `geo_ios_identity.py` / `geo_intelligence_auth.py`（Identity層、Pilot-grade、将来Entra ID等へ差し替わる可能性がある前提のまま）
- `geo_llm_common.call_llm_structured`（LLM構造化出力の共通ヘルパー、地政学基盤全体で共有）

## 未着手（V1のスコープ外、Phase 2F-4以降）

- True Multi-turn Chat / Conversation History（Phase 2F-4）
- 詳細Citation UI（Phase 2F-5予定）
- Theme Discovery連携UI
- 真のsemantic/vector検索（Azure OpenAI embeddings deployment導入後）
- 同一Reportの過去version本文へ明示的に遡る「以前どう見ていたか」機能（現状はapproved優先/最新版の簡易ルールのみ）
- Knowledge Itemのregion/theme/key_actor metadata（データ列が無いため現状filter対象外）
