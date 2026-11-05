# Cleanup Step 0/7: Baseline Snapshot と Before/After比較

作成日時: 2026-08-29（Step 0、コード変更0行の時点）〜2026-08-30（Step 7、全修正適用後）

## 0-1. 全テストBaseline

コマンド: `python -m pytest tests/ -q --tb=short`
結果: **378 passed, 6 failed**（7.97s）

Cleanup後に許容するのは、以下と**test node id・failure内容まで一致する既知failureのみ**。
件数（6件）自体を判定基準にはしない。

| test node id | failure内容 |
|---|---|
| `tests/test_geo_official_crawler.py::test_crawl_source_creates_events_and_observation` | `tests\test_geo_official_crawler.py:60: assert results[-1]["raw_value"] == 1.0` → `AssertionError: assert None == 1.0` |
| `tests/test_sustainability_article_selector.py::test_list_weekly_picks_excludes_not_selected_and_sorts_by_score` | `AssertionError: assert [] == ['c1', 'c3']`（Right contains 2 more items, first extra item: 'c1'） |
| `tests/test_sustainability_article_selector.py::test_list_weekly_picks_truncates_to_target_max` | `assert [p["article_cluster_id"] for p in picks] == ["c1", "c3"]` → `assert 0 == 3`（`len([])`） |
| `tests/test_sustainability_article_selector.py::test_list_weekly_picks_keeps_only_latest_run_per_cluster` | `assert [p["total_score"] for p in picks] == [100, 99, 98]` → `assert 0 == 1`（`len([])`） |
| `tests/test_sustainability_article_selector.py::test_list_weekly_picks_filters_by_expert_version` | `AssertionError: assert [] == ['c2']`（Right contains one more item: 'c2'） |
| `tests/test_sustainability_article_selector.py::test_list_weekly_picks_excludes_stale_runs_outside_since_days` | `AssertionError: assert [] == ['c2']`（Right contains one more item: 'c2'） |

いずれも地政学アプリ（geo_*）とは無関係（`geo_official_crawler`/`sustainability_article_selector`
ドメインの既存課題）。生ログ: `docs/cleanup-step0-baseline-pytest-raw.txt`に保存。

## 0-2. P1 DB Call Baseline（実測、Cleanup前コード）

`FakeSupabaseClient`を`select`/`insert`呼び出しをカウントするラッパーで包み、Cleanup前の
コードに対して実測した（推定値ではない）。

### P1-1: `attach_case_finding_provenance`（`geo_retrieval_service.py`）
- テストデータ: item 3件（`session_id`は`s1`×2、`s2`×1、それぞれ`case-1`/`case-2`に対応）
- **実測: 3 items → select 6回**（`geo_expert_review_sessions`3回＋`geo_cases`3回、
  itemごとに2回＝2N＝2×3=6と一致）

### P1-2: `_resolve_relationships_assets_dependencies`（`geo_intelligence_service.py`）
- テストデータ: entity 3件、relationship 2件
- **実測: 3 entities → select 8回**（`list_relationships_for_entity`がentityごとに2回
  ＝2N＝6回＋`geo_assets`1回＋`geo_dependencies`1回＝8回と一致）

### P1-3: `ingest_report`の`geo_claim_issues`（`geo_report_service.py`）
- テストデータ: chunk数C=2（複数段落の長文でchunk分割）、issue数I=2
  （country_region=["Iran","Oman"]から2つのIssueへ解決）
- **実測: `geo_claim_issues` select 4回、insert 4回**（C×I=2×2=4と一致。今回は
  全リンクが新規のためinsert回数もC×I=4——Cleanup後もこのinsert回数は変更しない）
- 参考: 全select内訳 `geo_source_registry`5, `geo_entities`4, `geo_claim_issues`4,
  `geo_claims`3, `geo_entity_aliases`2, `geo_documents`2, `geo_entity_issues`1,
  `geo_issues`1, `geo_themes`1, `geo_reports`1, `geo_document_chunks`1

