# THEME_DISCOVERY_V1 Freeze Manifest

- Freeze日: 2026-10-01
- 対象フェーズ: Phase 2G-1〜2G-5（Theme Discovery、Expert AI Handoffまで）
- 検証フェーズ: Phase 2G-6（本Manifestを作成したフェーズ、新機能実装なし）
- Verdict: **THEME DISCOVERY V1 READY**（詳細はPhase 2G-6 Completion Report参照）

## 1. Freeze対象スコープ

Theme Discovery V1として固定する範囲は、以下のファイル群が提供する機能全体。

| ファイル | 役割 |
|---|---|
| `geo_theme_candidate_service.py` | Candidate生成（Episode Cluster/Situation Link由来）、fingerprint、CRUD、lifecycle、Human-refined content保護 |
| `geo_theme_candidate_wallbounce_service.py` | Wall-bounce Chat（Candidate改善提案、Reading生成なし） |
| `geo_theme_candidate_handoff_service.py` | Expert AI Consultation Handoff Bridge |
| `geo_theme_candidate_api.py` | 上記すべてのHTTP API |
| `geo_ios_analysis_session_service.find_or_create_session_for_theme_candidate` | Handoff専用の原子的check-and-create |
| `sql/2026-09-30_geo_theme_candidates.sql` | `geo_theme_candidates`テーブル、`geo_ios_analysis_sessions.source_theme_candidate_id` |
| `sql/2026-10-01_geo_handoff_create_session_if_absent.sql` | Concurrent Idempotency用advisory lock RPC |
| `eco-digest-spark/src/routes/geo.theme-discovery.tsx` | Theme Discovery UI（一覧/Detail/Evidence/Internal Coverage/Wall-bounce/Handoff Preview） |
| `eco-digest-spark/src/lib/geoThemeCandidateApi.ts` | Frontend APIクライアント |

依存するが**変更していない**既存Core:
- Episode/Situation pipeline（`geo_episode_cluster_service.py`、`geo_situation_link_service.py`、`geo_actor_episode_service.py`、`geo_connector_gdelt*.py`）
- INTELLIGENCE_AI_V1（`geo_intelligence_ai_search_service.py`、`_analysis_service.py`、`_chat_service.py`、`_theme_coverage_service.py`）
- Expert AI Core（`geo_expert_engine.py`、CQAS、Reading Exposure）
- `geo_ios_intake_service.py`／`geo_ios_analysis_session_service.py`の既存関数群（`create_session`/`create_context_version`は無変更、新規関数のみ追加）

## 2. Flow

```
External Signal → Actor Episode Candidate → Episode Cluster(accepted)
    → Situation Link(accepted, 複数Cluster横断時) → Theme Candidate生成(sync)
    → Theme Discovery UI（一覧/Detail）
    → Internal Coverage（Runtime、Current requesterごと、非永続）
    → Wall-bounce（External Evidence + Internal Coverageにground、AI proposalは非自動反映）
    → Human Refinement（apply_candidate_refinement、allowlist方式）
    → SHORTLISTED / MONITORING / DISMISSED（Human操作のみ）
    → Handoff Preview（読み取り専用）
    → Handoff（Session + 初期Intake Context作成、concurrent idempotency付き）
    → HANDED_OFF
    → Expert AI（Intake → Cue → Actor → Multiple Readings → Primary/Alternative、既存フローを独立実行）
```

## 3. Security Invariants

- Internal Coverage / Wall-bounce / Handoffのいずれも、`geo_theme_candidates`へcoverageを**永続化しない**（Requester依存のため、毎回Runtime計算）。
- 他部門限定Report（`human_visibility=custom`、`allowed_org_units`に自部門を含まない）は、count/title/date/source/gap計算のどこにも影響しない（fail-closed）。
- Anonymous requesterは既存`geo_knowledge_access_policy`のfail-closed方針に従い、`custom`可視性のReportを一切見られない。
- Wall-bounce/Handoffは毎ターン・毎回、**現在のrequester_context**でPermissionを再評価する（前ターン・前回の結果を信用しない）。

## 4. Human Decision Invariants

- `sync_theme_candidates`（AI生成経路）は`update_candidate_status`を一切呼ばない——AIが自動でlifecycleを遷移させることは構造的にできない。
- `status=="NEW"`の間のみ、AI resyncがframing（title/question/observed_change/why_now/candidate_type/change_type/business_relevance）を更新する。SHORTLISTED以降はframingが自動上書きされない（evidence系フィールドのみ更新）。
- Wall-bounceの`REFINEMENT_EDITABLE_FIELDS`allowlist（title/question/actors/regions/themes/business_relevance）外は、Wall-bounce経由では構造的に変更不可（Evidence/statusを含まない）。
- Handoffは常にDB保存済みのCandidate framingのみを使う。Wall-bounceの未反映AI proposalはHandoff Contextへ混入しない。
- Handoff成功（Session + Intake Context作成の両方）時のみCandidate status→HANDED_OFF。失敗時はstatus変更なし、orphan Sessionも残さない。

