# SOURCE_INTELLIGENCE_V1 Freeze Manifest

| 項目 | 内容 |
|---|---|
| Freeze名 | **SOURCE_INTELLIGENCE_V1** |
| Freeze日時 | 2026-10-05 17:05 (JST, +0900) |
| 承認 | Final Hardening結果を承認し、Freezeを指示（ユーザー指示、2026-10-05） |
| Snapshot | `freeze/SOURCE_INTELLIGENCE_V1/`（本Manifestの複製は `freeze/SOURCE_INTELLIGENCE_V1/MANIFEST.md`、原本は `docs/SOURCE_INTELLIGENCE_V1_FREEZE.md`） |
| 検証 | `python freeze/SOURCE_INTELLIGENCE_V1/verification/freeze_check.py [--db]`（プロジェクト直下で実行） |
| SHA256SUMS | `71`ファイル分、`SHA256SUMS.txt` 自体のSHA256 = `c60460b932bc9324145b08671423c590b6e967da7c973323cec56bb204b432d3`（`sha256sum -c` で全件OK） |

## 目的

「何を見るべきか」→「どの情報源を見るか」→「継続して何が変わったかを見る」→「必要に応じて深掘り調査する」を
一つにつないだ、**地政学インテリジェンス情報収集基盤のV1固定版**。

Freeze対象は Theme Discovery / Source Library / Source Set / Monitoring / Research連携 による **Source Intelligence基盤**。
**READING_UI_V1 と Expert AI reasoning core（Frozen Responseを含む）は既存Freezeを維持しており、本Freezeの対象外**。
本基盤からExpert AIへは既存のHandoff（Theme Candidate → Analysis Session）で接続するだけで、Expert AI側は変更していない。

## Architecture

```
Theme（Theme Discovery: geo_theme_candidates ／ 注目度・分類は表示層で算出）
  ↓  おすすめSource（理由つき）/ 共通Source Selector
Source Set（geo_source_sets ＋ geo_source_set_members。Theme専用ではない共通オブジェクト）
  ↓
Source（geo_source_registry = 組織共通Source Master）  1 : N  Articles（geo_external_signals.source_id）
  ├─ Tags（AI提案/Human確定）・Domain Alias・Source↔Theme（多対多）
  ↓
Monitoring（geo_monitoring_themes / geo_monitoring_alerts）  ／  Research（Report Studio Project + research_context）
  ↓
Expert AI（既存Handoff。本Freeze対象外）
```

## Freeze対象（機能）

**Theme Discovery（テーマ探索）**: Theme一覧 / Filter（地域・カテゴリ・注目度・新規性・期間） / 分類タブ（注目・新規・継続・過去） /
自然言語検索 / 注目度（説明可能な暫定評価。Priorityとは呼ばない） / Source Recommendation（理由つき） / 共通Source Selector /
Theme → Monitoring / Theme → Research / Expert AI handoff（既存導線）

**Source Library（情報ソースDB）**: Library（server-side filter・pagination・自然言語検索） / Source Detail / Source CRUD /
Soft Delete（`archived_at`） / Source Tags（AI Tag・Human Tag、承認フロー） / Domain Alias / Source ↔ Theme 多対多 /
Article ↔ Source 逆参照（監査表つき） / Source Recommendation / Source Performance / Source → Research

**Source Set**: Source Set / Member / Ordering / Archive（`archived_at`。Monitoring参照中は既定で拒否） /
Theme Explorer・Monitoring・Researchからの共通利用

**Monitoring**: 一覧 / Detail（9セクション） / Source Set / Data Freshness / Stable と No Fresh Data の分離 / Recent Articles /
Key Actors / Alert / Alert Context Validation / Alert audit / Theme → Monitoring

**Research**: Theme / Query / selected source_ids のhandoff / Source → Research / 日本語→英語 Query Expansion /
Query Expansion Guard / 選択Source制約 / matched・supplementary の区別 / Reference（Article title・Source・Published date・Original URL）

**Navigation**: インテリジェンス（1.テーマ探索 2.モニタリング 3.エキスパートAI相談 4.リサーチスタジオ 5.レポートレビュー）／
ナレッジ（1.情報ソースDB 2.レポートDB 3.ニーズDB 4.外部専門家DB）。旧Connector画面・開発者向けMonitoringは開発者セクションに維持。

## 対象ファイル

SHA256は `freeze/SOURCE_INTELLIGENCE_V1/` 配下のsnapshot複製と、Freeze時点の原本の**両方で一致を確認済み**。
`api_server.py`・`app-sidebar.tsx`・`i18n.tsx`・`routeTree.gen.ts`・`tests/_fakes.py`・`geo.theme-discovery.tsx`・
`geo.report-studio.$projectId.tsx` は既存ファイルへの変更を含む（共有ファイルのため、ファイル全体のハッシュを固定する）。

### Backend（新規）

