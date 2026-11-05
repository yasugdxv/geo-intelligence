# Source Library / Monitoring 基盤 — Migration適用ガイド（2026-10-05）

**状態**: SQLファイルは作成済み。**Supabaseへは未適用**（適用はユーザーがSQL Editorで手動実施）。
**方針**: 全て additive / backward-compatible。既存列・既存テーブルの削除・改名なし。各Phaseは独立して適用・Rollback可能。

## 適用順序

| 順 | ファイル | 内容 | 既存への影響 | 前提 |
|---|---|---|---|---|
| 0 | `2026-10-05_geo_source_library_preflight.sql` | **読み取り専用**。件数・FK・domain一意率・既存有無の確認 | なし（SELECTのみ） | — |
| 1 | `2026-10-05_geo_source_library_db1_registry.sql` | geo_source_registry拡張（7列）、`theme_id` NOT NULL解除、`geo_normalize_domain()`、`geo_source_tags` / `geo_source_theme_links` / `geo_source_domains` | `theme_id`がNULL可に（列・FK・cascadeは維持）。`is_primary`を`source_tier='一次'`から初期化。legacy theme_idをtheme_linksへ複製、URLからprimary domainを投入（いずれも冪等） | Preflight確認済み |
| 2 | `2026-10-05b_geo_source_library_db2_source_sets.sql` | `geo_source_sets` / `geo_source_set_members` | なし（新規表のみ） | DB-1 |
| 3 | `2026-10-05c_geo_source_library_db3_signal_source_id.sql` | `geo_external_signals.source_id`（nullable, FK on delete set null, partial index）、`geo_external_signal_source_matches`（紐付け根拠の監査表） | 列追加のみ。既存行は全てNULLのまま | DB-1 |
| 4 | `2026-10-05d_geo_source_library_db3b_safe_backfill.sql` | **データ書き込み**: 一意に確定できるdomainのみ source_id を設定（ambiguous/unmatchedはNULL） | `geo_external_signals.source_id`と監査表へのINSERT/UPDATE。Preflight P8の`exact_unique`件数と一致するはず | DB-1, DB-3 |
| 5 | `2026-10-05e_geo_source_library_db4_monitoring.sql` | `geo_monitoring_themes` / `geo_monitoring_alerts` | なし（新規表のみ） | DB-2（geo_source_sets）。geo_theme_candidates/geo_themesは既存 |
| 6 | `2026-10-05f_geo_source_library_db5_research_context.sql` | `geo_report_projects.research_context`（nullable jsonb）追加 | 列追加のみ。既存Report Studio Project/Pipeline無改修 | — |
| 7 | `2026-10-05_geo_source_library_verification.sql` | **読み取り専用**。各Phase適用後に該当ブロックを実行 | なし | 各Phase |

> DB-3b（backfill）は件数が多いとSQL Editorのタイムアウトに注意。その場合は期間条件を足して分割実行（ファイル冒頭コメント参照）。

## 安全設計のポイント
- **domain照合は一意確定のみ**: 同一domainが複数Sourceにある場合は紐付けない（`source_id`=NULL）。推測しない。
- **Source Domain Alias**: `geo_source_domains`（match_enabled切替可）。Registry URLだけに依存しない。
- **照合根拠の監査**: `geo_external_signal_source_matches`（exact_domain / alias_domain / manual / crawler）。
- **Source削除はsoft delete**（`archived_at`）。Article・Source Set・Monitoringの参照は維持。FKも`on delete set null`。
- **未適用でも既存機能は動く**: APIは旧スキーマでも一覧を返す（legacy mode）。新機能は503＋案内を返す。

## 意図的に変更していない点（要判断）
- `geo_source_registry.theme_id` の FK は **`on delete cascade` のまま**。Themeを削除すると、そのtheme_idを持つlegacy Sourceが連鎖削除される（従来挙動）。新規Source（theme_id=NULL）は影響を受けない。Source Master化の観点では`on delete set null`が望ましいが、既存挙動を変えるため今回は変更していない（変更する場合は別途SQLを用意）。
- `geo_expert_research_service.get_or_create_domain_source` は、Expert AI Research時に **ドメイン名をnameとするSourceを自動登録**する（既存の挙動）。Source Libraryに「domain名だけのSource」が混ざる可能性があるため、登録後に`registered_by`が空のものを確認すること。

## Rollback
各SQLファイル末尾のコメント参照（DB-3bは「このbackfillで入れた分だけ戻す」SQLあり）。
