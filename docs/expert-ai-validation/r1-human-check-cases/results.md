# Phase R1 Human Check用 新規5ケース 実施結果

実施日: 2026-09-03。全ケース `profile="deep"`、`GEO_EXPERT_ACTOR_INTELLIGENCE_ENABLED`/
`GEO_EXPERT_INSTITUTION_INTELLIGENCE_ENABLED`/`GEO_EXPERT_EXTERNAL_RESEARCH_ENABLED`
全て有効。実行中に発見したNamed Entity Coverageのstopword不足バグ（クエリ中の英語見出し語
「Key Point」「Main Scenario」等を固有名詞と誤認識していた）を修正した版で最終実行。

本ファイルはAIの出力結果の要約であり、「正解」を判定・追記するものではない。完全な生
アウトプットは`raw/r1_human_check_<code>_result.json`に保存済み。評価順（1→3→2→4→5）に
沿ってHuman Check5項目（①Key Pointは正しいか ②重要Actorの抜け/過剰はないか
③Evidence以上に推論していないか ④深掘りの深さは適切か ⑤専門家として採用するか）を
ユーザー側で採点する。

---

## サマリ表

| # | Case | 狙い | Primary Key Point | control_type |
|---|---|---|---|---|
| 1 | 中国レアアース輸出管理 | Structural/Institution | 中国の重要鉱物・素材輸出許認可の運用方針と強度 | institutionally_constrained |
| 2 | サウジ・米・イスラエル | Actor Intelligence | 米国によるサウジへの安全保障保証と原子力協力パッケージの中身と承認可否 | mixed |
| 3 | インド・ロシア産原油 | Distributed/Structural | G7価格上限・海運保険・決済制裁の運用がインドのロシア産原油取引をどこまで拘束するか | institutionally_constrained |
| 4 | グリーンランド | Institution+Actor+Frame | グリーンランドの主権変更には住民同意と王国法制上の手続きが不可欠で、米・デンマークの二国間合意だけでは決まらない | institutionally_constrained |
| 5 | 台湾海峡「封鎖/演習」認識 | Evidence/Media/Frame/Temporal | 中国艦艇・海警船の台湾周辺への高頻度・高密度プレゼンスの常態化 | unknown |

---

## Case 1｜中国の重要鉱物・レアアース輸出管理

**候補（4件）**:
1. **[採用]** 中国の重要鉱物・素材輸出許認可の運用方針と強度（institutionally_constrained）
2. 米・日・欧・台湾などによる「非紅色サプライチェーン」構築の実効性（mixed）
3. グローバルな重要鉱物・素材サプライチェーンの構造的集中（structural、棄却——背景要因として位置づけ）
4. **Frame-breaking仮説**: 「米中対立」「資源の武器化」フレーム自体が説明力を失いつつある可能性（unknown、棄却——Evidence不足のため補助的位置づけ）

Key Actor: `CN_central`/`CN_mofcom`/`US_federal`/`JP_gov`/`EU_commission`/`TW_president`
（entity紐付けなしの条件下で、6主体を自力発見）。

品質: 38 pass/3 fail（`fact_claims_have_source_ref`/`temporal_hedge_language_not_present`/
`unsupported_actor_assumption_not_promoted`、いずれも既知の繰り返しパターン）。
Research: 既存DBのみでsufficient（2件）、追加検索なし。

## Case 2｜サウジ・米国・イスラエルの関係再編

**候補（4件）**:
1. **[採用]** 米国によるサウジへの安全保障保証と原子力協力パッケージの中身と承認可否（mixed）
2. イスラエル政府のパレスチナ問題に関する譲歩可能範囲（actor_controlled、棄却）
3. サウジアラビアの対イラン抑止と地域大国化の戦略的目標（actor_controlled、棄却）
4. 米国内政治（議会・世論）による対サウジ・原子力協力への制約（institutionally_constrained、棄却——Primaryと統合する方が自然と判断）

**特筆**: rejected_candidatesに`"unresolved"`エントリも含まれ、「他にFrame-breakingな
Key Pointを置くための直接Evidenceが不足しており、無理に採用すると推測の比重が過度に
高くなるため」と明記——Anti-overthinking ruleが機能。

Key Actor: MBS（`c47c90e6...`）/Netanyahu（`585cc564...`）、entity紐付け通り。

品質: 38 pass/3 fail（`supporting_knowledge_refs_grounded`/`temporal_hedge_language_
not_present`/`situation_actor_assessment_grounded`）。Research: 0→1件、partial止まり
（Evidence不足を正直に維持）。

## Case 3｜インドのロシア産原油購入と西側制裁

