# 地政学専門家AI Actor Intelligence Layer（Phase AI-1〜AI-3）

本ドキュメントは、既存の地政学専門家AI v1.2（段階実行方式）へ「Actor Intelligence Layer」を追加した
Phase AI-1（Store）〜AI-3（Expert Reasoning統合）の完了時点の状態を記録する。
承認済みPlan: `C:\Users\269811\.claude\plans\pure-drifting-cosmos.md`

## 1. Purpose

Foundation Pack・Case Context・Persistent Preferenceは「Reference Data（Entity/Relationship/
Claim/Precedent等）をそのまま渡す」レイヤーである。Actor Intelligence Layerは、これらの一段上に
「誰がこの局面を実際に動かせる／止められるのか、そのActorは何を狙っていて何が譲れないのか」という
構造化知識を専用に扱う。既存Knowledge DBを正本として再利用し、DBマイグレーションなしで実現した。

## 2. アーキテクチャ

```
Situation/Case → Relevant Entities（Issue/Relationship由来）
              → Actor Entity Type Filter（意思決定主体ではないEntityを除外）
              → Candidate Actors（Profileの有無と無関係に集める）
              → Key Actor Selection（Profileの有無と無関係にスコアリング）
              → Actor Intelligence Retrieval（選ばれたActorについてのみ、情報の有無を確認）
              → Temporal / Issue Relevance / Evidence Coverageで評価
              → sufficient / partial / insufficient（その都度算出、非永続）
              → Actor Intelligence Pack（Freshness・文字数Budget込み）
              → Expert Reasoning（staged/monolithic両方）
```

**最も重要な設計判断**: 「Actor Profileそのものの充実度」と「あるSituationに対するActor
Intelligenceの充足度」を明確に分離した。Key Actor SelectionはProfile保有の有無から独立に行い
（情報が豊富なActorだけが選ばれる偏りを避けるため）、`actor_intelligence_status`
（sufficient/partial/insufficient）はProfileへ永続化せず、Pack構築のたびにReference Date・
Temporal Filter・Issue Relevanceを見て都度算出する。

## 3. 既存DBとの関係（新規マイグレーションなし）

| Actor Intelligence概念 | 再利用した既存要素 |
|---|---|
| Actor Identity | `geo_entities`（既存の`external_ids` jsonbカラムへ`{"actor_intelligence_id": "donald_trump"}`を保存。正本の取得APIは`entity_id`ベース、`actor_intelligence_id`はSeed/名前解決専用） |
| Core Profile/Decision Pattern/Current Posture/Issue Position | `geo_knowledge_items`（`knowledge_type=actor_profile`）の`structured_content`へオプショナルキーとして追加。`geo_knowledge_item_schemas.ACTOR_PROFILE_SCHEMA`拡張 |
| Historical Behavior/Precedent | `geo_knowledge_items`（`knowledge_type=precedent`）の既存`PRECEDENT_SCHEMA`へ`primary_actor_entity_id`（オプショナル）を追加 |
| Actor Relationship | `geo_relationships`をそのまま再利用（新テーブルなし） |
| Evidence/Fact-Inference分類 | `geo_claims`/`geo_knowledge_items`の既存`information_class`（5値）へマッピング（新分類軸は作らない） |
| Situation→Actor紐付け | `geo_entity_issues`（既存junction）をそのまま再利用 |

Fact/Inference分類マッピング:

| 指示書の分類 | 既存`information_class` |
|---|---|
| observed_fact | `observed_fact` |
| reported_assessment | `external_assessment` または `actor_claim` |
| expert_interpretation | `internal_expert_assessment` |
| ai_inference / hypothesis | `internal_hypothesis` |

## 4. 新規ファイル

