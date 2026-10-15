# Media Intelligence Build — M5 Media Profile / Episode / Pattern Schema

## 1. 実装内容

`geo_knowledge_item_schemas.py`へ3つの新規Schemaを追加し、`SCHEMAS_BY_KNOWLEDGE_TYPE`へ
登録した。DBの`geo_knowledge_items.knowledge_type` CHECK制約は
`sql/2026-08-31_geo_media_intelligence_knowledge_types.sql`で拡張済み（ユーザー適用
済み、テスト書き込み・削除で動作確認済み）。列追加・データ移行なし。

### MEDIA_PROFILE_SCHEMA（knowledge_type="media_profile"）
必須: `source_id`（`media_source_registry.json`のsource_idと一致）, `source_name`, `confidence`。
`media_type`/`lens`/`country_or_region`等の多軸メタデータはRegistry側が正本のため
重複させず、`source_id`で参照するのみ。安定Intelligence本体
（ownership_or_governance/institutional_perspective/primary_audience/
coverage_strengths/typical_source_types/named_vs_anonymous_tendency/
historical_framing_characteristics/strategic_signaling_relevance/known_limitations/
use_for/do_not_use_for/evidence_ids）を持つ。

### MEDIA_EPISODE_SCHEMA（knowledge_type="media_episode"）
依頼書Section12のSchema例に準拠。必須: `media_episode_id`, `source_id`, `event`,
`period_start`, `confidence`。`observable_framing`（Observedのみ、意図語禁止）と
`hypothesized_information_role`（Hypothesis）を明確に分離。既存
`media_editorial_context.hypotheses[]`と同じ`corroboration_basis`/
`corroboration_target`を持たせ、「出来事の確認」と「意図の確認」の混同を防ぐ。

### MEDIA_PATTERN_SCHEMA（knowledge_type="media_pattern"）
依頼書Section20のSchema例に準拠。必須: `pattern`, `when_it_applies`,
`evidence_episode_ids`(minItems:1), `confidence`。既存Actor `decision_patterns`と
同型の`pattern_id`/`pattern_role`(default/exception)/`default_pattern_ref`/
`exception_conditions`を持たせ、矛盾・反例の保持を構造的に強制する
（依頼書Section21）。

## 2. Knowledge Lifecycle

既存の`candidate → Human Review → active`をそのまま再利用する。
`geo_knowledge_service.create_knowledge_item(status="candidate", origin="manual")`で
作成し、`activate=True`は使わない（既存Actor Intelligence Buildと同じガバナンス）。

## 3. 非回帰確認

`pytest tests/` — 391 passed / 6 failed（既知の無関係failureのみ、Schema追加による
新規リグレッションなし）。