| ファイル（`projects/geopolitical_monitor_dashboard/` からの相対） | SHA256 | 行数 |
|---|---|---|
| `geo_source_library_service.py` | `1f7183b21a518b0704f61cff1fa36eaf0c429b212e4e38e1f98baed740849cc8` | 636 |
| `geo_source_library_api.py` | `5c23d82edf9d83c5ad8e3d60bfb8370bb03f1ae843a9c85c5273cd7fac2c3bc4` | 450 |
| `geo_source_set_service.py` | `3991fb94ea47716d741980aa9b12e35e204fa3250aa4e45a8f934d2f3ed21dea` | 187 |
| `geo_source_recommendation_service.py` | `0b737ba9304ec147f9f4c23dc465db507f84348511a856876d4963c3b761d94a` | 133 |
| `geo_source_search_service.py` | `417aaeba104d9401e5341054e487cdec4bfd5d02f09e99f2df100a7f5790e90e` | 85 |
| `geo_source_research_service.py` | `2b0123acf7975bfc0069191c0d2705a411860e890b3c15838650d97082e1887d` | 256 |
| `geo_source_query_expansion.py` | `958fcb037dfc4e5cbd656457e7bc869d1f36e77a936881c400bbf6718e7655b9` | 190 |
| `geo_monitoring_theme_service.py` | `ba93a2d7ffc45d18c0ebe61749be7679344d0e633571e23c340dd60ebe634d62` | 466 |
| `geo_monitoring_theme_api.py` | `9109fcacd5b943f834fe1ea4f34f7dd67300ce1ed9bbee6a2ff1dbb433ac01d0` | 164 |
| `geo_theme_explorer_service.py` | `32d41ec866370a377106c1fe62185a9bca646a0a43f1ba8842e310de9b99f6b5` | 190 |
| `geo_alert_validation.py` | `8116a59f3465bc3e96e18dabe45eb09348ce3f6b4f21477b7604444dab5c9d79` | 210 |

### Backend（既存ファイルへの追記: router登録のみ）

| ファイル（`projects/geopolitical_monitor_dashboard/` からの相対） | SHA256 | 行数 |
|---|---|---|
| `api_server.py` | `35eeb60ce51bd9141a0b336a690e9b85dbfbc7d38a30e580c56f0067da06562c` | 927 |

### Frontend（新規）

| ファイル（`projects/geopolitical_monitor_dashboard/` からの相対） | SHA256 | 行数 |
|---|---|---|
| `eco-digest-spark/src/lib/geoSourceLibraryApi.ts` | `bb166fa96e835cec7b61b76dde4bb701a6bbe5fdd1fea8b3e05e3e806384124f` | 869 |
| `eco-digest-spark/src/lib/sourcePickStore.ts` | `57a7f645901188dae9d8ff3bf8d963d5f46806a3e3b2662c19e29d8851456e28` | 48 |
| `eco-digest-spark/src/lib/i18n-extra/index.ts` | `174332dcfc24dd3ff1a6b2af3890cf3ea3084808316070df0cea996cad8b0e92` | 11 |
| `eco-digest-spark/src/lib/i18n-extra/source-library.ts` | `76c07b7f0d5de56a2c3b7049e3befa667d85aa3f53c492bc28e6f118db64ee29` | 284 |
| `eco-digest-spark/src/lib/i18n-extra/source-selector.ts` | `ba2985b9589ad981bcce10e6466f6f28e0fc32f50580a118f338efae4244a0d1` | 89 |
| `eco-digest-spark/src/lib/i18n-extra/monitoring.ts` | `2c697ba2048b6d42ac06234657768e172b168c96f72d49f0e69a069e0873b5c0` | 280 |
| `eco-digest-spark/src/lib/i18n-extra/theme-explorer.ts` | `feaefaa206489e6daeccffe756c2e8b74c1a2681e8eaf1f0b94887c2dedfd145` | 184 |
| `eco-digest-spark/src/lib/i18n-extra/hardening.ts` | `542f8f9007fca357b31f82be9bfb443f4b13288ae1a645a06e5c04176ed8a3a8` | 61 |
| `eco-digest-spark/src/components/geo/data-freshness.tsx` | `8e47633f1fb17b99406d8eff6907f97aeaea6d80ef1df3339742b81418024dbd` | 43 |
| `eco-digest-spark/src/components/geo/source-selector.tsx` | `3429725c463d776e53898501341a5c63df1b20ad215575e59f25007c8257829c` | 406 |
| `eco-digest-spark/src/components/geo/source-library-parts.tsx` | `06f4ecc4a11531f959826eafe31192936b08d961443bc7d1eb69101058edd6c9` | 432 |
| `eco-digest-spark/src/components/geo/source-research-panel.tsx` | `5a6fcd7806cfdf3650d42df5569967d50037f8176b7c404145823117f968bb16` | 308 |
| `eco-digest-spark/src/components/geo/monitoring-common.tsx` | `17321ced87d1a79840e5061be77aa5ab3577768989ed4962fe5faae96019413d` | 132 |
| `eco-digest-spark/src/components/geo/monitoring-list.tsx` | `1ab5a254016964b58b86375aa8b366fcc6356333c55c7a6e032b644ac129a674` | 330 |
| `eco-digest-spark/src/components/geo/monitoring-detail.tsx` | `3ca51ab807dbf2531972208867faf36d40fd136f605a83afe24963ebcf3a0ddf` | 746 |
| `eco-digest-spark/src/components/geo/monitoring-edit-dialog.tsx` | `fc7c75bec0d52a84aaaf89ac27ea1bcba06d0072204961556e4991fb24e996de` | 168 |
| `eco-digest-spark/src/components/geo/theme-explorer-panel.tsx` | `825d85046e1565b812f12020dbf2f19c50fe1030350ade33013f6b63ad0bebf6` | 305 |
| `eco-digest-spark/src/components/geo/theme-explorer-card.tsx` | `eacb3ed53c9e74e57032fb529d9f7be6fac3cebd70cec9c0121771a12062043a` | 159 |
| `eco-digest-spark/src/components/geo/theme-explorer-actions.tsx` | `8e9f14575728abbda4f959d3f97767a00b1b46cc3dbe4413e9317332581ea650` | 178 |
| `eco-digest-spark/src/routes/geo.source-library.index.tsx` | `c10d85375cd9fd09028e28b1ad68d6fba919627324c733d1644f9e9956d95e8e` | 584 |
| `eco-digest-spark/src/routes/geo.source-library.$sourceId.tsx` | `7dea740f12e0a726c6aa98ae4245b9b3006186f74632a864455aadb2cdf6a068` | 590 |
| `eco-digest-spark/src/routes/geo.monitoring-themes.index.tsx` | `100b48c19865179804e29ddac16f4d5ce8811a6dba0baf81b70f88868628b498` | 7 |
| `eco-digest-spark/src/routes/geo.monitoring-themes.$monitoringThemeId.tsx` | `90b600ed263131762b1f23f68579b81d46c2aefa0658cc90fb8ff35aff888eb7` | 11 |

