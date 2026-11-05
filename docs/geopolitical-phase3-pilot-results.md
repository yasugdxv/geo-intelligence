# Phase 3-0 Pilot 実施結果（イラン・ホルムズ海峡Theater）

承認済みPlan: `C:\Users\269811\.claude\plans\pure-drifting-cosmos.md`

本ドキュメントは、Phase 3-0（実Theaterへの最小データ投入＋実案件3件のAI Draft生成）の
実施結果を記録する。**専門家レビューループ（AI Draft→社内の別専門家によるExpert Final→
Delta Extraction→Promote/Reinforce）は本Pilotの時点では未実施**であり、後日別途行う
（実施時は本ドキュメントを追記更新する）。

## 実施日
2026-08-21

## 投入データ（実DB、is_demo=false）

| 種別 | 件数 | 備考 |
|---|---|---|
| Theme | 1 | イラン・ホルムズ海峡危機 (`hormuz_strait`) |
| Entity | 9 | Iran/US/Israel/Saudi/UAE/Oman/Qatar/China/IRGC |
| Source | 9 | NPR/Axios/Bloomberg/CNBC/Al Jazeera/SCMP/UK Commons Library/TIME/Gulf International Forum |
| Document | 13 | 各Sourceの記事要約（Web検索で確認、原文全文未取得のものはその旨明記） |
| Claim | 13 | observed_fact/actor_claim/external_assessmentに分類 |
| Case-specific Issue | 3 | hormuz-reopening / us-gulf-security-cost / china-iran-influence（人為的な一本化はしていない） |
| Relationship | 4 | Iran-Oman / Iran-US / US-UAE / China-Iran |
| Knowledge Item (candidate) | 3 | country_foundation(Iran) / precedent(Islamabad MoU) / leverage_card(US→UAE) |
| Case | 3 | Case1/2/3（後述） |
| Hypothesis | 9 | 各Case: main×1 + alternative×2 |

## Knowledge Item candidate 3件のレビュー経緯

ユーザーによる詳細レビュー指摘（因果関係の誤り・出典不足・過度な一般化）を受け、
`geo_hormuz_pilot_seed_followup.py`で以下を実施済み:
- country_foundation: IRGCの実質支配に関する評価を、構造的事実から分離しExternal Assessment
  Claim（TIME 2026-04-21、Gulf International Forum）として登録
- precedent: 因果関係を「60日期限到来→崩壊」から「未解決の根本争点→暫定合意の脆弱性→
  期限前の再エスカレーション」へ訂正、出典の対応期間を明記
- leverage_card: UAEのHabshan-Fujairahパイプライン迂回能力（CNBC）を踏まえ`target_dependency`
  を是正、`objective`を一次報道で確認できる範囲に限定

3件とも**まだ`status=candidate`のまま**であり、candidate→activeの人間レビューは未実施
（`docs/geopolitical-phase3-pilot-runbook.md`のRunbookで別途実施）。

## Case実行結果

| Case | 問い | case_id | AI Draft response_id | Quality Guard |
|---|---|---|---|---|
| Case1 | イランはイスラマバードMoUに基づき自由通航を許容するか、実効支配・覇権的関与を継続するか | `a26560a5-f265-424f-8677-ada5774be085` | `2113d5e4-804a-4064-b2cd-5764b33f4cce` | 8/9 pass |
| Case2 | 米国の海上安全保障提供は湾岸国への費用負担Leverageとして機能するか | `d9d4b72a-42a6-491b-8e86-43c1a3420ba5` | `d4348d82-009a-43c5-9b73-4cb4151eec6e` | 7/9 pass |
| Case3 | 中国はイランの行動をどの程度制約できるか | `99464374-a6f6-45ce-9a3b-c9d24fcb4bf3` | `ea3f69dc-953b-40b9-a5d3-a5306a643a7d` | 8/9 pass |

各CaseのAI Draftの`main_scenario`/`alternative_scenarios`は`geo_case_hypotheses`へ
手動反映済み（1 case=main×1+alternative×2の3件、計9件）。

### Case1: 一言見立て
「イランはホルムズ海峡の自由通航を一般原則として受け入れる方向には向かわず、通航料・罰金・
船籍制限を通じた実効支配・覇権的関与を維持・強化しようとしていると見るのが最も整合的だが、
実現度合いはオマーン・湾岸諸国・中国など第三国の受容度に大きく制約される」

### Case2: 一言見立て
「米国の海上安全保障プレゼンスは理論上は湾岸諸国への費用負担要求のLeverageになり得るが、
現状では政治・外交コストが高く、実務的には行使可能性の低い（単独では持続不能な）カードに
とどまっている」

### Case3: 一言見立て
「中国はイランに対し経済面で潜在的な強いレバレッジを持つが、ホルムズ海峡での軍事的・準軍事的
行動を直接抑制するほどの『行使された制約力』は限定的であり、王毅外相の航行の自由要請は
象徴的圧力にとどまる」

