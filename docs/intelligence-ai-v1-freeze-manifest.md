# INTELLIGENCE_AI_V1 — Freeze Manifest

**Freeze日**: 2026-09-30
**Freeze対象（Phase 2F-1〜2F-6、Intelligence AI全体）**:

- `geo_intelligence_ai_search_service.py` — Permission-filtered Lexical Hybrid Search + Backend metadata filter（2F-1/2F-2/2F-3）
- `geo_intelligence_ai_analysis_service.py` — Cross-report Grounded Analysis（Context selection/Lineage dedup/Grounding rules/Source trace/Review・Version awareness、2F-3、`clean_snippet`は2F-5でpublic化）
- `geo_intelligence_ai_chat_service.py` — True Multi-turn Chat（Current query resolution、2F-4）
- `geo_intelligence_ai_theme_coverage_service.py` — Theme Discovery Internal Intelligence Bridge（2F-6）
- `geo_intelligence_ai_api.py` — `/search` `/analyze` `/chat` `/internal-coverage`
- `sql/2026-09-29_geo_intelligence_ai_report_search.sql` / `sql/2026-09-29b_geo_intelligence_ai_search_snippet.sql`
- `tests/test_geo_intelligence_ai_search_service.py`（22件）/ `tests/test_geo_intelligence_ai_analysis_service.py`（10件）/ `tests/test_geo_intelligence_ai_chat_service.py`（5件）/ `tests/test_geo_intelligence_ai_theme_coverage_service.py`（12件）
- `scripts/_phase2f3_validation.py` / `_phase2f4_validation.py` / `_phase2f6_validation.py` — 実DB Validation再現スクリプト
- `docs/intelligence-ai-grounded-analysis-v1-freeze-manifest.md` + 同validation-results.json — 前身Freeze（2F-1〜2F-3のみ）、本Freezeが継承・上書きする形で全体を対象化する
- Frontend（UI、**ロジックは変更しないがUIとしては引き続き発展させてよい**）: `eco-digest-spark/src/lib/geoIntelligenceAiApi.ts` / `src/components/geo/geo-intelligence-ai-search.tsx` / `src/routes/geo.intelligence-ai.tsx`

以降、上記Backendの8機能（下記Freeze条件）のロジックは変更しない。Phase 2G（Theme Discovery）はこのStack全体を**呼び出す側**として扱う。新しい要件が生じてこのStack自体を変更する必要がある場合はV2として別途設計する。

---

## 背景：Intelligence AIが何を成立させたか

Phase 2F-1から2F-6まで、以下の一直線の積み上げでIntelligence AIを構築した。

```
Search（2F-1: Permission-filtered Lexical Hybrid）
  → Search Results UI + Relevant Snippet（2F-2）
    → Cross-report Grounded Analysis（2F-3: Compare/Temporal/Coverage/Reviewed）
      → True Multi-turn Chat（2F-4: 会話しながら深掘り）
        → Citation / Source UX（2F-5: 根拠まで遡って確認できる）
          → Theme Discovery Internal Coverage Bridge（2F-6: 次工程からの再利用窓口）
```

到達点は、**社内に蓄積されたReport/Knowledgeを、権限を守りながら検索・比較・時系列分析・会話・引用確認し、その結果を次のTheme Discoveryでも再利用できる基盤**が一通り成立したこと。各Phaseで一貫して守った絶対原則:

```
Normalized Identity → Permission Evaluation → Eligible Corpus → Metadata Filter
→ Retrieval/Ranking → Context Selection → LLM Synthesis → Source Trace
```

LLMはこの順序のどこにもPermission判断を持たない。Multi-turn化（2F-4）でも、前turnのSource本文・Permission結果を一切信用せず、各turnで現在のIdentityによりゼロから再評価する。Theme Discovery Bridge（2F-6）でも、Unauthorized Reportの存在自体を示唆する情報は一切返さない。

---

## Freeze条件（すべて確認済み、実DB検証込み）

| 項目 | 結果 |
|---|---|
| Permission Evaluation順序 | 全機能（search/analyze/chat/internal-coverage）で必ずMetadata Filter/Retrievalより先 |
| Unauthorized source漏洩 | 0（2F-3/2F-4/2F-6の実DB検証で、同一検索語にヒットする別部門Reportを混入させても、answer/source_refs/coverage件数のいずれにも一切現れないことを繰り返し確認） |
| Multi-turn Permission再評価 | 同じ会話historyでもrequester_contextが変われば可視範囲が変わることを決定的に確認（前turnの結果を引き継がない） |
| Hallucination Guard | 存在しないref_idを引用したfindingを機械的に除外 |
| No Duplicate Source Inflation | 同一Report由来のReport-hit/Knowledge-hitを1 context sourceへ統合 |
| Review/Version Awareness | 未レビューをExpert Review済みとして扱わない、同一lineageでapproved版を優先 |
| Coverage Gap（2F-6） | Theme Candidateのkeywords/actors/regionsとaccessible Report群を決定的に比較するのみ、LLMで想像しない（実DB検証でspec例と完全一致） |
| Citation UX（2F-5） | source_refsをそのまま使い、独自source IDを作らない |
| バックエンドregression | **2069 PASS**（Phase 2F-6時点、regressionゼロ） |
| Frontend | TypeScript/ESLint/build すべてPASS |

---

## 既知の残課題（Blockerではない、Phase 2G開始前に直す必要なし）

1. **`limit=200`によるcoverage件数の上限**（`geo_intelligence_ai_theme_coverage_service.DEFAULT_COVERAGE_SCAN_LIMIT`）: これを超えるeligible+一致件数がある場合、件数が実数より少なく出る。Theme Discoveryが件数自体をKPIとして使う段階になった時点で、専用COUNT queryへの置き換えを検討する。
2. **Coverage Gap判定は部分一致ベース**: 表記ゆれ・同義語には対応しない（MVP相応、意図的な簡易実装）。
3. **Agent向けHTTPエンドポイント未公開**: `/api/geo/intelligence-ai/internal-coverage`は人間/IosIdentity向けのみ。Cross-Domain Agent呼び出しは`assess_internal_coverage()`をPython関数として直接呼ぶ想定（既存`geo_department_intelligence_service.py`と同じパターン）。

---

## 依存する既存機構（このFreezeの対象外、無変更）

- `geo_knowledge_access_policy.py`（Phase 2E、Permission判定の単一の正）
- `geo_report_service.py`（`get_reports_by_ids`/`derive_report_review_status`/`_build_search_text_source`等）
- `geo_ios_identity.py` / `geo_intelligence_auth.py`（Identity層、Pilot-grade）
- `geo_llm_common.call_llm_structured`（LLM構造化出力の共通ヘルパー）

## 未着手（V1のスコープ外、Phase 2G以降）

Phase 2G（Theme Discovery）が担う責務——Intelligence AI側では一切判断しない（§15、2F-6 Completion Report参照）:

- External Signals → Signal Cluster → Theme Candidate生成
- Business Relevance評価
- Theme優先順位付け・採否判断
- ユーザーとの壁打ちUI
- Expert AI相談へのHandoff
- Theme Candidate lifecycle管理

技術的な未着手（V2候補）:

- 詳細Citation UI強化（2F-5で基礎は実装済み、さらなる高度化は将来）
- 真のsemantic/vector検索（Azure OpenAI embeddings deployment導入後）
- 同一Reportの過去version本文へ明示的に遡る「以前どう見ていたか」機能
- Conversation persistence（Multi-turn Chatはブラウザメモリのみ、reloadで消失）
- 上記「既知の残課題」3点