### Frontend（既存ファイルの変更）

| ファイル（`projects/geopolitical_monitor_dashboard/` からの相対） | SHA256 | 行数 |
|---|---|---|
| `eco-digest-spark/src/routes/geo.theme-discovery.tsx` | `05a66078180635c25d9394df95da729771704c101c7259bb31212c595f5e984c` | 968 |
| `eco-digest-spark/src/routes/geo.report-studio.$projectId.tsx` | `6f64de784d71676a51f205c43d164030fc0bc2e3ad11ed7ac8ea537b72891e31` | 314 |
| `eco-digest-spark/src/components/app-sidebar.tsx` | `d8c8fe32fb4bc579b1fc393efbd8730b33e6d9fda3bb49db4c1415cc0f0798ec` | 224 |
| `eco-digest-spark/src/lib/i18n.tsx` | `3d89af30d766a1e3a27305bb25bafd20ac7554418a3fc3e0a0a111e49c1fefe6` | 3321 |
| `eco-digest-spark/src/routeTree.gen.ts` | `a083f2e16915e16cad073736a982d94a6e09d287827a13dcf84c9df3a2fbba3a` | 1033 |

### Test

| ファイル（`projects/geopolitical_monitor_dashboard/` からの相対） | SHA256 | 行数 |
|---|---|---|
| `tests/test_geo_source_library.py` | `f6b69d276c223089760003d287be05095536546e445b817efbb0a801ac0b676c` | 609 |
| `tests/test_geo_alert_validation.py` | `493024a3bcf6866b40a4d1d0a488042c511be91291a3be1406faa7aee3242177` | 249 |
| `tests/test_geo_source_query_expansion.py` | `2b15f92f99f52ec1c04b96819ff276bd901dd80b651cc303dd9bbc1df076df8d` | 243 |
| `tests/_fakes.py` | `f4fcd7aaa259bd183363b26111db3253715e2e7504e429db847c13d6bc2cfb5c` | 370 |

### SQL migration（DB-1〜DB-8）

| ファイル（`projects/geopolitical_monitor_dashboard/` からの相対） | SHA256 | 行数 |
|---|---|---|
| `sql/2026-10-05_geo_source_library_db1_registry.sql` | `fc0e42c40220fd72cee9c524adce07b0716b98847fab0253ff132f42ececd85c` | 202 |
| `sql/2026-10-05b_geo_source_library_db2_source_sets.sql` | `d4d3fc1628082da00e46d9bd64d0b88b0dd05164fa0f4b6aa5938f7c5ac5b4d8` | 44 |
| `sql/2026-10-05c_geo_source_library_db3_signal_source_id.sql` | `c1393f53980f44dc5bda3c5569698b1a6f430ac76ba76737ffc9962bf73e81f3` | 45 |
| `sql/2026-10-05d_geo_source_library_db3b_safe_backfill.sql` | `6596cf42cf9f029809cc2eda912360558c0a301066356e0d9d9b83d53f355c53` | 59 |
| `sql/2026-10-05e_geo_source_library_db4_monitoring.sql` | `350bde45c4dff5271de433ed649d55ea515dc447d6c5dfc477db41247de53c74` | 81 |
| `sql/2026-10-05f_geo_source_library_db5_research_context.sql` | `57309a6223fdd517b5b076b20ad952b8cc07a0c7c9315a759e1a95212e5b3038` | 19 |
| `sql/2026-10-05g_geo_source_library_db6_source_set_archive.sql` | `e4746e62c5c7f8eb14e62be33673488dc94ac571fe66671285bde02dd373a5e4` | 24 |
| `sql/2026-10-05h_geo_source_library_db7_theme_fk_set_null.sql` | `2ef4191dfbebdd97511af210c86fae976d6a318ca268d1319b454b422a930b94` | 54 |
| `sql/2026-10-05i_geo_source_library_db8_alert_validation_audit.sql` | `6c16b5af6a8c83fcd65f4e769329e6bef9bac93475a1dcef761b5e86613cbc77` | 31 |

### SQL Preflight / Verification

| ファイル（`projects/geopolitical_monitor_dashboard/` からの相対） | SHA256 | 行数 |
|---|---|---|
| `sql/2026-10-05_geo_source_library_preflight.sql` | `da354f8f53368b6b3b47a543feb13091c2e23fad37aa0af86a311e4e838bae90` | 127 |
| `sql/2026-10-05_geo_source_library_verification.sql` | `a33c4b909fe6dd45d41740e63aa1607da5e8680d9b76bdacbaf830444f21f002` | 60 |
| `sql/2026-10-05_geo_source_library_hardening_preflight_verification.sql` | `83be16b8dcbeadfbadeafb867c7d26846230463c8d88c0e55ebaa8e342f16e79` | 56 |