## Quality Guard所見（3Case共通）

`stakeholders_grounded_in_source_pack`チェックが3Case全てで不合格。これはSource Pack本文に
Entity名がそのまま出現しない（AIがstakeholder名を「イラン政権（最高指導者陣営を含む）」等、
説明的な表記にしているため文字列完全一致で検出できない）ことによる**既知の誤検知**であり、
内容の捏造や無根拠な人物・組織の登場を示すものではないことを個別に確認した。ただし
チェック自体の実用性が低い状態であり、Phase 3-1の改善対象とする（詳細は
`docs/geopolitical-phase3-improvement-backlog.md`）。

Case2では追加で`fact_claims_have_source_ref`が不合格（「米国はホルムズ海峡周辺で海軍
プレゼンスを維持し...」という観測claimにsource_refが無い）。これは実際のGapであり、
LLMが一般的背景知識を観測事実として無出典で述べてしまう可能性を示す実例として記録する。

## Human Review運用の変更（2026-08-21、方針転換）

Phase 3-0開始当初は「候補Knowledge Item（country_foundation/precedent/leverage_card）を
Case実行前に個別レビューする」運用を想定していたが、ユーザー方針転換により以下へ変更した:

```
External Sources / Structural Data
  ↓
Minimal Hormuz Seed
  ↓
Case 1 → AI Draft → Human Expert Review → Expert Final / Review Comments
  ↓
Delta Extraction → Reusable Knowledge Candidate → Promote / Reinforce
  ↓
Case 2 / 3
```

country_foundation/precedent/leverage_card 3件は**Case実行前の個別承認を必須にしない**。
Human Reviewの主対象はSeed KnowledgeではなくCase1のAI Draftそのものであり、Internal
Expert Knowledgeの承認は実際のAI Draftに対する専門家の修正・コメントを起点に行う
（既存のExpert Review Loop、`docs/geopolitical-internal-expert-learning.md`）。

### Gap1〜3の切り分け（2026-08-21実施）

Case1の`build_source_pack`/`build_case_context`をドライラン確認した結果、以下3件のGapを
特定した:

- **Gap1（Critical）**: Entity/Relationship/Asset/Dependency構造データが、Expert Engine
  呼び出し経路（`run_expert_request`）に一切接続されていなかった。原因はHuman Review要否
  ではなく、既存`geo_foundation_pack_service.build_foundation_pack`（Entity/Relationship/
  Asset/Dependency/既存Leverage Cardを22項目に集約する既存関数）が
  `geo_candidate_extraction_service`/`geo_knowledge_gap_service`からしか呼ばれておらず、
  Expert Engineの呼び出し経路には配線されていなかった**単純な配線漏れ**（Retrieval Gap）。
  External Reference経由の回避策は無い（Claimに含まれない構造データのため）。
- **Gap2（Critical、修正済み）**: IRGCの実質支配に関するExternal Assessment Claim（TIME/
  Gulf International Forum由来、3件）が`geo_claim_issues`でどのIssueにも未紐付けだった。
  `geo_claim_service.link_claim_to_issue`でissue_case1へ紐付けて解消（Human Review不要）。
  併せてClaim`2053e434`（MoU後の攻撃再開、「9隻以上・Al Rekayyat」）の本文を、現在の
  Source（Al Jazeera 7/9の要約のみ）で裏付けられる範囲の表現へ修正した（断定的な
  observed_factのままAI Contextへ入れない）。
- **Gap3（Phase3-1判断材料として保留）**: `build_source_pack`がCase→Issueのスコープを
  無視し、Theme全体の直近document titleを無差別に返す。Case1のBaseline実行では実際に
  Case2/3関連文書タイトル（UAEパイプライン記事、中国王毅発言記事）が混入し、AIがタイトル
  のみから内容を類推した観測claim（「UAEはホルムズ海峡を迂回する第二の西東パイプラインを
  前倒しで整備している」等、本文非開示のまま）を生成する実例を確認した。今回は修正せず、
  Retrieval GapとしてPhase 3-1の判断材料に残す。

### Gap1 Integration Fix（最小限接続）

新しいKnowledge Model/Retrieval方式は作らず、既存`build_foundation_pack`を
`geo_expert_engine.run_expert_request`へ最小限接続した。追加したのは`_format_structural_context`
関数のみで、Foundation Pack 22項目のうちbuild_case_contextと重複する部分（Foundation/
Reusable/Hypothesis/Gap/Claim）は除外し、**Entity一覧・Relationship・Dependency・Asset・
既存Leverage Card**のみを最小限の文字列化でuser_promptへ追加した
（`geo_expert_engine.py`の`_format_structural_context`/`run_expert_request`参照）。
既存Expert Engine関連テスト40件は全て合格を維持。

### Baseline（Gap1未修正）→ Integration Fix後（Gap1修正後）比較

