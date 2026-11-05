# Expert AI Retrieval V2 — 設計メモ（実装なし）

**作成日**: 2026-09-24
**ステータス**: 設計メモのみ。コード実装は行っていない。

---

## 背景

Past Case semantic validation（4 evaluable cases）で、以下5項目がRETRIEVAL_GAPと診断された。

1. 1979年イラン米大使館人質事件
2. ベイルート米海兵隊宿舎爆破事件
3. Trump第一期の対ロ政策・行動（ロシアゲート等）
4. TikTokを巡る米中交渉
5. EVを巡る米中政策・交渉

コーパス調査の結果、上記5項目はいずれも`geo_documents`/`geo_knowledge_items`に**該当コンテンツが一件も存在しない（CONTENT_ABSENT）**ことを確認した。したがって、Retrievalクエリ・ルーティングをどれだけ改善しても、今回評価対象の4 Past Caseではこの5項目自体の改善は測定できない。この理由により、**今回はRetrieval V2の実装を見送り**、設計メモのみ残す。

一方で調査の副産物として、Trump自身の「強行提示→即撤回」パターン（対中301条関税の対中エスカレーション→休戦）やTrumpの対イラン政策履歴（JCPOA離脱、2015年議会演説）など、Gold項目には直接一致しないが構造的に有用な`precedent`型knowledge_itemが**存在するが`status=candidate`/`retrieval_status=restricted`のため取得できない**ケースも見つかった。これは既存の`active`/`available`ゲート（`geo_retrieval_service.py`）が正しく機能している結果であり、Retrieval側で迂回すべきではない。これらの昇格判断（Human Gate #2、Learning Workbench経由）は本メモの対象外、別タスクとして切り離す。

---

## 既存アーキテクチャ（拡張の土台）

- `geo_expert_engine.py:1547` `build_foundation_context()` — 現状`geo_events`/`geo_documents`のみをキーワードAND一致で取得する、Actor/Issue無差別の"Layer1"実装。`_FOUNDATION_CONTEXT_V1_ENABLED`フラグでデフォルトOFF。docstring自体が「Actor/Issue keyword setsの分離は将来の拡張候補」と明記しており、本メモが想定する拡張の自然な差込先。
- `geo_actor_intelligence_service.py:566` `_relevant_precedents_for_entity()` — 既にactor単位の`knowledge_type=precedent`取得ロジックを持つ（`geo_knowledge_service.list_knowledge_items`経由）。Issue軸の絞り込みは`geo_knowledge_service.list_knowledge_items_for_issue()`（`geo_knowledge_item_issues`テーブル経由）で可能。
- `geo_actor_intelligence_service.py:608` `_relevant_relationships_between()` — 既存の「過去のrelationship/precedent」取得ロジック（`geo_relationship_service.list_relationships_for_entity`、日付範囲チェック済み）。
- `pattern_applicability_assessment`（`key_actor_profiles[]`、`schemas/geo_expert_ai_response.schema.json`）— `pattern_summary`/`current_similarities`/`current_differences`/`exception_evidence_ids`/`applicability`(high/medium/low/uncertain)/`conclusion`という、まさに本メモが要求する「tag付きprecedent、適用条件の保持」の既存の型。新しいtag名を発明せず、この型を踏襲する。
- `_ACTOR_PATTERN_APPLICABILITY_NOTE`（`geo_expert_schema.py:1547`）— 「Historical Patternを参照したら自動適用しないこと」という、本メモが要求する非自動適用の既存prompt文言。

---

## Retrieval V2が持つべき汎用責務（実装時のtodo）

### A. Key Actor × Issue の過去行動
`_relevant_precedents_for_entity`を、対象entityだけでなく対象issue（`geo_knowledge_item_issues`経由）との交差で絞り込むよう拡張する。現状はactor単位のみで絞り込んでおり、issue軸の交差フィルタが未実装。