### Docs

| ファイル（`projects/geopolitical_monitor_dashboard/` からの相対） | SHA256 | 行数 |
|---|---|---|
| `docs/source-library-monitoring-migration-guide-2026-10-05.md` | `946e3ecd64e3140bc6bce79a87bfd77b06fb3fb9100788cb2b1ec7400a6f10b5` | 33 |
| `docs/source-deduplication-candidates-2026-10-05.md` | `51d1521f5fbfa061b38f0c06e5306f4bf5aa7ac7db08a1b413f47a06494127bf` | 30 |
| `docs/issue-report-studio-startup-schema-check-503-2026-10-05.md` | `92159742388413972368b928dcdb2bf89be16250552763787fdb03688f4a8aa9` | 41 |
| `docs/SOURCE_INTELLIGENCE_V1_FEEDBACK.md` | `962e064e5d962b74af098cbd60eec23077431d6c24b0fc713074d0aba9eb6ec8` | 30 |

### snapshotのみに含まれる検証資料（`verification/`）
`db_state_at_freeze.json` / `schema_snapshot_postgrest.json` / `schema_declared_in_migrations.md` / `schema_pgcatalog_query.sql` /
`api_routes.txt` / `freeze_check.py` / `test_results/`（pytest・型チェック・ビルド・READING_UI_V1 Gold確認）。

## Version

| 項目 | 値 |
|---|---|
| Freeze | `SOURCE_INTELLIGENCE_V1` |
| **Query Expansion Version** | `query_expansion_v1`（`geo_source_query_expansion.QUERY_EXPANSION_VERSION`。応答の`query_expansion.version`に出力） |
| **Alert Validation Version** | `alert_validation_v1`（`geo_alert_validation.ALERT_VALIDATION_VERSION`。評価の`validation.version`に出力） |
| Freshness 閾値 | `STALE_AFTER_DAYS = 3`（`geo_source_library_service`）。直近30日にデータが無い場合も stale |
| Alert LLM上限 | `MAX_LLM_CALLS_PER_EVALUATION = 6`（曖昧な記事のみ。同一見出しは1回） |
| Query Expansion上限 | 展開語 `MAX_EXPANDED_TERMS = 8`、1語 `MAX_TERM_LENGTH = 60` |
| Python / Node | Python 3.10.11（FastAPI 0.139.2 / pydantic 2.13.4）／ Node v24.18.0 / TypeScript 5.9.3 |

## SQL migrations（実DBへ適用済み）

適用はユーザーがSupabase SQL Editorで手動実施（2026-10-05）。マイグレーションランナーは無い。
**表現について**: DB-1は `theme_id` のNOT NULL制約を解除し、**DB-7は既存FKの定義を変更**し、DB-3bはデータ（`source_id`）を書き込む。
したがって全てが「追加のみ（additive）」ではなく、**「既存データを破壊しないschema migration」**として管理する。
各ファイルの末尾にRollback手順がある。

| 順 | ファイル | 内容 | 既存への影響 |
|---|---|---|---|
| DB-1 | `2026-10-05_geo_source_library_db1_registry.sql` | `geo_source_registry`に7列追加（region / media_type / is_primary / description / reliability / registered_by / archived_at）、**`theme_id` のNOT NULL解除**、`geo_normalize_domain()`、`geo_source_tags` / `geo_source_theme_links` / `geo_source_domains` 新設、legacy `theme_id` を `geo_source_theme_links` へ複製、URLからprimary domainを投入 | NOT NULL制約の緩和。他は追加。複製・投入は冪等 |
| DB-2 | `2026-10-05b_…_db2_source_sets.sql` | `geo_source_sets` / `geo_source_set_members` | 追加のみ |
| DB-3 | `2026-10-05c_…_db3_signal_source_id.sql` | `geo_external_signals.source_id`（NULL可・FK `ON DELETE SET NULL`・partial index）、`geo_external_signal_source_matches`（紐付け監査表） | 列追加のみ |
| DB-3b | `2026-10-05d_…_db3b_safe_backfill.sql` | **データ書き込み**: 一意に確定できるdomainだけ `source_id` を設定（ambiguous/unmatchedはNULL） | `source_id` と監査表へINSERT/UPDATE（26件）。Rollback SQLあり |
| DB-4 | `2026-10-05e_…_db4_monitoring.sql` | `geo_monitoring_themes` / `geo_monitoring_alerts` | 追加のみ |
| DB-5 | `2026-10-05f_…_db5_research_context.sql` | `geo_report_projects.research_context`（jsonb, NULL可） | 列追加のみ |
| DB-6 | `2026-10-05g_…_db6_source_set_archive.sql` | `geo_source_sets.archived_at` ＋ partial index | 列追加のみ |
| **DB-7** | `2026-10-05h_…_db7_theme_fk_set_null.sql` | **`geo_source_registry.theme_id` のFKを `ON DELETE CASCADE` → `ON DELETE SET NULL` へ変更**（DOブロック1つで原子的。`theme_id`列とデータは変更しない） | **既存FK定義の変更**。件数は前後で不変 |
| DB-8 | `2026-10-05i_…_db8_alert_validation_audit.sql` | `geo_monitoring_alerts` に `candidate_rule` / `validation_result` / `validation_reason` | 列追加のみ |