## 0-3. AI Flow Baseline（実DB/実Azure OpenAI）

すべて実Supabase・実Azure OpenAIで測定（コード変更0行の時点）。Step 7で同一手法・同一入力を
使い、DB call数・Azure call数・token数を主指標として比較する（latencyは参考値）。

### Intelligence AI（`geo_intelligence_service.process_request`直接呼び出し）
- 入力: `theme_id=9a62d084-1442-45c9-96fc-50e2a0d3e1a9`（hormuz_strait）、
  質問「米国の海上安全保障プレゼンスは湾岸諸国への費用負担Leverageとして機能しているか」
  （既存の実Case`d9d4b72a-42a6-491b-8e86-43c1a3420ba5`の論点と同種、新規データ追加なし）
- **結果**: elapsed **26.5秒**、`knowledge_sufficiency=partial`、`expert_ai_called=False`
  （partial分岐のためExpert AIは呼ばれず、Azure call数=0）。
- Step 7で**同一の質問・同一theme_id**を再実行し比較する（この測定は既存データのみを使い、
  一切書き込みを行っていないため、Baseline測定用に特別なテストデータは不要）。
- 参考: 実データではsufficient分岐（Expert AI呼び出し）を再現する組み合わせが無いため、
  sufficient分岐のAzure call数/token Baselineは今回のBaseline測定では取得できていない
  （前回Closeoutセッションでは新規Report追加後にsufficient分岐を実証済み。今回のCleanupでは
  P0-1のE2E検証で新規Reportを追加するため、その際に副次的にsufficient分岐のAzure call数も
  記録する）。

### Expert AI（`geo_expert_engine.run_expert_request_with_mode`、mode=theme_design、case_idなし）
- 入力: `theme_id=9a62d084-1442-45c9-96fc-50e2a0d3e1a9`、
  query_text「ホルムズ海峡テーマの今後1ヶ月のResearch Design（追加で確認すべき論点）を整理してください」、
  `execution_mode`未指定（既定の`staged`）
- **結果**: elapsed **217.7秒**、Azure call数**2回**（stage1本実行＋stage3 repair、
  Quality Guardの2項目チェック失敗によりrepairが自動発火）、
  `prompt_tokens=52,324`、`completion_tokens=15,530`、`total_tokens=67,854`
- 内訳: stage1（1回目、elapsed_ms=72,172、prompt=23,414/completion=5,197）、
  repair:stage1（elapsed_ms=141,187、prompt=28,910/completion=10,333）
- Step 7で**同一theme_id・同一query_text・同一mode**を再実行し比較する（case_id不使用、
  theme_idのみのためテストデータ隔離不要）。

### Report Review / Delta extraction（`geo_report_service.start_report_review_and_version`）
実行のため、Cleanup Step 0専用の識別可能なテストデータを新規作成した（一意なtest prefix
`CLEANUP_STEP0_AIFLOW_BASELINE`をtitle/filenameに使用。`is_demo`は使わない——
`/api/geo/chat`等の通常Retrieval経路が`include_demo=False`で除外し得るため、本番相当経路と
同じ検索条件に乗るデータを優先し、識別はtitle上のtest prefixで行う）。

- **作成したテストデータのID一覧**（`report_identity_id=b64bf789-d13c-4b93-bd57-f1336b679e02`で
  追跡可能。既存本番相当データは変更していない）:
  - v1: `report_id=b64bf789-d13c-4b93-bd57-f1336b679e02`（実HTTP `POST /api/geo/reports`経由、
    country_region=["Iran"]、theme_id=hormuz_strait、issue_ids=
    `[4deedc6a-d92f-4881-b8e8-4fd7e6e6a90c, d3520d85-ef7e-4e98-b385-d1c9f9fa7da2]`へ紐付け済み）
  - v2: `report_id=4f0efe72-9706-4aff-8cd0-645dd8de4511`（実HTTP
    `POST /{report_id}/review`経由、`review_session_id=c2c1191f-452f-420b-af76-31ae38adec66`、
    change 1件抽出、elapsed **34.9秒**、Azure call数1回・repairなし——token usage未計測のため
    下記v3測定で別途補完）
  - v3: `report_id=3c2bfe00-3383-417a-ae03-f8452fe4929b`（Python直接呼び出し＋
    `geo_llm_common.call_llm_structured`をラップしてusage計測、change 3件抽出、
    elapsed **41.3秒**、**Azure call数1回**（repairなし）、
    `prompt_tokens=2,018`、`completion_tokens=941`、`total_tokens=2,959`）
