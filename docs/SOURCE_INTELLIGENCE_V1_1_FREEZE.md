# SOURCE_INTELLIGENCE_V1_1 Freeze Manifest

| 項目 | 内容 |
|---|---|
| Freeze名 | **SOURCE_INTELLIGENCE_V1_1** |
| Freeze日 | 2026-10-05 |
| 承認 | Article Discoveryの実装・実DB E2E・Regression結果を確認し、V1_1としてFreezeを指示（ユーザー指示、2026-10-05） |
| 位置づけ | **V1との差分snapshot**。V1（`freeze/SOURCE_INTELLIGENCE_V1/`）は一切変更していない。V1_1の全体像 = V1 + 本snapshotの差分 |
| Snapshot | `freeze/SOURCE_INTELLIGENCE_V1_1/`（Manifestの複製は `docs/SOURCE_INTELLIGENCE_V1_1_FREEZE.md`） |
| 検証 | `python freeze/SOURCE_INTELLIGENCE_V1_1/verification/freeze_check.py`（プロジェクト直下で実行。V1_1・V1の両snapshotと現行原本の一致を確認） |
| SHA256SUMS | `24`ファイル分、`SHA256SUMS.txt` 自体のSHA256 = `effe4ec8c2eb8220b9e2af06b44f23bc06b43393a67d44c4910ade3c63b9bf2f` |
| DBスキーマ変更 | **なし**（SQLの追加・変更なし。候補の由来は既存列 `generation_model` の接頭辞で判定） |

## Freeze対象（V1との追加・変更点）

- Article Discovery（記事からテーマ候補をその場で提案）
- Natural-language discovery query（自然言語の探索文）
- 30日を基本とするArticle retrieval（入力文で期間変更可、上限180日）
- Source Set / Source限定探索
- 実処理に対応した3段階進行表示（入力を解釈 / 関連記事を検索 / テーマ候補を整理）
- Theme Candidate generation
- Evidence article binding（title / source / published date / original URL）
- Candidate origin（記事から発見 / Situationから発見 / モニタリング / 手動）
- Proposal / pending表示（「提案（未確定）」）
- Existing Theme / Monitoringとのduplicate handling（「既存テーマに新しい動きがあります」）
- insufficient evidence handling（材料不足なら候補を作らない）
- Theme Discoveryの簡素化UI（プリセット・旧5フィルター・分類タブ・注目度バッジを廃止）
- keyword filtering / date sorting（新しい順・古い順）
- logical rejection（削除=DISMISSED、論理削除）
- Theme → Monitoring / Research / Expert AI handoff（維持）

Article Discovery Policy（動作順序・Evidence・Duplicate・Source Restriction・UI）は
`docs/SOURCE_INTELLIGENCE_V1_1_ARTICLE_DISCOVERY_POLICY.md` に仕様として固定した。要点:

1. Article → related article retrieval → change/issue clustering → Theme Candidate proposal の順。**1記事1Themeにしない**。Themeは「何が変わっているか／継続して見る価値があるか」を表す。
2. 候補は自動確定せず proposal / pending。**正式Themeへ自動昇格しない**。
3. すべての候補に根拠Article（title / source / published date / original URL）。**材料不足なら生成しない。LLMだけでThemeを作らない**。
4. 既存の Theme Candidate / Monitoring Theme / Theme と重複する場合は乱造せず「既存テーマに新しい動き」として扱う。
5. Source選択時は**選択Sourceの記事だけ**を根拠に使う。

## V1との差分ファイル

変更（V1 snapshotと内容が異なる原本）11件:
`geo_monitoring_theme_api.py` / `geo_source_recommendation_service.py` / `geo_theme_explorer_service.py` / `tests/test_geo_source_library.py` /
`eco-digest-spark/src/components/geo/{source-selector,theme-explorer-card,theme-explorer-panel}.tsx` / `eco-digest-spark/src/lib/geoSourceLibraryApi.ts` /
`eco-digest-spark/src/lib/i18n-extra/theme-explorer.ts` / `eco-digest-spark/src/routes/geo.theme-discovery.tsx` / `docs/SOURCE_INTELLIGENCE_V1_FEEDBACK.md`

新規5件:
`geo_theme_discovery_service.py` / `tests/test_geo_theme_discovery.py` / `eco-digest-spark/src/components/geo/theme-discovery-results.tsx` /
`docs/SOURCE_INTELLIGENCE_V1_1_CHANGELOG.md` / `docs/SOURCE_INTELLIGENCE_V1_1_ARTICLE_DISCOVERY_POLICY.md`

V1と同一（変更なし）: 49ファイル。V1 snapshotは無傷。
V1_1にはV1の「変更1・2」（FB-001/FB-002対応: プロンプト入力UI・進行表示・フィルター整理・削除）も含まれる（詳細は `CHANGELOG`）。

## Validation

| 項目 | 結果 | 証跡 |
|---|---|---|
| Python | **2596 PASS** | `verification/test_results/pytest_final.txt` |
| TypeScript | **PASS**（tsc exit 0） | `verification/test_results/typecheck_final.txt` |
| Frontend Build | **PASS**（vite build exit 0） | `verification/test_results/build_final.txt` |
| Real DB E2E | **PASS** | `verification/real_db_e2e_results.md` |
| READING_UI_V1 Gold 4件 | SHA256一致、GET中のLLM呼び出し0 | `verification/test_results/reading_ui_v1_gold_check_now.json` |

実DB確認: A 台湾海峡Query→Candidate生成 PASS / B Source restriction PASS / C Original URL PASS / D Duplicate prevention PASS /
E Insufficient evidence→Candidateなし PASS / F Situation Candidate compatibility PASS / G Monitoring・Research・Expert AI handoff PASS。

## Freeze時点のArticle Discovery Candidate

Freeze前に11件を確認し、**4件を却下（DISMISSED）、7件を「提案（未確定）」のまま残置**。正式Themeへの自動昇格なし。
一覧（candidate id / title / origin / status / 根拠記事数 / created_at / 判定・却下理由）:
`verification/article_discovery_candidates_at_freeze.md`（`.json`も同梱）。本文記事の複製はしていない。

## Known Limitations（V1_1）

- DB内の記事Coverageがまだ限定的
- Queryによっては材料不足になる
- 1件の出来事を継続傾向として読み過ぎる場合がある（Freeze前に4件を却下した理由の主因）
- 継続取り込み記事の一部に `source_id` が付いていない
- Article DiscoveryにはAI（Azure OpenAI）・認証が必要
- ProposalはHuman Review前提

V1から引き継ぐ既知事項（Report Studio起動時503、Sourceの重複候補未統合 等）は V1 Manifest を参照。

## V1 pg_catalog snapshot（Blockerではない）

V1で未取得の `schema_pgcatalog_snapshot.json` はV1_1 FreezeのBlockerにしない。DB schema証跡の補完作業として継続し、
取得できた時点でV1 / V1_1双方から参照できるverification artifactとして追加する。
既存Manifestを書き換える場合は、Freeze artifactの更新履歴を本Manifestとの両方に明示する。

### Freeze artifact更新履歴
- 2026-10-05: V1_1 Freeze作成（V1 snapshotは変更なし）。pg_catalog snapshotは未取得のまま。

## Freeze後の運用

評価期間中はArticle Discoveryも変更しない。改善点は `docs/SOURCE_INTELLIGENCE_V1_FEEDBACK.md`
（または `SOURCE_INTELLIGENCE_V1_1_FEEDBACK.md`）に記録し、次の改修はV1_2として扱う。
READING_UI_V1とExpert AI reasoning coreは既存Freezeを維持しており、本Freezeの対象外。