Preflight / Verification SQL: `2026-10-05_geo_source_library_preflight.sql`、`…_verification.sql`、`…_hardening_preflight_verification.sql`（H1〜H5 / V-H1〜V-H4）。
DB-7の確認: 適用前後のH1件数（source_total / theme_id_kept / source_theme_links）の一致をユーザーが確認し、さらに
**TESTのThemeを削除する実験で `ON DELETE SET NULL` を実証**した（Sourceは残り `theme_id` がNULLになり、`geo_source_theme_links` の行だけが消えた）。
この実験のためにTEST用Theme 1件（`[TEST] Hardening FK check`）を作成して削除した（本番Themeは削除していない）。

## Data model

| テーブル | 役割 | 主な制約 |
|---|---|---|
| `geo_source_registry` | 組織共通Source Master。`archived_at`でsoft delete。`theme_id`はlegacy（NULL可） | `theme_id` → `geo_themes` **ON DELETE SET NULL** |
| `geo_source_tags` | Tag。`origin`(ai/human/system)・`is_confirmed`でAI提案とHuman確定を区別 | UNIQUE(source_id, tag_type, lower(tag_value)) |
| `geo_source_theme_links` | Source ↔ Theme 多対多（`relation_type`: manual/ai_recommended/usage_derived）。Tagとは別物 | UNIQUE(source_id, theme_id, relation_type) |
| `geo_source_domains` | Domain Alias（`is_primary`・`match_enabled`）。Article→Source照合の根拠 | UNIQUE(source_id, domain)。domain単独のuniqueは付けない（ambiguous検出のため） |
| `geo_source_sets` / `geo_source_set_members` | 共通Source Set（Theme専用ではない）。`archived_at`でsoft delete。memberに`sort_order` | member: PK(source_set_id, source_id) |
| `geo_external_signals.source_id` | Article → Source 逆参照（NULL可、FK SET NULL） | partial index |
| `geo_external_signal_source_matches` | 「なぜこのArticleがこのSourceになったか」（match_method: exact_domain/alias_domain/manual/crawler） | PK(external_signal_id) |
| `geo_monitoring_themes` | ユーザー向けMonitoring。Theme候補・Source Set・Actors・Keywords・感度・頻度。`latest_assessment`にFreshness/状態/Alert検証の監査を保存 | FK SET NULL（候補・Theme・Source Set） |
| `geo_monitoring_alerts` | 理由(`reason`)必須のAlert。fingerprintで重複防止。検証監査列 | UNIQUE(monitoring_theme_id, fingerprint) |
| `geo_report_projects.research_context` | Research Studio Projectへ引き継ぐTheme/Query/source_ids | jsonb |

Source Registry の `source_tier` / `source_type` など既存列は維持（`is_primary`は一次情報フラグ、`media_type`はより細かい媒体種別）。
スキーマ資料: `verification/schema_snapshot_postgrest.json`（実DBの列・型・NULL可・PK・FK参照先。PostgREST OpenAPIから取得）、
`verification/schema_declared_in_migrations.md`（migrationが宣言するindex・FK ON DELETE rule）。
**実DBのindexとFKのON DELETE ruleを直接確認するSQL**は `verification/schema_pgcatalog_query.sql`（Supabase SQL Editorで実行し、結果JSONを
`verification/schema_pgcatalog_snapshot.json`として追加保存する。PostgREST経由では取得できないため、Freeze作成時点では未取得 → 下記「未完了の補足」参照）。

## API routes（38本、全て `/api/geo/...`）

Mutation（POST/PATCH/DELETE）は全て既存の `verify_ios_identity`（`X-IOS-Credential`）で保護。GETは未認証でも可。
migration未適用の環境ではテーブル/列なしを503＋案内で返す（旧スキーマ互換: 一覧はlegacy modeで動作）。

```
GET    /api/geo/monitoring-themes
POST   /api/geo/monitoring-themes
POST   /api/geo/monitoring-themes/alerts/{alert_id}/acknowledge
POST   /api/geo/monitoring-themes/check-all
POST   /api/geo/monitoring-themes/from-candidate
GET    /api/geo/monitoring-themes/{monitoring_theme_id}
PATCH  /api/geo/monitoring-themes/{monitoring_theme_id}
DELETE /api/geo/monitoring-themes/{monitoring_theme_id}
GET    /api/geo/source-library
POST   /api/geo/source-library
GET    /api/geo/source-library/meta
POST   /api/geo/source-library/recommend
GET    /api/geo/source-library/schema-status
GET    /api/geo/source-library/search
GET    /api/geo/source-library/{source_id}
PATCH  /api/geo/source-library/{source_id}
DELETE /api/geo/source-library/{source_id}
POST   /api/geo/source-library/{source_id}/domains
PATCH  /api/geo/source-library/{source_id}/domains/{domain_id}
DELETE /api/geo/source-library/{source_id}/domains/{domain_id}
POST   /api/geo/source-library/{source_id}/restore
POST   /api/geo/source-library/{source_id}/suggest-tags
POST   /api/geo/source-library/{source_id}/tags
DELETE /api/geo/source-library/{source_id}/tags/{tag_id}
POST   /api/geo/source-library/{source_id}/tags/{tag_id}/confirm
POST   /api/geo/source-library/{source_id}/theme-links
DELETE /api/geo/source-library/{source_id}/theme-links/{link_id}
POST   /api/geo/source-research/projects
GET    /api/geo/source-research/projects/{project_id}/context
POST   /api/geo/source-research/projects/{project_id}/run
GET    /api/geo/source-sets
POST   /api/geo/source-sets
GET    /api/geo/source-sets/{source_set_id}
PATCH  /api/geo/source-sets/{source_set_id}
DELETE /api/geo/source-sets/{source_set_id}
POST   /api/geo/source-sets/{source_set_id}/members
POST   /api/geo/source-sets/{source_set_id}/restore
GET    /api/geo/theme-explorer
```

