# 10 Key Actors — Phase A 現状棚卸し（Coverage Baseline）

2026-08-30、ユーザーより正式に確定された10 Key Actorsリスト。Phase Aとして、コード変更前に
実DB・既存Seed・既存Actor Intelligence実装を確認した現状（推測・再選定は行っていない）。

## 正式10 Actorリスト（ユーザー確定）

| # | Actor | 主な分析対象 |
|---|---|---|
| 1 | Donald Trump | 米外交・NATO・中国・ロシア・中東、取引型外交 |
| 2 | Vladimir Putin | ロシア・ウクライナ・NATO・制裁・核抑止 |
| 3 | Xi Jinping | 米中・台湾・経済安全保障・中国外交 |
| 4 | Kim Jong Un | 北朝鮮・核・米朝・中朝関係 |
| 5 | Volodymyr Zelenskyy | ウクライナ戦争・停戦・欧米支援 |
| 6 | Benjamin Netanyahu | Israel・Gaza・Iran・米国との関係 |
| 7 | Mohammed bin Salman (MBS) | Saudi・湾岸・石油・Iran・米中バランス |
| 8 | Emmanuel Macron | EU・NATO・欧州戦略的自律・仏外交 |
| 9 | Friedrich Merz | Germany・EU・NATO・再軍備・対露政策 |
| 10 | 高市早苗（Takaichi Sanae） | 日本・日米同盟・中国・台湾・経済安全保障 |

## Phase A調査で判明した事実（実DB確認済み、2026-08-30時点）

- 過去の会話・plan・seedスクリプトに「10人」という正式リストは存在しなかった（`scripts/
  seed_actor_intelligence.py`の`PRIORITY_ACTORS`および`docs/geopolitical-expert-actor-
  intelligence-phase-ai1-ai3.md`はいずれも「優先4名」とのみ記録）。**今回ユーザーから直接
  10人リストの提示を受け、これを正式なものとして確定した**。
- `geo_entities`テーブル全件（13件）を確認した結果、`entity_type=person`は4件のみ（Donald
  Trump/Vladimir Putin/Xi Jinping/Kim Jong Un）。**残り6名（Zelenskyy/Netanyahu/MBS/
  Macron/Merz/高市早苗）はentity自体が未作成**（名前の表記ゆれ・別entity_typeでの登録も含め
  広く検索したが該当なし）。
- 既存4名も`actor_intelligence_seed_phase_ai1`によるbareなseed（`core_profile`のみ存在、
  `persistent_preference`/`strategic_communication_pattern`/`decision_patterns`/
  `issue_positions`/`current_posture`はいずれも空）であり、今回依頼のHistorical
  Intelligence（Episode/Pattern等）は未投入。

## Coverage Matrix（実データのみ、推測補完なし）

| Actor | actor_ref | entity_id | Core Profile | Objectives | Decision Patterns | Persistent Preference | Red Line | Negotiation Pattern | Strategic Communication | Historical Episodes | Current Posture | Evidence Coverage |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Donald Trump | donald_trump | c93580fd-99b1-44d9-b924-beb89a2e8378 | ○（最小限） | 未整備 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | 実質ゼロ |
| Vladimir Putin | vladimir_putin | 897329dc-fbfe-4e2f-b0a1-41e621c065c0 | ○（最小限） | 未整備 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | 実質ゼロ |
| Xi Jinping | xi_jinping | f34571d3-4742-41b7-8648-5b3ee13b7720 | ○（最小限） | 未整備 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | 実質ゼロ |
| Kim Jong Un | kim_jong_un | 64db3e84-45e3-4217-bf08-1018e1f8c7c5 | ○（最小限） | 未整備 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | 実質ゼロ |
| Volodymyr Zelenskyy | volodymyr_zelenskyy | 0196d7bd-c39d-4027-a73b-6dad95aa8357 | 無 | 無 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | ゼロ（entity作成済み、profile未着手） |
| Benjamin Netanyahu | benjamin_netanyahu | 585cc564-5a71-40ac-959b-3995d3a409c8 | 無 | 無 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | ゼロ（entity作成済み、profile未着手） |
| Mohammed bin Salman (MBS) | mohammed_bin_salman | c47c90e6-eda1-4f56-9c3e-2b3206fde945 | 無 | 無 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | ゼロ（entity作成済み、profile未着手） |
| Emmanuel Macron | emmanuel_macron | 6b8dabf0-3d77-4071-88f6-1aa66df2d7e6 | 無 | 無 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | ゼロ（entity作成済み、profile未着手） |
| Friedrich Merz | friedrich_merz | 123ea194-e93c-498a-a77b-72938143bb69 | 無 | 無 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | ゼロ（entity作成済み、profile未着手） |
| 高市早苗 | takaichi_sanae | e4ef0935-f7b0-430a-88be-cad36388f3b9 | 無 | 無 | 0件 | 無 | 無 | 無 | 無 | 無 | 無 | ゼロ（entity作成済み、profile未着手） |

## 関連する既存Entity（Actor本人ではないが、Relationship/Context構築に使える）

`geo_entities`に既存: United States（country）、Israel（country）、Saudi Arabia（country）、
United Arab Emirates（country）、Qatar（country）、Oman（country）、China（country）、Iran
（country）、Islamic Revolutionary Guard Corps (IRGC)（security_service）。これらはHormuz
Theaterの既存Pilot Case向けに投入されたもの。今回の10 Actorのうち、Netanyahu↔Israel、MBS↔
Saudi Arabia、Trump↔United States、Xi Jinping↔China等は既存Country Entityとの
Relationship紐付けが可能（新規Country Entity作成は一部で不要）。Ukraine/Germany/France/Japan
のCountry Entityは未作成（Zelenskyy/Merz/Macron/高市早苗の紐付け先が無い）。

## Phase A時点の結論

- 実装前提として、6名分の`geo_entities`新規作成（`get_or_create_actor_entity`等の既存関数を
  再利用）が必要（Gate 1の一部としてPhase B以降で対応）。
- 既存4名の`core_profile`は維持しつつ、Historical Intelligence（Episode/Pattern/Persistent
  Preference/Strategic Communication Pattern等）を追加投入する形で拡張する（既存active
  profileを破棄・再作成しない）。
- 本ドキュメントは「現状」の記録であり、この後Phase B（Source Registry構築）へ進む。