- `geo_actor_intelligence_service.py` — Actor Identity解決、Actor Profile取得/Seed（内容フィンガープリントによるidempotent化）、Candidate Actor取得（Issue-linked＋1-hop Relationship展開、Top-K上限あり）、Actor Entity Type Filter、Key Actor Selection（決定的スコアリング、Profile保有と独立）、Freshness/Temporal Filter、Actor Intelligence Pack構築（文字数Budget込み）
- `scripts/seed_actor_intelligence.py` — 優先4名（Trump/Putin/Xi/Kim Jong Un）のSeed。既定は`candidate`のみ作成、`--activate`でactive化。内容フィンガープリントにより再実行してもcandidateが際限なく増えない
- `tests/test_geo_actor_intelligence_service.py`（22件）、`tests/test_geo_actor_intelligence_integration.py`（4件）

## 5. 変更ファイル

- `geo_knowledge_item_schemas.py` — `ACTOR_PROFILE_SCHEMA`/`PRECEDENT_SCHEMA`拡張（既存フィールドは変更なし、全てオプショナル追加）
- `geo_expert_engine.py` — Feature Flag（`GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED`、既定OFF）、`_format_actor_intelligence_block`、`run_expert_request`/`run_expert_request_staged`両方への組み込み（Stage0/`if case_id:`ブロック内、`known_ids`への合流、`actor_intelligence_status`のレスポンスへの決定的注入）
- `geo_expert_schema.py` — `_ACTOR_INTELLIGENCE_NOTE`（`_KEY_STAKEHOLDER_FIRST_NOTE`と同じく常時静的にsystem promptへ含める。文言自体で「Packが提供されている場合は」と条件分岐）
- `schemas/geo_expert_ai_response.schema.json` — `key_actor_profiles[]`へ`why_actor_matters`/`available_leverage`/`unavailable_or_costly_cards`/`vulnerabilities`/`relevant_decision_patterns`（オプショナル）、トップレベルへ`actor_intelligence_status`（オプショナル、コード側で注入しLLMには生成させない）
- `geo_retrieval_service.py` — `get_issue_ids_for_case`新設（既存2箇所の重複ロジックは変更しない）
- `eco-digest-spark/src/lib/api.ts` — `GeoExpertStakeholder`の`position`/`leverage`/`ability_to_move_or_block`（既存バックエンドとの表示ギャップ修正）、`GeoExpertKeyActorProfile`の`persistent_preference_change`＋Actor Intelligence新規5フィールド、`GeoExpertResponseJson.actor_intelligence_status`
- `eco-digest-spark/src/routes/geo.expert.tsx` — Key Actor Profileカードへ新規5フィールドの最小表示、`actor_intelligence_status`バッジ

`geo_expert_quality_guard.py`は変更していない。新規5フィールドは説明文字列でありEvidence ID参照を含まないため、既存のフィールド別Evidence走査（ハードコード、汎用ではない）に含める必要がない。

## 6. Feature Flag

| 環境変数 | 既定値 | 説明 |
|---|---|---|
| `GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED` | 未設定=false | Actor Intelligence統合の有効化 |
| `GEO_EXPERT_ACTOR_MAX_ACTORS` | 未設定=5 | Packに含める最大Actor数 |

Flag OFF時も、Output Schema拡張自体は常時スキーマに存在するため、LLMは新規5フィールドをnullとして
出力する（既存の`persistent_preference_change`追加時と同様の軽微なtoken増、ユーザー承認済み）。
Flag ONで実際に変わるのはPackがプロンプトへ注入され、LLMがこれらのフィールドへ実質的な内容を
埋めるようになる点のみ。`actor_intelligence_status`はLLM出力ではなくコード側決定的注入のため
Flag OFF時は常にレスポンスJSONに含まれない（キー自体が無い）。

既存の`GEO_EXPERT_EXECUTION_MODE`（既定staged）・`GEO_EXPERT_PP_CANDIDATES_ENABLED`（既定false）は
変更していない。monolithicは削除していない。

## 7. Seed（実データ、実DB投入済み）

`python scripts/seed_actor_intelligence.py --activate`を実行し、以下4名を実Supabase DBへ
Core Profileのみ（広く確立された構造的事実に限定）でactive化した:

