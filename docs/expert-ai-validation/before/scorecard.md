# Before Benchmark — 5-Case Frozen Baseline

## 重要な手順上の注記(正直な報告)

Case2〜5の実行は、Case1完了後に発生した一時的なネットワークエラー(DNS解決失敗)への
対応として再実行した際、**バックグラウンド実行中に並行してP0修正のコード編集を
開始してしまった**。そのため、Case2〜5は「完全にPre-Fix状態のコード」では実行されて
いない可能性があり(実行タイミングによって新旧コードが混在)、**Case1のみが確実に
Pre-Fix状態でのクリーンなBefore Benchmarkである**。この手順ミスは実装作業を優先し
検証プロセスの分離を怠ったことに起因する。Case2〜5のデータは参考情報として残すが、
「Before」としての厳密な比較対象にはCase1のみを用いる。

## Freeze情報(Case1実行時点)

| ファイル | fingerprint(sha256先頭16桁) |
|---|---|
| geo_expert_engine.py | 41c62395d7c69697 |
| geo_expert_schema.py | 82d175408b9224a7 |
| geo_expert_quality_guard.py | 6d317a48894bc061 |
| geo_actor_intelligence_service.py | b5ac9fb61803c6c3 |
| geo_media_intelligence_service.py | aa61f22f460b32a5 |
| geo_knowledge_item_schemas.py | 5ab8d2935eebf4b7 |
| schemas/geo_expert_ai_response.schema.json | b372ebb312f974c5 |

Model: Global-AzureOpenAI-DEV-gpt-5.1。Feature Flag: Actor Intelligence ON
(test-mode、`profile_lookup_statuses=("active","candidate")`)、Media Intelligence OFF
（5 Caseとも既存のMedia Intelligence Pilotが対象とする事案と異なるため）。
実行日: 2026-08-31。mode=case_analysis、profile=deep。Query textは依頼書の元の
（Alternativeを暗示する）表現をそのまま使用（Neutral版はAfter/Neutral段階で使用）。

## Case1: Trump-Xi US-China Semiconductor & AI Controls（唯一のクリーンなBefore）

- **Token/Call**: 2 calls, 73,070 prompt + 15,284 completion = 88,354 total tokens
- **Primary Key Point**: 「米国の対中AI・半導体規制の強度・適用範囲（輸出管理・投資
  規制）の強度と適用範囲」（要旨）
- **Control type**: `institutionally_constrained`、`controlling_entity_ids: null`
  （P0-2が要求するcontrol_structure_summaryの有無は個別確認が必要——後述4章参照）
- **Key Actor Profiles**: `actor_ref` = `trump`, `xi`
- **Quality Guard initial_failed_checks**: `fact_claims_have_source_ref`,
  `stakeholders_grounded_in_source_pack`（いずれも本テスト構成の既知の制約、
  P0固有の問題ではない）
- 本Caseでは`actor_intelligence_provenance_present`/`actor_pattern_applicability_
  present`/`hypothesis_pruning_present`等の新規Guardは**まだ実装前**のため対象外
  （このBefore実行時点でこれらのチェックは存在しない）。

## Case2〜5（参考データ、Before基準としては非採用）

実行時のコード状態が不確定なため、Token数・Key Point内容の参考記録に留める。
詳細な失敗チェック内容の解釈（特に`evidence_attribution_not_conflated`の出現）は、
実装途中のコードによるものである可能性が高く、Pre-Fix状態の挙動として解釈しない。

| Case | Primary Key Point(要旨) | control_type | controlling_entity_ids |
|---|---|---|---|
| Case2 Putin | プーチンとNATO/西側枠組みに対する脅威認識と、それに基づくウクライナの安全保障的地位の再定義 | mixed | Putinのentity_id(populated) |
| Case3 Netanyahu | 人質解放における責任論と国内政治圧力の必要性 | mixed | Netanyahuのentity_id(populated) |
| Case4 MBS | MBSの王位継承と国内政治は既存基盤の必要性 | actor_controlled | MBSのentity_id(populated) |
| Case5 Kim | 北朝鮮の核・ミサイル能力を交渉の基盤とする戦略・意図 | actor_controlled | Kimのentity_id(populated) |

**注記**: Case4/5で`control_type=actor_controlled`かつ`controlling_entity_ids`が
populated（空ではない）だった点は、依頼書が説明する「P0-2 Key Point Control
Structure」の症状（actor_controlledなのにcontrolling_entity_ids=[]）を、この
特定の再実行では明確には再現できなかったことを意味する。P0-2の決定的補正
(`normalize_control_type`)自体は設計として妥当かつ有用だが、**今回のBefore
再実行データはユーザー提示の症状パターンを1:1で裏付けるものではない**——コード
汚染の影響で確定的な判断はできない、という限界として正直に記録する。

## 結論

Before Benchmarkとして完全に信頼できるのはCase1のみ。P0修正の効果測定は、主に
Neutral Prompt Validation（`docs/expert-ai-validation/neutral/`）の結果を主軸とする。
