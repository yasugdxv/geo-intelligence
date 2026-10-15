# Media Intelligence Build — M0 既存アーキテクチャ調査

## 1. 結論サマリー

**Media/Information Reasoningの「per-case・揮発層」は、Session2 Reasoning Upgrade
（`docs/geopolitical-expert-session2-reasoning-upgrade.md`）で既に実装済みである。**
今回のMedia Intelligence Buildで新規に作るべきは、その揮発層が参照する
**「安定層」(Media Source Registry / Media Profile / Historical Media Episode /
Media Pattern Candidate)** であり、ゼロから設計するものではない。

## 2. 既に実装済み（再利用対象、重複実装しない）

### 2-1. per-case Media/Information Reasoning（`schemas/geo_expert_ai_response.schema.json`）

`media_source_context.media_editorial_context[]`（L408-521）に、今回の依頼書
Section3/13/14/15が要求する構造がほぼそのまま実装済み:

- `observed[]`: Observable専用（意図語を含まない事実表現のみ）— 依頼書Section13の
  Observed定義と完全一致。
- `hypotheses[]`: `category`（editorial_framing/source_signaling/audience_reaction）
  で3分類、`status`(supported/partially_supported/weak/unsupported/contradicted/unknown)、
  `corroboration_basis`/`corroboration_target`で「出来事の確認」と「意図の確認」を
  区別 — 依頼書Section15(Source-of-Sources)と同じ思想が`corroboration_target`
  （`hypothesis_itself` vs `underlying_event_only`）で既に実装済み。
- `narrative_deviation[].source_attribution`: `named/anonymous_official/
  anonymous_unspecified/unattributed` — 依頼書Section14と完全一致。
- `source_of_sources_note`: 依頼書Section15の文言そのまま既存フィールド名。

### 2-2. Quality Guard（`geo_expert_quality_guard.py`）

以下が既に実装・稼働中(L266-450付近):
- `_media_observed_fields_free_of_inference_language` — Observed/Hypothesis混入防止
- `_media_editorial_hypotheses_not_uniformly_confirmatory` — 「全会一致の陰謀論的解釈」検出
- `_media_beneficiary_not_treated_as_intent_proof` — 依頼書Section37 Beneficiary Guard
- `_media_hypothesis_evidence_grounded_check` / `_anonymous_sourced_hypothesis_confidence_capped`
  — Evidence Grounding・匿名ソースのConfidence Cap（corroboration_target区別込み）
- `_media_omitted_actor_analysis_grounded` — 省略Actor分析のEntity Grounding
- `_situation_actor_assessment_*` 系 — Media単体ではないがActor×Issue評価との整合チェック

**したがって依頼書Section37「Quality Guard」で列挙されたチェックの大部分は追加実装不要。
新規に必要なのは、今回追加するStable Media Profile/Historical Episode/Pattern
Candidate側のGrounding Check（後述4章）のみ。**

### 2-3. Prompt注入の型（`geo_expert_engine.py`）

`_format_actor_intelligence_block(pack)`(L251-)がActor Intelligence Packを
Prompt文字列へ変換する既存パターン。Media Intelligence Packも同型の
`_format_media_intelligence_block`を新設すれば足りる（Phase M9で実装、今回は設計のみ）。
`ACTOR_PACK_CHAR_BUDGET`と同様のToken予算制御をMedia側にも適用する。
CallBudget（Stage1+Stage2+repair<=3）は変更しない——Media Intelligence PackもFoundation
Pack同様、追加Azure Callなしで既存Stage1/Stage2のuser_promptへ注入するのみ。

## 3. 既存だが今回の目的には不向き（file-based代替が必要）

### 3-1. `geo_source_registry`（DB table）

`sql/2026-08-02_geo_core_schema.sql` L55-69で定義。要点:
```
theme_id  uuid not null references geo_themes(theme_id)  -- NOT NULL、テーマ1件に紐付け必須
source_tier text check (in '一次','二次','三次','四次')
source_type text check (in 'official_site','news','sns','commercial_data','other')
-- Phase2Aで追加: trust_tier/perspective/geographic_scope/thematic_scope/languages/
--   access_method/automation_allowed/content_storage_allowed/... (すべて固定型カラム、
--   JSONB自由拡張列は存在しない)
```
Media Sourceは特定のtheme一つに属するものではなく（Reutersは全テーマ横断）、
`media_type`/`lens[]`/`institutional_perspective`/`use_for[]`/`do_not_use_for[]`/
`factual_reliability`等、依頼書Section6-9が要求する多軸メタデータを持たせるには
新規カラムを複数追加するALTER migrationが前提になる。Actor Source Registry構築時と
同じ結論: **既存`geo_source_registry`は流用せず、file-based
（`docs/media-intelligence/media_source_registry.json`）で構築する。DB migration不要。**

### 3-2. `geo_knowledge_items.knowledge_type` CHECK制約

現行の許可値一覧（`sql/2026-08-29_geo_report_review_loop.sql`等）:
```
country_foundation, actor_profile, party_profile, relationship_assessment,
historical_path_dependency, precedent, mechanism, reasoning_rule, indicator,
foundation_update, case_finding, leverage_card,
actor_profile_update, relationship_update, dependency_update, sanctions_update, (他)
```
`media_profile`/`media_episode`/`media_pattern`に相当する値は存在しない。
**この決定は今回のGate1では確定させず、Gate2（M5-M8実装フェーズ）着手前に
ユーザーへ判断を仰ぐ、オープンな設計論点として残す**（5章参照）。

## 4. Gate2以降への設計論点（今回は決定しない、オープンイシューとして提示）

Stable Media Profile / Historical Media Episode / Media Pattern Candidateの永続化方式には
2案がある:

- **案A（Actor Historical Episodeと同じ手法）**: `knowledge_type="precedent"`を再利用し、
  `PRECEDENT_SCHEMA`へ`primary_source_id`（`geo_entities`のUUIDではなく、file-based
  Media Source RegistryのSource ID文字列）等の新規optionalフィールドを追加するのみ。
  DB migration不要。ただし「Precedent」という名前の意味論をActor Episode・伝統的な
  先例カードに加えてMedia Episodeにも拡張することになる。
- **案B（新しいknowledge_type追加）**: `media_profile`/`media_episode`/`media_pattern`を
  CHECK制約へ追加する軽量migration（他のknowledge_type追加時と同型のALTER、データ列追加は
  不要）。意味論は明確になるが、依頼書Section1が除外する「DB migrationを前提とした
  大規模再設計」に触れるため、今回のGate1報告でユーザー判断を仰ぐ。

いずれの案でも`status="candidate"`起点・Human Review必須・`activate=True`を
レビュー済みの代替根拠にしないという既存ガバナンスは変更しない。

## 5. Evidence/Claim/Documentとの関係（既存基盤の再利用）

`geo_connector_base.py`（Document/Raw Snapshot/Ingestion Run管理）、
`geo_claim_service.py`（Claim化・Chunk化）、`geo_official_crawler.py`は既存の
実データ取得パイプラインとして存在するが、依頼書Section1が「全Sourceの大量Historical
Ingestion」「全媒体の常時Crawler」を除外scopeとしているため、今回はこれらのConnector
基盤を**呼び出さない**。Media Episodeの`supporting_evidence_ids`等は既存
`geo_claims`/`geo_documents`のIDを引用する形にとどめる（Actor Historical Episode
構築時と同じ方針、新規Claim自動生成はしない、依頼書Section3-2/Section25と整合）。