## Source Matching Policy（固定）

Article → Source の紐付けは次の通り。**この方針はV1の仕様として固定**する。

1. **exact unique domain 等で、1つのSourceに一意に確定できる場合のみ** `geo_external_signals.source_id` を付与する。
   正規化は lowercase → scheme除去 → userinfo除去 → hostname抽出（port・path除去） → `www.`除去 → 末尾`.`除去。
   照合対象は `geo_source_domains`（`match_enabled=true`、未archivedのSource）。match_method は `exact_domain`（primary domain）/ `alias_domain`。
2. **複数Sourceが同じdomainを持つ（ambiguous）場合は `source_id = NULL` のまま**にする。**推測でSourceを割り当てない**。
3. **Sourceとして登録されていないdomainの記事を、無理に既存Sourceへ寄せない**（unmatched = NULL）。
4. 紐付けの根拠は必ず `geo_external_signal_source_matches` に残す（監査可能）。
5. archived Sourceへは紐付けない。

## Freshness Policy（固定）

**記事0件 ≠ Stable**。MonitoringではData Freshnessを先に判定し、停止中の「新着なし」を「安定」「変化なし」と表示しない。

| 条件 | 状態（`assessment.state`） | 意味 |
|---|---|---|
| Freshなデータ ＋ 該当記事0件 | `no_new_articles` | データは動いているがテーマに該当する記事が無い（**安定ではない**） |
| Stale / Unknown（直近3日超に新着なし、または取得実績なし） | `no_fresh_data` | **データ更新なし／状況判定不能**。Alert判定・Stable判定は行わない |
| Freshなデータ ＋ 関連記事あり ＋ 変化なし | `stable` | |
| Freshなデータ ＋ 関連記事あり ＋ 軽微な変化（low Alert） | `watch` | |
| Freshなデータ ＋ 関連記事あり ＋ 重要な変化（medium/high Alert） | `changed` | |

Freshnessは `published_at`（indexあり）の下限つきクエリで算出する（`geo_external_signals`に`created_at`のindexは無く、`created_at`での並べ替えは禁止）。

## Alert Policy（固定）

Alertは **Candidate Detection → Context Validation → Alert** の構造（`geo_alert_validation`、version `alert_validation_v1`）。

- **Candidate Detection**: 語Hitのルール（制裁・政策変更・軍事行動・交渉、記事量急増、一次情報Source新着）。
  単一Sourceの1記事だけの語Hitではeventの Alertにしない（2つ以上の独立Source、または一次情報Sourceが必要）。
- **Context Validation**: 「イベントが実際に発生したと記事が述べているか」だけを判定する。
  **除外対象**: negation（否定） / hypothetical（仮定） / forecast（予測・可能性・備え） / historical reference（過去の回想） /
  denial（否認） / discussion only（協議・議論しただけで発動の事実が無い）。
- **Rule-based guard優先**。**曖昧なケースだけLLM Validation**（`yes/no/unclear`）。1回の評価でLLMは最大6件・同一見出しは1回。
- **precision優先**。**LLM上限を超えた曖昧記事、LLMが失敗/unclearの記事は、Alertにしない**（Known Behavior）。
- 全Alertは `reason`（なぜAlertか）・`candidate_rule`・`validation_result`・`validation_reason` を保存し、除外された候補とその理由は
  `latest_assessment.validation.excluded` で確認できる。

## Research Policy（固定）

- 検索語は **Original User Query ＋ Translation ＋ Query Expansion**（`query_expansion_v1`）。出自（original / translated / expanded）を区別して保持・表示する。
  最終回答の問い自体はユーザーの原文のまま変更しない。
- **Guard**: 許可＝翻訳・同義語・正式名称・略語・一般的な英語表現。**禁止＝新しい国/地域・Actor・年（時間範囲）・Themeを勝手に追加しない**。
  LLMの自己申告（added_*）があれば展開語を全破棄、Queryに無い年・数字／国・地域名を含む語・長すぎる語は破棄し、`guard.dropped`に理由を残す。
- **選択Source制約**: 内部記事は選択Source/domainに限定。Web検索はdomainを`site:`で誘導し、返ったcitationを選択domain（subdomain含む）で必ず後段フィルタ。
- 検索結果の区別:
  - **`matched`** ＝ Research Evidence候補（異なる検索語の一致数が閾値以上。英語側の語が多いほど閾値を上げ最大3、原文側は最大2）。
  - **`supplementary`** ＝ **関連性未確認の補完情報**（matchedが少ない時のみ直近記事で補う）。**回答のEvidenceとして使用しない**（回答LLMへ渡さない）。UIでも区別して表示。
- **`matched`が0件の場合は、根拠のないResearch回答を生成しない**（補完記事は参考表示のみ）。
- **Reference**は必ず表示: Article title / Source / Published date / Original URL（Web検索の引用は日付不明があり得る）。
- Web検索ツールが返す要約は記事全文ではない。回答LLMには「Web検索ツールによる要約。記事本文ではない」と明示して渡す。

## Freeze時点の実DB状態

取得: `2026-10-05T16:59:31.671431+09:00`（`verification/db_state_at_freeze.json`、読み取りのみ）。

