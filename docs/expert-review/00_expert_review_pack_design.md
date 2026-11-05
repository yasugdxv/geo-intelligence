# Expert Review Pack — Session 3 Expert Calibration Package

## 0. 目的

新機能追加ではなく、これまで構築した(Expert Reasoning/Thinking OS + Geopolitical
Foundation + Actor Intelligence + Media Intelligence + Specialist/Physical Evidence +
Evidence Weighting)を1つの分析へ統合し、専門家がAIのReasoning過程(Key Point→Key
Player→Priority推定→Media/Signal解釈→Scenario導出)をレビューできる形にする。
レビュー結果は次のExpert AIチューニングデータとする。**専門家の実際の回答が得られる
前に、AIをExpert Judgmentの代用にしない。**

## 1. Implementation Freeze(実装Version固定)

本リポジトリはgit管理下にない(`Is a git repository: false`)ため、主要実装ファイルの
SHA256フィンガープリント(先頭16桁)で固定する。専門家レビュー完了までこれらのファイルは
変更しない。

| ファイル | fingerprint(sha256先頭16桁) |
|---|---|
| geo_expert_engine.py | 41c62395d7c69697 |
| geo_expert_schema.py | 82d175408b9224a7 |
| geo_expert_quality_guard.py | 6d317a48894bc061 |
| geo_actor_intelligence_service.py | b5ac9fb61803c6c3 |
| geo_media_intelligence_service.py | aa61f22f460b32a5 |
| geo_knowledge_item_schemas.py | 5ab8d2935eebf4b7 |
| schemas/geo_expert_ai_response.schema.json | b372ebb312f974c5 |
| geo_persistent_preference_service.py | 47e2b31ccec1b1c5 |
| geo_foundation_pack_service.py | 11dd58266c423f91 |

- **Prompt revision**: `PROMPT_VERSION = "geo-expert-v1.2-session2"`
  (`geo_expert_engine.py`定数、M9/M9.1変更後も同じ値のまま——プロンプト文言追加は
  行ったがPROMPT_VERSION自体のインクリメントはしていない点に留意)
- **Thinking OS revision**: `docs/geopolitical-expert-session2-reasoning-upgrade.md`
  時点の内容(Session2 Reasoning Upgrade完了版)+本タスクで追加した
  `_MEDIA_INTELLIGENCE_PACK_NOTE`/`_ACTOR_MEDIA_CROSS_REFERENCE_NOTE`
  (M9/M9.1、`geo_expert_schema.py`内)
- **Actor Intelligence revision**: 10 Actor Historical Intelligence Build
  (`actor_intelligence_10actor_build_2026-08-30_gate3`系、全件`status=candidate`)
- **Media Intelligence revision**: Media Intelligence Build Pilot1
  (`media_intelligence_zelenskyy_pilot_2026-08-31`、全件`status=candidate`、
  Episode15+Pattern5+Profile5)
- **Quality Guard revision**: M9/M9.1で追加した3チェック
  (`_historical_pattern_not_asserted_as_certain_fact`/`_cross_intelligence_
  contradiction_not_silently_dropped`/`_evidence_attribution_not_conflated`)+
  `apply_confidence_weighting_fix`決定的補正を含む版
- **Feature Flag状態**: `GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED=true`(test-mode、
  `profile_lookup_statuses=("active","candidate")`)、`GEO_EXPERT_MEDIA_
  INTELLIGENCE_ENABLED=true`(test-mode、`media_intelligence_statuses=("active",
  "candidate")`)——本番既定はいずれもOFF/activeのみ、本Pack用の分析のみ明示的に
  上書き
- **Azure deployment/model**: `Global-AzureOpenAI-DEV-gpt-5.1`
- **Execution date**: 2026-08-31

## 2. Frozen AI Analysis(本Packが参照する唯一のAI出力)

M9のD_integrated条件(Actor ON+Media ON)の実Azure実行結果
(`docs/media-intelligence/m9_raw_responses/m9_D_integrated_result.json`、
response_id: 実行時に採番、quality_evaluation.evaluation_id記録済み)へ、
M9.1の決定的補正(`apply_confidence_weighting_fix`)を事後適用したものを
「Frozen AI Analysis」として本Pack全体で使用する(`m9_D_integrated_CORRECTED_
for_review_pack.json`、scratchpad保存)。**新しいAzure呼び出しは行っていない
——既に実行済みの同一response_jsonへ、既存の決定的Python関数を後掛けで適用しただけ**
である。適用結果、`media_editorial_context[0].hypotheses[1]`
(匿名情報源による「トランプ側の意図的リーク」仮説)のconfidenceが高→中へ
自動的に引き下げられ、`confidence_adjustment_reason`が記録された。この
before/afterの差分自体を、本Pack内「M9 Self-Correction Case」(14章)の教材として
そのまま使用する。

## 3. Review Case

第一Review Case: **Zelenskyy Oval Office Clash → Jeddah negotiation**
(2025-02-28〜2025-03-11)。理由: Actor Intelligence(Zelenskyy/Trump/Putin)・
Media Intelligence(15 Episode+5 Pattern、Global/Ukraine/Russia/専門家/第三国の
視点を含む)・Specialist/Physical Evidence(ISW/ACLED)が単一Caseで揃う。第2 Case
(高市・台湾)は今回作成しない(依頼書3章の指示通り、最初から複数Case大量Packは
作らない)。

## 4. 成果物一覧

```
docs/expert-review/
  00_expert_review_pack_design.md        (本ファイル)
  01_zelenskyy_blind_review_input.md     (Part A: Blind Expert Assessment用)
  02_zelenskyy_ai_review_pack.md         (Part B: AI Review Pack)
  03_expert_review_form.md               (Agree/Partial/Disagree等の記入欄)
  04_expert_ai_gap_schema.json           (AI vs Expert差分Schema)
  05_session3_review_guide.md            (実施ガイド・provenance・Gap Routing)
```

## 5. Part A/Part B分離の徹底(Anchoring防止)

Part A(Blind Expert Assessment)ではAI Primary Key Point/Candidate Key Points/
Actor評価/Media仮説/Scenario/confidence/A-B-C-D比較結果のいずれも専門家へ提示しない。
専門家自身の判断が確定・記録された後にのみPart B(AI Review Pack)を提示する。