| Actor | actor_intelligence_id | entity_id |
|---|---|---|
| Donald Trump | donald_trump | c93580fd-99b1-44d9-b924-beb89a2e8378 |
| Vladimir Putin | vladimir_putin | b4133e96-303f-4853-8cc3-3793e8145913 |
| Xi Jinping | xi_jinping | f34571d3-4742-41b7-8648-5b3ee13b7720 |
| Kim Jong Un | kim_jong_un | a154f1d1-77fc-4263-8493-9913c6b3bdc1 |

Current Posture/Issue Position/Decision Patternは今回のSeedでは収録していない（`needs_enrichment`に
明記）。実確認できていない具体的な現在の姿勢・案件別立場を創作しない方針（既存`geo_hormuz_pilot_seed.py`
と同じ方針）のため。

既存の実Case（Hormuz/Iranテーマ、3件）のうち、実際に関連する2名を該当Issueへ`geo_entity_issues`で
実タグ付けした（新規Caseは作らず、既存の実データへ最小限の追加のみ）:
- Donald Trump → Case1（`a26560a5-...`、米国のホルムズ海峡自由航行意図）・Case2（`d9d4b72a-...`、米国の海洋安全保障提供がLeverageとして機能するか）— いずれも既に「United States」（国レベル）がタグ付けされている案件に、人物レベルの意思決定主体を追加
- Xi Jinping → Case3（`99464374-...`、中国のイランへの圧力水準）— 既に「China」がタグ付けされている案件に追加

Vladimir Putin / Kim Jong Unは、現時点の実Case（すべてHormuz/Iranテーマ）に自然な関連が無いため、
どの実Caseにもタグ付けしていない（Known Gaps参照）。

## 8. 実データOFF/ON比較結果（実Azure OpenAI API、2026-08-28実行）

`GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED`のOFF/ONで、既存3実Case（`mode=case_analysis`,
`profile=standard`既定、staged execution）を実行した。

| Case | Actor | Flag | 呼び出し回数 | 総token | actor_intelligence_status | Key Actor Profileに登場 |
|---|---|---|---|---|---|---|
| Case1（a26560a5） | Trump | OFF | 2 | 74,120 | (なし) | いいえ |
| Case1（a26560a5） | Trump | ON | 2 | 77,459 | partial | **はい** |
| Case2（d9d4b72a） | Trump | OFF | 2 | 66,281 | (なし) | いいえ |
| Case2（d9d4b72a） | Trump | ON | 2 | 67,381 | partial | **はい** |
| Case3（99464374） | Xi | OFF | 2 | 67,834 | (なし) | いいえ |
| Case3（99464374） | Xi | ON | 2 | 67,881 | partial | いいえ |

**観察**:
- 呼び出し回数（実Azure API呼び出し数）はOFF/ONで変化しない（Stage1+Stage2の2回のまま）。Packは
  プロンプトへの追加テキストのみで、CallBudgetを消費しない。
- token増加はCase1で+4.5%、Case2で+1.7%、Case3で+0.07%と、Packの内容量（Precedent/Relationship/
  Issue Position有無）に応じて変動する。いずれも軽微。
- Case1・Case2ではFlag ON時にTrumpが実際にKey Actor Profileへ登場し、Packに投入した
  Core Profile（関税・経済的レバレッジの選好、議会・予算制約等）を反映した具体的な
  `why_actor_matters`/`available_leverage`/`unavailable_or_costly_cards`/`vulnerabilities`/
  `relevant_decision_patterns`をLLMが生成した（下記例）。
- Case3ではFlag ON時もXi/Chinaは（OFFと同様）Key Actor Profileに登場しなかった。Case3の
  設問は「イラン自身の行動閾値」を主題としており、中国の圧力は副次要因として扱われた
  ため、LLMがXiを主要Key Actorに選ばなかったと考えられる。これはPackが「無条件に
  登場させる」ものではなく、LLMの判断を尊重する設計が機能している例として記録する。