| 項目 | 件数 |
|---|---|
| Source Registry | **112**（本番 **108** ＋ TEST 4。TESTは全てarchived） |
| 〃 本番のarchived | 0 / うち`registered_by`空のExpert AI自動登録 76 件 |
| Source Domain Alias | **99**（本番 95 ＋ TEST 4） |
| Source ↔ Theme link | **108**（すべて`manual`＝legacy theme_idの複製。TESTのlinkは0） |
| Source Tags | 6 |
| external_signals（Article）総数 | 7729（収集は継続中のため増加する） |
| **`source_id`付きArticle** | **26**（match監査表 26行。TESTのSourceに紐付いた記事は0） |
| **exact_domain** | **26**（alias_domain 0 / manual 0） |
| 一意に確定できるdomain数 | 87 |
| **ambiguous domain** | **4** |
| Monitoring Theme | **7**（本番 **0** ＋ TEST 7。全て`archived`） |
| Monitoring Alert | 9（すべてTEST由来・確認済み。検証監査列つき2件） |
| Source Set | **4**（active **0** / archived **4**。本番 0 ＋ TEST 4） |
| Source Set member | 12 |

### TESTデータによる件数差の説明
Source Registry の **112件＝本番108件＋TEST 4件**、Domain Aliasの **99件＝本番95件＋TEST 4件** は、E2E検証で作った`[TEST]` Sourceによる差で、
**本番データの変更ではない**。本番のSource（108件）・Domain Alias（95件）・Source↔Theme link（108件）・legacy `theme_id`保持（108件）は、
migration前後およびE2E前後で不変（`freeze_check.py --db` で確認可能）。
Monitoring Theme・Source Setが**本番0件**なのは、V1時点でユーザーがまだ本番のMonitoring/Source Setを作成していないため（現在の数値は全てTEST）。
本番のMonitoring/Source Setを作成するとこれらの件数は増える。

### ambiguous domain（未結合が正常）

| domain | 候補Source | 未結合のまま残っている記事数（Freeze時点） |
|---|---|---|
| `aljazeera.com` | Al Jazeera / www.aljazeera.com | 21 |
| `cnbc.com` | CNBC / www.cnbc.com | 2 |
| `commonslibrary.parliament.uk` | UK House of Commons Library / commonslibrary.parliament.uk | 0 |
| `eur-lex.europa.eu` | eur-lex.europa.eu / eur-lex.europa.eu | 0 |

上記4つは**未結合（`source_id = NULL`）のままが正常**。Canonical Source / Deduplication（V1.1以降）で解消するまで、推測で結合しない。

## TEST Data状態（Freeze時に再確認済み）

`[TEST]%` の**有効データは0件**（SQL/APIで検索して確認）:

| 対象 | 件数 | 有効 |
|---|---|---|
| Source | 4 | **0**（全てarchived） |
| Source Set | 4 | **0**（全てarchived） |
| Monitoring | 7 | **0**（全てarchived） |
| Alert | 9 | 未確認 **0**（全て確認済み） |
| Theme Candidate | 2 | 却下されていないもの **0**（全てDISMISSED） |
| Research Project | 7 | **0**（全てarchived） |
| `geo_themes`の`[TEST]` | 0 | 0（FK検証用に作成した1件は検証後に削除済み） |

DB上には論理削除済みとして残してある（物理削除はしていない。ただしFK検証用のTEST Theme 1件のみ、`ON DELETE SET NULL`を実証する目的で削除した）。

## Test results（Freeze直前の最終実行）

| 項目 | 結果 |
|---|---|
| Python full test | **2524 PASS**（`verification/test_results/pytest_final.txt`、139.5秒） |
| Frontend Type Check（`tsc --noEmit`） | **PASS**（エラー0、`typecheck_final.txt`） |
| Frontend Build（`vite build`） | **PASS**（`build_final.txt`） |
| Browser E2E | **PASS**（サイドバー順、テーマ探索3タブに[TEST]なし、モニタリング／情報ソースDBの既定一覧に[TEST]なし、Source Detail、Report Studio、Expert AI画面、READING_UI_V1表示、旧Connector画面。API/ページエラーは「最新セッションのintake未作成による404」1件のみ＝既存挙動） |
| 実DB E2E（API経路・X-IOS-Credential） | PASS（Source CRUD/Tag/Source Set/Monitoring/Alert検証/日本語→英語検索/Theme→Monitoring・Research/Source→Research/Source Set archive/ON DELETE SET NULL/Alert監査列） |

Alert Validation（実記事のas-ofリプレイ、保存なし）: 検証なし26件 → 検証あり12件。台湾テーマでは否定・予測の4候補を全て除外し、制裁・軍事行動の実際の出来事のAlertは維持。
日本語→英語検索: 旧（原文の語のみ）は関連0件 → 新は関連する英語記事2件（GlobalSecurity「PLA activities in the waters and airspace around Taiwan」）。

## 既存Freezeの保護（確認結果）

| 対象 | 確認 | 結果 |
|---|---|---|
| READING_UI_V1 | snapshot 25ファイルの自己整合（SHA256SUMS）／snapshotと現行原本14ファイルの一致 | **変更なし** |
| Gold 4件（Hormuz / US-China Semiconductor / Saudi-Israel / TSMC） | `READING_UI_V1/verification/freeze_check.py` を再実行し、Freeze時の基準（`gold_after_old_cache_removal.json`）と比較 | **4件ともpresentation SHA256一致**、カード数・LLMコメント数・流れ段数も同一、GET時のLLM呼び出し0回、決定的 |
| Expert AI reasoning core / Frozen Responses | `geo_expert_*.py`・`geo_ios_expert_*.py`・`geo_ios_situation_presentation_*.py`・`geo_ios_reading_*.py`・`geo_ios_analysis_*.py`・`expert_review_cache/` | 本作業開始後の更新**0件** |
| ブラウザ | 最新のAnalysis Session画面（読み方UI）が表示される | OK |