- v2/v3どちらもrepairなしで1回のAzure callで完結しており、Delta extractionのBaseline
  Azure call数は**1回**（複数回のcase往復で構造化出力が一発成功したケース）。
- Step 7では、この`report_identity_id`の続き（v3をsupersedeする新版）で同一手法の
  Delta extractionを実行し比較する。E2E後にこれらのテストデータ（v1/v2/v3、
  review_session 2件）のID一覧を最終報告に記載する（削除するかはユーザー判断、
  前回Closeoutと同様に最終確認する）。

---

## Step 7: After計測・比較結果（全P0/P1修正適用後）

### 全テスト回帰
`python -m pytest tests/ -q` → **391 passed, 6 failed**。failした6件は
`test node id`・failure内容ともにStep 0 Baselineと完全一致（新規failureなし）。
新規追加13件（P0-1: 3件、P0-2: 3件、P1-1: 4件、P1-2: 3件）はすべてグリーン。

### P1 DB Call数 Before/After（決定論的・確実な指標）

| 項目 | Before | After | 差分 |
|---|---|---|---|
| P1-1 `attach_case_finding_provenance`（item3件） | select 6回 | select 2回 | -4回 |
| P1-2 `_resolve_relationships_assets_dependencies`（entity3件） | select 8回 | select 2回 | -6回 |
| P1-3 `ingest_report`の`geo_claim_issues`（chunk2×issue2） | select 4回・insert 4回 | select 2回・insert 4回 | select -2回、insertは意図的に不変 |

すべて実測値（同一テストシナリオでBefore/After双方を実行）。write回数（insert）は
どの項目も変更していない——読み取りのみを削減した。

### P0-1完了条件の実証（実DB、stable identifier基準）

新規に一意な識別可能テストデータ（`CLEANUP_STEP7_P0_1_TEST`）でv1→v2を作成し、
`_resolve_internal_reports`（Retrieval層）を実DBに対して直接実行。表示用titleラベルではなく
`document_id`（stable identifier）で判定した:
- v1 document_id（`fd5d2288-...`）: **含まれない**（旧版が正しく除外された）
- v2 document_id（`30a69133-...`）: **含まれる**（新版が正しく取得できた）
- 実際に稼働中のAzure OpenAIパイプライン（`process_request`、実HTTP相当）の`references`
  でも同じ結果を再確認。加えて、既存のStep0 Baselineテストデータ（`CLEANUP_STEP0_AIFLOW_BASELINE`、
  v1/v2/v3の3版が既に実DBに存在）についても、`internal_report`として現れるのは
  **v3（document_id=`3a85df80-...`）のみ**であり、v1（`931a911f-...`）・v2（`75cb1373-...`）は
  現れないことを確認——**2つの独立したReport系列で同時に**、旧Versionの除外と新Versionの
  正しい置換の両方を実証した。

### Report Review → Knowledge再利用の回帰確認

P0-1検証用に作成したv1→v2のReviewから抽出されたChange（`reusability_class=
case_specific_assessment`）をPromoteし、`knowledge_type=report_finding`のKnowledge Item
（`f1702a32-...`）が生成されることを確認。`geo_knowledge_item_issues`への2件のIssue紐付けと、
`geo_retrieval_service.get_internal_expert_assessments`からの実際の再取得も確認済み
（Cleanup前セッションで確立した経路が引き続き正常動作、regressionなし）。

### Actor Intelligence Pack生成の回帰確認