## 5. Expert AI Independence

- Theme Discoveryのモジュール群（`geo_theme_candidate_*.py`）は`geo_expert_engine`・CQAS・Reading生成系モジュールを一切importしない（grep確認済み、ヒットはdocstring内の言及のみ）。
- Handoffが書き込むのは`geo_ios_analysis_contexts`のCONTEXT_FIELDSのみであり、`main_scenario`/`hypothesis_register`等のReading形状データを一切含まない。
- Expert AIは既存のIntake→Cue→Actor→Multiple Readings→Primary/Alternativeというフローを、Theme Discoveryからの初期Contextを入力として独立に実行する（本Freezeで変更なし）。

## 6. Known Non-blocking Technical Debt

1. **FTS AND-matching制限**（Theme Discovery固有ではなく、INTELLIGENCE_AI_V1由来の既知の制限）: Postgres `'simple'`設定+`plainto_tsquery`のAND-matchingにより、自然言語を含む長いCandidateタイトル・Questionをそのまま検索クエリに使うとInternal Coverageの類似検索が0件になることがある。短いkeyword形式なら正しく機能する（本フェーズでも実機確認済み）。Theme Discoveryのコード自体のバグではない。
2. **NEW状態でのWall-bounce編集の脆弱性**: `status=="NEW"`のままWall-bounceでframingを人手編集した場合、その後の`/sync`再実行でAI framingにより上書きされうる（保護はstatusのみを見ており「人間が編集したか」を見ていないため）。UI側のソフトな注意喚起のみで対応し、Core（Candidate generation）には触れない方針を維持（HANDED_OFF以降はこのリスクが自然に閉じる）。
3. **Handoff失敗時のAtomicityはunit testのみで検証**: Intake Context作成失敗時のSessionロールバックは、実DBで人為的に故障を再現するのが非現実的なため、monkeypatchによるunit test（`test_handoff_rolls_back_session_when_context_creation_fails`）でのみ検証済み。
4. **UI視覚確認はルート到達性のみ**: 本Freezeの検証では、実ブラウザでの目視レイアウト確認（Playwright等）は実行していない。dev server起動＋HTTPステータス確認（`/geo/theme-discovery`等が200を返すこと）のみ実施。レイアウト崩れ・無限loading等の目視確認はユーザー側で別途行うことを推奨する。

## 7. E2E Validation Evidence（Phase 2G-6、実DB・使い捨てスクリプトで実施・削除済み）

External Event→Actor Episode Candidate→Episode Cluster（実RPC経由accepted）→Candidate生成→Evidence trace→Dedup(2回目sync)→SHORTLISTED+Human Refinement保護(3回目sync後もtitle/question維持)→MONITORING/DISMISSED遷移→Internal Coverage 3段階Permission（own-dept可視/other-dept不可視/anonymous fail-closed）→Wall-bounce(grounding+Evidence/status不変)→Handoff Preview→Handoff(Session+Context作成、Human-refined内容の引き継ぎ、未反映proposal非混入、他部門非漏洩)→双方向traceability→Expert AI Independence（Context fieldがCONTEXT_FIELDSのみ）まで一本通しで確認、全項目PASS。検証用に作成した行はすべて削除し、残留ゼロを確認済み。

加えて、Phase 2G-5のConcurrent Idempotency修正についても実DBで2並行Handoff requestを発行し、Session 1件のみ・同一session_id・HANDED_OFF・orphanなしを確認済み。

## 8. Regression結果（Phase 2G-6時点）

- Backend full regression: **2122 passed**
- Theme Candidate関連（handoff/service/api/wallbounce）: 全PASS
- Frontend: ESLint・`tsc --noEmit`・production build すべてエラーなし
- 既存ルート（`/geo/news-signals`、`/geo/intelligence-ai`、`/geo/ios/analysis`、`/geo/theme-discovery`）: dev server上でHTTP 200確認

## 9. Freeze後に変更禁止とするCore

次の明示的なV2設計なしに変更しない:
- Candidate generation ロジック・fingerprint設計（`_compute_fingerprint`の入力、Evidence→Observed Change→Why Now→Questionの生成順序）
- Human refinement protection（status別のmerge patch分岐、`REFINEMENT_EDITABLE_FIELDS`allowlist）
- Runtime Internal Coverage原則（非永続・Current requester計算・2-stage設計）
- Wall-bounce grounding／Candidate update boundary（allowlist方式、AI proposalの非自動反映）
- Handoff mapping（Candidate→Intake Context field対応）
- Handoff idempotency（`source_theme_candidate_id`検索＋advisory lock RPC）
- Theme Discovery／Expert AI責務分離（Theme Discoveryは「何を見るべきか」、Expert AIは「どう読むべきか」）