- `actor_intelligence_status`は3ケースとも`partial`（Core Profileはあるがそのissueの
  Issue Position/Current Postureが無いため）。永続化しない設計（7章参照）が意図通り
  機能していることを実データで確認した。
- `completeness_status`（`incomplete_after_repair`）はOFF/ON双方で同じであり、Actor
  Intelligenceの導入によるQuality Guard結果への悪影響は無い（既存のv1.2既知課題、
  Closeoutドキュメント記載の課題であり本フェーズのスコープ外）。

**Case1、Flag ON時の実際のLLM出力例（Donald Trump）**:
> why_actor_matters: 「トランプ政権はイスラマバードMoUの当事者であり、米海軍の封鎖解除・再封鎖を
> 決める権限を持つため、イランの選択的支配を抑止し得る唯一の軍事主体である...」
> available_leverage: ["米海軍による封鎖解除・再封鎖", "対イラン制裁・経済圧力",
> "湾岸諸国への安全保障費用負担要求"]
> relevant_decision_patterns: ["安全保障・貿易案件で関税・費用負担を交渉カードとして用いるパターン", ...]

比較後、`GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED`は既定のOFF（未設定）へ戻して完了している。

## 9. テスト結果

`python -m pytest tests/ -q` → 344 passed, 6 failed（既知の無関係6件、`test_geo_official_crawler.py`/
`test_sustainability_article_selector.py`、Actor Intelligence追加前から存在する既存課題）。
新規テスト26件（Unit 22件＋Integration 4件）はすべてグリーン。

## 10. Known Gaps（次フェーズ検討課題）

1. **Putin/Kim Jong Unが実Caseにタグ付けされていない**: 現状の実Case（3件）はすべてHormuz/Iran
   テーマであり、両名に自然な関連が無いため、Actor Intelligence Pack経由の実データ検証が
   できていない。Ukraine/Taiwan/North Korea関連の実Caseが作られた際に検証する。
2. **Current Posture/Issue Position/Decision Patternが未収録**: 4名ともCore Profileのみで、
   具体的な現在の姿勢・案件別立場・複数先例からのDecision Patternは未整備（`needs_enrichment`
   に明記済み）。専用の調査・Evidence収集を経てから追加する。
3. **Versioning Granularity**: 現状、1つの`actor_profile`知識アイテムにCore Profile/Decision
   Pattern/Current Posture/Issue Positionsを全て格納している。Phase AI-4（自動更新）着手前に、
   Current Postureのみ高頻度更新するようになった場合の更新粒度（同一アイテム内に留めるか、
   概念ごとに別アイテム/別knowledge_typeへ分割するか）を再評価する。
4. **1-hop Relationship展開の実データ検証**: 今回の3ケースでは新たな1-hop展開によるCandidate
   Actor追加は発生しなかった（既存のIssueタグ付けで十分だったため）。Relationshipデータが
   より充実した案件で、この経路が実際に機能するかの追加確認が望ましい。
5. **v1.2既存Known Gaps**: `docs/geopolitical-expert-v1.2-staged-execution-closeout.md`記載の
   6項目は本フェーズでは対応していない（スコープ外）。

## 11. 次フェーズ（Phase AI-4〜AI-6、今回は未実装）

- **Phase AI-4（Actor Signal/Update Engine）**: ニュース・実データからCurrent Posture/Issue
  Position/Decision Patternを半自動的に更新するCandidate生成の仕組み（Persistent Preference
  Candidateと同様、Feature Flag既定無効・人間レビュー必須）。着手前にKnown Gap 3の
  Versioning Granularityを再評価する。
- **Phase AI-5（専用UI）**: Actor Intelligence専用の閲覧・レビューUI（現状は既存Key Actor
  Profileカードへの最小表示のみ）。
- **Phase AI-6（Expert Review学習）**: Actor Intelligenceの予測精度・Expert Reviewからの
  フィードバックを蓄積する仕組み。