実Case（`d9d4b72a-...`）に対し`build_actor_intelligence_pack`を実DBで実行。5名のKey Actor
（Trump/Qatar/Saudi Arabia/UAE/United States）が選出され、Trumpのactor_profileが正しく
`profile_available=True`で解決されることを確認（P0-2のretrieval_statusフィルタ追加後も、
実データがすべて`available`のため出力は変化しない＝regressionなし）。

### Intelligence AI / Expert AI Before/After（参考値、要注意）

**重要な限定事項**: 以下はDB状態・LLM非決定性の影響を受けるため、単純な数値比較を
Regression判定には使わない（ユーザー確認事項の通り、latencyは参考値、call数/token数も
本ケースでは以下の理由により交絡がある）。

- **Intelligence AI**（同一質問・同一theme_id）: Before `sufficiency=partial`
  （Expert AI呼ばれず、Azure call 0回、26.5秒）→ After `sufficiency=sufficient`
  （Expert AI呼ばれる、96.4秒）。**この変化はCleanupのコード変更が原因ではない**——
  Before/Afterの間に本Cleanup検証用のReport（`CLEANUP_STEP0_AIFLOW_BASELINE`・
  `CLEANUP_STEP7_P0_1_TEST`）を同一Issueへ追加したため、Retrieval対象の実データ量が
  増えたことによる。P0-1の修正自体は「古いVersionを除外する」方向にのみ働くため、
  もし影響があるとすれば参照数を**減らす**方向のはずであり、実際にはむしろ
  sufficientへ改善している——これはテストデータ追加による自然な結果である。
  `assess_sufficiency`/`should_call_expert_ai`のロジック自体は本Cleanupで一切変更していない
  （コード差分で確認可能）。
- **Expert AI**（同一theme_id・同一query_text・mode=theme_design）: Before 2 Azure call
  （stage1 + quality guard repair、217.7秒、total_tokens=67,854）→ After 1 Azure call
  （repairなし、41.4秒、total_tokens=26,074）。**この差もCleanupのコード変更が原因ではない**——
  `geo_expert_engine.py`は本Cleanupで一切変更しておらず（変更ファイル一覧に含まれない）、
  repairの発火はLLM出力の非決定性（Quality Guardチェックの通過可否）に依存するため、
  同一入力でも実行のたびに変動しうる。
- 上記2フローについての本当のregression-safety根拠は、数値比較ではなく
  **コード差分そのもの**（`geo_expert_engine.py`・`assess_sufficiency`・`should_call_expert_ai`
  は無変更）と、**Unit/Integrationテスト391件（Sufficiency分岐のテストも含む）が
  全てグリーン**であること。

### 作成した実DBテストデータ一覧（削除するかはユーザー判断、要確認）

| 用途 | report_identity_id | versions | 主なreport_id |
|---|---|---|---|
| Step0 AI Flow Baseline | `b64bf789-d13c-4b93-bd57-f1336b679e02` | v1/v2/v3 | v1=`b64bf789-...`, v2=`4f0efe72-...`, v3=`3c2bfe00-...` |
| Step7 P0-1検証 | `c4ae8da3-9ffc-4ce7-893c-8bc6ec977e02` | v1/v2 | v1=`c4ae8da3-...`, v2=`edde2c86-...` |

関連するReview Session（3件）、Knowledge Item（`report_finding`1件:
`f1702a32-3c3a-405d-b565-c547a52d8d7d`）も上記に付随して実DBに存在していた。
すべて`author`/titleに`CLEANUP_STEP0_`または`CLEANUP_STEP7_`のprefixが付いており、
既存の本番相当データと明確に区別できた。

**対応済み**: ユーザー確認の上、上記テストデータ（Report 2系列・5版、Review Session 3件、
Knowledge Item 1件、および関連するClaim/Claim Evidence/Claim-Issue紐付け/Document Chunk/
Document〈Report本体＋Review用draft/final文書〉）を依存関係の順序で全て実DBから削除し、
`geo_reports`/`geo_expert_review_sessions`が0行に戻ったことを確認済み。