**候補（4件）**:
1. **[採用]** G7価格上限・海運保険・決済制裁の運用がインドのロシア産原油取引をどこまで拘束するか（institutionally_constrained）
2. インド政府のエネルギー安全保障・成長優先というPersistent Preference（unknown、棄却——「首脳の意向だけで説明しない」という狙い通り、周辺要因に位置づけ）
3. ロシア産原油のディスカウントと欧州向け精製品価格のスプレッド（structural、棄却）
4. 西側が「インド経由のロシア原油」をどこまで政治的に容認するかという外交フレーム（mixed、棄却——直接Evidence不足）

Key Actor: `actor_india_gov`/`actor_us_gov`/`actor_eu`（entity紐付けなしの条件下で自力発見、
Putin個人への過度な焦点化は回避——狙い通り分散構造を捉えた）。

品質: 38 pass/3 fail（`fact_claims_have_source_ref`/`temporal_hedge_language_not_
present`/`unsupported_actor_assumption_not_promoted`）。Research: 0→1件、partial。

## Case 4｜グリーンランドをめぐる米国・デンマーク・自治政府

**候補（4件）**:
1. **[採用]** グリーンランドの主権変更には住民同意と王国法制上の手続きが不可欠で、米・デンマークの二国間合意だけでは決まらない構造（institutionally_constrained）
2. 北極圏における米軍プレゼンスと対中・対露抑止の必要性（structural、棄却）
3. 重要鉱物・北極航路をめぐる経済的利害が外部アクターを引き寄せグリーンランドに交渉レバレッジを与える（mixed、棄却）
4. **Frame-break候補**: 「米国対デンマーク」の二国間フレーム自体がグリーンランドの意思決定権を過小評価している可能性（unknown、棄却理由: 「これはメディア・ナラティブに関する仮説であり、実際の意思決定構造そのものではない」——フレーム批判とKey Point自体を正しく区別）

Key Actor: `US_federal_government`/`Denmark_kingdom_government`/`Greenland_self_
government`（Trumpのみentity紐付け、デンマーク・グリーンランドは自力発見）。

品質: 37 pass/4 fail（`fact_claims_have_source_ref`/`main_scenario_has_supporting_
evidence`/`supporting_knowledge_refs_grounded`/`temporal_hedge_language_not_
present`）。Research: 検索実行するもstop_condition=evidence_unobtainable、partial
のまま（Evidence不足を安全に維持）。

## Case 5｜台湾海峡の「封鎖／演習」認識

**候補（4件）**:
1. **[採用]** 中国艦艇・海警船の台湾周辺への高頻度・高密度プレゼンスの常態化（unknown）
2. 台湾周辺海域での実際の通航阻害・検査・拿捕の有無（institutionally_constrained、棄却——「Current Evidenceとしては確認できない」と明記）
3. 米国・日本など第三国の商船・航空会社・保険会社のリスク認識と運用変更（distributed、棄却）
4. **H0 vs H1 vs H2/H4**: Narrativeの違いが「単なるラベル差」か「異なるEvidence・Audience」を反映しているのか（mixed、棄却——「現時点のEvidenceは行動ではなくラベルの違いに関する一般的説明に依存」と明記、行動レベルのプレゼンス強度を優先）

**特筆**: 「封鎖」という強い言葉に引っ張られず、観測可能な「プレゼンスの常態化」という
最も再現性の高い行動レベルの説明をPrimaryに選定——Narrative divergence自体は重要な
論点として保持しつつ、Key Pointとは区別。

Key Actor: `PRC_leadership`/`PLA`/`PRC_CoastGuard`/`Taiwan_government`/`US_government`/
`Japan_government`（entity紐付けなしの条件下で6主体を自力発見）。

品質: 39 pass/2 fail（`supporting_knowledge_refs_grounded`/`temporal_hedge_language_
not_present`）。Research: 0→1件、partial。

---

## 横断的な所見

- 5ケース全てで、候補Key Pointが最低3〜4種の異なる説明軸（Institution/Structural/
  Actor/Frame-breaking）で競合しており、Chunk5のAnti-overthinking ruleも一貫して機能
  （Evidence不足のFrame-breaking候補は明示的な理由とともに正しく棄却）。
- entity紐付けなしのCase1/3/4(一部)/5でも、Foundation Pack由来のEntityやActor
  Intelligence Packに頼らず、自己命名のactor_refで妥当な主体を複数発見——Institution/
  Structural発見能力が事前タグ付けに依存していないことを確認。
- `temporal_hedge_language_not_present`が5件中4件で継続的に検出——P0-2で意図した
  「検出はできるが自動修正はしない」設計通りの挙動（根本課題は引き続きOpen）。
- 実行中に発見・修正: Named Entity Coverageのstopword不足（クエリ中の英語見出し語を
  固有名詞と誤認識）——修正後、5件全てで`named_entity_coverage_gaps`は空。