### B. Key Actorの類似局面での政策転換・交渉行動
Aと同じ取得経路を使うが、`knowledge_type=precedent`のうち「提示→撤回」「エスカレーション→デエスカレーション」等の構造を持つものを優先する（新しいknowledge_typeは作らず、既存`precedent`のstructured_content内で判別する）。

### C. 国内のinstitutional / political blocking actors
現状`geo_entities`に制度アクター（省庁・議会等）がほぼ存在せず、`institution_profile`型knowledge_itemもBIS以外に無い。Retrievalクエリの改善だけでは対応不可——これは**Entity/Knowledge投入（コンテンツ側）の課題**であり、Retrieval V2はデータが投入された場合に正しく拾える設計にしておくに留める（`geo_institution_intelligence_service.py`の既存`build_institution_intelligence_pack`が土台になる）。

### D. 異なるissueでも同じActorが使った類似交渉パターン
Aと同じ取得経路だが、issue軸を「一致」ではなく意図的に「不一致（cross-issue）」でフィルタする——同じactor・別issueの`precedent`を明示的に対象にする。

### E. 過去のrelationship / precedent
既存`_relevant_relationships_between`をそのまま`build_foundation_context`へ統合する（現状、両者は「v1では余計なことをしない」という設計判断により意図的に非統合）。

---

## 満たすべき制約（実装時に必ず守ること）

1. **`status=active AND retrieval_status=available`ゲートを絶対に迂回しない**。`geo_retrieval_service`が課しているこの条件は、Retrieval V2のどの拡張点でも例外なく適用する。`candidate`/`restricted`状態のknowledge_itemを新しい経路で取得可能にしてはならない。
2. 一度に大量の情報を渡さない——既存`_FOUNDATION_CONTEXT_MAX_ITEMS`/`_FOUNDATION_CONTEXT_MAX_CHARS`/`_FOUNDATION_CONTEXT_MAX_ITEM_CHARS`と同水準のcapを設ける。
3. 取得した各項目には、既存`pattern_applicability_assessment`型を踏襲し、最低限 `source`（knowledge_item_id等）・`time`（valid_from/as_of_date等）・`relevance`（なぜ関連するか）・`applicability`（適用可能性、high/medium/low/uncertain）・`why_may_not_apply`（適用条件が異なる可能性）を保持する。
4. **類似ケースを現在ケースへ自動適用しない**。`_ACTOR_PATTERN_APPLICABILITY_NOTE`と同じ「(a)過去の行動パターン(b)今回との共通点(c)今回との相違点(d)例外(e)適用可能性(f)結論」という決定手続きを踏襲する。
5. Case-specific hardcodeをしない——本メモのA〜Eはどのcaseにも適用される汎用責務であり、特定Gold文言・特定Actor名を条件式に埋め込まない。
6. 既存`build_foundation_context`のフォーマット規約（`- [{ref}] {date}: {body}`、または`_format_actor_intelligence_block`のrelevant_precedent行）を再利用し、新しいformatting規約を発明しない。

---

## 実装しないことの明示

- コード変更は一切行っていない（`geo_expert_engine.py`/`geo_actor_intelligence_service.py`/`geo_knowledge_service.py`のいずれも無変更）。
- `status=candidate`のknowledge_itemを`active`へ昇格する作業は行っていない、別タスクとして切り離す。
- Historical Mode等の新しいUI/APIは追加していない。

## 次にやるべきこと（別タスク）

1. コンテンツ投入（1979年人質事件等、Gold参照の史実そのもの）は本メモの範囲外。別途Document/Knowledge Ingestionタスクとして計画する。
2. `status=candidate`のprecedent knowledge_items（Trump301条関税パターン等、60件超）のHuman Gate #2昇格判断は、Learning Workbench等既存の承認フローを使い、Governance判断として別途実施する。
3. 上記1・2が進んだ段階で、本メモのA〜Eを実装し、同じ4 Past Case + New Blind Caseで改善を測定する。