| response_id | 状態 | Quality Guard |
|---|---|---|
| `9f5a03a5-2a55-4908-89b5-402aa8aa6505` | Baseline（Gap2修正済み・Gap1未修正） | 7/9（fact_claims_have_source_ref・stakeholders_grounded_in_source_pack不合格） |
| `0b65f309-6edf-4953-ba64-d5c6e7772386` | Integration Fix後（Gap1修正後） | 8/9（stakeholders_grounded_in_source_pack不合格のみ、既知の誤検知） |

| 観点 | Baseline | Integration Fix後 | 変化 |
|---|---|---|---|
| stakeholder coverage | 5主体。イラン政権中枢を「モジュタバ・ハメネイ＋IRGC」で1主体に合体 | 7主体。Iran/IRGC/Mojtaba Khameneiを別主体として分離、それぞれ異なるleverage/position/ability_to_move_or_blockを記述 | ◎改善（内部権力構造の粒度が上がった） |
| relationship understanding | dynamics文中に一般論として言及のみ、具体的Relationship未参照 | 「Relationship-China-Iran」を明示的にsource_ref・sources_and_citationsへ引用（`major_oil_buyer_discount_purchaser`のdescriptionと整合） | ◎改善（Relationshipレコードへ直接トレース可能に） |
| dependency understanding | 「湾岸諸国はホルムズ依存」と一般論のみ | 「Dependency-Saudi-Arabia」を明示的にsource_ref・main_scenarioのsupporting_evidenceへ引用 | ◎改善 |
| asset / leverage identification | `assets_dependencies_leverage`フィールドがnull（空） | 同フィールドに3件（asset=IRGCの海峡封鎖能力、dependency=Saudiの輸出依存、leverage_card=中国の割引原油バイヤー）を明記、Asset由来のclaim（IRGCの機雷・沿岸ミサイル能力と2026年3月の実際の封鎖実績）が新規追加 | ◎大幅改善（Capability=Assetと交渉力=Leverageの区別が明確化） |
| decisive power analysis | 「イラン政権中枢（特にIRGC）」を一体として扱う一層構造の記述 | 「実効支配＝IRGC／イラン」「制度的承認＝米国＋湾岸＋主要輸入国」という二層構造として明示的に整理 | ◎改善（IRGCが独立Entityとして扱われたことで力学の切り分けが精緻化） |

**結論**: Gap1（Entity/Relationship/Asset/Dependencyの配線漏れ）は、Case1の分析品質（特に
Stakeholder粒度・Decisive Power分析・Asset/Leverageの区別）に実際に測定可能な影響を与えて
いたことが確認された。最小限のIntegration Fixで改善が確認できたため、Phase 3-1へ先送り
せず今回のPilot内で解消したことは妥当だった。

### Human Expert Reviewの対象

上記Integration Fix後のresponse_id `0b65f309-6edf-4953-ba64-d5c6e7772386`（Case1の
`geo_cases.final_output`にも反映済み）を、社内の別専門家によるHuman Expert Reviewの対象と
する。レビュー観点はPlan・ユーザー指示通り: Stakeholder selection / Actor interests・
constraints / Relationship・power dynamics / Asset vs Leverage / Capability vs Intent /
Decisive Power / Precedent selection / Evidence weighting / Main Hypothesis / Alternative
Hypotheses / What would change the judgment。

## 未実施（本Pilotの範囲外として残る事項）

- **Expert Review Loop**（AI Draft→社内の別専門家によるExpert Final→Delta Extraction→
  Promote/Reinforce判断）: ユーザー方針により後日実施。実施後、本ドキュメントとPromote
  結果を追記する
- **8章のExpert Learning再利用テスト**: PromoteされたInternal Expert Reasoningが存在しない
  段階のため評価不能。「未実施（専門家レビュー未完了のため）」として記録し、失敗として
  扱わない
- **country_foundation/precedent/leverage_card 3件のcandidate→active承認**: 人間が
  実際にSource対応・構造妥当性・再利用価値を確認してから行う（Runbook参照）

## Success Criteria（Plan10章）に対する現時点の状況

| 基準 | 状況 |
|---|---|
| 最低3件のPilot Case実行 | ✅ 実施（Case1-3のAI Draft生成まで完了） |
| External Assessment/Actor Claim/Fact分離 | ✅ Claim情報でinformation_class分離済み、candidate内容も分離修正済み |
| Stakeholder/Leverage/Precedentを含む分析 | ✅ 各Case AI Draftにposition/leverage/ability_to_move_or_block/decisive_power_and_leverageを含む |
| AI Draft→Expert Final Review Loop実行 | ⏳ 未実施（後日） |
| 最低1件のExpert ReasoningをPromote | ⏳ 未実施（Review Loop未実施のため） |
| Reuse確認（3値記録） | ⏳ 未検証（Promote自体が未実施のため判定不能） |
| Gap分類・Phase3-1向けTop2-3改善項目 | ✅ `docs/geopolitical-phase3-improvement-backlog.md`参照 |
