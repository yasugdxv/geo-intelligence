# M9 — Actor × Media Intelligence Integration Architecture

## 1. 方針: 既存Reasoningの再実装ではなく接続

新しいReasoning Engine・新しい巨大Schemaは作らなかった。既存の`_format_actor_intelligence_
block`/`_format_media_intelligence_block`（M8.5実装済み）を同一`run_expert_request_staged`
呼び出し内で並行して注入し、既存Schemaフィールド（`situation_actor_assessment`の
`estimated_real_preference`/`genuine_or_tactical`/`media_salience_signal`/
`alternative_interpretation`、`media_editorial_context`、`key_point_assessment`）の中で
相互参照させるプロンプト指示を追加しただけである。**新しいAzure Callは追加していない**
（CallBudget<=3を維持、実測は全条件2 callsで完了）。

## 2. 実装した変更

### 2-1. Actor Intelligence: Test-mode Candidate Snapshot対応

`geo_actor_intelligence_service.get_actor_profile_item_by_entity_id`/`select_key_actors`/
`build_actor_intelligence_pack`へ、既定`statuses=("active",)`のまま後方互換を保ちつつ、
呼び出し元が明示的に`("active","candidate")`等へ上書きできる`profile_lookup_statuses`/
`profile_retrieval_status`パラメータを追加した。本番相当の挙動（`status=active`のみ）は
変更していない。Media Intelligence側（M8.5）と同じ設計思想。

**この変更が必要だった理由**: Pilot Case候補の事前確認（後述4章）で、Zelenskyy/Putin/
Trumpの持続的Preference・Decision Pattern・Strategic Communication Patternが全て
`status=candidate`（10 Actor Buildで作成、Human Review未了）であり、本番既定の
`status=active`のみで見ると、Putin/Trumpは骨格のみ（`confidence=判断不能`、
persistent_preference/decision_patterns/strategic_communication_pattern全て空）、
Zelenskyyに至っては該当レコードが**一件もない**ことが判明した。この状態のまま統合効果を
測定すると、Actor Intelligenceの寄与がほぼゼロとなり、統合効果を「Media単体の効果」と
誤認するリスクがあった（ユーザー指摘の懸念そのもの）。よってPilotに限り、Human Review未了
Candidateを含めて参照する明示的なテストモードを新設した。

### 2-2. Media Intelligence: 既存M8.5実装をそのまま再利用

変更なし。`geo_media_intelligence_service.build_media_intelligence_pack`/
`_format_media_intelligence_block`をそのまま使用。

### 2-3. Prompt指示の追加（Schema変更なし）

`geo_expert_schema.py`へ2つの静的ノートを追加（既存`_ACTOR_INTELLIGENCE_NOTE`等と同じ
パターンで、対応するPackが提供されていない場合は無害）:
- `_MEDIA_INTELLIGENCE_PACK_NOTE`: Media Pattern Candidatesが「過去の傾向」であり
  「今回の確定事実」ではないこと、Pattern適用可否は既存`status`enumで表現することを指示。
- `_ACTOR_MEDIA_CROSS_REFERENCE_NOTE`: Actor PackとMedia Packが両方提供されている場合の
  相互参照手順（6つの検討事項、依頼書Section12準拠）、および「Historical Pattern≠現在の
  確定事実」の再徹底、矛盾を黙って消さないことを指示。

### 2-4. Quality Guard 3件追加（`geo_expert_quality_guard.py`）

- `_historical_pattern_not_asserted_as_certain_fact`: 断定的表現の直接検出
- `_cross_intelligence_contradiction_not_silently_dropped`: 矛盾検出時にalternative_
  explanation/alternative_interpretationが空でないことを要求
- `_evidence_attribution_not_conflated`: Actor/Media PatternのDerived Intelligence
  knowledge_item_idが生Evidenceとして誤引用されていないか検証（`run_quality_guard`へ
  `derived_intelligence_ids`パラメータを追加、Actor/Media Packの`known_ids`から算出）

いずれも決定的Pythonチェックのみ、追加Azure Callなし。

## 3. Cross-domain Pattern Guard（Section21）

新規フィールドは追加せず、既存の`media_editorial_context.hypotheses[].status`
（supported/partially_supported/weak/unsupported/contradicted/unknown）と
`situation_actor_assessment[].alternative_interpretation_status`（plausible/weak/
unsupported/contradicted）を、他Caseから再利用したPatternの適用可否判定にもそのまま
使わせる方針とした（新しい`applies`/`partially_applies`等のenumを追加していない）。

## 4. Pilot Case選定前のActor Intelligence Coverage確認

| Actor | entity_id | status(active) | status(candidate) | persistent_preference | decision_patterns | strategic_comm_pattern | current_posture |
|---|---|---|---|---|---|---|---|
| Zelenskyy | 0196d7bd-... | **なし** | あり(93e8433c...) | ○ | 3件 | ○ | なし |
| Putin | 897329dc-... | あり(b4133e96...、骨格のみ) | あり(f39bf3e5...) | ○(candidateのみ) | 3件(candidateのみ) | ○(candidateのみ) | なし |
| Trump | c93580fd-... | あり(6fdacbcf...、骨格のみ) | あり(65bbd686...) | ○(candidateのみ) | 3件(candidateのみ) | ○(candidateのみ) | なし |

**結論**: 本番既定(`status=active`のみ)ではActor Intelligenceは実質的に空(Zelenskyy)〜
骨格のみ(Putin/Trump)。2-1節のTest-mode Candidate Snapshotが無ければ、この3Actorでの
統合効果測定はMedia単体の効果と区別できなかった。

## 5. Pilot Case

**Zelenskyy Oval Office Clash**（2025年2月28日〜3月11日）をそのままPilot Caseとして
採用した。理由: (1) Media Intelligence(Pilot1、15 Episode+5 Pattern+5 Profile)が
既に存在、(2) Actor Intelligence(candidate)がZelenskyy/Putin/Trumpの3名分存在、
(3) 実際のCase Contextを機能させるため、新規`geo_themes`(`ukraine_conflict`)+
`geo_issues`(`us_ukraine_alliance_relations`)+`geo_cases`（1件）を作成し、3 Entityを
Issue経由でリンクした（10 Actor全件・全Media Sourceへの横展開はしていない）。