## Operational Notes（プロセス自体はFreeze対象外）

現在の8001番APIは手動起動したプロセス。再現方法:

- 作業ディレクトリ: `projects/geopolitical_monitor_dashboard`
- Python環境: `C:\Users\269811\python\.venv\Scripts\python.exe`（Python 3.10.11、FastAPI 0.139.2）。**システムPython（`C:\Program Files\Python310`）にはfastapiが無いので使わない**
- 起動: `python -W ignore api_server.py` ＋ 環境変数 **`API_PORT=8001`**（未指定は8000。フロントは`http://127.0.0.1:8001`固定）。`API_HOST`未指定は`127.0.0.1`
- 設定: `config.json`（`supabase.url` / `supabase.key` / `azure.*` / `proxy.*` / `ssl.verify`）。コミット・外部出力禁止。社内プロキシ環境のためSSL検証が無効な場合あり
- 認証: Mutationは`X-IOS-Credential`（`geo_ios_identities`のcredential）。ブラウザではlocalStorageの`geo_ios_credential`
- フロント: `eco-digest-spark` で `npx vite dev`（5173）。ビルドは`npx vite build`
- 起動時間: 起動時のスキーマチェックで数分かかることがある。Report Studioが503なら再起動（Known Issue 2）
- 実行中のAPIプロセスは、Freeze直前に追加したバージョン定数（挙動に影響しない）を反映する前のコードで動いている場合がある。再起動すれば同じ

## Known Behavior

- Alertはprecision優先。曖昧でLLM上限超過/失敗/unclearの記事はAlertにしない（取りこぼしより誤検知を避ける）。
- ambiguous domainの記事は未結合。未登録domainの記事も未結合（Sourceを登録すれば以降の照合対象になる）。
- Web検索ツールの引用は公開日が取れず「日付不明」になることがある。

## Known Issues / Future Work（Freeze Blockerではない）

1. `GET /api/geo/knowledge/entities/resolve` が500（`/entities/{entity_id}` が先に定義されルートが競合、既存バグ・今回未変更）
2. Report Studio startup schema checkがDB負荷時に失敗し、再起動までReport Studio全体が503（`docs/issue-report-studio-startup-schema-check-503-2026-10-05.md`）
3. Source duplicate／Canonical Source未対応（`docs/source-deduplication-candidates-2026-10-05.md`。V1.1以降。自動登録Sourceが108件中76件）
4. Web Search要約は記事全文ではない（回答の根拠が弱くなり得る）
5. Web sourceの日付不明ケース
6. Alertはprecision優先（取りこぼしあり）
7. Source Set保存・再利用UI（API・DBは対応済み、UIは未実装）
8. 高度なSource Ranking
9. Alert sensitivity詳細設定
10. Digest配信
11. Tag横断filter（Tagクリックで一覧を絞り込む）

その他の既知事項: 一覧は総件数を返さずページ送りのみ／Region・Languageの候補一覧APIなし（自由入力）／
日本語Queryの展開はLLMに依存（LLM不可時は原文の語のみ）／Theme Candidateの生成（sync）は本Freezeの対象外で、本番ではまだ0件。

## 未完了の補足（Freeze有効性には影響しない）

- **実DBのindexとON DELETE ruleのpg_catalog snapshot**: PostgREST経由では取得できないため、取得SQL（`verification/schema_pgcatalog_query.sql`）を同梱した。
  実行結果JSONを`verification/schema_pgcatalog_snapshot.json`として追加保存すること（追加後は`SHA256SUMS.txt`を更新）。
  それまでの代替として、`schema_snapshot_postgrest.json`（実DBの列・FK参照先）、`schema_declared_in_migrations.md`（宣言）、
  DB-7のON DELETE SET NULL実証実験の結果（本Manifest）で確認できる。

## Snapshot構成と検証

```
freeze/SOURCE_INTELLIGENCE_V1/
  MANIFEST.md              本Manifestの複製
  SHA256SUMS.txt           snapshot内の全ファイル（MANIFEST.md / SHA256SUMS.txt / MANIFEST.sha256を除く）
  MANIFEST.sha256          MANIFEST.mdのSHA256
  src/                     Backend・Frontend・tests（原本と同じ相対パス）
  sql/                     DB-1〜8、Preflight/Verification
  docs/                    移行ガイド、Source重複候補、Report Studio 503 Issue、FEEDBACK
  verification/            DB状態・スキーマsnapshot・APIルート・freeze_check.py・test_results/
```

検証: `cd freeze/SOURCE_INTELLIGENCE_V1 && sha256sum -c SHA256SUMS.txt` ／ `python verification/freeze_check.py --db`（プロジェクト直下から）。

## Freeze後の運用

SOURCE_INTELLIGENCE_V1 の機能を評価期間中に**直接変更しない**。問題・改善案は `docs/SOURCE_INTELLIGENCE_V1_FEEDBACK.md` に記録する。
修正が必要になった場合は `SOURCE_INTELLIGENCE_V1_1` 等の次版として扱う（Query Expansion / Alert Validationのversionも上げる）。
